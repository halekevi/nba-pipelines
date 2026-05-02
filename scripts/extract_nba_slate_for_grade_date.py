#!/usr/bin/env python3
"""
Write outputs/<date>/step8_nba{1h|1q}_direction_clean_<date>.xlsx by filtering
a root step8 workbook to rows whose Game Time falls on --grade-date.

Used by run_grader.ps1 when the dated archive is missing but NBA\\step8_* exists
(mixed or wrong-day root slates).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd


def _game_dates(series: pd.Series) -> pd.Series:
    ts = pd.to_datetime(series, errors="coerce")
    return ts.dt.date


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Source step8 xlsx (ALL sheet)")
    ap.add_argument("--output", required=True, help="Dated output xlsx path")
    ap.add_argument("--grade-date", required=True, help="YYYY-MM-DD")
    args = ap.parse_args()

    inp = Path(args.input)
    outp = Path(args.output)
    if not inp.is_file():
        print(f"SKIP: input not found: {inp}", file=sys.stderr)
        sys.exit(0)

    xls = pd.ExcelFile(inp, engine="openpyxl")
    sheet = "ALL" if "ALL" in xls.sheet_names else xls.sheet_names[0]
    df = pd.read_excel(inp, sheet_name=sheet, engine="openpyxl")
    if "Game Time" not in df.columns and "Game Date" not in df.columns:
        print(f"SKIP: no 'Game Time' or 'Game Date' column in {inp.name}", file=sys.stderr)
        sys.exit(0)

    try:
        target = pd.to_datetime(args.grade_date).date()
    except Exception:
        print(f"SKIP: bad --grade-date {args.grade_date!r}", file=sys.stderr)
        sys.exit(0)

    ds = str(args.grade_date).strip()[:10]
    if "Game Date" in df.columns:
        gd = df["Game Date"].astype(str).str.strip().str[:10]
        nonempty = gd.ne("") & gd.ne("nan") & gd.ne("None")
        mask = gd.eq(ds) & nonempty
    else:
        row_days = _game_dates(df["Game Time"])
        mask = row_days == target
    sub = df.loc[mask].copy()
    if sub.empty:
        print(f"INFO: 0 rows on {target} in {inp.name} — not writing {outp.name}")
        sys.exit(0)

    outp.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(outp, engine="openpyxl") as writer:
        sub.to_excel(writer, sheet_name="ALL", index=False)
    print(f"OK: wrote {len(sub)} rows -> {outp}")


if __name__ == "__main__":
    main()
