"""
PrizePicks-style Goblin/Demon per-leg factors and ticket multipliers.

Loads optional tuning parameters from data/payout_curve_params.json.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

PARAMS_PATH = Path(__file__).resolve().parent.parent / "data" / "payout_curve_params.json"

BASE_POWER = {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0, 6: 37.5}
BASE_FLEX = {
    2: {2: 3.0},
    3: {3: 3.0, 2: 1.0},
    4: {4: 6.0, 3: 1.5},
    5: {5: 10.0, 4: 2.0, 3: 0.4},
    6: {6: 25.0, 5: 2.0, 4: 0.4},
}

_DEFAULT_PARAMS: dict[str, Any] = {
    "G_EXP": 1.0,
    "D_EXP": 1.5,
    "D_SCALE": 3.0,
    "observations_count": 0,
    "last_updated": None,
}


def load_params() -> dict[str, Any]:
    """Load curve params from JSON, fall back to defaults if missing."""
    p = dict(_DEFAULT_PARAMS)
    try:
        if PARAMS_PATH.is_file():
            raw = json.loads(PARAMS_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k in ("G_EXP", "D_EXP", "D_SCALE") and v is not None:
                        p[k] = float(v)
                    elif k == "observations_count" and v is not None:
                        p[k] = int(v)
                    elif k == "last_updated":
                        p[k] = v
    except Exception as e:
        logger.warning("Could not load %s (%s); using defaults", PARAMS_PATH, e)
    return p


def goblin_factor(delta_pct: float, g_exp: float) -> float:
    """Per-leg multiplier factor for a Goblin leg (0 < delta_pct < 1 typically)."""
    try:
        d = float(delta_pct)
    except (TypeError, ValueError):
        return 1.0
    if d <= 0:
        return 0.35
    return max(0.35, min(1.0, d**float(g_exp)))


def demon_factor(delta_pct: float, d_exp: float, d_scale: float) -> float:
    """Per-leg multiplier factor for a Demon leg (delta_pct > 1 typically)."""
    try:
        d = float(delta_pct)
    except (TypeError, ValueError):
        return 1.0
    excess = max(0.0, d - 1.0)
    return 1.0 + (excess ** float(d_exp)) * float(d_scale)


def leg_factor(delta_pct: Optional[float], pick_type: str, params: Optional[dict[str, Any]] = None) -> float:
    """
    Returns per-leg factor. Standard = 1.0.
    pick_type: 'Goblin', 'Demon', 'Standard' (case-insensitive).
    delta_pct: played_line / standard_line (None = treat as Standard factor 1.0).
    """
    p = params if params is not None else load_params()
    g_exp = float(p.get("G_EXP", 1.0))
    d_exp = float(p.get("D_EXP", 1.5))
    d_scale = float(p.get("D_SCALE", 3.0))

    pt_raw = (pick_type or "").strip().lower()
    if pt_raw == "standard" or delta_pct is None:
        if "goblin" in pt_raw or "demon" in pt_raw:
            if delta_pct is None:
                logger.warning(
                    "leg_factor: %s leg missing delta_pct (no standard_line); using factor 1.0",
                    pick_type,
                )
        return 1.0

    try:
        d = float(delta_pct)
    except (TypeError, ValueError):
        if "goblin" in pt_raw or "demon" in pt_raw:
            logger.warning(
                "leg_factor: invalid delta_pct for %s leg (%r); using 1.0",
                pick_type,
                delta_pct,
            )
        return 1.0

    if "goblin" in pt_raw:
        return goblin_factor(d, g_exp)
    if "demon" in pt_raw:
        return demon_factor(d, d_exp, d_scale)
    return 1.0


def ticket_multiplier(
    n_legs: int,
    leg_factors: list[float],
    mode: str = "power",
    hits: Optional[int] = None,
) -> float:
    """
    Final ticket multiplier = base_mult * product(leg_factors).
    mode: 'power' or 'flex'. hits: required for flex (number of legs that hit).
    """
    n = int(n_legs)
    combined_factor = 1.0
    for f in leg_factors:
        try:
            combined_factor *= float(f)
        except (TypeError, ValueError):
            combined_factor *= 1.0

    mode_l = (mode or "power").strip().lower()
    if mode_l == "power":
        base = float(BASE_POWER.get(n, 1.0))
        return round(base * combined_factor, 4)

    if mode_l == "flex":
        flex_table = BASE_FLEX.get(n, {})
        h = int(hits) if hits is not None else n
        base = float(flex_table.get(h, 0.0))
        return round(base * combined_factor, 4)

    return 0.0


def compute_ticket_ev(multiplier: float, combined_prob: float, stake: float) -> float:
    """EV = (prob * multiplier * stake) - ((1 - prob) * stake)"""
    try:
        m = float(multiplier)
        pr = float(combined_prob)
        s = float(stake)
    except (TypeError, ValueError):
        return float("nan")
    return (pr * m * s) - ((1.0 - pr) * s)


def leg_delta_pct(played_line: Any, standard_line: Any) -> Optional[float]:
    """played_line / standard_line, or None if not computable."""
    try:
        s = float(standard_line)
        l = float(played_line)
    except (TypeError, ValueError):
        return None
    if s == 0 or not math.isfinite(s) or not math.isfinite(l):
        return None
    return l / s


def leg_payout_method(delta_pct: Any, pick_type: str) -> str:
    """
    How this leg's factor was chosen for grading / exports.

    - ``curve``: Standard legs, or Goblin/Demon with a usable delta_pct (curve factors apply).
    - ``flat_fallback``: Goblin/Demon with missing/invalid delta_pct; factor forced to 1.0.
    """
    pt_raw = (pick_type or "").strip().lower()
    if "goblin" not in pt_raw and "demon" not in pt_raw:
        return "curve"
    if delta_pct is None:
        return "flat_fallback"
    try:
        d = float(delta_pct)
    except (TypeError, ValueError):
        return "flat_fallback"
    if not math.isfinite(d):
        return "flat_fallback"
    return "curve"


def multiplier_summary(
    legs: list[dict[str, Any]],
    mode: str = "power",
    hits: Optional[int] = None,
    stake: Optional[float] = None,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    legs: dicts with pick_type, optional delta_pct (or standard_line + line).
    Returns base_mult, est_mult, combined_factor, per_leg_factors, delta_pcts,
    combined_prob (if probs present), est_ev / flat_ev when stake set.
    """
    p = params if params is not None else load_params()
    n = len(legs)
    factors: list[float] = []
    delta_pcts: list[Optional[float]] = []
    probs: list[float] = []

    for leg in legs:
        dp = leg.get("delta_pct")
        if dp is None and (leg.get("standard_line") is not None or leg.get("line") is not None):
            dp = leg_delta_pct(leg.get("line"), leg.get("standard_line"))
        try:
            dp_f = float(dp) if dp is not None else None
        except (TypeError, ValueError):
            dp_f = None
        delta_pcts.append(dp_f)

        pt = str(leg.get("pick_type") or "Standard")
        factors.append(leg_factor(dp_f, pt, p))

        pr = leg.get("prob")
        if pr is None:
            pr = leg.get("ml_prob")
        try:
            pf = float(pr)
            if 0.0 < pf < 1.0:
                probs.append(pf)
        except (TypeError, ValueError):
            pass

    mode_l = (mode or "power").strip().lower()
    if mode_l == "power":
        base = float(BASE_POWER.get(n, 1.0))
    else:
        h = int(hits) if hits is not None else n
        base = float(BASE_FLEX.get(n, {}).get(h, 0.0))

    combined_factor = 1.0
    for f in factors:
        combined_factor *= f
    est_mult = round(base * combined_factor, 4)

    combined_prob: Optional[float] = None
    if len(probs) == n and n > 0:
        x = 1.0
        for q in probs:
            x *= q
        combined_prob = x

    out: dict[str, Any] = {
        "n_legs": n,
        "mode": mode_l,
        "hits": hits,
        "base_mult": base,
        "combined_factor": round(combined_factor, 6),
        "per_leg_factors": [round(x, 4) for x in factors],
        "delta_pcts": delta_pcts,
        "est_mult": est_mult,
        "flat_mult": base,
        "mult_delta": round(est_mult - base, 4),
    }

    if combined_prob is not None:
        out["combined_prob"] = round(combined_prob, 6)
    if stake is not None and combined_prob is not None:
        try:
            s = float(stake)
            out["est_ev"] = round(compute_ticket_ev(est_mult, combined_prob, s), 4)
            out["flat_ev"] = round(compute_ticket_ev(base, combined_prob, s), 4)
        except (TypeError, ValueError):
            pass

    return out


