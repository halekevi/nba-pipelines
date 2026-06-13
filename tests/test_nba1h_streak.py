"""NBA1H gate streak must use as-of rolling AUC from graded rows, not log replay."""

from __future__ import annotations

from datetime import date

import pytest

from scripts.track_model_performance import (
    _NBA1H_UNBLOCK_AUC,
    _consecutive_days_above_052,
    _nba1h_streak_consecutive_days,
)


def test_consecutive_days_above_052_breaks_on_low_auc():
    daily = [
        (date(2026, 6, 12), 0.54),
        (date(2026, 6, 11), 0.54),
        (date(2026, 6, 10), 0.51),
    ]
    assert _consecutive_days_above_052(daily, threshold=_NBA1H_UNBLOCK_AUC) == 2


def test_consecutive_days_above_052_breaks_on_calendar_gap():
    daily = [
        (date(2026, 6, 12), 0.54),
        (date(2026, 6, 11), 0.54),
        (date(2026, 6, 9), 0.54),
    ]
    assert _consecutive_days_above_052(daily, threshold=_NBA1H_UNBLOCK_AUC) == 2


def test_streak_from_rows_matches_production_june_12():
    """Regression: log replay briefly showed 3/3; as-of walk must stay at 2/3."""
    from scripts.track_model_performance import load_nba1h_graded_rows

    rows = load_nba1h_graded_rows()
    end = date(2026, 6, 12)
    streak = _nba1h_streak_consecutive_days(rows, end=end, min_n=10)
    assert streak == 2


def test_streak_from_synthetic_rows():
    rows = []
    for i in range(40):
        fd = f"2026-06-{12 - (i % 3):02d}"
        hit = 1 if i % 2 == 0 else 0
        prob = 0.65 if hit else 0.35
        rows.append({"file_date": fd, "hit": hit, "ml_prob": prob})
    streak = _nba1h_streak_consecutive_days(
        rows,
        end=date(2026, 6, 12),
        min_n=10,
        window_days=30,
    )
    assert streak >= 1
