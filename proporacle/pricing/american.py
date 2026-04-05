"""American odds ↔ implied probability and profit multiple (per 1 unit risk)."""

from __future__ import annotations


def to_implied_prob(american: int) -> float:
    """Vig-naive implied probability from American odds (0,1)."""
    a = int(american)
    if a == 0:
        raise ValueError("american odds cannot be 0")
    if a > 0:
        return 100.0 / (a + 100.0)
    return float(-a) / float(-a + 100)


def to_decimal_odds(american: int) -> float:
    """Decimal odds (stake included in return) for a winning bet."""
    a = int(american)
    if a == 0:
        raise ValueError("american odds cannot be 0")
    if a > 0:
        return 1.0 + a / 100.0
    return 1.0 + 100.0 / float(-a)


def profit_multiple(american: int) -> float:
    """Net profit per 1 unit staked if the bet wins (American convention)."""
    a = int(american)
    if a == 0:
        raise ValueError("american odds cannot be 0")
    if a > 0:
        return a / 100.0
    return 100.0 / float(-a)
