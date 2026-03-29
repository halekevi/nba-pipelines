#!/usr/bin/env python3
"""Train unified XGBoost edge classifier + Platt calibration on graded history."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

from edge_feature_engineering import (
    FEATURE_COLUMNS,
    build_feature_vector,
    fill_minutes_cv_median_by_sport,
)

SCRIPT_NAME = "train_edge_model"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _hit_column(df: pd.DataFrame) -> pd.Series | None:
    for c in ("hit", "result", "graded"):
        if c in df.columns:
            s = pd.to_numeric(df[c], errors="coerce")
            return s
    return None


def _norm_sport_folder(name: str) -> str | None:
    m = str(name or "").strip().upper()
    if m in ("NBA", "CBB", "NHL", "SOCCER", "MLB"):
        return "SOCCER" if m == "SOCCER" else m
    return None


def _discover_graded_files(root: Path) -> list[tuple[str, Path]]:
    sports = ("NBA", "CBB", "NHL", "Soccer", "MLB")
    out: list[tuple[str, Path]] = []
    for sp in sports:
        sp_key = "SOCCER" if sp.lower() == "soccer" else sp.upper()
        dirs = [
            root / sp / "outputs" / "graded",
            root / sp / "outputs",
            root / "outputs" / "graded",
            root / "outputs",
        ]
        seen: set[Path] = set()
        for d in dirs:
            if not d.is_dir():
                continue
            for pat in ("*graded*.csv", "*graded*.xlsx"):
                for p in d.glob(pat):
                    if p.is_file() and p.resolve() not in seen:
                        seen.add(p.resolve())
                        out.append((sp_key, p))
            for p in d.glob("combined_tickets_graded_*.xlsx"):
                if p.is_file() and p.resolve() not in seen:
                    seen.add(p.resolve())
                    out.append((sp_key, p))
    return out


def _read_table(path: Path, sport_hint: str) -> pd.DataFrame | None:
    try:
        if path.suffix.lower() == ".csv":
            return pd.read_csv(path, low_memory=False, encoding="utf-8-sig")
        if path.suffix.lower() in (".xlsx", ".xlsm"):
            xl = pd.ExcelFile(path)
            sheet = xl.sheet_names[0]
            for cand in (sport_hint, sport_hint.upper(), sport_hint.lower(), "Sheet1", "ALL"):
                if cand and cand in xl.sheet_names:
                    sheet = cand
                    break
            return pd.read_excel(path, sheet_name=sheet)
    except Exception as e:
        print(f"  [WARN] Failed to read {path}: {e}")
    return None


def load_all_graded(root: Path) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    per_file_log: list[str] = []
    for sp_hint, path in _discover_graded_files(root):
        df = _read_table(path, sp_hint)
        if df is None or df.empty:
            continue
        hit = _hit_column(df)
        if hit is None:
            per_file_log.append(f"  skip (no hit column): {path}")
            continue
        df = df.copy()
        df["_hit_y"] = hit
        df["_hit_y"] = df["_hit_y"].where(df["_hit_y"].isin([0, 1]), np.nan)
        if "sport" not in df.columns:
            parts = [p for p in path.parts if _norm_sport_folder(p)]
            if parts:
                df["sport"] = _norm_sport_folder(parts[0]) or sp_hint
            else:
                df["sport"] = sp_hint
        else:
            df["sport"] = df["sport"].astype(str).str.strip().str.upper()
            df["sport"] = df["sport"].replace({"SOC": "SOCCER", "SOCCER": "SOCCER"})
        df["_source_path"] = str(path)
        rows.append(df)
        per_file_log.append(f"  loaded {len(df)} rows from {path} (sport={df['sport'].iloc[0]})")
    for line in per_file_log:
        print(line)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def _normalize_sport_series(s: pd.Series) -> pd.Series:
    m = s.astype(str).str.strip().str.upper()
    return m.replace({"SOC": "SOCCER", "FOOTBALL": "SOCCER"})


def _prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["sport"] = _normalize_sport_series(df["sport"])
    parts: list[pd.DataFrame] = []
    for sp in df["sport"].dropna().unique():
        sp_str = str(sp).strip().upper()
        sub = df.loc[df["sport"] == sp].copy()
        parts.append(build_feature_vector(sub, sp_str))
    out = pd.concat(parts, ignore_index=True)
    out = fill_minutes_cv_median_by_sport(out)

    drop_m = (
        out["composite_hit_rate"].isna()
        & out["hit_rate_L5"].isna()
        & out["hit_rate_L10"].isna()
    )
    out = out.loc[~drop_m].copy()

    enc_cols = (
        "tier_encoded",
        "pick_type_encoded",
        "direction_encoded",
        "def_tier_encoded",
        "sport_encoded",
        "role_type_encoded",
    )
    for c in enc_cols:
        if c in out.columns:
            out[c] = _to_num_safe(out[c]).fillna(0.0)

    for c in FEATURE_COLUMNS:
        if c not in out.columns:
            out[c] = np.nan
        if c not in enc_cols:
            med = out.groupby("sport_encoded")[c].transform("median")
            out[c] = _to_num_safe(out[c]).fillna(med)
            out[c] = _to_num_safe(out[c]).fillna(float(_to_num_safe(out[c]).median()))
    return out


def _to_num_safe(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def main() -> None:
    print(f"[PropORACLE-{SCRIPT_NAME}] Starting...")
    root = _repo_root()
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", type=Path, default=root)
    args = ap.parse_args()
    root = Path(args.repo_root).resolve()
    models_dir = root / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    raw = load_all_graded(root)
    if raw.empty:
        print("[ERROR] No graded files with hit labels found.")
        return

    raw["sport"] = _normalize_sport_series(raw["sport"])
    raw = raw.loc[raw["_hit_y"].isin([0.0, 1.0])].copy()

    sport_counts = raw.groupby("sport").size()
    print("\nRows per sport (raw):")
    print(sport_counts.to_string())

    df = _prepare_features(raw)
    df["y"] = df["_hit_y"].astype(int)

    skip_sports: list[str] = []
    for sp in sorted(df["sport"].unique()):
        n = int((df["sport"] == sp).sum())
        if n < 50:
            skip_sports.append(f"{sp}: {n} rows")
            df = df.loc[df["sport"] != sp].copy()
    if skip_sports:
        print("\n[WARN] Skipping sports with <50 rows:")
        for s in skip_sports:
            print(f"  {s}")
    if df.empty:
        print("[ERROR] No sports left with enough rows.")
        return

    y = df["y"].astype(int)
    pos = int((y == 1).sum())
    neg = int((y == 0).sum())
    spw = (neg / pos) if pos > 0 else 1.0

    for sp in sorted(df["sport"].unique()):
        sub = df["sport"] == sp
        o = int(df.loc[sub, "direction_encoded"].eq(1.0).sum())
        u = int(df.loc[sub, "direction_encoded"].eq(0.0).sum())
        print(f"  {sp} OVER={o} UNDER={u} (direction_encoded)")

    strat = df["sport"].astype(str) + "_" + df["direction_encoded"].astype(int).astype(str)
    vc = strat.value_counts()
    if strat.nunique() < 2 or int(vc.min()) < 2:
        strat = df["sport"].astype(str)

    tr, te = train_test_split(df, test_size=0.2, random_state=42, stratify=strat)
    X_train = tr[FEATURE_COLUMNS].astype(float)
    X_test = te[FEATURE_COLUMNS].astype(float)
    y_train = tr["y"].astype(int)
    y_test = te["y"].astype(int)

    model = XGBClassifier(
        n_estimators=400,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=10,
        scale_pos_weight=spw,
        eval_metric="auc",
        early_stopping_rounds=30,
        random_state=42,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

    calibrated = CalibratedClassifierCV(model, method="sigmoid", cv="prefit")
    calibrated.fit(X_test, y_test)

    prob_test = calibrated.predict_proba(X_test)[:, 1]
    auc_overall = float(roc_auc_score(y_test, prob_test))
    print(f"\nROC-AUC (holdout, calibrated): {auc_overall:.4f}")

    print("\nROC-AUC per sport (holdout):")
    meta_auc: dict[str, float] = {}
    for sp in sorted(df["sport"].unique()):
        m = te["sport"].astype(str).values == str(sp)
        if int(np.sum(m)) < 5:
            print(f"  {sp}: n/a (too few test rows)")
            continue
        try:
            a = float(roc_auc_score(y_test.values[m], prob_test[m]))
            meta_auc[sp] = a
            print(f"  {sp}: {a:.4f}")
        except Exception:
            print(f"  {sp}: n/a")

    imp = dict(zip(FEATURE_COLUMNS, model.feature_importances_.tolist(), strict=True))
    top10 = sorted(imp.items(), key=lambda x: -x[1])[:10]
    print("\nTop 10 feature importances (pre-calibration booster):")
    for name, val in top10:
        print(f"  {name}: {val:.5f}")

    edge_bins = [(-np.inf, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, np.inf)]
    edge_labels = ["<0.05", "0.05-0.10", "0.10-0.20", ">0.20"]
    edges_te = te["edge"].astype(float).values
    y_te = y_test.values
    print("\nHit rate by edge bucket (test rows):")
    for (lo, hi), lab in zip(edge_bins, edge_labels, strict=True):
        m = (edges_te >= lo) & (edges_te < hi)
        if not np.any(m):
            print(f"  {lab}: (empty)")
            continue
        hr = float(np.mean(y_te[m]))
        print(f"  {lab}: n={int(np.sum(m))} hit_rate={hr:.3f}")

    print("\nCalibration check (5 bins, test):")
    pv = np.asarray(prob_test)
    qs = np.quantile(pv, [0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    for i in range(5):
        lo, hi = qs[i], qs[i + 1]
        if i == 4:
            bm = (pv >= lo) & (pv <= hi)
        else:
            bm = (pv >= lo) & (pv < hi)
        if not np.any(bm):
            print(f"  bin {i + 1}: empty")
            continue
        mp = float(np.mean(pv[bm]))
        ar = float(np.mean(y_te[bm]))
        print(f"  bin {i + 1}: mean_p={mp:.3f} actual={ar:.3f} n={int(np.sum(bm))}")

    joblib.dump(calibrated, models_dir / "edge_model_unified.pkl")
    (models_dir / "edge_model_features.json").write_text(
        json.dumps(FEATURE_COLUMNS, indent=2), encoding="utf-8"
    )
    meta = {
        "trained_at_utc": datetime.now(timezone.utc).isoformat(),
        "rows_per_sport": {str(k): int(v) for k, v in df.groupby("sport").size().items()},
        "roc_auc_overall": auc_overall,
        "roc_auc_per_sport": meta_auc,
        "scale_pos_weight": spw,
        "feature_columns": FEATURE_COLUMNS,
    }
    (models_dir / "edge_model_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nSaved: edge_model_unified.pkl, edge_model_features.json, edge_model_metadata.json -> {models_dir}")


if __name__ == "__main__":
    main()
