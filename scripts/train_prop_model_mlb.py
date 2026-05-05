#!/usr/bin/env python3
"""
Train MLB prop hit classifier for Sports/MLB/scripts/step7_rank_props_mlb.py.

Loads graded_mlb_*.xlsx (prefers Box Raw), builds the same feature vector as step7
(edge_feature_engineering.build_feature_vector) but **excludes edge / abs_edge** from
training inputs (leakage — edge is outcome-adjacent).

Saves models/prop_model_mlb.pkl (CalibratedClassifierCV + sigmoid = Platt-style),
prop_model_mlb_features.json, prop_model_mlb_blend_weight.json, metrics JSON.

Does not write prop_model_mlb_calibrator.pkl: Sports/MLB/scripts/step7_rank_props_mlb.py
expects that file to be a *second-stage* calibrator on raw probabilities only; the trained
sklearn model already includes calibration via CalibratedClassifierCV.

Requires ≥500 decided (HIT/MISS) rows after date filters or exits gracefully.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from edge_feature_engineering import FEATURE_COLUMNS, build_feature_vector  # noqa: E402

try:
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.metrics import brier_score_loss, roc_auc_score
except ImportError:
    raise SystemExit("pip install scikit-learn")

try:
    from xgboost import XGBClassifier
except ImportError:
    raise SystemExit("pip install xgboost")

MODEL_DIR = ROOT / "models"
MODEL_PATH = MODEL_DIR / "prop_model_mlb.pkl"
FEATURES_PATH = MODEL_DIR / "prop_model_mlb_features.json"
BLEND_PATH = MODEL_DIR / "prop_model_mlb_blend_weight.json"
METRICS_PATH = MODEL_DIR / "prop_model_mlb_metrics.json"
METADATA_PATH = MODEL_DIR / "prop_model_mlb_metadata.json"

DATE_RE = re.compile(r"graded_mlb_(?:synthetic_)?(\d{4}-\d{2}-\d{2})\.xlsx$", re.I)

# Same exclusion policy as user request / leakage guard
EXCLUDE_FROM_TRAINING: frozenset[str] = frozenset({"edge", "abs_edge"})

MIN_ROWS_TOTAL = 500


def _first_present(df: pd.DataFrame, options: tuple[str, ...]) -> str | None:
    lookup = {str(c).lower(): c for c in df.columns}
    for o in options:
        if str(o).lower() in lookup:
            return lookup[str(o).lower()]
    return None


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _map_hit(raw: pd.Series) -> pd.Series:
    s = raw.astype(str).str.strip().str.upper()
    out = np.full(len(raw), np.nan)
    out = np.where(s.eq("HIT"), 1.0, out)
    out = np.where(s.eq("MISS"), 0.0, out)
    out = np.where(s.isin(["1", "TRUE", "WIN"]), 1.0, out)
    out = np.where(s.isin(["0", "FALSE", "LOSS"]), 0.0, out)
    return pd.Series(out, index=raw.index)


def _mlb_sheet_usable(df: pd.DataFrame) -> bool:
    hit_col = _first_present(df, ("result", "Result", "outcome", "Grade", "grade"))
    if hit_col is None:
        return False
    line_or_prop = _first_present(df, ("line", "Line", "prop_type", "Prop", "prop_norm"))
    return line_or_prop is not None


def _collect_mlb_graded_files() -> list[Path]:
    out: list[Path] = []
    for base in (ROOT / "outputs", ROOT / "Sports" / "MLB", ROOT / "Sports" / "MLB" / "outputs"):
        if not base.is_dir():
            continue
        for p in base.rglob("graded_mlb*.xlsx"):
            if "synthetic" in str(p).lower():
                continue
            out.append(p)
    uniq: dict[str, Path] = {}
    for p in out:
        try:
            uniq[str(p.resolve())] = p
        except OSError:
            continue
    paths = sorted(uniq.values(), key=lambda x: str(x))
    return _dedupe_one_workbook_per_slate_date(paths)


def _dedupe_one_workbook_per_slate_date(paths: list[Path]) -> list[Path]:
    """
    Multiple exports per date (e.g. _mlbackfill, _tier_A) duplicate rows. Keep one file
    per slate date: prefer exact graded_mlb_YYYY-MM-DD.xlsx, else the shortest variant
    name (penalize mlbackfill / tier sub-suffixes).
    """
    by_date: dict[str, list[Path]] = {}
    for p in paths:
        m = DATE_RE.search(p.name)
        if not m:
            continue
        by_date.setdefault(m.group(1), []).append(p)
    chosen: list[Path] = []
    for d in sorted(by_date.keys()):
        plist = by_date[d]
        if len(plist) == 1:
            chosen.append(plist[0])
            continue
        want = f"graded_mlb_{d}.xlsx"
        for p in plist:
            if p.name.lower() == want.lower():
                chosen.append(p)
                break
        else:

            def _mtime(pp: Path) -> float:
                try:
                    return pp.stat().st_mtime
                except OSError:
                    return 0.0

            def _sort_key(pp: Path) -> tuple[int, int, float, str]:
                name_l = pp.name.lower()
                penal_ml = 2 if "mlbackfill" in name_l else 0
                penal_tier = 1 if "_tier_" in name_l else 0
                return (penal_ml + penal_tier, len(pp.name), -_mtime(pp), str(pp))

            plist_sorted = sorted(plist, key=_sort_key)
            chosen.append(plist_sorted[0])
    return chosen


def _load_one_mlb_workbook(path: Path) -> pd.DataFrame | None:
    try:
        xl = pd.ExcelFile(path, engine="openpyxl")
    except Exception as e:
        print(f"  (skip) Unreadable {path.name}: {e}")
        return None
    sheet_order = list(xl.sheet_names)
    # Prefer Box Raw (margin layout) when present
    for preferred in ("Box Raw", "Graded Props"):
        if preferred in sheet_order:
            sheet_order = [preferred] + [s for s in sheet_order if s != preferred]
    for sheet in sheet_order:
        try:
            df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
        except Exception as e:
            print(f"  (skip) Sheet {sheet!r} in {path.name}: {e}")
            continue
        if not _mlb_sheet_usable(df):
            continue
        df = df.copy()
        df["_source_file"] = str(path)
        m = DATE_RE.search(path.name)
        df["_source_date"] = m.group(1) if m else ""
        return df
    print(f"  (skip) No usable sheet in {path.name}")
    return None


def _norm_pick_cols(df: pd.DataFrame) -> pd.DataFrame:
    for col in ("pick_type", "Pick Type", "direction", "Direction", "bet_direction"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    return df


def blend_weight_for_n(n: int) -> float:
    if n < 200:
        return 0.15
    if n < 500:
        return 0.20
    return 0.30


def _calibration_deciles(y_true: np.ndarray, proba: np.ndarray) -> pd.DataFrame:
    dff = pd.DataFrame({"y": y_true, "p": proba})
    try:
        dff["bin"] = pd.qcut(dff["p"], q=10, duplicates="drop")
    except ValueError:
        dff["bin"] = pd.qcut(dff["p"], q=min(10, len(dff)), duplicates="drop")
    g = dff.groupby("bin", observed=True)
    out = pd.DataFrame(
        {
            "pred_mean": g["p"].mean(),
            "obs_hit_rate": g["y"].mean(),
            "n": g["y"].count(),
        }
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Train MLB prop_model_mlb from graded workbooks.")
    ap.add_argument("--from-date", dest="from_date", default="", help="Inclusive YYYY-MM-DD")
    ap.add_argument("--to-date", dest="to_date", default="", help="Inclusive YYYY-MM-DD")
    ap.add_argument(
        "--split-date",
        default="2026-04-01",
        help="Holdout starts here (train < split, test >= split). Default 2026-04-01",
    )
    ap.add_argument("--output-dir", default="", help="Override model directory (default: repo/models)")
    args = ap.parse_args()

    model_dir = Path(args.output_dir).resolve() if str(args.output_dir).strip() else MODEL_DIR
    model_path = model_dir / "prop_model_mlb.pkl"
    features_path = model_dir / "prop_model_mlb_features.json"
    blend_path = model_dir / "prop_model_mlb_blend_weight.json"
    metrics_path = model_dir / "prop_model_mlb_metrics.json"
    metadata_path = model_dir / "prop_model_mlb_metadata.json"

    print("=== MLB graded prop model training ===\n")
    files = _collect_mlb_graded_files()
    if not files:
        raise SystemExit(
            "No graded_mlb_*.xlsx found under outputs/ or Sports/MLB/. "
            "Run slate_grader for MLB dates first."
        )
    frames: list[pd.DataFrame] = []
    for p in files:
        block = _load_one_mlb_workbook(p)
        if block is not None:
            frames.append(block)
            print(f"  Loaded {len(block)} rows <- {p.name}")

    if not frames:
        raise SystemExit("No usable graded MLB workbooks.")

    df = pd.concat(frames, ignore_index=True)
    df = _norm_pick_cols(df)

    hit_col = _first_present(df, ("result", "Result", "outcome", "Grade", "grade"))
    if hit_col is None:
        raise SystemExit("No result/grade column in graded data.")

    df["_hit_raw"] = _map_hit(df[hit_col])
    decided = df["_hit_raw"].isin([0.0, 1.0])
    df = df.loc[decided].copy()
    df["_hit"] = df["_hit_raw"].astype(int)

    # Row dates for filtering / temporal split
    dt_parse = pd.to_datetime(df.get("_source_date", ""), errors="coerce")
    if "game_date" in df.columns:
        gd = pd.to_datetime(df["game_date"], errors="coerce")
        dt_parse = dt_parse.fillna(gd)
    if "Date" in df.columns:
        dt_parse = dt_parse.fillna(pd.to_datetime(df["Date"], errors="coerce"))
    df["_row_date"] = dt_parse

    fd = str(args.from_date).strip()
    td = str(args.to_date).strip()
    if fd:
        df = df.loc[df["_row_date"] >= pd.Timestamp(fd)].copy()
    if td:
        df = df.loc[df["_row_date"] <= pd.Timestamp(td)].copy()

    n_decided = len(df)
    print(f"\n-> Decided rows after date filters: {n_decided:,}")
    if n_decided < MIN_ROWS_TOTAL:
        print(
            f"\nERROR: Need at least {MIN_ROWS_TOTAL} decided MLB rows to train; "
            f"got {n_decided}. Expand --from/--to or grade more slates.\n"
        )
        raise SystemExit(1)

    # Feature vector (same as step7 pre-ML); fills neutral defaults for archive cols
    df = build_feature_vector(df, "MLB")

    feat_list = [c for c in FEATURE_COLUMNS if c not in EXCLUDE_FROM_TRAINING]
    missing = [c for c in feat_list if c not in df.columns]
    for c in missing:
        df[c] = 0.0
    X_all = df[feat_list].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    y_all = df["_hit"].astype(int)

    split_ts = pd.Timestamp(str(args.split_date).strip())
    train_mask = df["_row_date"].notna() & (df["_row_date"] < split_ts)
    test_mask = df["_row_date"].notna() & (df["_row_date"] >= split_ts)
    # Rows without dates: assign to train set only if before split unknown — drop from temporal eval
    nodate = df["_row_date"].isna()
    if nodate.any():
        print(f"  [warn] {int(nodate.sum())} rows missing dates — excluding from temporal split")
    train_mask = train_mask & ~nodate
    test_mask = test_mask & ~nodate

    X_train, y_train = X_all.loc[train_mask], y_all.loc[train_mask]
    X_test, y_test = X_all.loc[test_mask], y_all.loc[test_mask]

    n_split = len(X_train) + len(X_test)
    if n_split < MIN_ROWS_TOTAL:
        print(
            f"\nERROR: After excluding rows without dates, only {n_split} rows remain "
            f"(need {MIN_ROWS_TOTAL}). Fix dates in graded workbooks or relax filters.\n"
        )
        raise SystemExit(1)

    if len(X_train) < 100 or len(X_test) < 50:
        print(
            f"\nERROR: Temporal split produced train={len(X_train)}, test={len(X_test)}. "
            "Adjust --split-date or date range.\n"
        )
        raise SystemExit(1)

    n = len(X_train) + len(X_test)
    bw = blend_weight_for_n(n)

    if len(X_train) >= 500:
        base_model = XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            eval_metric="logloss",
        )
    else:
        base_model = XGBClassifier(
            n_estimators=50,
            max_depth=3,
            learning_rate=0.1,
            subsample=0.8,
            random_state=42,
            eval_metric="logloss",
        )

    # Platt-style (sigmoid) calibration
    model = CalibratedClassifierCV(estimator=base_model, method="sigmoid", cv=5)
    model.fit(np.asarray(X_train, dtype=float), np.asarray(y_train, dtype=int))

    proba = model.predict_proba(np.asarray(X_test, dtype=float))[:, 1]
    try:
        auc = roc_auc_score(y_test, proba) if y_test.nunique() > 1 else float("nan")
    except Exception:
        auc = float("nan")
    try:
        brier = brier_score_loss(y_test, proba)
    except Exception:
        brier = float("nan")

    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_path)
    features_path.write_text(json.dumps(feat_list, indent=2), encoding="utf-8")
    blend_path.write_text(json.dumps({"blend_weight": bw}, indent=2), encoding="utf-8")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    metrics_path.write_text(
        json.dumps(
            {
                "auc_holdout": None if np.isnan(auc) else float(auc),
                "brier_holdout": None if np.isnan(brier) else float(brier),
                "n_train": int(len(X_train)),
                "n_test": int(len(X_test)),
                "split_date": str(args.split_date),
                "timestamp": ts,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    feats = feat_list
    fi = None
    try:
        if hasattr(model, "calibrated_classifiers_") and model.calibrated_classifiers_:
            est = getattr(model.calibrated_classifiers_[0], "estimator", None)
            if est is not None and hasattr(est, "feature_importances_"):
                fi = pd.Series(est.feature_importances_, index=feats).sort_values(ascending=False)
    except Exception:
        pass

    cal_tbl = _calibration_deciles(y_test.to_numpy(), np.asarray(proba, dtype=float))
    print("\nCalibration (holdout deciles — pred_mean vs observed hit rate):")
    print(cal_tbl.to_string())

    meta = {
        "model": "prop_model_mlb",
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
        "roc_auc_holdout": None if np.isnan(auc) else float(auc),
        "brier_holdout": None if np.isnan(brier) else float(brier),
        "features": feats,
        "excluded_from_x": sorted(EXCLUDE_FROM_TRAINING),
    }
    metadata_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    log_path = model_dir / "training_log.csv"
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sport": "mlb",
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
        "auc": None if np.isnan(auc) else round(float(auc), 4),
        "brier": None if np.isnan(brier) else round(float(brier), 4),
        "n_features": int(len(feats)),
        "model_path": str(model_path),
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
        imp_path = model_dir / "prop_model_mlb_feature_importance.json"
        imp_path.write_text(
            json.dumps({str(k): float(v) for k, v in fi.head(30).items()}, indent=2),
            encoding="utf-8",
        )

    print("\n=== MLB prop model training complete ===")
    print(f"  Training rows:    {len(X_train)}")
    print(f"  Holdout rows:     {len(X_test)}")
    print(f"  ROC-AUC (holdout): {auc:.4f}" if not np.isnan(auc) else "  ROC-AUC (holdout): n/a")
    print(f"  Brier (holdout):   {brier:.4f}" if not np.isnan(brier) else "  Brier (holdout):   n/a")
    print(f"  Blend weight:      {bw:.2f}")
    if fi is not None:
        print("\n  Top 10 features:")
        for i, (k, v) in enumerate(fi.head(10).items(), 1):
            print(f"  {i}. {k:<28} {v:.6f}")
    print(f"\n  Saved: {model_path}")
    print(f"  Saved: {features_path}")
    print(f"  Saved: {blend_path}")
    print(f"  Saved: {metrics_path}")


if __name__ == "__main__":
    main()
