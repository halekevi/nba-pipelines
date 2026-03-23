#!/usr/bin/env python3
"""
Fetch game spreads and totals from ESPN scoreboard APIs into data/cache/game_lines.db.
"""

from __future__ import annotations

import argparse
import math
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
from ensure_local_cache import ensure_local_cache

ensure_local_cache(str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "cache" / "game_lines.db"

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (compatible; PropOracleGameLines/1.0)",
        "Accept": "application/json",
    }
)

# NBA: map ESPN abbrev → PrizePicks / pipeline abbrev (see NBA/scripts/step3_attach_defense.py)
NBA_ESPN_TO_SLATE: dict[str, str] = {
    "BKN": "BRK",
    "GSW": "GS",
    "NOP": "NO",
    "NYK": "NY",
    "SAS": "SA",
    "PHX": "PHO",
    "UTAH": "UTA",
}

# CBB: small alias table (CBB/scripts/pipeline/step1_pp_cbb_scraper.py) — ESPN usually matches PP
CBB_TEAM_ALIASES: dict[str, str] = {
    "GCU": "GC",
    "NEVADA": "NEV",
    "SDST": "SDSU",
    "MIZ": "MIZZ",
    "NCSU": "NCST",
    "GTECH": "GT",
}

# NHL: ESPN → PrizePicks (NHL/scripts/step3_attach_defense_nhl.py TEAM_ALIASES inverted)
NHL_ESPN_TO_SLATE: dict[str, str] = {
    "LAK": "LA",
    "NJD": "NJ",
    "SJS": "SJ",
    "TBL": "TB",
    "CBJ": "CLB",
    "UTA": "ARZ",
}

SCOREBOARD_URLS: dict[str, str] = {
    "NBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "CBB": "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard",
    "NHL": "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard",
    "Soccer": "https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard",
}

