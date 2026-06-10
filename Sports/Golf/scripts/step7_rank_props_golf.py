#!/usr/bin/env python3
"""
step7_rank_props_golf.py — rank PrizePicks golf props for step8.

Reads step1 CSV (or step2 context CSV) and writes step7_golf_ranked.xlsx (sheet ALL).

Run:
  py -3.14 Sports/Golf/scripts/step7_rank_props_golf.py --input outputs/step1_golf_props.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _tier_for(rs: float) -> str:
    if rs >= 6.2:
        return "A"
    if rs >= 5.3:
        return "B"
    return "C"


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="Golf step7 — rank props from step1 CSV.")
    ap.add_argument("--input", default="outputs/step1_golf_props.csv")
    ap.add_argument("--output", default="outputs/step7_golf_ranked.xlsx")
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
    if raw.empty:
        raise SystemExit("No rows with valid line values.")

    sort_cols = [c for c in ("start_time", "event", "player", "prop_type") if c in raw.columns]
    work = raw.sort_values(sort_cols, na_position="last").reset_index(drop=True)
    n = len(work)
    rank_score = 4.0 + 3.0 * (np.arange(n, dtype=float) / max(n - 1, 1))
    work["rank_score"] = rank_score
    work["tier"] = work["rank_score"].map(_tier_for)

    out = pd.DataFrame(
        {
            "tier": work["tier"],
            "rank_score": work["rank_score"],
            "player": work.get("player", "").fillna("").astype(str).str.strip(),
            "pos": work.get("pos", "").fillna("").astype(str),
            "team": work.get("team", work.get("event", "")).fillna("").astype(str),
            "event": work.get("event", work.get("tournament", "")).fillna("").astype(str),
            "tournament": work.get("tournament", "").fillna("").astype(str),
            "course": work.get("course", "").fillna("").astype(str),
            "opp_team": work.get("opp_team", work.get("course", "")).fillna("").astype(str),
            "league": work.get("league", "PGA").fillna("PGA").astype(str),
            "start_time": work.get("start_time", "").fillna("").astype(str),
            "prop_type": work.get("prop_type", "").fillna("").astype(str),
            "pick_type": work.get("pick_type", "Standard").fillna("Standard").astype(str),
            "line": work["line"],
            "standard_line": pd.to_numeric(work.get("standard_line", work["line"]), errors="coerce"),
            "final_bet_direction": "OVER",
            "edge": 0.0,
            "abs_edge": 0.0,
            "projection": work["line"],
            "ml_prob": np.nan,
            "line_hit_rate_over_ou_5": np.nan,
            "line_hit_rate_over_ou_10": np.nan,
            "stat_last5_avg": np.nan,
            "stat_season_avg": np.nan,
            "last5_over": np.nan,
            "last5_under": np.nan,
            "DEF_TIER": "LEAGUE AVG",
            "OVERALL_DEF_RANK": "N/A",
            "sport": "Golf",
            "pp_projection_id": work.get("projection_id", work.get("pp_projection_id", "")).fillna("").astype(str),
            "pp_game_id": work.get("pp_game_id", "").fillna("").astype(str),
            "course_fit_score": pd.to_numeric(work.get("course_fit_score"), errors="coerce"),
            "sg_ott": pd.to_numeric(work.get("sg_ott"), errors="coerce"),
            "sg_app": pd.to_numeric(work.get("sg_app"), errors="coerce"),
            "sg_arg": pd.to_numeric(work.get("sg_arg"), errors="coerce"),
            "weather_signal": work.get("weather_signal", pd.NA),
        }
    )

    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = root / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        out.to_excel(w, sheet_name="ALL", index=False)

    print(f"[Golf step7] Saved → {out_path}  rows={len(out)}")


if __name__ == "__main__":
    main()
