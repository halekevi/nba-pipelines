#!/usr/bin/env python3
"""
Refresh edge_slice_calibrators.pkl from graded history without retraining XGBoost.

Loads an existing edge_model_unified.pkl (default: pre-enrichment backup), rebuilds
feature rows from graded exports, and refits per-slice isotonic calibrators on train data.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

_SCRIPTS = Path(__file__).resolve().parent
_REPO = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from edge_feature_engineering import (  # noqa: E402
    FEATURE_COLUMNS,
    build_feature_vector,
    fill_minutes_cv_median_by_sport,
)
from edge_ml_bundle import EdgeCalibratedModel  # noqa: E402
from train_edge_model import (  # noqa: E402
    DEFAULT_SLICE_ISOTONIC_MIN_N,
    _fit_slice_isotonic_calibrators,
    load_all_graded,
)


def _rows_to_training_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Build per-sport feature matrices and concat (mirrors train_edge_model core path)."""
    parts: list[pd.DataFrame] = []
    for sp in sorted(raw["sport"].astype(str).str.upper().unique()):
        sub = raw[raw["sport"].astype(str).str.upper() == sp].copy()
        if sub.empty:
            continue
        sub["y"] = pd.to_numeric(sub["_hit_y"], errors="coerce")
        sub = sub[sub["y"].isin([0, 1])].copy()
        if len(sub) < 30:
            continue
        try:
            feat = build_feature_vector(sub, sp)
        except Exception as exc:
            print(f"  skip {sp}: build_feature_vector failed: {exc}")
            continue
        if feat.empty:
            continue
        feat = fill_minutes_cv_median_by_sport(feat)
        feat["sport"] = sp
        feat["y"] = sub["y"].values[: len(feat)]
        if "pick_type" not in feat.columns and "pick_type" in sub.columns:
            feat["pick_type"] = sub["pick_type"].astype(str).values[: len(feat)]
        parts.append(feat)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--model",
        type=Path,
        default=_REPO / "models" / "edge_model_unified_pre_enrichment.pkl",
        help="Calibrated model pickle to use for Platt probabilities",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=_REPO / "models",
        help="Where to write edge_slice_calibrators.pkl",
    )
    ap.add_argument("--isotonic-min-n", type=int, default=DEFAULT_SLICE_ISOTONIC_MIN_N)
    ap.add_argument("--include-synthetic", action="store_true")
    ap.add_argument("--no-recursive", action="store_true")
    args = ap.parse_args()

    model_path = args.model.expanduser().resolve()
    if not model_path.is_file():
        print(f"Model not found: {model_path}")
        return 1

    print(f"Loading model: {model_path}")
    calibrated: EdgeCalibratedModel = joblib.load(model_path)

    models_dir = model_path.parent
    feat_path = models_dir / "edge_model_features.json"
    if model_path.name.endswith("_pre_enrichment.pkl"):
        meta_pre = models_dir / "edge_model_metadata_pre_enrichment.json"
        if meta_pre.is_file():
            meta = json.loads(meta_pre.read_text(encoding="utf-8"))
            features_active = list(meta.get("feature_columns") or [])
        else:
            features_active = json.loads(feat_path.read_text(encoding="utf-8"))
    elif feat_path.is_file():
        features_active = json.loads(feat_path.read_text(encoding="utf-8"))
    else:
        print(f"Missing feature list: {feat_path}")
        return 1

    print(f"  feature columns: {len(features_active)}")

    print("Loading graded history...")
    raw, _ = load_all_graded(
        _REPO,
        recursive_outputs=not args.no_recursive,
        dedupe=True,
        include_synthetic=args.include_synthetic,
    )
    if raw.empty:
        print("No graded rows loaded.")
        return 1

    print(f"  raw rows: {len(raw):,}")
    tr = _rows_to_training_frame(raw)
    if tr.empty:
        print("Could not build training frame from graded data.")
        return 1

    feats = [c for c in features_active if c in tr.columns]
    missing = [c for c in features_active if c not in tr.columns]
    if missing:
        print(f"  warning: {len(missing)} model features absent in graded frame (median-filled)")
    for c in features_active:
        if c not in tr.columns:
            tr[c] = np.nan
    for c in features_active:
        ser = pd.to_numeric(tr[c], errors="coerce")
        med = float(np.nanmedian(ser.to_numpy(dtype=float))) if ser.notna().any() else 0.0
        if np.isnan(med):
            med = 0.0
        tr[c] = ser.fillna(med)
    if not feats:
        print("No overlapping feature columns in training frame.")
        return 1

    y_train = tr["y"].astype(int)
    models_dir = args.out_dir.expanduser().resolve()
    models_dir.mkdir(parents=True, exist_ok=True)

    fitted, skipped = _fit_slice_isotonic_calibrators(
        tr,
        y_train,
        features_active,
        calibrated,
        models_dir,
        isotonic_min_n=args.isotonic_min_n,
    )

    meta_path = models_dir / "edge_slice_isotonic_refresh.json"
    meta_path.write_text(
        json.dumps(
            {
                "model_source": str(model_path),
                "training_rows": int(len(tr)),
                "fitted_slices": fitted,
                "skipped": skipped,
                "isotonic_min_n": args.isotonic_min_n,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {models_dir / 'edge_slice_calibrators.pkl'}")
    print(f"Wrote {meta_path}")
    print(f"  fitted={len(fitted)} skipped={len(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
