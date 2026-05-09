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

## Master pipeline

`run_pipeline.ps1 -WNBAOnly` delegates to `scripts/run_wnba_pipeline.ps1` with the same contract.

## UI runner

In **ui_runner**, the “WNBA Full Run” commands invoke `../../scripts/run_wnba_pipeline.ps1` from workdir `Sports/WNBA`, with `-Date` set to `{TODAY}` (substituted by the runner).
