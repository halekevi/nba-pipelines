"""BetCandidate wraps BetContract with meta signals for ranking (not a second p_fair)."""

from __future__ import annotations

from dataclasses import dataclass

from proporacle.contracts.bet_contract import BetContract


@dataclass(frozen=True, slots=True)
class BetCandidate:
    contract: BetContract
    uncertainty: float  # in [0,1], higher = less trust in p_fair
    liquidity: float  # in [0,1]
    correlation_score: float  # higher = more overlap with rest of book/portfolio
    clv_prior: float | None = None  # optional shrink/boost from historical CLV bucket


def edge_quality(c: BetCandidate) -> float:
    """
    Ranking scalar only. edge_model / meta models should feed *here* (uncertainty, clv_prior),
    not overwrite contract.p_fair.
    """
    ev = c.contract.ev
    conf = max(0.0, 1.0 - c.uncertainty)
    liq = max(0.0, min(1.0, c.liquidity))
    corr_pen = max(0.05, 1.0 - max(0.0, min(1.0, c.correlation_score)))
    clv = 1.0 + (c.clv_prior if c.clv_prior is not None else 0.0)
    return ev * conf * liq * corr_pen * clv


def rank_candidates(cands: list[BetCandidate]) -> list[BetCandidate]:
    return sorted(cands, key=edge_quality, reverse=True)
