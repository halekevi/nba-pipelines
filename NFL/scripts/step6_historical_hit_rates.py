#!/usr/bin/env python3
"""
NFL step6 — historical line hit rates (skeleton).

# TODO: populate with 2024-2025 season historical data
# once PrizePicks NFL history is scraped

For now: copies step2 rows and sets hit_rate to empty / NaN so downstream
schemas can be wired before preseason data exists.

  set NFL_PIPELINE_ACTIVE=1
  py -3.14 scripts/step6_historical_hit_rates.py
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


def main() -> None:
    require_nfl_pipeline_active_or_exit()

    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/outputs/step2_clean_props.csv")
    ap.add_argument("--output", default="data/outputs/step6_hit_rates.csv")
    args = ap.parse_args()

    in_path = Path(args.input)
    if not in_path.is_file():
        print(f"[NFL step6] Missing input: {in_path}")
        sys.exit(1)

    df = pd.read_csv(in_path, encoding="utf-8-sig")
    df = df.copy()
    df["hit_rate"] = pd.NA

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"[NFL step6] Wrote {out_path} rows={len(df)} (hit_rate placeholder)")


if __name__ == "__main__":
    main()
