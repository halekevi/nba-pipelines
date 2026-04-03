#!/usr/bin/env python3
"""
Train Soccer prop ML model for step7_rank_props_soccer.py inference.

Loads all graded_soccer_*.xlsx under outputs/** and Soccer/**, builds the same
feature matrix as _apply_ml_blend (edge, hit_rate_l10, defense_tier, tier,
intel_shr_z, direction + prop_* one-hots), trains XGBoost, saves:

  <repo>/models/prop_model_soccer.pkl
  <repo>/models/prop_model_soccer_features.json

(step7 resolves models via Path(__file__).parents[2] from Soccer/scripts -> repo root.)
"""
from __future__ import annotations

import json
import re
import sqlite3
import csv
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
    from sklearn.calibration import CalibratedClassifierCV
except ImportError:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "scikit-learn", "--break-system-packages", "-q"]
    )
    from sklearn.metrics import brier_score_loss, roc_auc_score
    from sklearn.calibration import CalibratedClassifierCV

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

from ml_play_side_edge import play_side_edge

ensure_local_cache(str(ROOT))
SYNTHETIC_DB = ROOT / "data" / "cache" / "synthetic_graded.db"
MODEL_DIR = ROOT / "models"
MODEL_PATH = MODEL_DIR / "prop_model_soccer.pkl"
FEATURES_PATH = MODEL_DIR / "prop_model_soccer_features.json"
BLEND_PATH = MODEL_DIR / "prop_model_soccer_blend_weight.json"
CALIB_PATH = MODEL_DIR / "prop_model_soccer_calibrator.pkl"
METRICS_PATH = MODEL_DIR / "prop_model_soccer_metrics.json"

SYNTHETIC_RATIO_CAP = 1.0
REAL_ONLY_MODE = True

DATE_RE = re.compile(r"graded_soccer_(\d{4}-\d{2}-\d{2})\.xlsx$", re.I)


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


def _chrono_split_idx(df: pd.DataFrame, date_col: str | None) -> pd.Index:
    if date_col and date_col in df.columns:
        dd = pd.to_datetime(df[date_col], errors="coerce")
        if dd.notna().any():
            return dd.sort_values().index
    print("⚠️  [ML] No usable date column found — using index order (no shuffle).")
    return df.index


def _collect_soccer_graded_files() -> list[Path]:
    paths: list[Path] = []
    for pat in (
        ROOT / "outputs",
        ROOT / "Soccer",
        ROOT / "Soccer" / "outputs",
    ):
        if pat.is_dir():
            paths.extend(pat.rglob("graded_soccer*.xlsx"))
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


def _soccer_synthetic_to_workbook_like(syn: pd.DataFrame) -> pd.DataFrame:
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


def _sheet_usable_soccer(df: pd.DataFrame) -> bool:
    r = _first_present(df, ["result", "outcome", "grade"])
    e = _first_present(df, ["edge", "abs_edge", "edge_adj"])
    return r is not None and e is not None


def _load_soccer_frames() -> tuple[pd.DataFrame, list[str]]:
    files = _collect_soccer_graded_files()
    frames: list[pd.DataFrame] = []
    sources: list[str] = []
    for p in files:
        xl = pd.ExcelFile(p, engine="openpyxl")
        picked = False
        for sheet in xl.sheet_names:
            df = pd.read_excel(p, sheet_name=sheet, engine="openpyxl")
            if not _sheet_usable_soccer(df):
                continue
            df = df.copy()
            df["_source_file"] = str(p)
            df["_synthetic"] = 0
            df["_weight"] = 1.0
            m = DATE_RE.search(p.name)
            df["_source_date"] = m.group(1) if m else ""
            frames.append(df)
            sources.append(f"{p.name}::{sheet}")
            picked = True
            break
        if not picked:
            print(f"  (skip) No usable sheet in {p.name}")
    if not frames:
        raise FileNotFoundError(
            "No graded_soccer*.xlsx with result+edge under outputs/ or Soccer/ (non-synthetic)."
        )
    combo = pd.concat(frames, ignore_index=True)
    return combo, sources


def _audit_and_load_soccer() -> tuple[pd.DataFrame, list[str]]:
    df_real, sources = _load_soccer_frames()
    n_real = len(df_real)

    syn_raw = load_synthetic_training_data("Soccer", str(SYNTHETIC_DB))
    if REAL_ONLY_MODE or syn_raw.empty:
        df = df_real
        n_syn_used = 0
    else:
        syn_df = _soccer_synthetic_to_workbook_like(syn_raw)
        n_syn_cap = int(n_real * SYNTHETIC_RATIO_CAP)
        if n_syn_cap <= 0:
            syn_df = syn_df.iloc[0:0].copy()
        elif len(syn_df) > n_syn_cap:
            syn_df = syn_df.sample(n=n_syn_cap, random_state=42)
        n_syn_used = len(syn_df)
        df = pd.concat([df_real, syn_df], ignore_index=True)
        sources = list(sources) + ["synthetic_graded.db::synthetic"]

    print(f"Training mix — real: {n_real:,}  synthetic: {n_syn_used:,}  total: {len(df):,}")
    return df, sources


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


