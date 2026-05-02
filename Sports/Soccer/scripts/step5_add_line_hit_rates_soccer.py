#!/usr/bin/env python3
"""
step5_add_line_hit_rates_soccer.py  (Soccer Pipeline)

Identical logic to NBA step5_add_line_hit_rates.py.
Computes over/under/push hit rates from stat_g1..stat_g5 vs line.

Run:
  py -3.14 step5_add_line_hit_rates_soccer.py \
    --input step4_soccer_with_stats.csv \
    --output step5_soccer_hit_rates.csv
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Tuple

import numpy as np
import pandas as pd


def _get_stat_cols(df: pd.DataFrame, n: int) -> List[str]:
    cols = [f"stat_g{i}" for i in range(1, n + 1)]
    return [c for c in cols if c in df.columns]


def _compute_hits(
    df: pd.DataFrame,
    stat_cols: List[str],
    line_col: str,
) -> Tuple[pd.Series, ...]:
    sub  = df[stat_cols].apply(pd.to_numeric, errors="coerce")
    line = pd.to_numeric(df[line_col], errors="coerce")

    played = sub.notna().sum(axis=1).astype(float)
    over   = sub.gt(line, axis=0).sum(axis=1).astype(float)
    under  = sub.lt(line, axis=0).sum(axis=1).astype(float)
    push   = sub.eq(line, axis=0).sum(axis=1).astype(float)

    denom_played       = played.replace(0, np.nan)
    over_rate_played   = over  / denom_played
    under_rate_played  = under / denom_played

    denom_ou           = (over + under).replace(0, np.nan)
    over_rate_ou       = over  / denom_ou
    under_rate_ou      = under / denom_ou

    return played, over, under, push, over_rate_played, under_rate_played, over_rate_ou, under_rate_ou


def _ensure_cols(df: pd.DataFrame, cols: List[str]) -> None:
    for c in cols:
        if c not in df.columns:
            df[c] = np.nan


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",     required=True)
    ap.add_argument("--output",    required=True)
    ap.add_argument("--line-col",  default="line")
    ap.add_argument("--compute10", action="store_true", default=True)
    args = ap.parse_args()

    print(f"→ Loading: {args.input}")
    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig").copy()

    if df.empty:
        print("❌ [PropOracle-Soccer-S5] Empty input from S4 — aborting.")
        sys.exit(1)

    if args.line_col not in df.columns:
        raise RuntimeError(f"❌ Missing column: {args.line_col}")

    stat5 = _get_stat_cols(df, 5)
    if not stat5:
        raise RuntimeError("❌ Missing stat_g1..stat_g5. Run step4 first.")

    if "hit_rate_status" not in df.columns:
        df["hit_rate_status"] = "OK"

    unsupported_mask = pd.Series(False, index=df.index)
    if "unsupported_prop" in df.columns:
        unsupported_mask = (
            pd.to_numeric(df["unsupported_prop"], errors="coerce")
            .fillna(0).astype(int).eq(1)
        )
        df.loc[unsupported_mask, "hit_rate_status"] = "UNSUPPORTED_PROP"

    line_num = pd.to_numeric(df[args.line_col], errors="coerce")
    no_line  = line_num.isna()
    df.loc[no_line, "hit_rate_status"] = "MISSING_LINE"

    sub5      = df[stat5].apply(pd.to_numeric, errors="coerce")
    no_stats5 = (sub5.notna().sum(axis=1) == 0)
    df.loc[no_stats5 & ~unsupported_mask, "hit_rate_status"] = "MISSING_STAT_VALUES"

    ok5 = (~unsupported_mask) & (~no_line) & (~no_stats5)

    out5_cols = [
        "line_games_played_5",
        "line_hits_over_5", "line_hits_under_5", "line_hits_push_5",
        "line_hit_rate_over_5", "line_hit_rate_under_5",
        "line_hit_rate_over_ou_5", "line_hit_rate_under_ou_5",
    ]
    _ensure_cols(df, out5_cols)

    played5, over5, under5, push5, orp5, urp5, orou5, urou5 = _compute_hits(
        df.loc[ok5], stat5, args.line_col
    )
    df.loc[ok5, "line_games_played_5"]      = played5.values
    df.loc[ok5, "line_hits_over_5"]         = over5.values
    df.loc[ok5, "line_hits_under_5"]        = under5.values
    df.loc[ok5, "line_hits_push_5"]         = push5.values
    df.loc[ok5, "line_hit_rate_over_5"]     = orp5.values
    df.loc[ok5, "line_hit_rate_under_5"]    = urp5.values
    df.loc[ok5, "line_hit_rate_over_ou_5"]  = orou5.values
    df.loc[ok5, "line_hit_rate_under_ou_5"] = urou5.values

    if args.compute10:
        stat10 = _get_stat_cols(df, 10)
        if len(stat10) >= 6:
            sub10      = df[stat10].apply(pd.to_numeric, errors="coerce")
            no_stats10 = (sub10.notna().sum(axis=1) == 0)
            ok10       = (~unsupported_mask) & (~no_line) & (~no_stats10)
            out10_cols = [
                "line_games_played_10",
                "line_hits_over_10", "line_hits_under_10", "line_hits_push_10",
                "line_hit_rate_over_10", "line_hit_rate_under_10",
                "line_hit_rate_over_ou_10", "line_hit_rate_under_ou_10",
            ]
            _ensure_cols(df, out10_cols)
            played10, over10, under10, push10, orp10, urp10, orou10, urou10 = _compute_hits(
                df.loc[ok10], stat10, args.line_col
            )
            df.loc[ok10, "line_games_played_10"]      = played10.values
            df.loc[ok10, "line_hits_over_10"]         = over10.values
            df.loc[ok10, "line_hits_under_10"]        = under10.values
            df.loc[ok10, "line_hits_push_10"]         = push10.values
            df.loc[ok10, "line_hit_rate_over_10"]     = orp10.values
            df.loc[ok10, "line_hit_rate_under_10"]    = urp10.values
            df.loc[ok10, "line_hit_rate_over_ou_10"]  = orou10.values
            df.loc[ok10, "line_hit_rate_under_ou_10"] = urou10.values

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    if df.empty:
        print("❌ [PropOracle-Soccer-S5] Output is empty — aborting.")
        sys.exit(1)
    print(f"✅ Saved → {args.output}  rows={len(df)}")
    filled = int(pd.to_numeric(df["line_hit_rate_over_ou_5"], errors="coerce").notna().sum())
    print(f"Filled line_hit_rate_over_ou_5: {filled}/{len(df)}")
    print("hit_rate_status:")
    print(df["hit_rate_status"].value_counts().head(10).to_string())


if __name__ == "__main__":
    main()
