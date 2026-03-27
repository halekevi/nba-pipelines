#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import brier_score_loss, roc_auc_score
from xgboost import XGBClassifier


ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / "models"
MODEL_PATH = MODEL_DIR / "prop_model_nba.pkl"
FEATURES_PATH = MODEL_DIR / "prop_model_nba_features.json"


def _find_db_candidates() -> list[Path]:
    candidates: list[Path] = []
    for base in (ROOT, ROOT / "data", ROOT / "NBA" / "data"):
        if base.exists():
            candidates.extend(base.rglob("*.db"))
    uniq: list[Path] = []
    seen: set[str] = set()
    for p in candidates:
        key = str(p.resolve()).lower()
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return sorted(uniq, key=lambda p: p.stat().st_mtime, reverse=True)


def _table_columns(con: sqlite3.Connection, table: str) -> list[str]:
    cur = con.execute(f"PRAGMA table_info({table})")
    return [str(r[1]) for r in cur.fetchall()]


def _pick_table(con: sqlite3.Connection) -> str | None:
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    best: tuple[int, str] | None = None
    for t in tables:
        cols = {c.lower(): c for c in _table_columns(con, t)}
        score = 0
        if any(k in cols for k in ("hit", "result", "outcome", "grade")):
            score += 3
        if any(k in cols for k in ("edge", "edge_adj", "edge_norm")):
            score += 2
        if any(k in cols for k in ("prop_type", "prop_norm")):
            score += 2
        if any(k in cols for k in ("direction", "bet_direction", "final_bet_direction")):
            score += 2
        if any(k in cols for k in ("tier", "pick_type")):
            score += 1
        if "nba" in t.lower():
            score += 1
        if score > 0 and (best is None or score > best[0]):
            best = (score, t)
    return best[1] if best else None


