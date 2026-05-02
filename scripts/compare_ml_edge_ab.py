#!/usr/bin/env python3
"""
A/B: raw edge vs play-side edge for prop ML on a frozen slate.

Loads a step7-style workbook (or CSV), builds the same feature matrix as inference,
runs predict_proba twice — only the `edge` column differs — and prints summary stats.

Usage:
  python scripts/compare_ml_edge_ab.py --sport nba --slate NBA/step7_ranked_props.xlsx
  python scripts/compare_ml_edge_ab.py --sport nhl --slate NHL/outputs/step7_nhl.xlsx --sheet "All Props"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ml_play_side_edge import play_side_edge  # noqa: E402


def _read_table(path: Path, sheet: str) -> pd.DataFrame:
    if path.suffix.lower() in (".xlsx", ".xls"):
        xf = pd.ExcelFile(path, engine="openpyxl")
        sn = sheet if sheet else next((s for s in xf.sheet_names if "all" in s.lower()), xf.sheet_names[0])
        return pd.read_excel(path, sheet_name=sn, engine="openpyxl")
    return pd.read_csv(path, low_memory=False)


def _direction_num_from_df(df: pd.DataFrame) -> pd.Series:
    for c in ("bet_direction", "final_bet_direction", "direction", "recommended_side"):
        if c in df.columns:
            s = df[c].astype(str).str.strip().str.upper()
            return pd.Series(np.where(s.eq("OVER"), 1, 0), index=df.index)
    return pd.Series(1, index=df.index)


def _proba_pos(model, X: pd.DataFrame) -> np.ndarray:
    p = model.predict_proba(X.astype(float))[:, 1]
    return np.asarray(p, dtype=float)


def run_nba(df: pd.DataFrame, model, feats: list[str]) -> tuple[np.ndarray, np.ndarray]:
    nba_scripts = ROOT / "Sports" / "NBA" / "scripts"
    if str(nba_scripts) not in sys.path:
        sys.path.insert(0, str(nba_scripts))
    import step7_rank_props as s7  # noqa: WPS433

    X = s7._build_nba_ml_X(df, feats)
    raw = pd.to_numeric(df.reindex(X.index)["edge"], errors="coerce")
    X_old = X.copy()
    if "edge" in X_old.columns:
        X_old["edge"] = raw.fillna(X_old["edge"]).to_numpy()
    p_old = _proba_pos(model, X_old)
    p_new = _proba_pos(model, X)
    return p_old, p_new


def run_nhl(df: pd.DataFrame, model, feats: list[str]) -> tuple[np.ndarray, np.ndarray]:
    nhl_scripts = ROOT / "Sports" / "NHL" / "scripts"
    if str(nhl_scripts) not in sys.path:
        sys.path.insert(0, str(nhl_scripts))
    import step7_rank_props_nhl as s7  # noqa: WPS433

    dfp = df.copy()
    X = s7._build_nhl_ml_X(dfp, feats)
    raw = pd.to_numeric(dfp.reindex(X.index)["edge"], errors="coerce")
    X_old = X.copy()
    if "edge" in X_old.columns:
        X_old["edge"] = raw.fillna(X_old["edge"]).to_numpy()
    p_old = _proba_pos(model, X_old)
    p_new = _proba_pos(model, X)
    return p_old, p_new


def run_cbb(df: pd.DataFrame, model, feats: list[str]) -> tuple[np.ndarray, np.ndarray]:
    cbb_p = ROOT / "Sports" / "CBB" / "scripts" / "pipeline"
    if str(cbb_p) not in sys.path:
        sys.path.insert(0, str(cbb_p))
    import step6_rank_props_cbb as s6  # noqa: WPS433

    out = df.copy()
    pick = out.get("pick_type", pd.Series("Standard", index=out.index)).astype(str).str.lower()
    tier_num = pd.Series(np.where(pick.str.contains("gob"), 2, np.where(pick.str.contains("dem"), 0, 1)), index=out.index)
    _bd = out.get("bet_direction", pd.Series("OVER", index=out.index)).astype(str).str.upper().str.strip()
    dir_num = pd.Series(np.where(_bd.eq("OVER"), 1, 0), index=out.index)
    edge_raw = pd.to_numeric(out.get("edge", pd.Series(np.nan, index=out.index)), errors="coerce")
    edge_play = play_side_edge(edge_raw, dir_num)
    prop_norm = out.get("prop_norm", out.get("prop_type", pd.Series("unknown", index=out.index))).astype(str).str.lower().str.strip()
    prop_dummies = pd.get_dummies(prop_norm, prefix="prop", dtype=float)
    hr = pd.to_numeric(out.get("line_hit_rate", pd.Series(np.nan, index=out.index)), errors="coerce").fillna(0.5)
    if hr.notna().any() and hr.dropna().median() > 1.0:
        hr = hr / 100.0
    n_teams = float(s6._infer_cbb_n_teams(out))
    def_tier = pd.to_numeric(s6._ml_defense_tier_series(out, n_teams), errors="coerce").fillna(1.0)
    intel = pd.to_numeric(out.get("intel_shr_z", pd.Series(0.0, index=out.index)), errors="coerce").fillna(0.0)
    X_new = pd.concat(
        [
            pd.DataFrame(
                {
                    "edge": edge_play,
                    "hit_rate_l10": hr,
                    "defense_tier": def_tier,
                    "tier": pd.to_numeric(tier_num, errors="coerce").fillna(1.0),
                    "intel_shr_z": intel,
                    "direction": pd.to_numeric(dir_num, errors="coerce").fillna(0.0),
                },
                index=out.index,
            ),
            prop_dummies,
        ],
        axis=1,
    ).reindex(columns=feats, fill_value=0.0)
    X_old = X_new.copy()
    X_old["edge"] = edge_raw.fillna(0.0).to_numpy()
    X_old = X_old.reindex(columns=feats, fill_value=0.0)
    p_old = _proba_pos(model, X_old)
    p_new = _proba_pos(model, X_new)
    return p_old, p_new


def run_soccer(df: pd.DataFrame, model, feats: list[str], n_teams: int) -> tuple[np.ndarray, np.ndarray]:
    soc_scripts = ROOT / "Sports" / "Soccer" / "scripts"
    if str(soc_scripts) not in sys.path:
        sys.path.insert(0, str(soc_scripts))
    import step7_rank_props_soccer as s7  # noqa: WPS433

    out = df.copy()
    pick = out.get("pick_type", pd.Series("Standard", index=out.index)).astype(str).str.lower()
    tier_num = pd.Series(np.where(pick.str.contains("gob"), 2, np.where(pick.str.contains("dem"), 0, 1)), index=out.index)
    _bd = out.get("bet_direction", pd.Series("OVER", index=out.index)).astype(str).str.upper().str.strip()
    dir_num = pd.Series(np.where(_bd.eq("OVER"), 1, 0), index=out.index)
    edge_raw = pd.to_numeric(out.get("edge", pd.Series(np.nan, index=out.index)), errors="coerce")
    prop_norm = out.get("prop_norm", out.get("prop_type", pd.Series("unknown", index=out.index))).astype(str).str.lower().str.strip()
    prop_dummies = pd.get_dummies(prop_norm, prefix="prop", dtype=float)
    hr = pd.to_numeric(out.get("line_hit_rate", pd.Series(np.nan, index=out.index)), errors="coerce").fillna(0.5)
    if hr.notna().any() and hr.dropna().median() > 1.0:
        hr = hr / 100.0
    def_tier = s7._ml_defense_tier_series(out, n_teams)
    intel = pd.to_numeric(out.get("intel_shr_z", pd.Series(0.0, index=out.index)), errors="coerce").fillna(0.0)
    edge_play = play_side_edge(edge_raw, dir_num)
    X_new = pd.concat(
        [
            pd.DataFrame(
                {
                    "edge": edge_play,
                    "hit_rate_l10": hr,
                    "defense_tier": pd.to_numeric(def_tier, errors="coerce").fillna(1.0),
                    "tier": pd.to_numeric(tier_num, errors="coerce").fillna(1.0),
                    "intel_shr_z": intel,
                    "direction": pd.to_numeric(dir_num, errors="coerce").fillna(1.0),
                },
                index=out.index,
            ),
            prop_dummies,
        ],
        axis=1,
    ).reindex(columns=feats, fill_value=0.0)
    X_old = X_new.copy()
    X_old["edge"] = edge_raw.reindex(out.index).fillna(0.0).to_numpy()
    X_old = X_old.reindex(columns=feats, fill_value=0.0)
    p_old = _proba_pos(model, X_old)
    p_new = _proba_pos(model, X_new)
    return p_old, p_new


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare ML probs: raw edge vs play-side edge on a frozen slate.")
    ap.add_argument("--sport", required=True, choices=("nba", "nhl", "soccer", "cbb"))
    ap.add_argument("--slate", type=Path, required=True, help="step7 xlsx or csv")
    ap.add_argument("--sheet", default="", help="Excel sheet (default: first 'ALL' or first sheet)")
    ap.add_argument("--model", type=Path, default=None, help="Override .pkl path")
    ap.add_argument("--features", type=Path, default=None, help="Override features json")
    ap.add_argument("--n-teams", type=int, default=15, help="Soccer defense tier only")
    ap.add_argument("--out-csv", type=Path, default=None, help="Optional row-level diff export")
    args = ap.parse_args()

    sp = args.sport.lower()
    mdir = ROOT / "models"
    if sp == "nba":
        mp = args.model or mdir / "prop_model_nba.pkl"
        fp = args.features or mdir / "prop_model_nba_features.json"
    elif sp == "nhl":
        mp = args.model or mdir / "prop_model_nhl.pkl"
        fp = args.features or mdir / "prop_model_nhl_features.json"
    elif sp == "soccer":
        mp = args.model or mdir / "prop_model_soccer.pkl"
        fp = args.features or mdir / "prop_model_soccer_features.json"
    else:
        mp = args.model or mdir / "prop_model_cbb.pkl"
        fp = args.features or mdir / "prop_model_cbb_features.json"

    if not mp.is_file() or not fp.is_file():
        print(f"Missing model or features: {mp} / {fp}")
        sys.exit(1)

    feats = json.loads(fp.read_text(encoding="utf-8"))
    model = joblib.load(mp)
    df = _read_table(args.slate.resolve(), args.sheet.strip())
    if df.empty:
        print("Empty slate.")
        sys.exit(1)

    if sp == "nba":
        p_old, p_new = run_nba(df, model, feats)
    elif sp == "nhl":
        p_old, p_new = run_nhl(df, model, feats)
    elif sp == "cbb":
        p_old, p_new = run_cbb(df, model, feats)
    else:
        p_old, p_new = run_soccer(df, model, feats, args.n_teams)

    d = p_new - p_old
    print(f"Slate: {args.slate.name}  rows={len(df)}  sport={sp}  model={mp.name}")
    print(f"ml_prob  raw-edge mean={p_old.mean():.4f}  play-side mean={p_new.mean():.4f}")
    print(f"delta (new - old): mean={d.mean():.6f}  std={d.std():.6f}  max|d|={np.nanmax(np.abs(d)):.6f}")
    ok = np.isfinite(p_old) & np.isfinite(p_new)
    if ok.sum() > 2:
        corr = np.corrcoef(p_old[ok], p_new[ok])[0, 1]
        print(f"corr(ml_prob raw, ml_prob play-side) = {corr:.4f}")

    if args.out_csv:
        out = df.iloc[: len(p_new)].copy()
        out["ml_prob_raw_edge"] = p_old
        out["ml_prob_play_side_edge"] = p_new
        out["ml_prob_delta"] = d
        out.to_csv(args.out_csv, index=False, encoding="utf-8-sig")
        print(f"Wrote {args.out_csv}")


if __name__ == "__main__":
    main()
