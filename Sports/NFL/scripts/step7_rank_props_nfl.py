#!/usr/bin/env python3
"""
NFL step7 — rank / tier props for the NFL pipeline (lightweight).

Reads step6_hit_rates.csv, assigns rank_score + tier, and writes step7_nfl_ranked.xlsx (ALL sheet).

Run from NFL/ with NFL_PIPELINE_ACTIVE=1.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _nfl_pipeline_active import require_nfl_pipeline_active_or_exit

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from utils.consistency_grade_scores import apply_consistency_grade_scores
from utils.defense_tiers import def_tier_from_overall_rank
from utils.group_rank_tier import assign_tier_column, report_goblin_demon_standard_line_fill


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def main() -> None:
    require_nfl_pipeline_active_or_exit()

    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/outputs/step6_hit_rates.csv")
    ap.add_argument("--output", default="outputs/step7_nfl_ranked.xlsx")
    args = ap.parse_args()

    in_path = Path(args.input)
    if not in_path.is_file():
        print(f"[NFL step7] Missing input: {in_path}")
        sys.exit(1)

    df = pd.read_csv(in_path, encoding="utf-8-sig")
    if df.empty:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame().to_excel(out, sheet_name="ALL", index=False)
        print(f"[NFL step7] Wrote empty {out}")
        return

    df = df.copy()
    prop_col = "prop_type_normalized" if "prop_type_normalized" in df.columns else "prop_type"
    if prop_col not in df.columns:
        print("[NFL step7] prop_type column missing")
        sys.exit(1)

    player_col = "player_name" if "player_name" in df.columns else ("player" if "player" in df.columns else "")
    if not player_col:
        print("[NFL step7] player column missing")
        sys.exit(1)

    def _col_series(*names: str) -> pd.Series:
        for n in names:
            if n in df.columns:
                return df[n]
        return pd.Series(np.nan, index=df.index)

    line = _num(_col_series("line_score", "line"))
    proj = _num(_col_series("projection", "proj", "stat_last5_avg", "stat_last10_avg"))
    if proj.isna().all() and line.notna().any():
        proj = line.copy()
    edge = proj - line
    df["line_score"] = line
    df["projection"] = proj
    df["edge"] = edge
    df["abs_edge"] = edge.abs()

    hr_raw = _num(df["hit_rate"]) if "hit_rate" in df.columns else pd.Series(np.nan, index=df.index)
    hr_adj = hr_raw.where(hr_raw.notna(), 0.52)
    hr_adj = np.where(hr_adj > 1.5, hr_adj / 100.0, hr_adj)
    df["hit_rate"] = np.clip(hr_adj, 0.35, 0.92)

    opp_rnk = _num(df["opp_pass_def_rank"]) if "opp_pass_def_rank" in df.columns else pd.Series(np.nan, index=df.index)
    df["opp_pass_def_rank"] = opp_rnk
    def _opp_def_lbl(r: object) -> str:
        if pd.isna(r):
            return "UNKNOWN"
        try:
            return def_tier_from_overall_rank(int(float(r)), 32)
        except (TypeError, ValueError):
            return "UNKNOWN"

    df["def_tier"] = df["opp_pass_def_rank"].apply(_opp_def_lbl)

    base = 4.0 + 6.0 * pd.Series(df["hit_rate"], dtype=float)
    edge_bonus = (df["abs_edge"].fillna(0) / 10.0).clip(0, 1.5)
    rank_pts = base + edge_bonus
    if "opp_pass_def_rank" in df.columns:
        rank_pts = rank_pts + (32.0 - df["opp_pass_def_rank"].clip(1, 32)) / 80.0
    df["prop_score"] = rank_pts
    df["rank_score"] = rank_pts

    ts = df.get("start_time", df.get("game_time", ""))
    df["start_time"] = ts
    df["game_time"] = ts

    dir_u = np.where(df["edge"].fillna(0) >= 0, "OVER", "UNDER")
    df["recommended_side"] = dir_u
    df["bet_direction"] = dir_u

    df["ml_prob"] = pd.to_numeric(df["hit_rate"], errors="coerce").clip(0.35, 0.92)
    apply_consistency_grade_scores(df, "NFL")
    df["tier"] = assign_tier_column(df, sport="nfl")
    report_goblin_demon_standard_line_fill(df, "[NFL step7]")

    df["player_name"] = df[player_col].astype(str)
    df["stat_type"] = df[prop_col].astype(str)
    df["stat_norm"] = df[prop_col].astype(str)
    df["pick_type"] = df.get("pick_type", "Standard")
    df["pp_tier"] = df.get("pp_tier", "")

    df["composite_hit_rate"] = df["hit_rate"]
    df["line_hit_rate"] = df["hit_rate"]

    elig = df[df["tier"].isin(["A", "B", "C"])].copy()
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="ALL", index=False)
        elig.to_excel(w, sheet_name="ELIGIBLE", index=False)
    print(f"[NFL step7] Wrote {out_path} rows={len(df)} (ALL), eligible={len(elig)}")


if __name__ == "__main__":
    main()
