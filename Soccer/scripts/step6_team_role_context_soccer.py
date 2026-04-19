#!/usr/bin/env python3
"""
step6_team_role_context_soccer.py  (Soccer Pipeline)

Mirrors NBA step6_team_role_context.py but uses soccer positions:
  GK  = Goalkeeper
  DEF = Defender
  MID = Midfielder
  FWD = Forward / Attacker

Adds:
  minutes_tier    LOW / MEDIUM / HIGH
  shot_role       LOW_VOL / MID_VOL / HIGH_VOL
  usage_role      SUPPORT / SECONDARY / PRIMARY
  position_group  GK / DEF / MID / FWD

Run:
  py -3.14 step6_team_role_context_soccer.py \
    --input step5_soccer_hit_rates.csv \
    --output step6_soccer_role_context.csv
"""

from __future__ import annotations

import argparse
import sys
import pandas as pd
import numpy as np


# ── Soccer position normalizer ───────────────────────────────────────────────

POSITION_MAP = {
    # Goalkeeper variants
    "gk":  "GK", "goalkeeper": "GK", "g": "GK", "portero": "GK",
    # Defender variants
    "d":   "DEF", "def": "DEF", "defender": "DEF", "cb": "DEF", "lb": "DEF",
    "rb":  "DEF", "rwb": "DEF", "lwb": "DEF", "centre-back": "DEF",
    "fullback": "DEF",
    # Midfielder variants
    "m":   "MID", "mid": "MID", "midfielder": "MID", "cm": "MID", "cdm": "MID",
    "cam": "MID", "lm": "MID", "rm": "MID", "dm": "MID", "winger": "MID",
    "lw":  "MID", "rw": "MID",
    # Forward/Attacker variants
    "f":   "FWD", "fwd": "FWD", "forward": "FWD", "st": "FWD", "cf": "FWD",
    "striker": "FWD", "attacker": "FWD", "ss": "FWD",
}

def norm_position(pos: str) -> str:
    p = str(pos or "").lower().strip().replace("-", "").replace(" ", "")
    return POSITION_MAP.get(p, "MID")   # default MID if unknown


# ── Tier functions ────────────────────────────────────────────────────────────
# Soccer averages are lower than NBA — tune thresholds accordingly

def tier_minutes(x) -> str:
    """Soccer players play 90 min max. Most starters play 70+."""
    if pd.isna(x):    return "UNKNOWN"
    if x < 45:        return "LOW"       # Sub / limited role
    if x < 70:        return "MEDIUM"    # Rotational / partial
    return "HIGH"                        # Regular starter


def tier_shot_volume(x, pos_group: str = "MID") -> str:
    """Shot volume tier by position group (shots per game, last 5)."""
    if pd.isna(x):    return "UNKNOWN"
    if pos_group == "FWD":
        if x <= 1.5:  return "LOW_VOL"
        if x <= 3.0:  return "MID_VOL"
        return "HIGH_VOL"
    elif pos_group == "MID":
        if x <= 0.8:  return "LOW_VOL"
        if x <= 2.0:  return "MID_VOL"
        return "HIGH_VOL"
    else:  # DEF / GK
        if x <= 0.3:  return "LOW_VOL"
        if x <= 1.0:  return "MID_VOL"
        return "HIGH_VOL"


def tier_passes(x, pos_group: str = "MID") -> str:
    """Passes attempted per game."""
    if pd.isna(x):    return "UNKNOWN"
    if pos_group == "GK":
        thresholds = (20, 40)
    elif pos_group == "DEF":
        thresholds = (30, 55)
    elif pos_group == "MID":
        thresholds = (40, 70)
    else:  # FWD
        thresholds = (20, 40)
    if x <= thresholds[0]: return "SUPPORT"
    if x <= thresholds[1]: return "SECONDARY"
    return "PRIMARY"


def tier_field_involvement(x, pos_group: str = "MID") -> str:
    """
    Involvement tier based on passes + shots composite proxy.
    Thresholds differ by position since MF/DEF touch the ball far more than FWD.
    """
    if pd.isna(x):    return "UNKNOWN"
    if pos_group == "FWD":
        if x <= 20:   return "FRINGE"
        if x <= 40:   return "ROTATIONAL"
        return "STARTER"
    elif pos_group == "MID":
        if x <= 30:   return "FRINGE"
        if x <= 60:   return "ROTATIONAL"
        return "STARTER"
    elif pos_group == "DEF":
        if x <= 25:   return "FRINGE"
        if x <= 50:   return "ROTATIONAL"
        return "STARTER"
    else:  # GK
        if x <= 15:   return "FRINGE"
        if x <= 35:   return "ROTATIONAL"
        return "STARTER"


