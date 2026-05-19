# Soccer FBref xG enrichment (step4b)

## Manual HTML workflow

1. List leagues and required filenames:
   ```powershell
   py scripts/build_fbref_soccer_ref.py --list-leagues
   ```

2. Open FBref league stats in Chrome (example EPL):
   https://fbref.com/en/comps/9/stats/Premier-League-Stats

3. **File → Save As → Webpage, Complete** into:
   `data/cache/fbref_html/epl_summary.html`

4. Refresh the xG cache:
   ```powershell
   py -3.14 Sports\Soccer\scripts\step4b_attach_fbref_xg_soccer.py --refresh --season 2025-2026
   ```

5. Attach to a slate (in-place):
   ```powershell
   py -3.14 Sports\Soccer\scripts\step4b_attach_fbref_xg_soccer.py `
     --input outputs\YYYY-MM-DD\soccer\step4_soccer_with_stats.csv `
     --season 2025-2026
   ```

`run_pipeline.ps1 -Sport Soccer` runs step 4b automatically after step 4.

## Columns

| Column | Description |
|--------|-------------|
| `player_xg_per90` | npxG/90 (or xG/90) from FBref Expected block |
| `player_xag_per90` | xAG per 90 |
| `player_goals_minus_xg` | Goals minus xG (finishing luck) |
| `player_shots_per90` | Shots per 90 |
| `xg_tier` | `low` / `mid` / `high` (league cache tertiles) or `cache_miss` |
| `xg_data_source` | `fbref` or `cache_miss` |

## Validation target

With EPL summary HTML saved: `player_xg_per90` fill ≥ 40% on the slate.
`xg_tier` should be 100% populated (`cache_miss` when no FBref match).
