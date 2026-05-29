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
    bottom_rank_on_team: int | None = None,
) -> tuple[str, str]:
    """Return (edge_tier, notes) for OVER and UNDER matchup context."""
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
    fades_elite = hist.get("fades_vs_elite", False)
    avg_delta_elite = hist.get("avg_delta_vs_elite")
    eff = _effective_threshold(cat_id, threshold)
    rank_lbl = f"#{int(rank)}" if not np.isnan(rank) else "unknown rank"
    is_elite_opp = not np.isnan(rank) and rank <= elite_rank_cut
    is_weak_opp = not np.isnan(rank) and rank >= weak_cut
    is_bottom3 = bottom_rank_on_team is not None and bottom_rank_on_team <= 3

    def _bottom_note(note: str) -> str:
        if is_bottom3:
            return note + f" Team bottom-{bottom_rank_on_team} in category."
        return note

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
            if is_elite_opp:
                note = f"PP edge {pe:.1f} vs elite defense ({rank_lbl}) — lean UNDER."
                if fades_elite:
                    note = f"Historically fades vs elite D; PP edge {pe:.1f} vs {rank_lbl}."
                return "TOP_UNDER", _bottom_note(note)
            return "OK_UNDER", f"PP edge {pe:.1f} on board — lean UNDER."
        if pe <= -1.0 and is_elite_opp:
            return "OK_UNDER", _bottom_note(f"PP edge {pe:.1f} vs {rank_lbl} defense — lean UNDER.")
        if pe < 0 and is_elite_opp:
            if fades_elite or (is_bottom3 and pe <= -0.5):
                return "TOP_UNDER", _bottom_note(
                    f"Historically fades vs elite D; PP edge {pe:.1f} vs {rank_lbl}."
                    if fades_elite
                    else f"Bottom-{bottom_rank_on_team} producer; PP edge {pe:.1f} vs {rank_lbl}."
                )
            return "OK_UNDER", _bottom_note(f"Negative PP edge vs elite defense ({rank_lbl}) — lean UNDER.")
        if pe < 0 and is_bottom3 and is_elite_opp:
            return "OK_UNDER", _bottom_note(f"PP edge {pe:.1f} vs {rank_lbl} — lean UNDER.")
        if pe < 0:
            return "AVOID", f"PP edge {pe:.1f} on board — skip OVER."

    if is_elite_opp and fades_elite:
        delta_note = ""
        if avg_delta_elite is not None and not (isinstance(avg_delta_elite, float) and np.isnan(avg_delta_elite)):
            delta_note = f" Avg {avg_delta_elite:+.1f} vs elite D."
        return "TOP_UNDER", _bottom_note(f"Historically underperforms vs elite defenses ({rank_lbl}).{delta_note}")

    if is_elite_opp and is_bottom3 and season_avg < eff:
        return "OK_UNDER", _bottom_note("Bottom-3 producer vs elite defense — lean UNDER.")

    if is_elite_opp and season_avg < eff * 0.9:
        return "OK_UNDER", _bottom_note("Elite defense; production below threshold — lean UNDER.")

    if is_weak_opp and season_avg >= eff:
        note = "Strong avg vs soft defense tier."
        if over_weak:
            note += " Historical weak-D booster."
        return "TOP_EDGE", note
    if over_weak and not np.isnan(rank) and rank >= weak_cut - 2:
        return "TOP_EDGE", "Historically spikes vs weak defenses."
    if rank_on_team == 1 and is_weak_opp and season_avg >= eff * 0.7:
        return "OK_EDGE", "Team leader vs soft opponent defense."
    if not np.isnan(rank) and rank >= mid_cut and season_avg >= eff * 0.8:
        return "OK_EDGE", "Solid vs average-or-softer opponent defense."
    if is_elite_opp:
        return "NEUTRAL", "Elite opponent defense — no clear OVER edge on volume."
    return "NEUTRAL", "No strong matchup edge either way."
