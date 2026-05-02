#!/usr/bin/env python3
"""
step4_attach_player_stats_nhl.py  (DB version)

Replaces api-web.nhle.com live fetching with indexed reads from proporacle_ref.db.
The DB is populated nightly by build_boxscore_ref.py.

Usage:
    py step4_attach_player_stats_nhl.py \
        --input  outputs/step3_nhl_with_defense.csv \
        --output outputs/step4_nhl_with_stats.csv

NHL lookup is by player name (no ESPN ID in the NHL pipeline).
The name match is exact first, then case-insensitive fallback.
If names still miss, run with --show-misses to see which players need
name normalization in the DB.
"""

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs
try:
    from tqdm import tqdm as _tqdm
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "tqdm", "--break-system-packages", "-q"])
    from tqdm import tqdm as _tqdm

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
    ap.add_argument("--input",       default="outputs/step3_nhl_with_defense.csv")
    ap.add_argument("--output",      default="outputs/step4_nhl_with_stats.csv")
    ap.add_argument("--cache",       default="",
                    help="Legacy arg — ignored (DB is the cache now)")
    ap.add_argument("--season",      default="",
                    help="Legacy arg — ignored (DB holds all seasons)")
    ap.add_argument("--n",           type=int, default=10)
    ap.add_argument("--db",          default="", help="Override DB path")
    ap.add_argument("--show-misses", action="store_true",
                    help="Print players with NO_DATA to help diagnose name mismatches")
    ap.add_argument("--summary",     action="store_true")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else DB_PATH
    con = open_db(db_path)

    if args.summary:
        db_summary(con)
        return

    print(f"→ Loading slate: {args.input}")
    slate = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig").fillna("")
    print(f"  {len(slate)} rows")

    # NHL pipeline uses player_name column
    id_col = next(
        (c for c in ["player_name", "player", "Player"] if c in slate.columns),
        None
    )
    if not id_col:
        raise SystemExit(f"No player name column found. Columns: {list(slate.columns)}")

    # prop column name varies across NHL pipeline versions
    prop_col = next(
        (c for c in ["stat_norm", "prop_norm", "prop_type"] if c in slate.columns),
        "prop_norm"
    )

    print(f"\n→ Attaching NHL stats from DB (id_col={id_col}, prop_col={prop_col}, n={args.n})...")
    with _tqdm(total=len(slate), desc="  Attaching stats", unit="row") as pbar:
        slate, counts = attach_stats(
            slate, "nhl", con,
            id_col=id_col,
            prop_col=prop_col,
            n=args.n
        )
        pbar.update(len(slate))

    if args.show_misses:
        misses = slate[slate["stat_status"] == "NO_DATA"][
            [id_col, prop_col, "line"]
        ].drop_duplicates()
        if not misses.empty:
            print(f"\n⚠️  NO_DATA players ({len(misses)} unique):")
            print(misses.to_string(index=False))

    slate.to_csv(args.output, index=False, encoding="utf-8-sig")
    copy_pipeline_output_to_dated_dirs(
        output_path=args.output,
        df=slate,
        sport_dir_name="NHL",
        repo_root=_REPO_ROOT,
    )
    print(f"\n✅ Saved → {args.output}  ({len(slate)} rows)")
    print("\nstat_status breakdown:")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        if v > 0:
            print(f"  {k:25s} {v:>5}")


if __name__ == "__main__":
    main()
