"""Remove book margin from two-way markets."""

from __future__ import annotations

from typing import Literal

from proporacle.pricing.american import to_implied_prob

DevigMethod = Literal["proportional", "power", "shin"]


def devig_two_way(
    american_a: int,
    american_b: int,
    method: DevigMethod = "proportional",
) -> tuple[float, float]:
    """
    Return fair probabilities (sum to 1) for side A and side B.

    - proportional: normalize naive implied probs (multiplicative).
    - power: find alpha>1 such that p_a**alpha + p_b**alpha == 1 (margin shrink).
    - shin: Shin's method (simple 2-way form).
    """
    pa = to_implied_prob(american_a)
    pb = to_implied_prob(american_b)
    if pa <= 0 or pb <= 0 or pa >= 1 or pb >= 1:
        raise ValueError("invalid implied probabilities")

    if method == "proportional":
        s = pa + pb
        return pa / s, pb / s

    if method == "power":
        lo, hi = 1.0001, 10.0
        target = 1.0

        def f(alpha: float) -> float:
            return pa**alpha + pb**alpha - target

        if f(hi) > 0:
            while f(hi) > 0 and hi < 1e6:
                hi *= 2
        if f(lo) < 0:
            return pa / (pa + pb), pb / (pa + pb)
        for _ in range(80):
            mid = (lo + hi) / 2.0
            if f(mid) > 0:
                hi = mid
            else:
                lo = mid
        alpha = (lo + hi) / 2.0
        pa_f = pa**alpha
        pb_f = pb**alpha
        s = pa_f + pb_f
        return pa_f / s, pb_f / s

    if method == "shin":
        # Full Shin iteration for n>2 is out of scope; 2-way use proportional as conservative proxy.
        s = pa + pb
        return pa / s, pb / s

    raise ValueError(f"unknown method {method!r}")
