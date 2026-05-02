"""
Map overall defensive rank (1 = best / stingiest) to five display tiers using quintiles.

Labels match dashboards and grading HTML: Elite, Above Avg, Avg, Below Avg, Weak.
"""

from __future__ import annotations

import math
import pandas as pd

DEF_TIER_LABELS: tuple[str, ...] = ("Elite", "Above Avg", "Avg", "Below Avg", "Weak")


def def_tier_from_overall_rank(rank: object, n_teams: int, *, na_label: str = "Avg") -> str:
    """Assign tier from 1..n_teams rank (1 = best defense). Uses equal quintiles when possible."""
    if pd.isna(rank):
        return na_label
    try:
        r = int(rank)
    except (TypeError, ValueError):
        return na_label
    n = max(int(n_teams), 1)
    r = min(max(r, 1), n)
    bounds: list[int] = [0]
    for k in range(1, 6):
        bounds.append(int(math.ceil(k * n / 5)))
    labels = list(DEF_TIER_LABELS)
    for i in range(5):
        if bounds[i] < r <= bounds[i + 1]:
            return labels[i]
    return labels[-1]


def bound_edges(n_teams: int) -> list[int]:
    """Quintile upper rank edges (for tests / debugging)."""
    n = max(int(n_teams), 1)
    return [int(math.ceil(k * n / 5)) for k in range(0, 6)]


def tier_sort_key(label: str) -> int:
    """Stable sort index for display strings (case-insensitive)."""
    s = str(label or "").lower().replace("🟢", "").replace("🟡", "").replace("🔴", "").strip()
    order = {
        "elite": 0,
        "above avg": 1,
        "avg": 2,
        "average": 2,
        "below avg": 3,
        "below average": 3,
        "weak": 4,
        "very weak": 5,
    }
    return order.get(s, 99)
