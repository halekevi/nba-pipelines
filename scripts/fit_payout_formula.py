#!/usr/bin/env python3
"""
Fit goblin/demon payout adjustment coefficients from logged payout samples.
"""

from __future__ import annotations

import argparse
import ast
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

# Known standard Power payouts (used for adjustment normalization, not from data).
BASE_PAYOUTS = {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0, 6: 37.5}

# Reasonable observed multiplier ranges by leg count (drops bad parses like 2000x).
VALID_MULTIPLIERS = {
    2: (2.5, 4.0),
    3: (2.0, 8.0),
    4: (4.0, 12.0),
    5: (8.0, 25.0),
    6: (20.0, 45.0),
}

# Flex "miss" tiers can be below Power baseline; keep separate band.
VALID_FLEX_MISS = (1.0, 8.0)


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


def primary_mult_for_row(row: pd.Series) -> float | None:
    """Multiplier used for validation + primary adjustment (Power vs Flex first-place)."""
    ttype = str(row.get("ticket_type", "power")).lower().strip()
    if ttype == "flex":
        ff = pd.to_numeric(row.get("flex_first_place"), errors="coerce")
        if pd.notna(ff) and float(ff) > 0:
            return float(ff)
    v = pd.to_numeric(row.get("displayed_multiplier"), errors="coerce")
    if pd.notna(v) and float(v) > 0:
        return float(v)
    return choose_multiplier(row)


def parse_flex_miss_multipliers(raw: Any) -> list[float]:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none", "[]"):
        return []
    try:
        data = ast.literal_eval(s)
        if isinstance(data, list):
            out: list[float] = []
            for item in data:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    try:
                        out.append(float(item[1]))
                    except (TypeError, ValueError):
                        continue
            return out
    except (SyntaxError, ValueError, TypeError):
        pass
    return [float(x) for x in re.findall(r"\d+\.?\d*", s)]


def is_valid_sample(n_legs: int, mult: float, label: str = "") -> bool:
    if mult <= 0:
        return False
    if n_legs not in BASE_PAYOUTS:
        print(f"[FILTER] Dropping{label}: unsupported n_legs={n_legs}")
        return False
    if n_legs in VALID_MULTIPLIERS:
        lo, hi = VALID_MULTIPLIERS[n_legs]
        if not (lo <= mult <= hi):
            print(
                f"[FILTER] Dropping outlier{label}: "
                f"{n_legs}-leg mult={mult}x (valid: {lo}-{hi}x)"
            )
            return False
    return True


def is_valid_flex_miss(n_legs: int, mult: float) -> bool:
    if mult <= 0:
        return False
    lo, hi = VALID_FLEX_MISS
    if not (lo <= mult <= hi):
        print(
            f"[FILTER] Dropping flex-miss outlier: "
            f"{n_legs}-leg mult={mult}x (valid: {lo}-{hi}x)"
        )
        return False
    return True


def fit_linear(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float]:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    yhat = X @ beta
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-12 else 0.0
    return beta, r2


