#!/usr/bin/env python3
"""
Measure hit-rate lift by confidence_tier on graded prop history.

Usage:
  py -3.14 scripts/backtest_confidence_tier_lift.py
  py -3.14 scripts/backtest_confidence_tier_lift.py --sport NBA --min-n 50
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.analyze_graded_prop_winners import (  # noqa: E402
    exclude_non_rating_legs,
    load_graded_json_archive,
    load_unified,
    normalize_decided,
)

_DEFAULT_ROOTS = [
    _REPO / "ui_runner" / "graded_slate",
    _REPO / "outputs",
]


def _hr(sub: pd.DataFrame) -> tuple[float, int]:
    if sub.empty:
        return float("nan"), 0
    return float(sub["is_hit"].mean()), len(sub)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="", help="Filter sport code (NBA, NHL, …)")
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--out", default="data/reports/confidence_tier_lift.csv")
    ap.add_argument(
        "--xlsx",
        action="store_true",
        help="Load graded_*.xlsx workbooks instead of mobile JSON archives",
    )
    args = ap.parse_args()

    sport_filter = str(args.sport or "").strip().lower() or None
    if args.xlsx:
        raw = load_unified(_DEFAULT_ROOTS, sport=sport_filter)
    else:
        print("Loading graded_props JSON archives...")
        raw = load_graded_json_archive(sport=sport_filter)
        print(f"  {len(raw)} decided rows")
    if raw.empty:
        print("No graded rows found.")
        return

    decided = normalize_decided(raw)
    decided = exclude_non_rating_legs(decided)
    if decided.empty:
        print("No decided legs after filters.")
        return

    baseline_hr, baseline_n = _hr(decided)
    rows: list[dict] = [
        {
            "layer": "baseline",
            "hit_rate": baseline_hr,
            "n": baseline_n,
            "lift_pp": 0.0,
        }
    ]

    if "confidence_tier" not in decided.columns:
        print("confidence_tier column missing — run graded backfill or re-grade with latest pipeline.")
        return

    for tier in ("HIGH", "MED", "LOW"):
        sub = decided[decided["confidence_tier"].astype(str).str.upper().eq(tier)]
        hr, n = _hr(sub)
        if n < args.min_n:
            continue
        lift = (hr - baseline_hr) * 100.0 if pd.notna(hr) and pd.notna(baseline_hr) else float("nan")
        rows.append({"layer": f"tier_{tier}", "hit_rate": hr, "n": n, "lift_pp": lift})

    high_med = decided[decided["confidence_tier"].astype(str).str.upper().isin(["HIGH", "MED"])]
    hr, n = _hr(high_med)
    if n >= args.min_n:
        rows.append(
            {
                "layer": "tier_HIGH_or_MED",
                "hit_rate": hr,
                "n": n,
                "lift_pp": (hr - baseline_hr) * 100.0 if pd.notna(hr) else float("nan"),
            }
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = pd.DataFrame(rows)
    report.to_csv(out, index=False)
    print(report.to_string(index=False))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
