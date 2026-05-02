#!/usr/bin/env python3
"""
step4_attach_player_stats_soccer.py  (DB version)

Replaces live ESPN summary API calls (was 30+ min, 1000+ API calls)
with indexed reads from proporacle_ref.db.

The DB is populated nightly by build_boxscore_ref.py (called from run_grader.ps1).

Usage:
    py step4_attach_player_stats_soccer.py \
        --input  step3_soccer_with_defense.csv \
        --output step4_soccer_with_stats.csv

Soccer lookup is by espn_player_id. The ID is attached upstream in step1/step2.
If espn_player_id is missing from your slate, the row gets stat_status=NO_ID.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# Walk up from this file to find scripts/step4_db_reader.py
_here = Path(__file__).resolve().parent
for _ in range(6):
    if (_here / "scripts" / "step4_db_reader.py").exists():
        sys.path.insert(0, str(_here / "scripts"))
        break
    _here = _here.parent
from step4_db_reader import open_db, attach_stats, db_summary, DB_PATH


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",       default="step3_soccer_with_defense.csv")
    ap.add_argument("--cache",       default="",
                    help="Legacy arg — ignored (DB is the cache now)")
    ap.add_argument("--output",      default="step4_soccer_with_stats.csv")
    ap.add_argument("--n",           type=int, default=10)
    ap.add_argument("--db",          default="", help="Override DB path")
    ap.add_argument("--show-misses", action="store_true",
                    help="Print players with NO_DATA or NO_ID")
    ap.add_argument("--summary",     action="store_true")
    # Legacy args accepted but ignored (kept for run_pipeline.ps1 compatibility)
    ap.add_argument("--workers",     type=int, default=6)
    ap.add_argument("--season",      default="2025")
    ap.add_argument("--debug_misses",  default="")
    ap.add_argument("--debug_player",  default="")
    ap.add_argument("--debug_player_raw", default="")
    ap.add_argument("--league",      default="")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else DB_PATH
    con = open_db(db_path)

    if args.summary:
        db_summary(con)
        return

    print(f"→ Loading slate: {args.input}")
    slate = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig").fillna("")
    print(f"  {len(slate)} rows")

    # espn_player_id is the primary key for soccer DB lookups
    id_col = next(
        (c for c in ["espn_player_id", "ESPN_PLAYER_ID", "espn_id"] if c in slate.columns),
        None
    )
    if not id_col:
        raise SystemExit(
            "No espn_player_id column found — soccer step4 requires ESPN IDs.\n"
            f"Columns available: {list(slate.columns)}"
        )

    print(f"\n→ Attaching Soccer stats from DB (id_col={id_col}, n={args.n})...")
    match_before = float((slate[id_col].astype(str).str.strip() != "").mean())
    print(f"[SOCCER ID] step4 input match rate: {match_before*100:.1f}% "
          f"({int((slate[id_col].astype(str).str.strip()!='').sum())}/{len(slate)})")
    slate, counts = attach_stats(
        slate, "soccer", con,
        id_col=id_col,
        n=args.n
    )
    match_after = float((slate[id_col].astype(str).str.strip() != "").mean())
    print(f"[SOCCER ID] step4 post-attach match rate: {match_after*100:.1f}% "
          f"({int((slate[id_col].astype(str).str.strip()!='').sum())}/{len(slate)})")

    if args.show_misses or args.debug_misses:
        bad = slate[slate["stat_status"].isin(["NO_DATA", "NO_ID"])][[
            id_col, "player", "prop_norm", "line", "stat_status"
        ]].drop_duplicates()
        if not bad.empty:
            print(f"\n⚠️  Missing stats ({len(bad)} rows):")
            print(bad.to_string(index=False))
        if args.debug_misses and not bad.empty:
            bad.to_csv(args.debug_misses, index=False, encoding="utf-8-sig")
            print(f"Wrote misses → {args.debug_misses}")

    slate.to_csv(args.output, index=False, encoding="utf-8-sig")
    unresolved = slate[slate[id_col].astype(str).str.strip() == ""].copy()
    if not unresolved.empty:
        if "start_time" in unresolved.columns and unresolved["start_time"].astype(str).str.strip().ne("").any():
            ds = pd.to_datetime(unresolved["start_time"], errors="coerce").min()
            date_tag = ds.strftime("%Y-%m-%d") if pd.notna(ds) else datetime.now().strftime("%Y-%m-%d")
        else:
            date_tag = datetime.now().strftime("%Y-%m-%d")
        out_unmatched = Path(args.output).resolve().parent / f"unmatched_soccer_players_{date_tag}.csv"
        keep_cols = [c for c in ["player", "team", "prop_type", "pp_game_id"] if c in unresolved.columns]
        unresolved[keep_cols].rename(columns={"player": "player_name"}).drop_duplicates().to_csv(
            out_unmatched, index=False, encoding="utf-8-sig"
        )
        print(f"[SOCCER ID] Wrote unresolved players: {out_unmatched}")
    print(f"\n✅ Saved → {args.output}  ({len(slate)} rows)")
    print("\nstat_status breakdown:")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        if v > 0:
            print(f"  {k:25s} {v:>5}")


if __name__ == "__main__":
    main()
