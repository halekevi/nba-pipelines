#!/usr/bin/env python3
"""Train unified XGBoost edge classifier + Platt calibration on graded history.

Uses edge_feature_engineering.build_feature_vector() (play-side edge on rows for step7b
implied_prob only). The raw ``edge`` column is always excluded from the tree inputs
(see ALWAYS_EXCLUDE_FROM_EDGE_TRAINING). Retrain after feature-list changes.

Optional: ``--input-csv data/retrain_dataset.csv [--sport NBA] [--dry-run]`` loads
``result_binary`` as the label and maps CSV columns for ``build_feature_vector``.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

from edge_feature_engineering import (
    FEATURE_COLUMNS,
    WNBA_FEATURE_COLUMNS,
    _direction_series,
    build_feature_vector,
    drop_nba_features_below_fill_threshold,
    drop_wnba_features_below_fill_threshold,
    fill_minutes_cv_median_by_sport,
)
from edge_ml_bundle import EdgeCalibratedModel

SCRIPT_NAME = "train_edge_model"

# Label-adjacent columns: never use these as tree inputs (raw `edge` / abs_edge still exist on
# rows for step7b implied_prob + edge_score; they are not read from edge_model_features.json).
ALWAYS_EXCLUDE_FROM_EDGE_TRAINING: frozenset[str] = frozenset(
    {"edge", "result_binary", "hit", "outcome"}
)

_COMBINED_GRADED_DATE = re.compile(r"combined_tickets_graded_(\d{4}-\d{2}-\d{2})", re.IGNORECASE)


def _dedupe_combined_ticket_paths(items: list[tuple[str | None, Path]]) -> tuple[list[tuple[str | None, Path]], int]:
    """One combined_tickets_graded workbook per (parent dir, slate date): prefer exact filename, else newest mtime."""
    combined: list[tuple[str | None, Path]] = []
    other: list[tuple[str | None, Path]] = []
    for it in items:
        h, p = it
        if "combined_tickets_graded" in p.name.lower() and p.suffix.lower() == ".xlsx":
            if _COMBINED_GRADED_DATE.search(p.name):
                combined.append(it)
                continue
        other.append(it)

    groups: dict[tuple[Path, str], list[tuple[str | None, Path]]] = defaultdict(list)
    for it in combined:
        _h, p = it
        m = _COMBINED_GRADED_DATE.search(p.name)
        if m is None:
            continue
        key = (p.resolve().parent, m.group(1))
        groups[key].append(it)

    picked: list[tuple[str | None, Path]] = []
    skipped = 0
    for (_parent, date_str), group in groups.items():
        exact = f"combined_tickets_graded_{date_str}.xlsx"
        exact_hits = [it for it in group if it[1].name.lower() == exact.lower()]
        candidates = exact_hits if exact_hits else group
        best = max(candidates, key=lambda it: it[1].stat().st_mtime)
        picked.append(best)
        skipped += len(group) - 1

    return other + picked, skipped


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _hit_column(df: pd.DataFrame) -> pd.Series | None:
    """Resolve binary hit labels (0/1). Handles numeric columns and HIT/MISS/VOID text (graded exports)."""
    candidates = ("hit", "Hit", "HIT", "result", "graded", "leg_result", "leg_hit")
    for c in candidates:
        if c not in df.columns:
            continue
        s = df[c]
        num = pd.to_numeric(s, errors="coerce")
        if num.notna().sum() >= max(3, int(len(df) * 0.2)):
            return num
        up = s.astype(str).str.strip().str.upper()
        arr = np.full(len(df), np.nan, dtype=float)
        arr = np.where(up.isin(["HIT", "WIN", "W", "1", "TRUE", "YES"]), 1.0, arr)
        arr = np.where(up.isin(["MISS", "LOSS", "L", "0", "FALSE", "NO"]), 0.0, arr)
        ser = pd.Series(arr, index=df.index)
        if ser.notna().sum() >= max(3, int(len(df) * 0.05)):
            return ser
    for c in df.columns:
        cl = str(c).lower()
        if cl in ("hit", "result", "graded") or cl.endswith("_hit") or cl == "leg_result":
            s = df[c]
            num = pd.to_numeric(s, errors="coerce")
            if num.notna().sum() >= max(3, int(len(df) * 0.2)):
                return num
            up = s.astype(str).str.strip().str.upper()
            arr = np.full(len(df), np.nan, dtype=float)
            arr = np.where(up.isin(["HIT", "WIN", "W", "1", "TRUE", "YES"]), 1.0, arr)
            arr = np.where(up.isin(["MISS", "LOSS", "L", "0", "FALSE", "NO"]), 0.0, arr)
            ser = pd.Series(arr, index=df.index)
            if ser.notna().sum() >= max(3, int(len(df) * 0.05)):
                return ser
    return None


def _norm_sport_folder(name: str) -> str | None:
    m = str(name or "").strip().upper()
    if m in ("NBA", "CBB", "NHL", "SOCCER", "MLB"):
        return "SOCCER" if m == "SOCCER" else m
    return None


def _infer_sport_from_graded_filename(path: Path) -> str | None:
    """Map graded_*.xlsx names to unified training sport codes."""
    n = path.name.lower()
    if "graded_nhl" in n:
        return "NHL"
    if "graded_mlb" in n:
        return "MLB"
    if "graded_soccer" in n:
        return "SOCCER"
    if "graded_wcbb" in n or "graded_cbb" in n:
        return "CBB"
    if "graded_nba1h" in n:
        return "NBA1H"
    if "graded_nba1q" in n:
        return "NBA1Q"
    if "graded_nba" in n:
        return "NBA"
    return None


def _should_skip_graded_path(path: Path, include_synthetic: bool) -> bool:
    parts = {p.lower() for p in path.parts}
    if ".venv" in parts or "node_modules" in parts or ".git" in parts:
        return True
    if not include_synthetic and "synthetic" in parts:
        return True
    return False


def _discover_graded_files(
    root: Path, *, recursive_outputs: bool, include_synthetic: bool
) -> tuple[list[tuple[str | None, Path]], int]:
    """Return ((sport_hint or None, path), n_combined_paths_skipped_by_file_dedupe)."""
    seen: set[Path] = set()
    out: list[tuple[str | None, Path]] = []

    def add(sp_key: str | None, p: Path) -> None:
        if not p.is_file() or _should_skip_graded_path(p, include_synthetic):
            return
        r = p.resolve()
        if r in seen:
            return
        seen.add(r)
        out.append((sp_key, p))

    sports = ("NBA", "CBB", "NHL", "Soccer", "MLB")
    for sp in sports:
        sp_key = "SOCCER" if sp.lower() == "soccer" else sp.upper()
        dirs = [
            root / sp / "outputs" / "graded",
            root / sp / "outputs",
            root / "outputs" / "graded",
            root / "outputs",
        ]
        for d in dirs:
            if not d.is_dir():
                continue
            for pat in ("*graded*.csv", "*graded*.xlsx"):
                for p in d.glob(pat):
                    add(sp_key, p)
            for p in d.glob("combined_tickets_graded_*.xlsx"):
                add(sp_key, p)

    out_dir = root / "outputs"
    if recursive_outputs and out_dir.is_dir():
        for p in out_dir.rglob("*graded*.xlsx"):
            inferred = _infer_sport_from_graded_filename(p)
            add(inferred, p)
        for p in out_dir.rglob("*graded*.csv"):
            inferred = _infer_sport_from_graded_filename(p)
            add(inferred, p)
        for p in out_dir.rglob("combined_tickets_graded_*.xlsx"):
            add(None, p)

    extra_roots = [
        root / "NBA" / "data" / "outputs",
        root / "data" / "outputs",
    ]
    for er in extra_roots:
        if not er.is_dir():
            continue
        for p in er.rglob("*graded*.xlsx"):
            inferred = _infer_sport_from_graded_filename(p)
            add(inferred or "NBA", p)
        for p in er.rglob("*graded*.csv"):
            inferred = _infer_sport_from_graded_filename(p)
            add(inferred or "NBA", p)

    out, n_combined_dup = _dedupe_combined_ticket_paths(out)
    return out, n_combined_dup


def _read_table(path: Path, sport_hint: str) -> pd.DataFrame | None:
    try:
        if path.suffix.lower() == ".csv":
            return pd.read_csv(path, low_memory=False, encoding="utf-8-sig")
        if path.suffix.lower() in (".xlsx", ".xlsm"):
            xl = pd.ExcelFile(path)
            skip_sheets = {
                "summary",
                "by pick type",
                "by tier",
                "prop type x direction",
                "by direction",
                "by minutes tier",
                "by def tier",
                "by def rank",
                "by player role",
                "by shot role",
                "void reasons",
            }
            preferred = [
                "GRADED",
                "graded",
                "Box Raw",
                "box raw",
                "ALL",
                "All",
                "ELIGIBLE",
                sport_hint,
                sport_hint.upper(),
                sport_hint.lower(),
                "Sheet1",
            ]
            for cand in preferred:
                if cand and cand in xl.sheet_names:
                    df = pd.read_excel(path, sheet_name=cand, engine="openpyxl")
                    if len(df) > 0 and len(df.columns) > 0:
                        return df
            for sn in xl.sheet_names:
                if str(sn).strip().lower() in skip_sheets:
                    continue
                df = pd.read_excel(path, sheet_name=sn, engine="openpyxl")
                if len(df) > 0 and len(df.columns) > 0:
                    if _hit_column(df) is not None:
                        return df
            for sn in xl.sheet_names:
                df = pd.read_excel(path, sheet_name=sn, engine="openpyxl")
                if len(df) > 0 and len(df.columns) > 0:
                    return df
            return None
    except Exception as e:
        print(f"  [WARN] Failed to read {path}: {e}")
    return None


def _dedupe_graded_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Drop duplicate prop rows across dated exports (keep last). Returns (df, n_removed)."""
    n0 = len(df)
    colmap = {str(c).lower(): c for c in df.columns}

    def col(*names: str) -> pd.Series | None:
        for n in names:
            if n in df.columns:
                return df[n]
            if n.lower() in colmap:
                return df[colmap[n.lower()]]
        return None

    sport = col("sport")
    player = col("player_name", "player", "pp_player", "Player")
    gdate = col("game_date", "slate_date", "date", "start_time", "Game Date")
    prop = col("prop_type", "prop_type_norm", "prop_norm", "stat_norm", "stat_type")
    line = col("line", "line_score")
    direc = col("bet_direction", "recommended_side", "direction", "final_bet_direction")
    if sport is None or player is None or gdate is None or prop is None or line is None or direc is None:
        return df, 0

    tmp = df.copy()
    tmp["_dk_sport"] = sport.astype(str).str.strip().str.upper()
    tmp["_dk_player"] = player.astype(str).str.strip().str.lower()
    tmp["_dk_gd"] = pd.to_datetime(gdate, errors="coerce").dt.strftime("%Y-%m-%d")
    tmp["_dk_prop"] = prop.astype(str).str.strip().str.lower()
    tmp["_dk_line"] = pd.to_numeric(line, errors="coerce").astype(str)
    tmp["_dk_dir"] = direc.astype(str).str.strip().str.upper()
    tmp["_dk_key"] = list(
        zip(
            tmp["_dk_sport"],
            tmp["_dk_player"],
            tmp["_dk_gd"],
            tmp["_dk_prop"],
            tmp["_dk_line"],
            tmp["_dk_dir"],
        )
    )
    tmp = tmp.sort_values("_source_path", kind="mergesort", na_position="last")
    tmp = tmp.drop_duplicates(subset=["_dk_key"], keep="last")
    tmp = tmp.drop(
        columns=["_dk_sport", "_dk_player", "_dk_gd", "_dk_prop", "_dk_line", "_dk_dir", "_dk_key"],
        errors="ignore",
    )
    return tmp.reset_index(drop=True), n0 - len(tmp)


