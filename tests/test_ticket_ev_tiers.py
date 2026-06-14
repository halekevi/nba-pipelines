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
    _demote_strong_recommendation,
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


def test_strong_demoted_for_long_slips():
    ticket = {
        "legs": [{"sport": "NBA"}, {}] * 5,
        "p_win": 0.55,
        "payout": {"ev": 2.0},
    }
    assert _demote_strong_recommendation("STRONG", ticket) == "OK"


def test_strong_demoted_for_low_p_win_2leg():
    ticket = {
        "legs": [{"sport": "WNBA"}, {"sport": "WNBA"}],
        "p_win": 0.20,
        "payout": {"ev": 2.0},
    }
    assert _demote_strong_recommendation("STRONG", ticket) == "OK"


def test_strong_demoted_for_cross_sport():
    ticket = {
        "legs": [{"sport": "NBA"}, {"sport": "NHL"}],
        "p_win": 0.50,
        "payout": {"ev": 2.0},
    }
    assert _demote_strong_recommendation("STRONG", ticket) == "OK"


def test_leg_stratified_tiers_reduce_long_parlay_strong():
    payload = {
        "groups": [
            {
                "tickets": [
                    {
                        "legs": [{}] * 2,
                        "p_win": 0.40,
                        "payout": {"ev": 1.5, "recommendation": "SKIP"},
                    },
                    {
                        "legs": [{}] * 6,
                        "p_win": 0.55,
                        "payout": {"ev": 2.5, "recommendation": "SKIP"},
                    },
                ]
            }
        ]
    }
    # Pad 2-leg bucket so percentiles apply
    for i in range(10):
        payload["groups"][0]["tickets"].append(
            {
                "legs": [{}] * 2,
                "p_win": 0.40,
                "payout": {"ev": float(i) * 0.1, "recommendation": "SKIP"},
            }
        )
    apply_slate_ev_tier_recommendations(payload, log=False)
    dist = tier_distribution_summary(payload)
    assert dist["STRONG"] <= dist["OK"]
    for t in payload["groups"][0]["tickets"]:
        if len(t.get("legs") or []) > 3:
            pay = t.get("payout") or {}
            assert pay.get("recommendation") != "STRONG"
