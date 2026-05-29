from __future__ import annotations

import numpy as np


def _effective_threshold(cat_id: str, threshold: float) -> float:
    if cat_id in ("blk", "stl", "fg3m", "shots", "goals", "assists", "double_faults", "aces"):
        return threshold * 0.55
    return threshold


def classify_edge(
    season_avg: float,
    threshold: float,
    opp_rank: float | None,
    n_teams: int,
    *,
    elite_rank_cut: int = 4,
    hist: dict | None = None,
    cat_id: str = "",
    pp_line: float | None = None,
    pp_edge: float | None = None,
    rank_on_team: int | None = None,
) -> tuple[str, str]:
    hist = hist or {}
    rank = np.nan
    if opp_rank is not None and opp_rank != "":
        try:
            rank = float(opp_rank)
            if isinstance(rank, float) and np.isnan(rank):
                rank = np.nan
        except (TypeError, ValueError):
            rank = np.nan
    weak_cut = max(10, int(np.ceil(n_teams * 0.65)))
    mid_cut = max(7, int(n_teams / 2))
    over_weak = hist.get("overperform_vs_weak", False)
    eff = _effective_threshold(cat_id, threshold)
    rank_lbl = f"#{int(rank)}" if not np.isnan(rank) else "unknown rank"

    if pp_edge is not None and not (isinstance(pp_edge, float) and np.isnan(pp_edge)):
        pe = float(pp_edge)
        if pe >= 2.0:
            rank_note = f" vs {rank_lbl} defense" if not np.isnan(rank) else ""
            return "TOP_EDGE", f"PP edge +{pe:.1f} on board tonight{rank_note}."
        if pe >= 1.5:
            return "TOP_EDGE", f"PP edge +{pe:.1f} on board tonight."
        if pe >= 1.0:
            return "OK_EDGE", f"PP edge +{pe:.1f} on board tonight."
        if pe >= 0.5 and rank_on_team == 1:
            return "OK_EDGE", f"Team leader; PP edge +{pe:.1f}."
        if pe <= -2.0:
            if not np.isnan(rank) and rank <= elite_rank_cut:
                return "AVOID", f"PP edge {pe:.1f} vs elite defense ({rank_lbl})."
            return "AVOID", f"PP edge {pe:.1f} on board — lean UNDER or skip OVER."
        if pe < 0 and not np.isnan(rank) and rank <= elite_rank_cut:
            return "AVOID", "Negative PP edge vs elite defense — lean UNDER or skip OVER."

    if not np.isnan(rank) and rank <= elite_rank_cut and season_avg < eff * 0.9:
        return "AVOID", "Elite defense; production below threshold — lean UNDER or skip OVER."
    if not np.isnan(rank) and rank >= weak_cut and season_avg >= eff:
        note = "Strong avg vs soft defense tier."
        if over_weak:
            note += " Historical weak-D booster."
        return "TOP_EDGE", note
    if over_weak and not np.isnan(rank) and rank >= weak_cut - 2:
        return "TOP_EDGE", "Historically spikes vs weak defenses."
    if rank_on_team == 1 and not np.isnan(rank) and rank >= weak_cut and season_avg >= eff * 0.7:
        return "OK_EDGE", "Team leader vs soft opponent defense."
    if not np.isnan(rank) and rank >= mid_cut and season_avg >= eff * 0.8:
        return "OK_EDGE", "Solid vs average-or-softer opponent defense."
    if not np.isnan(rank) and rank <= elite_rank_cut:
        return "NEUTRAL", "Elite opponent defense — no clear OVER edge on volume."
    return "NEUTRAL", "No strong matchup edge either way."
