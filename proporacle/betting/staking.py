"""Kelly + portfolio caps + daily loss / drawdown gates (income-targeted)."""

from __future__ import annotations

from proporacle.betting.config import StakingConfig
from proporacle.pricing.american import profit_multiple
from proporacle.risk.state import RiskEvaluation, evaluate_risk_gates


def kelly_fraction(p_win: float, american_odds: int) -> float:
    """
    Full Kelly fraction of bankroll for binary payoff.

    b = net profit per 1 unit risk if win.
    f* = (p*b - q) / b, q = 1-p, clipped to [0,1].
    """
    p = float(p_win)
    if not (0.0 < p < 1.0):
        return 0.0
    b = profit_multiple(american_odds)
    if b <= 0:
        return 0.0
    q = 1.0 - p
    raw = (p * b - q) / b
    return max(0.0, min(1.0, raw))


def stake_units(
    p_win: float,
    american_odds: int,
    bankroll: float,
    kelly_frac: float,
    max_stake_frac: float,
) -> float:
    """Fractional Kelly stake in units before portfolio caps."""
    br = float(bankroll)
    if br <= 0 or kelly_frac <= 0 or max_stake_frac <= 0:
        return 0.0
    f_full = kelly_fraction(p_win, american_odds)
    f = f_full * float(kelly_frac)
    cap = min(f, float(max_stake_frac))
    return round(br * cap, 4)


def apply_portfolio_caps(
    stake: float,
    *,
    current_game_exposure: float,
    current_player_exposure: float,
    current_sport_exposure: float,
    bankroll: float,
    cfg: StakingConfig,
) -> float:
    """Clip stake by remaining room under per-game / player / sport caps."""
    br = float(bankroll)
    if br <= 0:
        return 0.0
    s = float(stake)
    room_game = max(0.0, cfg.max_exposure_frac_per_game * br - current_game_exposure)
    room_player = max(0.0, cfg.max_exposure_frac_per_player * br - current_player_exposure)
    room_sport = max(0.0, cfg.max_exposure_frac_per_sport * br - current_sport_exposure)
    return max(0.0, min(s, room_game, room_player, room_sport))


def compute_stake_with_risk(
    *,
    p_win: float,
    american_odds: int,
    cfg: StakingConfig,
    current_game_exposure: float,
    current_player_exposure: float,
    current_sport_exposure: float,
    daily_pnl_series: list[float],
    today_realized_pnl: float,
    risk: RiskEvaluation | None = None,
) -> tuple[float, RiskEvaluation]:
    """
    End-to-end stake in **units** after Kelly, scale multiplier, portfolio caps, and risk gates.

    If `risk` is None, evaluates gates from `daily_pnl_series` and `today_realized_pnl`.
    Returns (stake, risk_evaluation).
    """
    br = float(cfg.bankroll_units)
    if br <= 0:
        return 0.0, RiskEvaluation(
            allow_new_bets=False,
            reason="invalid_bankroll",
            current_drawdown=0.0,
            daily_loss_breached=False,
            drawdown_warn=False,
            drawdown_hard_breach_streak=False,
        )

    ev = risk or evaluate_risk_gates(
        bankroll_0=br,
        daily_pnl_series=daily_pnl_series,
        today_realized_pnl=today_realized_pnl,
        cfg=cfg,
    )
    if not ev.allow_new_bets:
        return 0.0, ev

    scale = float(cfg.bankroll_scale_up_mult)
    effective_br = br * scale

    raw = stake_units(
        p_win,
        american_odds,
        effective_br,
        cfg.kelly_fraction,
        cfg.max_stake_frac_per_bet,
    )
    capped = apply_portfolio_caps(
        raw,
        current_game_exposure=current_game_exposure,
        current_player_exposure=current_player_exposure,
        current_sport_exposure=current_sport_exposure,
        bankroll=effective_br,
        cfg=cfg,
    )
    return round(capped, 4), ev


def phase3_scale_multiplier(
    *,
    rolling_roi: float,
    mean_clv: float,
    max_drawdown_observed: float,
    cfg: StakingConfig,
) -> float:
    """
    Return stake multiplier in [1.0, cfg.bankroll_scale_up_mult] if conditions hold; else 1.0.

    Wire `rolling_roi` / `mean_clv` / `max_drawdown_observed` from your nightly job (60d window).
    """
    if rolling_roi <= cfg.bankroll_scale_up_min_roi:
        return 1.0
    if mean_clv <= 0:
        return 1.0
    if max_drawdown_observed >= cfg.bankroll_scale_up_max_dd:
        return 1.0
    return float(cfg.bankroll_scale_up_mult) if cfg.bankroll_scale_up_mult > 1.0 else 1.0
