# Local Cache

This folder contains SQLite databases used by the
Prop Oracle pipeline. These files are:

- Local only — excluded from OneDrive sync
- Not committed to git
- Auto-created by pipeline scripts on first run

## Databases

| File | Purpose |
|---|---|
| historical_actuals.db | Player game logs from ESPN/NHL API |
| player_consistency.db | Player grade profiles |
| game_lines.db | Game spreads and totals |
| synthetic_graded.db | Synthetic graded prop data |

## If you set up on a new machine

Run the full backfill to rebuild all databases:

```powershell
pwsh -File scripts\run_full_backfill.ps1
```

This will recreate all DB files from scratch.
It takes 20-60 minutes on first run.
