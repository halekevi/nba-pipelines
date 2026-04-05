"""Exposure groups and ticket constraint checks."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from proporacle.contracts.bet_contract import BetContract


def exposure_group(contract: BetContract) -> str:
    """Derive a stable exposure bucket from contract fields (extend with roster IDs)."""
    return contract.exposure_group


def game_key_from_group(exposure_group: str) -> str | None:
    if exposure_group.startswith("game:"):
        return exposure_group.split(":", 2)[-1] if exposure_group.count(":") >= 2 else exposure_group
    return None


@dataclass
class TicketRules:
    max_legs_per_game: int = 2
    max_props_per_star: int = 2
    max_legs_total: int = 6


def violates(legs: Sequence[BetContract], rules: TicketRules) -> str | None:
    if len(legs) > rules.max_legs_total:
        return f"too_many_legs:{len(legs)}>{rules.max_legs_total}"

    by_game: Counter[str] = Counter()
    by_star: Counter[str] = Counter()

    for leg in legs:
        g = game_key_from_group(leg.exposure_group) or leg.exposure_group
        by_game[g] += 1
        if leg.player_id:
            key = f"{g}|{leg.player_id}"
            by_star[key] += 1

    for g, n in by_game.items():
        if n > rules.max_legs_per_game:
            return f"game_cap:{g}:{n}>{rules.max_legs_per_game}"

    for k, n in by_star.items():
        if n > rules.max_props_per_star:
            return f"star_cap:{k}:{n}>{rules.max_props_per_star}"

    return None
