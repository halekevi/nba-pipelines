#!/usr/bin/env python3
"""
Train CBB prop ML model for step6_rank_props_cbb.py inference.

Loads graded_cbb_*.xlsx / graded_props_cbb_*.xlsx, reads the **Box Raw** sheet
when present (per CBB grader layout), builds the same feature matrix as
_apply_ml_blend, trains XGBoost, saves:

  <repo>/models/prop_model_cbb.pkl
  <repo>/models/prop_model_cbb_features.json

(step6 resolves models via Path(__file__).parents[3] from CBB/scripts/pipeline -> repo root.)
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
import subprocess
import sys
from pathlib import Path
from typing import Iterable

import joblib
import numpy as np
import pandas as pd

try:
    from sklearn.metrics import brier_score_loss, roc_auc_score
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import train_test_split
except ImportError:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "scikit-learn", "--break-system-packages", "-q"]
    )
    from sklearn.metrics import brier_score_loss, roc_auc_score
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import train_test_split

try:
    from xgboost import XGBClassifier
except ImportError:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "xgboost", "--break-system-packages", "-q"]
    )
    from xgboost import XGBClassifier


ROOT = Path(__file__).resolve().parent.parent
_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
from ensure_local_cache import ensure_local_cache

ensure_local_cache(str(ROOT))
SYNTHETIC_DB = ROOT / "data" / "cache" / "synthetic_graded.db"
MODEL_DIR = ROOT / "models"
MODEL_PATH = MODEL_DIR / "prop_model_cbb.pkl"
FEATURES_PATH = MODEL_DIR / "prop_model_cbb_features.json"
BLEND_PATH = MODEL_DIR / "prop_model_cbb_blend_weight.json"
CALIB_PATH = MODEL_DIR / "prop_model_cbb_calibrator.pkl"
METRICS_PATH = MODEL_DIR / "prop_model_cbb_metrics.json"

SYNTHETIC_RATIO_CAP = 1.0
REAL_ONLY_MODE = True

DATE_RE = re.compile(r"graded_(?:props_)?cbb_(\d{4}-\d{2}-\d{2})\.xlsx$", re.I)


def blend_weight_for_n(n: int) -> float:
    if n < 200:
        return 0.15
    if n < 500:
        return 0.20
    return 0.30


def _first_present(df: pd.DataFrame, options: Iterable[str]) -> str | None:
    lookup = {str(c).lower(): c for c in df.columns}
    for c in options:
        if str(c).lower() in lookup:
            return lookup[str(c).lower()]
    return None


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _collect_cbb_graded_files() -> list[Path]:
    paths: list[Path] = []
    for pat in (
        ROOT / "outputs",
        ROOT / "CBB",
        ROOT / "CBB" / "outputs",
    ):
        if pat.is_dir():
            paths.extend(pat.rglob("graded_cbb*.xlsx"))
            paths.extend(pat.rglob("graded_props_cbb*.xlsx"))
    uniq: dict[str, Path] = {}
    for p in paths:
        try:
            uniq[str(p.resolve())] = p
        except OSError:
            continue
    return sorted(uniq.values(), key=lambda x: str(x))


def load_synthetic_training_data(sport: str, db_path: str) -> pd.DataFrame:
    p = Path(db_path)
    if not p.is_file():
        return pd.DataFrame()
    conn = sqlite3.connect(str(p))
    df = pd.read_sql_query(
        "SELECT * FROM synthetic_graded_props WHERE sport = ?",
        conn,
        params=[sport],
    )
    conn.close()
    if len(df) > 0:
        df = df.copy()
        if "weight" in df.columns:
            df["_weight"] = pd.to_numeric(df["weight"], errors="coerce").fillna(0.7)
        else:
            df["_weight"] = 0.7
        print(f"  Synthetic: {len(df)} rows from DB")
    return df


def _cbb_synthetic_to_workbook_like(syn: pd.DataFrame) -> pd.DataFrame:
    if syn.empty:
        return syn
    idx = syn.index
    return pd.DataFrame(
        {
            "result": syn["result"],
            "edge": 0.0,
            "prop_type": syn["prop_type"],
            "direction": syn["direction"],
            "Pick Type": syn["tier"] if "tier" in syn.columns else pd.Series("Standard", index=idx),
            "_source_file": "synthetic_graded.db",
            "_synthetic": 1,
            "_source_date": syn["game_date"].astype(str) if "game_date" in syn.columns else "",
            "_weight": syn["_weight"],
        },
        index=idx,
    )


def rank_to_tier(rank: float, n_teams: float) -> str:
    """Mirror step6_rank_props_cbb.rank_to_tier for ML defense encoding."""
    try:
        r = float(rank)
        nt = float(n_teams)
        if nt <= 0 or np.isnan(r) or np.isnan(nt):
            return ""
    except (TypeError, ValueError):
        return ""
    pct = r / nt
    if pct <= 0.25:
        return "elite"
    elif pct <= 0.50:
        return "good"
    elif pct <= 0.75:
        return "average"
    else:
        return "weak"


def _infer_cbb_n_teams(df: pd.DataFrame) -> float:
    col = next(
        (c for c in ["OVERALL_DEF_RANK", "OPP_OVERALL_DEF_RANK", "opp_def_rank", "def_rank"] if c in df.columns),
        None,
    )
    if not col:
        return 362.0
    mx = _to_num(df[col]).max()
    if pd.isna(mx):
        return 362.0
    return 362.0 if float(mx) > 40 else 30.0


def _cbb_defense_tier_series(df: pd.DataFrame) -> pd.Series:
    """Match _ml_defense_tier_series in step6 (rank-based when possible)."""
    n_teams = _infer_cbb_n_teams(df)
    col = next(
        (c for c in ["OVERALL_DEF_RANK", "OPP_OVERALL_DEF_RANK", "opp_def_rank", "def_rank"] if c in df.columns),
        None,
    )
    if col:

        def _to_ml_tier(r):
            if pd.isna(r):
                return 1.0
            lbl = rank_to_tier(float(r), float(n_teams))
            if lbl == "weak":
                return 0.0
            if lbl == "average":
                return 1.0
            if lbl in ("good", "elite"):
                return 2.0
            return 1.0

        return _to_num(df[col]).apply(_to_ml_tier)

    dt = _first_present(df, ["def_tier", "defense_tier"])
    if dt:
        s = df[dt].astype(str).str.strip().str.lower()
        return pd.Series(
            np.where(
                s.str.contains("weak"),
                0.0,
                np.where(
                    s.str.contains("avg|average"),
                    1.0,
                    np.where(s.str.contains("good|elite|strong"), 2.0, 1.0),
                ),
            ),
            index=df.index,
        )
    return pd.Series(1.0, index=df.index)


def _load_cbb_frame(path: Path) -> pd.DataFrame | None:
    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet = "Box Raw" if "Box Raw" in xl.sheet_names else None
    if sheet is None:
        for sh in xl.sheet_names:
            t = pd.read_excel(path, sheet_name=sh, nrows=5, engine="openpyxl")
            if _first_present(t, ["result"]) and _first_present(t, ["edge"]):
                sheet = sh
                break
    if sheet is None:
        print(f"  (skip) No Box Raw / prop sheet in {path.name}")
        return None
    df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
    df = df.copy()
    df["_source_file"] = str(path)
    df["_synthetic"] = 0
    df["_weight"] = 1.0
    m = DATE_RE.search(path.name)
    df["_source_date"] = m.group(1) if m else ""
    return df


def _audit_and_load_cbb() -> pd.DataFrame:
    files = _collect_cbb_graded_files()
    real_frames: list[pd.DataFrame] = []
    for p in files:
        block = _load_cbb_frame(p)
        if block is not None:
            real_frames.append(block)
    if not real_frames:
        raise FileNotFoundError(
            "No graded_cbb*.xlsx / graded_props_cbb*.xlsx under outputs/ or CBB/ (non-synthetic)."
        )
    real_df = pd.concat(real_frames, ignore_index=True)
    n_real = len(real_df)

    syn_raw = load_synthetic_training_data("CBB", str(SYNTHETIC_DB))
    if REAL_ONLY_MODE or syn_raw.empty:
        df = real_df
        n_syn_used = 0
    else:
        syn_df = _cbb_synthetic_to_workbook_like(syn_raw)
        n_syn_cap = int(n_real * SYNTHETIC_RATIO_CAP)
        if n_syn_cap <= 0:
            syn_df = syn_df.iloc[0:0].copy()
        elif len(syn_df) > n_syn_cap:
            syn_df = syn_df.sample(n=n_syn_cap, random_state=42)
        n_syn_used = len(syn_df)
        df = pd.concat([real_df, syn_df], ignore_index=True)

    print(f"Training mix — real: {n_real:,}  synthetic: {n_syn_used:,}  total: {len(df):,}")
    return df


def _map_hit(raw: pd.Series) -> pd.Series:
    s = raw.astype(str).str.strip().str.upper()
    out = pd.Series(np.nan, index=raw.index, dtype="float64")
    out = np.where(s.eq("HIT"), 1.0, out)
    out = np.where(s.eq("MISS"), 0.0, out)
    out = np.where(s.isin(["1", "TRUE"]), 1.0, out)
    out = np.where(s.isin(["0", "FALSE"]), 0.0, out)
    return pd.Series(out, index=raw.index)


def _pick_type_tier_num(raw: pd.Series) -> pd.Series:
    s = raw.astype(str).str.lower()
    return pd.Series(
        np.where(s.str.contains("gob"), 2, np.where(s.str.contains("dem"), 0, 1)),
        index=raw.index,
    )


def _direction_num(raw: pd.Series) -> pd.Series:
    s = raw.astype(str).str.strip().str.upper()
    return pd.Series(np.where(s.eq("OVER"), 1, 0), index=raw.index)


def main() -> None:
    print("=== CBB graded data ===\n")
    df = _audit_and_load_cbb()
    print(f"-> Combined raw rows: {len(df)}")
    print(f"-> Columns ({len(df.columns)}): {list(df.columns)}")

    hit_col = _first_present(df, ["result", "outcome", "grade"])
    edge_col = _first_present(df, ["edge", "abs_edge"])
    if hit_col is None or edge_col is None:
        raise RuntimeError("Missing result or edge in CBB graded data.")

    hr_col = _first_present(
        df,
        [
            "line_hit_rate",
            "hit_rate",
            "hit_rate_l10",
            "line_hit_rate_over_ou_10",
            "line_hit_rate_over_10",
            "last10_hit_rate",
        ],
    )
    pick_col = _first_present(df, ["pick_type", "Pick Type"])
    prop_col = _first_present(df, ["prop_norm", "prop_type_norm", "prop_type"])
    dir_col = _first_present(df, ["bet_direction", "final_bet_direction", "direction"])
    intel_col = _first_present(df, ["intel_shr_z", "intel_season_hit_rate"])

    if prop_col is None or dir_col is None:
        raise RuntimeError(f"Missing prop or direction. Columns: {list(df.columns)}")

    train = pd.DataFrame(index=df.index)
    train["edge"] = _to_num(df[edge_col])
    if hr_col:
        hr = _to_num(df[hr_col])
        if hr.notna().any() and hr.dropna().median() > 1.0:
            hr = hr / 100.0
        train["hit_rate_l10"] = hr
    else:
        train["hit_rate_l10"] = np.nan

    train["defense_tier"] = _cbb_defense_tier_series(df)
    train["tier"] = (
        _pick_type_tier_num(df[pick_col]) if pick_col else pd.Series(1, index=df.index)
    )
    train["intel_shr_z"] = _to_num(df[intel_col]) if intel_col else 0.0
    train["intel_shr_z"] = train["intel_shr_z"].fillna(0.0)
    train["prop_type"] = df[prop_col].astype(str).str.strip().str.lower()
    train["direction"] = _direction_num(df[dir_col]).astype(int)
    train["hit"] = _map_hit(df[hit_col])

    train = train[train["hit"].isin([0.0, 1.0])].copy()
    train["hit"] = train["hit"].astype(int)
    train = train.dropna(subset=["edge"])
    train["hit_rate_l10"] = train["hit_rate_l10"].fillna(0.5)

    dates = df.loc[train.index, "_source_date"].astype(str)
    dr = dates[dates.str.match(r"\d{4}-\d{2}-\d{2}")]
    if len(dr):
        print(f"-> Date range (from filenames): {dr.min()} .. {dr.max()}")
    else:
        print("-> Date range: (unknown)")

    n = len(train)
    print(f"-> Decided rows (HIT/MISS only): {n}")
    print("-> hit label breakdown:")
    print(train["hit"].value_counts().sort_index())

    bw = blend_weight_for_n(n)
    if n < 200:
        print(
            f"WARNING: Only {n} graded rows for cbb — model may not be reliable.\n"
            f"Proceeding with ML_BLEND_WEIGHT = {bw}\n"
        )
    elif n < 500:
        print(f"-> Using ML_BLEND_WEIGHT = {bw} (medium sample)\n")
    else:
        print(f"-> Using ML_BLEND_WEIGHT = {bw} (full sample)\n")

    if n < 50:
        raise RuntimeError(f"Too few decided rows to train (n={n}).")

    X_base = train[
        ["edge", "hit_rate_l10", "defense_tier", "tier", "intel_shr_z", "direction"]
    ].copy()
    X_prop = pd.get_dummies(train["prop_type"], prefix="prop", dtype=float)
    X = pd.concat([X_base, X_prop], axis=1).fillna(0.0)
    y = train["hit"].astype(int)

    strat = y if y.nunique() > 1 else None
    if "_weight" in df.columns:
        sw = pd.to_numeric(df.loc[train.index, "_weight"], errors="coerce").fillna(1.0).to_numpy()
    else:
        sw = np.where(df.loc[train.index, "_synthetic"].to_numpy() > 0, 0.7, 1.0)

    X_train_all, X_test, y_train_all, y_test, sw_train_all, _sw_test = train_test_split(
        X, y, sw, test_size=0.20, random_state=42, stratify=strat
    )
    strat_train = y_train_all if y_train_all.nunique() > 1 else None
    X_train, X_cal, y_train, y_cal, sw_train, _sw_cal = train_test_split(
        X_train_all,
        y_train_all,
        sw_train_all,
        test_size=0.20,
        random_state=42,
        stratify=strat_train,
    )

    if n < 500:
        model = XGBClassifier(
            n_estimators=50,
            max_depth=3,
            learning_rate=0.1,
            subsample=0.8,
            random_state=42,
            eval_metric="logloss",
        )
    else:
        model = XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            eval_metric="logloss",
        )
    model.fit(X_train, y_train, sample_weight=sw_train)

    proba = model.predict_proba(X_test)[:, 1]
    cal_input = model.predict_proba(X_cal)[:, 1]
    calibrator = None
    calibration_type = "none"
    try:
        if len(X_cal) >= 1000:
            calibrator = IsotonicRegression(out_of_bounds="clip")
            calibrator.fit(cal_input, y_cal)
            calibration_type = "isotonic"
        else:
            calibrator = LogisticRegression()
            calibrator.fit(cal_input.reshape(-1, 1), y_cal)
            calibration_type = "sigmoid"
    except Exception:
        calibrator = None
        calibration_type = "none"

    try:
        auc = roc_auc_score(y_test, proba) if y_test.nunique() > 1 else float("nan")
    except Exception:
        auc = float("nan")
    try:
        brier_raw = brier_score_loss(y_test, proba)
    except Exception:
        brier_raw = float("nan")
    proba_cal_test = np.asarray(proba, dtype=float).copy()
    if calibrator is not None:
        try:
            if hasattr(calibrator, "predict_proba"):
                proba_cal_test = calibrator.predict_proba(proba_cal_test.reshape(-1, 1))[:, 1]
            else:
                proba_cal_test = calibrator.predict(proba_cal_test)
        except Exception:
            proba_cal_test = np.asarray(proba, dtype=float)
    try:
        brier_calibrated = brier_score_loss(y_test, proba_cal_test)
    except Exception:
        brier_calibrated = float("nan")

    feats = list(X.columns)
    fi = pd.Series(model.feature_importances_, index=feats).sort_values(ascending=False)
    top5 = fi.head(5)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    FEATURES_PATH.write_text(json.dumps(feats, indent=2), encoding="utf-8")
    BLEND_PATH.write_text(json.dumps({"blend_weight": bw}, indent=2), encoding="utf-8")
    if calibrator is not None:
        joblib.dump(calibrator, CALIB_PATH)
    elif CALIB_PATH.exists():
        CALIB_PATH.unlink()
    METRICS_PATH.write_text(
        json.dumps(
            {
                "auc": None if np.isnan(auc) else float(auc),
                "brier_raw": None if np.isnan(brier_raw) else float(brier_raw),
                "brier_calibrated": None if np.isnan(brier_calibrated) else float(brier_calibrated),
                "calibration_type": calibration_type,
                "n_train": int(len(X_train)),
                "n_cal": int(len(X_cal)),
                "n_test": int(len(X_test)),
                "real_only_mode": REAL_ONLY_MODE,
                "timestamp": ts,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print("CBB model training complete")
    print("-----------------------------")
    print(f"  Training rows:    {len(X_train)}")
    print(f"  Calibration rows: {len(X_cal)}")
    print(f"  Test rows:        {len(X_test)}")
    print(f"  ROC-AUC:          {auc:.4f}" if not np.isnan(auc) else "  ROC-AUC:          n/a")
    print(f"  Brier (raw):      {brier_raw:.4f}" if not np.isnan(brier_raw) else "  Brier (raw):      n/a")
    print(f"  Brier (cal test): {brier_calibrated:.4f}" if not np.isnan(brier_calibrated) else "  Brier (cal test): n/a")
    print(f"  Blend weight:     {bw:.2f}")
    print(f"  Calibration:      {calibration_type}")
    print("\n  Top 5 features:")
    for k, v in top5.items():
        print(f"  - {k}: {v:.6f}")
    print(f"\n  Saved: {MODEL_PATH}")
    print(f"  Saved: {FEATURES_PATH}")
    print(f"  Saved: {BLEND_PATH}")
    if calibrator is not None:
        print(f"  Saved: {CALIB_PATH}")
    print(f"  Saved: {METRICS_PATH}")


if __name__ == "__main__":
    main()
