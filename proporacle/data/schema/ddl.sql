-- PropORACLE relational core (SQLite-compatible; works on Postgres with minor type tweaks).
-- Use slate_id + market_id as natural keys for idempotent upserts.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS slate_run (
    slate_id       TEXT PRIMARY KEY,
    sport          TEXT NOT NULL,
    slate_date     TEXT NOT NULL,  -- ISO date
    status         TEXT NOT NULL DEFAULT 'ingested',
    raw_parquet_uri TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    config_hash    TEXT
);

CREATE TABLE IF NOT EXISTS model_version (
    model_version   TEXT PRIMARY KEY,
    sport           TEXT NOT NULL,
    segment         TEXT NOT NULL DEFAULT 'default',
    trained_from    TEXT NOT NULL,
    trained_to      TEXT NOT NULL,
    n_train         INTEGER NOT NULL,
    artifact_uri    TEXT,
    card_json       TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS prediction (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slate_id        TEXT NOT NULL REFERENCES slate_run(slate_id),
    market_id       TEXT NOT NULL,
    p_raw           REAL,
    p_calibrated    REAL NOT NULL,
    model_version   TEXT NOT NULL REFERENCES model_version(model_version),
    feature_version TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (slate_id, market_id, model_version)
);

CREATE TABLE IF NOT EXISTS odds_snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slate_id        TEXT NOT NULL,
    market_id       TEXT NOT NULL,
    american_odds   INTEGER NOT NULL,
    captured_at     TEXT NOT NULL,
    book            TEXT NOT NULL DEFAULT 'prizepicks',
    is_close        INTEGER NOT NULL DEFAULT 0,
    UNIQUE (slate_id, market_id, book, is_close)
);

CREATE TABLE IF NOT EXISTS bet_candidate (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slate_id        TEXT NOT NULL,
    market_id       TEXT NOT NULL,
    p_fair          REAL NOT NULL,
    p_implied       REAL NOT NULL,
    ev              REAL NOT NULL,
    edge_quality    REAL NOT NULL,
    american_odds   INTEGER NOT NULL,
    pricing_version TEXT NOT NULL,
    uncertainty     REAL,
    liquidity       REAL,
    correlation_score REAL,
    clv_prior       REAL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (slate_id, market_id, pricing_version)
);

CREATE TABLE IF NOT EXISTS bet_recommendation (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slate_id        TEXT NOT NULL,
    market_id       TEXT NOT NULL,
    stake           REAL NOT NULL,
    filter_reason   TEXT,
    model_version   TEXT NOT NULL,
    pricing_version TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bet_result (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    slate_id            TEXT NOT NULL,
    market_id           TEXT NOT NULL,
    result              TEXT NOT NULL,
    pnl_units           REAL,
    american_odds_open  INTEGER,
    american_odds_close INTEGER,
    clv_implied_delta   REAL,
    settled_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (slate_id, market_id)
);

CREATE TABLE IF NOT EXISTS backtest_run (
    backtest_id     TEXT PRIMARY KEY,
    sport           TEXT NOT NULL,
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    model_spec      TEXT NOT NULL,
    pricing_spec    TEXT NOT NULL,
    config_json     TEXT,
    metrics_json    TEXT,
    detail_parquet_uri TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_prediction_slate ON prediction(slate_id);
CREATE INDEX IF NOT EXISTS idx_odds_slate ON odds_snapshot(slate_id);
CREATE INDEX IF NOT EXISTS idx_candidate_slate ON bet_candidate(slate_id);
CREATE INDEX IF NOT EXISTS idx_result_slate ON bet_result(slate_id);
