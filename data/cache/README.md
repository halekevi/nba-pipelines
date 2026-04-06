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
| proporacle_income.db | Settled bets + predictions for `/dashboard/income` (auto-created; schema from `proporacle/data/schema/`) |

### Income dashboard (`proporacle_income.db`)

On first open, the UI applies `ddl.sql` and `views.sql` automatically. If the DB has no `bet_result` rows:

- **Local:** demo slates are inserted automatically unless you set `PROPORACLE_INCOME_SEED_DEMO=0`.
- **Railway / hosted:** set `PROPORACLE_INCOME_SEED_DEMO=1` once for sample charts, or ingest real results.

Manual seed (any environment):

```bash
python scripts/seed_income_dashboard_demo.py
```

## If you set up on a new machine

Run the full backfill to rebuild all databases:

```powershell
pwsh -File scripts\run_full_backfill.ps1
```

This will recreate all DB files from scratch.
It takes 20-60 minutes on first run.
