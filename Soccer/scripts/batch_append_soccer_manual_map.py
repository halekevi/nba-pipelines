#!/usr/bin/env python3
"""
Append resolved ESPN athlete IDs to pp_to_espn_id_map_soccer.csv from an
unmatched_soccer_players_*.csv dump (same resolution rules as step2).

Also appends one line per resolution to soccer_id_batch_resolve_log.csv for audit.

Usage (from repo root):
  py -3.14 Soccer/scripts/batch_append_soccer_manual_map.py \\
      --unmatched Soccer/outputs/unmatched_soccer_players_2026-04-09.csv \\
      --rostercache Soccer/soccer_roster_cache.csv

  py -3.14 Soccer/scripts/batch_append_soccer_manual_map.py --latest-unmatched
"""

from __future__ import annotations

import argparse
import csv
import glob
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_scripts = Path(__file__).resolve().parent
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

import step2_attach_picktypes_soccer as s2  # noqa: E402


def _latest_unmatched(outputs_dir: Path) -> Path | None:
    files = sorted(glob.glob(str(outputs_dir / "unmatched_soccer_players_*.csv")))
    if not files:
        return None
    return Path(files[-1])


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch-resolve soccer PP names to ESPN IDs.")
    ap.add_argument("--unmatched", default="", help="Path to unmatched_soccer_players_*.csv")
    ap.add_argument(
        "--latest-unmatched",
        action="store_true",
        help="Use newest Soccer/outputs/unmatched_soccer_players_*.csv",
    )
    ap.add_argument(
        "--manual",
        default="",
        help="Output manual map CSV (default: alongside unmatched parent)",
    )
    ap.add_argument(
        "--rostercache",
        default="",
        help="Roster cache CSV (default: Soccer/outputs/soccer_roster_cache.csv)",
    )
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--refresh-roster", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parents[2]  # .../Soccer/scripts/file.py -> repo root
    if not (repo / "Soccer").is_dir():
        repo = Path(__file__).resolve().parents[1]
    outputs_dir = repo / "Soccer" / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    unmatched_path = Path(args.unmatched) if args.unmatched else None
    if args.latest_unmatched or not unmatched_path:
        unmatched_path = _latest_unmatched(outputs_dir)
    if not unmatched_path or not unmatched_path.is_file():
        print("ERROR: no unmatched file (use --unmatched or --latest-unmatched)")
        return 1

    manual_path = Path(args.manual) if args.manual else (unmatched_path.parent / "pp_to_espn_id_map_soccer.csv")
    log_path = unmatched_path.parent / "soccer_id_batch_resolve_log.csv"
    roster_cache = Path(args.rostercache) if args.rostercache else (outputs_dir / "soccer_roster_cache.csv")
    if not roster_cache.is_absolute() and not roster_cache.is_file():
        roster_cache = outputs_dir / roster_cache.name
    if not roster_cache.is_file():
        roster_cache = repo / "Soccer" / roster_cache.name

    df = pd.read_csv(unmatched_path, dtype=str, encoding="utf-8-sig").fillna("")
    col_player = "player_name" if "player_name" in df.columns else "player"
    if col_player not in df.columns:
        print("ERROR: need player_name or player column")
        return 1
    if "team" not in df.columns:
        print("ERROR: need team column")
        return 1

    manual_map: dict[tuple[str, str], str] = {}
    if manual_path.exists():
        mdf = pd.read_csv(manual_path, dtype=str, encoding="utf-8-sig").fillna("")
        for _, rr in mdf.iterrows():
            pname = s2.norm_name(rr.get("player_name", rr.get("player", "")))
            pteam = s2.norm_team(rr.get("team", ""))
            aid = str(rr.get("espn_athlete_id", rr.get("espn_player_id", ""))).strip()
            if pname and pteam and aid:
                manual_map[(pname, pteam)] = aid

    roster_map, roster_last_team = s2.build_roster_id_map(
        s2.LEAGUE_SLUGS_FOR_ROSTER,
        workers=args.workers,
        roster_cache_path=str(roster_cache),
        force_refresh=args.refresh_roster,
    )

    uniq = (
        df[[col_player, "team"]]
        .drop_duplicates()
        .rename(columns={col_player: "player_name"})
    )

    new_rows: list[dict[str, str]] = []
    log_rows: list[dict[str, str]] = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for _, rr in uniq.iterrows():
        raw_p = str(rr["player_name"]).strip()
        raw_t = str(rr["team"]).strip()
        if not raw_p or not raw_t:
            continue
        pn = s2.norm_name(raw_p)
        tn = s2.norm_team(raw_t)
        if (pn, tn) in manual_map:
            continue
        aid = s2.resolve_soccer_player_espn_id(raw_p, raw_t, roster_map, roster_last_team, manual_map)
        if not aid:
            continue
        manual_map[(pn, tn)] = aid
        new_rows.append(
            {"player_name": raw_p, "team": raw_t, "espn_athlete_id": aid, "source": "batch_resolve"}
        )
        log_rows.append(
            {
                "ts_utc": ts,
                "player_name": raw_p,
                "team": raw_t,
                "espn_athlete_id": aid,
                "unmatched_file": unmatched_path.name,
            }
        )

    print(f"Resolved {len(new_rows)} new (player, team) pairs from {unmatched_path.name}")

    if args.dry_run:
        for row in new_rows[:30]:
            print(" ", row)
        if len(new_rows) > 30:
            print(f"  ... and {len(new_rows) - 30} more")
        return 0

    if new_rows:
        write_header = not manual_path.exists()
        with manual_path.open("a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["player_name", "team", "espn_athlete_id", "source"],
                extrasaction="ignore",
            )
            if write_header:
                w.writeheader()
            w.writerows(new_rows)
        print(f"Appended → {manual_path}")

    if log_rows:
        write_log_header = not log_path.exists()
        with log_path.open("a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["ts_utc", "player_name", "team", "espn_athlete_id", "unmatched_file"],
            )
            if write_log_header:
                w.writeheader()
            w.writerows(log_rows)
        print(f"Log → {log_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
