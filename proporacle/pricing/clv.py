"""Closing line value helpers (implied-prob and model-centric forms)."""

from __future__ import annotations

from proporacle.pricing.american import to_implied_prob


def compute_clv_implied_delta(american_open: int, american_close: int) -> float:
    """Δ(implied close - implied open) on the same side; positive = line moved your way."""
    o0 = to_implied_prob(american_open)
    o1 = to_implied_prob(american_close)
    return round(o1 - o0, 6)


def compute_clv_vs_model(
    p_fair_at_bet: float,
    american_open: int,
    american_close: int,
) -> tuple[float, float]:
    """
    Returns (delta_implied, mean_implied_vs_p_fair).

    `mean_implied_vs_p_fair` = average(implied_open, implied_close) - p_fair
    (useful when comparing model to market path).
    """
    p0 = to_implied_prob(american_open)
    p1 = to_implied_prob(american_close)
    delta = p1 - p0
    mid_implied = 0.5 * (p0 + p1)
    return delta, round(mid_implied - float(p_fair_at_bet), 6)
