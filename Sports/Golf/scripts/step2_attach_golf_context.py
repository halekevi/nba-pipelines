#!/usr/bin/env python3
"""
step2_attach_golf_context.py — pass-through + placeholder golf context columns.

Reads step1 CSV and emits enriched CSV for step7 ranking.

Run:
  py -3.14 Sports/Golf/scripts/step2_attach_golf_context.py \
      --input outputs/step1_golf_props.csv --output outputs/step2_golf_context.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="Golf step2 — attach placeholder context columns.")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default="outputs/step2_golf_context.csv")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_absolute():
        inp = root / inp
    if not inp.is_file():
        df = pd.DataFrame()
    else:
        df = pd.read_csv(inp, low_memory=False)

    for c in ("course_fit_score", "sg_ott", "sg_app", "sg_arg", "weather_signal"):
        if c not in df.columns:
            df[c] = pd.NA

    out = Path(args.output)
    if not out.is_absolute():
        out = root / out
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"[Golf step2] Wrote {out} ({len(df)} rows)")


if __name__ == "__main__":
    main()
