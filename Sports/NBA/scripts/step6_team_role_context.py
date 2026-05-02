#!/usr/bin/env python3
"""
step6_team_role_context.py  (FIXED 2026-03-01)

FIX: minutes_tier, shot_role, and usage_role were all being derived from
     stat_last5_avg (the prop scoring avg), which gave completely wrong tiers
     for low-volume stats like STL/BLK/stocks. A player playing 35 min/game
     with a steals avg of 1.2 was getting minutes_tier=LOW.

     Now:
       - minutes_tier uses min_last5_avg (actual minutes played avg from step4)
       - shot_role    uses stat_last5_avg for FGA-type props; otherwise
                      uses min_last5_avg as proxy (more mins = more shots)
       - usage_role   uses stat_last5_avg for scoring props; otherwise
                      uses minutes proxy

FIX: step4 now outputs min_last5_avg (real ESPN MIN column avg).
     step6 reads that column. If missing (older cache), falls back to UNKNOWN.

FIX: Removed broken merge path that was producing step6_with_context.csv with
     1,589 dropped rows (referenced cbb_player_id / espn_opp_team_id which
     don't exist in the NBA pipeline). Only one output file now.

Run:
  py -3.14 step6_team_role_context.py --input step5_with_line_hit_rates.csv --output step6_with_team_role_context.csv
"""

from __future__ import annotations
import argparse
import pandas as pd
import numpy as np


# ----------------------------
# Tier functions
# ----------------------------

def tier_minutes(x):
    """Based on actual avg minutes played."""
    if pd.isna(x): return "UNKNOWN"
    if x <= 20: return "LOW"
    if x <= 30: return "MEDIUM"
    return "HIGH"

def tier_shots(x):
    """Based on actual FGA avg (for shot-volume props)."""
    if pd.isna(x): return "UNKNOWN"
    if x <= 8:  return "LOW_VOL"
    if x <= 14: return "MID_VOL"
    return "HIGH_VOL"

def tier_shots_from_min(x):
    """Minutes proxy for shot volume (for non-FGA props)."""
    if pd.isna(x): return "UNKNOWN"
    if x <= 18: return "LOW_VOL"
    if x <= 28: return "MID_VOL"
    return "HIGH_VOL"

def tier_usage(x):
    """Based on scoring/combined stat avg."""
    if pd.isna(x): return "UNKNOWN"
    if x <= 10: return "SUPPORT"
    if x <= 18: return "SECONDARY"
    return "PRIMARY"

def tier_usage_from_min(x):
    """Minutes proxy for usage (for non-scoring props)."""
    if pd.isna(x): return "UNKNOWN"
    if x <= 18: return "SUPPORT"
    if x <= 28: return "SECONDARY"
    return "PRIMARY"


# Props where stat_last5_avg IS a shot-volume count
_SHOT_VOL_PROPS = {
    "fga", "fgm", "fg2a", "fg2m", "fg3a", "fg3m",
    "3ptmade", "3ptattempted", "fta", "ftm",
    "freethrowsmade", "freethrowsattempted",
}

# Props where stat_last5_avg is a meaningful scoring/usage indicator
_USAGE_PROPS = {
    "pts", "points", "pra", "pr", "pa", "ra", "fantasy",
    "fga", "fgm", "fg2a", "fg2m",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--roles-csv",   default=None)
    ap.add_argument("--defense-csv", default=None)
    args = ap.parse_args()

    print(f"→ Loading: {args.input}")
    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")

    stat_last5 = pd.to_numeric(df.get("stat_last5_avg"), errors="coerce")
    min_last5  = pd.to_numeric(df.get("min_last5_avg"),  errors="coerce")
    prop_norm  = df.get("prop_norm", pd.Series([""] * len(df), index=df.index)).astype(str).str.lower().str.strip()

    if min_last5.isna().all():
        print("⚠️  min_last5_avg column missing or all NaN — minutes_tier will be UNKNOWN.")
        print("   Re-run step4 to populate min_last5_avg from ESPN cache.")

    # minutes_tier — always from real minutes avg
    minutes_tier = min_last5.apply(tier_minutes)

    # shot_role — use actual FGA avg for shot props; minutes proxy for others
    is_shot_prop = prop_norm.isin(_SHOT_VOL_PROPS)
    shot_role = pd.Series("UNKNOWN", index=df.index)
    shot_role = shot_role.where(~is_shot_prop,  stat_last5.apply(tier_shots))
    shot_role = shot_role.where( is_shot_prop,  min_last5.apply(tier_shots_from_min))

    # usage_role — use scoring avg for scoring props; minutes proxy for others
    is_usage_prop = prop_norm.isin(_USAGE_PROPS)
    usage_role = pd.Series("UNKNOWN", index=df.index)
    usage_role = usage_role.where(~is_usage_prop, stat_last5.apply(tier_usage))
    usage_role = usage_role.where( is_usage_prop, min_last5.apply(tier_usage_from_min))

    new_cols = pd.DataFrame({
        "min_player_avg": min_last5,
        "pts_player_avg": stat_last5,
        "minutes_tier":   minutes_tier,
        "shot_role":      shot_role,
        "usage_role":     usage_role,
    }, index=df.index)
    df = pd.concat([df, new_cols], axis=1).copy()

    # Optional: Team Roles
    if args.roles_csv:
        print(f"→ Merging roles from: {args.roles_csv}")
        roles = pd.read_csv(args.roles_csv, encoding="utf-8-sig")
        roles["PLAYER_ID"] = roles["PLAYER_ID"].astype(str)
        if "nba_player_id" in df.columns:
            df["nba_player_id"] = df["nba_player_id"].astype(str)
            role_cols = [c for c in roles.columns if c.startswith("role_")]
            df = df.merge(
                roles[["PLAYER_ID"] + role_cols],
                left_on="nba_player_id", right_on="PLAYER_ID", how="left"
            )
            df.drop(columns=["PLAYER_ID"], inplace=True, errors="ignore")

    # Optional: Defense
    if args.defense_csv:
        print(f"→ Merging defense from: {args.defense_csv}")
        defense = pd.read_csv(args.defense_csv, encoding="utf-8-sig")
        if "espn_team_id" in defense.columns and "espn_opp_team_id" in df.columns:
            defense["espn_team_id"] = defense["espn_team_id"].astype(str)
            df["espn_opp_team_id"]  = df["espn_opp_team_id"].astype(str)
            defense_cols = [c for c in ["espn_team_id","OVERALL_DEF_RANK","DEF_TIER"] if c in defense.columns]
            df = df.merge(defense[defense_cols], left_on="espn_opp_team_id", right_on="espn_team_id", how="left")
            df.rename(columns={"OVERALL_DEF_RANK":"OPP_OVERALL_DEF_RANK","DEF_TIER":"OPP_DEF_TIER"}, inplace=True)
            df.drop(columns=["espn_team_id"], inplace=True, errors="ignore")

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"✅ Saved → {args.output}")
    print(f"Rows: {len(df)} | Cols: {len(df.columns)}")
    print()
    print("minutes_tier breakdown:")
    print(df["minutes_tier"].value_counts().to_string())
    print()
    print("shot_role breakdown:")
    print(df["shot_role"].value_counts().to_string())
    print()
    print("usage_role breakdown:")
    print(df["usage_role"].value_counts().to_string())


if __name__ == "__main__":
    main()
