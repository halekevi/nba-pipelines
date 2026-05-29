"""Tests for matchup edge classification (OVER and UNDER paths)."""
from utils.matchup_edge.classify import classify_edge


def test_top_edge_positive_pp():
    edge, note = classify_edge(20.0, 15.0, 10, 13, pp_edge=3.5, rank_on_team=1)
    assert edge == "TOP_EDGE"
    assert "+3.5" in note


def test_top_under_vs_elite():
    edge, note = classify_edge(
        12.0,
        15.0,
        2,
        13,
        pp_edge=-2.5,
        hist={"fades_vs_elite": True, "avg_delta_vs_elite": -1.2},
        rank_on_team=1,
    )
    assert edge == "TOP_UNDER"
    assert "UNDER" in note or "fades" in note.lower()


def test_ok_under_negative_pp_elite():
    edge, _ = classify_edge(14.0, 15.0, 3, 13, pp_edge=-1.2, rank_on_team=2)
    assert edge == "OK_UNDER"


def test_avoid_negative_pp_non_elite():
    edge, note = classify_edge(14.0, 15.0, 8, 13, pp_edge=-1.5, rank_on_team=2)
    assert edge == "AVOID"
    assert "skip OVER" in note


def test_bottom3_under_vs_elite():
    edge, note = classify_edge(
        8.0,
        15.0,
        2,
        13,
        pp_edge=-0.8,
        bottom_rank_on_team=1,
        rank_on_team=None,
    )
    assert edge in ("TOP_UNDER", "OK_UNDER")
    assert "bottom-1" in note.lower()
