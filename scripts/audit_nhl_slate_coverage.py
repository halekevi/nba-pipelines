#!/usr/bin/env python3
"""
Audit NHL slate row counts through the pipeline (step1 → step7) and flag attrition.

Run from repo root:
  py -3.14 scripts/audit_nhl_slate_coverage.py
  py -3.14 scripts/audit_nhl_slate_coverage.py --nhl-dir NHL

Typical loss points (by design):
  - step7: Goblin + negative play-side edge → DROPPED sheet (still in step7 workbook)
  - combined_slate_tickets.load_nhl: faceoff props removed
  - step8: rows not matching --date local filter (see step8 console [DateFilter])
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

import pandas as pd


def _count_csv(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        return sum(1 for _ in open(path, encoding="utf-8-sig", errors="replace")) - 1
    except OSError:
        return None


def _count_xlsx_rows(path: Path, sheet: str | None = None) -> int | None:
    if not path.is_file():
        return None
    try:
        df = pd.read_excel(path, sheet_name=sheet or 0, engine="openpyxl")
        return len(df)
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare NHL pipeline artifact row counts.")
    ap.add_argument("--nhl-dir", default="NHL", help="Path to NHL folder (default: NHL)")
    args = ap.parse_args()

    root = Path(os.environ.get("PROPORACLE_ROOT", ".")).resolve()
    nhl = (root / args.nhl_dir).resolve()
    out = nhl / "outputs"
    if not nhl.is_dir():
        print(f"Missing NHL dir: {nhl}", file=sys.stderr)
        sys.exit(1)

    steps = [
        ("step1", out / "step1_nhl_props.csv", "csv"),
        ("step2", out / "step2_nhl_picktypes.csv", "csv"),
        ("step3", out / "step3_nhl_with_defense.csv", "csv"),
        ("step4", out / "step4_nhl_with_stats.csv", "csv"),
        ("step5", out / "step5_nhl_hit_rates.csv", "csv"),
        ("step6", out / "step6_nhl_context.csv", "csv"),
        ("step7", out / "step7_nhl_ranked.xlsx", "xlsx_all"),
    ]

    print(f"NHL dir: {nhl}\n")
    prev: int | None = None
    for label, path, kind in steps:
        n: int | None
        if kind == "csv":
            n = _count_csv(path)
        elif kind == "xlsx_all":
            n = _count_xlsx_rows(path, "All Props")
            if n is None:
                n = _count_xlsx_rows(path, None)
        else:
            n = None
        status = "—" if n is None else str(n)
        line = f"  {label:8s} {path.name:32s} {status:>8s}"
        if n is not None and prev is not None and prev > 0:
            delta = n - prev
            if delta != 0:
                line += f"   ({delta:+d} vs previous)"
        print(line)
        if n is not None:
            prev = n

    # step1 richness
    s1 = out / "step1_nhl_props.csv"
    if s1.is_file():
        try:
            df = pd.read_csv(s1, encoding="utf-8-sig", dtype=str, nrows=50000)
            nu = df["player_name"].nunique() if "player_name" in df.columns else None
            st = df["stat_type"].nunique() if "stat_type" in df.columns else None
            if nu is not None:
                print(f"\n  step1 unique players (approx): {nu}")
            if st is not None:
                print(f"  step1 distinct stat_type labels: {st}")
                vc = df["stat_type"].value_counts().head(15)
                print("  top stat_type counts:")
                for k, v in vc.items():
                    print(f"    {k!r}: {int(v)}")
        except Exception as e:
            print(f"\n  (Could not summarize step1: {e})")

    print(
        "\nNotes:\n"
        "  - If step7 << step1, inspect step7 workbook tab 'DROPPED' (neg-edge Goblin audit).\n"
        "  - combined_slate_tickets.load_nhl drops faceoff props.\n"
        "  - step4 --show-misses lists players with NO_DATA in the reference DB.\n"
    )


if __name__ == "__main__":
    main()
