"""
Map overall defensive rank (1 = best / stingiest) to five display tiers using quintiles.

Labels match dashboards and grading HTML: Elite, Above Avg, Avg, Below Avg, Weak.
"""

from __future__ import annotations

import math
import pandas as pd

DEF_TIER_LABELS: tuple[str, ...] = ("Elite", "Above Avg", "Avg", "Below Avg", "Weak")

VALID_DEF_TIERS: frozenset[str] = frozenset(
    {"Elite", "Above Avg", "Avg", "Below Avg", "Weak", "N/A", ""}
)


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


def def_tier_from_rank(rank: object, n_teams: int, *, na_label: str = "Avg") -> str:
    """Quintile 5-bucket label; rank 1 = best defense. Alias of ``def_tier_from_overall_rank``."""
    return def_tier_from_overall_rank(rank, n_teams, na_label=na_label)


def normalize_def_tier_label(raw: object) -> str:
    """
    Map legacy / mixed-case defense labels to canonical 5-bucket strings.
    NHL legacy: SOLID → Above Avg, AVERAGE → Avg, ELITE/WEAK preserved.
    Returns \"\" for unknown input (caller may treat as missing).
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    s0 = str(raw).strip()
    if not s0:
        return ""
    s = (
        s0.replace("🟢", "")
        .replace("🟡", "")
        .replace("🔴", "")
        .strip()
    )
    key = s.lower()
    legacy = {
        "elite": "Elite",
        "above avg": "Above Avg",
        "above average": "Above Avg",
        "solid": "Above Avg",
        "avg": "Avg",
        "average": "Avg",
        "below avg": "Below Avg",
        "below average": "Below Avg",
        "weak": "Weak",
        "very weak": "Weak",
        "n/a": "N/A",
        "na": "N/A",
    }
    if key in legacy:
        return legacy[key]
    for canon in DEF_TIER_LABELS:
        if s.lower() == canon.lower():
            return canon
    if key in ("(unknown)", "unknown"):
        return ""
    return ""


def validate_def_tier(label: str) -> str:
    """Return canonical label or raise if not in the allowed set (after normalization)."""
    if label is None:
        raise ValueError("Invalid def_tier label: None")
    s = str(label).strip()
    if s in VALID_DEF_TIERS:
        return s
    n = normalize_def_tier_label(s)
    if n in VALID_DEF_TIERS:
        return n
    raise ValueError(f"Invalid def_tier label: {label!r}")


def assert_def_tier_column(df: pd.DataFrame, col: str = "def_tier", *, allow_empty: bool = True) -> None:
    """Validate every non-empty cell in ``col`` is a canonical def tier."""
    if col not in df.columns:
        return
    for v in df[col].tolist():
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        s = str(v).strip()
        if not s and allow_empty:
            continue
        validate_def_tier(s)


def format_def_tier_counts(df: pd.DataFrame, col: str = "def_tier") -> str:
    if col not in df.columns or df.empty:
        return f"{col}: (no column or empty)"
    vc = df[col].astype(str).str.strip()
    vc = vc[vc.ne("") & vc.ne("nan")]
    if vc.empty:
        return f"{col}: (all empty)"
    parts = [f"{k}={int(v)}" for k, v in vc.value_counts().items()]
    return f"{col}: " + " ".join(parts)


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
        "solid": 1,
        "avg": 2,
        "average": 2,
        "below avg": 3,
        "below average": 3,
        "weak": 4,
        "very weak": 5,
    }
    return order.get(s, 99)
