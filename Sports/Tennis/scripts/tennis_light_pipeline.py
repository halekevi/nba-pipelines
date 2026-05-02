#!/usr/bin/env python3
"""
Light Tennis ETL: step1 PrizePicks CSV -> step7_ranked.xlsx + step8_direction_clean.xlsx.

Produces combined_slate_tickets-compatible workbooks. Direction is placeholder OVER with
projection = line until a full stats / direction pipeline exists.

Run from Tennis/ (or pass absolute paths):
  py -3.14 scripts/tennis_light_pipeline.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="Tennis light ETL: step1 -> step7 + step8 xlsx.")
    ap.add_argument("--input", default="outputs/step1_tennis_props.csv")
    ap.add_argument("--step7-out", default="outputs/step7_tennis_ranked.xlsx")
    ap.add_argument("--step8-xlsx", default="outputs/step8_tennis_direction_clean.xlsx")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_absolute():
        inp = root / inp
    if not inp.is_file():
        raise SystemExit(f"Missing input: {inp}")

    raw = pd.read_csv(inp, dtype=str, low_memory=False)
    raw["line"] = pd.to_numeric(raw.get("line"), errors="coerce")
    raw = raw.dropna(subset=["line"])
    raw = raw[raw["line"] >= 0]

    sort_cols = [c for c in ("start_time", "player", "prop_type") if c in raw.columns]
    work = raw.sort_values(sort_cols, na_position="last").reset_index(drop=True)
    n = max(len(work), 1)
    rank_score = 4.0 + 3.0 * (np.arange(n, dtype=float) / max(n - 1, 1))
    work["rank_score"] = rank_score

    def tier_for(rs: float) -> str:
        if rs >= 6.2:
            return "A"
        if rs >= 5.3:
            return "B"
        return "C"

    work["tier"] = work["rank_score"].map(tier_for)
    opp_col = "opp_team" if "opp_team" in work.columns else "opp"

    out = pd.DataFrame(
        {
            "Player": work.get("player", pd.Series([""] * len(work))).fillna("").astype(str).str.strip(),
            "Tier": work["tier"],
            "Rank Score": work["rank_score"],
            "Pos": work.get("pos", pd.Series([""] * len(work))).fillna("").astype(str),
            "Team": work.get("team", pd.Series([""] * len(work))).fillna("").astype(str).str.upper(),
            "Opp": work.get(opp_col, pd.Series([""] * len(work))).fillna("").astype(str).str.upper(),
            "Game Time": work.get("start_time", pd.Series([""] * len(work))).fillna("").astype(str),
            "Prop": work.get("prop_type", pd.Series([""] * len(work))).fillna("").astype(str),
            "Pick Type": work.get("pick_type", pd.Series(["Standard"] * len(work))).fillna("Standard").astype(str),
            "Line": work["line"],
            "Direction": ["OVER"] * len(work),
            "Edge": [0.0] * len(work),
            "Projection": work["line"],
            "Hit Rate (5g)": np.nan,
            "Last 5 Avg": np.nan,
            "Season Avg": np.nan,
            "L5 Over": np.nan,
            "L5 Under": np.nan,
            "L10 Over": np.nan,
            "L10 Under": np.nan,
            "Def Tier": ["LEAGUE AVG"] * len(work),
        }
    )

    s7 = Path(args.step7_out)
    if not s7.is_absolute():
        s7 = root / s7
    s8 = Path(args.step8_xlsx)
    if not s8.is_absolute():
        s8 = root / s8
    s7.parent.mkdir(parents=True, exist_ok=True)
    s8.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(s7, engine="openpyxl") as w:
        out.to_excel(w, sheet_name="ALL", index=False)

    with pd.ExcelWriter(s8, engine="openpyxl") as w:
        out.to_excel(w, sheet_name="Tennis", index=False)
        out.to_excel(w, sheet_name="ALL", index=False)

    print(f"OK step7 -> {s7}  rows={len(out)}")
    print(f"OK step8 -> {s8}  rows={len(out)}")


if __name__ == "__main__":
    main()