def _first_present(df: pd.DataFrame, options: Iterable[str]) -> str | None:
    lookup = {c.lower(): c for c in df.columns}
    for c in options:
        if c.lower() in lookup:
            return lookup[c.lower()]
    return None


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _chrono_split(
    df_raw: pd.DataFrame, X: pd.DataFrame, y: pd.Series, date_col: str | None
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
    """
    80/20 chronological split (no shuffling).
    If date_col is missing or unparsable, falls back to index order and prints a warning.
    """
    if date_col and date_col in df_raw.columns:
        dd = pd.to_datetime(df_raw.loc[X.index, date_col], errors="coerce")
        if dd.notna().any():
            order = dd.sort_values().index
            Xo = X.loc[order]
            yo = y.loc[order]
            split_idx = int(len(Xo) * 0.80)
            return Xo.iloc[:split_idx], Xo.iloc[split_idx:], yo.iloc[:split_idx], yo.iloc[split_idx:]

    print("⚠️  [ML] No usable date column found — using index order 80/20 split (no shuffle).")
    split_idx = int(len(X) * 0.80)
    return X.iloc[:split_idx], X.iloc[split_idx:], y.iloc[:split_idx], y.iloc[split_idx:]


def _map_defense_tier(raw: pd.Series) -> pd.Series:
    s = raw.astype(str).str.strip().str.lower()
    out = pd.Series(np.nan, index=raw.index, dtype="float64")
    out = np.where(s.str.contains("weak"), 0, out)
    out = np.where(s.str.contains("avg|average|mid|med"), 1, out)
    out = np.where(s.str.contains("strong"), 2, out)
    return pd.Series(out, index=raw.index)


def _map_tier(raw: pd.Series) -> pd.Series:
    s = raw.astype(str).str.strip().str.lower()
    return pd.Series(
        np.where(
            s.str.contains("gob"),
            2,
            np.where(s.str.contains("std|standard"), 1, np.where(s.str.contains("dem"), 0, 1)),
        ),
        index=raw.index,
    )


def _map_direction(raw: pd.Series) -> pd.Series:
    s = raw.astype(str).str.strip().str.upper()
    return pd.Series(np.where(s.eq("OVER"), 1, 0), index=raw.index)


def _map_hit(raw: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(raw):
        return _to_num(raw)
    s = raw.astype(str).str.strip().str.upper()
    out = pd.Series(np.nan, index=raw.index, dtype="float64")
    out = np.where(s.eq("HIT"), 1, out)
    out = np.where(s.eq("MISS"), 0, out)
    out = np.where(s.isin(["1", "TRUE"]), 1, out)
    out = np.where(s.isin(["0", "FALSE"]), 0, out)
    return pd.Series(out, index=raw.index)


def main() -> None:
    db_candidates = _find_db_candidates()
    if not db_candidates:
        raise FileNotFoundError("No SQLite .db file found in project root or data folders.")

    selected_db: Path | None = None
    selected_table: str | None = None
    for dbp in db_candidates:
        try:
            con = sqlite3.connect(str(dbp))
            table = _pick_table(con)
            con.close()
            if table:
                selected_db = dbp
                selected_table = table
                break
        except Exception:
            continue

    if selected_db is None or selected_table is None:
        raise RuntimeError("Could not find a graded props-like table with required columns.")

    print(f"→ Using DB: {selected_db}")
    print(f"→ Using table: {selected_table}")
    con = sqlite3.connect(str(selected_db))
    df = pd.read_sql_query(f"SELECT * FROM {selected_table}", con)
    con.close()
    print(f"→ Loaded rows: {len(df)}")

    edge_col = _first_present(df, ["edge", "edge_adj", "edge_norm"])
    hr_col = _first_present(
        df,
        [
            "hit_rate_l10",
            "line_hit_rate_over_ou_10",
            "line_hit_rate_over_10",
            "line_hit_rate_10",
            "line_hit_rate",
            "last10_hit_rate",
            "hit_rate",
        ],
    )
    defense_tier_col = _first_present(df, ["defense_tier", "def_tier"])
    defense_rank_col = _first_present(df, ["overall_def_rank", "def_rank"])
    tier_col = _first_present(df, ["tier", "pick_type"])
    intel_col = _first_present(df, ["intel_shr_z", "intel_season_hit_rate", "sharper_consensus_z"])
    prop_type_col = _first_present(df, ["prop_type", "prop_norm"])
    direction_col = _first_present(df, ["direction", "bet_direction", "final_bet_direction"])
    hit_col = _first_present(df, ["hit", "result", "outcome", "grade"])

    required = {
        "edge": edge_col,
        "prop_type": prop_type_col,
        "direction": direction_col,
        "hit": hit_col,
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise RuntimeError(f"Missing required columns in training table: {missing}")

    train = pd.DataFrame(index=df.index)
    train["edge"] = _to_num(df[edge_col])
    train["hit_rate_l10"] = _to_num(df[hr_col]) if hr_col else np.nan

    if defense_tier_col:
        train["defense_tier"] = _map_defense_tier(df[defense_tier_col])
    elif defense_rank_col:
        dr = _to_num(df[defense_rank_col]).fillna(15.0)
        train["defense_tier"] = np.where(dr <= 10, 2, np.where(dr <= 20, 1, 0))
    else:
        train["defense_tier"] = 1
    train["defense_tier"] = _to_num(train["defense_tier"]).fillna(1).astype(int)

    train["tier"] = _map_tier(df[tier_col]) if tier_col else 1
    train["intel_shr_z"] = _to_num(df[intel_col]) if intel_col else 0.0
    train["intel_shr_z"] = train["intel_shr_z"].fillna(0.0)
    train["prop_type"] = df[prop_type_col].astype(str).str.strip().str.lower()
    train["direction"] = _map_direction(df[direction_col]).astype(int)
    train["hit"] = _map_hit(df[hit_col])

    train = train[train["hit"].isin([0, 1])].copy()
    train["hit"] = train["hit"].astype(int)
    train = train.dropna(subset=["edge"])
    train["hit_rate_l10"] = train["hit_rate_l10"].fillna(0.5)
    print(f"→ Decided rows after filtering: {len(train)}")
    if len(train) < 100:
        raise RuntimeError(f"Not enough decided rows to train robustly (rows={len(train)}).")

    X_base = train[["edge", "hit_rate_l10", "defense_tier", "tier", "intel_shr_z", "direction"]].copy()
    X_prop = pd.get_dummies(train["prop_type"], prefix="prop", dtype=float)
    X = pd.concat([X_base, X_prop], axis=1).fillna(0.0)
    y = train["hit"].astype(int)

    # Prefer chronological split to avoid temporal leakage.
    date_col = _first_present(df, ["game_date", "date", "_source_date", "slate_date"])
    if date_col:
        print(f"→ Using temporal split on column: {date_col}")
    X_train, X_test, y_train, y_test = _chrono_split(df, X, y, date_col)
    print(f"→ Split: {len(X_train)} train / {len(X_test)} test")

    base_model = XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.9,
        colsample_bytree=0.9,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=42,
    )
    model = CalibratedClassifierCV(base_model, method="isotonic", cv=5)
    model.fit(X_train, y_train)

    proba = model.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, proba)
    brier = brier_score_loss(y_test, proba)

    feats = list(X.columns)
    # Feature importances come from the underlying estimator (calibrated wrapper has none).
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

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    FEATURES_PATH.write_text(json.dumps(feats, indent=2), encoding="utf-8")

    # Training log (for decay monitoring)
    sport = MODEL_PATH.stem.replace("prop_model_", "")
    log_path = MODEL_DIR / "training_log.csv"
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sport": sport,
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
        "auc": round(float(auc), 4),
        "brier": round(float(brier), 4),
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

    # Feature importance log (from base estimator)
    if fi is not None:
        imp = {str(k): float(v) for k, v in fi.to_dict().items()}
        imp_path = MODEL_DIR / f"prop_model_{sport}_feature_importance.json"
        imp_path.write_text(json.dumps(imp, indent=2), encoding="utf-8")

    print(f"✅ Saved model: {MODEL_PATH}")
    print(f"✅ Saved features: {FEATURES_PATH}")
    print(f"📈 Test AUC: {auc:.4f}")
    print(f"📉 Brier score: {brier:.4f}")
    if top5 is not None:
        print("⭐ Top 5 features:")
        for k, v in top5.items():
            print(f"  - {k}: {v:.6f}")


if __name__ == "__main__":
    main()
