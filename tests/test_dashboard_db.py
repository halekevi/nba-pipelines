"""Golden-style: DDL + views + seed row → dashboard queries run."""

import sqlite3
from pathlib import Path

import pytest

DDL = Path(__file__).resolve().parents[1] / "proporacle" / "data" / "schema" / "ddl.sql"
VIEWS = Path(__file__).resolve().parents[1] / "proporacle" / "data" / "schema" / "views.sql"


def _apply_sql(conn: sqlite3.Connection, path: Path) -> None:
    conn.executescript(path.read_text(encoding="utf-8"))


def test_views_roundtrip(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    try:
        _apply_sql(conn, DDL)
        _apply_sql(conn, VIEWS)
        conn.execute(
            "INSERT INTO slate_run (slate_id, sport, slate_date) VALUES ('s1','nba','2026-04-05')"
        )
        conn.execute(
            "INSERT INTO model_version (model_version, sport, trained_from, trained_to, n_train) "
            "VALUES ('mv1','nba','2026-01-01','2026-04-01',1000)"
        )
        conn.execute(
            "INSERT INTO prediction (slate_id, market_id, p_calibrated, model_version) "
            "VALUES ('s1','m1',0.6,'mv1')"
        )
        conn.execute(
            "INSERT INTO bet_candidate (slate_id, market_id, p_fair, p_implied, ev, edge_quality, "
            "american_odds, pricing_version) VALUES ('s1','m1',0.6,0.52,0.08,0.1,-110,'pv1')"
        )
        conn.execute(
            "INSERT INTO bet_recommendation (slate_id, market_id, stake, model_version, pricing_version) "
            "VALUES ('s1','m1',2.0,'mv1','pv1')"
        )
        conn.execute(
            "INSERT INTO bet_result (slate_id, market_id, result, pnl_units, american_odds_open, "
            "american_odds_close, clv_implied_delta) VALUES "
            "('s1','m1','HIT',1.8,-110,-115,0.01)"
        )
        conn.commit()

        from proporacle.monitoring.dashboard_queries import (
            fetch_calibration_bins,
            fetch_clv_by_edge_bucket,
            fetch_roi_daily,
        )

        roi = fetch_roi_daily(conn)
        assert len(roi) >= 1
        assert roi[0]["daily_pnl"] == pytest.approx(1.8)
        clv = fetch_clv_by_edge_bucket(conn)
        assert len(clv) >= 1
        assert clv[0]["mean_clv"] == pytest.approx(0.01)
        cal = fetch_calibration_bins(conn)
        assert len(cal) >= 1
        assert cal[0]["hit_rate"] == pytest.approx(1.0)
    finally:
        conn.close()


def test_load_income_db_applies_schema(monkeypatch, tmp_path):
    db = tmp_path / "fresh.db"
    monkeypatch.setenv("PROPORACLE_DB_PATH", str(db))
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_PROJECT_ID", raising=False)
    monkeypatch.setenv("PROPORACLE_INCOME_SEED_DEMO", "0")

    from proporacle.monitoring.dashboard_queries import fetch_roi_daily, load_income_db

    conn = load_income_db()
    try:
        fetch_roi_daily(conn)
    finally:
        conn.close()
