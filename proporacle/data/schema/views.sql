-- Income-engine dashboard views (SQLite). Apply after ddl.sql on the same DB.
-- PROPORACLE_DB_PATH or data/cache/proporacle_income.db

DROP VIEW IF EXISTS v_calibration_bins;
DROP VIEW IF EXISTS v_clv_by_edge_bucket;
DROP VIEW IF EXISTS v_roi_daily;

-- Daily realized PnL and stake (for ROI = sum(pnl)/sum(stake) over a window).
CREATE VIEW v_roi_daily AS
SELECT
  date(br.settled_at) AS bet_day,
  SUM(COALESCE(br.pnl_units, 0)) AS daily_pnl,
  SUM(COALESCE(rs.stake, 0)) AS daily_stake
FROM bet_result br
LEFT JOIN (
  SELECT slate_id, market_id, MAX(stake) AS stake
  FROM bet_recommendation
  GROUP BY slate_id, market_id
) rs ON br.slate_id = rs.slate_id AND br.market_id = rs.market_id
GROUP BY 1;

-- CLV vs EV bucket (join frozen candidate row at bet time).
CREATE VIEW v_clv_by_edge_bucket AS
SELECT
  CASE
    WHEN bc.ev <= 0 THEN 'ev_nonpos'
    WHEN bc.ev < 0.02 THEN 'ev_0_2pct'
    WHEN bc.ev < 0.05 THEN 'ev_2_5pct'
    WHEN bc.ev < 0.10 THEN 'ev_5_10pct'
    ELSE 'ev_10pct_plus'
  END AS ev_bucket,
  COUNT(*) AS n,
  AVG(br.clv_implied_delta) AS mean_clv,
  SUM(COALESCE(br.pnl_units, 0)) AS sum_pnl
FROM bet_result br
INNER JOIN bet_candidate bc ON br.slate_id = bc.slate_id AND br.market_id = bc.market_id
WHERE br.clv_implied_delta IS NOT NULL
GROUP BY 1;

-- Calibration: predicted prob vs realized hit (adjust HIT set to your result enums).
CREATE VIEW v_calibration_bins AS
SELECT
  CAST(p.p_calibrated * 10 AS INTEGER) AS p_bucket,
  AVG(p.p_calibrated) AS pred_mean,
  AVG(
    CASE
      WHEN UPPER(TRIM(br.result)) IN ('HIT', 'WIN', 'W')
        THEN 1.0
      WHEN UPPER(TRIM(br.result)) IN ('MISS', 'LOSS', 'L')
        THEN 0.0
      ELSE NULL
    END
  ) AS hit_rate,
  COUNT(*) AS n
FROM prediction p
JOIN bet_result br ON p.slate_id = br.slate_id AND p.market_id = br.market_id
GROUP BY 1
HAVING COUNT(*) >= 1;
