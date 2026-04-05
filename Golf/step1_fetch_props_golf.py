#!/usr/bin/env python3
"""
Stub: PrizePicks golf prop fetch (Playwright session pattern — mirror MLB step1).

Board URL / league id wiring TBD.

Usage:
    py -3.14 Golf/step1_fetch_props_golf.py --output outputs/step1_golf_props.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

COLS = [
    "projection_id",
    "player",
    "event",
    "prop_type",
    "line",
    "pick_type",
    "start_time",
]


def main() -> None:
    ap = argparse.ArgumentParser(description="Golf step1 stub — writes empty scaffold CSV.")
    ap.add_argument("--output", default="outputs/step1_golf_props.csv")
    args = ap.parse_args()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(columns=COLS).to_csv(out, index=False)
    print(f"[Golf step1 stub] Wrote {out} (0 rows). Implement Playwright fetch like MLB step1.")


if __name__ == "__main__":
    main()
