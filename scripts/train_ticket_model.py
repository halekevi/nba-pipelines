#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = ROOT / "data" / "ml" / "ticket_training_dataset.csv"
MODELS_DIR = ROOT / "models"
MODEL_PATH = MODELS_DIR / "ticket_model.pkl"
FEATURES_PATH = MODELS_DIR / "ticket_model_features.json"
META_PATH = MODELS_DIR / "ticket_model_metadata.json"


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _chrono_split(df: pd.DataFrame, frac_train: float = 0.80) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = df.copy()
    if "slate_date" in d.columns:
        ts = pd.to_datetime(d["slate_date"], errors="coerce")
        if ts.notna().any():
            d = d.assign(_dt=ts).sort_values(["_dt"], ascending=True).drop(columns="_dt")
    n = len(d)
    cut = max(1, min(n - 1, int(n * frac_train)))
    return d.iloc[:cut].copy(), d.iloc[cut:].copy()


def _build_features(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    num_cols = [
        "n_legs",
        "is_flex_structure",
        "sports_in_ticket",
        "legs_nba",
        "legs_cbb",
        "legs_nhl",
        "legs_soccer",
        "legs_mlb",
        "pick_standard_count",
        "pick_goblin_count",
        "pick_demon_count",
        "ticket_objective_score",
        "ev_power",
        "est_ev",
        "flat_ev",
        "payout_multiplier",
        "power_payout",
        "flex_payout",
        "est_win_prob",
        "predicted_payout_mult",
        "predicted_p_win",
        "predicted_ev",
        "avg_hit_rate_leg",
        "avg_ml_prob_leg",
        "min_ml_prob_leg",
        "max_ml_prob_leg",
        "std_ml_prob_leg",
        "avg_leg_prob_used",
        "min_leg_prob_used",
        "avg_edge_leg",
        "min_edge_leg",
        "max_edge_leg",
        "avg_abs_edge_leg",
        "avg_rank_score_leg",
        "min_rank_score_leg",
        "avg_context_score_leg",
        "avg_intel_hit_rate_leg",
    ]
    for c in num_cols:
        if c not in df.columns:
            df[c] = np.nan
        df[c] = _to_num(df[c])
    df[num_cols] = df[num_cols].fillna(0.0)

    cat_cols = []
    for c in ("group_type", "dominant_sport"):
        if c in df.columns:
            cat_cols.append(c)
        else:
            df[c] = ""
            cat_cols.append(c)

    X_num = df[num_cols].astype(float)
    X_cat = pd.get_dummies(df[cat_cols].astype(str), prefix=cat_cols, dtype=float)
    X = pd.concat([X_num, X_cat], axis=1).fillna(0.0)
    return X, list(X.columns)


def main() -> None:
    ap = argparse.ArgumentParser(description="Train ticket-level cash-probability model from backfilled ticket dataset.")
    ap.add_argument("--input-csv", default=str(DEFAULT_DATASET), help="Ticket training CSV from build_ticket_training_dataset.py")
    ap.add_argument("--target", default="label_cash", choices=["label_cash", "label_paid"], help="Binary target column.")
    ap.add_argument("--dry-run", action="store_true", help="Print dataset summary only; do not train/write model.")
    args = ap.parse_args()

    path = Path(args.input_csv)
    if not path.is_file():
        raise FileNotFoundError(f"Training dataset not found: {path}")

    df = pd.read_csv(path, low_memory=False)
    if args.target not in df.columns:
        raise RuntimeError(f"Missing target column: {args.target}")

    y_raw = _to_num(df[args.target])
    m = y_raw.isin([0, 1])
    df = df.loc[m].copy()
    y = y_raw.loc[m].astype(int)

    if len(df) < 80:
        raise RuntimeError(f"Not enough decided ticket rows to train robustly (rows={len(df)}).")

    X, feat_cols = _build_features(df)
    train_df, test_df = _chrono_split(df, frac_train=0.80)
    train_idx = train_df.index
    test_idx = test_df.index
    X_train = X.loc[train_idx]
    X_test = X.loc[test_idx]
    y_train = y.loc[train_idx]
    y_test = y.loc[test_idx]

    print(f"-> Input rows: {len(df)} | train={len(X_train)} test={len(X_test)} | target={args.target}")
    print(f"-> Positive rate: train={y_train.mean():.3f} test={y_test.mean():.3f}")
    print(f"-> Feature count: {len(feat_cols)}")

    if args.dry_run:
        print("[DRY RUN] Done (no model written).")
        return

    # Robust baseline for tabular binary outcomes; calibrated for probability quality.
    base = LogisticRegression(
        C=1.0,
        max_iter=2000,
        class_weight="balanced",
        random_state=42,
    )
    model = CalibratedClassifierCV(base, method="isotonic", cv=5)
    model.fit(X_train, y_train)

    p_test = model.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, p_test)
    brier = brier_score_loss(y_test, p_test)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH, compress=3)
    FEATURES_PATH.write_text(json.dumps(feat_cols, indent=2), encoding="utf-8")

    meta: dict[str, Any] = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_type": "CalibratedClassifierCV(LogisticRegression)",
        "target": args.target,
        "n_rows": int(len(df)),
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
        "positive_rate_train": float(y_train.mean()),
        "positive_rate_test": float(y_test.mean()),
        "auc_test": float(auc),
        "brier_test": float(brier),
        "feature_count": int(len(feat_cols)),
        "input_csv": str(path),
    }
    META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"\nAUC={auc:.4f}  Brier={brier:.4f}")
    print(f"Saved model -> {MODEL_PATH}")
    print(f"Saved features -> {FEATURES_PATH}")
    print(f"Saved metadata -> {META_PATH}")


if __name__ == "__main__":
    main()
