"""
Fractional Kelly staking in dollars for a fixed bankroll.

Used by combined_slate_tickets for optional per-leg stake hints on built slips.
"""
from __future__ import annotations


def fractional_kelly(
    edge_pct: float,
    implied_prob: float,
    bankroll: float,
    fraction: float = 0.25,
) -> float:
    """
    Return recommended stake in dollars.

    - No stake unless edge_pct > 3 (percent points).
    - Uses even-money payoff approximation (b = 1): full Kelly f* = 2p - 1.
    - Applies fractional Kelly, then caps at 5% of bankroll.
    """
    try:
        e = float(edge_pct)
        p = float(implied_prob)
        br = float(bankroll)
        fr = float(fraction)
    except (TypeError, ValueError):
        return 0.0
    if br <= 0 or fr <= 0:
        return 0.0
    if e <= 3.0:
        return 0.0
    p = min(max(p, 1e-6), 1.0 - 1e-6)
    b = 1.0  # net profit per unit stake at even-money
    q = 1.0 - p
    k_full = (p * b - q) / b
    k = max(0.0, k_full * fr)
    stake_frac = min(k, 0.05)
    return round(br * stake_frac, 2)


def leg_edge_pct_for_kelly(ml_prob: object, points_edge: object | None = None) -> float:
    """
    Derive a percentage edge for gating Kelly when only model prob + optional points edge exist.

    Primary signal: distance of ml_prob from 50% in percentage points.
    If points_edge looks like a small ratio (|x|<=1), treat as fractional edge and scale.
    """
    try:
        p = float(ml_prob)
    except (TypeError, ValueError):
        return 0.0
    edge = abs(p - 0.5) * 100.0
    if points_edge is not None:
        try:
            pe = float(points_edge)
            if abs(pe) <= 1.0:
                edge = max(edge, abs(pe) * 100.0)
        except (TypeError, ValueError):
            pass
    return float(edge)
