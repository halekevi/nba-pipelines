"""Tests for percentile-calibrated ticket EV tiers."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.ticket_ev_tiers import (
    apply_slate_ev_tier_recommendations,
    compute_ev_tier_thresholds,
    recommendation_from_ev,
    tier_distribution_summary,
)


def test_percentile_thresholds_spread_tiers():
    evs = list(np.linspace(-0.5, 3.0, 100))
    th = compute_ev_tier_thresholds(evs)
    payload = {
        "groups": [
            {
                "tickets": [
                    {"payout": {"ev": float(e), "recommendation": "SKIP"}}
                    for e in evs
                ]
            }
        ]
    }
    apply_slate_ev_tier_recommendations(payload, log=False)
    dist = tier_distribution_summary(payload)
    assert dist["STRONG"] >= 10
    assert dist["OK"] >= 15
    assert dist["MARGINAL"] >= 15
    assert dist["SKIP"] >= 20
    assert th["strong"] >= th["ok"] >= th["marginal"]


def test_negative_ev_always_skip():
    th = {"strong": 0.1, "ok": 0.05, "marginal": 0.0}
    assert recommendation_from_ev(-0.2, th) == "SKIP"
    assert recommendation_from_ev(-0.01, th) == "SKIP"
    assert recommendation_from_ev(0.0, th) == "MARGINAL"


def test_legacy_fallback_small_sample():
    th = compute_ev_tier_thresholds([0.1, 0.2])
    assert th["strong"] == 1.40
    assert th["ok"] == 1.15
