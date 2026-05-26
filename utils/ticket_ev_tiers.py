"""
Percentile-calibrated ticket EV tiers (STRONG / OK / MARGINAL / SKIP).

Fixed cuts (1.0 / 1.15 / 1.40 on payout.ev) collapse almost all slips into SKIP on
typical slates. Thresholds are computed per payload from the empirical payout.ev
distribution, with small absolute floors so tiers stay meaningful on thin slates.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, MutableMapping, Sequence

import numpy as np

# Percentile ranks for tier floors (higher EV → higher tier).
TIER_EV_PERCENTILES: dict[str, float] = {
    "strong": 85.0,
    "ok": 60.0,
    "marginal": 35.0,
}

# Legacy absolute cuts when too few tickets have payout.ev.
LEGACY_TIER_EV_THRESHOLDS: dict[str, float] = {
    "strong": 1.40,
    "ok": 1.15,
    "marginal": 1.0,
}

# Minimum EV floors after percentile calibration (per $1 stake style).
TIER_EV_MIN_STRONG: float = 0.50
TIER_EV_MIN_OK: float = 0.20
TIER_EV_MIN_MARGINAL: float = 0.0

MIN_SAMPLES_FOR_PERCENTILES: int = 8


def _finite_evs(evs: Sequence[float]) -> list[float]:
    out: list[float] = []
    for raw in evs:
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            out.append(v)
    return out


def compute_ev_tier_thresholds(
    evs: Sequence[float],
    *,
    percentiles: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """
    Return strong/ok/marginal cutoffs from the EV sample.

    Falls back to LEGACY_TIER_EV_THRESHOLDS when len(evs) < MIN_SAMPLES_FOR_PERCENTILES.
    """
    pct_map = dict(TIER_EV_PERCENTILES)
    if percentiles:
        pct_map.update({str(k): float(v) for k, v in percentiles.items()})

    clean = _finite_evs(evs)
    if len(clean) < MIN_SAMPLES_FOR_PERCENTILES:
        return dict(LEGACY_TIER_EV_THRESHOLDS)

    arr = np.array(clean, dtype=float)
    return {
        "strong": float(np.percentile(arr, pct_map["strong"])),
        "ok": float(np.percentile(arr, pct_map["ok"])),
        "marginal": float(np.percentile(arr, pct_map["marginal"])),
    }


def recommendation_from_ev(
    ev: float,
    thresholds: Mapping[str, float] | None = None,
) -> str:
    """Map one payout.ev to STRONG / OK / MARGINAL / SKIP."""
    try:
        v = float(ev)
    except (TypeError, ValueError):
        return "SKIP"
    if not math.isfinite(v) or v < TIER_EV_MIN_MARGINAL:
        return "SKIP"

    th = dict(LEGACY_TIER_EV_THRESHOLDS)
    if thresholds:
        th.update({str(k): float(x) for k, x in thresholds.items()})

    strong_min = max(float(th["strong"]), TIER_EV_MIN_STRONG)
    ok_min = max(float(th["ok"]), TIER_EV_MIN_OK)
    marginal_min = max(float(th["marginal"]), TIER_EV_MIN_MARGINAL)

    if v >= strong_min:
        return "STRONG"
    if v >= ok_min:
        return "OK"
    if v >= marginal_min:
        return "MARGINAL"
    return "SKIP"


def _iter_tickets(payload: Mapping[str, Any]):
    for g in payload.get("groups") or []:
        for t in g.get("tickets") or []:
            if isinstance(t, dict):
                yield t


def collect_payload_payout_evs(payload: Mapping[str, Any]) -> list[float]:
    evs: list[float] = []
    for t in _iter_tickets(payload):
        pay = t.get("payout")
        if not isinstance(pay, dict):
            continue
        raw = pay.get("ev")
        if raw is None:
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            evs.append(v)
    return evs


def apply_slate_ev_tier_recommendations(
    payload: MutableMapping[str, Any],
    *,
    percentiles: Mapping[str, float] | None = None,
    log: bool = True,
) -> dict[str, float]:
    """
    Recompute payout.recommendation (and empirical_recommendation) for all tickets
    in a slate payload using percentile thresholds. Stores cuts on the payload.
    """
    evs = collect_payload_payout_evs(payload)
    thresholds = compute_ev_tier_thresholds(evs, percentiles=percentiles)

    counts = {"STRONG": 0, "OK": 0, "MARGINAL": 0, "SKIP": 0}
    for t in _iter_tickets(payload):
        pay = t.get("payout")
        if not isinstance(pay, dict):
            continue
        raw = pay.get("ev")
        if raw is None:
            continue
        try:
            ev = float(raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(ev):
            continue
        rec = recommendation_from_ev(ev, thresholds)
        pay["recommendation"] = rec
        t["empirical_recommendation"] = rec
        counts[rec] = counts.get(rec, 0) + 1

    payload["tier_ev_thresholds"] = thresholds
    payload["tier_ev_percentiles"] = dict(TIER_EV_PERCENTILES)
    if percentiles:
        payload["tier_ev_percentiles"] = {**payload["tier_ev_percentiles"], **dict(percentiles)}

    if log:
        n = len(evs)
        print(
            f"[tier-ev] n={n} cuts: strong>={thresholds['strong']:.3f} "
            f"ok>={thresholds['ok']:.3f} marginal>={thresholds['marginal']:.3f} "
            f"| STRONG={counts['STRONG']} OK={counts['OK']} "
            f"MARGINAL={counts['MARGINAL']} SKIP={counts['SKIP']}"
        )
    return thresholds


def tier_distribution_summary(payload: Mapping[str, Any]) -> dict[str, int]:
    counts = {"STRONG": 0, "OK": 0, "MARGINAL": 0, "SKIP": 0}
    for t in _iter_tickets(payload):
        pay = t.get("payout") if isinstance(t.get("payout"), dict) else {}
        rec = str(pay.get("recommendation") or t.get("empirical_recommendation") or "SKIP").strip().upper()
        counts[rec if rec in counts else "SKIP"] = counts.get(rec if rec in counts else "SKIP", 0) + 1
    return counts
