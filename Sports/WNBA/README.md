# WNBA pipeline (standalone)

## Entry point

From the **repository root**, run the orchestrator (sets repo paths, optional venv, dated `outputs/` tree):

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_wnba_pipeline.ps1
```

Common options:

| Flag | Purpose |
|------|---------|
| `-Date YYYY-MM-DD` | Slate / output folder date (default: today local) |
| `-SkipFetch` | Reuse existing step 1 CSV; run steps 2 onward |
| `-RefreshCache` | Rebuild ESPN player-stats cache before step 4 |
| `-Cdp URL` | Step 1 via PrizePicks browser attach (WNBA `step1_fetch_prizepicks.py`) |
| `-UsePlaywright` | Step 1 via in-browser fetch (no HTTP API) |

Default step 1 uses the same PrizePicks HTTP path as NBA (`Sports/NBA/scripts/step1_fetch_prizepicks_api.py` with `--league_id 3`). Outputs remain under this sport folder and under `outputs/<date>/`.

## Where outputs go

- **Per-step artifacts (canonical run):** `outputs/<date>/wnba/` — e.g. `step1_wnba_props.csv` through step 9 outputs.
- **Published clean sheet:** after step 8, `scripts/run_wnba_pipeline.ps1` mirrors the direction-clean workbook to `outputs/<date>/` (e.g. `step8_wnba_direction_clean_<date>.xlsx`) for dated consumers and mobile sync patterns.

## Combined slate / web tickets

`scripts/combined_slate_tickets.py` already merges **WNBA** into the full slate (same `build_combined_slate` path as NBA, NHL, etc.) when a step8 workbook is found. Resolution order matches other sports: `outputs/<date>/wnba/step8_wnba_direction_clean.xlsx`, then the dated mirror `outputs/<date>/step8_wnba_direction_clean_<date>.xlsx`, then legacy `Sports/WNBA/...` paths.

- **Full / partial master run:** `run_pipeline.ps1` runs WNBA in parallel (on/after `WNBA_SEASON_START`, or with `-ForceWNBA`) and then **combined**, so WNBA props flow into `combined_slate_tickets_<date>.xlsx` and web JSON when those steps succeed.
- **Standalone WNBA only:** run `scripts/run_wnba_pipeline.ps1`, then either `run_pipeline.ps1 -CombinedOnly ...` for the same date, or invoke `combined_slate_tickets.py` with `--date <YYYY-MM-DD>` (and your usual `--output` / `--web-outdir` flags). `-WNBAOnly` runs the WNBA pipeline and then combined automatically.

## Master pipeline

`run_pipeline.ps1 -WNBAOnly` delegates to `scripts/run_wnba_pipeline.ps1` with the same contract, then runs the combined slate step.

## UI runner

In **ui_runner**, the “WNBA Full Run” commands invoke `../../scripts/run_wnba_pipeline.ps1` from workdir `Sports/WNBA`, with `-Date` set to `{TODAY}` (substituted by the runner).
