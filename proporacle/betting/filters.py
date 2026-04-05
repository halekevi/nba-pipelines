"""Hard filters before ticket builder — only EV+ passes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from proporacle.betting.bet_candidate import BetCandidate


@dataclass
class FilterConfig:
    ev_min: float = 0.0
    sports_allow: frozenset[str] | None = None
    books_allow: frozenset[str] | None = None
    max_p_fair: float = 0.999
    min_p_fair: float = 0.01
    bad_calibration_buckets: frozenset[str] = field(default_factory=frozenset)

    def sport_ok(self, sport: str) -> bool:
        if self.sports_allow is None:
            return True
        return sport.upper() in {s.upper() for s in self.sports_allow}

    def book_ok(self, book: str) -> bool:
        if self.books_allow is None:
            return True
        return book.lower() in {b.lower() for b in self.books_allow}


def filter_candidates(
    cands: list[BetCandidate],
    cfg: FilterConfig,
    *,
    calibration_bucket_fn: Callable[[BetCandidate], str] | None = None,
) -> tuple[list[BetCandidate], list[tuple[str, str]]]:
    """
    Returns (kept, dropped) where dropped entries are (market_id, reason).

    If calibration_bucket_fn is set, it must return a bucket label per candidate;
    candidates in cfg.bad_calibration_buckets are dropped.
    """
    kept: list[BetCandidate] = []
    dropped: list[tuple[str, str]] = []

    for c in cands:
        ct = c.contract
        if ct.ev <= cfg.ev_min:
            dropped.append((ct.market_id, f"ev {ct.ev:.4f} <= {cfg.ev_min}"))
            continue
        if not cfg.sport_ok(ct.sport):
            dropped.append((ct.market_id, "sport_blocked"))
            continue
        if not (cfg.min_p_fair <= ct.p_fair <= cfg.max_p_fair):
            dropped.append((ct.market_id, "p_fair_out_of_bounds"))
            continue
        if calibration_bucket_fn is not None:
            bucket = str(calibration_bucket_fn(c))
            if bucket in cfg.bad_calibration_buckets:
                dropped.append((ct.market_id, f"bad_cal_bucket:{bucket}"))
                continue
        kept.append(c)

    return kept, dropped
