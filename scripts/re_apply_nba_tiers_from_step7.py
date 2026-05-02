#!/usr/bin/env python3
"""
Recompute NBA `tier` from existing `rank_score` in step7_ranked_props.xlsx (sheet ALL).

Matches NBA/scripts/step7_rank_props.py:
  - tier from rank_score bands
  - rows with eligible != 1 -> Tier D

Does not refetch PrizePicks or rerun steps 1–6. Use when inputs were already built.

Usage:
  py -3.14 scripts/re_apply_nba_tiers_from_step7.py
  py -3.14 scripts/re_apply_nba_tiers_from_step7.py --step7 NBA/data/outputs/step7_ranked_props.xlsx
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def _tier_from_score_series(score: pd.Series) -> pd.Series:
    """Mirror step7_rank_props._tier_from_score_series."""
    s = pd.to_numeric(score, errors="coerce")
    return pd.Series(
        np.where(
            s >= 1.25,
            "A",
            np.where(s >= 0.75, "B", np.where(s >= 0.40, "C", "D")),
        ),
        index=score.index,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--step7",
        default="",
        help="Path to step7_ranked_props.xlsx (default: <repo>/NBA/data/outputs/step7_ranked_props.xlsx)",
    )
    ap.add_argument(
        "--score-column",
        default="rank_score",
        help="Column to bucket into tiers (default: rank_score — same as step7 final ranking)",
    )
    ap.add_argument(
        "--only-date",
        default="",
        help="If set (YYYY-MM-DD), only recompute tier for rows whose start_time is that calendar date.",
    )
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent.parent
    step7 = Path(args.step7) if args.step7.strip() else repo / "NBA" / "data" / "outputs" / "step7_ranked_props.xlsx"
    if not step7.is_file():
        raise SystemExit(f"Missing step7 workbook: {step7}")

    xl = pd.ExcelFile(step7)
    if "ALL" not in xl.sheet_names:
        raise SystemExit(f"No ALL sheet in {step7}")

    sheets: dict[str, pd.DataFrame] = {name: pd.read_excel(step7, sheet_name=name) for name in xl.sheet_names}
    df = sheets["ALL"].copy()

    col = args.score_column.strip() or "rank_score"
    if col not in df.columns:
        raise SystemExit(f"Column {col!r} not found on ALL sheet")

    only = (args.only_date or "").strip()
    if only:
        st = pd.to_datetime(df["start_time"], errors="coerce").dt.strftime("%Y-%m-%d")
        mask = st.eq(only)
        n_sel = int(mask.sum())
        print(f"[only-date] {only}: {n_sel} row(s) on ALL sheet")
        if n_sel == 0:
            print("SKIP_NO_ROWS_FOR_DATE")
            raise SystemExit(5)
        tier_new = _tier_from_score_series(df.loc[mask, col])
        if "eligible" in df.columns:
            el = pd.to_numeric(df.loc[mask, "eligible"], errors="coerce").fillna(0)
            tier_new = tier_new.where(el.eq(1), "D")
        df.loc[mask, "tier"] = tier_new
    else:
        tier_new = _tier_from_score_series(df[col])
        if "eligible" in df.columns:
            el = pd.to_numeric(df["eligible"], errors="coerce").fillna(0)
            tier_new = tier_new.where(el.eq(1), "D")
        df["tier"] = tier_new

    sheets["ALL"] = df

    with pd.ExcelWriter(step7, engine="openpyxl") as w:
        for name in xl.sheet_names:
            sheets[name].to_excel(w, sheet_name=name, index=False)

    vc = df["tier"].value_counts()
    print(f"OK -> {step7}")
    print(vc.to_string())


if __name__ == "__main__":
    main()
