#!/usr/bin/env python3
"""Lookup confidence tiers from accuracy_tracking.db."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_DB = _REPO / "data" / "accuracy_tracking.db"

_CACHE: dict[tuple[str, str, str], tuple[float | None, int]] | None = None


def _load_cache(db_path: Path) -> dict[tuple[str, str, str], tuple[float | None, int]]:
    if not db_path.is_file():
        return {}
    out: dict[tuple[str, str, str], tuple[float | None, int]] = {}
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT sport, direction, tier, hit_rate, n
            FROM accuracy_slices
            WHERE window_days = (
                SELECT MAX(window_days) FROM accuracy_slices
            )
            """
        )
        for sport, direction, tier, hr, n in cur.fetchall():
            key = (
                str(sport or "").strip().upper(),
                str(direction or "").strip().upper(),
                str(tier or "").strip().upper() or "—",
            )
            out[key] = (float(hr) if hr is not None else None, int(n or 0))
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return out


def confidence_tier(
    sport: str,
    direction: str,
    tier: str,
    *,
    db_path: Path | None = None,
) -> str:
    global _CACHE
    path = db_path or _DB
    if _CACHE is None:
        _CACHE = _load_cache(path)
    sp = str(sport or "").strip().upper()
    dr = str(direction or "").strip().upper()
    tr = str(tier or "").strip().upper() or "—"
    hr, n = _CACHE.get((sp, dr, tr), (None, 0))
    if n < 50:
        return "TRACKING"
    if hr is None:
        return "TRACKING"
    if hr >= 0.55:
        return "HIGH"
    if hr >= 0.45:
        return "MEDIUM"
    return "LOW"


def confidence_tier_with_hr(
    sport: str,
    direction: str,
    tier: str,
    *,
    db_path: Path | None = None,
) -> tuple[str, float | None]:
    global _CACHE
    path = db_path or _DB
    if _CACHE is None:
        _CACHE = _load_cache(path)
    sp = str(sport or "").strip().upper()
    dr = str(direction or "").strip().upper()
    tr = str(tier or "").strip().upper() or "—"
    hr, n = _CACHE.get((sp, dr, tr), (None, 0))
    tier_label = confidence_tier(sp, dr, tr, db_path=path)
    return tier_label, hr


def load_slice_cache(db_path: Path | None = None) -> dict[tuple[str, str, str], tuple[float | None, int]]:
    """Load accuracy slice map for batch UI enrichment (one query per request)."""
    return _load_cache(db_path or _DB)


def confidence_tier_from_cache(
    sport: str,
    direction: str,
    tier: str,
    cache: dict[tuple[str, str, str], tuple[float | None, int]],
) -> tuple[str, float | None]:
    sp = str(sport or "").strip().upper()
    dr = str(direction or "").strip().upper()
    tr = str(tier or "").strip().upper() or "—"
    hr, n = cache.get((sp, dr, tr), (None, 0))
    if n < 50:
        return "TRACKING", hr
    if hr is None:
        return "TRACKING", hr
    if hr >= 0.55:
        return "HIGH", hr
    if hr >= 0.45:
        return "MEDIUM", hr
    return "LOW", hr
