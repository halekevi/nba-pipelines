#!/usr/bin/env python3
"""Rebuild outputs/<date>/nba/step1_pp_props_today.csv from dated step8 or combined slate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.backfill_line_history_date import load_recovery_frame  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="Slate date YYYY-MM-DD")
    ap.add_argument(
        "--output",
        default="",
        help="Override step1 path (default: outputs/<date>/nba/step1_pp_props_today.csv)",
    )
    args = ap.parse_args()
    date = str(args.date).strip()
    out = (
        Path(args.output)
        if str(args.output).strip()
        else REPO / "outputs" / date / "nba" / "step1_pp_props_today.csv"
    )
    df, source = load_recovery_frame(date)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"Wrote {len(df):,} rows from {source} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
