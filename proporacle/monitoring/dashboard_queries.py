"""SQL + Python helpers for /dashboard/income (ROI, CLV, calibration, drawdown)."""

from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _today() -> date:
    """Calendar “today” for demo windows; tests may monkeypatch this symbol."""
    return date.today()
_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "data" / "schema"
_DDL_SQL = _SCHEMA_DIR / "ddl.sql"
_VIEWS_SQL = _SCHEMA_DIR / "views.sql"

# Loading proporacle.risk.drawdown via the package runs risk/__init__.py → state → pydantic,
# which ui_runner does not install. Load the module file directly (drawdown.py has no deps).
_drawdown_mod = None


def _drawdown_math():
    global _drawdown_mod
    if _drawdown_mod is None:
        import importlib.util

        path = Path(__file__).resolve().parent.parent / "risk" / "drawdown.py"
        spec = importlib.util.spec_from_file_location(
            "proporacle.risk._drawdown_income_only", path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load drawdown helpers from {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _drawdown_mod = mod
    return _drawdown_mod


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


def _non_demo_bet_result_count(conn: sqlite3.Connection) -> int:
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM bet_result WHERE slate_id NOT LIKE 'demo_slate_%'"
            ).fetchone()[0]
        )
    except sqlite3.OperationalError:
        return 0


def _max_bet_day_str(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute("SELECT MAX(date(settled_at)) FROM bet_result").fetchone()
        return str(row[0]) if row and row[0] is not None else None
    except sqlite3.OperationalError:
        return None


def purge_demo_income_slates(conn: sqlite3.Connection) -> int:
    """Remove demo_slate_* rows (child tables first). Returns number of slates removed."""
    cur = conn.cursor()
    n = 0
    cur.execute("SELECT slate_id FROM slate_run WHERE slate_id LIKE 'demo_slate_%'")
    ids = [r[0] for r in cur.fetchall()]
    for sid in ids:
        cur.execute("DELETE FROM bet_result WHERE slate_id = ?", (sid,))
        cur.execute("DELETE FROM bet_recommendation WHERE slate_id = ?", (sid,))
        cur.execute("DELETE FROM bet_candidate WHERE slate_id = ?", (sid,))
        cur.execute("DELETE FROM prediction WHERE slate_id = ?", (sid,))
        cur.execute("DELETE FROM slate_run WHERE slate_id = ?", (sid,))
        n += 1
    conn.commit()
    return n


def income_dashboard_meta(conn: sqlite3.Connection) -> dict:
    """UI hint: whether charts are demo-only vs ingested grades, and last calendar day in DB."""
    n_demo = 0
    n_real = _non_demo_bet_result_count(conn)
    try:
        n_demo = int(
            conn.execute(
                "SELECT COUNT(*) FROM bet_result WHERE slate_id LIKE 'demo_slate_%'"
            ).fetchone()[0]
        )
    except sqlite3.OperationalError:
        pass
    if n_real > 0:
        mode = "mixed" if n_demo else "real"
    elif n_demo > 0:
        mode = "demo"
    else:
        mode = "empty"
    return {"mode": mode, "data_through": _max_bet_day_str(conn)}


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

    random.seed(42)
    mv = "demo_model_v1"
    pv = "demo_pricing_v1"
    # Rolling 40-day window ending yesterday so placeholder charts stay current without redeploy.
    end_day = _today() - timedelta(days=1)
    start = end_day - timedelta(days=39)
    conn.execute(
        "INSERT OR IGNORE INTO model_version (model_version, sport, trained_from, trained_to, n_train) "
        "VALUES (?, 'nba', '2025-01-01', ?, 5000)",
        (mv, end_day.isoformat()),
    )

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

    If the DB only contains demo_slate_* rows and the newest settled day is older than
    PROPORACLE_INCOME_DEMO_REFRESH_DAYS (default 10), demo slates are purged and re-seeded
    with a rolling 40-day window ending yesterday (keeps Railway / ephemeral volumes from
    freezing on an old fixed-range sample).

    Opt out with PROPORACLE_INCOME_SEED_DEMO=0 (or false/no) if you use an empty DB on purpose
    or ingest only real results.
    """
    flag = os.environ.get("PROPORACLE_INCOME_SEED_DEMO", "").strip().lower()
    if flag in ("0", "false", "no"):
        return

    n_real = _non_demo_bet_result_count(conn)
    if n_real > 0:
        return

    refresh_days_raw = os.environ.get("PROPORACLE_INCOME_DEMO_REFRESH_DAYS", "10").strip()
    try:
        refresh_days = max(1, int(refresh_days_raw))
    except ValueError:
        refresh_days = 10

    n_total = bet_result_count(conn)
    if n_total > 0:
        max_day_s = _max_bet_day_str(conn)
        if not max_day_s:
            return
        try:
            max_day = date.fromisoformat(max_day_s)
        except ValueError:
            return
        age = (_today() - max_day).days
        if age <= refresh_days:
            return
        purge_demo_income_slates(conn)

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
    dm = _drawdown_math()
    equity_curve_from_daily_pnl = dm.equity_curve_from_daily_pnl
    drawdown_fraction_series = dm.drawdown_fraction_series

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
