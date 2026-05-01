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

MLB_MIN_GAMES = {
    "pitcher": 8.0,
    "hitter": 6.0,
}

PITCHER_PROP_KEYWORDS = (
    "strikeout",
    "pitching out",
    "earned run",
    "walks allowed",
    "hits allowed",
    "pitches thrown",
    "innings",
)

RELIABILITY_PRIOR = 0.55

# Prefer canonical prop_norm so "Hitter Strikeouts" is not treated as a pitcher prop.
_MLB_PITCHER_PROP_NORMS = frozenset({
    "strikeouts",
    "pitching_outs",
    "innings_pitched",
    "hits_allowed",
    "earned_runs",
    "walks_allowed",
    "batters_faced",
    "pitches_thrown",
})


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


def _is_pitcher_prop(df: pd.DataFrame) -> pd.Series:
    if "prop_norm" in df.columns:
        return (
            df["prop_norm"]
            .astype(str)
            .str.lower()
            .str.strip()
            .isin(_MLB_PITCHER_PROP_NORMS)
        )
    text = df["prop_type"].astype(str).str.lower()
    patt = "|".join(PITCHER_PROP_KEYWORDS)
    hit = text.str.contains(patt, regex=True, na=False)
    # Legacy rows: avoid classifying "Hitter Strikeouts" as pitcher via the word "strikeout".
    not_hitter_so = ~text.str.contains(r"hitter\s*strikeout", regex=True, na=False)
    return hit & not_hitter_so


def _apply_reliability_gate(df: pd.DataFrame, ok_mask: pd.Series) -> None:
    """Blend thin 5-game hit rates toward a conservative prior."""
    _ensure_cols(df, ["reliability_note"])
    df["reliability_note"] = df["reliability_note"].astype(object)

    # Only apply to rows with line + stats; keep missing-line/missing-stat statuses untouched.
    work = ok_mask.copy()
    if not work.any():
        return

    n = pd.to_numeric(df.loc[work, "line_games_played_5"], errors="coerce").fillna(0.0)
    raw = pd.to_numeric(df.loc[work, "line_hit_rate_over_ou_5"], errors="coerce")
    is_pitcher = _is_pitcher_prop(df.loc[work])
    min_games = pd.Series(
        np.where(is_pitcher, MLB_MIN_GAMES["pitcher"], MLB_MIN_GAMES["hitter"]),
        index=n.index,
        dtype=float,
    )
    thin = n < min_games
    if not thin.any():
        return

    weight = (n / min_games).clip(lower=0.0, upper=1.0)
    blended = (weight * raw) + ((1.0 - weight) * RELIABILITY_PRIOR)
    blended = blended.clip(lower=0.0, upper=1.0)
    target_idx = thin[thin].index

    # Propagate the blended 5g signal to both over-ou and over-only columns used downstream.
    df.loc[target_idx, "line_hit_rate_over_ou_5"] = blended.loc[target_idx].round(4)
    if "line_hit_rate_over_5" in df.columns:
        df.loc[target_idx, "line_hit_rate_over_5"] = blended.loc[target_idx].round(4)
    if "line_hit_rate_under_ou_5" in df.columns:
        df.loc[target_idx, "line_hit_rate_under_ou_5"] = (1.0 - blended.loc[target_idx]).round(4)
    if "line_hit_rate_under_5" in df.columns:
        df.loc[target_idx, "line_hit_rate_under_5"] = (1.0 - blended.loc[target_idx]).round(4)

    n_int = n.loc[target_idx].round().astype(int).astype(str)
    df.loc[target_idx, "hit_rate_status"] = "BLENDED_n" + n_int

    # Emit a reliability note only when the raw signal looked extremely inflated.
    raw_hi = (raw >= 0.90) & thin
    if raw_hi.any():
        hi_idx = raw_hi[raw_hi].index
        pct = (raw.loc[hi_idx] * 100.0).round(0).astype(int).astype(str)
        n_hi = n.loc[hi_idx].round().astype(int).astype(str)
        df.loc[hi_idx, "reliability_note"] = "THIN_SAMPLE_" + n_hi + "g_raw_" + pct + "%"


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
    _apply_reliability_gate(df, ok5)

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