def load_all_graded(
    root: Path,
    *,
    recursive_outputs: bool = True,
    dedupe: bool = True,
    include_synthetic: bool = False,
) -> tuple[pd.DataFrame, int]:
    rows: list[pd.DataFrame] = []
    per_file_log: list[str] = []
    discovered, n_combined_dup = _discover_graded_files(
        root, recursive_outputs=recursive_outputs, include_synthetic=include_synthetic
    )
    if n_combined_dup:
        per_file_log.append(
            f"  [dedupe files] omitted {n_combined_dup} redundant combined_tickets_graded paths (same folder + slate date)"
        )
    for sp_hint, path in discovered:
        hint = sp_hint or _infer_sport_from_graded_filename(path) or "NBA"
        df = _read_table(path, hint)
        if df is None or df.empty:
            per_file_log.append(f"  skip (empty): {path}")
            continue
        if "combined_tickets_graded" in path.name.lower() and "sport" not in df.columns:
            per_file_log.append(f"  skip (combined file without sport column): {path}")
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
                df["sport"] = _norm_sport_folder(parts[0]) or hint
            else:
                df["sport"] = hint
        else:
            df["sport"] = _sanitize_sport_with_hint(df["sport"], hint)
        df["_source_path"] = str(path)
        rows.append(df)
        per_file_log.append(f"  loaded {len(df)} rows from {path} (sport={df['sport'].iloc[0]})")
    for line in per_file_log:
        print(line)
    if not rows:
        return pd.DataFrame(), n_combined_dup
    merged = pd.concat(rows, ignore_index=True)
    if dedupe and len(merged) > 0 and "_source_path" in merged.columns:
        merged, removed = _dedupe_graded_rows(merged)
        if removed:
            print(f"\n  [dedupe] removed {removed} duplicate rows (same sport/player/date/prop/line/dir)")
    return merged, n_combined_dup


def _normalize_sport_series(s: pd.Series) -> pd.Series:
    m = s.astype(str).str.strip().str.upper()
    return m.replace({"SOC": "SOCCER", "FOOTBALL": "SOCCER"})


_ALLOWED_TRAINING_SPORTS = frozenset({"NBA", "CBB", "NHL", "SOCCER", "MLB", "NBA1H", "NBA1Q"})


def _sanitize_sport_with_hint(s: pd.Series, hint: str) -> pd.Series:
    """Replace junk numeric sport codes (e.g. Excel 13.0) with filename/path hint."""
    hint_u = str(hint or "NBA").strip().upper()
    out = s.astype(str).str.strip().str.upper()
    out = out.replace({"SOC": "SOCCER", "FOOTBALL": "SOCCER"})
    num_junk = out.str.fullmatch(r"\d+\.?\d*", na=False)
    bad = num_junk | out.isin(["", "NAN", "NONE", "NULL"]) | ~out.isin(_ALLOWED_TRAINING_SPORTS)
    out = out.where(~bad, hint_u)
    return out


def _adapt_retrain_csv_for_feature_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    """
    Map build_retrain_dataset / retrain_dataset.csv columns onto names that
    `build_feature_vector` reads via `_first_col` / `_direction_series`.

    `_prop_type_key` uses ``stat_type``, ``stat_norm``, ``prop_type``, ``prop_norm`` (not ``prop``).
    ``edge`` is read only from a column named ``edge`` (coalesce ``edge_score`` here).
    ``composite_hit_rate`` is filled from ``blended_score`` when present.
    """
    out = df.copy()
    if "prop_type" not in out.columns and "prop" in out.columns:
        out["prop_type"] = out["prop"].astype(str)
    if "bet_direction" not in out.columns and "direction" in out.columns:
        out["bet_direction"] = out["direction"].astype(str).str.strip().str.upper()
    if "game_date" not in out.columns:
        gd = pd.Series(pd.NaT, index=out.index)
        for c in ("graded_date", "slate_date", "event_date", "game_datetime", "created_at", "start_time"):
            if c in out.columns:
                t = pd.to_datetime(out[c], errors="coerce")
                gd = gd.where(gd.notna(), t)
        fd = (
            pd.to_datetime(out["file_date"], errors="coerce")
            if "file_date" in out.columns
            else pd.Series(pd.NaT, index=out.index)
        )
        gd = gd.where(gd.notna(), fd)
        if "step8_game_date" in out.columns:
            st = out["step8_game_date"].astype(str).str.strip()
            st = st.replace("", pd.NA)
            sg = pd.to_datetime(st, errors="coerce")
            gd = sg.where(sg.notna(), gd)
        out["game_date"] = gd
    else:
        out["game_date"] = pd.to_datetime(out["game_date"], errors="coerce")

    e = pd.to_numeric(out["edge"], errors="coerce") if "edge" in out.columns else pd.Series(np.nan, index=out.index)
    if "edge_score" in out.columns:
        es = pd.to_numeric(out["edge_score"], errors="coerce")
        e = e.where(e.notna(), es)
    out["edge"] = e

    if "blended_score" in out.columns:
        bl = pd.to_numeric(out["blended_score"], errors="coerce")
        if "composite_hit_rate" in out.columns:
            ch = pd.to_numeric(out["composite_hit_rate"], errors="coerce")
            out["composite_hit_rate"] = ch.where(ch.notna(), bl)
        else:
            out["composite_hit_rate"] = bl
    return out