def fit_model(fit_df: pd.DataFrame, label: str) -> dict[str, Any] | None:
    if fit_df is None or len(fit_df) < 5:
        print(f"[FIT] {label}: skip (need >=5 samples, have {0 if fit_df is None else len(fit_df)})")
        return None

    work = fit_df.copy()
    work = work[(work["adjustment"] >= 0.3) & (work["adjustment"] <= 2.5)]
    if work.empty or len(work) < 5:
        print(f"[FIT] {label}: skip after adjustment clip (n={len(work)})")
        return None

    ng = work["n_goblins"].to_numpy(dtype=float)
    nd = work["n_demons"].to_numpy(dtype=float)
    avg_g = work["avg_goblin_distance"].to_numpy(dtype=float)
    avg_d = work["avg_demon_distance"].to_numpy(dtype=float)
    y = work["adjustment"].to_numpy(dtype=float)
    X = np.column_stack(
        [
            np.ones(len(work)),
            ng,
            ng * avg_g,
            nd,
            nd * avg_d,
        ]
    )
    beta, r2 = fit_linear(X, y)
    intercept, b_ng, b_ngd, b_nd, b_ndd = [float(x) for x in beta]
    A = max(0.0, -b_ng)
    B = max(0.0, -b_ngd)
    C = max(0.0, b_nd)
    D = max(0.0, b_ndd)

    print(f"[FIT] {label} A={A:.6f} B={B:.6f} C={C:.6f} D={D:.6f} intercept={intercept:.6f} R2={r2:.4f} n={len(work)}")

    pred_adj = X @ beta
    pred_mult = work["base_payout"].to_numpy(dtype=float) * pred_adj
    val = work.copy()
    val["predicted_multiplier"] = pred_mult
    val["error_pct"] = np.where(
        val["displayed_multiplier"].to_numpy(dtype=float) > 0,
        (
            np.abs(val["predicted_multiplier"] - val["displayed_multiplier"])
            / val["displayed_multiplier"]
        )
        * 100.0,
        np.nan,
    )
    print(f"\n=== {label} VALIDATION (clean) ===")
    print("Config | Actual Mult | Predicted | Error%")
    keys = ["n_legs", "n_goblins", "n_demons", "ticket_type"]
    printed: set[tuple[Any, ...]] = set()
    for _, row in val.sort_values(["n_legs", "ticket_type", "n_goblins", "n_demons"]).iterrows():
        k = tuple(row[c] for c in keys)
        if k in printed:
            continue
        printed.add(k)
        cfg = f"{int(row['n_legs'])}-leg {row['ticket_type']} g{int(row['n_goblins'])} d{int(row['n_demons'])}"
        print(
            f"{cfg:<24} | {float(row['displayed_multiplier']):>8.2f}x | "
            f"{float(row['predicted_multiplier']):>8.2f}x | {float(row['error_pct']):>6.2f}%"
        )
        if len(printed) >= 15:
            break

    return {
        "label": label,
        "goblin_base_discount": A,
        "goblin_distance_scale": B,
        "demon_base_premium": C,
        "demon_distance_scale": D,
        "intercept": intercept,
        "r_squared": float(r2),
        "n_samples": int(len(work)),
    }


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

    raw = pd.concat(frames, ignore_index=True)
    if raw.empty:
        raise RuntimeError("No rows in payout logs.")

    # --- Clean: valid multiplier per row before building adjustment ---
    clean_rows: list[dict[str, Any]] = []
    for _, r in raw.iterrows():
        n_legs = int(pd.to_numeric(r.get("n_legs"), errors="coerce") or 0)
        mult = primary_mult_for_row(r)
        if mult is None:
            continue
        if not is_valid_sample(n_legs, mult):
            continue
        ttype = str(r.get("ticket_type", "power")).lower().strip()
        base = float(BASE_PAYOUTS[n_legs])
        adj = float(mult) / base
        adj = float(np.clip(adj, 0.3, 2.5))

        legs = parse_legs(r.get("legs"))
        dists_g: list[float] = []
        dists_d: list[float] = []
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

        clean_rows.append(
            {
                "ticket_type": ttype,
                "n_legs": n_legs,
                "displayed_multiplier": float(mult),
                "base_payout": base,
                "adjustment": adj,
                "n_goblins": n_g,
                "n_demons": n_d,
                "avg_goblin_distance": avg_g,
                "avg_demon_distance": avg_d,
            }
        )

    df = pd.DataFrame(clean_rows)
    print(f"[FIT] {len(df)} clean samples after outlier filter")
    if df.empty:
        raise RuntimeError("No rows left after outlier filter.")

    df = df[(df["adjustment"] >= 0.3) & (df["adjustment"] <= 2.5)]
    print(f"[FIT] {len(df)} samples in adjustment band [0.3, 2.5]")

    print("\nAdjustment factor distribution:")
    print(
        df.groupby(["n_goblins", "n_demons"])["adjustment"].agg(["mean", "count", "min", "max"])
    )

    print("\n=== CLEAN SAMPLE SUMMARY ===")
    print(
        df[
            [
                "n_legs",
                "n_goblins",
                "n_demons",
                "ticket_type",
                "displayed_multiplier",
                "adjustment",
            ]
        ].to_string()
    )

    print("\n=== ADJUSTMENT BY GOBLIN COUNT ===")
    print(df.groupby("n_goblins")["adjustment"].agg(["mean", "std", "count"]))

    power_df = df[df["ticket_type"] == "power"].copy()
    flex_df = df[df["ticket_type"] == "flex"].copy()
    print(f"\nPower samples: {len(power_df)}")
    print(f"Flex samples:  {len(flex_df)}")

    power_result = fit_model(power_df, "POWER") if len(power_df) >= 5 else None
    flex_result = fit_model(flex_df, "FLEX") if len(flex_df) >= 5 else None

    # Flex miss-1 tier: smallest parsed "correct pays" mult, separate valid band.
    miss_rows: list[dict[str, Any]] = []
    for _, r in raw.iterrows():
        if str(r.get("ticket_type", "")).lower().strip() != "flex":
            continue
        n_legs = int(pd.to_numeric(r.get("n_legs"), errors="coerce") or 0)
        if n_legs not in BASE_PAYOUTS:
            continue
        miss_vals = parse_flex_miss_multipliers(r.get("flex_miss_1"))
        if not miss_vals:
            continue
        mult = min(miss_vals)
        if not is_valid_flex_miss(n_legs, mult):
            continue
        base = float(BASE_PAYOUTS[n_legs])
        adj = float(np.clip(float(mult) / base, 0.3, 2.5))

        legs = parse_legs(r.get("legs"))
        dists_g: list[float] = []
        dists_d: list[float] = []
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
        miss_rows.append(
            {
                "ticket_type": "flex_miss",
                "n_legs": n_legs,
                "displayed_multiplier": float(mult),
                "base_payout": base,
                "adjustment": adj,
                "n_goblins": n_g,
                "n_demons": n_d,
                "avg_goblin_distance": avg_g,
                "avg_demon_distance": avg_d,
            }
        )
    flex_miss_df = pd.DataFrame(miss_rows)
    flex_miss_result = (
        fit_model(flex_miss_df, "FLEX_MISS") if len(flex_miss_df) >= 5 else None
    )

    fitted_at = datetime.utcnow().isoformat()
    coeffs_out: dict[str, Any] = {
        "fitted_at": fitted_at,
        "base_payouts": BASE_PAYOUTS,
        "n_clean_samples": int(len(df)),
    }
    if power_result:
        coeffs_out["power"] = {k: v for k, v in power_result.items() if k != "label"}
    if flex_result:
        coeffs_out["flex"] = {k: v for k, v in flex_result.items() if k != "label"}
    if flex_miss_result:
        coeffs_out["flex_miss"] = {k: v for k, v in flex_miss_result.items() if k != "label"}

    # Top-level keys for backward compatibility (prefer POWER fit).
    if power_result:
        coeffs_out["goblin_base_discount"] = power_result["goblin_base_discount"]
        coeffs_out["goblin_distance_scale"] = power_result["goblin_distance_scale"]
        coeffs_out["demon_base_premium"] = power_result["demon_base_premium"]
        coeffs_out["demon_distance_scale"] = power_result["demon_distance_scale"]
        coeffs_out["intercept"] = power_result["intercept"]
        coeffs_out["r_squared"] = power_result["r_squared"]
        coeffs_out["n_samples"] = power_result["n_samples"]
    elif flex_result:
        coeffs_out["goblin_base_discount"] = flex_result["goblin_base_discount"]
        coeffs_out["goblin_distance_scale"] = flex_result["goblin_distance_scale"]
        coeffs_out["demon_base_premium"] = flex_result["demon_base_premium"]
        coeffs_out["demon_distance_scale"] = flex_result["demon_distance_scale"]
        coeffs_out["intercept"] = flex_result["intercept"]
        coeffs_out["r_squared"] = flex_result["r_squared"]
        coeffs_out["n_samples"] = flex_result["n_samples"]

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(coeffs_out, indent=2), encoding="utf-8")
    print(f"\n[FIT] Coefficients saved -> {OUT_JSON}")

    updated = False
    if power_result and power_result["r_squared"] > float(args.min_r2_update):
        updated = update_combined_constants_if_good(
            power_result["goblin_base_discount"],
            power_result["goblin_distance_scale"],
            power_result["r_squared"],
        )
    if updated:
        print("[FIT] Updated combined_slate_tickets goblin constants (R2 gate passed).")
    else:
        print("[FIT] No combined_slate_tickets update (R2 gate failed or constants not found).")


if __name__ == "__main__":
    main()
