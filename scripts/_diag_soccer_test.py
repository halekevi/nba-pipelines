#!/usr/bin/env python3
"""Soccer test-set diagnostics (temporal split aligned with train_edge_model)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

from train_edge_model import (  # noqa: E402
    TEST_HOLDOUT_FRAC,
    _event_date_series_for_raw,
    _load_retrain_csv_as_raw,
    _normalize_sport_series,
    _prepare_features,
    _split_train_calib_test_holdout,
)

print("=" * 72)
print("1) Soccer test set size and label balance (temporal split)")
print("=" * 72)

raw = _load_retrain_csv_as_raw(_ROOT / "data" / "retrain_dataset.csv", None, source_label="retrain")
raw["sport"] = _normalize_sport_series(raw["sport"])
raw = raw.loc[raw["_hit_y"].isin([0.0, 1.0])].copy()
raw["_stress_event_dt"] = _event_date_series_for_raw(raw, temporal_date_column="file_date")[0]

df = _prepare_features(raw)
df["y"] = df["_hit_y"].astype(int)
df = (
    df.dropna(subset=["_stress_event_dt"])
    .sort_values("_stress_event_dt", kind="mergesort")
    .reset_index(drop=True)
)

tr_fit, cal, te = _split_train_calib_test_holdout(df, temporal=True)
soc_test = te[te["sport"].astype(str).str.strip().str.upper().eq("SOCCER")].copy()
soc_all = df[df["sport"].astype(str).str.strip().str.upper().eq("SOCCER")]

te_dt = pd.to_datetime(te["_stress_event_dt"], errors="coerce")
print(f"Full dataset Soccer rows: {len(soc_all):,}")
print(f"Temporal test window: {te_dt.min()} .. {te_dt.max()}")
print(f"Soccer test n: {len(soc_test):,}")
print("Label dist (test):")
print(soc_test["y"].value_counts().rename({0: "MISS", 1: "HIT"}))
hit_pct = 100.0 * soc_test["y"].mean() if len(soc_test) else 0.0
print(f"Hit rate (test): {hit_pct:.1f}%")

chr = pd.to_numeric(soc_test.get("composite_hit_rate"), errors="coerce")
print(f"CHR null pct (test): {chr.isna().mean():.3f}")
print(f"CHR historical source pct: {(soc_test.get('chr_source', '') == 'historical').mean():.3f}")

if "def_tier_encoded" in soc_test.columns:
    dt = pd.to_numeric(soc_test["def_tier_encoded"], errors="coerce")
    print(f"def_tier_encoded == 0 (missing fill) pct: {(dt == 0).mean():.3f}")
    print(f"defense_known mean: {pd.to_numeric(soc_test.get('defense_known'), errors='coerce').mean():.3f}")
else:
    print("def_tier_encoded: col missing")

# Raw CSV slice (user-style cutoff check)
csv = pd.read_csv(_ROOT / "data" / "retrain_dataset.csv", low_memory=False, encoding="utf-8-sig")
csv["file_date"] = pd.to_datetime(csv["file_date"], errors="coerce")
n = len(df)
cut_idx = int(n * (1 - TEST_HOLDOUT_FRAC))
cut_dt = df.iloc[cut_idx]["_stress_event_dt"] if cut_idx < n else pd.NaT
soc_csv_test = csv[
    (csv["sport"].astype(str).str.strip().str.upper().eq("SOCCER")
     & (csv["file_date"] >= cut_dt))
]
print(f"\nCSV Soccer rows with file_date >= split cutoff ({cut_dt}): {len(soc_csv_test):,}")
if "result_binary" in soc_csv_test.columns:
    rb = pd.to_numeric(soc_csv_test["result_binary"], errors="coerce")
    print("CSV label dist (result_binary):")
    print(rb.value_counts())
chr_csv = pd.to_numeric(soc_csv_test.get("composite_hit_rate"), errors="coerce")
print(f"CSV CHR null pct: {chr_csv.isna().mean():.3f}")
def_raw = soc_csv_test.get("def_tier")
if def_raw is not None:
    print(f"CSV def_tier null/empty pct: {def_raw.isna().mean() + (def_raw.astype(str).str.strip() == '').mean():.3f}")

print("\n" + "=" * 72)
print("2) Prod model feature importance (top 20)")
print("=" * 72)

feat_path = _ROOT / "models" / "edge_model_features.json"
feats = json.loads(feat_path.read_text(encoding="utf-8"))
prod = joblib.load(_ROOT / "models" / "edge_model_unified.pkl")
booster = prod.booster
imp = pd.Series(booster.feature_importances_, index=booster.get_booster().feature_names).sort_values(ascending=False)
print(imp.head(20).to_string())

print("\n" + "=" * 72)
print("3) Prod Soccer AUC on same temporal test split")
print("=" * 72)

features_active = [f for f in feats if f in te.columns]
X_te = te[features_active].astype(float)
y_te = te["y"].astype(int)
soc_mask = te["sport"].astype(str).str.strip().str.upper().eq("SOCCER")
X_soc = X_te.loc[soc_mask]
y_soc = y_te.loc[soc_mask]

p_soc = prod.predict_proba(X_soc)[:, 1]
auc_prod = roc_auc_score(y_soc, p_soc)
print(f"Prod Soccer AUC on temporal test: {auc_prod:.4f}")
print(f"Soccer test n: {len(y_soc):,} | label mean (hit rate): {y_soc.mean():.3f}")

# New model was reverted; compare to last train log AUC 0.6828 if candidate exists
cand = _ROOT / "models" / "edge_model_unified_candidate.pkl"
if cand.is_file():
    new_m = joblib.load(cand)
    p_new = new_m.predict_proba(X_soc)[:, 1]
    print(f"Candidate Soccer AUC on same test: {roc_auc_score(y_soc, p_new):.4f}")
else:
    print("(No candidate.pkl — new model was 0.6828 on last train run, not saved)")

print("\nGate reference: Overall>0.7454 Soccer>=0.7029 NHL>=0.6355")
