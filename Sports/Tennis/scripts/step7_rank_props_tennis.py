#!/usr/bin/env python3
"""
Tennis step7 — composite_score tiers (A/B/C/D) + rank_score for ticket sorting.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
_TENNIS_REPO = Path(__file__).resolve().parents[3]
if str(_TENNIS_REPO) not in sys.path:
    sys.path.insert(0, str(_TENNIS_REPO))
from utils.group_rank_tier import assign_tier_column, report_goblin_demon_standard_line_fill  # noqa: E402


def main() -> None:
    print("[Tennis step7] Starting...")
    root = _SCRIPT_DIR.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="outputs/step6_tennis_role_context.csv")
    ap.add_argument("--output", default="outputs/step7_tennis_ranked.xlsx")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_absolute():
        inp = root / inp
    out = Path(args.output)
    if not out.is_absolute():
        out = root / out

    df = pd.read_csv(inp, low_memory=False, encoding="utf-8-sig").fillna("")
    if df.empty:
        print("ERROR [Tennis step7] empty input")
        sys.exit(1)

    hr5 = pd.to_numeric(df.get("line_hit_rate_over_ou_5", np.nan), errors="coerce")
    hr10 = pd.to_numeric(df.get("line_hit_rate_over_ou_10", np.nan), errors="coerce")
    hr10 = hr10.fillna(hr5)
    composite_hit_rate = (0.5 * hr5.fillna(0.5) + 0.5 * hr10.fillna(0.5)).clip(0.0, 1.0)
    composite_score = composite_hit_rate * 2.0

    surf = df["surface"].astype(str).str.lower() if "surface" in df.columns else pd.Series("hard", index=df.index)
    best = (
        df["best_surface"].astype(str).str.lower()
        if "best_surface" in df.columns
        else pd.Series("", index=df.index)
    )
    bonus_surf = ((best.str.len() > 1) & (surf == best)).astype(float) * 0.10
    rdiff = pd.to_numeric(df.get("ranking_diff", 0), errors="coerce").fillna(0.0)
    bonus_rank = (rdiff > 50).astype(float) * 0.05 - (rdiff < -50).astype(float) * 0.05

    composite_score = composite_score + bonus_surf + bonus_rank
    df["composite_hit_rate"] = composite_hit_rate
    df["composite_score"] = composite_score

    line = pd.to_numeric(df.get("line", df.get("line_score", np.nan)), errors="coerce")
    l5 = pd.to_numeric(df.get("stat_last5_avg", np.nan), errors="coerce")
    seas = pd.to_numeric(df.get("stat_season_avg", np.nan), errors="coerce")
    proj = l5.fillna(seas).fillna(line)
    df["projection"] = proj
    df["edge"] = proj - line

    df["ml_prob"] = (0.42 + 0.22 * hr5.fillna(0.5).clip(0.35, 0.72)).clip(0.38, 0.78)
    df["edge_score"] = (df["edge"].astype(float).abs().clip(0, 8) / 8.0 * 10.0).round(4)
    df["rank_score"] = (composite_score * 4.0).clip(0.0, 10.0).round(4)
    df["blended_score"] = (0.3 * df["ml_prob"] + 0.7 * composite_hit_rate).round(4)
    if "bet_direction" not in df.columns:
        df["bet_direction"] = "OVER"
    df["tier"] = assign_tier_column(df, sport="tennis")
    report_goblin_demon_standard_line_fill(df, "[Tennis step7]")

    df["void_reason"] = ""
    bad = (
        df["stat_status"].astype(str).str.upper().isin(["NO_DATA", "NO_ID"])
        & df["unsupported_prop"].astype(str).ne("1")
    )
    df.loc[bad, "void_reason"] = "WEAK_MATCH_HISTORY"

    df["espn_player_id"] = df.get("espn_athlete_id", "")
    if "pos" not in df.columns:
        df["pos"] = ""
    df["league"] = (df["tour"].astype(str).str.upper() + " / " + df["surface"].astype(str).str.upper()).str.strip()
    opp_col = "opp_team" if "opp_team" in df.columns else "opp"
    df["opp_team"] = df[opp_col].astype(str) if opp_col in df.columns else ""

    df["DEF_TIER"] = "N/A"
    df["OVERALL_DEF_RANK"] = df.get("OVERALL_DEF_RANK", "")

    out.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="ALL", index=False)
    print(f"OK [Tennis step7] -> {out}  rows={len(df)}")


if __name__ == "__main__":
    main()
