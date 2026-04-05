"""Equity curve and drawdown from realized PnL — income-engine risk signals."""

from __future__ import annotations


def equity_curve_from_daily_pnl(bankroll_0: float, daily_pnls: list[float]) -> list[float]:
    """
    End-of-day equity after each day (length = len(daily_pnls)).
    First value = bankroll_0 + daily_pnls[0].
    """
    br0 = float(bankroll_0)
    out: list[float] = []
    eq = br0
    for x in daily_pnls:
        eq += float(x)
        out.append(eq)
    return out


def drawdown_fraction_series(equity: list[float]) -> list[float]:
    """
    Per-step drawdown from running peak: (peak - equity) / peak.
    Safe for equity <= 0 (returns 0.0 if peak <= 0).
    """
    peak = float("-inf")
    dd: list[float] = []
    for e in equity:
        peak = max(peak, float(e))
        if peak <= 0:
            dd.append(0.0)
        else:
            dd.append(max(0.0, (peak - float(e)) / peak))
    return dd


def consecutive_days_above_threshold(values: list[float], threshold: float, need: int) -> bool:
    """True if `threshold` is exceeded for `need` consecutive days (end of series)."""
    if need <= 0:
        return False
    run = 0
    for v in values:
        if float(v) > float(threshold):
            run += 1
            if run >= need:
                return True
        else:
            run = 0
    return False


def warn_drawdown(current_dd: float, warn_frac: float) -> bool:
    return float(current_dd) > float(warn_frac)
