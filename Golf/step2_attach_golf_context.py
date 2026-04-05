#!/usr/bin/env python3
"""
Stub: attach golf context (course fit score, strokes-gained fields, weather signal).

Expected to read step1 CSV and emit enriched CSV for future ranking steps.

Usage:
    py -3.14 Golf/step2_attach_golf_context.py --input outputs/step1_golf_props.csv --output outputs/step2_golf_context.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def main() -> None:
    ap = argparse.ArgumentParser(description="Golf step2 stub — pass-through with placeholder columns.")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default="outputs/step2_golf_context.csv")
    args = ap.parse_args()
    p = Path(args.input)
    if not p.is_file():
        df = pd.DataFrame()
    else:
        df = pd.read_csv(p)
    for c in ("course_fit_score", "sg_ott", "sg_app", "sg_arg", "weather_signal"):
        if c not in df.columns:
            df[c] = pd.NA
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"[Golf step2 stub] Wrote {out} ({len(df)} rows).")


if __name__ == "__main__":
    main()
