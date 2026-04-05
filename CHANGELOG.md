# Changelog — Goblin/Demon payout curve (2026-04-05)

## Added

- `data/payout_curve_params.json` — tunable `G_EXP`, `D_EXP`, `D_SCALE` for the approximation curve.
- `data/payout_observations.csv` — header row for logging real PrizePicks multipliers (append via API or manually).
- `utils/goblin_demon_multiplier.py` — per-leg factors, `ticket_multiplier`, `multiplier_summary`, `compute_ticket_ev`, synthetic combo legs.
- `utils/fit_payout_curve.py` — fits the three parameters from observations (`--dry-run`, `--min-obs`, `--export-curve-report`); requires SciPy.
- `scripts/write_combo_table_latest.py` — writes `outputs/combo_table_latest.json` for the payout reference UI.

## Modified

- `scripts/combined_slate_tickets.py` — `enrich_ticket_curve_payouts`, `--curve-stake-usd`, JSON/Excel fields (`delta_pct`, `est_multiplier`, `flat_multiplier`, EV columns), ticket sheet Std Line / Delta %, `render_tickets_html` delta badges and curve KPIs.
- `scripts/combined_ticket_grader.py` — merges `standard_line` from combined JSON, `delta_pct` / `gd_leg_factor`, `est_curve_mult`, `payout_est_curve`, ROI summary; sheets `PAYOUT_LEG_DETAIL`, `PAYOUT_TICKET_DETAIL`, `PAYOUT_ACCURACY`, `PAYOUT_WARNINGS`; optional payout overrides in `compute_ticket_payout`.
- `scripts/run_daily.ps1` — STEP A1d runs `fit_payout_curve.py` and `write_combo_table_latest.py`.
- `ui_runner/app.py` — `GET /api/slate/today-tickets`, `POST /api/payout/log-observation`, `GET /api/payout/combo-table`.
- `ui_runner/templates/payout_calculator.html` — “Import from slate JSON” for the Log Lines tab.
- `NBA/scripts/step1_fetch_prizepicks_api.py`, `Soccer/scripts/step1_fetch_prizepicks_soccer.py`, `MLB/scripts/step1_fetch_prizepicks_mlb.py` — `standard_line` column from API when present.
- `NHL/scripts/step1_fetch_prizepicks_nhl.py` — `standard_line` from API or matched Standard prop per player/stat; `delta_pct` = `line_score / standard_line`; warns when Goblin/Demon rows lack a baseline.
