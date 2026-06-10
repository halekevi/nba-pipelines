#!/usr/bin/env python3
"""Train ticket-level cash-probability models (combined + per-sport)."""
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

from ticket_ml_sports import (
    MIN_ROWS_BUCKET,
    TICKET_ML_SPORT_KEYS,
    dataset_path_for_sport,
    filter_training_rows,
    min_rows_for_sport,
    model_artifact_paths,
    sport_display_name,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = ROOT / "data" / "ml" / "ticket_training_dataset.csv"
MODELS_DIR = ROOT / "models"


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
        "legs_wnba",
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


def _fit_calibrated_logit(X_train: pd.DataFrame, y_train: pd.Series):
    base = LogisticRegression(
        C=1.0,
        max_iter=2000,
        class_weight="balanced",
        random_state=42,
    )
    model = CalibratedClassifierCV(base, method="isotonic", cv=5)
    model.fit(X_train, y_train)
    return model


def _bucket_name(n_legs: float | int | None) -> str:
    try:
        n = int(n_legs or 0)
    except Exception:
        n = 0
    if n <= 2:
        return "2leg"
    if n == 3:
        return "3leg"
    return "4plus"


def train_ticket_model_from_df(
    df: pd.DataFrame,
    *,
    sport_key: str = "combined",
    target: str = "label_cash",
    bucketed: bool = True,
    dry_run: bool = False,
    models_dir: Path | None = None,
) -> dict[str, Any]:
    """Train and persist ticket model artifacts; return summary dict."""
    key = str(sport_key or "combined").strip().lower()
    paths = model_artifact_paths(key, models_dir)
    min_rows = min_rows_for_sport(key)

    if target not in df.columns:
        raise RuntimeError(f"Missing target column: {target}")

    y_raw = _to_num(df[target])
    m = y_raw.isin([0, 1])
    df = df.loc[m].copy()
    y = y_raw.loc[m].astype(int)

    summary: dict[str, Any] = {
        "sport_key": key,
        "sport_label": sport_display_name(key),
        "trained": False,
        "reason": "",
        "n_rows": int(len(df)),
        "min_rows": int(min_rows),
    }

    if len(df) < min_rows:
        summary["reason"] = f"insufficient rows ({len(df)} < {min_rows})"
        return summary

    X, feat_cols = _build_features(df)
    train_df, test_df = _chrono_split(df, frac_train=0.80)
    train_idx = train_df.index
    test_idx = test_df.index
    X_train = X.loc[train_idx]
    X_test = X.loc[test_idx]
    y_train = y.loc[train_idx]
    y_test = y.loc[test_idx]

    summary.update(
        {
            "n_train": int(len(X_train)),
            "n_test": int(len(X_test)),
            "positive_rate_train": float(y_train.mean()) if len(y_train) else None,
            "positive_rate_test": float(y_test.mean()) if len(y_test) else None,
            "feature_count": int(len(feat_cols)),
        }
    )

    print(
        f"→ [{key}] rows={len(df)} train={len(X_train)} test={len(X_test)} "
        f"pos_rate train={y_train.mean():.3f} test={y_test.mean():.3f}"
    )

    if dry_run:
        summary["trained"] = False
        summary["reason"] = "dry_run"
        return summary

    model = _fit_calibrated_logit(X_train, y_train)
    p_test = model.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, p_test) if y_test.nunique() > 1 else float("nan")
    brier = brier_score_loss(y_test, p_test)

    mdir = paths["model"].parent
    mdir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, paths["model"], compress=3)
    paths["features"].write_text(json.dumps(feat_cols, indent=2), encoding="utf-8")

    meta: dict[str, Any] = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "sport_key": key,
        "sport_label": sport_display_name(key),
        "model_type": "CalibratedClassifierCV(LogisticRegression)",
        "target": target,
        "n_rows": int(len(df)),
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
        "positive_rate_train": float(y_train.mean()),
        "positive_rate_test": float(y_test.mean()),
        "auc_test": float(auc) if auc == auc else None,
        "brier_test": float(brier),
        "feature_count": int(len(feat_cols)),
        "bucketed_enabled": bool(bucketed),
        "model_path": str(paths["model"]),
        "features_path": str(paths["features"]),
    }

    bucket_meta: dict[str, Any] = {}
    if bucketed:
        nlegs = pd.to_numeric(df.get("n_legs", 0), errors="coerce").fillna(0).astype(int)
        bucket_series = nlegs.map(_bucket_name)
        for bname in ("2leg", "3leg", "4plus"):
            bm = bucket_series.eq(bname)
            d_b = df.loc[bm].copy()
            y_b = y.loc[bm].copy()
            if len(d_b) < MIN_ROWS_BUCKET or y_b.nunique() < 2:
                bucket_meta[bname] = {
                    "trained": False,
                    "reason": f"insufficient rows or classes (rows={len(d_b)}, classes={int(y_b.nunique())})",
                }
                continue
            X_b = X.loc[d_b.index].copy()
            tr_b, te_b = _chrono_split(d_b, frac_train=0.80)
            tr_idx = tr_b.index
            te_idx = te_b.index
            Xb_train = X_b.loc[tr_idx]
            Xb_test = X_b.loc[te_idx]
            yb_train = y_b.loc[tr_idx]
            yb_test = y_b.loc[te_idx]
            if len(Xb_train) < min(40, MIN_ROWS_BUCKET) or yb_train.nunique() < 2:
                bucket_meta[bname] = {
                    "trained": False,
                    "reason": f"insufficient train rows or classes (rows={len(Xb_train)})",
                }
                continue
            mb = _fit_calibrated_logit(Xb_train, yb_train)
            pb = mb.predict_proba(Xb_test)[:, 1]
            auc_b = roc_auc_score(yb_test, pb) if yb_test.nunique() > 1 else float("nan")
            brier_b = brier_score_loss(yb_test, pb)
            outp = paths[bname]
            joblib.dump(mb, outp, compress=3)
            bucket_meta[bname] = {
                "trained": True,
                "model_path": str(outp),
                "n_rows": int(len(d_b)),
                "n_train": int(len(Xb_train)),
                "n_test": int(len(Xb_test)),
                "auc_test": float(auc_b) if auc_b == auc_b else None,
                "brier_test": float(brier_b),
            }
        meta["bucket_models"] = bucket_meta

    paths["metadata"].write_text(json.dumps(meta, indent=2), encoding="utf-8")

    summary.update(
        {
            "trained": True,
            "auc_test": float(auc) if auc == auc else None,
            "brier_test": float(brier),
            "model_path": str(paths["model"]),
            "metadata_path": str(paths["metadata"]),
            "bucket_models": bucket_meta,
        }
    )
    print(f"  Saved [{key}] AUC={auc:.4f} Brier={brier:.4f} -> {paths['model']}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Train ticket-level cash-probability model from backfilled ticket dataset.")
    ap.add_argument("--input-csv", default=str(DEFAULT_DATASET), help="Ticket training CSV from build_ticket_training_dataset.py")
    ap.add_argument("--sport", default="combined", choices=[*TICKET_ML_SPORT_KEYS], help="Sport scope for this model.")
    ap.add_argument("--target", default="label_cash", choices=["label_cash", "label_paid"], help="Binary target column.")
    ap.add_argument("--dry-run", action="store_true", help="Print dataset summary only; do not train/write model.")
    ap.add_argument("--no-bucketed", action="store_true", help="Skip per leg-count bucket models.")
    args = ap.parse_args()

    path = Path(args.input_csv)
    if not path.is_file():
        raise FileNotFoundError(f"Training dataset not found: {path}")

    df = pd.read_csv(path, low_memory=False)
    sport_key = str(args.sport).strip().lower()
    if sport_key != "combined":
        df = filter_training_rows(df, sport_key)
        print(f"→ Filtered to sport={sport_key} ({sport_display_name(sport_key)}): {len(df)} rows")

    summary = train_ticket_model_from_df(
        df,
        sport_key=sport_key,
        target=str(args.target),
        bucketed=not bool(args.no_bucketed),
        dry_run=bool(args.dry_run),
    )
    if not summary.get("trained") and summary.get("reason") not in ("dry_run",):
        raise RuntimeError(summary.get("reason") or "training failed")
    if args.dry_run:
        print("[DRY RUN] Done (no model written).")


if __name__ == "__main__":
    main()
