"""SQL + Python helpers for /dashboard/income (ROI, CLV, calibration, drawdown)."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "data" / "schema"
_DDL_SQL = _SCHEMA_DIR / "ddl.sql"
_VIEWS_SQL = _SCHEMA_DIR / "views.sql"


def _apply_sql_script(conn: sqlite3.Connection, path: Path) -> None:
    conn.executescript(path.read_text(encoding="utf-8"))


def ensure_income_schema(conn: sqlite3.Connection) -> None:
    """Create core tables and dashboard views if missing (e.g. fresh SQLite file)."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='view' AND name='v_roi_daily'"
    ).fetchone()
    if row:
        return
    if not _DDL_SQL.is_file() or not _VIEWS_SQL.is_file():
        raise FileNotFoundError(
            f"Income schema files not found: expected {_DDL_SQL} and {_VIEWS_SQL}"
        )
    _apply_sql_script(conn, _DDL_SQL)
    _apply_sql_script(conn, _VIEWS_SQL)
    conn.commit()


def bet_result_count(conn: sqlite3.Connection) -> int:
    try:
        return int(conn.execute("SELECT COUNT(*) FROM bet_result").fetchone()[0])
    except sqlite3.OperationalError:
        return 0


def seed_demo_income_data(conn: sqlite3.Connection) -> bool:
    """
    Insert synthetic slates/bets so income charts are non-empty. Idempotent: skips if
    demo slates already exist. Returns True if rows were inserted.
    """
    ensure_income_schema(conn)
    if conn.execute(
        "SELECT 1 FROM bet_result WHERE slate_id LIKE 'demo_slate_%' LIMIT 1"
    ).fetchone():
        return False

    import random
    from datetime import date, timedelta

    random.seed(42)
    mv = "demo_model_v1"
    pv = "demo_pricing_v1"
    conn.execute(
        "INSERT OR IGNORE INTO model_version (model_version, sport, trained_from, trained_to, n_train) "
        "VALUES (?, 'nba', '2025-01-01', '2026-03-01', 5000)",
        (mv,),
    )

    start = date(2026, 2, 1)
    for d in range(40):
        day = start + timedelta(days=d)
        ds = day.isoformat()
        sid = f"demo_slate_{ds}"
        settled = f"{ds} 18:00:00"
        conn.execute(
            "INSERT OR IGNORE INTO slate_run (slate_id, sport, slate_date) VALUES (?, 'nba', ?)",
            (sid, ds),
        )
        for k in range(4):
            mid = f"leg_{k}"
            p_cal = 0.36 + (k * 0.11) % 0.45
            ev = 0.015 + (k * 0.022)
            hit = random.random() < p_cal
            res = "HIT" if hit else "MISS"
            pnl = 0.91 if hit else -1.0
            clv = (random.random() - 0.5) * 0.025
            conn.execute(
                "INSERT OR IGNORE INTO prediction (slate_id, market_id, p_calibrated, model_version) "
                "VALUES (?,?,?,?)",
                (sid, mid, min(0.92, max(0.08, p_cal)), mv),
            )
            conn.execute(
                "INSERT OR IGNORE INTO bet_candidate (slate_id, market_id, p_fair, p_implied, ev, edge_quality, "
                "american_odds, pricing_version) VALUES (?,?,?,?,?,?, -110, ?)",
                (sid, mid, p_cal, 0.52, ev, 0.1, pv),
            )
            conn.execute(
                "INSERT OR IGNORE INTO bet_recommendation (slate_id, market_id, stake, model_version, pricing_version) "
                "VALUES (?,?,1.5,?,?)",
                (sid, mid, mv, pv),
            )
            conn.execute(
                "INSERT OR IGNORE INTO bet_result (slate_id, market_id, result, pnl_units, american_odds_open, "
                "american_odds_close, clv_implied_delta, settled_at) VALUES (?,?,?,?,?,?,?,?)",
                (sid, mid, res, pnl, -110, -108, clv, settled),
            )
    conn.commit()
    return True


def load_income_db(path: str | Path | None = None) -> sqlite3.Connection:
    """Open PropORACLE income DB and ensure ddl.sql + views.sql are applied. Caller must `conn.close()`."""
    p = path or os.environ.get("PROPORACLE_DB_PATH")
    if not p:
        p = _REPO_ROOT / "data" / "cache" / "proporacle_income.db"
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    ensure_income_schema(conn)
    return conn


def maybe_seed_demo_income(conn: sqlite3.Connection) -> None:
    """
    When bet_result is empty, insert demo rows so charts render (idempotent demo slates).

    Opt out with PROPORACLE_INCOME_SEED_DEMO=0 (or false/no) if you use an empty DB on purpose
    or ingest only real results.
    """
    if bet_result_count(conn) > 0:
        return
    flag = os.environ.get("PROPORACLE_INCOME_SEED_DEMO", "").strip().lower()
    if flag in ("0", "false", "no"):
        return
    seed_demo_income_data(conn)


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
    from proporacle.risk.drawdown import drawdown_fraction_series, equity_curve_from_daily_pnl

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
