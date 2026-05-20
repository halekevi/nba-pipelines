#!/usr/bin/env python3
"""Audit NBA1H points grading for full-game stat contamination (same heuristic as NBA1Q)."""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    nba1h_pts: list[dict] = []
    files = sorted((_REPO / "mobile" / "www").glob("graded_props_*.json"))
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        props = data if isinstance(data, list) else data.get("props", [])
        for p in props:
            if (
                p.get("sport") == "NBA1H"
                and str(p.get("prop", p.get("prop_type", ""))).lower() in ("points", "pts")
                and not p.get("grading_suspect")
            ):
                val = p.get("actual_value")
                line = p.get("line")
                date = str(p.get("graded_at", ""))[:7]
                if val is not None and line is not None:
                    try:
                        nba1h_pts.append(
                            {
                                "date": date,
                                "line": float(line),
                                "actual": float(val),
                                "hit": p.get("hit"),
                            }
                        )
                    except (TypeError, ValueError):
                        pass

    print(f"NBA1H points props: {len(nba1h_pts)}")
    if not nba1h_pts:
        return 0

    corrupt_months = 0
    for m, rows in sorted({r["date"]: [] for r in nba1h_pts}.items()):
        month_rows = [r for r in nba1h_pts if r["date"] == m]
        actuals = [r["actual"] for r in month_rows]
        hr = sum(1 for r in month_rows if r.get("hit") in (1, "1", True)) / len(month_rows)
        hits = [r for r in month_rows if r.get("hit") is not None]
        if hits:
            hr = sum(1 for r in hits if str(r.get("hit")) in ("1", "True") or r.get("hit") == 1) / len(
                month_rows
            )
        else:
            hr = sum(1 for r in month_rows if r.get("hit")) / len(month_rows) if month_rows else 0
        suspect = sum(1 for r in month_rows if r["actual"] > 25)
        mean_a = statistics.mean(actuals)
        print(
            f"{m}: mean_actual={mean_a:.1f} HR={hr:.1%} suspect(>25)={suspect}/{len(month_rows)}"
        )
        if mean_a > 22 or hr > 0.75:
            corrupt_months += 1

    overall_mean = statistics.mean([r["actual"] for r in nba1h_pts])
    if overall_mean > 22 or corrupt_months >= 2:
        print("\nVERDICT: NBA1H grading likely CORRUPT — run regrade_nba1q.py --sport NBA1H")
        return 2
    print("\nVERDICT: NBA1H grading confirmed clean — period stats plausible")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
