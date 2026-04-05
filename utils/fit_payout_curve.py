#!/usr/bin/env python3
"""
Fit G_EXP, D_EXP, D_SCALE from data/payout_observations.csv by minimizing
ticket-level squared error vs actual_mult.

Usage:
  py -3.14 utils/fit_payout_curve.py [--dry-run] [--min-obs 10] [--export-curve-report PATH]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.goblin_demon_multiplier import leg_factor, ticket_multiplier  # noqa: E402

PARAMS_PATH = ROOT / "data" / "payout_curve_params.json"
OBS_PATH = ROOT / "data" / "payout_observations.csv"


def _parse_legs(raw: str) -> list[dict]:
    if not raw or not str(raw).strip():
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except json.JSONDecodeError:
        pass
    return []


def _predict_mult(row: pd.Series, g_exp: float, d_exp: float, d_scale: float) -> float:
    stub = {"G_EXP": g_exp, "D_EXP": d_exp, "D_SCALE": d_scale}
    legs = _parse_legs(str(row.get("leg_details_json", "")))
    if not legs:
        return float("nan")
    n = int(row.get("n_legs", len(legs)) or len(legs))
    factors = []
    for leg in legs[:n]:
        dp = leg.get("delta_pct")
        try:
            dp_f = float(dp) if dp is not None and str(dp).strip() != "" else None
        except (TypeError, ValueError):
            dp_f = None
        pt = str(leg.get("pick_type") or "Standard")
        factors.append(leg_factor(dp_f, pt, stub))
    slip = str(row.get("slip_type", "power") or "power").lower()
    if slip == "flex":
        hits = row.get("hits")
        try:
            h = int(hits) if hits is not None and str(hits).strip() != "" else n
        except (TypeError, ValueError):
            h = n
        return ticket_multiplier(n, factors, "flex", hits=h)
    return ticket_multiplier(n, factors, "power")


def _loss(vec: np.ndarray, df: pd.DataFrame) -> float:
    g_exp, d_exp, d_scale = float(vec[0]), float(vec[1]), float(vec[2])
    err = 0.0
    k = 0
    for _, row in df.iterrows():
        try:
            act = float(row["actual_mult"])
        except (TypeError, ValueError):
            continue
        pred = _predict_mult(row, g_exp, d_exp, d_scale)
        if not math.isfinite(pred):
            continue
        err += (pred - act) ** 2
        k += 1
    return err / max(k, 1)


def _r_squared(df: pd.DataFrame, g_exp: float, d_exp: float, d_scale: float) -> float:
    acts: list[float] = []
    preds: list[float] = []
    for _, row in df.iterrows():
        try:
            act = float(row["actual_mult"])
        except (TypeError, ValueError):
            continue
        pred = _predict_mult(row, g_exp, d_exp, d_scale)
        if math.isfinite(pred):
            acts.append(act)
            preds.append(pred)
    if len(acts) < 3:
        return float("nan")
    acts_a = np.array(acts, dtype=float)
    preds_a = np.array(preds, dtype=float)
    ss_res = float(np.sum((acts_a - preds_a) ** 2))
    ss_tot = float(np.sum((acts_a - np.mean(acts_a)) ** 2))
    if ss_tot <= 0:
        return float("nan")
    return 1.0 - ss_res / ss_tot


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-obs", type=int, default=10)
    ap.add_argument("--export-curve-report", default="", help="Write predicted vs actual CSV")
    args = ap.parse_args()

    if not OBS_PATH.is_file():
        print(f"No observations file: {OBS_PATH}")
        return 1

    df = pd.read_csv(OBS_PATH, dtype=str)
    df = df.fillna("")
    # usable rows
    use = []
    for _, row in df.iterrows():
        try:
            float(row.get("actual_mult", ""))
        except (TypeError, ValueError):
            continue
        legs = _parse_legs(str(row.get("leg_details_json", "")))
        if legs:
            use.append(row)
    fit_df = pd.DataFrame(use)
    n_obs = len(fit_df)
    if n_obs < int(args.min_obs):
        print(f"Not enough observations ({n_obs} < {args.min_obs}); skipping fit.")
        return 2

    try:
        from scipy.optimize import minimize
    except ImportError:
        print("scipy required for fit_payout_curve.py")
        return 3

    x0 = np.array([1.0, 1.5, 3.0], dtype=float)
    bounds = [(0.3, 3.0), (0.5, 4.0), (0.5, 8.0)]

    res = minimize(
        lambda v: _loss(v, fit_df),
        x0,
        method="L-BFGS-B",
        bounds=bounds,
    )
    g_exp, d_exp, d_scale = float(res.x[0]), float(res.x[1]), float(res.x[2])
    r2 = _r_squared(fit_df, g_exp, d_exp, d_scale)

    print(f"Fit on n={n_obs} observations")
    print(f"  G_EXP={g_exp:.4f} D_EXP={d_exp:.4f} D_SCALE={d_scale:.4f}")
    print(f"  MSE={res.fun:.6f}  R^2={r2:.4f}")
    if r2 < 0.7 and math.isfinite(r2):
        print("  WARNING: R^2 < 0.7 — model may be a poor match to observations.")

    ex = str(args.export_curve_report or "").strip()
    if ex:
        out_p = Path(ex)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with out_p.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "n_legs", "slip_type", "actual_mult", "pred_mult", "residual"])
            for _, row in fit_df.iterrows():
                act = float(row["actual_mult"])
                pred = _predict_mult(row, g_exp, d_exp, d_scale)
                w.writerow(
                    [
                        row.get("date", ""),
                        row.get("n_legs", ""),
                        row.get("slip_type", ""),
                        act,
                        pred if math.isfinite(pred) else "",
                        act - pred if math.isfinite(pred) else "",
                    ]
                )
        print(f"Wrote curve report -> {out_p}")

    if args.dry_run:
        print("Dry run: not writing payout_curve_params.json")
        return 0

    payload = {}
    if PARAMS_PATH.is_file():
        try:
            payload = json.loads(PARAMS_PATH.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    payload["G_EXP"] = g_exp
    payload["D_EXP"] = d_exp
    payload["D_SCALE"] = d_scale
    payload["observations_count"] = n_obs
    payload["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["last_fit_r_squared"] = round(r2, 4) if math.isfinite(r2) else None
    PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PARAMS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Updated {PARAMS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
