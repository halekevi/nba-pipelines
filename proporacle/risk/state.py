"""Combine daily PnL, drawdown series, and config into allow / deny new risk."""

from __future__ import annotations

from dataclasses import dataclass

from proporacle.betting.config import StakingConfig
from proporacle.risk.drawdown import (
    consecutive_days_above_threshold,
    drawdown_fraction_series,
    equity_curve_from_daily_pnl,
    warn_drawdown,
)


@dataclass(frozen=True, slots=True)
class RiskEvaluation:
    allow_new_bets: bool
    reason: str
    current_drawdown: float
    daily_loss_breached: bool
    drawdown_warn: bool
    drawdown_hard_breach_streak: bool


def evaluate_risk_gates(
    *,
    bankroll_0: float,
    daily_pnl_series: list[float],
    today_realized_pnl: float,
    cfg: StakingConfig,
) -> RiskEvaluation:
    """
    `daily_pnl_series` = ordered list of **completed** days' sum(pnl) (most recent last).
    `today_realized_pnl` = current session realized PnL (negative = loss).

    Drawdown hard stop: **consecutive** end-of-day drawdowns > `drawdown_hard_stop_frac`
    for `drawdown_hard_stop_days` days (see `drawdown_fraction_series`).
    """
    br = float(bankroll_0)
    if br <= 0:
        return RiskEvaluation(
            allow_new_bets=False,
            reason="invalid_bankroll",
            current_drawdown=0.0,
            daily_loss_breached=False,
            drawdown_warn=False,
            drawdown_hard_breach_streak=False,
        )

    daily_loss_breached = today_realized_pnl <= -cfg.daily_loss_stop_frac * br

    equity = equity_curve_from_daily_pnl(br, daily_pnl_series)
    dd_series = drawdown_fraction_series(equity)
    current_dd = dd_series[-1] if dd_series else 0.0

    dd_warn = warn_drawdown(current_dd, cfg.drawdown_warn_frac)
    hard_streak = consecutive_days_above_threshold(
        dd_series,
        cfg.drawdown_hard_stop_frac,
        cfg.drawdown_hard_stop_days,
    )

    if daily_loss_breached:
        return RiskEvaluation(
            allow_new_bets=False,
            reason="daily_loss_stop",
            current_drawdown=current_dd,
            daily_loss_breached=True,
            drawdown_warn=dd_warn,
            drawdown_hard_breach_streak=hard_streak,
        )
    if hard_streak:
        return RiskEvaluation(
            allow_new_bets=False,
            reason="drawdown_hard_stop_consecutive",
            current_drawdown=current_dd,
            daily_loss_breached=False,
            drawdown_warn=dd_warn,
            drawdown_hard_breach_streak=True,
        )

    return RiskEvaluation(
        allow_new_bets=True,
        reason="ok",
        current_drawdown=current_dd,
        daily_loss_breached=False,
        drawdown_warn=dd_warn,
        drawdown_hard_breach_streak=False,
    )
