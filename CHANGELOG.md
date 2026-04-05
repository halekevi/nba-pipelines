# PropORACLE changelog (agent session)

## Summary

Additive upgrades: CLV logging in SQLite, optional graded workbook CLV columns, fractional Kelly and correlation-aware ticket scoring in `combined_slate_tickets.py`, NBA/CBB ML context features + meta ranker, Grades dashboard insights API/tab, NFL/Golf pipeline stubs, PowerShell switches and daily enrich step.

## Files created

- `utils/optional_ml_context.py` — shared optional features for ML training (`pace_percentile`, `days_rest`, `line_move_direction`, `is_back_to_back`).
- `scripts/enrich_graded_workbook_clv.py` — post-process graded `.xlsx` to add implied-prob / `clv_delta` columns when odds columns exist.
- `NFL/step1_fetch_props_nfl.py` — stub PrizePicks NFL fetch (empty scaffold CSV).
- `Golf/step1_fetch_props_golf.py` — stub PrizePicks golf fetch (empty scaffold CSV).
- `Golf/step2_attach_golf_context.py` — stub context pass-through with placeholder columns.
- `CHANGELOG.md` — this file.

## Files modified

- `utils/clv_tracker.py` — `graded_rows_to_clv_log` extended (pick/tier/result, American odds fallback, prop label); removed broken row-wise enrich helper.
- `utils/kelly_staking.py` — (existing) fractional Kelly helpers used by tickets JSON.
- `scripts/step_archive.py` — `clv_log` table + `archive_clv_log()`; CLI archives CLV after graded props.
- `scripts/combined_slate_tickets.py` — `sys.path` + Kelly imports; `_correlation_multiplier_and_audit` (same-game density, same-team −15%, player stack +5%); ticket audit fields; `--bankroll` + `recommended_stake_usd` / correlation fields in web payload; `_correlation_penalty` wrapper retained for `combined_ticket_grader`.
- `scripts/train_prop_model_nba.py` — optional context columns in feature matrix; XGBoost meta ranker saved as `models/{segment}_meta_model.pkl`.
- `scripts/train_prop_model_cbb.py` — `optional_context_features` join; expanded `X_base`; CBB meta ranker `models/cbb_meta_model.pkl`.
- `NBA/scripts/step7_rank_props.py` — inference features for new columns; `_meta_adjust_ml_prob` after base/calibrated prob.
- `CBB/scripts/pipeline/step6_rank_props_cbb.py` — optional context columns in ML matrix; `_cbb_meta_adjust_ml_prob`.
- `ui_runner/app.py` — `sqlite3`; `/api/grades/insights` (calibration, CLV by sport/prop/tier, edge buckets).
- `ui_runner/templates/indexGrades.html` — “CLV & calibration” tab + glass-style insight cards + fetch to API.
- `run_pipeline.ps1` — `-NFLOnly`, `-GolfOnly` stub flows.
- `scripts/run_daily.ps1` — STEP A1c runs `enrich_graded_workbook_clv.py` on `outputs/<yesterday>/`.

## Notes / follow-ups

- NHL, Soccer, MLB training scripts were not fully extended in this pass; use `utils/optional_ml_context.py` and the NBA/CBB meta pattern to align them when needed.
- Meta models are only trained when the main trainer completes successfully; inference falls back to base ML prob if the meta pickle is missing.
- `graded_rows_to_clv_log` requires implied probabilities or American open/close odds on graded sheets for non-null CLV rows.
