# Prop ML models (local artifacts)

Binary model files (`*.pkl`) are **not** committed to Git (see repo `.gitignore`). After clone or when a sport’s model is missing, regenerate from the repo root.

**Environment (Windows PowerShell):**

```powershell
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
```

**Trainers (run from repository root):**

| Sport | Command | Outputs (under `models/`) |
|--------|-----------|----------------------------|
| NBA full game | `py -3.14 scripts/train_prop_model_nba.py` | `prop_model_nba.pkl`, `prop_model_nba_features.json`, `prop_model_nba_blend_weight.json`, `prop_model_nba_calibrator.pkl`, `prop_model_nba_metrics.json` |
| NBA 1st quarter | `py -3.14 scripts/train_prop_model_nba1q.py` | `prop_model_nba1q.*` (same suffix pattern) |
| NBA 1st half | `py -3.14 scripts/train_prop_model_nba1h.py` | `prop_model_nba1h.*` |
| CBB | `py -3.14 scripts/train_prop_model_cbb.py` | `prop_model_cbb.pkl`, `*_features.json`, `*_blend_weight.json`, `prop_model_cbb_calibrator.pkl`, `prop_model_cbb_metrics.json` |
| NHL | `py -3.14 scripts/train_prop_model_nhl.py` | `prop_model_nhl.pkl`, `*_features.json`, `*_blend_weight.json`, `prop_model_nhl_calibrator.pkl`, `prop_model_nhl_metrics.json`, plus `prop_model_nhl_metadata.json` |
| Soccer | `py -3.14 scripts/train_prop_model_soccer.py` | `prop_model_soccer.pkl`, `*_features.json`, `*_blend_weight.json`, `prop_model_soccer_calibrator.pkl`, `prop_model_soccer_metrics.json` |

**Data dependencies:** each trainer needs graded slates (Excel under `outputs/`, sport folders, etc.) with resolved **HIT/MISS** labels. Optional synthetic rows live in `data/cache/synthetic_graded.db` when `REAL_ONLY_MODE` is off in a given script; the default is **real-only** training.

**Automation:** `run_pipeline.ps1` runs a missing-`*.pkl` check before the parallel sport jobs and invokes the matching trainer when a primary model file is absent.
