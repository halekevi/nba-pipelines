#!/usr/bin/env python3
"""
Stub: PrizePicks NFL prop fetch (Playwright session pattern — mirror MLB step1).

League/board wiring TBD when NFL board is enabled on PrizePicks.

Usage:
    py -3.14 NFL/step1_fetch_props_nfl.py --output outputs/step1_nfl_props.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

COLS = [
    "projection_id",
    "player",
    "team",
    "opp_team",
    "prop_type",
    "line",
    "pick_type",
    "game_time",
]


def main() -> None:
    ap = argparse.ArgumentParser(description="NFL step1 stub — writes empty scaffold CSV.")
    ap.add_argument("--output", default="outputs/step1_nfl_props.csv")
    args = ap.parse_args()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(columns=COLS).to_csv(out, index=False)
    print(f"[NFL step1 stub] Wrote {out} (0 rows). Implement Playwright fetch like MLB step1.")


if __name__ == "__main__":
    main()
