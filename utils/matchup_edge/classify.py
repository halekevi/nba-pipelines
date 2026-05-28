from __future__ import annotations

import numpy as np


def classify_edge(
    season_avg: float,
    threshold: float,
    opp_rank: float | None,
    n_teams: int,
    *,
    elite_rank_cut: int = 4,
    hist: dict | None = None,
) -> tuple[str, str]:
    hist = hist or {}
    rank = float(opp_rank) if opp_rank is not None and not (isinstance(opp_rank, float) and np.isnan(opp_rank)) else np.nan
    weak_cut = max(10, int(np.ceil(n_teams * 0.65)))

    if not np.isnan(rank) and rank <= elite_rank_cut and season_avg < threshold * 0.9:
        return "AVOID", "Elite defense; production below threshold — lean UNDER or skip OVER."
    if not np.isnan(rank) and rank >= weak_cut and season_avg >= threshold:
        note = "Strong avg vs soft defense tier."
        if hist.get("overperform_vs_weak"):
            note += " Historical weak-D booster."
        return "TOP_EDGE", note
    if hist.get("overperform_vs_weak") and not np.isnan(rank) and rank >= weak_cut - 2:
        return "TOP_EDGE", "Historically spikes vs weak defenses."
    if not np.isnan(rank) and rank >= int(n_teams / 2) and season_avg >= threshold * 0.85:
        return "OK_EDGE", "Solid vs average-or-softer opponent defense."
    if not np.isnan(rank) and rank <= elite_rank_cut:
        return "NEUTRAL", "Elite opponent defense — no clear OVER edge on volume."
    return "NEUTRAL", "No strong matchup edge either way."
