#!/usr/bin/env python3
"""
Train NHL prop ML model for NHL/scripts/step7_rank_props_nhl.py inference.

Loads graded_nhl_*.xlsx (+ synthetic), trains XGBoost, saves
models/prop_model_nhl.pkl, features json, blend_weight json.
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
MODEL_PATH = MODEL_DIR / "prop_model_nhl.pkl"
FEATURES_PATH = MODEL_DIR / "prop_model_nhl_features.json"
BLEND_PATH = MODEL_DIR / "prop_model_nhl_blend_weight.json"
CALIB_PATH = MODEL_DIR / "prop_model_nhl_calibrator.pkl"
METRICS_PATH = MODEL_DIR / "prop_model_nhl_metrics.json"
LATEST_TEST_RESULTS_PATH = ROOT / "NHL" / "data" / "outputs" / "latest_nhl_test_results.xlsx"
METADATA_PATH = MODEL_DIR / "prop_model_nhl_metadata.json"

DATE_RE = re.compile(r"graded_nhl_(?:synthetic_)?(\d{4}-\d{2}-\d{2})\.xlsx$", re.I)
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
    print("[WARN] [ML] No usable date column found - using index order (no shuffle).")
    return df.index


def _norm_cat(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.upper()


def _final_direction_series(df: pd.DataFrame, index: pd.Index | None = None) -> pd.Series:
    """
    Canonical direction with fallback:
      bet_direction -> direction -> recommended_side -> OVER
    """
    idx = index if index is not None else df.index
    bt = df["bet_direction"] if "bet_direction" in df.columns else pd.Series(np.nan, index=idx)
    dr = df["direction"] if "direction" in df.columns else pd.Series(np.nan, index=idx)
    rs = (
        df["recommended_side"]
        if "recommended_side" in df.columns
        else pd.Series(np.nan, index=idx)
    )
    out = bt.reindex(idx).copy()
    out = out.fillna(dr.reindex(idx))
    out = out.fillna(rs.reindex(idx))
    out = _norm_cat(out).replace({"NAN": np.nan, "NONE": np.nan, "NULL": np.nan, "": np.nan})
    return out.fillna("OVER")


def _pick_direction_col(df: pd.DataFrame, decided_mask: pd.Series) -> str | None:
    """
    Prefer direction columns that contain both OVER and UNDER on decided rows.
    Falls back to first available candidate.
    """
    cands = ["bet_direction", "direction", "recommended_side"]
    best = None
    best_score = -1
    for c in cands:
        if c not in df.columns:
            continue
        s = _norm_cat(df[c])
        d = s[decided_mask]
        over = int((d == "OVER").sum())
        under = int((d == "UNDER").sum())
        score = min(over, under)
        if score > best_score:
            best_score = score
            best = c
    return best


def _collect_nhl_graded_files() -> list[tuple[Path, bool]]:
    out: list[tuple[Path, bool]] = []
    for base in (ROOT / "outputs", ROOT / "NHL", ROOT / "NHL" / "outputs"):
        if not base.is_dir():
            continue
        for p in base.rglob("graded_nhl*.xlsx"):
            if "synthetic" in str(p).lower() or "synthetic" in p.parts:
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


def _nhl_synthetic_to_workbook_like(syn: pd.DataFrame) -> pd.DataFrame:
    if syn.empty:
        return syn
    idx = syn.index
    if "home_away" in syn.columns:
        ha = syn["home_away"].astype(str).str.strip().str.upper()
        ih = np.where(ha.str.startswith("H"), "HOME", np.where(ha.str.startswith("A"), "AWAY", ""))
        is_home_col = pd.Series(ih, index=idx)
    else:
        is_home_col = pd.Series("", index=idx)
    return pd.DataFrame(
        {
            "result": syn["result"],
            "edge": 0.0,
            "stat_norm": syn["prop_type"],
            "direction": syn["direction"],
            "line": syn["line"],
            "Pick Type": syn["tier"] if "tier" in syn.columns else pd.Series("Standard", index=idx),
            "is_home": is_home_col,
            "_source_file": "synthetic_graded.db",
            "_synthetic": 1,
            "_source_date": syn["game_date"].astype(str) if "game_date" in syn.columns else "",
            "_weight": syn["_weight"],
        },
        index=idx,
    )


def _sheet_usable(df: pd.DataFrame) -> bool:
    r = _first_present(df, ["result", "outcome", "grade"])
    e = _first_present(df, ["edge", "abs_edge"])
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
    dt = _first_present(df, ["def_tier", "defense_tier", "DEF_TIER"])
    if dt:
        s = df[dt].astype(str).str.strip().str.lower()
        return pd.Series(
            np.where(
                s.str.contains("weak"),
                0,
                np.where(
                    s.str.contains("avg|average"),
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
    rk = _first_present(df, ["def_rank", "opp_def_rank"])
    if rk:
        r = _to_num(df[rk]).fillna(16.0)
        return pd.Series(
            np.where(r <= 5, 3, np.where(r <= 10, 2, np.where(r <= 20, 1, 0))),
            index=df.index,
        )
    return pd.Series(1, index=df.index)


def _pp_unit_num(raw: pd.Series) -> pd.Series:
    s = raw.astype(str).str.strip().str.upper()
    return pd.Series(
        np.where(s.str.contains("PP1"), 2.0, np.where(s.str.contains("PP2"), 1.0, 0.0)),
        index=raw.index,
    )


def _goalie_conf_num(raw: pd.Series) -> pd.Series:
    s = raw.astype(str).str.strip().str.lower()
    return pd.Series(
        np.where(s.isin(["1", "true", "yes", "confirmed"]), 1.0, 0.0),
        index=raw.index,
    )


def _tier_letter_num(raw: pd.Series) -> pd.Series:
    s = raw.astype(str).str.strip().str.upper()
    m = {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0}
    return s.map(lambda x: m.get(x, 2.0)).astype(float)


def normalize_nhl_prop(raw: str) -> str:
    x = str(raw or "").strip().lower()
    xc = re.sub(r"\s+", "", x)
    if not x:
        return "unknown"
    if ("goalie" in x and "save" in x) or "saves" in x:
        return "saves"
    if "faceoff" in x:
        return "faceoffs"
    if "blocked" in x:
        return "blocked_shots"
    if "shot" in x or "sog" in x or xc == "shots":
        return "shots"
    if "assist" in x:
        return "assists"
    if "goal" in x and "against" not in x:
        return "goals"
    if "point" in x and "goal" not in x:
        return "points"
    if "toi" in x or "time on ice" in x or "time_on" in xc:
        return "toi"
    if "hit" in x and "blocked" not in x:
        return "hits"
    return re.sub(r"[^a-z0-9]+", "_", x).strip("_") or "unknown"


def _audit_and_load() -> pd.DataFrame:
    files = _collect_nhl_graded_files()
    real_frames: list[pd.DataFrame] = []
    for p, syn in files:
        block = _load_one_workbook(p, syn)
        if block is not None:
            real_frames.append(block)
    if not real_frames:
        raise FileNotFoundError(
            "No graded_nhl*.xlsx under outputs/ or NHL/ (non-synthetic)."
        )
    real_df = pd.concat(real_frames, ignore_index=True)
    n_real = len(real_df)

    syn_raw = load_synthetic_training_data("NHL", str(SYNTHETIC_DB))
    if REAL_ONLY_MODE or syn_raw.empty:
        df = real_df
        n_syn_used = 0
    else:
        syn_df = _nhl_synthetic_to_workbook_like(syn_raw)
        n_syn_cap = int(n_real * SYNTHETIC_RATIO_CAP)
        if n_syn_cap <= 0:
            syn_df = syn_df.iloc[0:0].copy()
        elif len(syn_df) > n_syn_cap:
            syn_df = syn_df.sample(n=n_syn_cap, random_state=42)
        n_syn_used = len(syn_df)
        df = pd.concat([real_df, syn_df], ignore_index=True)

    # Categorical normalization before any downstream feature engineering.
    for col in ("pick_type", "direction", "prop_type", "player", "bet_direction"):
        if col in df.columns:
            df[col] = _norm_cat(df[col])
    df["final_direction"] = _final_direction_series(df)

    print(f"Training mix - real: {n_real:,}  synthetic: {n_syn_used:,}  total: {len(df):,}")
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
    pc = _first_present(sub, ["stat_norm", "prop_type", "stat_type", "Prop"])
    if pc:
        print("-> Hit rate by prop_type (top 15):")
        g = sub.groupby(sub[pc].astype(str), dropna=False)["_hit"].mean().sort_values(ascending=False)
        print(g.head(15).to_string())
    if "final_direction" in sub.columns:
        print("-> Hit rate by direction:")
        print(sub.groupby(sub["final_direction"])["_hit"].mean().to_string())
    tc = _first_present(sub, ["pick_type", "Pick Type", "tier"])
    if tc:
        print("-> Hit rate by tier/pick_type:")
        print(sub.groupby(sub[tc].astype(str).str.strip())["_hit"].mean().to_string())
    return df


def main() -> None:
    print("=== NHL graded data audit ===\n")
    df = _audit_and_load()

    hit_col = _first_present(df, ["result", "outcome", "grade"])
    edge_col = _first_present(df, ["edge", "abs_edge"])
    if hit_col is None or edge_col is None:
        raise RuntimeError("Missing result or edge.")

    hr5 = _first_present(
        df,
        ["hit_rate_over_L5", "hit_rate_l5", "hr_L5", "hr_last5", "over_L5"],
    )
    hr10 = _first_present(
        df,
        [
            "composite_hit_rate",
            "hit_rate_over_L10",
            "hit_rate_l10",
            "hr_L10",
            "hr_last10",
        ],
    )
    hr20 = _first_present(df, ["hit_rate_over_L20", "hit_rate_l20", "hr_L20", "hr_last20"])
    hr_season = _first_present(
        df,
        ["season_hit_rate", "hit_rate_season", "hr_season", "sample_season_hit_rate"],
    )
    pick_col = _first_present(df, ["pick_type", "Pick Type"])
    prop_col = _first_present(df, ["prop_type_norm", "stat_norm", "prop_type", "stat_type", "Prop"])
    # Build canonical direction to prevent null bet_direction gaps on latest dates.
    df["final_direction"] = _final_direction_series(df)
    line_col = _first_present(df, ["line", "line_score", "Line"])
    pp_col = _first_present(df, ["pp_unit", "pp_tier", "PP Tier"])
    toi_col = _first_present(
        df,
        ["projected_toi", "toi_per_game_api", "toi_avg_L10", "toi_minutes", "Time On Ice"],
    )
    gc_col = _first_present(df, ["goalie_confirmed", "Goalie Confirmed"])
    home_col = _first_present(df, ["is_home", "home_away"])
    edge2_col = _first_present(df, ["EDGE", "edge"])
    rank_col = _first_present(df, ["RANK", "rank_score", "rank"])
    tier_col = _first_present(df, ["tier", "Tier"])
    def_tier_col = _first_present(df, ["def_tier", "defense_tier", "DEF_TIER"])
    pace_col = _first_present(df, ["pace_factor", "pace", "game_script_mult"])

    if prop_col is None:
        raise RuntimeError(f"Missing prop column. Columns: {list(df.columns)}")

    train = pd.DataFrame(index=df.index)
    train["edge"] = _to_num(df[edge2_col or edge_col])
    train["hit_rate_l5"] = _to_num(df[hr5]) if hr5 else np.nan
    train["hit_rate_l10"] = _to_num(df[hr10]) if hr10 else np.nan
    train["hit_rate_l20"] = _to_num(df[hr20]) if hr20 else np.nan
    for c in ("hit_rate_l5", "hit_rate_l10", "hit_rate_l20"):
        col = train[c]
        if col.notna().any() and col.dropna().median() > 1.0:
            train[c] = col / 100.0
    train["line"] = _to_num(df[line_col]) if line_col else np.nan
    train["direction_raw"] = _norm_cat(df["final_direction"])
    train["direction"] = _direction_num(train["direction_raw"]).astype(int)
    train["edge"] = play_side_edge(train["edge"], train["direction"])
    train["tier"] = _tier_letter_num(df[tier_col]) if tier_col else 2.0
    train["pick_tier_num"] = _pick_type_tier_num(df[pick_col]) if pick_col else 1.0
    train["defense_tier"] = _defense_tier_4(df)
    if def_tier_col:
        train["def_tier_raw"] = _norm_cat(df[def_tier_col])
    else:
        train["def_tier_raw"] = "AVG"
    train["pp_unit"] = _pp_unit_num(df[pp_col]) if pp_col else 0.0
    # Safety: only use projected/pre-game TOI. Drop if source looks like actual boxscore TOI.
    if toi_col and ("actual" not in str(toi_col).lower()):
        train["toi_minutes"] = _to_num(df[toi_col])
    else:
        train["toi_minutes"] = np.nan
        if toi_col:
            print(f"  [hygiene] Dropping TOI feature '{toi_col}' (looks post-event/actual).")
    train["goalie_confirmed"] = _goalie_conf_num(df[gc_col]) if gc_col else 0.0
    if home_col:
        ih = df[home_col].astype(str).str.strip()
        train["is_home"] = np.where(ih.isin(["1", "1.0", "true", "True", "HOME", "home"]), 1.0, 0.0)
    else:
        train["is_home"] = np.nan
    train["pace_factor"] = _to_num(df[pace_col]) if pace_col else np.nan
    train["rank"] = _to_num(df[rank_col]) if rank_col else np.nan
    if prop_col:
        prop_raw = _norm_cat(df[prop_col])
    else:
        prop_raw = pd.Series("UNKNOWN", index=df.index)
    train["prop_type"] = prop_raw.map(normalize_nhl_prop).astype(str).str.strip().str.upper()
    train["hit"] = _map_hit(df[hit_col])
    train["source_date"] = pd.to_datetime(df["_source_date"], errors="coerce")

    # Strictly keep decided rows only.
    train = train[train["hit"].isin([0.0, 1.0])].copy()
    train["hit"] = train["hit"].astype(int)
    train = train.dropna(subset=["edge"])

    # Drop rows with missing canonical prop type if strongly skewed.
    prop_missing = train["prop_type"].isin(["", "NAN", "NONE", "UNKNOWN"])
    known = train.loc[~prop_missing]
    missing = train.loc[prop_missing]
    if len(known) > 0 and len(missing) > 0:
        hr_known = float(known["hit"].mean())
        hr_missing = float(missing["hit"].mean())
        if abs(hr_missing - hr_known) >= 0.08:
            print(
                f"  [hygiene] Dropping {len(missing)} UNKNOWN/NAN prop rows "
                f"(hit skew {hr_missing:.3f} vs known {hr_known:.3f})"
            )
            train = known.copy()
    train["hit_rate_l5"] = train["hit_rate_l5"].fillna(train["hit_rate_l10"]).fillna(0.5)
    train["hit_rate_l10"] = train["hit_rate_l10"].fillna(0.5)
    train["hit_rate_l20"] = train["hit_rate_l20"].fillna(train["hit_rate_l10"]).fillna(0.5)
    train["season_hit_rate"] = _to_num(df[hr_season]).reindex(train.index) if hr_season else np.nan
    if "season_hit_rate" in train.columns:
        c = train["season_hit_rate"]
        if c.notna().any() and c.dropna().median() > 1.0:
            train["season_hit_rate"] = c / 100.0
    train["season_hit_rate"] = train["season_hit_rate"].fillna(train["hit_rate_l20"]).fillna(0.5)
    train["trend_delta"] = train["hit_rate_l5"] - train["season_hit_rate"]
    train["line"] = train["line"].fillna(0.0)
    train["toi_minutes"] = train["toi_minutes"].fillna(0.0)
    train["is_home"] = train["is_home"].fillna(0.5)
    train["pace_factor"] = train["pace_factor"].fillna(0.0)
    train["rank"] = train["rank"].fillna(0.0)

    # Normalize pick types for diagnostics and features.
    if pick_col:
        pick_norm = _norm_cat(df[pick_col])
    else:
        pick_norm = pd.Series("STANDARD", index=df.index)
    train["pick_type_norm"] = pick_norm.reindex(train.index).fillna("STANDARD")

    over_n = int((train["direction_raw"] == "OVER").sum())
    under_n = int((train["direction_raw"] == "UNDER").sum())
    print(f"  [hygiene] Direction counts (decided): OVER={over_n} UNDER={under_n}")
    if over_n == 0 or under_n == 0:
        print("  [hygiene] WARNING: direction imbalance remains after normalization.")
    print("  [hygiene] PICK_TYPE hit rates:")
    print(
        train.groupby("pick_type_norm")["hit"]
        .agg(["count", "mean"])
        .sort_values("count", ascending=False)
        .to_string()
    )

    # Strict 50/50 directional balancing.
    over_df = train[train["direction_raw"] == "OVER"].copy()
    under_df = train[train["direction_raw"] == "UNDER"].copy()
    if len(over_df) and len(under_df):
        minority_n = min(len(over_df), len(under_df))
        over_bal = over_df.sample(n=minority_n, random_state=42) if len(over_df) > minority_n else over_df
        under_bal = under_df.sample(n=minority_n, random_state=42) if len(under_df) > minority_n else under_df
        train = pd.concat([over_bal, under_bal], ignore_index=False).sample(frac=1.0, random_state=42)
        print(f"  [hygiene] Balanced directions to 1:1 -> OVER={minority_n} UNDER={minority_n}")
    else:
        print("  [hygiene] WARNING: could not 1:1 balance directions (one side missing).")

    print("  [hygiene] Bias check (balanced):")
    print(
        train.groupby("pick_type_norm")["hit"]
        .agg(["count", "mean"])
        .sort_values("count", ascending=False)
        .to_string()
    )

    if "_weight" in df.columns:
        sw = pd.to_numeric(df.loc[train.index, "_weight"], errors="coerce").fillna(1.0)
    else:
        sw = pd.Series(
            np.where(df.loc[train.index, "_synthetic"].to_numpy(dtype=float) > 0, 0.7, 1.0),
            index=train.index,
        )

    n = len(train)
    bw = blend_weight_for_n(n)
    if n < 200:
        print(
            f"\nWARNING: Only {n} decided rows for NHL - model will be trained but accuracy may be low.\n"
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
        "season_hit_rate",
        "trend_delta",
        "line",
        "direction",
        "tier",
        "pick_tier_num",
        "rank",
        "defense_tier",
        "pp_unit",
        "pace_factor",
        "toi_minutes",
        "goalie_confirmed",
        "is_home",
    ]
    X_base = train[base_cols].copy().fillna(0.0)
    X_prop = pd.get_dummies(train["prop_type"], prefix="prop", dtype=float)
    X_pick = pd.get_dummies(train["pick_type_norm"], prefix="pick", dtype=float)
    X = pd.concat([X_base, X_prop, X_pick], axis=1).fillna(0.0)
    # Strict feature pruning: drop missingness artifacts.
    X = X[[c for c in X.columns if c != "prop_nan"]]
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
        brier_raw = brier_score_loss(y_test, proba)
    except Exception:
        brier_raw = float("nan")
    y_pred = (np.asarray(proba, dtype=float) >= 0.5).astype(int)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    feats = list(X.columns)
    base_est = None
    try:
        if hasattr(model, "calibrated_classifiers_") and model.calibrated_classifiers_:
            base_est = getattr(model.calibrated_classifiers_[0], "estimator", None)
    except Exception:
        base_est = None
    fi = None
    if base_est is not None and hasattr(base_est, "feature_importances_"):
        fi = (
            pd.Series(getattr(base_est, "feature_importances_"), index=feats)
            .sort_values(ascending=False)
        )

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_TEST_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
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
    # Training log (repo-level)
    log_path = MODEL_DIR / "training_log.csv"
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sport": "nhl",
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

    # Feature importance log
    if fi is not None:
        imp_path = MODEL_DIR / "prop_model_nhl_feature_importance.json"
        imp = {str(k): float(v) for k, v in fi.to_dict().items()}
        imp_path.write_text(json.dumps(imp, indent=2), encoding="utf-8")
    metadata = {
        "model": "prop_model_nhl",
        "notes": [
            "Direction uses final_direction fallback: bet_direction -> direction -> recommended_side -> OVER.",
            "Recent slates may be OVER-heavy by design due to upstream selection/enforcement.",
            "Calibrate and evaluate by direction to monitor class imbalance drift over time.",
        ],
        "direction_counts_decided": {
            "OVER": over_n,
            "UNDER": under_n,
        },
        "training_rows_balanced": int(n),
        "test_rows_temporal_split": int(len(X_test)),
        "roc_auc": None if np.isnan(auc) else float(auc),
        "brier_raw": None if np.isnan(brier_raw) else float(brier_raw),
    }
    METADATA_PATH.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    test_export = train.loc[X_test.index].copy()
    test_export["ml_prob"] = proba
    test_export["prediction"] = y_pred
    test_export["is_win"] = y_test.values
    test_export["source_date"] = test_export["source_date"].dt.strftime("%Y-%m-%d")
    test_export.to_excel(LATEST_TEST_RESULTS_PATH, index=False)

    print("NHL Model Training Complete")
    print("-----------------------------")
    print(f"  Training rows:    {len(X_train)}")
    print(f"  Test rows:        {len(X_test)}")
    print(f"  ROC-AUC:          {auc:.4f}" if not np.isnan(auc) else "  ROC-AUC:          n/a")
    print(f"  Brier (raw):      {brier_raw:.4f}" if not np.isnan(brier_raw) else "  Brier (raw):      n/a")
    print(f"  Blend weight:     {bw:.2f}")
    if fi is not None:
        print("\n  Top 20 features:")
        for i, (k, v) in enumerate(fi.head(20).items(), 1):
            print(f"  {i}. {k:<28} {v:.6f}")
    print(f"\n  Saved: {MODEL_PATH}")
    print(f"  Saved: {FEATURES_PATH}")
    print(f"  Saved: {BLEND_PATH}")
    print(f"  Saved: {METRICS_PATH}")
    print(f"  Saved: {METADATA_PATH}")
    print(f"  Saved: {LATEST_TEST_RESULTS_PATH}")


if __name__ == "__main__":
    main()
