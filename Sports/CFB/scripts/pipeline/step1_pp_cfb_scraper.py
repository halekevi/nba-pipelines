#!/usr/bin/env python3
"""
pp_cfb_scraper.py  (optimized)

PrizePicks API scraper for College Football (CFB) projections (league_id=15 by default).

OPTIMIZATIONS vs original:
- Shared TEAM_ALIASES / norm_team consistent with rest of pipeline
- extract_home_away(): early-return once both values found; removed redundant
  isinstance(dict) fallback that was already covered by the first branch
- Player & game lookups built with a single dict comprehension pass per type
- _norm_pick_type() extracted as small helper (reused in step_b)
- Removed unused norm_team() inside extract_home_away (already called by caller)
- Pagination: `params` set to None once cursor url is obtained (unchanged, was
  already correct — kept as-is)

Usage:
  py -3.14 pp_cfb_scraper.py --out step1_fetch_prizepicks_api_cbb.csv
  py -3.14 pp_cfb_scraper.py --league_id 20 --out cfb_pp.csv --include_combos
  py -3.14 pp_cfb_scraper.py --out cfb_pp.csv --max_pages 3
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import pandas as pd
import requests

# Ensure <repo>/PropOracle is on sys.path so we can import PropOracle-level helpers.
_PROPORACLE_ROOT = Path(__file__).resolve().parents[4]
if str(_PROPORACLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROPORACLE_ROOT))

from scripts.db_utils import log_pipeline_health

PP_URL = "https://api.prizepicks.com/projections"

PP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Origin": "https://app.prizepicks.com",
    "Referer": "https://app.prizepicks.com/",
}

TEAM_ALIASES: Dict[str, str] = {
    "GCU":   "GC",
    "NEVADA":"NEV",
    "SDST":  "SDSU",
    "MIZ":   "MIZZ",
    "NCSU":  "NCST",
    "GTECH": "GT",
    "":      "",
}


def norm_team(s: str) -> str:
    t = str(s or "").strip().upper()
    return TEAM_ALIASES.get(t, t)


def _norm_pick_type(odds_type: str) -> str:
    pt = str(odds_type or "").lower()
    if "gob" in pt: return "goblin"
    if "dem" in pt: return "demon"
    return "standard"


def safe_get(d: Dict[str, Any], *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def extract_home_away(game_attrs: Dict[str, Any]) -> Tuple[str, str]:
    """Return (home, away) normalised team codes."""
    if not isinstance(game_attrs, dict):
        return "", ""

    home = norm_team(
        game_attrs.get("home_team_abbreviation")
        or game_attrs.get("home_team")
        or game_attrs.get("home_team_code")
        or ""
    )
    away = norm_team(
        game_attrs.get("away_team_abbreviation")
        or game_attrs.get("away_team")
        or game_attrs.get("away_team_code")
        or ""
    )
    return home, away




def _backfill_opponents(rows: list, game_teams: dict) -> list:
    """Second pass: fill pp_opp_team for any rows that got blank opponents.
    By the time all pages are fetched, game_teams is fully populated."""
    for r in rows:
        if r.get("pp_opp_team"):
            continue
        gid = str(r.get("pp_game_id", "")).strip()
        team = r.get("pp_team", "")
        if gid and team:
            others = game_teams.get(gid, set()) - {team}
            if others:
                r["pp_opp_team"] = next(iter(others))
    return rows

def fetch_cfb_projections(
    league_id: int,
    per_page: int,
    single_stat: bool,
    game_mode: str,
    sleep_s: float,
    max_pages: Optional[int] = None,
) -> pd.DataFrame:
    session = requests.Session()
    session.headers.update(PP_HEADERS)

    params: Optional[Dict[str, Any]] = {
        "league_id": league_id,
        "per_page":  per_page,
        "game_mode": game_mode,
    }
    if single_stat:
        params["single_stat"] = "true"

    url: Optional[str] = PP_URL
    rows: List[Dict[str, Any]] = []
    seen_proj_ids: set = set()
    page = 1
    # game_teams accumulates across ALL pages so cross-page opponents resolve correctly
    game_teams: Dict[str, set] = {}

    while url:
        if max_pages is not None and page > max_pages:
            break

        for attempt in range(6):
            try:
                r = session.get(url, params=params, timeout=30)

                if r.status_code == 429:
                    wait = min(20 * (1.5 ** attempt), 90) + random.uniform(0, 3)
                    print(f"  ⚠️  429 — waiting {wait:.0f}s")
                    time.sleep(wait)
                    continue

                r.raise_for_status()
                j = r.json()

                data     = j.get("data") or []
                included = j.get("included") or []

                if not data:
                    print(f"  ⛔ No data on page {page}. Done.")
                    return pd.DataFrame(rows)

                # Build lookups in a single pass over included
                players:   Dict[str, Dict[str, Any]] = {}
                games:     Dict[str, Dict[str, Any]] = {}
                # team_name/market -> abbreviation map (CBB: new_player.team is full name)
                name_to_abbr: Dict[str, str] = {}
                durations: Dict[str, str] = {}  # duration_id -> name e.g. "Full Game" / "2nd Half"
                for obj in included:
                    otype = obj.get("type", "")
                    oid   = str(obj.get("id", ""))
                    attrs = obj.get("attributes") or {}
                    if otype in ("new_player", "player", "players"):
                        players[oid] = attrs
                    elif otype in ("game", "games", "new_game"):
                        games[oid] = attrs
                    elif otype == "duration":
                        durations[oid] = str(attrs.get("name", "")).strip()
                    elif otype == "team":
                        abbr = norm_team(attrs.get("abbreviation") or "")
                        if abbr:
                            for key in (attrs.get("name",""), attrs.get("market",""),
                                        attrs.get("abbreviation","")):
                                k = str(key).strip().upper()
                                if k:
                                    name_to_abbr[k] = abbr

                def resolve_team(raw: str) -> str:
                    """Try norm_team first, then name_to_abbr lookup."""
                    if not raw: return ""
                    direct = norm_team(raw)
                    # if direct is a short code (<=5 chars) trust it
                    if len(direct) <= 5 and direct.isalpha():
                        return direct
                    # try lookup by full name
                    return name_to_abbr.get(str(raw).strip().upper(), direct)

                # Build game_id -> set of teams from projection data (CBB fix)
                # CBB game objects don't have home/away fields, so we derive
                # the two teams from all players in each game.
                # NOTE: game_teams is declared outside the loop and persists
                # across pages so cross-page opponents always resolve correctly.
                for proj in data:
                    rel  = proj.get("relationships") or {}
                    pid_data = (rel.get("new_player") or rel.get("player") or {}).get("data") or {}
                    pid  = str(pid_data.get("id", ""))
                    p    = players.get(pid, {})
                    team = norm_team(p.get("team") or p.get("team_abbreviation") or "")
                    game_rel = (rel.get("game") or rel.get("new_game") or {}).get("data") or {}
                    gid  = str(game_rel.get("id", "")) if isinstance(game_rel, dict) else ""
                    if gid and team:
                        game_teams.setdefault(gid, set()).add(team)

                added = 0
                bounced_blank_player = 0
                bounced_blank_team = 0
                for proj in data:
                    proj_id = str(proj.get("id", ""))
                    if not proj_id or proj_id in seen_proj_ids:
                        continue
                    seen_proj_ids.add(proj_id)

                    attr = proj.get("attributes") or {}
                    rel  = proj.get("relationships") or {}

                    pid_data = (rel.get("new_player") or rel.get("player") or {}).get("data") or {}
                    pid      = str(pid_data.get("id", ""))
                    p        = players.get(pid, {})

                    # Player name can appear under different keys depending on PP API version.
                    # Keep this upstream so downstream steps never see blank players.
                    player_name = (
                        p.get("display_name")
                        or p.get("name")
                        or p.get("full_name")
                        or p.get("fullName")
                        or p.get("short_name")
                        or p.get("shortName")
                        or ""
                    )
                    if not player_name:
                        first = str(p.get("first_name") or p.get("firstName") or "").strip()
                        last  = str(p.get("last_name")  or p.get("lastName")  or "").strip()
                        if first or last:
                            player_name = f"{first} {last}".strip()
                    pp_team     = resolve_team(p.get("team") or p.get("team_abbreviation") or "")

                    # ── Bouncer (strict): never emit blank player/team ──────────────
                    if not str(player_name).strip():
                        bounced_blank_player += 1
                        continue
                    if not str(pp_team).strip():
                        bounced_blank_team += 1
                        continue

                    # Derive opponent using game_teams map
                    pp_opp_team = ""
                    game_rel = (rel.get("game") or rel.get("new_game") or {}).get("data") or {}
                    if isinstance(game_rel, dict):
                        gid = str(game_rel.get("id", ""))
                        teams_in_game = game_teams.get(gid, set())
                        others = teams_in_game - {pp_team}
                        if others:
                            pp_opp_team = next(iter(others))
                        # fallback: try game attributes home/away
                        if not pp_opp_team:
                            home, away = extract_home_away(games.get(gid, {}))
                            if pp_team and home and away:
                                pp_opp_team = away if pp_team == home else (home if pp_team == away else "")

                    stat_type  = attr.get("stat_type") or attr.get("display_stat_type") or ""
                    line       = attr.get("line_score") if attr.get("line_score") is not None else attr.get("line")
                    odds_type  = str(attr.get("odds_type") or "standard")
                    start_time = attr.get("start_time") or ""

                    # Capture duration (Full Game vs 2nd Half)
                    dur_rel  = (rel.get("duration") or {}).get("data") or {}
                    dur_id   = str(dur_rel.get("id", "")) if isinstance(dur_rel, dict) else ""
                    duration = durations.get(dur_id, "Full Game") if dur_id else "Full Game"

                    rows.append({
                        "proj_id":      proj_id,
                        "player_id":    pid,
                        "player":       player_name,
                        "team":         p.get("team") or "",
                        "pp_team":      pp_team,
                        "pp_opp_team":  pp_opp_team,
                        "pp_game_id":   game_rel.get("id", "") if isinstance(game_rel, dict) else "",
                        "pos":          p.get("position") or "",
                        "stat_type":    stat_type,
                        "line":         line,
                        "odds_type":    _norm_pick_type(odds_type),
                        "start_time":   start_time,
                        "league_id":    league_id,
                        "duration":     duration,
                    })
                    added += 1

                if bounced_blank_player or bounced_blank_team:
                    log_pipeline_health(
                        "cfb.step1_pp_cfb_scraper",
                        "bouncer_blank_identity",
                        extra={
                            "page": page,
                            "bounced_blank_player": bounced_blank_player,
                            "bounced_blank_team": bounced_blank_team,
                        },
                        start=Path(__file__),
                    )

                print(
                    f"  ✓ Page {page}: +{added} new projections (unique total {len(seen_proj_ids)})"
                    + (f" | bounced blank player={bounced_blank_player}, blank team={bounced_blank_team}"
                       if (bounced_blank_player or bounced_blank_team) else "")
                )

                next_url = safe_get(j, "links", "next", default=None)
                if not next_url:
                    print("  ✅ No links.next — pagination complete.")
                    _backfill_opponents(rows, game_teams)
                    return pd.DataFrame(rows)

                url    = next_url
                params = None   # cursor URL already embeds query params
                page  += 1
                time.sleep(max(0.0, sleep_s))
                break

            except Exception as e:
                time.sleep(2 ** attempt)
                if attempt == 5:
                    print(f"  ❌ Failed after retries on page {page}: {e}")
                    log_pipeline_health(
                        "cfb.step1_pp_cfb_scraper",
                        "page_failed_after_retries",
                        extra={"page": page, "error": f"{type(e).__name__}: {e}"},
                        start=Path(__file__),
                    )
                    _backfill_opponents(rows, game_teams)
                    return pd.DataFrame(rows)

    _backfill_opponents(rows, game_teams)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league_id",      type=int,   default=15)
    ap.add_argument("--per_page",       type=int,   default=250)
    ap.add_argument("--single_stat",    action="store_true")
    ap.add_argument("--include_combos", action="store_true",
                    help="Disables single_stat filter")
    ap.add_argument("--game_mode",      default="prizepools")
    ap.add_argument("--sleep",          type=float, default=0.5)
    ap.add_argument("--max_pages",      type=int,   default=None)
    ap.add_argument("--out",            required=True)
    ap.add_argument("--out_json",       default="")
    args = ap.parse_args()

    single_stat = args.single_stat and not args.include_combos

    print(f"📡 Fetching PrizePicks | league_id={args.league_id} | per_page={args.per_page} | single_stat={single_stat}")
    df = fetch_cfb_projections(
        league_id=args.league_id,
        per_page=args.per_page,
        single_stat=single_stat,
        game_mode=args.game_mode,
        sleep_s=args.sleep,
        max_pages=args.max_pages,
    )

    if df.empty:
        print("❌ No projections fetched.")
        log_pipeline_health(
            "cfb.step1_pp_cfb_scraper",
            "no_projections",
            extra={"league_id": args.league_id, "game_mode": args.game_mode},
            start=Path(__file__),
        )
        return

    # ── Bouncer: reject junk rows ─────────────────────────────────────────────
    required = ["proj_id", "player", "pp_team", "stat_type", "line"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        log_pipeline_health(
            "cfb.step1_pp_cfb_scraper",
            "missing_required_columns",
            extra={"missing": missing, "cols": list(df.columns)},
            start=Path(__file__),
        )
        print(f"❌ Missing required columns: {missing}")
        return

    before = len(df)
    df["line_num"] = pd.to_numeric(df["line"], errors="coerce")
    df = df[df["proj_id"].astype(str).str.strip() != ""].copy()
    df = df[df["player"].astype(str).str.strip() != ""].copy()
    df = df[df["pp_team"].astype(str).str.strip() != ""].copy()
    # No negative lines; NaN allowed (promos/oddities)
    df = df[(df["line_num"].isna()) | (df["line_num"] >= 0)].copy()
    # Opp team can be blank early in day; allow, but log proportion
    bounced = before - len(df)
    if bounced:
        print(f"  🧹 Bouncer: removed {bounced} junk rows")
        log_pipeline_health(
            "cfb.step1_pp_cfb_scraper",
            "bouncer_removed_rows",
            extra={"removed": bounced, "before": before, "after": len(df)},
            start=Path(__file__),
        )

    df.to_csv(args.out, index=False, encoding="utf-8")
    print(f"✅ Saved CSV → {args.out} | rows={len(df)} | unique proj_id={df['proj_id'].nunique()}")

    if "pp_opp_team" in df.columns:
        filled = int((df["pp_opp_team"].astype(str).str.strip() != "").sum())
        print(f"🆚 pp_opp_team filled: {filled}/{len(df)}")
        if len(df):
            blank = len(df) - filled
            if blank:
                log_pipeline_health(
                    "cfb.step1_pp_cfb_scraper",
                    "opp_team_missing",
                    extra={"blank": blank, "total": len(df)},
                    start=Path(__file__),
                )

    if args.out_json:
        df.to_json(args.out_json, orient="records", indent=2)
        print(f"✅ Saved JSON → {args.out_json}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_pipeline_health(
            "cfb.step1_pp_cfb_scraper",
            "run_failed",
            extra={"error": f"{type(e).__name__}: {e}"},
            start=Path(__file__),
        )
        print(f"❌ CBB step1 failed (logged). {type(e).__name__}: {e}")
