# PropORACLE — project layout and path contracts

Use this when moving folders or wiring scheduled tasks. Commands for daily use live in [PROPORACLE_RUN_COMMANDS.md](PROPORACLE_RUN_COMMANDS.md).

## Repo root (canonical)

Everything assumes the **repository root** is the working directory for `run_pipeline.ps1` and for `py` invocations that use `.\scripts\...`.

| Location | Role |
|----------|------|
| `run_pipeline.ps1` | Master multi-sport pipeline (NBA, CBB, NHL, MLB, Soccer, WNBA, combined tickets) |
| `main.py` | WSGI shim: re-exports `app` from `ui_runner.app` |
| `scripts\` | Shared Python utilities, graders, ML training, combined slate builder, ticket eval (`scripts\build_ticket_eval.py`), entries harvest (`capture_entries.py`) |
| `docs\` | `CHANGELOG.md`, `DESIGN_PRINCIPLES.md`, runbooks (`PROPORACLE_RUN_COMMANDS.md`, this file), `docs\guides\` for long-form setup |
| `config\` | `requirements-pipeline.txt` (ML/grading/pipeline extra deps — not used by Docker/Railway) |
| `pyproject.toml` | **Local only:** pytest + ruff settings (replaces root `pytest.ini` / `ruff.toml`). Deploy still uses `requirements.txt` + `Dockerfile` |
| `docs\guides\` | Long-form setup and status docs (e.g. `BROWSER_FETCH_SETUP.md`, `APP_SYSTEM_STATUS.md`) |
| `archive\root-text\` | Ad hoc notes, patch snippets, and log copies moved off the repo root |
| `archive\web_exports\` | Large standalone HTML exports (e.g. old `player_prop_hitrates.html` copies) |
| `archive\legacy\` | Older root `_archive` tree (dev notes, sample actuals, zips, one-off scripts) — kept for reference |
| `local\` | **Gitignored** local runtime: put Playwright fallbacks here, e.g. `local\browser_session\` and `local\browser_session_harvest2\` (optional second profile). Legacy root `browser_session*` still works and stays ignored. |
| `data\db\` | Default location for `MyTicketPerformance.db` (ticket/entries SQLite; `*.db` is gitignored) |
| `data\reports\ticket_diversity\` | `ticket_diversity_audit_<date>.json` from `combined_slate_tickets.py` (since repo cleanup; no longer written beside `combined_slate_tickets_*.xlsx`) |
| `data\archive\` | One-off backups moved off the repo root (e.g. stray `tickets_latest_repo_root.json`, `mlb_id_cache_repo_root.csv`) |
| (avoid at root) | Do not keep sport caches (`mlb_id_cache.csv` → `MLB\`, `nhl_*` → `NHL\` or `NHL\cache\`), `schedule_cache_*.csv` (→ `NBA\`), MLB step CSVs (→ `MLB\outputs\`), or ad-hoc exports — use `archive\` or sport folders |

### Keep at repository root (hosting / tooling)

Do **not** move these without updating **Docker**, **Railway / Nixpacks**, **Procfile**, and docs: `Dockerfile`, `Procfile`, `railway.toml`, `nixpacks.toml`, `main.py` (Gunicorn `main:app`), `requirements.txt`, `run_pipeline.ps1`, `.dockerignore`. Pipeline ML deps live at `config\requirements-pipeline.txt` (install explicitly — not part of the web image).
| `outputs\<yyyy-MM-dd>\` | Dated run artifacts (copies of combined tickets, quality reports, etc.) |
| `logs\` | Long-lived logs, `git_push_log.txt` (from optional pipeline git push), and dated debug text from `organize_project_root.ps1` |
| `ui_runner\` | Flask app (`app.py`), static assets, HTML templates (including generated slate JSON/HTML) |
| `data\` | Small shared data (e.g. pipeline health log under `data\logs\`) |
| `.venv\` | Optional local virtualenv (activated by `run_pipeline.ps1` if present) |

## Sport modules (per-league trees)

Each sport keeps its own steps, caches, and docs. **Paths are not uniform** — combined logic in `run_pipeline.ps1` hard-codes the files below.

| Sport | Main scripts | Typical intermediate outputs | Combined input (when applicable) |
|-------|----------------|--------------------------------|--------------------------------|
| **NBA** | `NBA\scripts\` | `NBA\data\outputs\step*.csv`, `step7_ranked_props.xlsx` | `NBA\data\outputs\step8_all_direction_clean.xlsx` |
| **NBA 1H / 1Q** | Same tree, separate step files | `NBA\step*_nba1h_*.csv`, `NBA\step*_nba1q_*.csv` | `NBA\step8_nba1h_direction_clean.xlsx`, `NBA\step8_nba1q_direction_clean.xlsx` |
| **CBB / WCBB** | `CBB\scripts\pipeline\` | Dated intermediates: `CBB\outputs\<yyyy-MM-dd>\step*.csv` / `step6_*.xlsx`; caches: `CBB\data\cache\` | Latest slates copied to **`CBB\step6_ranked_cbb.xlsx`** and **`CBB\step6_ranked_wcbb.xlsx`** for combine / UI |
| **NHL** | `NHL\scripts\` | `NHL\outputs\step*.csv` / `step*.xlsx`, `NHL\cache\` (IDs, gamelog JSON, defense summary) | `NHL\outputs\step8_nhl_direction_clean.xlsx` (root path kept as fallback in some tools) |
| **MLB** | `MLB\scripts\` | `MLB\outputs\` | Resolved by `Resolve-MLBCleanSlateFile` (several fallback paths under `MLB\`) |
| **Soccer** | `Soccer\scripts\` | `Soccer\outputs\`, `Soccer\cache\` | `Soccer\outputs\step8_soccer_direction_clean.xlsx` |
| **WNBA** | `WNBA\` (scripts at folder root) | WNBA step files in `WNBA\` | Season-gated; see `scripts\run_wnba_pipeline.ps1` |

## Root cleanup helpers (PowerShell)

| Script | Purpose |
|--------|---------|
| `scripts\organize_project_root.ps1` | Preview / move dated `combined_slate_tickets_*`, performance CSVs into `outputs\...`, debug text into `logs\` |
| `scripts\organize_root.ps1` | Legacy mover: relocates *specific* filenames from root into `scripts\`, `scripts\grading\`, etc., and can patch `run_pipeline.ps1` (preview with no `-Execute`) |
| `scripts\organize_folder.ps1` / `scripts\organize_slateiq_root.ps1` | Additional housekeeping (read headers before running) |
| `CBB\scripts\Organize-CBB.ps1`, `NHL\scripts\Reorganize_NHL.ps1` | Sport-specific organization (NHL script is informational) |

## If you move directories or rename outputs

1. **`run_pipeline.ps1`** — Update `$NBADir`, `$CBBDir`, … and the **hard-coded combine paths** inside `Run-Combined` (NBA step8, CBB step6, NHL, Soccer, MLB resolver, NBA1H/1Q, WCBB).
2. **`scripts\Register_Daily_Task.ps1`** — Set `$PipelineRoot` to the folder that **contains** `run_pipeline.ps1`.
3. **`scripts\validate_pipeline_outputs.py`** — Expects `--repo-root`; if filenames or relative locations change, update validator rules there.
4. **`scripts\combined_slate_tickets.py`** — CLI paths are passed from `run_pipeline.ps1`; if default behavior inside the script assumes relative paths, grep for sport folder names.
5. **Python modules** — Search for `NBA\`, `CBB\`, `Join-Path`, and `step8` in `scripts\` and sport `scripts\` folders after any move.
6. **External cache** — `scripts\ensure_local_cache.py` uses `%LOCALAPPDATA%\PropORACLE\cache` (independent of repo path).
7. **Ticket DB** — `scripts\capture_entries.py` and `find_myticket_db()` helpers resolve `data\db\MyTicketPerformance.db` first, then legacy root paths.

**Git metadata:** Keep `.gitignore` and `.gitattributes` at the **repository root**; do not move them into subfolders.

## UI and deployment

| Path | Role |
|------|------|
| `ui_runner\app.py` | Flask application |
| `ui_runner\templates\` | Jinja/HTML; pipeline writes `slate_latest.json`, ticket eval artifacts here when configured |
| `ui_runner\static\` | CSS/JS served as static files |

## Single-line mental model

**Root** = orchestration (`run_pipeline.ps1`, dated `outputs\`). **`<Sport>\`** = fetch → enrich → rank → direction → tickets. **`scripts\`** = cross-sport tools (combine, grade, validate, ML).
