from __future__ import annotations

from collections import Counter
import logging
from typing import Any

_log = logging.getLogger("ticket_diversity")


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in {"1", "true", "yes", "y", "on"}


def _player_token(leg: dict[str, Any]) -> str:
    return str(leg.get("player", "")).strip().lower()


def _prop_token(leg: dict[str, Any]) -> str:
    return str(leg.get("prop_type", leg.get("prop", ""))).strip().lower()


def _line_token(leg: dict[str, Any]) -> str:
    """Stable line key so 2.5, 2.50, and float 2.5 match for exposure counting."""
    for k in ("line", "played_line", "std_line", "standard_line"):
        val = leg.get(k)
        if val is None:
            continue
        try:
            f = float(val)
            if isinstance(f, float) and f != f:  # NaN
                continue
            if abs(f - round(f)) < 1e-9:
                return str(int(round(f)))
            return f"{f:.6g}"
        except (TypeError, ValueError):
            s = str(val).strip().lower()
            if s:
                return s
    return ""


def _direction_token(leg: dict[str, Any]) -> str:
    d = str(leg.get("direction") or leg.get("bet_direction") or "").strip().upper()
    if "UNDER" in d:
        return "under"
    if "OVER" in d:
        return "over"
    return d.lower()


def _leg_token(leg: dict[str, Any]) -> str:
    return f"{_player_token(leg)}|{_prop_token(leg)}|{_line_token(leg)}|{_direction_token(leg)}"


def _leg_sample_size(leg: dict[str, Any]) -> int:
    for k in ("sample_size", "l5_sample", "history_sample", "n_samples"):
        v = leg.get(k)
        if v is None:
            continue
        i = _as_int(v, default=-1)
        if i >= 0:
            return i
    return -1


def _leg_is_void_risk(leg: dict[str, Any], min_sample: int) -> bool:
    ss = _leg_sample_size(leg)
    if ss >= 0 and ss < int(min_sample):
        return True
    if _bool(leg.get("void_risk")):
        return True
    if _bool(leg.get("no_history")):
        return True
    edge_val = _as_float(leg.get("edge"), default=999.0)
    if abs(edge_val - 0.5) < 1e-9:
        return True
    return False


def _ticket_base_ev(ticket: dict[str, Any]) -> float:
    for k in ("base_ev", "ev_power", "est_ev", "ticket_objective_score", "edge_score"):
        if k in ticket and ticket.get(k) not in (None, ""):
            return _as_float(ticket.get(k), default=0.0)
    rows = ticket.get("rows") or ticket.get("legs") or []
    if isinstance(rows, list) and rows:
        edges = [_as_float(r.get("edge"), default=0.0) for r in rows if isinstance(r, dict)]
        if edges:
            return float(sum(edges) / max(1, len(edges)))
        return float(len(rows))
    return 0.0


def apply_diversity_filter(candidate_tickets: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Post-process candidate tickets with diversity / overlap constraints.

    Returns only accepted tickets; it does not mutate input ticket dicts.
    """
    cfg = dict(config or {})
    if not _bool(cfg.get("enabled", True)):
        _log.info("[diversity] disabled via config; returning %d candidate(s)", len(candidate_tickets or []))
        return list(candidate_tickets or [])

    max_leg_exposure = max(1, _as_int(cfg.get("max_leg_exposure"), 2))
    max_player_exposure = max(1, _as_int(cfg.get("max_player_exposure"), 3))
    void_risk_min_sample = max(0, _as_int(cfg.get("void_risk_min_sample"), 10))
    max_jaccard_overlap = max(0.0, min(1.0, _as_float(cfg.get("max_jaccard_overlap"), 0.5)))
    exposure_w = _as_float(cfg.get("exposure_penalty_weight"), 0.1)
    overlap_w = _as_float(cfg.get("overlap_penalty_weight"), 0.2)
    void_w = _as_float(cfg.get("void_penalty_weight"), 0.5)

    prepared: list[tuple[dict[str, Any], float, set[str], set[str]]] = []
    reasons = Counter()
    n_in = len(candidate_tickets or [])

    for t in (candidate_tickets or []):
        rows = t.get("rows") or t.get("legs") or []
        if not isinstance(rows, list) or not rows:
            reasons["void_filter"] += 1
            continue
        clean_rows: list[dict[str, Any]] = []
        void_count = 0
        for leg in rows:
            if not isinstance(leg, dict):
                continue
            if _leg_is_void_risk(leg, void_risk_min_sample):
                void_count += 1
                continue
            clean_rows.append(leg)

        original_len = int(t.get("n_legs") or len(rows) or 0)
        min_len = max(2, original_len)
        if len(clean_rows) < min_len:
            reasons["void_filter"] += 1
            continue

        # Initial adjusted score (for sorting only).
        leg_counts = Counter(_leg_token(leg) for leg in clean_rows)
        player_counts = Counter(_player_token(leg) for leg in clean_rows if _player_token(leg))
        dup_legs = sum(max(0, c - 1) for c in leg_counts.values())
        dup_players = sum(max(0, c - 1) for c in player_counts.values())
        exposure_penalty = exposure_w * float(dup_legs + dup_players)
        overlap_penalty = overlap_w * 0.0
        void_penalty = void_w * float(void_count)
        adjusted = _ticket_base_ev(t) - exposure_penalty - overlap_penalty - void_penalty

        leg_set = set(leg_counts.keys())
        player_set = set(player_counts.keys())
        prepared.append((t, adjusted, leg_set, player_set))

    prepared.sort(key=lambda x: x[1], reverse=True)

    accepted: list[dict[str, Any]] = []
    accepted_leg_sets: list[set[str]] = []
    leg_exposure = Counter()
    player_exposure = Counter()

    for ticket, _score, leg_set, player_set in prepared:
        if any(leg_exposure[k] >= max_leg_exposure for k in leg_set):
            reasons["exposure_cap"] += 1
            reasons["exposure_leg_cap"] += 1
            continue
        if any(player_exposure[p] >= max_player_exposure for p in player_set):
            reasons["exposure_cap"] += 1
            reasons["exposure_player_cap"] += 1
            continue

        overlap_hit = False
        for prev in accepted_leg_sets:
            union_n = len(prev | leg_set)
            if union_n <= 0:
                continue
            jac = len(prev & leg_set) / float(union_n)
            if jac >= max_jaccard_overlap:
                overlap_hit = True
                break
        if overlap_hit:
            reasons["overlap"] += 1
            continue

        accepted.append(ticket)
        accepted_leg_sets.append(leg_set)
        for k in leg_set:
            leg_exposure[k] += 1
        for p in player_set:
            player_exposure[p] += 1

    _log.info(
        "[diversity] candidates=%d accepted=%d dropped=%d",
        n_in,
        len(accepted),
        max(0, n_in - len(accepted)),
    )
    _log.info(
        "[diversity] dropped: void_filter=%d exposure_cap=%d overlap=%d",
        int(reasons.get("void_filter", 0)),
        int(reasons.get("exposure_cap", 0)),
        int(reasons.get("overlap", 0)),
    )
    return accepted
