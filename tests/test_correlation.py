from datetime import date

from proporacle.betting.correlation import TicketRules, violates
from proporacle.contracts.bet_contract import BetContract


def _leg(**kw) -> BetContract:
    base = dict(
        sport="nba",
        slate_id="s1",
        slate_date=date(2026, 4, 5),
        market_id="m",
        stat="pts",
        line=24.5,
        side="over",
        american_odds=-110,
        p_fair=0.55,
        p_implied=0.52,
        ev=0.03,
        exposure_group="game:nba:2026-04-05:LAL-DAL",
        model_version="t",
        pricing_version="p",
        feature_version="f",
    )
    base.update(kw)
    return BetContract(**base)


def test_game_cap():
    g = "game:nba:2026-04-05:LAL-DAL"
    legs = [
        _leg(market_id="a", player_id="x", exposure_group=g),
        _leg(market_id="b", player_id="y", exposure_group=g),
        _leg(market_id="c", player_id="z", exposure_group=g),
    ]
    assert violates(legs, TicketRules(max_legs_per_game=2, max_props_per_star=5, max_legs_total=6)) is not None
