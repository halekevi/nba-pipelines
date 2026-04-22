#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = ROOT / "data" / "ml" / "ticket_training_dataset.csv"
MODEL_PATH = ROOT / "models" / "ticket_model.pkl"
FEATURES_PATH = ROOT / "models" / "ticket_model_features.json"
DEFAULT_OUT_CSV = ROOT / "data" / "ml" / "ticket_model_eval_by_date.csv"
DEFAULT_OUT_JSON = ROOT / "data" / "ml" / "ticket_model_eval_summary.json"


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    num_cols = [
        "n_legs",
        "is_flex_structure",
        "sports_in_ticket",
        "legs_nba",
        "legs_cbb",
        "legs_nhl",
        "legs_soccer",
        "legs_mlb",
        "pick_standard_count",
        "pick_goblin_count",
        "pick_demon_count",
        "ticket_objective_score",
        "ev_power",
        "est_ev",
        "flat_ev",
        "payout_multiplier",
        "power_payout",
        "flex_payout",
        "est_win_prob",
        "predicted_payout_mult",
        "predicted_p_win",
        "predicted_ev",
        "avg_hit_rate_leg",
        "avg_ml_prob_leg",
        "min_ml_prob_leg",
        "max_ml_prob_leg",
        "std_ml_prob_leg",
        "avg_leg_prob_used",
        "min_leg_prob_used",
        "avg_edge_leg",
        "min_edge_leg",
        "max_edge_leg",
        "avg_abs_edge_leg",
        "avg_rank_score_leg",
        "min_rank_score_leg",
        "avg_context_score_leg",
        "avg_intel_hit_rate_leg",
    ]
    for c in num_cols:
        if c not in df.columns:
            df[c] = np.nan
        df[c] = _to_num(df[c])
    df[num_cols] = df[num_cols].fillna(0.0)

    cat_cols = []
    for c in ("group_type", "dominant_sport"):
        if c not in df.columns:
            df[c] = ""
        cat_cols.append(c)

    X_num = df[num_cols].astype(float)
    X_cat = pd.get_dummies(df[cat_cols].astype(str), prefix=cat_cols, dtype=float)
    return pd.concat([X_num, X_cat], axis=1).fillna(0.0)


def _norm01(vals: pd.Series) -> pd.Series:
    x = _to_num(vals).fillna(0.0)
    mn = float(x.min()) if len(x) else 0.0
    mx = float(x.max()) if len(x) else 0.0
    if abs(mx - mn) < 1e-12:
        return pd.Series([0.5] * len(x), index=x.index, dtype=float)
    return (x - mn) / (mx - mn)


def _summarize_subset(df: pd.DataFrame) -> dict[str, float]:
    n = len(df)
    if n == 0:
        return {
            "n": 0,
            "cash_rate": 0.0,
            "avg_net_10": 0.0,
            "total_net_10": 0.0,
            "avg_est_ev": 0.0,
        }
    lc = _to_num(df.get("label_cash", pd.Series([], dtype=float))).fillna(0.0)
    net10 = _to_num(df.get("net_10", pd.Series([], dtype=float))).fillna(0.0)
    est_ev = _to_num(df.get("est_ev", pd.Series([], dtype=float))).fillna(0.0)
    return {
        "n": float(n),
        "cash_rate": float(lc.mean()),
        "avg_net_10": float(net10.mean()),
        "total_net_10": float(net10.sum()),
        "avg_est_ev": float(est_ev.mean()),
    }


