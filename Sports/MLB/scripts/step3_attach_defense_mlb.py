#!/usr/bin/env python3
"""
step3_attach_defense_mlb.py  (MLB Pipeline)

Merges starting pitcher quality and team-level defensive ratings
onto each prop row.

For MLB, "defense context" means two things:
  1. Team pitching rank (for hitter props) — how good is the opposing pitcher?
  2. Team fielding/run-prevention rank (general)

Inputs:
  --input    step2_mlb_picktypes.csv
  --defense  mlb_defense_summary.csv   (team pitching/defense ratings)
Output:
  --output   step3_mlb_with_defense.csv

The defense CSV should have at minimum:
  TEAM_ABBREVIATION, OVERALL_DEF_RANK
  Optional: ERA_RANK, WHIP_RANK, OBP_ALLOWED_RANK, SP_ERA, etc.

Run:
  py -3.14 step3_attach_defense_mlb.py \
    --input step2_mlb_picktypes.csv \
    --defense mlb_defense_summary.csv \
    --output step3_mlb_with_defense.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

_MLB_REPO = Path(__file__).resolve().parents[3]
if str(_MLB_REPO) not in sys.path:
    sys.path.insert(0, str(_MLB_REPO))
from utils.defense_tiers import assert_def_tier_column, format_def_tier_counts


def _col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _safe_upper(x) -> str:
    return str(x or "").strip().upper()


def split_slash(s: str) -> Tuple[str, str]:
    parts = [p.strip() for p in str(s or "").split("/")]
    return (parts[0], parts[1]) if len(parts) >= 2 else (parts[0], "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",   default="step2_mlb_picktypes.csv")
    ap.add_argument("--defense", default="mlb_defense_summary.csv")
    ap.add_argument("--output",  default="step3_mlb_with_defense.csv")
    args = ap.parse_args()

    print(f"→ Loading Step2: {args.input}")
    df = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig").fillna("")

    print(f"→ Loading defense: {args.defense}")
    d  = pd.read_csv(args.defense, dtype=str, encoding="utf-8-sig").fillna("")

    key = _col(d, ["TEAM_ABBREVIATION", "team_abbr", "abbr", "TEAM_ABBR", "team"])
    if not key:
        raise RuntimeError(f"❌ Defense file missing team key. Columns: {list(d.columns)}")

    d[key] = d[key].astype(str).str.strip().str.upper()
    def_cols = [c for c in d.columns if c != key]

    if "opp_team" not in df.columns:
        df["opp_team"] = ""
    df["opp_team"] = df["opp_team"].astype(str).str.strip().str.upper()

    if "is_combo_player" not in df.columns:
        df["is_combo_player"] = df["player"].astype(str).str.contains(r"\+").astype(int)

    singles_mask = df["is_combo_player"].astype(str).isin(["0", "False", "false", ""])
    combos_mask  = ~singles_mask

    # ── Singles: merge opponent's defensive rating ──
    print("→ Merging defense for singles...")
    singles = df.loc[singles_mask].copy()
    singles = singles.merge(
        d[[key] + def_cols], how="left", left_on="opp_team", right_on=key
    )
    if key in singles.columns:
        singles.drop(columns=[key], inplace=True)

    # ── Combos: merge both sides ──
    combos = df.loc[combos_mask].copy()
    if len(combos) > 0:
        for c in ["team_1", "team_2", "pp_home_team", "pp_away_team"]:
            if c not in combos.columns:
                combos[c] = ""

        def _derive_opps(row):
            t1   = _safe_upper(row.get("team_1", ""))
            t2   = _safe_upper(row.get("team_2", ""))
            home = _safe_upper(row.get("pp_home_team", ""))
            away = _safe_upper(row.get("pp_away_team", ""))
            if home and away and t1 and t2:
                opp1 = away if t1 == home else (home if t1 == away else "")
                opp2 = away if t2 == home else (home if t2 == away else "")
                return opp1, opp2
            opp = str(row.get("opp_team", "")).strip()
            if "/" in opp:
                return split_slash(opp)
            return "", ""

        opps = combos.apply(_derive_opps, axis=1, result_type="expand")
        opps.columns = ["opp_team_1", "opp_team_2"]
        combos["opp_team_1"] = opps["opp_team_1"].str.upper()
        combos["opp_team_2"] = opps["opp_team_2"].str.upper()

        leg1 = combos.merge(d[[key] + def_cols], how="left", left_on="opp_team_1", right_on=key)
        if key in leg1.columns: leg1.drop(columns=[key], inplace=True)
        leg2 = combos.merge(d[[key] + def_cols], how="left", left_on="opp_team_2", right_on=key)
        if key in leg2.columns: leg2.drop(columns=[key], inplace=True)

        leg1 = leg1.rename(columns={c: f"{c}_DEF_1" for c in def_cols})
        leg2 = leg2.rename(columns={c: f"{c}_DEF_2" for c in def_cols})
        combos = pd.concat([
            combos.reset_index(drop=True),
            leg1[[c for c in leg1.columns if c.endswith("_DEF_1")]].reset_index(drop=True),
            leg2[[c for c in leg2.columns if c.endswith("_DEF_2")]].reset_index(drop=True),
        ], axis=1)

    out = pd.concat([singles, combos], axis=0, ignore_index=True)

    if "def_tier" in out.columns and "DEF_TIER" not in out.columns:
        out = out.rename(columns={"def_tier": "DEF_TIER"})

    desired_front = ["mlb_player_id", "player", "pos", "player_type", "team", "opp_team",
                     "line", "prop_type", "prop_norm", "pick_type"]
    front  = [c for c in desired_front if c in out.columns]
    tail   = ["is_combo_player"] if "is_combo_player" in out.columns else []
    middle = [c for c in out.columns if c not in set(front + tail)]
    out    = out[front + middle + tail]

    _dt_col = "DEF_TIER" if "DEF_TIER" in out.columns else ("def_tier" if "def_tier" in out.columns else None)
    if _dt_col:
        _chk = out[[_dt_col]].rename(columns={_dt_col: "def_tier"})
        _m = _chk["def_tier"].astype(str).str.strip().ne("")
        if _m.any():
            assert_def_tier_column(_chk.loc[_m], "def_tier", allow_empty=False)
        print(f"[MLB step3] {format_def_tier_counts(_chk, 'def_tier')}")

    out.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"✅ Saved → {args.output}")
    print(f"Rows: {len(out)} | Cols: {len(out.columns)}")

    if "OVERALL_DEF_RANK" in out.columns:
        filled = (out["OVERALL_DEF_RANK"].astype(str).str.strip() != "").sum()
        print(f"Defense filled (OVERALL_DEF_RANK): {filled}/{len(out)}")


if __name__ == "__main__":
    main()
