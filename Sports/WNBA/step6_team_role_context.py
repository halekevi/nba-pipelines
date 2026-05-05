#!/usr/bin/env python3
"""
step6_team_role_context.py  (WNBA)

WNBA Step 6:
- Adds minutes / shot / usage tiers for downstream ranking context
- Uses available stat columns from Step 4/5
"""

from __future__ import annotations
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs


# ----------------------------
# Tiering
# ----------------------------

def tier_minutes(x):
    if pd.isna(x):
        return "UNKNOWN"
    if x < 24:
        return "LOW"
    if x < 32:
        return "MED"
    return "HIGH"

def tier_shots(x):
    if pd.isna(x):
        return "UNKNOWN"
    if x < 8:
        return "LOW_VOL"
    if x < 14:
        return "MID_VOL"
    return "HIGH_VOL"

def tier_usage(x):
    if pd.isna(x):
        return "UNKNOWN"
    if x < 7:
        return "SUPPORT"
    if x < 13:
        return "SECONDARY"
    return "PRIMARY"


# ----------------------------
# Main
# ----------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--roles-csv", default=None)
    ap.add_argument("--defense-csv", default=None)
    args = ap.parse_args()

    print(f"→ Loading: {args.input}")
    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")

    # -----------------------------------
    # Basic tiers (already in Step5 stats)
    # -----------------------------------

    # WNBA boards mainly include pts/ast/reb/3ptmade/stl/blk. Use last5 + season
    # values from step4 as role signals (minutes metric falls back to season stat
    # signal when explicit minutes are unavailable).
    def _num_series(name: str) -> pd.Series:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce")
        return pd.Series(np.nan, index=df.index, dtype=float)

    last5 = _num_series("stat_last5_avg")
    season = _num_series("stat_season_avg")
    line = _num_series("line")
    min_signal = _num_series("min_player_avg")
    min_signal = min_signal.where(min_signal.notna(), season.where(season.notna(), last5))
    shot_signal = last5.where(last5.notna(), season.where(season.notna(), line))
    usage_signal = season.where(season.notna(), last5.where(last5.notna(), line))
    new_cols = {
        "min_player_avg": min_signal,
        "fga_player_avg": shot_signal,
        "pts_player_avg": usage_signal,
        "minutes_tier":   min_signal.apply(tier_minutes),
        "shot_role":      shot_signal.apply(tier_shots),
        "usage_role":     usage_signal.apply(tier_usage),
    }
    for c, s in new_cols.items():
        df[c] = s

    # -----------------------------------
    # Merge Team Roles (Step10)
    # -----------------------------------

    if args.roles_csv:
        print(f"→ Merging roles from: {args.roles_csv}")
        roles = pd.read_csv(args.roles_csv, encoding="utf-8-sig")

        roles["PLAYER_ID"] = roles["PLAYER_ID"].astype(str)
        df["cbb_player_id"] = df["cbb_player_id"].astype(str)

        role_cols = [c for c in roles.columns if c.startswith("role_")]

        df = df.merge(
            roles[["PLAYER_ID"] + role_cols],
            left_on="cbb_player_id",
            right_on="PLAYER_ID",
            how="left"
        )

        df.drop(columns=["PLAYER_ID"], inplace=True, errors="ignore")

    # -----------------------------------
    # Merge Defense (Step11)
    # -----------------------------------

    if args.defense_csv:
        print(f"→ Merging defense from: {args.defense_csv}")
        defense = pd.read_csv(args.defense_csv, encoding="utf-8-sig")

        defense["espn_team_id"] = defense["espn_team_id"].astype(str)
        df["espn_opp_team_id"] = df["espn_opp_team_id"].astype(str)

        defense_cols = [
            "espn_team_id",
            "OVERALL_DEF_RANK",
            "DEF_TIER"
        ]

        df = df.merge(
            defense[defense_cols],
            left_on="espn_opp_team_id",
            right_on="espn_team_id",
            how="left"
        )

        df.rename(columns={
            "OVERALL_DEF_RANK": "OPP_OVERALL_DEF_RANK",
            "DEF_TIER": "OPP_DEF_TIER"
        }, inplace=True)

        df.drop(columns=["espn_team_id"], inplace=True, errors="ignore")

    # -----------------------------------
    # Save
    # -----------------------------------

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    copy_pipeline_output_to_dated_dirs(
        output_path=args.output,
        df=df,
        sport_dir_name="WNBA",
        repo_root=_REPO_ROOT,
    )
    print(f"✅ Saved → {args.output}")
    print(f"Rows: {len(df)}")


if __name__ == "__main__":
    main()