def _calibration_bins(df: pd.DataFrame, score_col: str, y_col: str, bins: int = 10) -> list[dict[str, Any]]:
    x = _to_num(df[score_col]).clip(0.0, 1.0)
    y = _to_num(df[y_col])
    w = pd.DataFrame({"x": x, "y": y}).dropna()
    if w.empty:
        return []
    # equal-width bins in probability space for interpretability
    edges = np.linspace(0.0, 1.0, bins + 1)
    out: list[dict[str, Any]] = []
    for i in range(bins):
        lo, hi = float(edges[i]), float(edges[i + 1])
        if i < bins - 1:
            m = (w["x"] >= lo) & (w["x"] < hi)
        else:
            m = (w["x"] >= lo) & (w["x"] <= hi)
        sub = w.loc[m]
        if sub.empty:
            continue
        out.append(
            {
                "bin": i + 1,
                "lo": lo,
                "hi": hi,
                "n": int(len(sub)),
                "avg_pred": float(sub["x"].mean()),
                "empirical": float(sub["y"].mean()),
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare EV-only vs ticket-model rerank outcomes by date and top-N.")
    ap.add_argument("--input-csv", default=str(DEFAULT_DATASET), help="Ticket-level dataset CSV.")
    ap.add_argument("--model", default=str(MODEL_PATH), help="Trained ticket model path.")
    ap.add_argument("--features", default=str(FEATURES_PATH), help="Ticket model features json path.")
    ap.add_argument("--top-n", type=int, default=10, help="Top-N tickets per date to compare.")
    ap.add_argument("--weight", type=float, default=0.35, help="Blend weight for model p_cash vs EV-normalized score.")
    ap.add_argument(
        "--ranking-mode",
        choices=("blend", "model"),
        default="blend",
        help="Rerank mode for model arm: blend (EV+model) or model (model p_cash only).",
    )
    ap.add_argument("--out-csv", default=str(DEFAULT_OUT_CSV), help="Per-date metrics CSV output.")
    ap.add_argument("--out-json", default=str(DEFAULT_OUT_JSON), help="Summary JSON output.")
    args = ap.parse_args()

    data_path = Path(args.input_csv)
    model_path = Path(args.model)
    feats_path = Path(args.features)
    if not data_path.is_file():
        raise FileNotFoundError(f"Dataset not found: {data_path}")
    if not model_path.is_file():
        raise FileNotFoundError(f"Model not found: {model_path}")
    if not feats_path.is_file():
        raise FileNotFoundError(f"Features file not found: {feats_path}")

    df = pd.read_csv(data_path, low_memory=False)
    if "label_cash" not in df.columns:
        raise RuntimeError("Dataset is missing label_cash.")
    m = _to_num(df["label_cash"]).isin([0, 1])
    df = df.loc[m].copy()
    if df.empty:
        raise RuntimeError("No decided rows in dataset after filtering.")

    model = joblib.load(model_path)
    feat_cols = json.loads(feats_path.read_text(encoding="utf-8"))
    if not isinstance(feat_cols, list) or not feat_cols:
        raise RuntimeError("Feature list is empty/invalid.")

    X_all = _build_features(df)
    for c in feat_cols:
        if c not in X_all.columns:
            X_all[c] = 0.0
    X_all = X_all[feat_cols].astype(float)
    p_cash = model.predict_proba(X_all)[:, 1]
    df["ticket_model_p_cash"] = p_cash

    df["est_ev_num"] = _to_num(df.get("est_ev", np.nan)).fillna(0.0)
    # EV-only score (within date) and blended score for rerank
    w = max(0.0, min(1.0, float(args.weight)))
    out_rows: list[dict[str, Any]] = []

    if "slate_date" not in df.columns:
        df["slate_date"] = ""
    for d, g in df.groupby("slate_date", sort=True):
        g = g.copy()
        if g.empty:
            continue
        g["score_ev"] = _norm01(g["est_ev_num"])
        g["score_blend"] = (1.0 - w) * g["score_ev"] + w * g["ticket_model_p_cash"]
        g["score_model"] = _to_num(g["ticket_model_p_cash"]).fillna(0.0)

        n = max(1, int(args.top_n))
        top_ev = g.sort_values(["score_ev", "ticket_model_p_cash"], ascending=[False, False]).head(n)
        model_score_col = "score_model" if str(args.ranking_mode) == "model" else "score_blend"
        top_model = g.sort_values([model_score_col, "ticket_model_p_cash"], ascending=[False, False]).head(n)

        ev_keys = set(top_ev.get("ticket_uid", pd.Series([], dtype=str)).astype(str).tolist())
        model_keys = set(top_model.get("ticket_uid", pd.Series([], dtype=str)).astype(str).tolist())
        overlap = len(ev_keys & model_keys)
        swapped = max(0, n - overlap)

        a = _summarize_subset(top_ev)
        b = _summarize_subset(top_model)
        out_rows.append(
            {
                "slate_date": str(d),
                "top_n": int(n),
                "ev_n": int(a["n"]),
                "ev_cash_rate": a["cash_rate"],
                "ev_avg_net_10": a["avg_net_10"],
                "ev_total_net_10": a["total_net_10"],
                "model_n": int(b["n"]),
                "model_cash_rate": b["cash_rate"],
                "model_avg_net_10": b["avg_net_10"],
                "model_total_net_10": b["total_net_10"],
                "delta_cash_rate": b["cash_rate"] - a["cash_rate"],
                "delta_avg_net_10": b["avg_net_10"] - a["avg_net_10"],
                "delta_total_net_10": b["total_net_10"] - a["total_net_10"],
                "top_overlap_count": int(overlap),
                "top_swapped_count": int(swapped),
            }
        )

    by_date = pd.DataFrame(out_rows)
    out_csv = Path(args.out_csv)
    out_json = Path(args.out_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    if not by_date.empty:
        by_date = by_date.sort_values("slate_date")
        by_date.to_csv(out_csv, index=False, encoding="utf-8-sig")

    # Global calibration on decided rows
    calib = _calibration_bins(df, "ticket_model_p_cash", "label_cash", bins=10)
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_csv": str(data_path),
        "model_path": str(model_path),
        "features_path": str(feats_path),
        "rows_decided": int(len(df)),
        "date_count": int(by_date["slate_date"].nunique()) if not by_date.empty else 0,
        "top_n": int(args.top_n),
        "blend_weight": float(w),
        "ranking_mode": str(args.ranking_mode),
        "overall": {
            "cash_rate": float(_to_num(df["label_cash"]).mean()),
            "avg_net_10": float(_to_num(df.get("net_10", np.nan)).fillna(0.0).mean()),
            "avg_pred_p_cash": float(_to_num(df["ticket_model_p_cash"]).mean()),
        },
        "by_date_avg_delta": {
            "delta_cash_rate": float(by_date["delta_cash_rate"].mean()) if not by_date.empty else 0.0,
            "delta_avg_net_10": float(by_date["delta_avg_net_10"].mean()) if not by_date.empty else 0.0,
            "delta_total_net_10": float(by_date["delta_total_net_10"].mean()) if not by_date.empty else 0.0,
            "top_swapped_count": float(by_date["top_swapped_count"].mean()) if not by_date.empty else 0.0,
        },
        "calibration_bins": calib,
    }
    out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"-> Decided rows: {len(df)}")
    print(f"-> Date groups: {summary['date_count']}")
    if not by_date.empty:
        print("-> Mean delta (model - EV) by date:")
        print(
            f"   cash_rate={summary['by_date_avg_delta']['delta_cash_rate']:.4f} "
            f"avg_net_10={summary['by_date_avg_delta']['delta_avg_net_10']:.4f} "
            f"total_net_10={summary['by_date_avg_delta']['delta_total_net_10']:.4f} "
            f"avg_swapped={summary['by_date_avg_delta']['top_swapped_count']:.2f}"
        )
        print(f"-> Wrote {out_csv}")
    print(f"-> Wrote {out_json}")


if __name__ == "__main__":
    main()
