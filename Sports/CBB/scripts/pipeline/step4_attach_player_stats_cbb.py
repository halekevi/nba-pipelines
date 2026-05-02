#!/usr/bin/env python3
"""
step4_attach_player_stats_cbb.py  (DB version)

CBB step4 — reads from proporacle_ref.db instead of live ESPN API calls.
Same structure as NBA step4 but queries the cbb table.

Usage:
    py step4_attach_player_stats_cbb.py \
        --slate step3_cbb_with_defense.csv \
        --out   step4_cbb_with_stats.csv
"""

import argparse
import sys
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
    ap.add_argument("--slate",    default="step3_cbb_with_defense.csv")
    ap.add_argument("--out",      default="step4_cbb_with_stats.csv")
    ap.add_argument("--date",     default="")
    ap.add_argument("--n",        type=int, default=10)
    ap.add_argument("--id-col",   default="espn_athlete_id",
                    help="Column with ESPN athlete ID")
    ap.add_argument("--db",       default="", help="Override DB path")
    ap.add_argument("--summary",  action="store_true")
    # Legacy args — accepted but ignored
    ap.add_argument("--cache",    default="")
    ap.add_argument("--season",   default="")
    ap.add_argument("--window",   type=int, default=2)
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else DB_PATH
    con = open_db(db_path)

    if args.summary:
        db_summary(con)
        return

    print(f"→ Loading slate: {args.slate}")
    slate = pd.read_csv(args.slate, dtype=str, encoding="utf-8-sig").fillna("")
    print(f"  {len(slate)} rows")

    id_col = args.id_col
    if id_col not in slate.columns:
        fallbacks = ["espn_athlete_id", "ESPN_ATHLETE_ID", "athlete_id", "player_id"]
        for fb in fallbacks:
            if fb in slate.columns:
                print(f"  ⚠️  '{id_col}' not found — using '{fb}'")
                id_col = fb
                break
        else:
            raise SystemExit(f"No ESPN ID column found. Columns: {list(slate.columns)}")

    print(f"\n→ Attaching CBB stats from DB (id_col={id_col}, n={args.n})...")

    # Sanity-check: confirm the DB has meaningful CBB coverage before proceeding.
    # If it's empty or nearly empty, warn loudly — the caller should be running
    # step5b_attach_boxscore_stats.py instead (which fetches live ESPN boxscores).
    try:
        import sqlite3 as _sq3
        _con_check = _sq3.connect(str(db_path))
        _cur = _con_check.execute(
            "SELECT COUNT(*) FROM player_game_log WHERE sport='cbb'"
            if "sport" in [r[1] for r in _con_check.execute("PRAGMA table_info(player_game_log)").fetchall()]
            else "SELECT COUNT(*) FROM player_game_log"
        )
        _cbb_rows = _cur.fetchone()[0]
        _con_check.close()
        if _cbb_rows < 100:
            print(f"\n  ⚠️  WARNING: DB has only {_cbb_rows} CBB rows in player_game_log.")
            print("  ⚠️  CBB stats are best attached via step5b_attach_boxscore_stats.py")
            print("  ⚠️  (which fetches live ESPN boxscores + uses cbb_boxscore_cache.csv)")
            print("  ⚠️  Continuing with DB path — all rows may end up with NO_TEAM_ID.\n")
    except Exception as _e:
        print(f"  [DB check skipped: {_e}]")

    slate, counts = attach_stats(slate, "cbb", con, id_col=id_col, n=args.n)

    slate.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"\n✅ Saved → {args.out}  ({len(slate)} rows)")
    print("\nstat_status breakdown:")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        if v > 0:
            print(f"  {k:25s} {v:>5}")


if __name__ == "__main__":
    main()