# Completed games often drop odds from the site scoreboard; core API still has closing lines.
CORE_ODDS_PREFIX: dict[str, str] = {
    "NBA": "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events",
    "CBB": "https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball/events",
    "NHL": "https://sports.core.api.espn.com/v2/sports/hockey/leagues/nhl/events",
    "Soccer": "https://sports.core.api.espn.com/v2/sports/soccer/leagues/usa.1/events",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_slate_team(sport: str, abbr: str) -> str:
    t = str(abbr or "").strip().upper()
    if not t:
        return ""
    if sport == "NBA":
        return NBA_ESPN_TO_SLATE.get(t, t)
    if sport == "CBB":
        return CBB_TEAM_ALIASES.get(t, t)
    if sport == "NHL":
        return NHL_ESPN_TO_SLATE.get(t, t)
    return t


def _american_to_implied_prob(odds_str: Any) -> float | None:
    if odds_str is None:
        return None
    s = str(odds_str).replace("−", "-").strip().upper()
    if not s or s in ("EVEN", "PK", "PICK"):
        return None
    try:
        if s[0] in "+-":
            sign, rest = s[0], s[1:]
            n = float(rest)
        else:
            sign, n = "+", float(s)
    except ValueError:
        return None
    if sign == "-":
        return n / (n + 100.0)
    return 100.0 / (n + 100.0)


def _parse_spread_from_details(details: str | None) -> float | None:
    if not details:
        return None
    m = re.search(r"([+-]?\d+(?:\.\d+)?)", str(details))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _computed_home_spread_from_win_prob(home_win: float) -> float | None:
    p = float(home_win)
    if p <= 0.02 or p >= 0.98:
        return None
    try:
        ratio = p / (1.0 - p)
        if ratio <= 0:
            return None
        return -math.log(ratio) * 6.5
    except (ValueError, OverflowError):
        return None


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS game_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sport TEXT NOT NULL,
            game_date TEXT NOT NULL,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            spread REAL,
            total REAL,
            home_win_prob REAL,
            away_win_prob REAL,
            source TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(sport, game_date, home_team, away_team)
        )
        """
    )
    conn.commit()


def fetch_scoreboard_json(url: str, yyyymmdd: str) -> dict[str, Any] | None:
    try:
        r = SESSION.get(url, params={"dates": yyyymmdd}, timeout=45.0)
        if not r.ok:
            return None
        return r.json()
    except requests.RequestException:
        return None


def fetch_core_odds_item(sport: str, event_id: str) -> dict[str, Any] | None:
    base = CORE_ODDS_PREFIX.get(sport)
    if not base:
        return None
    url = f"{base}/{event_id}/competitions/{event_id}/odds"
    try:
        r = SESSION.get(url, timeout=45.0)
        if not r.ok:
            return None
        data = r.json()
        items = data.get("items") or []
        return items[0] if items else None
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return None


def _odds_dict_from_core(item: dict[str, Any]) -> dict[str, Any]:
    """Map core odds item into the same shape as site scoreboard odds[0]."""
    out: dict[str, Any] = {
        "spread": item.get("spread"),
        "overUnder": item.get("overUnder"),
        "details": item.get("details"),
    }
    ho = item.get("homeTeamOdds") or {}
    ao = item.get("awayTeamOdds") or {}
    ml_h = ho.get("moneyLine")
    ml_a = ao.get("moneyLine")
    moneyline: dict[str, Any] = {"home": {}, "away": {}}

    def _ml_str(v: Any) -> str:
        try:
            x = float(v)
            if abs(x - round(x)) < 1e-9:
                return str(int(round(x)))
            return str(x)
        except (TypeError, ValueError):
            return str(v)

    if ml_h is not None:
        moneyline["home"]["close"] = {"odds": _ml_str(ml_h)}
    if ml_a is not None:
        moneyline["away"]["close"] = {"odds": _ml_str(ml_a)}
    out["moneyline"] = moneyline
    out["homeTeamOdds"] = ho
    out["awayTeamOdds"] = ao
    return out


def _pick_odds_provider(odds_list: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not odds_list:
        return None
    for o in odds_list:
        if o.get("spread") is not None or o.get("overUnder") is not None:
            return o
    return odds_list[0]


def parse_games_for_sport(sport: str, payload: dict[str, Any], game_date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ev in payload.get("events") or []:
        comps = (ev.get("competitions") or [None])[0]
        if not isinstance(comps, dict):
            continue
        home_abbr = away_abbr = ""
        for c in comps.get("competitors") or []:
            team = (c.get("team") or {})
            ab = str(team.get("abbreviation") or "").strip().upper()
            ha = str(c.get("homeAway") or "").lower()
            if ha == "home":
                home_abbr = ab
            elif ha == "away":
                away_abbr = ab
        if not home_abbr or not away_abbr:
            continue

        event_id = str(ev.get("id") or comps.get("id") or "").strip()

        home_team = _norm_slate_team(sport, home_abbr)
        away_team = _norm_slate_team(sport, away_abbr)

        odd = _pick_odds_provider(comps.get("odds"))
        if odd is None and event_id:
            core_item = fetch_core_odds_item(sport, event_id)
            if core_item:
                odd = _odds_dict_from_core(core_item)

        spread: float | None = None
        total: float | None = None
        home_wp: float | None = None
        away_wp: float | None = None
        source = "espn"

        if isinstance(odd, dict):
            sp = odd.get("spread")
            if sp is not None:
                try:
                    spread = float(sp)
                except (TypeError, ValueError):
                    spread = None
            ou = odd.get("overUnder")
            if ou is not None:
                try:
                    total = float(ou)
                except (TypeError, ValueError):
                    total = None

            ml = odd.get("moneyline") or {}
            ho = ((ml.get("home") or {}).get("close") or {}).get("odds")
            ao = ((ml.get("away") or {}).get("close") or {}).get("odds")
            if ho is None:
                ho = ((ml.get("home") or {}).get("open") or {}).get("odds")
            if ao is None:
                ao = ((ml.get("away") or {}).get("open") or {}).get("odds")
            ph = _american_to_implied_prob(ho)
            pa = _american_to_implied_prob(ao)
            if ph is not None and pa is not None and ph + pa > 0:
                ssum = ph + pa
                home_wp = ph / ssum
                away_wp = pa / ssum
            elif ph is not None:
                home_wp = ph
                away_wp = 1.0 - ph
            elif pa is not None:
                away_wp = pa
                home_wp = 1.0 - pa

            if spread is None:
                det_sp = _parse_spread_from_details(odd.get("details"))
                if det_sp is not None:
                    spread = det_sp

        if spread is None and home_wp is not None:
            cs = _computed_home_spread_from_win_prob(home_wp)
            if cs is not None:
                spread = cs
                source = "computed"

        rows.append(
            {
                "sport": sport,
                "game_date": game_date,
                "home_team": home_team,
                "away_team": away_team,
                "spread": spread,
                "total": total,
                "home_win_prob": home_wp,
                "away_win_prob": away_wp,
                "source": source,
            }
        )
    return rows


def upsert_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    now = _now_iso()
    for r in rows:
        conn.execute(
            """
            INSERT INTO game_lines (
                sport, game_date, home_team, away_team, spread, total,
                home_win_prob, away_win_prob, source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sport, game_date, home_team, away_team) DO UPDATE SET
                spread = excluded.spread,
                total = excluded.total,
                home_win_prob = excluded.home_win_prob,
                away_win_prob = excluded.away_win_prob,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (
                r["sport"],
                r["game_date"],
                r["home_team"],
                r["away_team"],
                r["spread"],
                r["total"],
                r["home_win_prob"],
                r["away_win_prob"],
                r["source"],
                now,
                now,
            ),
        )
    conn.commit()


