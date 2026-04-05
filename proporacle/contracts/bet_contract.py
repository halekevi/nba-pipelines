"""Canonical bet row: probabilities, prices, EV, versions, exposure."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

BetSide = Literal["over", "under", "yes", "no"]


class BetContract(BaseModel):
    """Single unified contract for all sports — source of truth before ticketing."""

    sport: str
    slate_id: str
    slate_date: date
    market_id: str
    player_id: str | None = None
    stat: str
    line: float
    side: BetSide
    american_odds: int

    p_fair: float = Field(..., ge=0.0, le=1.0, description="Calibrated model P(win this contract)")
    p_implied: float = Field(..., ge=0.0, le=1.0, description="Market implied prob (raw or post-devig)")
    ev: float = Field(..., description="Expected value per 1 unit risk at american_odds")
    edge_quality: float = Field(
        0.0,
        description="Ranking scalar (EV × confidence × liquidity); not a probability",
    )

    stake: float = Field(0.0, ge=0.0)
    exposure_group: str = Field(
        ...,
        description="Bucket for correlation / caps, e.g. game:nba:2026-04-05:LAL-DAL",
    )

    model_version: str = ""
    pricing_version: str = ""
    feature_version: str = ""

    @field_validator("sport", "slate_id", "market_id", "stat", "exposure_group")
    @classmethod
    def _non_empty_str(cls, v: str) -> str:
        s = str(v).strip()
        if not s:
            raise ValueError("must be non-empty")
        return s

    def with_stake(self, stake: float) -> BetContract:
        return self.model_copy(update={"stake": float(stake)})


class PayoutTable(BaseModel):
    """PrizePicks-style flex / multiplier schedule (stake = 1 unit)."""

    name: str = "pp_flex_default"
    #: Maps number of legs -> gross payout multiple on stake (including stake return style = define convention)
    leg_count_to_multiplier: dict[int, float] = Field(default_factory=dict)

    @field_validator("leg_count_to_multiplier")
    @classmethod
    def _positive_mults(cls, v: dict[int, float]) -> dict[int, float]:
        out: dict[int, float] = {}
        for k, m in v.items():
            kk = int(k)
            mm = float(m)
            if kk < 1 or mm <= 0:
                raise ValueError("invalid payout table entry")
            out[kk] = mm
        return out
