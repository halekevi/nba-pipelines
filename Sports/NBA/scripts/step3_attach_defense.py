#!/usr/bin/env python3
"""
step3_attach_defense.py  (NBA Pipeline)

Left-merges opponent defensive context onto each prop row.
Merges on: opp_team (slate) → TEAM_ABBREVIATION (defense CSV)

Defense file: defense_team_summary.csv
  Must include TEAM_ABBREVIATION + OVERALL_DEF_RANK + DEF_TIER

Run:
  py -3.14 step3_attach_defense.py \
      --input  data\\outputs\\step2_with_picktypes.csv \
      --defense defense_team_summary.csv \
      --output data\\outputs\\step3_with_defense.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_NBA_REPO = Path(__file__).resolve().parents[3]
if str(_NBA_REPO) not in sys.path:
    sys.path.insert(0, str(_NBA_REPO))
from utils.defense_tiers import assert_def_tier_column, format_def_tier_counts


TEAM_ALIAS_FIX = {
    "BRK": "BKN", "BKN": "BRK",   # pipeline uses BRK, nba_api uses BKN
    "GS":  "GSW",
    "NO":  "NOP",
    "NY":  "NYK",
    "SA":  "SAS",
    "PHO": "PHX",
    "WSH": "WAS",
    "UTAH": "UTA",
}


def norm_team(t) -> str:
    if not t or (isinstance(t, float)):
        return ""
    s = str(t).strip().upper()
    return TEAM_ALIAS_FIX.get(s, s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",   required=True)
    ap.add_argument("--defense", required=True)
    ap.add_argument("--output",  required=True)
    args = ap.parse_args()

    print(f"→ Loading: {args.input}")
    df = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig").fillna("")
    print(f"  {len(df)} rows")

    print(f"→ Loading defense: {args.defense}")
    d = pd.read_csv(args.defense, dtype=str, encoding="utf-8-sig").fillna("")
    print(f"  {len(d)} teams")

    # Normalize team keys in both frames
    df["opp_team"] = df["opp_team"].apply(norm_team)

    # Combo props: step2 leaves opp_team blank. Player_1 is on team_1, player_2 on team_2
    # (same game). Use team_2 as the defense team_1 faces (same as singles opp).
    if "is_combo_player" in df.columns and "team_1" in df.columns and "team_2" in df.columns:
        icp = pd.to_numeric(df["is_combo_player"], errors="coerce").fillna(0).astype(int).eq(1)
        blank_opp = df["opp_team"].isna() | df["opp_team"].astype(str).str.strip().isin(["", "nan", "None"])
        for idx in df.index[icp & blank_opp]:
            t1 = norm_team(df.at[idx, "team_1"])
            t2 = norm_team(df.at[idx, "team_2"])
            if t1 and t2 and t1 != t2:
                df.at[idx, "opp_team"] = t2
            elif "pp_home_team" in df.columns and "pp_away_team" in df.columns:
                ph = norm_team(df.at[idx, "pp_home_team"])
                pa = norm_team(df.at[idx, "pp_away_team"])
                if t1 and ph and pa:
                    if t1 == ph:
                        df.at[idx, "opp_team"] = pa
                    elif t1 == pa:
                        df.at[idx, "opp_team"] = ph

    d["TEAM_ABBREVIATION"] = d["TEAM_ABBREVIATION"].apply(norm_team)

    # Also normalize defense with aliases so both sides match
    # Build a second alias pass so BRK/BKN both resolve
    def_cols = [c for c in d.columns if c != "TEAM_ABBREVIATION"]

    # Merge
    before = len(df)
    df = df.merge(
        d[["TEAM_ABBREVIATION"] + def_cols],
        how="left",
        left_on="opp_team",
        right_on="TEAM_ABBREVIATION",
    )
    # Drop the redundant key column added by merge
    if "TEAM_ABBREVIATION" in df.columns:
        df.drop(columns=["TEAM_ABBREVIATION"], inplace=True)

    filled = df["OVERALL_DEF_RANK"].notna().sum() if "OVERALL_DEF_RANK" in df.columns else 0
    print(f"  Defense filled (OVERALL_DEF_RANK): {filled}/{len(df)}")

    # Warn about unmatched teams
    unmatched = df.loc[df["OVERALL_DEF_RANK"].isna(), "opp_team"].unique()
    unmatched = [t for t in unmatched if t]  # skip empty (combo props)
    if unmatched:
        print(f"  ⚠️  Unmatched opp teams: {sorted(unmatched)}")
        print(f"     Check TEAM_ALIAS_FIX or refresh defense_team_summary.csv")

    if "def_tier" in df.columns and "DEF_TIER" not in df.columns:
        df = df.rename(columns={"def_tier": "DEF_TIER"})

    _dt_col = "DEF_TIER" if "DEF_TIER" in df.columns else ("def_tier" if "def_tier" in df.columns else None)
    if _dt_col:
        _chk = df[[_dt_col]].rename(columns={_dt_col: "def_tier"})
        _m = _chk["def_tier"].astype(str).str.strip().ne("")
        if _m.any():
            assert_def_tier_column(_chk.loc[_m], "def_tier", allow_empty=False)
        print(f"[NBA step3] {format_def_tier_counts(_chk, 'def_tier')}")

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"✅ Saved → {args.output}  rows={len(df)}")


if __name__ == "__main__":
    main()