def _soccer_defense_tier(df: pd.DataFrame, n_teams: int = 15) -> pd.Series:
    dt_col = _first_present(df, ["def_tier", "defense_tier"])
    if dt_col:
        s = df[dt_col].astype(str).str.strip().str.lower()
        return pd.Series(
            np.where(
                s.str.contains("weak"),
                0,
                np.where(
                    s.str.contains("avg|average|mid"),
                    1,
                    np.where(s.str.contains("elite|strong|solid|above"), 2, 1),
                ),
            ),
            index=df.index,
        )
    rk_col = _first_present(df, ["overall_def_rank", "def_rank", "OVERALL_DEF_RANK"])
    if rk_col:
        r = _to_num(df[rk_col]).fillna((n_teams + 1) / 2.0)
        return pd.Series(
            np.where(
                r <= max(1, n_teams * 0.33),
                2,
                np.where(r <= max(2, n_teams * 0.66), 1, 0),
            ),
            index=df.index,
        )
    return pd.Series(1, index=df.index)


def main() -> None:
    print("=== Soccer graded data ===\n")
    df, sources = _audit_and_load_soccer()
    print(f"-> Loaded sheets from {len(sources)} files (raw rows: {len(df)})")
    for s in sources[:15]:
        print(f"    {s}")
    if len(sources) > 15:
        print(f"    ... +{len(sources) - 15} more")

    print(f"-> Columns ({len(df.columns)}): {list(df.columns)}")

    hit_col = _first_present(df, ["result", "outcome", "grade"])
    edge_col = _first_present(df, ["edge", "abs_edge"])
    if hit_col is None or edge_col is None:
        raise RuntimeError("Missing result or edge column after load.")

    hr_col = _first_present(
        df,
        [
            "line_hit_rate",
            "hit_rate",
            "hit_rate_raw",
            "hit_rate_l10",
            "Hit Rate (10g)",
            "line_hit_rate_over_ou_10",
            "line_hit_rate_over_10",
        ],
    )
    pick_col = _first_present(df, ["pick_type", "Pick Type"])
    prop_col = _first_present(df, ["prop_norm", "prop_type_norm", "prop_type", "prop"])
    dir_col = _first_present(df, ["bet_direction", "final_bet_direction", "direction"])
    intel_col = _first_present(df, ["intel_shr_z", "intel_season_hit_rate", "sharper_consensus_z"])

    if prop_col is None or dir_col is None:
        raise RuntimeError(f"Missing prop or direction column. Have: {list(df.columns)}")

    train = pd.DataFrame(index=df.index)
    train["edge"] = _to_num(df[edge_col])
    if hr_col:
        hr = _to_num(df[hr_col])
        if hr.notna().any() and hr.dropna().median() > 1.0:
            hr = hr / 100.0
        train["hit_rate_l10"] = hr
    else:
        train["hit_rate_l10"] = np.nan

    train["defense_tier"] = _soccer_defense_tier(df)
    train["tier"] = (
        _pick_type_tier_num(df[pick_col]) if pick_col else pd.Series(1, index=df.index)
    )
    train["intel_shr_z"] = _to_num(df[intel_col]) if intel_col else 0.0
    train["intel_shr_z"] = train["intel_shr_z"].fillna(0.0)
    # Match step7 prop_norm mapping so one-hot columns align with inference.
    _PROP_NORM_MAP = {
        "shots on target": "shots_on_target",
        "shotsontarget": "shots_on_target",
        "goalie saves": "saves",
        "goaliesaves": "saves",
        "passes attempted": "passes",
        "passesattempted": "passes",
        "goals allowed": "goals_allowed",
        "goalsallowed": "goals_allowed",
        "goal + assist": "goal_assist",
        "goalassist": "goal_assist",
        "shots assisted": "shots_assisted",
        "shotsassisted": "shots_assisted",
        "attempted dribbles": "attempted_dribbles",
        "attempteddribbles": "attempted_dribbles",
        "crosses": "crosses",
        "goals allowed in first 30 minutes": "goals_allowed_first30",
        "goalsallowedinfirst30minutes": "goals_allowed_first30",
    }
    train["prop_type"] = (
        df[prop_col].astype(str).str.lower().str.strip().map(lambda x: _PROP_NORM_MAP.get(x, x))
    )
    train["direction"] = _direction_num(df[dir_col]).astype(int)
    train["edge"] = play_side_edge(train["edge"], train["direction"])
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
    print("-> result / hit breakdown:")
    print(train["hit"].value_counts().sort_index())

    bw = blend_weight_for_n(n)
    if n < 200:
        print(
            f"WARNING: Only {n} graded rows for soccer — model may not be reliable.\n"
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

    date_col = _first_present(train, ["game_date", "date", "_source_date", "slate_date"])
    if date_col:
        print(f"-> Using temporal split on: {date_col}")
    order = _chrono_split_idx(train, date_col)
    Xo = X.loc[order]
    yo = y.loc[order]
    swo = sw[np.asarray([X.index.get_loc(i) for i in order], dtype=int)]
    split_idx = int(len(Xo) * 0.80)
    X_train, X_test = Xo.iloc[:split_idx], Xo.iloc[split_idx:]
    y_train, y_test = yo.iloc[:split_idx], yo.iloc[split_idx:]
    sw_train, _sw_test = swo[:split_idx], swo[split_idx:]

    if n < 500:
        base_model = XGBClassifier(
            n_estimators=50,
            max_depth=3,
            learning_rate=0.1,
            subsample=0.8,
            random_state=42,
            eval_metric="logloss",
        )
    else:
        base_model = XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            eval_metric="logloss",
        )
    model = CalibratedClassifierCV(base_model, method="isotonic", cv=5)
    model.fit(X_train, y_train, sample_weight=sw_train)

    proba = model.predict_proba(X_test)[:, 1]

    try:
        auc = roc_auc_score(y_test, proba) if y_test.nunique() > 1 else float("nan")
    except Exception:
        auc = float("nan")
    try:
        brier_raw = brier_score_loss(y_test, proba)
    except Exception:
        brier_raw = float("nan")

    feats = list(X.columns)
    base_est = None
    try:
        if hasattr(model, "calibrated_classifiers_") and model.calibrated_classifiers_:
            base_est = getattr(model.calibrated_classifiers_[0], "estimator", None)
    except Exception:
        base_est = None
    fi = None
    top5 = None
    if base_est is not None and hasattr(base_est, "feature_importances_"):
        fi = (
            pd.Series(getattr(base_est, "feature_importances_"), index=feats)
            .sort_values(ascending=False)
        )
        top5 = fi.head(5)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    FEATURES_PATH.write_text(json.dumps(feats, indent=2), encoding="utf-8")
    BLEND_PATH.write_text(json.dumps({"blend_weight": bw}, indent=2), encoding="utf-8")
    METRICS_PATH.write_text(
        json.dumps(
            {
                "auc": None if np.isnan(auc) else float(auc),
                "brier_raw": None if np.isnan(brier_raw) else float(brier_raw),
                "n_train": int(len(X_train)),
                "n_test": int(len(X_test)),
                "real_only_mode": REAL_ONLY_MODE,
                "timestamp": ts,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    log_path = MODEL_DIR / "training_log.csv"
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sport": "soccer",
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
        "auc": None if np.isnan(auc) else round(float(auc), 4),
        "brier": None if np.isnan(brier_raw) else round(float(brier_raw), 4),
        "n_features": int(len(feats)),
        "model_path": str(MODEL_PATH),
    }
    write_header = not log_path.exists()
    with log_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["timestamp", "sport", "n_train", "n_test", "auc", "brier", "n_features", "model_path"],
        )
        if write_header:
            w.writeheader()
        w.writerow(row)

    if fi is not None:
        imp_path = MODEL_DIR / "prop_model_soccer_feature_importance.json"
        imp = {str(k): float(v) for k, v in fi.to_dict().items()}
        imp_path.write_text(json.dumps(imp, indent=2), encoding="utf-8")

    print("Soccer model training complete")
    print("-----------------------------")
    print(f"  Training rows:    {len(X_train)}")
    print(f"  Test rows:        {len(X_test)}")
    print(f"  ROC-AUC:          {auc:.4f}" if not np.isnan(auc) else "  ROC-AUC:          n/a")
    print(f"  Brier (raw):      {brier_raw:.4f}" if not np.isnan(brier_raw) else "  Brier (raw):      n/a")
    print(f"  Blend weight:     {bw:.2f}")
    if top5 is not None:
        print("\n  Top 5 features:")
        for k, v in top5.items():
            print(f"  - {k}: {v:.6f}")
    print(f"\n  Saved: {MODEL_PATH}")
    print(f"  Saved: {FEATURES_PATH}")
    print(f"  Saved: {BLEND_PATH}")
    print(f"  Saved: {METRICS_PATH}")


if __name__ == "__main__":
    main()