def _assign_starter_tier(row: pd.Series) -> str:
    """
    Priority order:
    1. avg_minutes (real DB data when available)
    2. minutes_tier from step6 heuristics (HIGH/MEDIUM/LOW)
    3. position_group + pick_type inference
    """
    # Level 1 — real minutes data
    avg_min = pd.to_numeric(pd.Series([row.get("avg_minutes")]), errors="coerce").iloc[0]
    if pd.notna(avg_min) and float(avg_min) > 0:
        m = float(avg_min)
        if m >= 60:
            return "STARTER"
        if m >= 30:
            return "ROTATION"
        return "SUB"

    # Level 2 — minutes_tier heuristic from step6
    mt = str(row.get("minutes_tier") or "").strip().upper()
    if mt == "HIGH":
        return "STARTER"
    if mt == "MEDIUM":
        return "ROTATION"
    if mt == "LOW":
        return "SUB"

    # Level 3 — position + pick_type inference
    pos = str(row.get("position_group") or "").strip().upper()
    pick = str(row.get("pick_type") or "").strip().lower()

    # Goalkeepers almost always start (only 1 per team)
    if pos == "GK":
        return "STARTER"

    # Goblin lines = low lines = player likely plays limited minutes
    if "goblin" in pick:
        return "ROTATION"

    # DEF/MID with standard lines typically start
    if pos in ("DEF", "MID"):
        return "STARTER"

    # FWD — could be starter or rotation, default rotation
    if pos == "FWD":
        return "ROTATION"

    return "UNKNOWN"


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    print(f"→ Loading: {args.input}")
    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")

    if df.empty:
        print("❌ [PropOracle-Soccer-S6] Empty input from S5 — aborting.")
        sys.exit(1)

    # Normalize position to group
    if "pos" in df.columns:
        df["position_group"] = df["pos"].astype(str).apply(norm_position)
    else:
        df["position_group"] = "MID"

    # Pull numeric averages
    last5_avg   = pd.to_numeric(df.get("stat_last5_avg",   pd.Series(dtype=float)), errors="coerce")
    season_avg  = pd.to_numeric(df.get("stat_season_avg",  pd.Series(dtype=float)), errors="coerce")
    avg_minutes = pd.to_numeric(df.get("avg_minutes",      pd.Series(dtype=float)), errors="coerce")

    prop_norm = df.get("prop_norm", pd.Series([""] * len(df))).astype(str)

    new_cols = {}

    # minutes_tier — use avg_minutes from S4 stats when available,
    # fall back to position-based inference (soccer ESPN feed rarely has minutes)
    def _minutes_tier(i):
        v = avg_minutes.iloc[i]
        if not pd.isna(v):
            return tier_minutes(v)
        # Infer from position: starters in key positions → HIGH, subs/GK backups → MEDIUM
        pg = df["position_group"].iloc[i] if "position_group" in df.columns else "MID"
        pt = str(df.get("pick_type", pd.Series(["Standard"] * len(df))).iloc[i]).lower()
        # Goblins tend to be set for players who DO play; Demons for players with high usage
        if "gob" in pt:  return "HIGH"
        if "dem" in pt:  return "HIGH"
        # Position inference: GK and starting DEF/MID typically play full 90
        if pg == "GK":   return "HIGH"
        if pg == "DEF":  return "MEDIUM"
        if pg == "MID":  return "MEDIUM"
        return "LOW"  # FWD with no data — rotational

    new_cols["minutes_tier"] = pd.Series([_minutes_tier(i) for i in range(len(df))], index=df.index)
    df["starter_tier"] = df.apply(_assign_starter_tier, axis=1)

    # shot_volume — shooting output tier per position
    def _shot_volume(i):
        pg  = df["position_group"].iloc[i] if "position_group" in df.columns else "MID"
        val = last5_avg.iloc[i] if prop_norm.iloc[i] == "shots" else np.nan
        return tier_shot_volume(val, pg)
    new_cols["shot_volume"] = pd.Series([_shot_volume(i) for i in range(len(df))], index=df.index)

    # field_involvement — composite passes+shots involvement proxy
    def _field_involvement(i):
        pg  = df["position_group"].iloc[i] if "position_group" in df.columns else "MID"
        val = last5_avg.iloc[i] if prop_norm.iloc[i] == "passes" else season_avg.iloc[i]
        return tier_field_involvement(val, pg)
    new_cols["field_involvement"] = pd.Series([_field_involvement(i) for i in range(len(df))], index=df.index)

    # pass_role
    def _pass_role(i):
        pg  = df["position_group"].iloc[i] if "position_group" in df.columns else "MID"
        val = last5_avg.iloc[i] if prop_norm.iloc[i] == "passes" else np.nan
        return tier_passes(val, pg)
    new_cols["pass_role"] = pd.Series([_pass_role(i) for i in range(len(df))], index=df.index)

    # GK flag — useful downstream
    if "position_group" in df.columns:
        new_cols["is_goalkeeper"] = (df["position_group"] == "GK").astype(int)
    else:
        new_cols["is_goalkeeper"] = 0

    df = pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1).copy()

    # Add aliases so step8 column references work correctly
    # step8 looks for shot_role and usage_role; step6 computes shot_volume and field_involvement
    df["shot_role"]  = df["shot_volume"]
    df["usage_role"] = df["field_involvement"]

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    if df.empty:
        print("❌ [PropOracle-Soccer-S6] Output is empty — aborting.")
        sys.exit(1)
    print(f"✅ Saved → {args.output}  rows={len(df)}")
    if "position_group" in df.columns:
        print("position_group breakdown:")
        print(df["position_group"].value_counts().to_string())
    if "minutes_tier" in df.columns:
        print("minutes_tier breakdown:")
        print(df["minutes_tier"].value_counts().to_string())
    if "shot_volume" in df.columns:
        print("shot_volume breakdown:")
        print(df["shot_volume"].value_counts().to_string())
    if "field_involvement" in df.columns:
        print("field_involvement breakdown:")
        print(df["field_involvement"].value_counts().to_string())


if __name__ == "__main__":
    main()