def _load_retrain_csv_as_raw(path: Path, sport_filter: str | None, *, source_label: str) -> pd.DataFrame:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        print(f"[ERROR] --input-csv not found: {path}")
        return pd.DataFrame()
    df = pd.read_csv(path, low_memory=False, encoding="utf-8-sig")
    n_loaded = len(df)
    if sport_filter:
        sf = str(sport_filter).strip().upper()
        if "sport" not in df.columns:
            print("[ERROR] --input-csv has no sport column.")
            return pd.DataFrame()
        norm_sp = _normalize_sport_series(df["sport"])
        mask = norm_sp.astype(str).str.strip().str.upper() == sf
        if not bool(mask.any()):
            print(f"[WARN] No rows with sport=={sf!r} after normalization (loaded {n_loaded:,} rows).")
        df = df.loc[mask].copy()
    if "result_binary" not in df.columns:
        print("[ERROR] --input-csv must include a result_binary column.")
        return pd.DataFrame()
    rb = pd.to_numeric(df["result_binary"], errors="coerce")
    df = df.loc[rb.notna()].copy()
    rb = pd.to_numeric(df["result_binary"], errors="coerce")
    df["_hit_y"] = rb.astype(float)
    df = df.loc[df["_hit_y"].isin([0.0, 1.0])].copy()
    df["_source_path"] = source_label
    return _adapt_retrain_csv_for_feature_pipeline(df)


