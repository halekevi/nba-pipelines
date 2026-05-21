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
    _direction_series,
    build_feature_vector,
    fill_minutes_cv_median_by_sport,
)

# Shared with step7b_edge_score. Linear multipliers (provisional; sigmoid slices → isotonic later).
ML_PROB_CALIBRATION_SCALARS: dict[tuple[str, str, str], float] = {
    # NBA scalars: recalibrate after usage_pct + pace + injury context retrain (step4b/c/d).
    ("NBA", "standard", "OVER"): 0.7999,
    ("NBA", "goblin", "OVER"): 0.8624,
    # Demon+OVER excluded from calibration — unbookable (drop_demon_over_rows).
    ("NBA", "standard", "UNDER"): 1.029,
    # NHL — provisional (thin samples; isotonic pass needed)
    ("NHL", "standard", "OVER"): 1.6485,
    ("NHL", "standard", "UNDER"): 0.7309,
    ("NHL", "goblin", "OVER"): 2.2124,
    ("NHL", "demon", "OVER"): 1.8855,
    # MLB
    ("MLB", "standard", "OVER"): 1.2019,
    ("MLB", "goblin", "OVER"): 1.2258,
    ("MLB", "demon", "OVER"): 1.4152,
    # Soccer — step7b sport key is SOCCER (not report label "Soccer")
    ("SOCCER", "standard", "OVER"): 2.778,
    ("SOCCER", "goblin", "OVER"): 0.6942,
    ("SOCCER", "demon", "OVER"): 2.121,
    # WNBA — recalibrated from graded archive (scripts/recalibrate_ml_prob_scalars.py --sport WNBA)
    ("WNBA", "standard", "OVER"): 1.0906,
    ("WNBA", "standard", "UNDER"): 0.7745,
    ("WNBA", "goblin", "OVER"): 1.2207,
    ("WNBA", "demon", "OVER"): 2.2358,
    ("MLB", "standard", "UNDER"): 0.9687,
    ("SOCCER", "goblin", "UNDER"): 1.0811,
    ("SOCCER", "standard", "UNDER"): 1.751,
    ("CBB", "goblin", "OVER"): 0.8054,
    ("CBB", "standard", "OVER"): 0.9911,
    ("CBB", "standard", "UNDER"): 0.8652,
    ("NBA1Q", "goblin", "OVER"): 0.5677,
    ("NBA1Q", "standard", "OVER"): 0.5943,
    ("NBA1Q", "standard", "UNDER"): 0.6676,
    ("NBA1H", "standard", "OVER"): 0.5539,
    ("NBA1H", "standard", "UNDER"): 0.6046,
    ("TENNIS", "goblin", "OVER"): 0.9648,
    ("TENNIS", "standard", "OVER"): 0.9827,
}

_SLICE_CAL_PATH: Path | None = None
_SLICE_CAL_MTIME: float | None = None
_SLICE_CAL_BUNDLE: dict | None = None


def _load_slice_calibrators(models_dir: Path) -> dict | None:
    """Load ``edge_slice_calibrators.pkl`` if present; reload when file mtime changes."""
    global _SLICE_CAL_PATH, _SLICE_CAL_MTIME, _SLICE_CAL_BUNDLE
    p = models_dir / "edge_slice_calibrators.pkl"
    if not p.is_file():
        _SLICE_CAL_PATH, _SLICE_CAL_MTIME, _SLICE_CAL_BUNDLE = None, None, None
        return None
    try:
        mt = float(p.stat().st_mtime)
    except OSError:
        return _SLICE_CAL_BUNDLE
    if _SLICE_CAL_BUNDLE is not None and _SLICE_CAL_PATH == p.resolve() and _SLICE_CAL_MTIME == mt:
        return _SLICE_CAL_BUNDLE
    _SLICE_CAL_BUNDLE = joblib.load(p)
    _SLICE_CAL_PATH = p.resolve()
    _SLICE_CAL_MTIME = mt
    return _SLICE_CAL_BUNDLE


def apply_ml_prob_post_calibration(
    p_platt: np.ndarray,
    sport_norm: str,
    pick_lower: pd.Series,
    dir_upper: pd.Series,
    models_dir: Path,
) -> np.ndarray:
    """
    Post-process Platt-calibrated positive-class probabilities.

    Order: ``p_platt`` → per-slice isotonic (if key in ``edge_slice_calibrators.pkl``)
    → linear ``ML_PROB_CALIBRATION_SCALARS`` (default 1.0) → clip [0, 1].

    Isotonic regressors are fit on a stratified **train-fit** subset (disjoint from the
    Platt calibration slice and the untouched test set in ``train_edge_model.py``).
    """
    p = np.asarray(p_platt, dtype=float).copy()
    n = len(p)
    if n == 0:
        return p
    spu = str(sport_norm or "").strip().upper()
    bundle = _load_slice_calibrators(models_dir)
    cal_map: dict = {}
    if isinstance(bundle, dict):
        cal_map = bundle.get("calibrators") or {}
    if cal_map:
        ptv = pick_lower.astype(str).str.strip().str.lower()
        drv = dir_upper.astype(str).str.strip().str.upper()
        for key, iso in cal_map.items():
            if not isinstance(key, tuple) or len(key) != 3:
                continue
            s0, p0, d0 = str(key[0]).upper(), str(key[1]).lower(), str(key[2]).upper()
            if s0 != spu:
                continue
            m = ptv.eq(p0) & drv.eq(d0)
            if not bool(m.any()):
                continue
            idx = np.where(m.to_numpy())[0]
            pv = p[idx]
            try:
                p[idx] = np.asarray(iso.predict(pv), dtype=float)
            except Exception:
                continue
    adj = np.ones(n, dtype=float)
    ptv = pick_lower.astype(str).str.strip().str.lower()
    drv = dir_upper.astype(str).str.strip().str.upper()
    for (s0, p0, d0), mult in ML_PROB_CALIBRATION_SCALARS.items():
        if spu != s0:
            continue
        m = ptv.eq(p0) & drv.eq(d0)
        adj[m.to_numpy()] = float(mult)
    p *= adj
    return np.clip(p, 0.0, 1.0)


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
    spu = str(sport_for_model or "").strip().upper()
    p_platt = np.asarray(model.predict_proba(X)[:, 1], dtype=float)
    dirs_u = _direction_series(df2).astype(str).str.strip().str.upper()
    pt_l = df2.get("pick_type", pd.Series("", index=df2.index)).astype(str).str.strip().str.lower()
    p_adj = apply_ml_prob_post_calibration(p_platt, spu, pt_l, dirs_u, mdir)
    ml_prob = pd.Series(p_adj, index=df2.index, dtype=float)
    edge_col = pd.to_numeric(df2.get("edge", pd.Series(0.0, index=df2.index)), errors="coerce").fillna(0.0)
    implied_prob = 1.0 / (1.0 + np.exp(-edge_col.clip(-20, 20)))
    comp = pd.to_numeric(
        df2.get("composite_hit_rate", df2.get("line_hit_rate", pd.Series(0.5, index=df2.index))),
        errors="coerce",
    ).fillna(0.5)
    edge_score = ml_prob - implied_prob
    blended = 0.3 * ml_prob + 0.7 * comp
    return ml_prob, edge_score, blended

