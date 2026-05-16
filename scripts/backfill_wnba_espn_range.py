#!/usr/bin/env python3
"""
Backfill WNBA boxscores from ESPN into:
  - Sports/WNBA/wnba_espn_cache.csv (rolling cache for step4)
  - data/cache/proporacle_ref.db table `wnba`

Use this to load a prior season (e.g. 2025) so step4 can attach each player's
last N games through a chosen end date (e.g. finals) via:

  step4_fetch_player_stats.py --attach-stats-through 2025-10-20 --attach-stats-season 2025 ...

Example (full 2025 regular season + playoffs into cache):
  python scripts/backfill_wnba_espn_range.py --from 2025-05-01 --to 2025-10-31 --season 2025

ESPN is queried by calendar date, not “N games per player”. To approximate “last games of 2025”
(playoffs / finals) use a late window, e.g. --preset late-2025 or finals-2025.

Presets (pair with --season 2025):
  full-2025     May–Oct 2025 (full calendar span we use for cache)
  late-2025     Sep 1 – Oct 31 2025 (postseason-heavy; good with early 2026 L5)
  finals-2025   Oct 1 – Oct 31 2025 (minimal tail — smallest useful chunk before 2026 season)
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent

# ISO (start, end) inclusive. Tune finals-2025 if ESPN shifts postseason dates in a given year.
PRESETS: dict[str, Tuple[str, str]] = {
    "full-2025": ("2025-05-01", "2025-10-31"),
    "late-2025": ("2025-09-01", "2025-10-31"),
    "finals-2025": ("2025-10-01", "2025-10-31"),
}
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.db_utils import ensure_wnba_schema, open_db, upsert_rows  # noqa: E402


def _load_wnba_step4():
    path = REPO / "Sports" / "WNBA" / "step4_fetch_player_stats.py"
    spec = importlib.util.spec_from_file_location("wnba_step4_fetch", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _expand_dates(d0: date, d1: date) -> list[date]:
    out: list[date] = []
    cur = d0
    while cur <= d1:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill WNBA ESPN games into cache + SQLite.")
    ap.add_argument(
        "--preset",
        choices=sorted(PRESETS.keys()),
        default="",
        help="Date range shortcut (overrides --from/--to when set). Use with --season.",
    )
    ap.add_argument("--from", dest="date_from", default="", help="Start calendar date YYYY-MM-DD (inclusive).")
    ap.add_argument("--to", dest="date_to", default="", help="End calendar date YYYY-MM-DD (inclusive).")
    ap.add_argument("--season", required=True, help="Value stored in cache SEASON column (e.g. 2025).")
    ap.add_argument(
        "--cache",
        default=str(REPO / "Sports" / "WNBA" / "wnba_espn_cache.csv"),
        help="Path to wnba_espn_cache.csv",
    )
    ap.add_argument("--db", default="", help="Override SQLite path (default: data/cache/proporacle_ref.db)")
    ap.add_argument("--sleep", type=float, default=0.6)
    ap.add_argument("--retries", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    if args.preset:
        d0s, d1s = PRESETS[args.preset]
        d0 = date.fromisoformat(d0s)
        d1 = date.fromisoformat(d1s)
        print(f"[backfill] preset {args.preset}: {d0} .. {d1}")
    else:
        if not str(args.date_from).strip() or not str(args.date_to).strip():
            raise SystemExit("Provide --preset or both --from and --to (YYYY-MM-DD).")
        d0 = date.fromisoformat(args.date_from.strip())
        d1 = date.fromisoformat(args.date_to.strip())
    if d1 < d0:
        raise SystemExit("--to must be on or after --from")

    mod = _load_wnba_step4()

    cache_path = Path(args.cache)
    db_path = Path(args.db) if args.db else None
    con = open_db(db_path)
    ensure_wnba_schema(con)

    if cache_path.exists():
        cache = pd.read_csv(cache_path, dtype=str, encoding="utf-8-sig").fillna("")
    else:
        cache = pd.DataFrame()

    existing_events: set = set()
    incomplete_events: set = set()
    if not cache.empty and "event_id" in cache.columns:
        existing_events = set(cache["event_id"].astype(str).unique())
        if "PTS" in cache.columns:
            cache["_pts_num"] = pd.to_numeric(cache["PTS"], errors="coerce")
            grouped = cache.groupby(cache["event_id"].astype(str))["_pts_num"].apply(lambda s: int(s.notna().sum()))
            incomplete_events = set(grouped[grouped <= 0].index.tolist())
            cache = cache.drop(columns=["_pts_num"], errors="ignore")

    new_rows: list[dict] = []
    events_fetched = events_skipped = 0

    for d in _expand_dates(d0, d1):
        dt = datetime(d.year, d.month, d.day)
        if mod._is_allstar(dt):
            continue
        yyyymmdd = d.strftime("%Y%m%d")
        try:
            event_ids = mod.fetch_event_ids(yyyymmdd, args.timeout, args.retries, args.sleep)
        except Exception as e:
            print(f"  [WARN] Scoreboard failed {yyyymmdd}: {e}")
            continue

        for eid in event_ids:
            if eid in existing_events and eid not in incomplete_events:
                continue
            try:
                url = mod.SUMMARY_URL.format(event_id=eid)
                summary = mod.espn_get(url, args.timeout, args.retries, args.sleep)
                df_box = mod.parse_boxscore(summary)
                if df_box.empty:
                    events_skipped += 1
                    continue
                df_box["event_id"] = eid
                df_box["SEASON"] = str(args.season)
                new_rows.extend(df_box.to_dict("records"))

                rows_db = []
                for r in df_box.to_dict("records"):
                    rows_db.append(
                        {
                            "game_date": str(r.get("game_date", ""))[:10],
                            "event_id": str(r.get("event_id", "")),
                            "league": "WNBA",
                            "home_team": None,
                            "away_team": None,
                            "player": str(r.get("PLAYER_NAME", "")).strip(),
                            "team": str(r.get("TEAM", "")).strip().upper() or None,
                            "position": None,
                            "espn_athlete_id": str(r.get("ESPN_ATHLETE_ID", "")).strip() or None,
                            "minutes": mod._parse_minutes(r.get("MIN"))
                            if isinstance(r.get("MIN"), str)
                            else (r.get("MIN") if r.get("MIN") is not None else None),
                            "pts": float(r["PTS"])
                            if r.get("PTS") not in (None, "")
                            and not (isinstance(r.get("PTS"), float) and np.isnan(r.get("PTS")))
                            else None,
                            "reb": float(r["REB"])
                            if r.get("REB") not in (None, "")
                            and not (isinstance(r.get("REB"), float) and np.isnan(r.get("REB")))
                            else None,
                            "ast": float(r["AST"])
                            if r.get("AST") not in (None, "")
                            and not (isinstance(r.get("AST"), float) and np.isnan(r.get("AST")))
                            else None,
                            "stl": float(r["STL"])
                            if r.get("STL") not in (None, "")
                            and not (isinstance(r.get("STL"), float) and np.isnan(r.get("STL")))
                            else None,
                            "blk": float(r["BLK"])
                            if r.get("BLK") not in (None, "")
                            and not (isinstance(r.get("BLK"), float) and np.isnan(r.get("BLK")))
                            else None,
                            "tov": float(r["TO"])
                            if r.get("TO") not in (None, "")
                            and not (isinstance(r.get("TO"), float) and np.isnan(r.get("TO")))
                            else None,
                            "fgm": float(r["FGM"])
                            if r.get("FGM") not in (None, "")
                            and not (isinstance(r.get("FGM"), float) and np.isnan(r.get("FGM")))
                            else None,
                            "fga": float(r["FGA"])
                            if r.get("FGA") not in (None, "")
                            and not (isinstance(r.get("FGA"), float) and np.isnan(r.get("FGA")))
                            else None,
                            "fg3m": float(r["FG3M"])
                            if r.get("FG3M") not in (None, "")
                            and not (isinstance(r.get("FG3M"), float) and np.isnan(r.get("FG3M")))
                            else None,
                            "fg3a": float(r["FG3A"])
                            if r.get("FG3A") not in (None, "")
                            and not (isinstance(r.get("FG3A"), float) and np.isnan(r.get("FG3A")))
                            else None,
                            "fg2m": float(r["FG2M"])
                            if r.get("FG2M") not in (None, "")
                            and not (isinstance(r.get("FG2M"), float) and np.isnan(r.get("FG2M")))
                            else None,
                            "fg2a": float(r["FG2A"])
                            if r.get("FG2A") not in (None, "")
                            and not (isinstance(r.get("FG2A"), float) and np.isnan(r.get("FG2A")))
                            else None,
                            "ftm": float(r["FTM"])
                            if r.get("FTM") not in (None, "")
                            and not (isinstance(r.get("FTM"), float) and np.isnan(r.get("FTM")))
                            else None,
                            "fta": float(r["FTA"])
                            if r.get("FTA") not in (None, "")
                            and not (isinstance(r.get("FTA"), float) and np.isnan(r.get("FTA")))
                            else None,
                            "oreb": None,
                            "dreb": None,
                            "pf": None,
                            "pra": None,
                            "pr": None,
                            "pa": None,
                            "ra": None,
                            "bs": None,
                            "fantasy_score": None,
                        }
                    )
                upsert_rows(con, "wnba", rows_db)
                existing_events.add(eid)
                events_fetched += 1
            except Exception as e:
                print(f"  [WARN] Event {eid} ({yyyymmdd}): {e}")
                events_skipped += 1

    print(f"ESPN backfill: {events_fetched} events merged, {events_skipped} skipped/empty")

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        if incomplete_events:
            cache = cache[~cache["event_id"].astype(str).isin(incomplete_events)].copy()
        cache = pd.concat([cache, new_df], ignore_index=True) if not cache.empty else new_df
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache.to_csv(cache_path, index=False, encoding="utf-8-sig")
        print(f"Cache updated → {cache_path}  ({len(cache)} rows)")
    else:
        print("No new boxscore rows (cache already had this range or no games found).")

    con.close()


if __name__ == "__main__":
    main()
