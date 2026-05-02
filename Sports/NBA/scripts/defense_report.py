#!/usr/bin/env python3
"""
defense_report.py

Pull ALL NBA team defense + pace context (current season) from nba_api and
output:
  - defense_team_summary.csv (all 30 teams)
  - printed leaderboards (overall defense, 2P points allowed, 3P makes allowed,
    total points allowed, slowest/fastest pace)

Usage:
  py -3.14 defense_report.py --season 2025-26 --out defense_team_summary.csv --top 10

Notes:
- nba_api tables sometimes omit TEAM_ABBREVIATION; we derive it from TEAM_ID using static teams.
- "Allowed" metrics come from measure_type_detailed_defense="Opponent".
- "Overall defense" is a composite rank built from several allowed ranks (Option B style).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from utils.defense_tiers import def_tier_from_overall_rank

from nba_api.stats.endpoints import leaguedashteamstats
from nba_api.stats.static import teams as static_teams


TEAM_ALIAS_FIX = {
    "BRK": "BKN",
    "GS": "GSW",
    "NO": "NOP",
    "NOR": "NOP",
    "NY": "NYK",
    "SA": "SAS",
}


def norm_team_abbr(x: Any) -> str:
    if x is None:
        return "UNK"
    s = str(x).strip().upper()
    if s in ("", "NAN", "NONE", "NULL"):
        return "UNK"
    return TEAM_ALIAS_FIX.get(s, s)


def safe_num(df: pd.DataFrame, col: str) -> None:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")


def rank_series(s: pd.Series, ascending: bool) -> pd.Series:
    return s.rank(method="min", ascending=ascending).astype("Int64")


def ensure_team_abbr(df: pd.DataFrame) -> pd.DataFrame:
    """
    nba_api sometimes returns TEAM_ABBREVIATION, sometimes only TEAM_ID/TEAM_NAME.
    This function guarantees TEAM_ABBREVIATION exists by mapping TEAM_ID -> abbr.
    """
    d = df.copy()

    if "TEAM_ABBREVIATION" in d.columns:
        d["TEAM_ABBREVIATION"] = d["TEAM_ABBREVIATION"].apply(norm_team_abbr)
        return d

    if "TEAM_ID" in d.columns:
        id_to_abbr = {t["id"]: norm_team_abbr(t["abbreviation"]) for t in static_teams.get_teams()}
        d["TEAM_ABBREVIATION"] = (
            pd.to_numeric(d["TEAM_ID"], errors="coerce")
            .map(id_to_abbr)
            .fillna("UNK")
        )
        return d

    raise RuntimeError(f"Cannot derive TEAM_ABBREVIATION. Columns present: {list(d.columns)}")


def pull_base_and_opponent(season: str, timeout: int) -> pd.DataFrame:
    """
    Base: DEF_RATING, PACE
    Opponent: OPP_* allowed metrics (OPP_PTS, OPP_FG3M, etc.)
    Returns merged per-team table keyed by TEAM_ABBREVIATION.
    """
    base = leaguedashteamstats.LeagueDashTeamStats(
        season=season,
        season_type_all_star="Regular Season",
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Base",
        timeout=timeout,
    ).get_data_frames()[0]

    opp = leaguedashteamstats.LeagueDashTeamStats(
        season=season,
        season_type_all_star="Regular Season",
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Opponent",
        timeout=timeout,
    ).get_data_frames()[0]

    if base is None or base.empty:
        raise RuntimeError("Base table returned empty from nba_api.")
    if opp is None or opp.empty:
        raise RuntimeError("Opponent table returned empty from nba_api.")

    base = ensure_team_abbr(base)
    opp = ensure_team_abbr(opp)

    # Keep only needed columns (and only ones that exist)
    base_keep = [c for c in ["TEAM_ABBREVIATION", "DEF_RATING", "PACE"] if c in base.columns]
    opp_keep = [c for c in [
        "TEAM_ABBREVIATION",
        "OPP_PTS", "OPP_FGA", "OPP_FGM", "OPP_FG3A", "OPP_FG3M", "OPP_FTA", "OPP_TOV"
    ] if c in opp.columns]

    out = base[base_keep].merge(opp[opp_keep], on="TEAM_ABBREVIATION", how="left")

    # Numeric coercion
    for c in [x for x in (base_keep + opp_keep) if x != "TEAM_ABBREVIATION"]:
        safe_num(out, c)

    # Derive 2PA/2PM/2PTS allowed
    if "OPP_FGA" in out.columns and "OPP_FG3A" in out.columns:
        out["OPP_2PA"] = (out["OPP_FGA"] - out["OPP_FG3A"]).clip(lower=0)
    else:
        out["OPP_2PA"] = np.nan

    if "OPP_FGM" in out.columns and "OPP_FG3M" in out.columns:
        out["OPP_2PM"] = (out["OPP_FGM"] - out["OPP_FG3M"]).clip(lower=0)
    else:
        out["OPP_2PM"] = np.nan

    out["OPP_2PTS"] = pd.to_numeric(out["OPP_2PM"], errors="coerce") * 2

    return out


def add_ranks_and_tiers(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Overall defense: lower DEF_RATING is better
    if "DEF_RATING" in df.columns:
        df["DEF_RANK"] = rank_series(df["DEF_RATING"], ascending=True)

    # Pace: provide both slow and fast ranks
    if "PACE" in df.columns:
        df["PACE_FAST_RANK"] = rank_series(df["PACE"], ascending=False)  # 1 = fastest
        df["PACE_SLOW_RANK"] = rank_series(df["PACE"], ascending=True)   # 1 = slowest

    # Allowed metrics: lower is better defense
    allowed = ["OPP_PTS", "OPP_FG3M", "OPP_FG3A", "OPP_FGA", "OPP_FGM", "OPP_2PA", "OPP_2PM", "OPP_2PTS", "OPP_FTA"]
    for c in allowed:
        if c in df.columns:
            df[f"{c}_RANK"] = rank_series(df[c], ascending=True)

    # Turnovers forced: higher OPP_TOV means they force more TOs
    if "OPP_TOV" in df.columns:
        df["OPP_TOV_FORCED_RANK"] = rank_series(df["OPP_TOV"], ascending=False)  # 1 = most TO forced

    # Composite overall defense score (Option B style using ranks)
    rank_cols = [c for c in [
        "OPP_PTS_RANK",
        "OPP_FGM_RANK",
        "OPP_FGA_RANK",
        "OPP_2PTS_RANK",
        "OPP_FG3M_RANK",
        "OPP_FTA_RANK",
    ] if c in df.columns]

    if rank_cols:
        weights = {
            "OPP_PTS_RANK": 3.0,
            "OPP_FGM_RANK": 2.0,
            "OPP_FGA_RANK": 1.0,
            "OPP_2PTS_RANK": 1.5,
            "OPP_FG3M_RANK": 1.0,
            "OPP_FTA_RANK": 0.8,
        }
        w = np.array([weights.get(c, 1.0) for c in rank_cols], dtype=float)
        w = w / w.sum()

        df["OVERALL_DEF_SCORE"] = df[rank_cols].mul(w, axis=1).sum(axis=1)
        df["OVERALL_DEF_RANK"] = rank_series(df["OVERALL_DEF_SCORE"], ascending=True)
    else:
        df["OVERALL_DEF_SCORE"] = np.nan
        df["OVERALL_DEF_RANK"] = pd.NA

    _n_teams = len(df)

    def tier_from_rank(r) -> str:
        return def_tier_from_overall_rank(r, _n_teams)

    df["DEF_TIER"] = df["OVERALL_DEF_RANK"].apply(tier_from_rank)

    return df


def print_leaders(df: pd.DataFrame, topn: int) -> None:
    def show(title: str, col: str, asc: bool = True):
        if col not in df.columns:
            print(f"\n{title}: (missing {col})")
            return
        t = df.sort_values(col, ascending=asc).head(topn)[["TEAM_ABBREVIATION", col]]
        print(f"\n{title}")
        print(t.to_string(index=False))

    show("BEST OVERALL DEFENSE (composite) — OVERALL_DEF_RANK", "OVERALL_DEF_RANK", asc=True)
    show("LEAST POINTS ALLOWED — OPP_PTS_RANK", "OPP_PTS_RANK", asc=True)
    show("LEAST 2P POINTS ALLOWED — OPP_2PTS_RANK", "OPP_2PTS_RANK", asc=True)
    show("LEAST 3P MAKES ALLOWED — OPP_FG3M_RANK", "OPP_FG3M_RANK", asc=True)
    show("SLOWEST PACE — PACE_SLOW_RANK", "PACE_SLOW_RANK", asc=True)
    show("FASTEST PACE — PACE_FAST_RANK", "PACE_FAST_RANK", asc=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025-26")
    ap.add_argument("--out", default="defense_team_summary.csv")
    ap.add_argument("--timeout", type=int, default=45)
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    df = pull_base_and_opponent(season=args.season, timeout=args.timeout)
    df = add_ranks_and_tiers(df)

    # Save output (handles OneDrive/Excel locks by timestamping)
    out_path = args.out
    try:
        df.to_csv(out_path, index=False)
    except PermissionError:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_path.replace(".csv", f"_{stamp}.csv")
        df.to_csv(out_path, index=False)

    print_leaders(df, topn=args.top)
    print(f"\nSaved → {out_path}")
    print(f"Teams found: {df['TEAM_ABBREVIATION'].nunique()}")


if __name__ == "__main__":
    main()
