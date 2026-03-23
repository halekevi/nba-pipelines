#!/usr/bin/env python3
"""
One-time migration: import outputs/synthetic/graded_*_synthetic_*.xlsx into
data/cache/synthetic_graded.db. Does not delete source files.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
from ensure_local_cache import ensure_local_cache

ensure_local_cache(str(Path(__file__).resolve().parents[1]))

import build_player_consistency as bpc
from build_synthetic_graded import SYNTHETIC_GRADED_DB, write_to_db

REPO_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_DIR = REPO_ROOT / "outputs" / "synthetic"
SYNTHETIC_GLOB = "graded_*_synthetic_*.xlsx"


def _season_from_filename(name: str) -> str | None:
    m = re.search(r"_synthetic_(.+)\.xlsx$", name, flags=re.I)
    return m.group(1).strip() if m else None


def main() -> None:
    ap = argparse.ArgumentParser(description="Migrate synthetic graded Excel files to SQLite.")
    ap.add_argument("--sport", choices=("NBA", "CBB", "NHL", "Soccer"), default=None)
    args = ap.parse_args()

    if not SYNTHETIC_DIR.is_dir():
        print(f"No directory {SYNTHETIC_DIR}")
        return

    paths = sorted(SYNTHETIC_DIR.glob(SYNTHETIC_GLOB))
    if not paths:
        print(f"No files matching {SYNTHETIC_GLOB} under {SYNTHETIC_DIR}")
        return

    dbp = str(SYNTHETIC_GRADED_DB)
    for path in paths:
        sport = bpc._sport_from_synthetic_filename(path.name)
        if not sport:
            print(f"  (skip) Unknown sport: {path.name}")
            continue
        if args.sport and sport != args.sport:
            continue
        df = bpc._read_graded_frame(path)
        if df is None or df.empty:
            print(f"  (skip) Empty/unreadable: {path.name}")
            continue
        if "season" not in df.columns:
            sn = _season_from_filename(path.name)
            if not sn:
                print(f"  (skip) No season column or filename pattern: {path.name}")
                continue
            df = df.copy()
            df["season"] = sn
        total_file = 0
        for season, g in df.groupby("season"):
            g2 = g.copy()
            if "sport" not in g2.columns:
                g2["sport"] = sport
            sn = str(season).strip()
            write_to_db(g2, sport, sn, dbp)
            total_file += len(g2)
        print(f"  {path.name}: {total_file} rows migrated (by season)")

    print()
    print("Migration complete.")
    print("Old Excel files can now be deleted from outputs\\synthetic\\")
    print("They are no longer needed.")
    print("To delete run:")
    print("  del outputs\\synthetic\\graded_*_synthetic_*.xlsx")


if __name__ == "__main__":
    main()
