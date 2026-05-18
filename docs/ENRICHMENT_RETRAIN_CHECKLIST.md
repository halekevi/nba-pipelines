# Enrichment retrain checklist (machine)

Repo must be at `3ce77ec5` or later (`git pull`).

## 1. Smoke test

```powershell
py -3.14 scripts/verify_enrichment_ready.py --smoke-test
```

Expect: `200` and `400+ rows`. Stop if not.

## 2. Populate caches

```powershell
py -3.14 Sports\NBA\scripts\step4b_attach_nba_context.py `
  --input Sports\NBA\step4_with_stats.csv `
  --output Sports\NBA\step4_with_stats.csv `
  --season 2024-25 --refresh

py -3.14 Sports\WNBA\scripts\step4b_attach_wnba_context.py `
  --input Sports\WNBA\step4_wnba_stats.csv `
  --output Sports\WNBA\step4_wnba_stats.csv `
  --season 2025 --refresh
```

## 3. Full pipeline (enrichment → step8 snapshots)

Use `-Sport` shorthand or `*Only` switches:

```powershell
.\run_pipeline.ps1 -Sport NBA -SkipFetch
.\run_pipeline.ps1 -Sport WNBA -SkipFetch
.\run_pipeline.ps1 -Sport MLB -SkipFetch
.\run_pipeline.ps1 -Sport NHL -SkipFetch
```

WNBA can also use: `.\scripts\run_wnba_pipeline.ps1 -SkipFetch`

## 4. Rebuild retrain CSV (required)

```powershell
py -3.14 scripts/build_retrain_dataset.py
```

## 5. Verify before retrain

```powershell
py -3.14 scripts/verify_enrichment_ready.py
```

All enrichment columns must be present (not MISSING). OK or LOW fill is fine.

## 6. Retrain

```powershell
py -3.14 scripts/train_edge_model.py `
  --input-csv data/retrain_dataset.csv `
  --temporal-split `
  --output-model models/edge_model_unified.pkl
```

Backup: `models/edge_model_unified_pre_enrichment.pkl`

## Paste back

1. Smoke test line  
2. Cache key counts  
3. Fill-rate table from verify script  
4. Overall AUC, MLB AUC, NBA AUC, top-10 features, any `Dropped low-fill` log lines  