def has_data_for_date(conn: sqlite3.Connection, sport: str, game_date: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM game_lines WHERE sport = ? AND game_date = ? LIMIT 1",
        (sport, game_date),
    )
    return cur.fetchone() is not None


def run_fetch(
    game_date: str,
    sports: list[str],
    refresh: bool,
) -> dict[str, list[dict[str, Any]]]:
    yyyymmdd = game_date.replace("-", "")
    out: dict[str, list[dict[str, Any]]] = {s: [] for s in sports}
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        init_db(conn)
        for sp in sports:
            if not refresh and has_data_for_date(conn, sp, game_date):
                cur = conn.execute(
                    "SELECT sport, game_date, home_team, away_team, spread, total, "
                    "home_win_prob, away_win_prob, source FROM game_lines "
                    "WHERE sport = ? AND game_date = ?",
                    (sp, game_date),
                )
                cols = [d[0] for d in cur.description]
                out[sp] = [dict(zip(cols, row)) for row in cur.fetchall()]
                continue
            url = SCOREBOARD_URLS.get(sp)
            if not url:
                continue
            payload = fetch_scoreboard_json(url, yyyymmdd)
            if not payload:
                continue
            rows = parse_games_for_sport(sp, payload, game_date)
            if rows:
                upsert_rows(conn, rows)
            out[sp] = rows
    finally:
        conn.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch game lines from ESPN into SQLite.")
    ap.add_argument("--sport", choices=("NBA", "CBB", "NHL", "Soccer"), default=None)
    ap.add_argument("--date", default="", help="YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--refresh", action="store_true", help="Re-fetch even if DB has this date")
    args = ap.parse_args()

    if args.date.strip():
        game_date = args.date.strip()
    else:
        game_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    sports = [args.sport] if args.sport else ["NBA", "CBB", "NHL", "Soccer"]
    result = run_fetch(game_date, sports, args.refresh)

    total_games = 0
    spread_ok = 0
    total_ok = 0
    lines: list[str] = []
    for sp in sports:
        rows = result.get(sp) or []
        n = len(rows)
        total_games += n
        spread_ok += sum(1 for r in rows if r.get("spread") is not None)
        total_ok += sum(1 for r in rows if r.get("total") is not None)
        lines.append(f"  {sp}: {n} games fetched ({game_date})")

    print("\n".join(lines))
    print(f"  Spreads available: {spread_ok}/{total_games} games")
    print(f"  Totals available: {total_ok}/{total_games} games")
    print(f"  DB: {DB_PATH}")


if __name__ == "__main__":
    main()
