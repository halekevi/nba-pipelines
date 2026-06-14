"""Tests for STRONG-eligible Goblin+HOT ticket builder."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from combined_slate_tickets import (  # noqa: E402
    _strong_candidate_legs,
    build_strong_tickets,
)
from utils.ticket_ev_tiers import apply_slate_ev_tier_recommendations  # noqa: E402


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "sport": "NBA",
                "player": "Alpha One",
                "team": "BOS",
                "opp": "NYK",
                "prop_type": "Points",
                "pick_type": "Goblin",
                "tier": "A",
                "direction": "OVER",
                "line": 18.5,
                "hit_rate": 0.72,
                "rank_score": 90,
                "ml_prob": 0.72,
                "l10_over": 8.0,
                "l10_under": 2.0,
                "l10_streak": "HOT",
                "prop_quality_score": 0.85,
            },
            {
                "sport": "NBA",
                "player": "Beta Two",
                "team": "LAL",
                "opp": "GSW",
                "prop_type": "Rebounds",
                "pick_type": "Goblin",
                "tier": "B",
                "direction": "OVER",
                "line": 7.5,
                "hit_rate": 0.68,
                "rank_score": 80,
                "ml_prob": 0.68,
                "l10_over": 7.0,
                "l10_under": 3.0,
                "l10_streak": "HOT",
                "prop_quality_score": 0.80,
            },
            {
                "sport": "NBA",
                "player": "Cold Three",
                "team": "MIA",
                "opp": "CHI",
                "prop_type": "Assists",
                "pick_type": "Goblin",
                "tier": "A",
                "direction": "OVER",
                "line": 5.5,
                "hit_rate": 0.40,
                "rank_score": 50,
                "ml_prob": 0.40,
                "l10_over": 2.0,
                "l10_under": 8.0,
                "l10_streak": "COLD",
                "prop_quality_score": 0.30,
            },
            {
                "sport": "NBA",
                "player": "Std Four",
                "team": "PHX",
                "opp": "DAL",
                "prop_type": "Points",
                "pick_type": "Standard",
                "tier": "A",
                "direction": "OVER",
                "line": 22.5,
                "hit_rate": 0.75,
                "rank_score": 95,
                "ml_prob": 0.75,
                "l10_over": 9.0,
                "l10_under": 1.0,
                "l10_streak": "HOT",
                "prop_quality_score": 0.90,
            },
        ]
    )


def test_strong_candidate_legs_filters_goblin_hot_ab():
    df = _sample_df()
    out = _strong_candidate_legs(df)
    assert len(out) == 2
    players = set(out["player"].astype(str))
    assert players == {"Alpha One", "Beta Two"}


def test_strong_candidate_legs_excludes_mlb_and_non_core_props():
    df = pd.DataFrame(
        [
            {
                "sport": "MLB",
                "player": "Hitter One",
                "prop_type": "Hits",
                "pick_type": "Goblin",
                "tier": "A",
                "l10_streak": "HOT",
            },
            {
                "sport": "WNBA",
                "player": "Shooter One",
                "prop_type": "3-PT Made",
                "pick_type": "Goblin",
                "tier": "A",
                "l10_streak": "HOT",
            },
            {
                "sport": "WNBA",
                "player": "Scorer One",
                "prop_type": "Points",
                "pick_type": "Goblin",
                "tier": "A",
                "l10_streak": "HOT",
            },
        ]
    )
    out = _strong_candidate_legs(df)
    assert len(out) == 1
    assert str(out.iloc[0]["player"]) == "Scorer One"


def test_build_strong_tickets_produces_labeled_slips():
    tickets = build_strong_tickets(_sample_df(), max_tickets=5, date_str="2026-06-14")
    assert len(tickets) >= 1
    t = tickets[0]
    assert t.get("strong_builder") is True
    assert t.get("n_legs") == 2
    assert float(t.get("est_win_prob") or 0) >= 0.33
    for row in t.get("rows") or []:
        assert "goblin" in str(row.get("pick_type", "")).lower()
        assert str(row.get("tier", "")).upper() in ("A", "B")
        assert str(row.get("l10_streak", "")).upper() == "HOT"


def test_strong_builder_slips_keep_strong_recommendation():
    tickets = build_strong_tickets(_sample_df(), max_tickets=3, date_str="2026-06-14")
    assert tickets
    payload = {
        "date": "2026-06-14",
        "groups": [
            {
                "group_name": "STRONG Goblin HOT",
                "tickets": [
                    {
                        "strong_builder": True,
                        "n_legs": t["n_legs"],
                        "p_win": t["est_win_prob"],
                        "legs": [
                            {
                                "sport": r.get("sport"),
                                "player": r.get("player"),
                                "pick_type": r.get("pick_type"),
                                "tier": r.get("tier"),
                                "l10_streak": r.get("l10_streak"),
                                "prop_type": r.get("prop_type"),
                                "line": r.get("line"),
                            }
                            for r in t["rows"]
                        ],
                        "payout": {"ev": float(t.get("ev_power") or 1.0)},
                    }
                    for t in tickets[:1]
                ],
            }
        ],
    }
    apply_slate_ev_tier_recommendations(payload, log=False)
    rec = payload["groups"][0]["tickets"][0]["payout"]["recommendation"]
    assert rec == "STRONG"
