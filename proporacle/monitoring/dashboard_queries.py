"""SQL + Python helpers for /dashboard/income (ROI, CLV, calibration, drawdown)."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from proporacle.risk.drawdown import drawdown_fraction_series, equity_curve_from_daily_pnl


def load_income_db(path: str | Path | None = None) -> sqlite3.Connection:
    """Open PropORACLE income DB; caller must `conn.close()`."""
    p = path or os.environ.get("PROPORACLE_DB_PATH")
    if not p:
        p = Path(__file__).resolve().parents[2] / "data" / "cache" / "proporacle_income.db"
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(p))


def fetch_roi_daily(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        "SELECT bet_day, daily_pnl, daily_stake FROM v_roi_daily ORDER BY bet_day"
    )
    return [
        {"bet_day": r[0], "daily_pnl": r[1], "daily_stake": r[2]}
        for r in cur.fetchall()
    ]


def fetch_clv_by_edge_bucket(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        "SELECT ev_bucket, n, mean_clv, sum_pnl FROM v_clv_by_edge_bucket ORDER BY ev_bucket"
    )
    return [
        {"ev_bucket": r[0], "n": r[1], "mean_clv": r[2], "sum_pnl": r[3]}
        for r in cur.fetchall()
    ]


def fetch_calibration_bins(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        "SELECT p_bucket, pred_mean, hit_rate, n FROM v_calibration_bins ORDER BY p_bucket"
    )
    return [
        {"p_bucket": r[0], "pred_mean": r[1], "hit_rate": r[2], "n": r[3]}
        for r in cur.fetchall()
    ]


def fetch_equity_drawdown(conn: sqlite3.Connection, bankroll_0: float = 200.0) -> list[dict]:
    """
    Cumulative equity and drawdown from v_roi_daily (Python — avoids hard-coded SQL recursion).
    """
    rows = fetch_roi_daily(conn)
    pnls = [float(r["daily_pnl"] or 0) for r in rows]
    days = [r["bet_day"] for r in rows]
    equity = equity_curve_from_daily_pnl(bankroll_0, pnls)
    dd = drawdown_fraction_series(equity)
    out: list[dict] = []
    for i, day in enumerate(days):
        out.append(
            {
                "bet_day": day,
                "equity": equity[i],
                "drawdown": dd[i],
            }
        )
    return out