def _prepare_features(df: pd.DataFrame, *, skip_median_fill: bool = False) -> pd.DataFrame:
    df = df.copy()
    df["sport"] = _normalize_sport_series(df["sport"])
    parts: list[pd.DataFrame] = []
    for sp in df["sport"].dropna().unique():
        sp_str = str(sp).strip().upper()
        sub = df.loc[df["sport"] == sp].copy()
        parts.append(build_feature_vector(sub, sp_str))
    out = pd.concat(parts, ignore_index=True)
    out = fill_minutes_cv_median_by_sport(out)

    # Graded "Box Raw" exports often omit hit-rate columns but still have edge / line / scores.
    keep = (
        out["composite_hit_rate"].notna()
        | out["hit_rate_L5"].notna()
        | out["hit_rate_L10"].notna()
        | out["edge"].notna()
        | out["line_score"].notna()
        | out["prop_score"].notna()
    )
    out = out.loc[keep].copy()

    enc_cols = (
        "tier_encoded",
        "tier_era",
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
            out[c] = _to_num_safe(out[c])
            if skip_median_fill:
                continue
            med = out.groupby("sport_encoded")[c].transform("median")
            out[c] = out[c].fillna(med)
            out[c] = _to_num_safe(out[c]).fillna(float(_to_num_safe(out[c]).median()))
    return out


def _to_num_safe(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


MIN_SPORT_ROWS = 200
MAX_CLASS_DOMINANCE_PCT = 90.0  # skip if dominant hit class > 90%
MIN_HOLDOUT_ROWS_PER_SPORT = 50

# Slice isotonic: fit on a stratified subset of TRAIN only (disjoint from holdout `te` used for Platt).
DEFAULT_SLICE_ISOTONIC_MIN_N = 200
# WNBA uses the same per-sport isotonic path when graded rows >= min_n (default 200).
WNBA_SLICE_ISOTONIC_MIN_N = 200
ISO_CALIB_TRAIN_FRAC = 0.15
# Deactivated in prod: rows may remain in unified training history; do not allocate isotonic calibrators.
INACTIVE_SPORTS = frozenset({"CBB"})


def _hit_class_balance(y: pd.Series) -> tuple[int, int, float]:
    """Counts of y==1 / y==0 and pct of dominant class (0-100)."""
    yv = pd.to_numeric(y, errors="coerce")
    n1 = int((yv == 1).sum())
    n0 = int((yv == 0).sum())
    n = n1 + n0
    if n == 0:
        return 0, 0, 0.0
    dom = max(n1, n0) / n * 100.0
    return n1, n0, dom


def _sport_skip_reason(n_rows: int, dom_pct: float) -> str | None:
    if n_rows < MIN_SPORT_ROWS:
        return f"only {n_rows} rows / {dom_pct:.1f}% class imbalance"
    if dom_pct > MAX_CLASS_DOMINANCE_PCT:
        return f"only {n_rows} rows / {dom_pct:.1f}% class imbalance"
    return None


def _print_nba1h_hit_derivation_debug(raw: pd.DataFrame) -> None:
    """First 5 NBA1H rows: columns relevant to hit / direction / result (for half-game QA)."""
    if raw.empty or "sport" not in raw.columns:
        return
    m = _normalize_sport_series(raw["sport"]).astype(str).str.upper().eq("NBA1H")
    if not m.any():
        return
    sub = raw.loc[m].head(5)
    pat = re.compile(
        r"direction|result|hit|actual|margin|line|pick|graded|void|outcome|scored|slate",
        re.I,
    )
    interesting = [c for c in sub.columns if pat.search(str(c)) or c == "_hit_y"]
    interesting = sorted(set(interesting))[:25]
    if not interesting:
        interesting = list(sub.columns[:12])
    print("\n[NBA1H hit derivation] First 5 rows (hit-related columns):")
    try:
        print(sub[interesting].to_string())
    except Exception as e:
        print(f"  (could not format table: {e})")


def _raw_columns_leakage_scan(raw: pd.DataFrame) -> list[str]:
    """Flag raw workbook columns that look post-game (should not feed features)."""
    bad_sub = (
        "result",
        "outcome",
        "actual_value",
        "scored",
        "graded",
        "final_stat",
        "leg_result",
        "final_margin",
    )
    found: list[str] = []
    for c in raw.columns:
        cl = str(c).strip().lower()
        if any(b in cl for b in bad_sub):
            found.append(str(c))
    return sorted(set(found))


def _feature_name_leakage_matches(name: str) -> bool:
    n = str(name).lower()
    needles = (
        "result",
        "outcome",
        "actual_value",
        "scored",
        "graded",
        "final_stat",
        "leg_result",
    )
    return any(x in n for x in needles)


def _correlations_with_target(
    tr: pd.DataFrame, sport: str, feature_cols: list[str], y_col: str = "y"
) -> dict[str, float]:
    m = tr["sport"].astype(str).str.strip().str.upper() == sport.upper()
    if int(m.sum()) < 30:
        return {}
    sub = tr.loc[m]
    y = pd.to_numeric(sub[y_col], errors="coerce")
    out: dict[str, float] = {}
    for f in feature_cols:
        if f not in sub.columns:
            continue
        x = pd.to_numeric(sub[f], errors="coerce")
        ok = x.notna() & y.notna()
        if int(ok.sum()) < 30:
            continue
        xv = x[ok].to_numpy(dtype=float)
        yv = y[ok].to_numpy(dtype=float)
        if np.std(xv) < 1e-12 or np.std(yv) < 1e-12:
            continue
        r = float(np.corrcoef(xv, yv)[0, 1])
        if np.isfinite(r):
            out[f] = r
    return out


def _auc_safe(y: object, p: object) -> float | None:
    yv = np.asarray(y).astype(int)
    pv = np.asarray(p, dtype=float)
    if len(yv) < 5 or len(np.unique(yv)) < 2:
        return None
    try:
        return float(roc_auc_score(yv, pv))
    except ValueError:
        return None


def _infer_event_dates_from_source_paths(paths: pd.Series) -> pd.Series:
    """Parse YYYY-MM-DD from outputs/.../YYYY-MM-DD/... or graded_*_YYYY-MM-DD.xlsx in path."""
    norm = paths.astype(str).str.replace("\\", "/", regex=False)
    d_folder = norm.str.extract(r"/(\d{4}-\d{2}-\d{2})/", expand=False)
    d_file = norm.str.extract(r"(?i)graded_[^/]+_(\d{4}-\d{2}-\d{2})\.(?:xlsx|csv)", expand=False)
    t1 = pd.to_datetime(d_folder, errors="coerce")
    t2 = pd.to_datetime(d_file, errors="coerce")
    return t1.where(t1.notna(), t2)


def _event_date_series_for_raw(
    raw: pd.DataFrame, *, temporal_date_column: str | None = None
) -> tuple[pd.Series, str]:
    """Prefer game_date, graded_date, created_at; else slate_date / date / start_time; else _source_path."""
    colmap = {str(c).lower(): c for c in raw.columns}
    if temporal_date_column:
        tdc = str(temporal_date_column).strip()
        if tdc:
            col: str | None = None
            if tdc in raw.columns:
                col = tdc
            elif tdc.lower() in colmap:
                col = colmap[tdc.lower()]
            if col is not None:
                s = pd.to_datetime(raw[col], errors="coerce")
                n_ok = int(s.notna().sum())
                need = max(5, int(len(raw) * 0.02))
                if n_ok >= need:
                    return s, str(col)
                print(
                    f"[WARN] --temporal-date-column {tdc!r}: only {n_ok}/{len(raw)} parseable dates "
                    f"(need>={need}); falling back to auto-detect."
                )
    preferred = ("game_date", "graded_date", "created_at")
    for key in preferred:
        col: str | None = None
        if key in raw.columns:
            col = key
        elif key.lower() in colmap:
            col = colmap[key.lower()]
        if col is None:
            continue
        s = pd.to_datetime(raw[col], errors="coerce")
        n_ok = int(s.notna().sum())
        if n_ok >= max(5, int(len(raw) * 0.02)):
            return s, str(col)
    for key in ("slate_date", "date", "start_time", "Game Date"):
        col = None
        if key in raw.columns:
            col = key
        elif str(key).lower() in colmap:
            col = colmap[str(key).lower()]
        if col is None:
            continue
        s = pd.to_datetime(raw[col], errors="coerce")
        n_ok = int(s.notna().sum())
        if n_ok >= max(5, int(len(raw) * 0.02)):
            return s, str(col)
    if "_source_path" in raw.columns:
        s_path = _infer_event_dates_from_source_paths(raw["_source_path"])
        n_path = int(s_path.notna().sum())
        if n_path >= max(5, int(len(raw) * 0.02)):
            return s_path, "_source_path (YYYY-MM-DD from export folder or graded_*_date filename)"
    return pd.Series(pd.NaT, index=raw.index), ""


def _stress_player_key_series(raw: pd.DataFrame) -> pd.Series:
    colmap = {str(c).lower(): c for c in raw.columns}
    for name in ("player_name", "player", "pp_player", "Player"):
        if name in raw.columns:
            return raw[name].astype(str).str.strip().str.lower()
        if name.lower() in colmap:
            return raw[colmap[name.lower()]].astype(str).str.strip().str.lower()
    return pd.Series("", index=raw.index, dtype=str)


def _stratify_series_for_split(sub: pd.DataFrame) -> pd.Series | None:
    d = pd.to_numeric(sub["direction_encoded"], errors="coerce").fillna(0).astype(int)
    vc = d.value_counts()
    if d.nunique() >= 2 and int(vc.min()) >= 2:
        return d
    y = sub["y"].astype(int)
    vc2 = y.value_counts()
    if y.nunique() >= 2 and int(vc2.min()) >= 2:
        return y
    return None


def _fit_xgb_platt_auc(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    spw: float,
) -> float | None:
    ytr = y_train.astype(int).to_numpy()
    yte = y_test.astype(int).to_numpy()
    if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2:
        return None
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
    p_hold = model.predict_proba(X_test)[:, 1].reshape(-1, 1)
    platt_lr = LogisticRegression(C=1e12, max_iter=2000, random_state=42, solver="lbfgs")
    platt_lr.fit(p_hold, y_test)
    calibrated = EdgeCalibratedModel(model, platt_lr)
    prob_test = calibrated.predict_proba(X_test)[:, 1]
    return _auc_safe(yte, prob_test)


def _apply_step7b_nhl_soccer_ml_cap() -> None:
    path = Path(__file__).resolve().parent / "step7b_edge_score.py"
    text = path.read_text(encoding="utf-8")
    if "if sp in (" in text and "0.15 * pd.Series(ml_prob, index=df2.index)" in text:
        print("\n[Fix B] step7b_edge_score.py already applies NHL/SOCCER ml_prob cap — no change.")
        return
    old = "    blended = 0.3 * pd.Series(ml_prob, index=df2.index) + 0.7 * comp\n"
    new = (
        "    if sp in (\"NHL\", \"SOCCER\"):\n"
        "        blended = 0.15 * pd.Series(ml_prob, index=df2.index) + 0.85 * comp\n"
        "    else:\n"
        "        blended = 0.3 * pd.Series(ml_prob, index=df2.index) + 0.7 * comp\n"
    )
    if old not in text:
        print("[WARN] Fix B: expected blended_score line not found in step7b_edge_score.py — no change.")
        return
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("\n[Fix B] Patched step7b_edge_score.py: NHL/SOCCER blended_score uses ml_prob weight 0.15.")


def _run_stress_test_nhl_soccer(root: Path, args: argparse.Namespace) -> str:
    print("\n" + "=" * 72)
    print("STRESS TEST: NHL + SOCCER (isolated; does not write edge_model_unified.pkl)")
    print("=" * 72)

    raw, _ncomb = load_all_graded(
        root,
        recursive_outputs=not args.no_recursive_outputs,
        dedupe=not args.no_dedupe,
        include_synthetic=args.include_synthetic,
    )
    if raw.empty:
        print("[ERROR] No graded data for stress test.")
        return "A"

    raw["sport"] = _normalize_sport_series(raw["sport"])
    raw = raw.loc[raw["_hit_y"].isin([0.0, 1.0])].copy()
    raw = raw.loc[raw["sport"].isin(["NHL", "SOCCER"])].copy()
    if raw.empty:
        print("[ERROR] No NHL or SOCCER rows after filtering.")
        return "A"

    _dt, date_col = _event_date_series_for_raw(raw)
    ddesc = date_col if date_col else "(none: tried game_date, graded_date, created_at, fallbacks, _source_path)"
    print(f"\nStress test date column used: {ddesc!r}")
    raw = raw.copy()
    raw["_stress_event_dt"] = _dt
    raw["_stress_player_key"] = _stress_player_key_series(raw)

    df = _prepare_features(raw)
    df["y"] = df["_hit_y"].astype(int)

    print("\n--- Per-sport quality (stress subset) ---")
    sports_to_drop: list[str] = []
    for sp in sorted(df["sport"].unique()):
        sub_m = df["sport"] == sp
        n = int(sub_m.sum())
        n1, n0, dom_pct = _hit_class_balance(df.loc[sub_m, "y"])
        reason = _sport_skip_reason(n, dom_pct)
        if reason:
            print(f"  Skipping {sp}: {reason}")
            sports_to_drop.append(str(sp))
    if sports_to_drop:
        df = df.loc[~df["sport"].isin(sports_to_drop)].copy()

    if df.empty:
        print("[ERROR] No rows left after quality filters.")
        return "A"

    features_active = list(FEATURE_COLUMNS)
    for c in FEATURE_COLUMNS:
        if c not in df.columns:
            df[c] = np.nan

    present_ps = sorted({str(x) for x in df["sport"].unique() if str(x) in ("NHL", "SOCCER")})
    temporal_overfit_sp: list[str] = []
    player_overfit_sp: list[str] = []
    high_dep_features: set[str] = set()

    for sp in ("NHL", "SOCCER"):
        if sp not in present_ps:
            print(f"\n=== {sp}: not present after filters — skipping ===")
            continue

        sub = df[df["sport"].astype(str) == sp].copy().reset_index(drop=True)
        pos = int((sub["y"] == 1).sum())
        neg = int((sub["y"] == 0).sum())
        spw = (neg / pos) if pos > 0 else 1.0

        print(f"\n{'=' * 72}\nTASK 1 — Temporal vs random ({sp})\n{'=' * 72}")
        sub_d = sub.dropna(subset=["_stress_event_dt"]).copy()
        auc_rand: float | None = None
        auc_temp: float | None = None
        if len(sub_d) < 80:
            print(f"  Random-split ROC-AUC (holdout, calibrated): {sp} = n/a (dated cohort n={len(sub_d)})")
            print(f"Temporal ROC-AUC: {sp} = n/a")
            print("  (insufficient dated rows for stable 80/20 temporal slice)")
        else:
            strat = _stratify_series_for_split(sub_d)
            tr_r, te_r = train_test_split(sub_d, test_size=0.2, random_state=42, stratify=strat)
            auc_rand = _fit_xgb_platt_auc(
                tr_r[features_active].astype(float),
                tr_r["y"],
                te_r[features_active].astype(float),
                te_r["y"],
                spw,
            )

            sub_sorted = sub_d.sort_values("_stress_event_dt", kind="mergesort")
            n = len(sub_sorted)
            k = int(n * 0.8)
            tr_t = sub_sorted.iloc[:k]
            te_t = sub_sorted.iloc[k:]
            if (
                k >= 10
                and (n - k) >= 10
                and len(np.unique(tr_t["y"].to_numpy())) >= 2
                and len(np.unique(te_t["y"].to_numpy())) >= 2
            ):
                auc_temp = _fit_xgb_platt_auc(
                    tr_t[features_active].astype(float),
                    tr_t["y"],
                    te_t[features_active].astype(float),
                    te_t["y"],
                    spw,
                )

            r_s = "n/a" if auc_rand is None else f"{auc_rand:.4f}"
            print(f"  Random-split ROC-AUC (holdout, calibrated): {sp} = {r_s}")
            t_s = "n/a" if auc_temp is None else f"{auc_temp:.4f}"
            print(f"Temporal ROC-AUC: {sp} = {t_s}")
            if auc_rand is not None and auc_temp is not None:
                if (auc_rand - auc_temp) > 0.10:
                    print("  TEMPORAL OVERFIT — model memorized historical patterns")
                    temporal_overfit_sp.append(sp)
                else:
                    print("  TEMPORAL OK — signal is stable")
            elif auc_temp is None:
                print("  (Temporal AUC not computed: class imbalance or small newest-20% test)")

        print(f"\n{'=' * 72}\nTASK 2 — Player holdout ({sp})\n{'=' * 72}")
        keys = sub["_stress_player_key"].fillna("").astype(str).str.strip()
        players_arr = np.array(sorted(keys.unique()))
        rng = np.random.RandomState(43)
        auc_p: float | None = None
        if len(players_arr) < 5:
            print(f"Player-holdout ROC-AUC: {sp} = n/a (unique players={len(players_arr)})")
        else:
            n_ho = max(1, int(round(0.2 * len(players_arr))))
            holdout_p = set(rng.choice(players_arr, size=n_ho, replace=False))
            tr_m = ~keys.isin(holdout_p)
            te_m = keys.isin(holdout_p)
            s_tr = sub.loc[tr_m]
            s_te = sub.loc[te_m]
            if (
                len(s_tr) >= 30
                and len(s_te) >= 15
                and len(np.unique(s_tr["y"].to_numpy())) >= 2
                and len(np.unique(s_te["y"].to_numpy())) >= 2
            ):
                auc_p = _fit_xgb_platt_auc(
                    s_tr[features_active].astype(float),
                    s_tr["y"],
                    s_te[features_active].astype(float),
                    s_te["y"],
                    spw,
                )
        p_s = "n/a" if auc_p is None else f"{auc_p:.4f}"
        print(f"Player-holdout ROC-AUC: {sp} = {p_s}")
        if auc_p is not None:
            if auc_p < 0.65:
                print("  PLAYER OVERFIT — model memorized player profiles")
                player_overfit_sp.append(sp)
            else:
                print("  PLAYER OK — generalizes to unseen players")

        print(f"\n{'=' * 72}\nTASK 3 — Feature ablation ({sp})\n{'=' * 72}")
        strat_ab = _stratify_series_for_split(sub)
        tr_a, te_a = train_test_split(sub, test_size=0.2, random_state=44, stratify=strat_ab)
        baseline_ab: float | None = None
        if len(tr_a) >= 20 and len(te_a) >= 10:
            if len(np.unique(tr_a["y"])) >= 2 and len(np.unique(te_a["y"])) >= 2:
                baseline_ab = _fit_xgb_platt_auc(
                    tr_a[features_active].astype(float),
                    tr_a["y"],
                    te_a[features_active].astype(float),
                    te_a["y"],
                    spw,
                )
        print(f"  {'Feature':<28} | {'AUC Without':>12} | {'AUC Drop':>10} | Notes")
        print(f"  {'-' * 28}-+-{'-' * 12}-+-{'-' * 10}-+-{'-' * 24}")
        if baseline_ab is None:
            print("  (Ablation skipped: stratified split not viable for this sport)")
        else:
            for fname in features_active:
                cols = [c for c in features_active if c != fname]
                aw = _fit_xgb_platt_auc(
                    tr_a[cols].astype(float),
                    tr_a["y"],
                    te_a[cols].astype(float),
                    te_a["y"],
                    spw,
                )
                if aw is None:
                    aw_s = "n/a"
                    drop = 0.0
                    note = ""
                else:
                    aw_s = f"{aw:.4f}"
                    drop = float(baseline_ab) - float(aw)
                    note = "HIGH DEPENDENCY" if drop > 0.15 else ""
                    if drop > 0.15:
                        high_dep_features.add(fname)
                print(f"  {fname:<28} | {aw_s:>12} | {drop:>10.4f} | {note}")

    present_set = {str(x) for x in df["sport"].unique()}
    ps = {"NHL", "SOCCER"} & present_set
    temporal_both = ps <= set(temporal_overfit_sp) and len(ps) == 2
    player_both = ps <= set(player_overfit_sp) and len(ps) == 2
    if temporal_both or player_both or len(high_dep_features) >= 2:
        return "C"
    if temporal_overfit_sp or player_overfit_sp or high_dep_features:
        return "B"
    return "A"


def _fit_slice_isotonic_calibrators(
    tr: pd.DataFrame,
    y_train: pd.Series,
    features_active: list[str],
    calibrated: EdgeCalibratedModel,
    models_dir: Path,
    *,
    isotonic_min_n: int = DEFAULT_SLICE_ISOTONIC_MIN_N,
) -> tuple[list[str], list[str]]:
    """
    Fit per-(sport, pick_type, direction) isotonic regressors on Platt probabilities
    using ``ISO_CALIB_TRAIN_FRAC`` of **train** rows (never the Platt holdout ``te``).
    Sports in ``INACTIVE_SPORTS`` are skipped (no calibrator keys at inference).
    """
    fitted_keys: list[str] = []
    skipped: list[str] = []
    if len(tr) < 80:
        skipped.append("insufficient_train_rows_for_iso_split")
        joblib.dump(
            {"calibrators": {}, "min_n": isotonic_min_n, "fitted_keys": [], "skipped": skipped, "version": 1},
            models_dir / "edge_slice_calibrators.pkl",
            compress=3,
        )
        return fitted_keys, skipped

    idx_all = np.arange(len(tr))
    try:
        _, iso_idx = train_test_split(
            idx_all,
            test_size=ISO_CALIB_TRAIN_FRAC,
            random_state=43,
            stratify=y_train.astype(int).values,
        )
    except ValueError:
        _, iso_idx = train_test_split(idx_all, test_size=ISO_CALIB_TRAIN_FRAC, random_state=43)

    tr_iso = tr.iloc[iso_idx].copy()
    if len(tr_iso) < 30:
        skipped.append("iso_split_too_small")
        joblib.dump(
            {"calibrators": {}, "min_n": isotonic_min_n, "fitted_keys": [], "skipped": skipped, "version": 1},
            models_dir / "edge_slice_calibrators.pkl",
            compress=3,
        )
        return fitted_keys, skipped

    dirs = _direction_series(tr_iso).astype(str).str.strip().str.upper()
    pt = tr_iso.get("pick_type", pd.Series("", index=tr_iso.index)).astype(str).str.strip().str.lower()
    sp = tr_iso["sport"].astype(str).str.strip().str.upper()

    tmp = tr_iso.copy()
    tmp["_spu"] = sp.values
    tmp["_ptl"] = pt.values
    tmp["_dru"] = dirs.values

    calibrators: dict[tuple[str, str, str], IsotonicRegression] = {}
    for (sp0, pt0, dr0), sub in tmp.groupby(["_spu", "_ptl", "_dru"], sort=False):
        key = (str(sp0), str(pt0).lower(), str(dr0).upper())
        if str(key[0]).strip().upper() in INACTIVE_SPORTS:
            skipped.append(f"{key}:inactive")
            continue
        if not key[1]:
            skipped.append(f"{key}:empty_pick_type")
            continue
        n = len(sub)
        if n < isotonic_min_n:
            skipped.append(f"{key}:n={n}")
            continue
        Xi = sub[features_active].astype(float)
        pi = np.asarray(calibrated.predict_proba(Xi)[:, 1], dtype=float)
        yi = sub["y"].astype(int).values
        if len(np.unique(yi)) < 2:
            skipped.append(f"{key}:single_class")
            continue
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        try:
            iso.fit(pi, yi)
        except Exception:
            skipped.append(f"{key}:fit_failed")
            continue
        calibrators[key] = iso
        fitted_keys.append(f"{key[0]}/{key[1]}/{key[2]}")

    payload = {
        "calibrators": calibrators,
        "min_n": isotonic_min_n,
        "fitted_keys": fitted_keys,
        "skipped": skipped,
        "version": 1,
    }
    joblib.dump(payload, models_dir / "edge_slice_calibrators.pkl", compress=3)
    print(
        f"\n[Slice isotonic] fitted={len(fitted_keys)} slices "
        f"(train calib frac={ISO_CALIB_TRAIN_FRAC}, min_n={isotonic_min_n}); skipped={len(skipped)}"
    )
    return fitted_keys, skipped


def _train_unified_edge_model(
    root: Path,
    *,
    recursive_outputs: bool,
    dedupe: bool,
    include_synthetic: bool,
    temporal_split: bool,
    exclude_player_level_features: bool,
    input_csv: Path | None = None,
    sport_filter: str | None = None,
    dry_run: bool = False,
    temporal_date_column: str | None = None,
    isotonic_min_n: int = DEFAULT_SLICE_ISOTONIC_MIN_N,
) -> None:
    print(f"[PropORACLE-{SCRIPT_NAME}] Starting unified training...")
    models_dir = root / "models"

    print(
        "  [config] recursive_outputs=%s dedupe=%s temporal_split=%s exclude_player_hr=%s isotonic_min_n=%s"
        % (recursive_outputs, dedupe, temporal_split, exclude_player_level_features, isotonic_min_n)
    )
    if temporal_date_column:
        print(f"  [config] temporal_date_column={temporal_date_column!r}")
    if input_csv is not None:
        print(f"  [config] input_csv={input_csv} sport_filter={sport_filter!r} dry_run={dry_run}")
    n_combined_files_omitted = 0
    if input_csv is not None:
        csv_path = Path(input_csv).expanduser().resolve()
        raw = _load_retrain_csv_as_raw(csv_path, sport_filter, source_label=str(csv_path))
        if dedupe and not raw.empty:
            raw, removed = _dedupe_graded_rows(raw)
            if removed:
                print(f"\n  [dedupe] removed {removed} duplicate rows (same sport/player/date/prop/line/dir)")
    else:
        raw, n_combined_files_omitted = load_all_graded(
            root,
            recursive_outputs=recursive_outputs,
            dedupe=dedupe,
            include_synthetic=include_synthetic,
        )
    if raw.empty:
        if input_csv is not None:
            print("[ERROR] No rows left after loading --input-csv (check sport filter and result_binary).")
        else:
            print("[ERROR] No graded files with hit labels found.")
        return

    raw["sport"] = _normalize_sport_series(raw["sport"])
    raw = raw.loc[raw["_hit_y"].isin([0.0, 1.0])].copy()

    if dry_run:
        if input_csv is None:
            print("[ERROR] --dry-run requires --input-csv.")
            return
        n_after = len(raw)
        print("\n=== DRY RUN (--input-csv) ===")
        print(f"Rows after sport filter + null result_binary drop: {n_after:,}")
        n1, n0, dom = _hit_class_balance(raw["_hit_y"])
        tot = n1 + n0
        if tot:
            print(f"Class balance — HIT (1): {n1:,}  MISS (0): {n0:,}  hit%: {100.0 * n1 / tot:.2f}%  dominant-class%: {dom:.1f}")
        else:
            print("Class balance — no labeled rows.")
        print("\nSport counts (raw):")
        print(raw.groupby("sport").size().to_string())
        print(
            "\nFeature completeness (FEATURE_COLUMNS; after build_feature_vector + row filter; "
            "median imputation OFF for this diagnostic):"
        )
        df_dry = _prepare_features(raw, skip_median_fill=True)
        for col in FEATURE_COLUMNS:
            if col not in df_dry.columns:
                print(f"  {col:<28}  missing column")
                continue
            rate = float(pd.to_numeric(df_dry[col], errors="coerce").notna().mean())
            print(f"  {col:<28}  non_null={rate:.4f}")
        if len(df_dry) == 0:
            print("\n[WARN] Zero rows after _prepare_features (check edge/line/rank_score/blended_signal).")
        print("\n[DRY RUN] Done (no model written).")
        return

    models_dir.mkdir(parents=True, exist_ok=True)

    if temporal_split:
        dt_ser, col_used = _event_date_series_for_raw(raw, temporal_date_column=temporal_date_column)
        if not col_used:
            print(
                "[ERROR] --temporal-split requires parseable dates (game_date, graded_date, "
                "created_at, or slate_date/date/start_time) on enough graded rows."
            )
            return
        print(f"Temporal split date column used: {col_used!r}")
        raw = raw.copy()
        raw["_stress_event_dt"] = dt_ser

    sport_counts = raw.groupby("sport").size()
    print("\nRows per sport (raw, before feature prep):")
    print(sport_counts.to_string())

    leak_raw_cols = _raw_columns_leakage_scan(raw)
    if leak_raw_cols:
        print(
            f"\n[WARN] Raw graded columns look post-game (inspect for leakage): "
            f"{leak_raw_cols[:20]}{' ...' if len(leak_raw_cols) > 20 else ''}"
        )

    _print_nba1h_hit_derivation_debug(raw)

    df = _prepare_features(raw)
    df["y"] = df["_hit_y"].astype(int)

    print("\n--- Per-sport training quality (before filters) ---")
    print(
        f"{'Sport':<8} {'Rows':>7} {'Hits':>7} {'Misses':>7} "
        f"{'Hit%':>8} {'Dom%':>8} {'Status':<12}"
    )
    sports_to_drop: list[str] = []
    quality_skipped_rows: dict[str, int] = {}
    for sp in sorted(df["sport"].unique()):
        sub_m = df["sport"] == sp
        n = int(sub_m.sum())
        n1, n0, dom_pct = _hit_class_balance(df.loc[sub_m, "y"])
        hit_pct = (100.0 * n1 / n) if n else 0.0
        reason = _sport_skip_reason(n, dom_pct)
        status = "SKIP" if reason else "OK"
        print(f"{str(sp):<8} {n:7d} {n1:7d} {n0:7d} {hit_pct:7.1f}% {dom_pct:7.1f}% {status:<12}")
        if reason:
            print(f"  Skipping {sp}: {reason}")
            sports_to_drop.append(str(sp))
            quality_skipped_rows[str(sp)] = n

    if sports_to_drop:
        df = df.loc[~df["sport"].isin(sports_to_drop)].copy()

    if df.empty:
        print("[ERROR] No sports left after row-count / class-balance filters.")
        return

    if temporal_split:
        df = (
            df.dropna(subset=["_stress_event_dt"])
            .sort_values("_stress_event_dt", kind="mergesort")
            .reset_index(drop=True)
        )
        if len(df) < 100:
            print("[ERROR] Temporal split: too few rows with valid event dates after feature prep.")
            return

    y = df["y"].astype(int)
    pos = int((y == 1).sum())
    neg = int((y == 0).sum())
    spw = (neg / pos) if pos > 0 else 1.0

    print("\n--- Direction mix (direction_encoded: 1=OVER, 0=UNDER) ---")
    for sp in sorted(df["sport"].unique()):
        sub = df["sport"] == sp
        o = int(df.loc[sub, "direction_encoded"].eq(1.0).sum())
        u = int(df.loc[sub, "direction_encoded"].eq(0.0).sum())
        print(f"  {sp} OVER={o} UNDER={u}")

    if temporal_split:
        n = len(df)
        k = max(1, int(n * 0.8))
        if k < 50 or (n - k) < 50:
            print("[ERROR] Temporal split: insufficient train or test rows.")
            return
        tr, te = df.iloc[:k].copy(), df.iloc[k:].copy()
        if "_stress_event_dt" in tr.columns and "_stress_event_dt" in te.columns:
            tr_dt = pd.to_datetime(tr["_stress_event_dt"], errors="coerce")
            te_dt = pd.to_datetime(te["_stress_event_dt"], errors="coerce")
            if tr_dt.notna().any() and te_dt.notna().any():
                print(
                    "[train_edge_model] temporal holdout: "
                    f"train n={len(tr)} [{tr_dt.min()} .. {tr_dt.max()}] | "
                    f"test n={len(te)} [{te_dt.min()} .. {te_dt.max()}]"
                )
    else:
        strat = df["sport"].astype(str) + "_" + df["direction_encoded"].astype(int).astype(str)
        vc = strat.value_counts()
        if strat.nunique() < 2 or int(vc.min()) < 2:
            strat = df["sport"].astype(str)
        tr, te = train_test_split(df, test_size=0.2, random_state=42, stratify=strat)

    # ── NHL / Soccer: feature-target correlation on TRAIN only (leakage suspects) ──
    print("\n--- Feature vs hit correlation (train only; |r|>0.5 = leakage suspect) ---")
    leak_by_corr: set[str] = set()
    for sp_label in ("NHL", "SOCCER"):
        corrs = _correlations_with_target(tr, sp_label, list(FEATURE_COLUMNS))
        if not corrs:
            print(f"  {sp_label}: insufficient train rows for correlation scan")
            continue
        suspects = [(f, r) for f, r in corrs.items() if abs(r) > 0.5]
        suspects.sort(key=lambda x: -abs(x[1]))
        print(f"  {sp_label} (n_train={int((tr['sport'].astype(str)==sp_label).sum())}):")
        for f, r in sorted(corrs.items(), key=lambda x: -abs(x[1]))[:15]:
            tag = " *** SUSPECT" if abs(r) > 0.5 else ""
            print(f"    {f}: r={r:+.4f}{tag}")
        for f, _r in suspects:
            leak_by_corr.add(f)

    leak_by_name = {f for f in FEATURE_COLUMNS if _feature_name_leakage_matches(f)}
    if leak_by_name:
        print(f"\n[WARN] Feature names matching leakage substrings removed: {sorted(leak_by_name)}")

    always_excl = sorted(ALWAYS_EXCLUDE_FROM_EDGE_TRAINING & set(FEATURE_COLUMNS))
    if always_excl:
        print(f"\n[Leakage] Always excluded from tree training (label-adjacent): {always_excl}")

    leak_confirmed = bool(leak_by_corr or leak_by_name)
    features_active = [
        f
        for f in FEATURE_COLUMNS
        if f not in leak_by_corr and f not in leak_by_name and f not in ALWAYS_EXCLUDE_FROM_EDGE_TRAINING
    ]
    if len(features_active) < 8:
        print(
            "[WARN] Too few features after leakage removal; restoring FEATURE_COLUMNS "
            "minus always-excluded label-adjacent columns only."
        )
        features_active = [f for f in FEATURE_COLUMNS if f not in ALWAYS_EXCLUDE_FROM_EDGE_TRAINING]
        leak_confirmed = False
    elif leak_confirmed:
        print(
            f"\n[Leakage] Confirmed suspects removed from training: "
            f"{sorted(leak_by_corr | leak_by_name)}"
        )
    else:
        print("\n[Leakage] No |r|>0.5 feature-target correlation on NHL/Soccer train; no name-based removals.")

    if exclude_player_level_features:
        before_ex = list(features_active)
        features_active = [f for f in features_active if f != "player_hr_historical"]
        if len(features_active) < 8:
            print(
                "[WARN] Excluding player_hr_historical would leave too few features; "
                "keeping player_hr_historical."
            )
            features_active = before_ex
        else:
            print("\n[Training] Excluded player-level feature: player_hr_historical")

    if WNBA_FEATURE_COLUMNS and (tr["sport"].astype(str).str.upper() == "WNBA").any():
        features_active, wnba_dropped = drop_wnba_features_below_fill_threshold(features_active, tr)
        if wnba_dropped:
            print(
                f"\n[WNBA] Dropped low-fill features (<50% non-null on WNBA train rows): "
                f"{wnba_dropped}"
            )

    if (tr["sport"].astype(str).str.upper() == "NBA").any():
        features_active, nba_dropped = drop_nba_features_below_fill_threshold(features_active, tr)
        if nba_dropped:
            print(
                f"\n[NBA] Dropped low-fill features (<60% non-null on NBA train rows): "
                f"{nba_dropped}"
            )

    X_train = tr[features_active].astype(float)
    X_test = te[features_active].astype(float)
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

    p_hold = model.predict_proba(X_test)[:, 1].reshape(-1, 1)
    platt_lr = LogisticRegression(C=1e12, max_iter=2000, random_state=42, solver="lbfgs")
    platt_lr.fit(p_hold, y_test)
    calibrated = EdgeCalibratedModel(model, platt_lr)

    iso_fitted, iso_skipped = _fit_slice_isotonic_calibrators(
        tr, y_train, features_active, calibrated, models_dir, isotonic_min_n=isotonic_min_n
    )

    prob_test = calibrated.predict_proba(X_test)[:, 1]
    auc_overall = float(roc_auc_score(y_test, prob_test))
    print(f"\nROC-AUC (holdout, calibrated): {auc_overall:.4f}")

    print("\nROC-AUC per sport (holdout):")
    meta_auc: dict[str, float | None] = {}
    sport_status: dict[str, str] = {}
    for sp in sorted(df["sport"].unique()):
        m = te["sport"].astype(str).values == str(sp)
        n_te = int(np.sum(m))
        if n_te < MIN_HOLDOUT_ROWS_PER_SPORT:
            print(f"  {sp}: insufficient holdout (test n={n_te} < {MIN_HOLDOUT_ROWS_PER_SPORT}) — excluded from per-sport ROC")
            meta_auc[str(sp)] = None
            sport_status[str(sp)] = "insufficient holdout"
            continue
        if n_te < 5:
            print(f"  {sp}: n/a (too few test rows)")
            meta_auc[str(sp)] = None
            sport_status[str(sp)] = "too few test"
            continue
        y_sub = y_test.values[m]
        if len(np.unique(y_sub)) < 2:
            print(f"  {sp}: n/a (single class in test)")
            meta_auc[str(sp)] = None
            sport_status[str(sp)] = "single-class test"
            continue
        try:
            a = float(roc_auc_score(y_sub, prob_test[m]))
            meta_auc[str(sp)] = a
            sport_status[str(sp)] = "ok"
            note = ""
            if a > 0.85:
                note = " (investigate: >0.85)"
            elif a > 0.80:
                note = " (watch: >0.80)"
            print(f"  {sp}: {a:.4f}{note}")
        except Exception:
            print(f"  {sp}: n/a")
            meta_auc[str(sp)] = None
            sport_status[str(sp)] = "auc error"

    for sp in ("NHL", "SOCCER"):
        a = meta_auc.get(sp)
        if a is not None and float(a) > 0.85 and not leak_confirmed:
            print(
                f"\n[Findings] {sp} holdout ROC-AUC={a:.4f} with no feature |r|>0.5 vs hit on train — "
                "not treated as confirmed column leakage. High AUC may reflect strong separable signals "
                "(e.g. edge × sport slice) or slice-specific structure; consider time-based CV or ablation."
            )

    feat_imp = dict(zip(features_active, model.feature_importances_.tolist(), strict=True))
    top10 = sorted(feat_imp.items(), key=lambda x: -x[1])[:10]
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

    joblib.dump(calibrated, models_dir / "edge_model_unified.pkl", compress=3)
    (models_dir / "edge_model_features.json").write_text(
        json.dumps(features_active, indent=2), encoding="utf-8"
    )
    rows_per_sport = {str(k): int(v) for k, v in df.groupby("sport").size().items()}
    meta = {
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "trained_at_utc": datetime.now(timezone.utc).isoformat(),
        "training_rows_total": int(len(df)),
        "rows_per_sport": rows_per_sport,
        "roc_auc_overall": auc_overall,
        "roc_auc_per_sport": {k: v for k, v in meta_auc.items() if v is not None},
        "roc_auc_per_sport_with_nulls": {k: (float(v) if v is not None else None) for k, v in meta_auc.items()},
        "per_sport_holdout_status": sport_status,
        "scale_pos_weight": spw,
        "feature_columns": features_active,
        "always_excluded_from_tree_training": sorted(ALWAYS_EXCLUDE_FROM_EDGE_TRAINING & set(FEATURE_COLUMNS)),
        "features_removed_leakage": sorted(leak_by_corr | leak_by_name) if leak_confirmed else [],
        "sports_skipped_quality": {k: quality_skipped_rows[k] for k in sorted(quality_skipped_rows)},
        "leakage_confirmed": bool(leak_confirmed),
        "recursive_outputs_used": recursive_outputs,
        "dedupe_used": dedupe,
        "temporal_split_used": temporal_split,
        "temporal_date_column": temporal_date_column if temporal_split else None,
        "exclude_player_level_features_used": exclude_player_level_features,
        "combined_graded_file_paths_omitted": int(n_combined_files_omitted),
        "edge_slice_calibrators_file": "edge_slice_calibrators.pkl",
        "edge_slice_isotonic_min_n": isotonic_min_n,
        "edge_slice_isotonic_train_frac": ISO_CALIB_TRAIN_FRAC,
        "edge_slice_isotonic_inactive_sports": sorted(INACTIVE_SPORTS),
        "edge_slice_isotonic_fitted_count": len(iso_fitted),
        "edge_slice_isotonic_fitted_keys": iso_fitted[:250],
        "edge_slice_isotonic_skipped": iso_skipped[:400],
    }
    (models_dir / "edge_model_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nSaved: edge_model_unified.pkl, edge_model_features.json, edge_model_metadata.json -> {models_dir}")

    print("\n=== Summary: Sport | Rows | Test Size | ROC-AUC | Status ===")
    print(f"{'Sport':<8} | {'Rows':>6} | {'Test':>6} | {'ROC-AUC':>10} | {'Status':<22}")
    print("-" * 55)
    all_sports_keys = sorted(set(rows_per_sport.keys()) | set(quality_skipped_rows.keys()))
    for sp in all_sports_keys:
        if sp in quality_skipped_rows:
            n_tot = quality_skipped_rows[sp]
            print(f"{sp:<8} | {n_tot:6d} | {0:6d} | {'—':>10} | {'skipped (quality)':<22}")
            continue
        n_tot = rows_per_sport[sp]
        n_te = int((te["sport"].astype(str) == sp).sum())
        auc_s = meta_auc.get(sp)
        st = sport_status.get(sp, "—")
        if auc_s is None:
            auc_str = "—"
        else:
            auc_str = f"{auc_s:.4f}"
        print(f"{sp:<8} | {n_tot:6d} | {n_te:6d} | {auc_str:>10} | {st:<22}")
    print(f"{'OVERALL':<8} | {len(df):6d} | {len(te):6d} | {auc_overall:10.4f} | {'holdout':<22}")


def main() -> None:
    default_root = _repo_root()
    ap = argparse.ArgumentParser(
        description="Train edge_model_unified on all graded workbooks (including outputs/YYYY-MM-DD/)."
    )
    ap.add_argument("--repo-root", type=Path, default=default_root)
    ap.add_argument(
        "--no-recursive-outputs",
        action="store_true",
        help="Only use flat outputs/ and sport folders (skip outputs/**/dated nested graded files).",
    )
    ap.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Keep duplicate rows if the same prop appears in multiple dated exports.",
    )
    ap.add_argument(
        "--include-synthetic",
        action="store_true",
        help="Include paths under .../synthetic/ (off by default).",
    )
    ap.add_argument(
        "--stress-test-nhl-soccer",
        action="store_true",
        help="Run temporal / player-holdout / ablation stress tests for NHL+SOCCER only (no .pkl write).",
    )
    ap.add_argument(
        "--temporal-split",
        action="store_true",
        help=(
            "Train on oldest 80%% of rows by event date, test on newest 20%% (full unified model). "
            "Works with graded discovery and with --input-csv when dates are parseable "
            "(game_date from retrain CSVs, or columns coalesced in _adapt_retrain_csv_for_feature_pipeline). "
            "Override detection with --temporal-date-column."
        ),
    )
    ap.add_argument(
        "--temporal-date-column",
        type=str,
        default=None,
        help=(
            "With --temporal-split only: use this raw column (case-insensitive match) as the event timestamp "
            "before automatic column selection. Must parse for >= max(5, 2%% of rows) or falls back to auto-detect."
        ),
    )
    ap.add_argument(
        "--exclude-player-level-features",
        action="store_true",
        help="Drop player_hr_historical from training (full unified model).",
    )
    ap.add_argument(
        "--input-csv",
        type=Path,
        default=None,
        help="Optional training table (e.g. data/retrain_dataset.csv with result_binary). Relative paths resolve against --repo-root.",
    )
    ap.add_argument(
        "--sport",
        type=str,
        default=None,
        help="When using --input-csv, keep only this sport (e.g. NBA). Omit for all sports in the file.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="With --input-csv only: print row counts, class balance, and feature completeness; do not train or write models.",
    )
    ap.add_argument(
        "--isotonic-min-n",
        type=int,
        default=DEFAULT_SLICE_ISOTONIC_MIN_N,
        dest="isotonic_min_n",
        help="Minimum slice sample size to fit isotonic calibrator (default 200).",
    )
    args = ap.parse_args()
    root = Path(args.repo_root).resolve()

    if getattr(args, "temporal_date_column", None) and not args.temporal_split:
        print("[WARN] --temporal-date-column is ignored without --temporal-split.")

    input_csv_resolved: Path | None = None
    if args.input_csv is not None:
        p = Path(args.input_csv)
        input_csv_resolved = (root / p).resolve() if not p.is_absolute() else p.expanduser().resolve()

    if args.dry_run and input_csv_resolved is None:
        print("[ERROR] --dry-run requires --input-csv.")
        raise SystemExit(1)
    if args.sport and input_csv_resolved is None:
        print("[WARN] --sport is ignored without --input-csv (graded file discovery unchanged).")

    if args.stress_test_nhl_soccer:
        if input_csv_resolved is not None:
            print("[ERROR] Cannot combine --stress-test-nhl-soccer with --input-csv.")
            raise SystemExit(1)
        rec = _run_stress_test_nhl_soccer(root, args)
        print("\n" + "=" * 72)
        if rec == "A":
            print(
                "RECOMMENDATION A) NHL/Soccer AUC is legitimate — strong sport-specific signals confirmed"
            )
        elif rec == "B":
            print(
                "RECOMMENDATION B) NHL/Soccer AUC inflated — recommend capping ml_prob weight to 0.15 for "
                "these sports in step7b_edge_score.py blended_score formula"
            )
        else:
            print(
                "RECOMMENDATION C) NHL/Soccer AUC severely overfit — recommend retraining with temporal "
                "split only and excluding player-level features"
            )
        print("=" * 72)
        if rec == "B":
            _apply_step7b_nhl_soccer_ml_cap()
        elif rec == "C":
            print("\n[Fix C] Retraining unified edge model with temporal split + excluding player_hr_historical…")
            _train_unified_edge_model(
                root,
                recursive_outputs=not args.no_recursive_outputs,
                dedupe=not args.no_dedupe,
                include_synthetic=args.include_synthetic,
                temporal_split=True,
                exclude_player_level_features=True,
                input_csv=None,
                sport_filter=None,
                dry_run=False,
                isotonic_min_n=int(args.isotonic_min_n),
            )
        return

    _train_unified_edge_model(
        root,
        recursive_outputs=not args.no_recursive_outputs,
        dedupe=not args.no_dedupe,
        include_synthetic=args.include_synthetic,
        temporal_split=args.temporal_split,
        exclude_player_level_features=args.exclude_player_level_features,
        input_csv=input_csv_resolved,
        sport_filter=args.sport,
        dry_run=args.dry_run,
        temporal_date_column=str(args.temporal_date_column).strip() if args.temporal_date_column else None,
        isotonic_min_n=int(args.isotonic_min_n),
    )


if __name__ == "__main__":
    main()
