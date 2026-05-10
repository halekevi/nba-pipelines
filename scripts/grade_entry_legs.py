#!/usr/bin/env python3
"""
Grade MyTicketPerformance entry_legs against historical_actuals.db.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone
from difflib import get_close_matches
from pathlib import Path
from typing import Any

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
from ensure_local_cache import ensure_local_cache
from sport_league_map import league_to_sport

_cache_dir = Path(ensure_local_cache(str(Path(__file__).resolve().parents[1])))

import build_player_consistency as bpc

REPO_ROOT = Path(__file__).resolve().parents[1]
HIST_DB = _cache_dir / "historical_actuals.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalize_player_name(name: str) -> str:
    s = _strip_accents(str(name or "").lower().strip())
    s = re.sub(r"\b(jr\.?|sr\.?|ii|iii|iv|v)\b\.?", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def find_myticket_db() -> Path | None:
    for p in (
        REPO_ROOT / "data" / "db" / "MyTicketPerformance.db",
        REPO_ROOT / "MyTicketPerformance.db",
        REPO_ROOT / "data" / "MyTicketPerformance.db",
        REPO_ROOT / "data" / "cache" / "MyTicketPerformance.db",
    ):
        if p.is_file():
            return p
    return None


def parse_direction(text: str | None) -> str | None:
    if not text:
        return None
    u = str(text).upper()
    if re.search(r"\b(OVER|MORE|HIGHER|O\s*[\+\-])\b", u) or " MORE " in f" {u} ":
        return "OVER"
    if re.search(r"\b(UNDER|LESS|LOWER|U\s*[\+\-])\b", u) or " LESS " in f" {u} ":
        return "UNDER"
    if "OVER" in u:
        return "OVER"
    if "UNDER" in u:
        return "UNDER"
    return None


def parse_game_date(description: str | None, created_at: str | None) -> str | None:
    if description:
        m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", str(description))
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    if created_at:
        try:
            ts = created_at.replace("Z", "+00:00")
            return str(datetime.fromisoformat(ts).date())
        except ValueError:
            try:
                return str(datetime.fromisoformat(created_at[:19]).date())
            except ValueError:
                pass
    return None


def stat_column_for_prop(norm_prop: str, sport: str) -> str | None:
    n = norm_prop.strip()
    if n in ("Blks+Stls", "Blks Stls", "Stocks"):
        return "blks_stls"
    m = {
        "Points": "points",
        "Rebounds": "rebounds",
        "Assists": "assists",
        "PRA": "pra",
        "Pts+Asts": "pts_asts",
        "Pts+Rebs": "pts_rebs",
        "Rebs+Asts": "rebs_asts",
        "Threes": "threes_made",
        "Steals": "steals",
        "Blocks": "blocks",
        "Turnovers": "turnovers",
        "Fantasy Score": "fantasy_score",
        "FTA": "fta",
        "FG Attempted": "fg_attempted",
        "Blks+Stls": "blks_stls",
        "Shots": "shots",
        "Saves": "saves",
        "Passes": "passes_attempted",
        "Shots on Target": "shots_on_target",
        "Goals": "goals",
        "2-PT Made": "fg_attempted",
    }
    if n in m:
        return m[n]
    if n == "Points" and sport == "NHL":
        return "points"
    return None


def grade_leg(actual: float | None, line: float, direction: str) -> str:
    if actual is None:
        return "VOID"
    if direction == "OVER":
        if actual > line:
            return "HIT"
        if actual < line:
            return "MISS"
        return "PUSH"
    if direction == "UNDER":
        if actual < line:
            return "HIT"
        if actual > line:
            return "MISS"
        return "PUSH"
    return "VOID"


def ensure_results_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entry_leg_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT,
            leg_index INTEGER,
            player_name TEXT,
            stat_type TEXT,
            line REAL,
            direction TEXT,
            league TEXT,
            game_date TEXT,
            actual_value REAL,
            result TEXT,
            graded_at TEXT,
            source TEXT,
            UNIQUE(entry_id, leg_index)
        )
        """
    )


