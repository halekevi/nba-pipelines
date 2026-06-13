#!/usr/bin/env python3
"""
step7_rank_props_golf.py — rank PrizePicks golf props for step8.

Reads step1/step2 CSV and writes step7_golf_ranked.xlsx (sheet ALL).

Run:
  py -3.14 Sports/Golf/scripts/step7_rank_props_golf.py --input outputs/step2_golf_context.csv
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

_GOLF_REPO = Path(__file__).resolve().parents[3]
if str(_GOLF_REPO) not in sys.path:
    sys.path.insert(0, str(_GOLF_REPO))
from utils.group_rank_tier import (  # noqa: E402
    assign_tier_column,
    report_goblin_demon_standard_line_fill,
)


def _norm_pick_type(x: str) -> str:
    t = (str(x) if x is not None else "").strip().lower()
    if "gob" in t:
        return "Goblin"
    if "dem" in t:
        return "Demon"
    return "Standard"


def _forced_over_only(pick_type: str) -> int:
    return 1 if _norm_pick_type(pick_type) in ("Goblin", "Demon") else 0


def _num_series(df: pd.DataFrame, col: str, default: float = np.nan) -> pd.Series:
    if col not in df.columns:
        return pd.Series(default, index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="Golf step7 — rank props from step1/2 CSV.")
    ap.add_argument("--input", default="outputs/step1_golf_props.csv")
    ap.add_argument("--output", default="outputs/step7_golf_ranked.xlsx")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_absolute():
        inp = root / inp
    if not inp.is_file():
        raise SystemExit(f"Missing input: {inp}")

    work = pd.read_csv(inp, dtype=str, low_memory=False)
    work["line"] = pd.to_numeric(work.get("line"), errors="coerce")
    work = work.dropna(subset=["line"])
    work = work[work["line"] >= 0]
    if work.empty:
        raise SystemExit("No rows with valid line values.")

    line = work["line"]
    l5 = _num_series(work, "stat_last5_avg")
    seas = _num_series(work, "stat_season_avg")
    proj = l5.fillna(seas).fillna(line)
    edge = proj - line

    hr5 = _num_series(work, "line_hit_rate_over_ou_5")
    hr10 = _num_series(work, "line_hit_rate_over_ou_10")
    hr10 = hr10.fillna(hr5)
    composite_hr = (0.5 * hr5.fillna(0.5) + 0.5 * hr10.fillna(0.5)).clip(0.0, 1.0)

    pick = work.get("pick_type", pd.Series(["Standard"] * len(work))).fillna("Standard").astype(str)
    forced = pick.map(_forced_over_only).astype(int)
    bet_dir = np.where(forced.eq(1), "OVER", np.where(edge >= 0, "OVER", "UNDER"))

    course_fit = _num_series(work, "course_fit_score", default=0.0).fillna(0.0).clip(-1.0, 1.0)
    sg_bonus = (
        _num_series(work, "sg_ott", default=0.0).fillna(0.0)
        + _num_series(work, "sg_app", default=0.0).fillna(0.0)
        + _num_series(work, "sg_arg", default=0.0).fillna(0.0)
    ).clip(-3.0, 3.0) * 0.05

    edge_z = (edge.abs().clip(0, 6) / 6.0).fillna(0.0)
    rank_score = (
        3.5
        + composite_hr * 5.0
        + edge_z * 1.5
        + course_fit * 0.4
        + sg_bonus
    ).clip(0.0, 10.0)

    out = pd.DataFrame(
        {
            "tier": "",
            "rank_score": rank_score.round(4),
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
            "pick_type": pick,
            "line": line,
            "standard_line": pd.to_numeric(work.get("standard_line", line), errors="coerce"),
            "bet_direction": bet_dir,
            "final_bet_direction": bet_dir,
            "edge": edge.round(4),
            "abs_edge": edge.abs().round(4),
            "projection": proj.round(4),
            "composite_hit_rate": composite_hr.round(4),
            "ml_prob": (0.40 + 0.25 * composite_hr).clip(0.38, 0.78).round(4),
            "line_hit_rate": composite_hr.round(4),
            "line_hit_rate_over_ou_5": hr5,
            "line_hit_rate_over_ou_10": hr10,
            "stat_last5_avg": l5,
            "stat_season_avg": seas,
            "last5_over": _num_series(work, "last5_over"),
            "last5_under": _num_series(work, "last5_under"),
            "DEF_TIER": "LEAGUE AVG",
            "OVERALL_DEF_RANK": "N/A",
            "sport": "Golf",
            "pp_projection_id": work.get("projection_id", work.get("pp_projection_id", "")).fillna("").astype(str),
            "pp_game_id": work.get("pp_game_id", "").fillna("").astype(str),
            "course_fit_score": _num_series(work, "course_fit_score"),
            "sg_ott": _num_series(work, "sg_ott"),
            "sg_app": _num_series(work, "sg_app"),
            "sg_arg": _num_series(work, "sg_arg"),
            "weather_signal": work.get("weather_signal", pd.NA),
        }
    )
    out["tier"] = assign_tier_column(out, sport="golf")
    report_goblin_demon_standard_line_fill(out, "[Golf step7]")

    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = root / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        out.to_excel(w, sheet_name="ALL", index=False)

    print(f"[Golf step7] Saved → {out_path}  rows={len(out)}")


if __name__ == "__main__":
    main()
