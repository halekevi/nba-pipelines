# College Football (CFB) Pipeline

Mirrors the **CBB** layout: PrizePicks fetch → normalize → defense → ESPN IDs → boxscore stats → rank (step6 xlsx). Outputs land under `outputs/<date>/cfb/` and are picked up automatically by `combined_slate_tickets.py`.

## PrizePicks

- **League ID:** `15` (CFB on PrizePicks API)
- Step 1: `scripts/pipeline/step1_pp_cfb_scraper.py`

## Run

From repo root:

```powershell
.\run_pipeline.ps1 -CFBOnly
.\run_pipeline.ps1 -CFBOnly -SkipFetch   # reuse existing step1 CSV
```

Full parallel run includes CFB when the slate date is in **Aug–Jan** (off-season skipped Feb–Jul).

## Outputs

| Step | File |
|------|------|
| 1 | `outputs/<date>/cfb/step1_cfb.csv` |
| 2 | `outputs/<date>/cfb/step2_cfb.csv` |
| 3 | `outputs/<date>/cfb/step3b_with_def_rankings_cfb.csv` |
| 4 | `outputs/<date>/cfb/step3_cfb.csv` |
| 5 | `outputs/<date>/cfb/step5b_cfb.csv` |
| 6 | `outputs/<date>/cfb/step6_ranked_cfb.xlsx` ← combined / UI |

## Data files

- `data/reference/ncaa_football_athletes_master.csv` — bootstrap ESPN athlete map (grows over time)
- `data/reference/cfb_def_rankings.csv` — team defense ranks (placeholder from CBB template until CFB-specific table is built)
- `data/cache/cfb_boxscore_cache.csv` — ESPN college-football game logs (populate via backfill + step5b)

## Rolling stats (L5 / L10) — backfill prior season

CFB players only play ~12 games per year, so week 1–4 slates need **last season’s games** in the cache for full L5/L10.

**One-time (or preseason) backfill** from repo root:

```powershell
# 2025 regular season + bowls
python scripts/backfill_cfb_espn_range.py --preset full-2025 --season 2025

# Prior year (for early fall 2026 slates)
python scripts/backfill_cfb_espn_range.py --preset full-2024 --season 2024
```

Other presets: `regular-2025`, `bowls-2025`. Custom range: `--from 2025-08-23 --to 2026-01-19 --season 2025`.

Then run the pipeline (`-CFBOnly` or full parallel). Step 5 scans **200 days** back from `--date` and reads the shared cache.

## Regular season unit rankings (conference)

Pass/rush **offense** and **defense** are ranked **within each FBS conference** with quintile tiers: Elite, Above Avg, Avg, Below Avg, Weak.

| Unit | Rank 1 means |
|------|----------------|
| Pass / rush offense | Most yards per game |
| Pass / rush defense | Fewest yards allowed per game |

**Build / refresh** (ESPN regular-season byteam stats):

```powershell
cd Sports\CFB
py -3.14 scripts\build_cfb_unit_rankings.py --season 2025
```

Output: `data/reference/cfb_team_unit_rankings.csv`

The pipeline runs this automatically as **CFB Step 3a**, then **Step 3b** attaches columns to each prop row (`team_pass_off_tier`, `opp_pass_def_tier`, `matchup_pass_off_vs_def_tier`, etc.).

## Postseason (playoffs / CFP)

CFB does not use March Madness, but the pipeline treats **College Football Playoff + bowl season** like CBB tournament context:

- Metadata: `utils/cfb_playoff_metadata.py` — edit `CFB_CFP_2026` when the 12-team bracket is set (seeds + round: `CFP_BYE`, `CFP_FIRST`, `CFP_QUARTER`, `CFP_SEMI`, `CFP_CHAMP`)
- Step 6 sets `is_playoff_game`, `playoff_round`, and slightly dampens yard/TD props in playoff games (tighter scripts)
- Combined slate / UI: `is_tournament_game` mirrors `is_playoff_game` for parity with CBB; columns `team_seed`, `opp_seed`, `playoff_round`

## Notes

- ESPN sport path: `football/college-football`
- Combined slate key: `cfb` in `slate_latest.json` (sport label `CFB`)
- Replace `cfb_def_rankings.csv` with real FBS defensive efficiency when available for better opp_def tiers
