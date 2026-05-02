# Canonical Pipelines and Scripts

This file defines the primary scripts to run and maintain. Prefer these over machine-specific variants or backups.

## Daily production pipeline

- `run_pipeline.ps1` - top-level daily orchestration entrypoint.
- `scripts/run_daily.ps1` - core daily data pipeline (includes WNBA via `run_pipeline.ps1`; NFL/CBB are season/input-gated).
- `scripts/run_post_pipeline_grader.ps1` - post-pipeline grading follow-up.

## Ticket generation and grading

- `scripts/combined_slate_tickets.py` - canonical ticket generator.
- `scripts/combined_ticket_grader.py` - canonical ticket grader.
- `scripts/run_grader.ps1` - canonical grader wrapper for daily/manual runs.

## Backtest and model comparison

- `scripts/backtest_ticket_generation_dates.py` - grade archived generated tickets across date ranges.
- `scripts/replay_new_generator_backtest.py` - replay generator on historical days and grade outputs.
- `scripts/ab_new_vs_old_tickets_last10.py` - 10-day old vs new arm comparison.

## ML training and evaluation

- `scripts/build_ticket_training_dataset.py` - builds ticket-level training/eval dataset from graded history.
- `scripts/train_ticket_model.py` - trains ticket-level cash probability model.
- `scripts/evaluate_ticket_model.py` - evaluates EV-only vs model rerank by date and top-N.

## Sport-specific helpers still in active use

- `scripts/run_wnba_pipeline.ps1` (steps 1–8 + **step7b** edge overlay like NBA, then step9 local tickets; writes `step8_wnba_direction_clean.xlsx` and copies to `WNBA/data/outputs/` for `Run-Combined`)
- `scripts/run_wnba_grader.ps1`
- `Soccer/scripts/run_soccer_pipeline.ps1`
- `Tennis/scripts/tennis_light_pipeline.py`

## Archival policy

- Machine-specific script variants (for example `*-Travel-PC*`, `*-DESKTOP-*`) are archived under `archive/script_cleanup/`.
- Backup files (`*.bak`) should not live beside canonical scripts; archive them or delete after validation.
- When adding a new orchestrator, update this file and deprecate/repoint older entrypoints in the same change.
