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
import pandas as pd


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

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"✅ Saved → {args.output}  rows={len(df)}")


if __name__ == "__main__":
    main()
