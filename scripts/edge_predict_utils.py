#!/usr/bin/env python3
"""Unified edge model inference (shared by step7b and graded backfill)."""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import joblib
import numpy as np
import pandas as pd

import edge_ml_bundle  # noqa: F401 — pickle root

from edge_feature_engineering import (  # type: ignore
    FEATURE_COLUMNS,
    build_feature_vector,
    fill_minutes_cv_median_by_sport,
)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def augment_graded_box_raw_for_edge(df: pd.DataFrame) -> pd.DataFrame:
    """Map Box Raw / slate_grader column names onto edge_feature_engineering inputs."""
    out = df.copy()
    if "composite_hit_rate" not in out.columns and "last5_hit_rate" in out.columns:
        out["composite_hit_rate"] = pd.to_numeric(out["last5_hit_rate"], errors="coerce")
    if "line_hit_rate_over_ou_5" not in out.columns and "last5_hit_rate" in out.columns:
        out["line_hit_rate_over_ou_5"] = pd.to_numeric(out["last5_hit_rate"], errors="coerce")
    if "stat_last5_avg" not in out.columns and "last5_avg" in out.columns:
        out["stat_last5_avg"] = pd.to_numeric(out["last5_avg"], errors="coerce")
    if "stat_season_avg" not in out.columns and "season_avg" in out.columns:
        out["stat_season_avg"] = pd.to_numeric(out["season_avg"], errors="coerce")
    return out


def graded_filename_sport_to_train_sport(s: str) -> str:
    u = str(s or "").strip().lower()
    if u in ("nba1q", "nba1h", "wnba"):
        return "NBA"
    if u == "wcbb":
        return "CBB"
    if u == "football":
        return "SOCCER"
    return u.upper()


def predict_unified_edge_scores(
    df: pd.DataFrame,
    *,
    sport_for_model: str,
    models_dir: Path | None = None,
) -> tuple[pd.Series, pd.Series, pd.Series] | None:
    """
    Returns (ml_prob, edge_score, blended_score) aligned to df.index.
    sport_for_model: NBA, CBB, NHL, SOCCER, MLB (not nba1q / wcbb — normalize first).
    """
    root = repo_root()
    mdir = models_dir or (root / "models")
    model_path = mdir / "edge_model_unified.pkl"
    feat_path = mdir / "edge_model_features.json"
    if not model_path.is_file() or not feat_path.is_file():
        return None
    feats = json.loads(feat_path.read_text(encoding="utf-8"))
    aug = augment_graded_box_raw_for_edge(df)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        df2 = build_feature_vector(aug, sport_for_model)
    if len(df2) == 0:
        return None
    df2 = fill_minutes_cv_median_by_sport(df2)
    for c in feats:
        if c not in df2.columns:
            df2[c] = np.nan
        ser = pd.to_numeric(df2[c], errors="coerce")
        med = float(np.nanmedian(ser.to_numpy(dtype=float))) if ser.notna().any() else 0.0
        if np.isnan(med):
            med = 0.0
        df2[c] = ser.fillna(med)
    try:
        model = joblib.load(model_path)
    except Exception:
        return None
    X = df2[feats].astype(float)
    ml_prob = pd.Series(model.predict_proba(X)[:, 1], index=df2.index, dtype=float)
    edge_col = pd.to_numeric(df2.get("edge", pd.Series(0.0, index=df2.index)), errors="coerce").fillna(0.0)
    implied_prob = 1.0 / (1.0 + np.exp(-edge_col.clip(-20, 20)))
    comp = pd.to_numeric(
        df2.get("composite_hit_rate", df2.get("line_hit_rate", pd.Series(0.5, index=df2.index))),
        errors="coerce",
    ).fillna(0.5)
    edge_score = ml_prob - implied_prob
    blended = 0.3 * ml_prob + 0.7 * comp
    return ml_prob, edge_score, blended

