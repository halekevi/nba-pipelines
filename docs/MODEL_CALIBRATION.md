# Model & calibration workflow

## Production model (do not replace casually)

| Artifact | AUC (holdout) | Role |
|----------|---------------|------|
| `models/edge_model_unified_pre_enrichment.pkl` | **0.7546** | **Production** — keep until enrichment retrain wins |
| `models/edge_model_unified.pkl` | 0.7371 | Manual retrain on empty enrichment features — **do not promote** |

Promotion gate: new model overall AUC **> 0.7546** on temporal holdout with enrichment columns **>10% fill** in `data/retrain_dataset.csv`.

## Calibration stack (inference order)

1. **XGBoost** → raw score  
2. **Platt** (`LogisticRegression` on holdout) → `p_platt`  
3. **Slice isotonic** (`models/edge_slice_calibrators.pkl`) — per `(sport, pick_type, direction)` when `n >= 200`  
4. **Linear scalars** (`ML_PROB_CALIBRATION_SCALARS` in `scripts/edge_predict_utils.py`)

## When stats.nba.com is up (enrichment retrain)

```powershell
py -3.14 scripts/verify_enrichment_ready.py --smoke-test
pwsh -File scripts\run_enrichment_retrain_sequence.ps1 -Date (Get-Date -Format yyyy-MM-dd)
```

Compare `models/edge_model_metadata.json` to `edge_model_metadata_pre_enrichment.json` before swapping production pickle.

## Without full retrain (graded archive only)

### 1. Refresh linear scalars (WNBA now has 200+ graded rows)

```powershell
py -3.14 scripts\recalibrate_ml_prob_scalars.py --sport WNBA --min-n 50
py -3.14 scripts\recalibrate_ml_prob_scalars.py --sport WNBA --apply
```

All sports report:

```powershell
py -3.14 scripts\recalibrate_ml_prob_scalars.py --min-n 100
```

Output: `outputs/calibration/ml_prob_scalar_recommendations.csv`

### 2. Refresh slice isotonic (no XGBoost retrain)

Uses **pre-enrichment** model + full graded history:

```powershell
py -3.14 scripts\refresh_slice_isotonic.py
```

Writes `models/edge_slice_calibrators.pkl` and `models/edge_slice_isotonic_refresh.json`.

## WNBA

- Graded archive: **200+** rows required for isotonic slice (`WNBA_SLICE_ISOTONIC_MIN_N = 200` in `train_edge_model.py`).
- Scalars were `1.0` placeholders; run `recalibrate_ml_prob_scalars.py --sport WNBA --apply` after each major model swap.

## NBA scalars

Comment in `edge_predict_utils.py`: recalibrate after enrichment retrain when `usage_pct` / `team_pace` columns survive the 60% fill filter in training.

## Still blocked

| Item | Blocker |
|------|---------|
| Enrichment retrain | stats.nba.com HTTP 500 |
| NBA/WNBA usage% in training | Same |
| Model promotion | Retrain AUC vs 0.7546 |
