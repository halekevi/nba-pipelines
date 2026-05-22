"""Win-rate Today's Best panel guards (bench legs, sort score)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import combined_slate_tickets as cst  # noqa: E402


def test_bench_risk_detects_support_low():
    leg = {
        "sport": "NBA",
        "min_tier": "LOW",
        "usage_role": "SUPPORT",
        "shot_role": "LOW_VOL",
    }
    assert cst._winrate_leg_bench_risk(leg) is True


def test_bench_risk_false_for_starter():
    leg = {
        "sport": "NBA",
        "min_tier": "HIGH",
        "usage_role": "PRIMARY",
        "shot_role": "HIGH_VOL",
    }
    assert cst._winrate_leg_bench_risk(leg) is False


def test_same_game_bench_stack():
    ticket = {
        "legs": [
            {
                "sport": "NBA",
                "team": "NYK",
                "opp": "CLE",
                "min_tier": "LOW",
                "usage_role": "SUPPORT",
                "shot_role": "LOW_VOL",
            },
            {
                "sport": "NBA",
                "team": "NYK",
                "opp": "CLE",
                "min_tier": "LOW",
                "usage_role": "SUPPORT",
                "shot_role": "LOW_VOL",
            },
        ]
    }
    assert cst._winrate_ticket_same_game_bench_stack(ticket) is True


def test_win_prob_prefers_est_win_prob_over_pcash():
    ticket = {"p_win": 0.64, "ticket_model_p_cash": 0.41, "est_win_prob": 0.58}
    assert cst._winrate_ticket_win_prob(ticket) == pytest.approx(0.58, rel=1e-3)
    assert cst._winrate_ticket_rank_score(ticket) == pytest.approx(0.58, rel=1e-3)


def test_rank_score_not_driven_by_ticket_model_p_cash():
    ticket = {"p_win": 0.50, "ticket_model_p_cash": 0.90, "est_win_prob": 0.52}
    assert cst._winrate_ticket_win_prob(ticket) == pytest.approx(0.52, rel=1e-3)


def test_leg_prob_cap_lower_for_bench():
    leg = {
        "leg_prob_used": 0.99,
        "sport": "NBA",
        "min_tier": "LOW",
        "usage_role": "SUPPORT",
        "shot_role": "LOW_VOL",
    }
    assert cst._leg_prob_for_p_win_from_mapping(leg) <= 0.62
