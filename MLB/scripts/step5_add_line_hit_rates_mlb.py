#!/usr/bin/env python3
"""
step5_add_line_hit_rates_mlb.py  (MLB Pipeline)
Identical logic to NBA/Soccer step5. Computes over/under hit rates.

Run:
  py -3.14 step5_add_line_hit_rates_mlb.py \
    --input step4_mlb_with_stats.csv \
    --output step5_mlb_hit_rates.csv
"""

from __future__ import annotations

import argparse
from typing import List, Tuple

import numpy as np
import pandas as pd


def _get_stat_cols(df: pd.DataFrame, n: int) -> List[str]:
    return [f"stat_g{i}" for i in range(1, n + 1) if f"stat_g{i}" in df.columns]


def _compute_hits(df, stat_cols, line_col):
    sub   = df[stat_cols].apply(pd.to_numeric, errors="coerce")
    line  = pd.to_numeric(df[line_col], errors="coerce")
    played= sub.notna().sum(axis=1).astype(float)
    over  = sub.gt(line, axis=0).sum(axis=1).astype(float)
    under = sub.lt(line, axis=0).sum(axis=1).astype(float)
    push  = sub.eq(line, axis=0).sum(axis=1).astype(float)
    dp    = played.replace(0, np.nan)
    dou   = (over + under).replace(0, np.nan)
    return played, over, under, push, over/dp, under/dp, over/dou, under/dou


def _ensure_cols(df, cols):
    for c in cols:
        if c not in df.columns:
            df[c] = np.nan


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",     default="MLB/scripts/step4_mlb_with_stats.csv")
    ap.add_argument("--output",    default="MLB/scripts/step5_mlb_hit_rates.csv")
    ap.add_argument("--line-col",  default="line")
    ap.add_argument("--compute10", action="store_true")
    args = ap.parse_args()

    print(f"→ Loading: {args.input}")
    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig").copy()

    stat5 = _get_stat_cols(df, 5)
    if not stat5:
        raise RuntimeError("❌ Missing stat_g1..stat_g5. Run step4 first.")

    if "hit_rate_status" not in df.columns:
        df["hit_rate_status"] = "OK"

    line_num   = pd.to_numeric(df[args.line_col], errors="coerce")
    no_line    = line_num.isna()
    sub5       = df[stat5].apply(pd.to_numeric, errors="coerce")
    no_stats5  = (sub5.notna().sum(axis=1) == 0)

    df.loc[no_line,   "hit_rate_status"] = "MISSING_LINE"
    df.loc[no_stats5 & ~no_line, "hit_rate_status"] = "MISSING_STAT_VALUES"

    ok5 = ~no_line & ~no_stats5
    _ensure_cols(df, ["line_games_played_5","line_hits_over_5","line_hits_under_5","line_hits_push_5",
                       "line_hit_rate_over_5","line_hit_rate_under_5",
                       "line_hit_rate_over_ou_5","line_hit_rate_under_ou_5"])

    played5, over5, under5, push5, orp5, urp5, orou5, urou5 = _compute_hits(df.loc[ok5], stat5, args.line_col)
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
            sub10     = df[stat10].apply(pd.to_numeric, errors="coerce")
            ok10      = ~no_line & ~(sub10.notna().sum(axis=1) == 0)
            _ensure_cols(df, ["line_games_played_10","line_hits_over_10","line_hits_under_10","line_hits_push_10",
                               "line_hit_rate_over_10","line_hit_rate_under_10",
                               "line_hit_rate_over_ou_10","line_hit_rate_under_ou_10"])
            played10, over10, under10, push10, orp10, urp10, orou10, urou10 = _compute_hits(df.loc[ok10], stat10, args.line_col)
            df.loc[ok10, "line_games_played_10"]      = played10.values
            df.loc[ok10, "line_hits_over_10"]         = over10.values
            df.loc[ok10, "line_hits_under_10"]        = under10.values
            df.loc[ok10, "line_hits_push_10"]         = push10.values
            df.loc[ok10, "line_hit_rate_over_10"]     = orp10.values
            df.loc[ok10, "line_hit_rate_under_10"]    = urp10.values
            df.loc[ok10, "line_hit_rate_over_ou_10"]  = orou10.values
            df.loc[ok10, "line_hit_rate_under_ou_10"] = urou10.values

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"✅ Saved → {args.output}  rows={len(df)}")
    filled = int(pd.to_numeric(df["line_hit_rate_over_ou_5"], errors="coerce").notna().sum())
    print(f"Filled line_hit_rate_over_ou_5: {filled}/{len(df)}")
    print("hit_rate_status:", df["hit_rate_status"].value_counts().head(5).to_dict())


if __name__ == "__main__":
    main()
