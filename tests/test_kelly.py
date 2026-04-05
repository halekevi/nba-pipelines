from proporacle.betting.config import StakingConfig
from proporacle.betting.staking import compute_stake_with_risk, kelly_fraction


def test_kelly_even_money_edge():
    # p=0.55, +100 -> b=1, f* = (0.55-0.45)/1 = 0.1
    assert abs(kelly_fraction(0.55, 100) - 0.1) < 1e-6


def test_daily_loss_stop_zeros_stake():
    cfg = StakingConfig(bankroll_units=200.0, daily_loss_stop_frac=0.05)
    stake, ev = compute_stake_with_risk(
        p_win=0.58,
        american_odds=-110,
        cfg=cfg,
        current_game_exposure=0.0,
        current_player_exposure=0.0,
        current_sport_exposure=0.0,
        daily_pnl_series=[],
        today_realized_pnl=-11.0,
    )
    assert stake == 0.0
    assert ev.reason == "daily_loss_stop"
