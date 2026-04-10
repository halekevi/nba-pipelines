#!/usr/bin/env python3
"""
Tennis step7 — rank / tier from line hit rates + opponent rank + player rank.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent


def main() -> None:
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
        print("ERROR [Tennis-S7] empty input")
        sys.exit(1)

    hr5 = pd.to_numeric(df.get("line_hit_rate_over_ou_5", np.nan), errors="coerce")
    hr10 = pd.to_numeric(df.get("line_hit_rate_over_ou_10", np.nan), errors="coerce")
    hr10 = hr10.fillna(hr5)
    opp_r = pd.to_numeric(df.get("OVERALL_DEF_RANK", 50), errors="coerce").fillna(50.0)
    p_r = pd.to_numeric(df.get("player_atp_rank", 100), errors="coerce").fillna(100.0)

    opp_adj = (85.0 - opp_r.clip(1, 120)) / 85.0
    player_adj = (120.0 - p_r.clip(1, 300)) / 120.0
    base = (
        0.18
        + 0.48 * hr5.fillna(0.52)
        + 0.18 * hr10.fillna(hr5).fillna(0.52)
        + 0.10 * opp_adj.clip(0, 1)
        + 0.06 * player_adj.clip(0, 1)
    )
    rank_score = (base * 10.0).clip(0.0, 10.0)
    df["rank_score"] = rank_score

    def tier_for(rs: float, h: float) -> str:
        if rs >= 6.8 and h >= 0.58:
            return "A"
        if rs >= 6.0 and h >= 0.52:
            return "B"
        if rs >= 5.0:
            return "C"
        return "D"

    df["tier"] = [tier_for(rank_score.iat[i], float(hr5.fillna(0.5).iat[i])) for i in range(len(df))]

    line = pd.to_numeric(df["line"], errors="coerce")
    l5 = pd.to_numeric(df.get("stat_last5_avg", np.nan), errors="coerce")
    seas = pd.to_numeric(df.get("stat_season_avg", np.nan), errors="coerce")
    proj = l5.fillna(seas).fillna(line)
    df["projection"] = proj
    df["edge"] = proj - line

    df["ml_prob"] = (0.42 + 0.22 * hr5.fillna(0.5).clip(0.35, 0.72)).clip(0.38, 0.78)
    df["edge_score"] = (df["edge"].astype(float).abs().clip(0, 8) / 8.0 * 10.0).round(4)
    df["blended_score"] = (pd.to_numeric(df["rank_score"], errors="coerce") * 0.55 + df["ml_prob"] * 10 * 0.45).round(4)

    df["void_reason"] = ""
    bad = df["stat_status"].astype(str).str.upper().isin(["NO_DATA", "NO_ID"]) & df["unsupported_prop"].astype(str).ne("1")
    df.loc[bad, "void_reason"] = "WEAK_MATCH_HISTORY"

    df["espn_player_id"] = df.get("espn_athlete_id", "")
    if "pos" not in df.columns:
        df["pos"] = ""
    df["league"] = (df["tour"].astype(str).str.upper() + " / " + df["surface"].astype(str).str.upper()).str.strip()
    opp_col = "opp_team" if "opp_team" in df.columns else "opp"
    df["opp_team"] = df[opp_col].astype(str)

    out.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="ALL", index=False)
    print(f"OK [Tennis-S7] -> {out}  rows={len(df)}")


if __name__ == "__main__":
    main()
