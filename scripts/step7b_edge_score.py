#!/usr/bin/env python3
"""Apply unified edge model scores to step7 ranked workbook (daily, post-step7).

Writes ml_prob, edge_score, blended_score into the step7 xlsx (primary sheet). Run this
before step8 / slate grading so Box Raw exports can include those columns (slate_grader
and nhl_soccer_grader pass them through when present).
"""

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

import edge_ml_bundle  # noqa: F401 — EdgeCalibratedModel pickle root
from edge_feature_engineering import FEATURE_COLUMNS, build_feature_vector

SCRIPT_NAME = "step7b_edge_score"

SPORT_ALIASES = {"NBA", "CBB", "NHL", "SOCCER", "MLB", "SOC", "NBA1H", "NBA1Q", "WCBB", "TENNIS"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _norm_sport(s: str) -> str:
    x = str(s or "").strip().upper()
    if x == "SOC":
        return "SOCCER"
    if x in ("NBA1H", "NBA1Q"):
        return "NBA"
    if x == "WCBB":
        return "CBB"
    return x


def resolve_step7_path(root: Path, sport: str) -> Path | None:
    sp = _norm_sport(sport)
    raw_sp = str(sport or "").strip().upper()
    sl = sp.lower()
    candidates: list[Path] = [
        root / sp / "outputs" / f"step7_{sl}_ranked.xlsx",
        root / "NBA" / "data" / "outputs" / "step7_ranked_props.xlsx",
        root / "NBA" / "outputs" / "step7_nba_ranked.xlsx",
        root / "NBA" / "data" / "outputs" / "step7_nba1q_ranked_props.xlsx",
        root / "NBA" / "data" / "outputs" / "step7_nba1h_ranked_props.xlsx",
        root / "NBA" / "step7_nba1q_ranked_props.xlsx",
        root / "NBA" / "step7_nba1h_ranked_props.xlsx",
        root / "NHL" / f"step7_nhl_ranked.xlsx",
        root / "NHL" / "outputs" / f"step7_{sl}_ranked.xlsx",
        root / "Soccer" / "outputs" / "step7_soccer_ranked.xlsx",
        root / "Soccer" / "step7_soccer_ranked.xlsx",
        root / "Tennis" / "outputs" / "step7_tennis_ranked.xlsx",
        root / "MLB" / "outputs" / "step7_mlb_ranked.xlsx",
        root / "MLB" / "scripts" / "step7_mlb_ranked.xlsx",
        root / "MLB" / "step7_mlb_ranked.xlsx",
        root / "CBB" / "outputs" / f"step7_{sl}_ranked.xlsx",
        root / "CBB" / "outputs" / "step6_ranked_cbb.xlsx",
        root / "CBB" / "step6_ranked_cbb.xlsx",
    ]
    if raw_sp == "NBA1Q":
        candidates = [
            root / "NBA" / "data" / "outputs" / "step7_nba1q_ranked_props.xlsx",
            root / "NBA" / "step7_nba1q_ranked_props.xlsx",
            *candidates,
        ]
    elif raw_sp == "NBA1H":
        candidates = [
            root / "NBA" / "data" / "outputs" / "step7_nba1h_ranked_props.xlsx",
            root / "NBA" / "step7_nba1h_ranked_props.xlsx",
            *candidates,
        ]
    elif raw_sp == "WCBB":
        candidates = [
            root / "CBB" / "step6_ranked_wcbb.xlsx",
            root / "CBB" / "outputs" / "step6_ranked_wcbb.xlsx",
            *candidates,
        ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _first_sheet(path: Path) -> str:
    xl = pd.ExcelFile(path)
    return xl.sheet_names[0]


def main() -> None:
    print(f"[PropORACLE-{SCRIPT_NAME}] Starting...")
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", required=True, help="NBA, CBB, NHL, Soccer, MLB")
    ap.add_argument(
        "--step7-xlsx",
        default="",
        help="Optional full path to step7 workbook (overrides default location; e.g. NBA1H/1Q file).",
    )
    ap.add_argument("--repo-root", type=Path, default=None)
    args = ap.parse_args()
    root = Path(args.repo_root).resolve() if args.repo_root else _repo_root()
    sp = _norm_sport(args.sport)
    if sp not in SPORT_ALIASES and sp != "SOCCER":
        print(f"[WARN] Unknown sport {args.sport!r}, proceeding with key {sp!r}")

    model_path = root / "models" / "edge_model_unified.pkl"
    feat_path = root / "models" / "edge_model_features.json"
    if not model_path.is_file() or not feat_path.is_file():
        print(f"[WARN] Edge model not found ({model_path}) — skipping scoring.")
        return

    xlsx: Path | None = None
    if str(args.step7_xlsx or "").strip():
        p = Path(str(args.step7_xlsx).strip())
        if not p.is_absolute():
            p = (root / p).resolve()
        xlsx = p if p.is_file() else None
        if xlsx is None:
            print(f"[WARN] --step7-xlsx not found: {p}")
    if xlsx is None:
        xlsx = resolve_step7_path(root, sp)
    if xlsx is None:
        print(f"[WARN] No step7 workbook found for sport={sp} — skip.")
        return

    sheet = _first_sheet(xlsx)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        df = pd.read_excel(xlsx, sheet_name=sheet, engine="openpyxl")
    if df.empty:
        print(f"[WARN] Empty sheet {sheet!r} in {xlsx} — skip.")
        return

    df2 = build_feature_vector(df, sp)
    if len(df2) == 0:
        print(f"[WARN] 0 rows after feature build for {sp} — skip.")
        return

    feats = json.loads(feat_path.read_text(encoding="utf-8"))
    missing = [c for c in feats if c not in df2.columns]
    if missing:
        print(f"[WARN] Missing {len(missing)} feature cols for {sp} — filling with 0.0: {missing[:8]}")
        for col in missing:
            df2[col] = 0.0

    try:
        model = joblib.load(model_path)
    except Exception as e:
        print(f"[WARN] Could not load model: {e} — skip.")
        return

    X = df2[feats].astype(float)
    ml_prob = model.predict_proba(X)[:, 1]
    edge_col = pd.to_numeric(df2.get("edge", pd.Series(0.0, index=df2.index)), errors="coerce").fillna(0.0)
    abs_edge_col = pd.to_numeric(df2.get("abs_edge", pd.Series(np.nan, index=df2.index)), errors="coerce")
    edge_mag = abs_edge_col.where(abs_edge_col.notna(), edge_col.abs()).fillna(0.0)
    implied_prob = 1.0 / (1.0 + np.exp(-edge_mag.clip(-20, 20)))
    comp = pd.to_numeric(
        df2.get("composite_hit_rate", df2.get("line_hit_rate", pd.Series(0.5, index=df2.index))),
        errors="coerce",
    ).fillna(0.5)
    # Playoff uplift: emphasize short-window same-opponent trend where available.
    # step7 populates l5_vs_same_opp_hit_rate direction-aware (high = supports pick side).
    if sp == "NBA" and "l5_vs_same_opp_hit_rate" in df2.columns:
        opp_l5 = pd.to_numeric(df2["l5_vs_same_opp_hit_rate"], errors="coerce")
        opp_l5 = pd.Series(np.where(opp_l5 > 1.0, opp_l5 / 100.0, opp_l5), index=df2.index)
        playoff = (
            df2.get("is_playoff_game", pd.Series(False, index=df2.index))
            .astype(str).str.strip().str.lower().isin(["1", "true", "t", "yes", "y"])
        )
        use_opp_l5 = playoff & opp_l5.notna()
        if use_opp_l5.any():
            comp = pd.Series(np.where(use_opp_l5, (0.55 * comp + 0.45 * opp_l5), comp), index=df2.index)
    edge_score = pd.Series(ml_prob, index=df2.index) - implied_prob
    if sp in ("NHL", "SOCCER"):
        blended = 0.15 * pd.Series(ml_prob, index=df2.index) + 0.85 * comp
    else:
        blended = 0.3 * pd.Series(ml_prob, index=df2.index) + 0.7 * comp

    df2["ml_prob"] = ml_prob
    df2["edge_score"] = edge_score.values
    df2["blended_score"] = blended.values
    # Do not re-sort NHL — it uses explicit rank ordering in its step7 output
    if sp.upper() != "NHL":
        df2 = df2.sort_values("blended_score", ascending=False, na_position="last", kind="mergesort")

    xl_obj = pd.ExcelFile(xlsx)
    all_sheets: dict[str, pd.DataFrame] = {}
    for sn in xl_obj.sheet_names:
        if sn == sheet:
            all_sheets[sn] = df2
        else:
            all_sheets[sn] = pd.read_excel(xlsx, sheet_name=sn, engine="openpyxl")
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        for sn, frame in all_sheets.items():
            frame.to_excel(w, sheet_name=sn, index=False)

    print(f"  Scored {len(df2)} rows for {sp} -> {xlsx} (sheet={sheet!r})")
    top = df2.head(5)
    pc = next((c for c in ("player_name", "player", "pp_player") if c in top.columns), None)
    prop_c = next((c for c in ("prop_norm", "prop_type", "stat_norm") if c in top.columns), None)
    for rank, (_, row) in enumerate(top.iterrows(), start=1):
        label = ""
        if pc:
            label += f" {row.get(pc, '')}"
        if prop_c:
            label += f" | {row.get(prop_c, '')}"
        print(f"    #{rank} blended={float(row['blended_score']):.4f}{label}")


if __name__ == "__main__":
    main()
