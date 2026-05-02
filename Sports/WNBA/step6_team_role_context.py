#!/usr/bin/env python3
"""
step6_cbb_team_role_context.py

CBB Step 6:
- Adds minutes / shot / usage tiers
- Merges team roles (role_*) from Step10
- Merges defense context from Step11
"""

from __future__ import annotations
import argparse
import pandas as pd
import numpy as np


# ----------------------------
# Tiering
# ----------------------------

def tier_minutes(x):
    if pd.isna(x): return "UNKNOWN"
    if x <= 20: return "LOW"
    if x <= 30: return "MEDIUM"
    return "HIGH"

def tier_shots(x):
    if pd.isna(x): return "UNKNOWN"
    if x <= 8: return "LOW_VOL"
    if x <= 14: return "MID_VOL"
    return "HIGH_VOL"

def tier_usage(x):
    if pd.isna(x): return "UNKNOWN"
    if x <= 10: return "SUPPORT"
    if x <= 18: return "SECONDARY"
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

    # Collect all new columns into a dict and concat once to avoid
    # DataFrame fragmentation warnings on wide DataFrames (95+ cols)
    last5 = pd.to_numeric(df.get("stat_last5_avg"), errors="coerce")
    new_cols = pd.DataFrame({
        "min_player_avg": last5,
        "fga_player_avg": last5,
        "pts_player_avg": last5,
        "minutes_tier":   last5.apply(tier_minutes),
        "shot_role":      last5.apply(tier_shots),
        "usage_role":     last5.apply(tier_usage),
    }, index=df.index)
    df = pd.concat([df, new_cols], axis=1).copy()

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
    print(f"✅ Saved → {args.output}")
    print(f"Rows: {len(df)}")


if __name__ == "__main__":
    main()
