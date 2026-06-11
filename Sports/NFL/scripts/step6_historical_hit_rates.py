#!/usr/bin/env python3
"""
NFL step6 — finalize hit-rate columns for step7/step8.

Reads step5 output when available (real L5/L10 from ESPN boxscores); otherwise
falls back to proxy hit_rate from rolling averages vs today's line.

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


def _attach_boxscore_hit_rates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    hr5 = _first_col(out, "line_hit_rate_over_ou_5", "line_hit_rate_over_5")
    hr10 = _first_col(out, "line_hit_rate_over_ou_10", "line_hit_rate_over_10")
    hr10 = hr10.fillna(hr5)
    composite = (0.5 * hr5.fillna(0.5) + 0.5 * hr10.fillna(0.5)).clip(0.0, 1.0)
    has_real = hr5.notna()

    if has_real.any():
        out.loc[has_real, "hit_rate"] = composite.loc[has_real]
        out.loc[has_real, "composite_hit_rate"] = composite.loc[has_real]
        for src, dst in (
            ("line_hits_over_5", "l5_over"),
            ("line_hits_under_5", "l5_under"),
            ("line_hits_over_5", "last5_over"),
            ("line_hits_under_5", "last5_under"),
            ("line_hits_over_10", "l10_over"),
            ("line_hits_under_10", "l10_under"),
        ):
            if src in out.columns and dst not in out.columns:
                out[dst] = _num(out[src])
            elif src in out.columns:
                out[dst] = _num(out[src]).combine_first(_num(out.get(dst)))

    l5 = _first_col(out, "stat_last5_avg", "l5_avg", "last5_avg")
    l10 = _first_col(out, "stat_last10_avg", "l10_avg", "last10_avg")
    seas = _first_col(out, "stat_season_avg", "season_avg")
    line = _first_col(out, "line_score", "line")
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

    for c in _HIT_COLS:
        if c not in out.columns:
            out[c] = np.nan

    return out


def _attach_proxy_hit_rates(df: pd.DataFrame) -> pd.DataFrame:
    out = _attach_boxscore_hit_rates(df)
    if out.get("hit_rate") is not None and pd.to_numeric(out["hit_rate"], errors="coerce").notna().any():
        return out

    line = _first_col(out, "line_score", "line")
    proj = _first_col(out, "projection")
    over_proxy = np.where(line.notna() & proj.notna(), (proj >= line).astype(float), np.nan)
    over_proxy = pd.Series(over_proxy, index=out.index).clip(0.05, 0.95)
    has_proxy = proj.notna() & line.notna()
    if has_proxy.any():
        out.loc[has_proxy, "line_hit_rate_over_ou_5"] = over_proxy.loc[has_proxy]
        out.loc[has_proxy, "line_hit_rate_over_5"] = over_proxy.loc[has_proxy]
        out.loc[has_proxy, "hit_rate"] = over_proxy.loc[has_proxy]
        out.loc[has_proxy, "composite_hit_rate"] = over_proxy.loc[has_proxy]
    elif "hit_rate" not in out.columns:
        out["hit_rate"] = pd.NA
    return out


def main() -> None:
    require_nfl_pipeline_active_or_exit()

    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/outputs/step5_nfl_with_stats.csv")
    ap.add_argument("--output", default="data/outputs/step6_hit_rates.csv")
    args = ap.parse_args()

    in_path = Path(args.input)
    if not in_path.is_file():
        fallback = Path("data/outputs/step3_nfl_with_defense.csv")
        if fallback.is_file():
            in_path = fallback
            print(f"[NFL step6] step5 missing — using {fallback}")
        else:
            print(f"[NFL step6] Missing input: {in_path}")
            sys.exit(1)

    df = pd.read_csv(in_path, encoding="utf-8-sig")
    out_df = _attach_proxy_hit_rates(df)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    real_n = int(pd.to_numeric(out_df.get("line_hit_rate_over_ou_5"), errors="coerce").notna().sum())
    proxy_n = int(pd.to_numeric(out_df.get("hit_rate"), errors="coerce").notna().sum())
    print(f"[NFL step6] Wrote {out_path} rows={len(out_df)} real_hr_rows={real_n} hit_rate_rows={proxy_n}")


if __name__ == "__main__":
    main()
