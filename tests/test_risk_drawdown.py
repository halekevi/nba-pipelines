from proporacle.risk.drawdown import (
    consecutive_days_above_threshold,
    drawdown_fraction_series,
    equity_curve_from_daily_pnl,
)


def test_equity_and_dd():
    eq = equity_curve_from_daily_pnl(200.0, [10.0, -30.0, 5.0])
    assert eq[0] == 210.0
    assert eq[1] == 180.0
    dd = drawdown_fraction_series(eq)
    assert dd[0] == 0.0
    assert dd[1] > 0


def test_consecutive_threshold():
    assert consecutive_days_above_threshold([0.1, 0.26, 0.26, 0.26], 0.25, 3) is True
    assert consecutive_days_above_threshold([0.1, 0.26, 0.1, 0.26], 0.25, 3) is False
