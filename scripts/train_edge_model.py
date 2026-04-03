#!/usr/bin/env python3
"""Train unified XGBoost edge classifier + Platt calibration on graded history.

Uses edge_feature_engineering.build_feature_vector(), which applies play-side edge
(negate raw projection-line edge for explicit UNDER rows) so the `edge` feature matches
step7b / prop ML conventions. Retrain after that convention change.
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
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

from edge_feature_engineering import (
    FEATURE_COLUMNS,
    build_feature_vector,
    fill_minutes_cv_median_by_sport,
)
from edge_ml_bundle import EdgeCalibratedModel

SCRIPT_NAME = "train_edge_model"

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
    if "graded_nba1h" in n or "graded_nba1q" in n or "graded_nba" in n:
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
    ap = argparse.ArgumentParser(
        description="Train edge_model_unified on all graded workbooks (including outputs/YYYY-MM-DD/)."
    )
    ap.add_argument("--repo-root", type=Path, default=root)
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
    args = ap.parse_args()
    root = Path(args.repo_root).resolve()
    models_dir = root / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    print(
        "  [config] recursive_outputs=%s dedupe=%s"
        % (not args.no_recursive_outputs, not args.no_dedupe)
    )
    raw, n_combined_files_omitted = load_all_graded(
        root,
        recursive_outputs=not args.no_recursive_outputs,
        dedupe=not args.no_dedupe,
        include_synthetic=args.include_synthetic,
    )
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

    p_hold = model.predict_proba(X_test)[:, 1].reshape(-1, 1)
    platt_lr = LogisticRegression(C=1e12, max_iter=2000, random_state=42, solver="lbfgs")
    platt_lr.fit(p_hold, y_test)
    calibrated = EdgeCalibratedModel(model, platt_lr)

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

    joblib.dump(calibrated, models_dir / "edge_model_unified.pkl", compress=3)
    (models_dir / "edge_model_features.json").write_text(
        json.dumps(FEATURE_COLUMNS, indent=2), encoding="utf-8"
    )
    meta = {
        "trained_at_utc": datetime.now(timezone.utc).isoformat(),
        "training_rows_total": int(len(df)),
        "rows_per_sport": {str(k): int(v) for k, v in df.groupby("sport").size().items()},
        "roc_auc_overall": auc_overall,
        "roc_auc_per_sport": meta_auc,
        "scale_pos_weight": spw,
        "feature_columns": FEATURE_COLUMNS,
        "recursive_outputs_used": not args.no_recursive_outputs,
        "dedupe_used": not args.no_dedupe,
        "combined_graded_file_paths_omitted": int(n_combined_files_omitted),
    }
    (models_dir / "edge_model_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nSaved: edge_model_unified.pkl, edge_model_features.json, edge_model_metadata.json -> {models_dir}")


if __name__ == "__main__":
    main()
