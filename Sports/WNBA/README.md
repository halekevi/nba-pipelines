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
| `-CdpWhenListening` | Skip HTTP when Chrome debug port 9222 is up (warm session) |
| `-HttpOnly` | Do not fall back to CDP after HTTP fails |
| `-UsePlaywright` | Step 1 via in-browser fetch (no HTTP API) |

Default step 1 uses **`Sports/WNBA/step1_fetch_prizepicks.py`** (HTTP: `/leagues` warmup, `curl_cffi` **chrome131**, session waves on 403). On failure it retries the NBA API script with **chrome120**, then CDP on port 9222 if Chrome is running.

## PrizePicks DataDome (Press & Hold)

Direct HTTP step1 often gets **403** or the site shows **Press & Hold**. Use **Chrome CDP** (real logged-in session):

```powershell
# 1) From repo root — opens Chrome on port 9222 (WNBA board = league_id 3, not 7)
pwsh -File scripts\launch_prizepicks_chrome_cdp.ps1 -OpenBoard -LeagueId 3

# 2) Complete login + human check in that window (board must load)

# 3) Fetch props + full pipeline
pwsh -File scripts\run_wnba_pipeline.ps1 -Cdp http://127.0.0.1:9222 -Date 2026-05-16
```

If HTTP fails, the runner tries **chrome120** via the NBA API script, then **auto-retries CDP** when port 9222 is up (unless `-NoCdpFallback` or `-HttpOnly`). Use `-CdpWhenListening` to skip HTTP when debug Chrome is already warm.  
**L5 / L10 stats** come from **ESPN** (`wnba_espn_cache.csv`), not PrizePicks — backfill 2025 with `scripts/backfill_wnba_espn_range.py`.

## Rolling stats (L5 / last season)

`step4_fetch_player_stats.py` **by default** combines **current and previous** `SEASON` rows in `wnba_espn_cache.csv` and uses a **~420-day** lookback so early-season slates still get up to five prior games for L5. Pass `--no-include-prior-season-stats` to restrict to one season only.

If 2025 rows are missing from the cache, run a one-time backfill, for example:

`python scripts/backfill_wnba_espn_range.py --from 2025-05-01 --to 2025-10-31 --season 2025`

To pull only the **tail** of the 2025 calendar (postseason / October — good for filling L5 with “recent 2025” games without re-fetching the whole season):

`python scripts/backfill_wnba_espn_range.py --preset finals-2025 --season 2025`

Other presets: `full-2025` (May–Oct), `late-2025` (Sep–Oct). ESPN is queried **by date**, not “exactly five games per player”; step4 then takes the last five **cached** games vs the current line (after dropping outings under **20 minutes**, which better matches PrizePicks L5/L10).

## Where outputs go

- **Per-step artifacts (canonical run):** `outputs/<date>/wnba/` — e.g. `step1_wnba_props.csv` through step 9 outputs.
- **Published clean sheet:** after step 8, `scripts/run_wnba_pipeline.ps1` mirrors the direction-clean workbook to `outputs/<date>/` (e.g. `step8_wnba_direction_clean_<date>.xlsx`) for dated consumers and mobile sync patterns.
- **Slate Explorer / mobile:** after step 9, the same runner calls `scripts/publish_wnba_slate_to_ui.py`, which **merges** WNBA rows into `ui_runner/templates/slate_latest.json` and `mobile/www/slate_latest.json` (and writes `slate_sport_wnba.json`) without wiping other sports. Run that script manually with `--date` if you need to refresh only the web JSON.
- **Matchup Edge panel:** the WNBA slate card in Slate Explorer includes a collapsible **Matchup Edge** block (team × stat category vs tonight’s opponent defense). Data is built by `scripts/build_wnba_matchup_edge_json.py` (uses `wnba_top3_vs_defense.csv` + `wnba_defense_summary.csv` + slate). API: `GET /api/wnba/matchup-edge`; static fallback: `mobile/www/data/wnba_matchup_edge.json`.

## Combined slate / web tickets

`scripts/combined_slate_tickets.py` already merges **WNBA** into the full slate (same `build_combined_slate` path as NBA, NHL, etc.) when a step8 workbook is found. Resolution order matches other sports: `outputs/<date>/wnba/step8_wnba_direction_clean.xlsx`, then the dated mirror `outputs/<date>/step8_wnba_direction_clean_<date>.xlsx`, then legacy `Sports/WNBA/...` paths.

- **Full / partial master run:** `run_pipeline.ps1` runs WNBA in parallel (on/after `WNBA_SEASON_START`, or with `-ForceWNBA`) and then **combined**, so WNBA props flow into `combined_slate_tickets_<date>.xlsx` and web JSON when those steps succeed.
- **Standalone WNBA only:** run `scripts/run_wnba_pipeline.ps1`, then either `run_pipeline.ps1 -CombinedOnly ...` for the same date, or invoke `combined_slate_tickets.py` with `--date <YYYY-MM-DD>` (and your usual `--output` / `--web-outdir` flags). `-WNBAOnly` runs the WNBA pipeline and then combined automatically.

## Master pipeline

`run_pipeline.ps1 -WNBAOnly` delegates to `scripts/run_wnba_pipeline.ps1` with the same contract, then runs the combined slate step.

## UI runner

In **ui_runner**, the “WNBA Full Run” commands invoke `../../scripts/run_wnba_pipeline.ps1` from workdir `Sports/WNBA`, with `-Date` set to `{TODAY}` (substituted by the runner).
