# CBB Bart Torvik team ratings

Manual workflow (no automated barttorvik.com scraping).

## Refresh reference CSV

1. Open https://barttorvik.com/trank.php (or export season team table).
2. Save as `Sports/CBB/data/reference/torvik_team_ratings.csv` with columns: `team`, `adj_o`, `adj_d`, `adj_em`, `tempo`.
3. Or download JSON: `https://barttorvik.com/2025_team_results.json` and convert (see repo helper in torvik_ratings_api).

## Pipeline

```powershell
py -3.14 Sports\CBB\scripts\step3c_attach_torvik_context.py --refresh --season 2025-26
py -3.14 Sports\CBB\scripts\step3c_attach_torvik_context.py `
  --input outputs\<date>\cbb\step3b_with_def_rankings_cbb.csv `
  --output outputs\<date>\cbb\step3b_with_def_rankings_cbb.csv `
  --season 2025-26
```

`run_pipeline.ps1` runs step 3c after defense rankings (step 3b).

## Validation

- `team_adj_em` fill ≥ 70% when reference CSV is present
- `torvik_data_source`: `torvik` vs `cache_miss`

WCBB uses a separate path (not wired in v1).
