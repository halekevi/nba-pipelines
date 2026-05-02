#!/usr/bin/env python3
"""Soccer pipeline health checks for direction and edge coverage.

Emits INFO/WARNING diagnostics and exits 0 (non-blocking):
- Step7: edge non-null %, OVER/UNDER counts
- Step8 (post-date-filter output): OVER/UNDER counts

Warnings:
- Step7 edge null % > 20%
- Step8 has 0 rows for either OVER or UNDER direction
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

WARN_EDGE_NULL_PCT = 20.0


def _load_sheet(path: Path, preferred: tuple[str, ...]) -> pd.DataFrame:
    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet = next((s for s in preferred if s in xl.sheet_names), xl.sheet_names[0])
    return pd.read_excel(path, sheet_name=sheet, engine="openpyxl")


def _first_present(df: pd.DataFrame, cols: tuple[str, ...]) -> str | None:
    for c in cols:
        if c in df.columns:
            return c
    return None


def _dir_counts(df: pd.DataFrame, cols: tuple[str, ...]) -> tuple[int, int]:
    col = _first_present(df, cols)
    if col is None:
        return 0, 0
    s = df[col].astype(str).str.upper().str.strip()
    return int(s.eq("OVER").sum()), int(s.eq("UNDER").sum())


def main() -> int:
    ap = argparse.ArgumentParser(description="Health-check Soccer step7/step8 direction outputs.")
    ap.add_argument("--step7", required=True, help="Path to step7_soccer_ranked.xlsx")
    ap.add_argument("--step8", required=True, help="Path to step8_soccer_direction_clean.xlsx")
    args = ap.parse_args()

    step7 = Path(args.step7)
    step8 = Path(args.step8)

    if not step7.is_file():
        print(f"WARNING [soccer-health] Step7 file missing: {step7}")
        return 0
    if not step8.is_file():
        print(f"WARNING [soccer-health] Step8 file missing: {step8}")
        return 0

    s7 = _load_sheet(step7, ("ALL",))
    edge_col = _first_present(s7, ("edge", "Edge"))
    if edge_col is None:
        edge_non_null = 0
        edge_null_pct = 100.0
    else:
        edge_num = pd.to_numeric(s7[edge_col], errors="coerce")
        edge_non_null = int(edge_num.notna().sum())
        edge_null_pct = (100.0 * float(edge_num.isna().mean())) if len(edge_num) else 100.0

    s7_over, s7_under = _dir_counts(s7, ("bet_direction", "final_bet_direction", "direction", "Direction"))
    print(
        "[soccer-health] step7 "
        f"rows={len(s7)} edge_non_null={edge_non_null}/{len(s7)} "
        f"edge_null_pct={edge_null_pct:.1f}% OVER={s7_over} UNDER={s7_under}"
    )
    if edge_null_pct > WARN_EDGE_NULL_PCT:
        print(
            "WARNING [soccer-health] "
            f"step7 edge null pct {edge_null_pct:.1f}% exceeds {WARN_EDGE_NULL_PCT:.1f}%"
        )

    s8 = _load_sheet(step8, ("Soccer", "ALL"))
    s8_over, s8_under = _dir_counts(s8, ("Direction", "final_bet_direction", "bet_direction", "direction"))
    print(f"[soccer-health] step8 rows={len(s8)} OVER={s8_over} UNDER={s8_under}")
    if s8_over == 0 or s8_under == 0:
        print(
            "WARNING [soccer-health] "
            f"step8 direction suppression detected (OVER={s8_over}, UNDER={s8_under})"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
