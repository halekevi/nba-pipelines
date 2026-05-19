#!/usr/bin/env python3
"""
NFL step4c — role context placeholders (Phase 2: nflverse snap counts).

Adds snap_pct_L3, snap_pct_season, role_stability_score with defaults until
nfl_snap_pct_cache.json is populated.

Run from NFL/ with NFL_PIPELINE_ACTIVE=1.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _nfl_pipeline_active import require_nfl_pipeline_active_or_exit

OUT_COLS = ("snap_pct_L3", "snap_pct_season", "role_stability_score")


def main() -> None:
    require_nfl_pipeline_active_or_exit()

    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/outputs/step3_nfl_with_defense.csv")
    ap.add_argument("--output", default="data/outputs/step3_nfl_with_defense.csv")
    args = ap.parse_args()

    in_path = Path(args.input)
    if not in_path.is_file():
        print(f"[NFL step4c] Missing input: {in_path}")
        sys.exit(1)

    df = pd.read_csv(in_path, encoding="utf-8-sig")
    for c in OUT_COLS:
        df[c] = pd.NA

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"[NFL step4c] Wrote placeholders {OUT_COLS} -> {out_path} rows={len(df)}")


if __name__ == "__main__":
    main()
