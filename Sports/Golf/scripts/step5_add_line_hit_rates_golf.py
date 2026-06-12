#!/usr/bin/env python3
"""
Golf step5 — line hit rates from stat_g1..stat_g10 (post step4).

Run:
  py -3.14 Sports/Golf/scripts/step5_add_line_hit_rates_golf.py \\
      --input outputs/2026-06-12/golf/step4_golf_with_stats.csv \\
      --output outputs/2026-06-12/golf/step5_golf_hit_rates.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
from scripts.l10_streak_utils import finalize_l10_ui_columns


def _get_stat_cols(df: pd.DataFrame, n: int) -> List[str]:
    return [f"stat_g{i}" for i in range(1, n + 1) if f"stat_g{i}" in df.columns]


def _compute_hits(
    df: pd.DataFrame,
    stat_cols: List[str],
    line_col: str,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    sub = df[stat_cols].apply(pd.to_numeric, errors="coerce")
    line = pd.to_numeric(df[line_col], errors="coerce")
    played = sub.notna().sum(axis=1).astype(float)
    over = sub.gt(line, axis=0).sum(axis=1).astype(float)
    under = sub.lt(line, axis=0).sum(axis=1).astype(float)
    push = sub.eq(line, axis=0).sum(axis=1).astype(float)
    denom_played = played.replace(0, np.nan)
    denom_ou = (over + under).replace(0, np.nan)
    return (
        played,
        over,
        under,
        push,
        over / denom_played,
        under / denom_played,
        over / denom_ou,
        under / denom_ou,
    )


def _ensure_cols(df: pd.DataFrame, cols: List[str]) -> None:
    for c in cols:
        if c not in df.columns:
            df[c] = np.nan


def main() -> None:
    print("[Golf step5] Starting...")
    ap = argparse.ArgumentParser(description="Golf step5 — line hit rates from round history.")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--line-col", default="line")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_absolute():
        inp = _REPO / inp
    out = Path(args.output)
    if not out.is_absolute():
        out = _REPO / out

    df = pd.read_csv(inp, low_memory=False, encoding="utf-8-sig").copy()
    if args.line_col not in df.columns and "line_score" in df.columns:
        df[args.line_col] = df["line_score"]
    if args.line_col not in df.columns:
        print(f"ERROR [Golf step5] missing line column: {args.line_col}")
        sys.exit(1)

    stat5 = _get_stat_cols(df, 5)
    if not stat5:
        print("ERROR [Golf step5] missing stat_g1..stat_g5 — run step4 first.")
        sys.exit(1)

    if "hit_rate_status" not in df.columns:
        df["hit_rate_status"] = "OK"

    unsupported_mask = pd.Series(False, index=df.index)
    if "unsupported_prop" in df.columns:
        unsupported_mask = (
            pd.to_numeric(df["unsupported_prop"], errors="coerce").fillna(0).astype(int).eq(1)
        )
        df.loc[unsupported_mask, "hit_rate_status"] = "UNSUPPORTED_PROP"

    line_num = pd.to_numeric(df[args.line_col], errors="coerce")
    no_line = line_num.isna()
    df.loc[no_line, "hit_rate_status"] = "MISSING_LINE"

    sub5 = df[stat5].apply(pd.to_numeric, errors="coerce")
    no_stats5 = sub5.notna().sum(axis=1) == 0
    df.loc[no_stats5 & ~unsupported_mask, "hit_rate_status"] = "MISSING_STAT_VALUES"

    ok5 = (~unsupported_mask) & (~no_line) & (~no_stats5)
    out5_cols = [
        "line_games_played_5",
        "line_hits_over_5",
        "line_hits_under_5",
        "line_hits_push_5",
        "line_hit_rate_over_5",
        "line_hit_rate_under_5",
        "line_hit_rate_over_ou_5",
        "line_hit_rate_under_ou_5",
        "last5_over",
        "last5_under",
        "last5_push",
    ]
    _ensure_cols(df, out5_cols)

    played5, over5, under5, push5, orp5, urp5, orou5, urou5 = _compute_hits(df.loc[ok5], stat5, args.line_col)
    df.loc[ok5, "line_games_played_5"] = played5.values
    df.loc[ok5, "line_hits_over_5"] = over5.values
    df.loc[ok5, "line_hits_under_5"] = under5.values
    df.loc[ok5, "line_hits_push_5"] = push5.values
    df.loc[ok5, "line_hit_rate_over_5"] = orp5.values
    df.loc[ok5, "line_hit_rate_under_5"] = urp5.values
    df.loc[ok5, "line_hit_rate_over_ou_5"] = orou5.values
    df.loc[ok5, "line_hit_rate_under_ou_5"] = urou5.values
    df.loc[ok5, "last5_over"] = over5.values
    df.loc[ok5, "last5_under"] = under5.values
    df.loc[ok5, "last5_push"] = push5.values

    stat10 = _get_stat_cols(df, 10)
    if stat10:
        if len(stat10) >= 6:
            sub10 = df[stat10].apply(pd.to_numeric, errors="coerce")
            ok10 = (~unsupported_mask) & (~no_line) & ~(sub10.notna().sum(axis=1) == 0)
            out10_cols = [
                "line_games_played_10",
                "line_hits_over_10",
                "line_hits_under_10",
                "line_hits_push_10",
                "line_hit_rate_over_10",
                "line_hit_rate_under_10",
                "line_hit_rate_over_ou_10",
                "line_hit_rate_under_ou_10",
            ]
            _ensure_cols(df, out10_cols)
            played10, over10, under10, push10, orp10, urp10, orou10, urou10 = _compute_hits(
                df.loc[ok10], stat10, args.line_col
            )
            df.loc[ok10, "line_games_played_10"] = played10.values
            df.loc[ok10, "line_hits_over_10"] = over10.values
            df.loc[ok10, "line_hits_under_10"] = under10.values
            df.loc[ok10, "line_hits_push_10"] = push10.values
            df.loc[ok10, "line_hit_rate_over_10"] = orp10.values
            df.loc[ok10, "line_hit_rate_under_10"] = urp10.values
            df.loc[ok10, "line_hit_rate_over_ou_10"] = orou10.values
            df.loc[ok10, "line_hit_rate_under_ou_10"] = urou10.values

    hr5 = pd.to_numeric(df.get("line_hit_rate_over_ou_5"), errors="coerce")
    hr10 = pd.to_numeric(df.get("line_hit_rate_over_ou_10"), errors="coerce")
    df["composite_hit_rate"] = (0.5 * hr5.fillna(0.5) + 0.5 * hr10.fillna(hr5).fillna(0.5)).clip(0.0, 1.0).round(4)

    df = finalize_l10_ui_columns(df, line_col=args.line_col)

    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")

    filled = int(pd.to_numeric(df["line_hit_rate_over_ou_5"], errors="coerce").notna().sum())
    print(f"[Golf step5] L5 hit rate fill: {filled}/{len(df)} rows")
    print(f"[Golf step5] Wrote {out} ({len(df)} rows)")
    print("hit_rate_status:", df["hit_rate_status"].value_counts().head(6).to_dict())


if __name__ == "__main__":
    main()
