"""Expected value from model probability and posted price."""

from __future__ import annotations

import math
from typing import Sequence

from proporacle.contracts.bet_contract import PayoutTable
from proporacle.pricing.american import profit_multiple


def ev_per_unit(p_fair: float, american_odds: int) -> float:
    """
    EV per 1 unit **risked** at `american_odds`.

    Win: +b units profit. Lose: -1 unit.
    EV = p_fair * b - (1 - p_fair)
    """
    p = float(p_fair)
    if not (0.0 <= p <= 1.0):
        raise ValueError("p_fair must be in [0,1]")
    b = profit_multiple(american_odds)
    return p * b - (1.0 - p)


def ev_slip_independent(
    p_fair_legs: Sequence[float],
    payout_table: PayoutTable,
    *,
    all_legs_must_hit: bool = True,
) -> float:
    """
    Naive flex EV: independent legs, fixed payout multiple for n legs.

    `leg_count_to_multiplier` = **gross return multiple** per 1 unit stake
    (e.g. 6.0 means you get 6× stake back including stake, i.e. +5 profit).
    Adjust your table to match your book's convention.
    """
    if not p_fair_legs:
        raise ValueError("no legs")
    ps = [float(x) for x in p_fair_legs]
    if any(p < 0 or p > 1 for p in ps):
        raise ValueError("p_fair must be in [0,1] for each leg")

    n = len(ps)
    mult = payout_table.leg_count_to_multiplier.get(n)
    if mult is None:
        raise KeyError(f"no multiplier defined for {n} legs in table {payout_table.name!r}")

    if not all_legs_must_hit:
        raise NotImplementedError("flex partial-hit payout requires explicit payout tree")

    p_all = math.prod(ps)
    # Net profit if win = (mult - 1) * stake; lose = -1 * stake
    profit_if_win = mult - 1.0
    return p_all * profit_if_win - (1.0 - p_all)