def synthetic_legs_for_combo(combo_id: str, n_legs: int) -> list[dict[str, Any]]:
    """Build leg list for reference combo labels (all Standard default line)."""
    n = max(2, min(6, int(n_legs)))
    legs: list[dict[str, Any]] = [{"pick_type": "Standard", "delta_pct": 1.0} for _ in range(n)]
    cid = (combo_id or "all_standard").lower()

    def _set_goblin(idx: int, d: float) -> None:
        if 0 <= idx < len(legs):
            legs[idx] = {"pick_type": "Goblin", "delta_pct": d}

    def _set_demon(idx: int, d: float) -> None:
        if 0 <= idx < len(legs):
            legs[idx] = {"pick_type": "Demon", "delta_pct": d}

    if cid == "all_standard":
        pass
    elif cid == "1_goblin_90":
        _set_goblin(0, 0.90)
    elif cid == "1_goblin_80":
        _set_goblin(0, 0.80)
    elif cid == "1_goblin_70":
        _set_goblin(0, 0.70)
    elif cid == "1_goblin_60":
        _set_goblin(0, 0.60)
    elif cid == "all_goblins_80":
        legs = [{"pick_type": "Goblin", "delta_pct": 0.80} for _ in range(n)]
    elif cid == "all_goblins_70":
        legs = [{"pick_type": "Goblin", "delta_pct": 0.70} for _ in range(n)]
    elif cid == "all_goblins_60":
        legs = [{"pick_type": "Goblin", "delta_pct": 0.60} for _ in range(n)]
    elif cid == "1_demon_110":
        _set_demon(0, 1.10)
    elif cid == "1_demon_125":
        _set_demon(0, 1.25)
    elif cid == "1_demon_140":
        _set_demon(0, 1.40)
    elif cid == "mixed_gob70_dem120":
        _set_goblin(0, 0.70)
        if n > 1:
            _set_demon(1, 1.20)
    return legs
