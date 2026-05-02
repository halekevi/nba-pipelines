#!/usr/bin/env python3
"""
step3_attach_defense.py  (WNBA Pipeline)

Attaches opponent defensive context to each prop row.
Identical logic to NBA step3 — left-merge on opp_team vs defense CSV.

Defense file: wnba_defense_summary.csv
  Must include TEAM_ABBREVIATION (or team_abbr) + OVERALL_DEF_RANK + DEF_TIER.

Key difference from NBA: WNBA has 13 teams (not 30).
Tier cutoffs in defense_report_wnba.py use 13-team scale:
  1-3 Elite | 4-6 Above Avg | 7-9 Avg | 10-13 Weak

Run:
  py -3.14 step3_attach_defense.py \
      --input  step2_wnba_picktypes.csv \
      --defense wnba_defense_summary.csv \
      --output step3_wnba_defense.csv
"""

from __future__ import annotations

import argparse
from typing import List, Optional, Tuple

import pandas as pd


def _col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _safe_upper(x) -> str:
    return str(x or "").strip().upper()


def derive_combo_opponents(row: pd.Series) -> Tuple[str, str]:
    team1 = _safe_upper(row.get("team_1", ""))
    team2 = _safe_upper(row.get("team_2", ""))
    home  = _safe_upper(row.get("pp_home_team", ""))
    away  = _safe_upper(row.get("pp_away_team", ""))

    if home and away and team1 and team2:
        opp1 = away if team1 == home else (home if team1 == away else "")
        opp2 = away if team2 == home else (home if team2 == away else "")
        return opp1, opp2

    opp = str(row.get("opp_team", "")).strip()
    if "/" in opp:
        parts = [p.strip() for p in opp.split("/")]
        return (parts[0], parts[1]) if len(parts) >= 2 else ("", "")
    return "", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",   required=True)
    ap.add_argument("--defense", required=True)
    ap.add_argument("--output",  required=True)
    args = ap.parse_args()

    print(f"→ Loading: {args.input}")
    df = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig").fillna("")

    print(f"→ Loading defense: {args.defense}")
    d = pd.read_csv(args.defense, dtype=str, encoding="utf-8-sig").fillna("")

    key = _col(d, ["TEAM_ABBREVIATION","team_abbr","abbr","TEAM_ABBR"])
    if not key:
        raise RuntimeError(f"❌ Defense file missing TEAM_ABBREVIATION. Found: {list(d.columns)}")

    d[key] = d[key].astype(str).str.strip().str.upper()
    def_cols = [c for c in d.columns if c != key]

    if "opp_team" not in df.columns:
        df["opp_team"] = ""
    df["opp_team"] = df["opp_team"].astype(str).str.strip().str.upper()

    if "is_combo_player" not in df.columns:
        df["is_combo_player"] = df.get("player","").astype(str).str.contains(r"\+").astype(int)

    singles_mask = df["is_combo_player"].astype(str).isin(["0","False","false",""])
    combos_mask  = ~singles_mask

    # Singles
    singles = df.loc[singles_mask].copy()
    singles = singles.merge(d[[key]+def_cols], how="left", left_on="opp_team", right_on=key)
    if key in singles.columns:
        singles.drop(columns=[key], inplace=True)

    # Combos
    combos = df.loc[combos_mask].copy()
    if len(combos):
        for c in ["team_1","team_2","pp_home_team","pp_away_team"]:
            if c not in combos.columns:
                combos[c] = ""
        opps = combos.apply(derive_combo_opponents, axis=1, result_type="expand")
        opps.columns = ["opp_team_1","opp_team_2"]
        combos["opp_team_1"] = opps["opp_team_1"].astype(str).str.strip().str.upper()
        combos["opp_team_2"] = opps["opp_team_2"].astype(str).str.strip().str.upper()

        leg1 = combos.merge(d[[key]+def_cols], how="left", left_on="opp_team_1", right_on=key)
        if key in leg1.columns: leg1.drop(columns=[key], inplace=True)
        leg2 = combos.merge(d[[key]+def_cols], how="left", left_on="opp_team_2", right_on=key)
        if key in leg2.columns: leg2.drop(columns=[key], inplace=True)

        leg1 = leg1.rename(columns={c: f"{c}_DEF_1" for c in def_cols})
        leg2 = leg2.rename(columns={c: f"{c}_DEF_2" for c in def_cols})

        combos = pd.concat([
            combos.reset_index(drop=True),
            leg1[[c for c in leg1.columns if c.endswith("_DEF_1")]].reset_index(drop=True),
            leg2[[c for c in leg2.columns if c.endswith("_DEF_2")]].reset_index(drop=True),
        ], axis=1)

    out = pd.concat([singles, combos], axis=0, ignore_index=True)

    desired_front = ["wnba_player_id","player","pos","team","opp_team","line","prop_type","prop_norm","pick_type"]
    front  = [c for c in desired_front if c in out.columns]
    tail   = ["is_combo_player"] if "is_combo_player" in out.columns else []
    middle = [c for c in out.columns if c not in set(front+tail)]
    out    = out[front + middle + tail]

    out.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"✅ Saved → {args.output}  rows={len(out)}")

    if "OVERALL_DEF_RANK" in out.columns:
        filled = (out["OVERALL_DEF_RANK"].astype(str).str.strip() != "").sum()
        print(f"Defense filled (OVERALL_DEF_RANK): {filled}/{len(out)}")


if __name__ == "__main__":
    main()
