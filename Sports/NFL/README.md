# NFL Pipeline

## Status: Under Construction

## Target: Active for 2026 regular season (Sept 9)

## Key Differences from NBA/MLB

- Snap count % replaces minutes for role certainty
- Weather affects passing props (wind > 15mph = reduce)
- Game script matters: blowouts = RB heavy, passing game shrinks
- Bye weeks: no props posted for teams on bye
- Thursday/Monday games: smaller PrizePicks boards

## Phase 1 (Now): Scaffold

## Phase 2 (May-June): NFL-specific data sources

## Phase 3 (June-July): Historical backfill 2024-2025

## Phase 4 (August): Preseason live testing

## Phase 5 (September): Production activation

### Running scaffold steps locally

PrizePicks `league_id` for NFL is **9** (see `scripts/capture_entries.py`). Step1 does not require `NFL_PIPELINE_ACTIVE`.

Steps 2, 4, and 6 are gated until you opt in:

```powershell
set NFL_PIPELINE_ACTIVE=1
cd NFL
py -3.14 scripts\step2_clean_props.py
py -3.14 scripts\step4_defense_rankings.py
py -3.14 scripts\step6_historical_hit_rates.py
```

### Outputs (paths relative to `NFL/`)

| Step | Output |
|------|--------|
| 1 | `data/outputs/step1_pp_props_today.csv` |
| 2 | `data/outputs/step2_clean_props.csv` |
| 4 | `data/defense_rankings.csv` |
| 4b | `data/nfl_team_last5.csv` — each team’s last **5** completed regular-season games (ESPN scoreboards; PF/PA, W-L, opponents) |
| 8 (target) | `outputs/step8_nfl_direction_clean.xlsx` — same layout as **NHL** (`NHL/outputs/step8_…`), **not** a flat file under repo `outputs/`. Matches `NFL_SLATE` in `ui_runner/app.py`. |

### Web / `slate_latest.json`

- Combined `write_slate_json` emits the **`nfl`** key (lowercase) alongside other sports; `ui_runner` normalizes any legacy mixed-case keys when loading slate JSON.
- Ticket legs may show **`sport`: `"NFL"`** (uppercase); the home page maps that to panel key `nfl` and CSS `sp-nfl` via `.toLowerCase()`.
| 6 | `data/outputs/step6_hit_rates.csv` |
