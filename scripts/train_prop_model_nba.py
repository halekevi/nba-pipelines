#!/usr/bin/env python3
"""
Train NBA prop ML model for NBA/scripts/step7_rank_props.py inference.

Loads graded workbooks (full slate `graded_nba_*.xlsx`, or `graded_nba1q_*.xlsx` /
`graded_nba1h_*.xlsx` when `--segment` is set), builds feature matrix, trains XGBoost,
and saves `models/prop_model_{segment}.pkl` plus features / blend / calibrator sidecars.

Training applies play-side edge (negate raw edge for UNDER) so the `edge` feature matches
`NBA/scripts/step7_rank_props.py` inference after the 2026 direction-aware ML fix.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
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

SEGMENT_KEY = "nba"
SYNTHETIC_SPORT = "NBA"
DATE_RE = re.compile(r"graded_nba_(?:synthetic_)?(\d{4}-\d{2}-\d{2})\.xlsx$", re.I)
MODEL_PATH = MODEL_DIR / "prop_model_nba.pkl"
FEATURES_PATH = MODEL_DIR / "prop_model_nba_features.json"
BLEND_PATH = MODEL_DIR / "prop_model_nba_blend_weight.json"
CALIB_PATH = MODEL_DIR / "prop_model_nba_calibrator.pkl"
METRICS_PATH = MODEL_DIR / "prop_model_nba_metrics.json"
META_MODEL_PATH = MODEL_DIR / "nba_meta_model.pkl"


def apply_segment(segment: str) -> None:
    """Apply training segment (nba | nba1q | nba1h): model output names + graded filename regex."""
    global SEGMENT_KEY, SYNTHETIC_SPORT, DATE_RE
    global MODEL_PATH, FEATURES_PATH, BLEND_PATH, CALIB_PATH, METRICS_PATH, META_MODEL_PATH
    s = (segment or "nba").strip().lower()
    if s not in ("nba", "nba1q", "nba1h"):
        raise ValueError(f"Unknown segment {segment!r}; expected nba, nba1q, or nba1h")
    SEGMENT_KEY = s
    MODEL_PATH = MODEL_DIR / f"prop_model_{s}.pkl"
    FEATURES_PATH = MODEL_DIR / f"prop_model_{s}_features.json"
    BLEND_PATH = MODEL_DIR / f"prop_model_{s}_blend_weight.json"
    CALIB_PATH = MODEL_DIR / f"prop_model_{s}_calibrator.pkl"
    METRICS_PATH = MODEL_DIR / f"prop_model_{s}_metrics.json"
    META_MODEL_PATH = MODEL_DIR / f"{s}_meta_model.pkl"
    sport_map = {"nba": "NBA", "nba1q": "NBA1Q", "nba1h": "NBA1H"}
    SYNTHETIC_SPORT = sport_map[s]
    if s == "nba":
        DATE_RE = re.compile(r"graded_nba_(?:synthetic_)?(\d{4}-\d{2}-\d{2})\.xlsx$", re.I)
    elif s == "nba1q":
        DATE_RE = re.compile(r"graded_nba1q_(?:synthetic_)?(\d{4}-\d{2}-\d{2})\.xlsx$", re.I)
    else:
        DATE_RE = re.compile(r"graded_nba1h_(?:synthetic_)?(\d{4}-\d{2}-\d{2})\.xlsx$", re.I)
SYNTHETIC_RATIO_CAP = 1.0  # max synthetic rows = real_rows * this cap
REAL_ONLY_MODE = True      # baseline: train on real graded rows only


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


def _collect_nba_graded_files() -> list[tuple[Path, bool]]:
    """Return (path, is_synthetic) pairs — synthetic props come from SQLite, not Excel."""
    out: list[tuple[Path, bool]] = []
    if SEGMENT_KEY == "nba1q":
        patterns = ("graded_nba1q*.xlsx",)
    elif SEGMENT_KEY == "nba1h":
        patterns = ("graded_nba1h*.xlsx",)
    else:
        patterns = ("graded_nba*.xlsx",)
    for base in (ROOT / "outputs", ROOT / "NBA", ROOT / "NBA" / "outputs"):
        if not base.is_dir():
            continue
        for pat in patterns:
            for p in base.rglob(pat):
                if "synthetic" in str(p).lower() or "synthetic" in p.parts:
                    continue
                ln = p.name.lower()
                if SEGMENT_KEY == "nba" and ("nba1q" in ln or "nba1h" in ln):
                    continue
                out.append((p, False))
    uniq: dict[str, tuple[Path, bool]] = {}
    for p, syn in out:
        try:
            uniq[str(p.resolve())] = (p, syn)
        except OSError:
            continue
    return sorted(uniq.values(), key=lambda x: str(x[0]))


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


def _nba_synthetic_to_workbook_like(syn: pd.DataFrame) -> pd.DataFrame:
    if syn.empty:
        return syn
    idx = syn.index
    if "home_away" in syn.columns:
        ha = syn["home_away"].astype(str).str.strip()
    else:
        ha = pd.Series("", index=idx)
    return pd.DataFrame(
        {
            "result": syn["result"],
            "edge": 0.0,
            "prop_type": syn["prop_type"],
            "direction": syn["direction"],
            "line": syn["line"],
            "Pick Type": syn["tier"] if "tier" in syn.columns else pd.Series("Standard", index=idx),
            "minutes": pd.to_numeric(syn["minutes"], errors="coerce")
            if "minutes" in syn.columns
            else pd.Series(np.nan, index=idx),
            "home_away": ha,
            "_source_file": "synthetic_graded.db",
            "_synthetic": 1,
            "_source_date": syn["game_date"].astype(str) if "game_date" in syn.columns else "",
            "_weight": syn["_weight"],
        },
        index=idx,
    )


def _sheet_usable(df: pd.DataFrame) -> bool:
    r = _first_present(df, ["result", "outcome", "grade"])
    e = _first_present(df, ["edge", "abs_edge", "edge_adj"])
    return r is not None and e is not None


def _load_one_workbook(path: Path, is_synthetic: bool) -> pd.DataFrame | None:
    try:
        xl = pd.ExcelFile(path, engine="openpyxl")
    except Exception as e:
        print(f"  (skip) Unreadable workbook {path.name}: {e}")
        return None
    sheet_order = list(xl.sheet_names)
    if "Graded Props" in sheet_order:
        sheet_order = ["Graded Props"] + [s for s in sheet_order if s != "Graded Props"]
    for sheet in sheet_order:
        try:
            df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
        except Exception as e:
            print(f"  (skip) Sheet {sheet!r} in {path.name}: {e}")
            continue
        if not _sheet_usable(df):
            continue
        df = df.copy()
        df["_source_file"] = str(path)
        df["_synthetic"] = 1 if is_synthetic else 0
        df["_weight"] = 0.7 if is_synthetic else 1.0
        m = DATE_RE.search(path.name)
        df["_source_date"] = m.group(1) if m else ""
        return df
    print(f"  (skip) No usable sheet in {path.name}")
    return None


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


def _defense_tier_4(df: pd.DataFrame) -> pd.Series:
    dt = _first_present(df, ["def_tier", "defense_tier", "DEF_TIER", "Opp Def Tier"])
    if dt:
        s = df[dt].astype(str).str.strip().str.lower()
        return pd.Series(
            np.where(
                s.str.contains("weak"),
                0,
                np.where(
                    s.str.contains("avg|average|mid|med"),
                    1,
                    np.where(
                        s.str.contains("good|solid|above"),
                        2,
                        np.where(s.str.contains("elite|strong"), 3, 1),
                    ),
                ),
            ),
            index=df.index,
        )
    rk = _first_present(df, ["OVERALL_DEF_RANK", "def_rank", "Def Rank"])
    if rk:
        r = _to_num(df[rk]).fillna(15.0)
        return pd.Series(
            np.where(
                r <= 5,
                3,
                np.where(r <= 10, 2, np.where(r <= 20, 1, 0)),
            ),
            index=df.index,
        )
    return pd.Series(1, index=df.index)


def _consistency_grade_num(raw: pd.Series) -> pd.Series:
    s = raw.astype(str).str.strip().str.upper()
    m = {
        "S": 5.0,
        "A": 4.0,
        "B": 3.0,
        "C": 2.0,
        "D": 1.0,
        "F": 0.0,
        "?": 2.0,
        "": 2.0,
        "NAN": 2.0,
    }
    return s.map(lambda x: m.get(x, 2.0)).astype(float)


def normalize_nba_prop(raw: str) -> str:
    x = str(raw or "").strip().lower()
    x_compact = re.sub(r"\s+", "", x)
    if not x:
        return "unknown"
    if "pts+reb+ast" in x_compact or x_compact == "pra" or "pra" in x.split():
        return "pra"
    if "pts+asts" in x_compact or "ptsasts" in x_compact or "points+assists" in x_compact:
        return "pts_asts"
    if "pts+rebs" in x_compact or "ptsrebs" in x_compact:
        return "pts_rebs"
    if "rebs+asts" in x_compact or "rebsasts" in x_compact:
        return "rebs_asts"
    if "blks+stls" in x_compact or "blksstls" in x_compact:
        return "blks_stls"
    if "3-pt" in x or "3pt" in x_compact or "threes" in x or "fg3m" in x or "three" in x:
        return "threes"
    if "fantasy" in x:
        return "fantasy_score"
    if "free throw" in x or x.startswith("fta") or " ftm" in x:
        return "fta"
    if ("field goal" in x and "attempt" in x) or x == "fga" or "fg attempted" in x:
        return "fg_attempted"
    if "rebound" in x or x in ("reb", "rebs"):
        return "rebounds"
    if "assist" in x or x in ("ast", "asts"):
        return "assists"
    if "point" in x or x in ("pts", "pt"):
        return "points"
    if "steal" in x or x == "stl":
        return "steals"
    if "block" in x or x == "blk":
        return "blocks"
    if "turnover" in x or x == "tov" or x == "to":
        return "turnovers"
    return re.sub(r"[^a-z0-9]+", "_", x).strip("_") or "unknown"


def _audit_and_load() -> pd.DataFrame:
    files = _collect_nba_graded_files()
    real_frames: list[pd.DataFrame] = []
    for p, syn in files:
        block = _load_one_workbook(p, syn)
        if block is not None:
            real_frames.append(block)
    if not real_frames:
        raise FileNotFoundError(
            f"No graded workbooks for segment {SEGMENT_KEY!r} under outputs/ or NBA/ (non-synthetic)."
        )
    real_df = pd.concat(real_frames, ignore_index=True)
    n_real = len(real_df)

    syn_raw = load_synthetic_training_data(SYNTHETIC_SPORT, str(SYNTHETIC_DB))
    if REAL_ONLY_MODE or syn_raw.empty:
        df = real_df
        n_syn_used = 0
    else:
        syn_df = _nba_synthetic_to_workbook_like(syn_raw)
        n_syn_cap = int(n_real * SYNTHETIC_RATIO_CAP)
        if n_syn_cap <= 0:
            syn_df = syn_df.iloc[0:0].copy()
        elif len(syn_df) > n_syn_cap:
            syn_df = syn_df.sample(n=n_syn_cap, random_state=42)
        n_syn_used = len(syn_df)
        df = pd.concat([real_df, syn_df], ignore_index=True)

    print(f"Training mix — real: {n_real:,}  synthetic: {n_syn_used:,}  total: {len(df):,}")
    print(f"-> Total rows (all sources): {len(df)}")
    print(f"-> Columns ({len(df.columns)}): {list(df.columns)}")
    hit_col = _first_present(df, ["result", "outcome", "grade"])
    if hit_col is None:
        raise RuntimeError("No result column.")
    hit = _map_hit(df[hit_col])
    decided = hit.isin([0.0, 1.0])
    print(f"-> Decided rows (HIT/MISS only): {int(decided.sum())}")
    dr = df.loc[decided, "_source_date"].astype(str)
    dr2 = dr[dr.str.match(r"\d{4}-\d{2}-\d{2}")]
    if len(dr2):
        print(f"-> Date range: {dr2.min()} .. {dr2.max()}")
    sub = df.loc[decided].copy()
    sub["_hit"] = hit[decided].astype(int)
    if _first_present(sub, ["prop_type_norm", "prop_type", "prop_norm", "Prop"]):
        pc = _first_present(sub, ["prop_type_norm", "prop_type", "prop_norm", "Prop"])
        pt = sub[pc].astype(str)
        print("-> Hit rate by prop_type (top 15):")
        g = sub.groupby(pt, dropna=False)["_hit"].mean().sort_values(ascending=False)
        print(g.head(15).to_string())
    dc = _first_present(sub, ["bet_direction", "direction", "final_bet_direction"])
    if dc:
        print("-> Hit rate by direction:")
        print(sub.groupby(sub[dc].astype(str).str.upper())["_hit"].mean().to_string())
    tc = _first_present(sub, ["pick_type", "Pick Type", "tier", "Tier"])
    if tc:
        print("-> Hit rate by tier/pick_type:")
        print(sub.groupby(sub[tc].astype(str).str.strip())["_hit"].mean().to_string())
    return df


def main() -> None:
    ap = argparse.ArgumentParser(description="Train NBA / NBA1Q / NBA1H prop ML models.")
    ap.add_argument(
        "--segment",
        choices=["nba", "nba1q", "nba1h"],
        default="nba",
        help="Graded workbook family and models/prop_model_<segment>.* output names.",
    )
    args = ap.parse_args()
    apply_segment(args.segment)

    print(f"=== {SEGMENT_KEY.upper()} graded data audit ===\n")
    df = _audit_and_load()

    hit_col = _first_present(df, ["result", "outcome", "grade"])
    edge_col = _first_present(df, ["edge", "abs_edge", "edge_adj"])
    if hit_col is None or edge_col is None:
        raise RuntimeError("Missing result or edge.")

    hr5 = _first_present(
        df,
        [
            "hit_rate_l5",
            "l5_hit_rate",
            "line_hit_rate_over_ou_5",
            "line_hit_rate_over_5",
            "Hit Rate (5g)",
        ],
    )
    hr10 = _first_present(
        df,
        [
            "line_hit_rate",
            "hit_rate",
            "hit_rate_l10",
            "line_hit_rate_over_ou_10",
            "line_hit_rate_over_10",
            "Hit Rate (10g)",
        ],
    )
    hr20 = _first_present(
        df,
        [
            "hit_rate_l20",
            "l20_hit_rate",
            "line_hit_rate_over_ou_20",
            "line_hit_rate_over_20",
            "Hit Rate (20g)",
        ],
    )
    pick_col = _first_present(df, ["pick_type", "Pick Type"])
    prop_col = _first_present(df, ["prop_norm", "prop_type_norm", "prop_type", "Prop", "prop"])
    dir_col = _first_present(df, ["bet_direction", "final_bet_direction", "direction"])
    line_col = _first_present(df, ["line", "Line"])
    min_col = _first_present(df, ["minutes", "avg_minutes", "MIN", "Minutes", "Avg Min"])
    ha_col = _first_present(df, ["home_away", "home/away", "Home/Away"])
    gs_col = _first_present(df, ["game_script_mult", "Game Script Mult"])
    cg_col = _first_present(df, ["consistency_grade", "Consistency Grade"])
    pace_col = _first_present(
        df,
        ["pace_percentile", "pace_pct", "Pace Percentile", "pace_vs_league_pct"],
    )
    rest_col = _first_present(df, ["days_rest", "rest_days", "Days Rest", "team_rest_days"])
    lmd_col = _first_present(
        df,
        ["line_move_direction", "line_move_toward_over", "Line Move Direction", "line_move"],
    )
    b2b_col = _first_present(df, ["is_back_to_back", "b2b", "is_b2b", "back_to_back"])

    if prop_col is None or dir_col is None:
        raise RuntimeError(f"Missing prop or direction. Columns: {list(df.columns)}")

    train = pd.DataFrame(index=df.index)
    train["edge"] = _to_num(df[edge_col])
    train["hit_rate_l5"] = _to_num(df[hr5]) if hr5 else np.nan
    train["hit_rate_l10"] = _to_num(df[hr10]) if hr10 else np.nan
    train["hit_rate_l20"] = _to_num(df[hr20]) if hr20 else np.nan
    for c in ("hit_rate_l5", "hit_rate_l10", "hit_rate_l20"):
        col = train[c]
        if col.notna().any() and col.dropna().median() > 1.0:
            train[c] = col / 100.0
    train["line"] = _to_num(df[line_col]) if line_col else np.nan
    train["direction"] = _direction_num(df[dir_col]).astype(int)
    train["edge"] = play_side_edge(train["edge"], train["direction"])
    train["tier"] = _pick_type_tier_num(df[pick_col]) if pick_col else 1
    train["defense_tier"] = _defense_tier_4(df)
    train["minutes"] = _to_num(df[min_col]) if min_col else np.nan
    if ha_col:
        ha = df[ha_col].astype(str).str.strip().str.upper()
        train["home_away"] = np.where(ha.str.startswith("H"), 1.0, 0.0)
    else:
        train["home_away"] = np.nan
    train["game_script_mult"] = _to_num(df[gs_col]) if gs_col else np.nan
    train["consistency_grade"] = _consistency_grade_num(df[cg_col]) if cg_col else np.nan
    train["prop_type"] = df[prop_col].astype(str).map(normalize_nba_prop)
    train["hit"] = _map_hit(df[hit_col])

    train = train[train["hit"].isin([0.0, 1.0])].copy()
    train["hit"] = train["hit"].astype(int)
    train = train.dropna(subset=["edge"])
    train["hit_rate_l5"] = train["hit_rate_l5"].fillna(train["hit_rate_l10"]).fillna(0.5)
    train["hit_rate_l10"] = train["hit_rate_l10"].fillna(0.5)
    train["hit_rate_l20"] = train["hit_rate_l20"].fillna(train["hit_rate_l10"]).fillna(0.5)
    train["line"] = train["line"].fillna(0.0)
    train["minutes"] = train["minutes"].fillna(0.0)
    train["home_away"] = train["home_away"].fillna(0.5)
    train["game_script_mult"] = train["game_script_mult"].fillna(1.0)
    train["consistency_grade"] = train["consistency_grade"].fillna(2.0)
    train["pace_percentile"] = train["pace_percentile"].fillna(0.5)
    train["days_rest"] = train["days_rest"].fillna(1.0)
    train["line_move_direction"] = train["line_move_direction"].fillna(0.0)
    train["is_back_to_back"] = train["is_back_to_back"].fillna(0.0)

    if "_weight" in df.columns:
        sw = pd.to_numeric(df.loc[train.index, "_weight"], errors="coerce").fillna(1.0)
    else:
        sw = pd.Series(
            np.where(df.loc[train.index, "_synthetic"].to_numpy() > 0, 0.7, 1.0),
            index=train.index,
        )

    n = len(train)
    bw = blend_weight_for_n(n)
    if n < 200:
        print(
            f"\nWARNING: Only {n} decided rows for {SEGMENT_KEY.upper()} — model will be trained but accuracy may be low.\n"
            "Recommend waiting for more graded history before using ML blend in production.\n"
            f"Proceeding with ML_BLEND_WEIGHT = {bw}\n"
        )
    elif n < 500:
        print(f"\n-> Using ML_BLEND_WEIGHT = {bw} (medium sample)\n")
    else:
        print(f"\n-> Using ML_BLEND_WEIGHT = {bw} (full sample)\n")

    if n < 50:
        raise RuntimeError(f"Too few decided rows to train (n={n}).")

    base_cols = [
        "edge",
        "hit_rate_l5",
        "hit_rate_l10",
        "hit_rate_l20",
        "line",
        "direction",
        "tier",
        "defense_tier",
        "minutes",
        "home_away",
        "game_script_mult",
        "consistency_grade",
        "pace_percentile",
        "days_rest",
        "line_move_direction",
        "is_back_to_back",
    ]
    X_base = train[base_cols].copy().fillna(0.0)
    X_prop = pd.get_dummies(train["prop_type"], prefix="prop", dtype=float)
    X = pd.concat([X_base, X_prop], axis=1).fillna(0.0)
    y = train["hit"].astype(int)

    # Chronological split to avoid temporal leakage
    date_col = _first_present(train, ["game_date", "date", "_source_date", "slate_date"])
    if date_col:
        print(f"-> Using temporal split on: {date_col}")
    order = _chrono_split_idx(train, date_col)
    Xo = X.loc[order]
    yo = y.loc[order]
    swo = sw.loc[order]
    split_idx = int(len(Xo) * 0.80)
    X_train, X_test = Xo.iloc[:split_idx], Xo.iloc[split_idx:]
    y_train, y_test = yo.iloc[:split_idx], yo.iloc[split_idx:]
    sw_train, _sw_test = swo.iloc[:split_idx], swo.iloc[split_idx:]

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
        brier = brier_score_loss(y_test, proba)
    except Exception:
        brier = float("nan")

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

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    FEATURES_PATH.write_text(json.dumps(feats, indent=2), encoding="utf-8")

    # Meta-model on (base_prob, edge, defense_tier, prop_code) — used as final ranking signal in step7 when present
    try:
        tr_ordered = train.loc[order].reset_index(drop=True)
        y_ordered = y.loc[order].reset_index(drop=True)
        tr_train = tr_ordered.iloc[:split_idx]
        y_tr = y_ordered.iloc[:split_idx].astype(int)
        base_p_train = model.predict_proba(X_train)[:, 1]
        prop_uniques = sorted(tr_train["prop_type"].astype(str).unique().tolist())
        prop_to_i = {p: i for i, p in enumerate(prop_uniques)}
        pcodes = tr_train["prop_type"].astype(str).map(lambda x: prop_to_i.get(x, 0)).astype(int)
        X_meta = np.column_stack(
            [
                base_p_train,
                tr_train["edge"].to_numpy(dtype=float),
                tr_train["defense_tier"].to_numpy(dtype=float),
                pcodes.astype(float),
            ]
        )
        meta_clf = XGBClassifier(
            n_estimators=120,
            max_depth=3,
            learning_rate=0.06,
            subsample=0.85,
            colsample_bytree=0.9,
            random_state=42,
            eval_metric="logloss",
        )
        meta_clf.fit(X_meta, y_tr)
        joblib.dump(
            {"model": meta_clf, "prop_uniques": prop_uniques},
            META_MODEL_PATH,
        )
        print(f"  Saved meta ranker: {META_MODEL_PATH}")
    except Exception as e:
        print(f"  (meta-model skipped) {e}")
    BLEND_PATH.write_text(json.dumps({"blend_weight": bw}, indent=2), encoding="utf-8")
    METRICS_PATH.write_text(
        json.dumps(
            {
                "auc_test": None if np.isnan(auc) else float(auc),
                "brier_test_raw": None if np.isnan(brier) else float(brier),
                "n_train": int(len(X_train)),
                "n_test": int(len(X_test)),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # Training log (repo-level)
    log_path = MODEL_DIR / "training_log.csv"
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sport": SEGMENT_KEY,
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
        "auc": None if np.isnan(auc) else round(float(auc), 4),
        "brier": None if np.isnan(brier) else round(float(brier), 4),
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

    # Feature importance log
    if fi is not None:
        imp_path = MODEL_DIR / f"prop_model_{SEGMENT_KEY}_feature_importance.json"
        imp = {str(k): float(v) for k, v in fi.to_dict().items()}
        imp_path.write_text(json.dumps(imp, indent=2), encoding="utf-8")

    print(f"{SEGMENT_KEY.upper()} model training complete")
    print("-----------------------------")
    print(f"  Training rows:    {len(X_train)}")
    print(f"  Test rows:        {len(X_test)}")
    print(f"  ROC-AUC:          {auc:.4f}" if not np.isnan(auc) else "  ROC-AUC:          n/a")
    print(f"  Brier score:      {brier:.4f}" if not np.isnan(brier) else "  Brier score:      n/a")
    print(f"  Blend weight:     {bw:.2f}")
    if top5 is not None:
        print("\n  Top 5 features:")
        for i, (k, v) in enumerate(top5.items(), 1):
            print(f"  {i}. {k:<28} {v:.6f}")
    print(f"\n  Saved: {MODEL_PATH}")
    print(f"  Saved: {FEATURES_PATH}")
    print(f"  Saved: {BLEND_PATH}")
    print(f"  Saved: {METRICS_PATH}")


if __name__ == "__main__":
    main()
