#!/usr/bin/env python3
"""
NFL step6 — line hit-rate columns for step7/step8.

When player game logs are unavailable, emits the standard hit-tracking column
schema with NaN values. If stat_last5_avg / l5_avg columns exist on the input,
computes a direction-aware proxy hit_rate from recent average vs today's line.

  set NFL_PIPELINE_ACTIVE=1
  py -3.14 scripts/step6_historical_hit_rates.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _nfl_pipeline_active import require_nfl_pipeline_active_or_exit

_HIT_COLS = (
    "line_hit_rate_over_ou_5",
    "line_hit_rate_over_ou_10",
    "line_hit_rate_over_5",
    "line_hit_rate_over_10",
    "l5_over",
    "l5_under",
    "last5_over",
    "last5_under",
    "l10_over",
    "l10_under",
    "stat_last5_avg",
    "stat_last10_avg",
    "stat_season_avg",
)


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _first_col(df: pd.DataFrame, *names: str) -> pd.Series:
    for n in names:
        if n in df.columns:
            return _num(df[n])
    return pd.Series(np.nan, index=df.index)


def _attach_proxy_hit_rates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    line = _first_col(out, "line_score", "line")
    l5 = _first_col(out, "stat_last5_avg", "l5_avg", "last5_avg")
    l10 = _first_col(out, "stat_last10_avg", "l10_avg", "last10_avg")
    seas = _first_col(out, "stat_season_avg", "season_avg")

    if "stat_last5_avg" not in out.columns:
        out["stat_last5_avg"] = l5
    if "stat_last10_avg" not in out.columns:
        out["stat_last10_avg"] = l10
    if "stat_season_avg" not in out.columns:
        out["stat_season_avg"] = seas

    proj = l5.fillna(l10).fillna(seas)
    if "projection" not in out.columns:
        out["projection"] = proj.where(proj.notna(), line)
    else:
        out["projection"] = _num(out["projection"]).where(_num(out["projection"]).notna(), proj)

    # Proxy: fraction of recent window that would have cleared the line (OVER lens).
    over_proxy = np.where(line.notna() & proj.notna(), (proj >= line).astype(float), np.nan)
    over_proxy = pd.Series(over_proxy, index=out.index).clip(0.05, 0.95)

    for c in _HIT_COLS:
        if c not in out.columns:
            out[c] = np.nan

    has_proxy = proj.notna() & line.notna()
    if has_proxy.any():
        out.loc[has_proxy, "line_hit_rate_over_ou_5"] = over_proxy.loc[has_proxy]
        out.loc[has_proxy, "line_hit_rate_over_5"] = over_proxy.loc[has_proxy]
        out.loc[has_proxy, "hit_rate"] = over_proxy.loc[has_proxy]
    elif "hit_rate" not in out.columns:
        out["hit_rate"] = pd.NA

    return out


def main() -> None:
    require_nfl_pipeline_active_or_exit()

    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/outputs/step3_nfl_with_defense.csv")
    ap.add_argument("--output", default="data/outputs/step6_hit_rates.csv")
    args = ap.parse_args()

    in_path = Path(args.input)
    if not in_path.is_file():
        print(f"[NFL step6] Missing input: {in_path}")
        sys.exit(1)

    df = pd.read_csv(in_path, encoding="utf-8-sig")
    out_df = _attach_proxy_hit_rates(df)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    proxy_n = int(
        pd.to_numeric(out_df.get("line_hit_rate_over_ou_5"), errors="coerce").notna().sum()
    )
    print(f"[NFL step6] Wrote {out_path} rows={len(out_df)} proxy_hit_rate_rows={proxy_n}")


if __name__ == "__main__":
    main()