def load_logs_for_date(
    hconn: sqlite3.Connection, sport: str, game_date: str
) -> list[tuple[str, str, dict[str, Any]]]:
    cur = hconn.execute(
        "SELECT * FROM player_game_logs WHERE sport = ? AND game_date = ?",
        (sport, game_date),
    )
    cols = [d[0] for d in cur.description]
    out: list[tuple[str, str, dict[str, Any]]] = []
    for row in cur.fetchall():
        rec = dict(zip(cols, row))
        pn = str(rec.get("player_name") or "")
        out.append((normalize_player_name(pn), pn, rec))
    return out


def resolve_player_row(
    candidates: list[tuple[str, str, dict[str, Any]]],
    want_name: str,
    logf: list[str],
    sport: str,
) -> tuple[dict[str, Any] | None, str | None]:
    nk = normalize_player_name(want_name)
    for nrm, full, rec in candidates:
        if str(rec.get("sport") or "") != sport:
            continue
        if nrm == nk:
            return rec, full
    names = [full for _, full, rec in candidates if str(rec.get("sport") or "") == sport]
    nrm_list = [normalize_player_name(x) for x in names]
    match = get_close_matches(nk, nrm_list, n=1, cutoff=0.85)
    if match:
        idx = nrm_list.index(match[0])
        full = names[idx]
        logf.append(f"Fuzzy matched: {want_name!r} -> {full!r}")
        for nrm, fn, rec in candidates:
            if fn == full and str(rec.get("sport") or "") == sport:
                return rec, full
    return None, None


