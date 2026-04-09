#!/usr/bin/env python3
"""
Fit goblin/demon payout adjustment coefficients from logged payout samples.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = ROOT / "data" / "payout_samples"
OUT_JSON = ROOT / "data" / "payout_formula_coefficients.json"
COMBINED_TICKETS = ROOT / "scripts" / "combined_slate_tickets.py"

BASE_POWER = {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0, 6: 25.0}


def parse_legs(legs_raw: Any) -> list[dict]:
    if isinstance(legs_raw, list):
        return legs_raw
    try:
        v = json.loads(str(legs_raw))
        if isinstance(v, list):
            return v
    except Exception:
        pass
    return []


def choose_multiplier(row: pd.Series) -> float | None:
    if str(row.get("ticket_type", "")).lower() == "flex":
        for c in ["flex_first_place", "displayed_multiplier"]:
            v = pd.to_numeric(row.get(c), errors="coerce")
            if pd.notna(v) and float(v) > 0:
                return float(v)
        ea = pd.to_numeric(row.get("entry_amount"), errors="coerce")
        tw = pd.to_numeric(row.get("to_win_amount"), errors="coerce")
        if pd.notna(ea) and pd.notna(tw) and float(ea) > 0:
            return float(tw) / float(ea)
        return None
    v = pd.to_numeric(row.get("displayed_multiplier"), errors="coerce")
    if pd.notna(v) and float(v) > 0:
        return float(v)
    ea = pd.to_numeric(row.get("entry_amount"), errors="coerce")
    tw = pd.to_numeric(row.get("to_win_amount"), errors="coerce")
    if pd.notna(ea) and pd.notna(tw) and float(ea) > 0:
        return float(tw) / float(ea)
    return None


def fit_linear(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float]:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    yhat = X @ beta
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-12 else 0.0
    return beta, r2


def update_combined_constants_if_good(a: float, b: float, r2: float) -> bool:
    if r2 <= 0.85 or not COMBINED_TICKETS.exists():
        return False
    txt = COMBINED_TICKETS.read_text(encoding="utf-8")
    orig = txt
    txt = re.sub(
        r"(?m)^(\s*GOBLIN_BASE_DISCOUNT\s*=\s*)([0-9]*\.?[0-9]+)",
        rf"\g<1>{a:.6f}",
        txt,
    )
    txt = re.sub(
        r"(?m)^(\s*GOBLIN_LINE_DIST_SCALE\s*=\s*)([0-9]*\.?[0-9]+)",
        rf"\g<1>{b:.6f}",
        txt,
    )
    if txt != orig:
        COMBINED_TICKETS.write_text(txt, encoding="utf-8")
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-r2-update", type=float, default=0.85)
    args = ap.parse_args()

    files = sorted(SAMPLES_DIR.glob("payout_log_*.csv"))
    if not files:
        raise FileNotFoundError(f"No payout logs found in {SAMPLES_DIR}")

    frames = []
    for f in files:
        try:
            frames.append(pd.read_csv(f, low_memory=False))
        except Exception as e:
            print(f"[FIT] WARN: skip {f.name}: {e}")
    if not frames:
        raise RuntimeError("No readable payout logs.")

    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        raise RuntimeError("No rows in payout logs.")

    rows = []
    for _, r in df.iterrows():
        n_legs = int(pd.to_numeric(r.get("n_legs"), errors="coerce") or 0)
        ttype = str(r.get("ticket_type", "power")).lower().strip()
        mult = choose_multiplier(r)
        if mult is None or n_legs not in BASE_POWER:
            continue
        base = float(BASE_POWER[n_legs])
        adj = float(mult) / base if base > 0 else np.nan
        if not np.isfinite(adj):
            continue

        legs = parse_legs(r.get("legs"))
        dists_g = []
        dists_d = []
        for leg in legs:
            ptype = str(leg.get("pick_type", "")).lower()
            dist = pd.to_numeric(leg.get("line_distance"), errors="coerce")
            if pd.isna(dist):
                continue
            if "goblin" in ptype:
                dists_g.append(float(dist))
            elif "demon" in ptype:
                dists_d.append(float(dist))

        n_g = int(pd.to_numeric(r.get("n_goblins"), errors="coerce") or 0)
        n_d = int(pd.to_numeric(r.get("n_demons"), errors="coerce") or 0)
        avg_g = float(np.mean(dists_g)) if dists_g else 0.0
        avg_d = float(np.mean(dists_d)) if dists_d else 0.0
        rows.append(
            {
                "ticket_type": ttype,
                "n_legs": n_legs,
                "actual_multiplier": float(mult),
                "base_multiplier": base,
                "adjustment": adj,
                "n_goblins": n_g,
                "n_demons": n_d,
                "avg_goblin_distance": avg_g,
                "avg_demon_distance": avg_d,
            }
        )

    fit_df = pd.DataFrame(rows)
    if fit_df.empty:
        raise RuntimeError("No usable rows for fitting.")

    # adjustment = 1 - A*ng - B*(ng*avg_g) + C*nd + D*(nd*avg_d)
    ng = fit_df["n_goblins"].to_numpy(dtype=float)
    nd = fit_df["n_demons"].to_numpy(dtype=float)
    avg_g = fit_df["avg_goblin_distance"].to_numpy(dtype=float)
    avg_d = fit_df["avg_demon_distance"].to_numpy(dtype=float)
    y = fit_df["adjustment"].to_numpy(dtype=float)
    X = np.column_stack(
        [
            np.ones(len(fit_df)),
            ng,
            ng * avg_g,
            nd,
            nd * avg_d,
        ]
    )
    beta, r2 = fit_linear(X, y)
    intercept, b_ng, b_ngd, b_nd, b_ndd = [float(x) for x in beta]
    A = max(0.0, -b_ng)  # goblin base discount
    B = max(0.0, -b_ngd)  # goblin distance scale
    C = max(0.0, b_nd)  # demon base premium
    D = max(0.0, b_ndd)  # demon distance scale

    coeffs = {
        "goblin_base_discount": A,
        "goblin_distance_scale": B,
        "demon_base_premium": C,
        "demon_distance_scale": D,
        "intercept": intercept,
        "r_squared": float(r2),
        "n_samples": int(len(fit_df)),
        "fitted_at": datetime.utcnow().isoformat(),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(coeffs, indent=2), encoding="utf-8")

    updated = False
    if r2 > float(args.min_r2_update):
        updated = update_combined_constants_if_good(A, B, r2)

    print("[FIT] Fitted formula:")
    print("adjustment = 1.0 - (A * n_goblins) - (B * n_goblins * avg_goblin_distance) + (C * n_demons) + (D * n_demons * avg_demon_distance)")
    print(f"[FIT] A={A:.6f} B={B:.6f} C={C:.6f} D={D:.6f} intercept={intercept:.6f} R2={r2:.4f} n={len(fit_df)}")
    print(f"[FIT] Coefficients saved -> {OUT_JSON}")
    if updated:
        print("[FIT] Updated combined_slate_tickets goblin constants (R2 gate passed).")
    else:
        print("[FIT] No combined_slate_tickets update (R2 gate failed or constants not found).")

    # Quick validation table
    pred_adj = X @ beta
    pred_mult = fit_df["base_multiplier"].to_numpy(dtype=float) * pred_adj
    val = fit_df.copy()
    val["predicted_multiplier"] = pred_mult
    val["error_pct"] = np.where(
        val["actual_multiplier"].to_numpy(dtype=float) > 0,
        (np.abs(val["predicted_multiplier"] - val["actual_multiplier"]) / val["actual_multiplier"]) * 100.0,
        np.nan,
    )
    keys = ["n_legs", "n_goblins", "n_demons", "ticket_type"]
    val = val.sort_values(["n_legs", "ticket_type", "n_goblins", "n_demons"])
    print("\nConfig | Actual Mult | Predicted | Error%")
    printed = set()
    for _, r in val.iterrows():
        k = tuple(r[c] for c in keys)
        if k in printed:
            continue
        printed.add(k)
        cfg = f"{int(r['n_legs'])}-leg {r['ticket_type']} g{int(r['n_goblins'])} d{int(r['n_demons'])}"
        print(
            f"{cfg:<24} | {float(r['actual_multiplier']):>8.2f}x | {float(r['predicted_multiplier']):>8.2f}x | {float(r['error_pct']):>6.2f}%"
        )
        if len(printed) >= 15:
            break


if __name__ == "__main__":
    main()