def compute_actual(rec: dict[str, Any], col: str, norm_prop: str) -> float | None:
    if col == "fg_attempted" and norm_prop == "2-PT Made":
        fga = rec.get("fg_attempted")
        tpm = rec.get("threes_made")
        try:
            if fga is None:
                return None
            fga_f = float(fga)
            tpm_f = float(tpm) if tpm is not None else 0.0
            return max(0.0, fga_f - tpm_f)
        except (TypeError, ValueError):
            return None
    v = rec.get(col)
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Grade ticket entry legs vs historical game logs.")
    ap.add_argument("--sport", choices=("NBA", "CBB", "NHL", "Soccer"), default=None)
    ap.add_argument("--regraded", action="store_true", help="Replace all existing entry_leg_results")
    args = ap.parse_args()

    mtp = find_myticket_db()
    if not mtp:
        print("MyTicketPerformance.db not found (checked repo root, data/, data/cache/).")
        return
    if not HIST_DB.is_file():
        print(f"historical_actuals.db not found at {HIST_DB}")
        return

    hconn = sqlite3.connect(str(HIST_DB))
    mconn = sqlite3.connect(str(mtp))
    try:
        ensure_results_table(mconn)
        if args.regraded:
            mconn.execute("DELETE FROM entry_leg_results")
            mconn.commit()

        q = """
            SELECT l.entry_id, l.leg_index, l.player_name, l.stat_type, l.line, l.description, l.league,
                   e.created_at
            FROM entry_legs l
            LEFT JOIN user_entries e ON e.entry_id = l.entry_id
        """
        try:
            legs = mconn.execute(q).fetchall()
        except sqlite3.OperationalError as ex:
            print(f"Cannot read entry_legs: {ex}")
            return

        totals = {"HIT": 0, "MISS": 0, "PUSH": 0, "VOID": 0}
        by_sport: dict[str, dict[str, int]] = {}

        for row in legs:
            entry_id, leg_index, player_name, stat_type, line, description, league, created_at = row
            sport = league_to_sport(league)
            if not sport or not player_name or line is None:
                totals["VOID"] += 1
                continue
            if args.sport and sport != args.sport:
                continue
            direction = parse_direction(description)
            game_date = parse_game_date(description, created_at)
            if not direction or not game_date:
                mconn.execute(
                    """
                    INSERT INTO entry_leg_results (
                        entry_id, leg_index, player_name, stat_type, line, direction, league, game_date,
                        actual_value, result, graded_at, source
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(entry_id, leg_index) DO UPDATE SET
                        actual_value=excluded.actual_value,
                        result=excluded.result,
                        graded_at=excluded.graded_at,
                        source=excluded.source,
                        game_date=excluded.game_date,
                        direction=excluded.direction
                    """,
                    (
                        str(entry_id),
                        int(leg_index or 0),
                        str(player_name),
                        str(stat_type or ""),
                        float(line),
                        direction or "",
                        str(league or ""),
                        game_date or "",
                        None,
                        "VOID",
                        _now_iso(),
                        "",
                    ),
                )
                totals["VOID"] += 1
                continue

            raw_st = str(stat_type or "")
            prop_norm = bpc._normalize_prop_type(raw_st, sport)
            col = stat_column_for_prop(prop_norm, sport)
            if not col and "2" in raw_st.upper() and "PT" in raw_st.upper():
                col = "fg_attempted"
                prop_norm = "2-PT Made"
            if not col:
                mconn.execute(
                    """
                    INSERT INTO entry_leg_results (
                        entry_id, leg_index, player_name, stat_type, line, direction, league, game_date,
                        actual_value, result, graded_at, source
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(entry_id, leg_index) DO UPDATE SET
                        result=excluded.result, graded_at=excluded.graded_at, actual_value=excluded.actual_value
                    """,
                    (
                        str(entry_id),
                        int(leg_index or 0),
                        str(player_name),
                        raw_st,
                        float(line),
                        direction,
                        str(league or ""),
                        game_date,
                        None,
                        "VOID",
                        _now_iso(),
                        "",
                    ),
                )
                totals["VOID"] += 1
                continue

            candidates = load_logs_for_date(hconn, sport, game_date)
            fuzzy_log: list[str] = []
            rec, _matched = resolve_player_row(candidates, str(player_name), fuzzy_log, sport)
            for msg in fuzzy_log:
                print(msg)

            if not rec:
                res = "VOID"
                actual = None
                src = ""
            else:
                actual = compute_actual(rec, col, prop_norm)
                res = grade_leg(actual, float(line), direction)
                src = str(rec.get("source") or "")

            mconn.execute(
                """
                INSERT INTO entry_leg_results (
                    entry_id, leg_index, player_name, stat_type, line, direction, league, game_date,
                    actual_value, result, graded_at, source
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(entry_id, leg_index) DO UPDATE SET
                    player_name=excluded.player_name,
                    stat_type=excluded.stat_type,
                    line=excluded.line,
                    direction=excluded.direction,
                    league=excluded.league,
                    game_date=excluded.game_date,
                    actual_value=excluded.actual_value,
                    result=excluded.result,
                    graded_at=excluded.graded_at,
                    source=excluded.source
                """,
                (
                    str(entry_id),
                    int(leg_index or 0),
                    str(player_name),
                    str(stat_type or ""),
                    float(line),
                    direction,
                    str(league or ""),
                    game_date,
                    actual,
                    res,
                    _now_iso(),
                    src,
                ),
            )
            totals[res] = totals.get(res, 0) + 1
            by_sport.setdefault(sport, {"n": 0, "hits": 0})
            if res in ("HIT", "MISS"):
                by_sport[sport]["n"] += 1
                if res == "HIT":
                    by_sport[sport]["hits"] += 1

        mconn.commit()

        decided = totals["HIT"] + totals["MISS"]
        print("\n--- Entry leg grading summary ---")
        print(f"Total legs graded: {sum(totals.values())}")
        if decided:
            print(f"  HIT:  {totals['HIT']} ({100.0 * totals['HIT'] / decided:.1f}% of HIT+MISS)")
        else:
            print(f"  HIT:  {totals['HIT']}")
        print(f"  MISS: {totals['MISS']}")
        print(f"  PUSH: {totals['PUSH']}")
        print(f"  VOID: {totals['VOID']} (player/game not found or unmapped stat)")
        print("\nBy sport (HIT+MISS only):")
        for sp, d in sorted(by_sport.items()):
            n, h = d["n"], d["hits"]
            hr = (100.0 * h / n) if n else 0.0
            print(f"  {sp}: {n} legs, {hr:.1f}% hit rate")
    finally:
        hconn.close()
        mconn.close()


if __name__ == "__main__":
    main()
