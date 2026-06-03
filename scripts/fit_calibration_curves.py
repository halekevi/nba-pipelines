#!/usr/bin/env python3
"""
Offline calibration curve fit from retrain_dataset.csv.

Writes data/calibration/calibration_curves_<date>.json and calibration_curves_latest.json.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(REPO_ROOT))
from utils.pipeline_read_enrichment import enrich_read_fields_dataframe  # noqa: E402

DEFAULT_INPUT = REPO_ROOT / "data" / "retrain_dataset.csv"
CALIB_DIR = REPO_ROOT / "data" / "calibration"

SKIP_SPORTS = frozenset({"NBA1H", "NBA1Q"})
LIVE_EXCLUDED_SPORTS = frozenset({"NBA1H", "NBA1Q", "SOCCER"})
QUANTILE_10_SPORTS = frozenset({"MLB", "NBA", "WNBA", "SOCCER", "TENNIS"})
NHL_BINS = 5
DEFAULT_BINS = 10


def _norm_sport(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    if s in ("SOC", "FOOTBALL"):
        return "SOCCER"
    return s


def _prepare_retrain_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "prop_type" not in out.columns and "prop" in out.columns:
        out["prop_type"] = out["prop"]
    if "opp" not in out.columns and "opp_team" in out.columns:
        out["opp"] = out["opp_team"]
    if "direction" not in out.columns and "over_under" in out.columns:
        out["direction"] = out["over_under"]
    return out


def _load_dataset(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False)
    df["sport"] = df["sport"].map(_norm_sport)
    prep = _prepare_retrain_columns(df)
    enriched = enrich_read_fields_dataframe(prep)
    prob = pd.to_numeric(enriched.get("hit_prob_selected"), errors="coerce")
    if prob.isna().all() and "ml_prob" in df.columns:
        prob = pd.to_numeric(df["ml_prob"], errors="coerce")
    df["_prob"] = prob.clip(0.01, 0.99)

    hit = pd.to_numeric(df.get("hit"), errors="coerce")
    if "result" in df.columns:
        ru = df["result"].astype(str).str.strip().str.upper()
        hit = hit.mask(ru.isin(("HIT", "WIN", "W", "1", "TRUE")), 1)
        hit = hit.mask(ru.isin(("MISS", "LOSS", "L", "0", "FALSE")), 0)
        keep = ~ru.eq("PUSH")
        df = df.loc[keep].copy()
        hit = hit.loc[keep]
    df["_hit"] = hit
    return df


def _filter_fit_rows(df: pd.DataFrame) -> pd.DataFrame:
    sub = df.loc[~df["sport"].isin(SKIP_SPORTS)].copy()
    sub = sub.loc[sub["_prob"].notna() & sub["_hit"].isin((0, 1))]
    return sub


def _bin_rows(
    g: pd.DataFrame,
    *,
    sport: str,
    n_bins: int,
    bin_method: str,
) -> list[dict[str, Any]]:
    prob = g["_prob"].to_numpy(dtype=float)
    hit = g["_hit"].to_numpy(dtype=float)
    n_total = len(g)
    if n_total < n_bins * 5:
        return []

    if bin_method == "quantile":
        try:
            cats = pd.qcut(g["_prob"], q=n_bins, duplicates="drop")
        except ValueError:
            return []
        g = g.copy()
        g["_bin"] = cats.cat.codes
        if (g["_bin"] < 0).any():
            g = g.loc[g["_bin"] >= 0]
    else:
        edges = np.linspace(0.0, 1.0, n_bins + 1)
        g = g.copy()
        g["_bin"] = pd.cut(
            g["_prob"],
            bins=edges,
            labels=False,
            include_lowest=True,
        ).astype("Int64")
        g = g.loc[g["_bin"].notna()]

    bins_out: list[dict[str, Any]] = []
    for bcode, bg in g.groupby("_bin", sort=True):
        bidx = int(bcode)
        lo = float(bg["_prob"].min())
        hi = float(bg["_prob"].max())
        mean_pred = float(bg["_prob"].mean())
        actual = float(bg["_hit"].mean())
        n = int(len(bg))
        brier = float(np.mean((bg["_hit"].to_numpy() - bg["_prob"].to_numpy()) ** 2))
        bins_out.append(
            {
                "bin": bidx,
                "lower": round(lo, 6),
                "upper": round(hi, 6),
                "mean_predicted": round(mean_pred, 6),
                "actual_hit_rate": round(actual, 6),
                "n": n,
                "brier_contrib": round(brier * n / n_total, 6),
            }
        )
    bins_out.sort(key=lambda x: x["bin"])
    return bins_out


def _calibration_flag(bins: list[dict[str, Any]], *, top_actual_min: float = 0.60) -> str:
    """Auditable sport-level flag: top decile under-realizes vs predicted."""
    if not bins:
        return "ok"
    top_actual = float(bins[-1]["actual_hit_rate"])
    return "inverted_top" if top_actual < top_actual_min else "ok"


def _ece(bins: list[dict[str, Any]], n_total: int) -> float:
    if not bins or n_total <= 0:
        return float("nan")
    return float(
        sum(
            abs(b["actual_hit_rate"] - b["mean_predicted"]) * b["n"] / n_total
            for b in bins
        )
    )


def _fit_sport(g: pd.DataFrame, sport: str, fit_date: str) -> dict[str, Any] | None:
    n_total = len(g)
    if n_total < 50:
        return None

    notes = ""
    if sport == "NHL":
        n_bins = NHL_BINS
        bin_method = "equal_width"
        notes = (
            "Provisional: borderline sample size; confidence_score uses neutral "
            "std_norm until step8 game-log export. Re-check after NHL distribution_std."
        )
    elif sport == "TENNIS":
        n_bins = DEFAULT_BINS
        bin_method = "quantile"
        notes = (
            "Sparse retrain history; calibration_bucket optional on slate. "
            "Re-fit when Tennis graded volume grows."
        )
    elif sport in QUANTILE_10_SPORTS:
        n_bins = DEFAULT_BINS
        bin_method = "quantile"
    else:
        n_bins = DEFAULT_BINS
        bin_method = "quantile"

    bins = _bin_rows(g, sport=sport, n_bins=n_bins, bin_method=bin_method)
    if not bins:
        return None

    return {
        "sport": sport,
        "n_total": int(n_total),
        "ece": round(_ece(bins, n_total), 6),
        "calibration_flag": _calibration_flag(bins),
        "bin_method": bin_method,
        "n_bins": len(bins),
        "bins": bins,
        "fit_date": fit_date,
        "notes": notes,
    }


def fit_curves(
    df: pd.DataFrame,
    *,
    fit_date: str,
) -> dict[str, Any]:
    sub = _filter_fit_rows(df)
    sports_out: dict[str, dict[str, Any]] = {}
    for sport, g in sub.groupby("sport", sort=True):
        su = str(sport).strip().upper()
        if su in SKIP_SPORTS:
            continue
        rec = _fit_sport(g, su, fit_date)
        if rec:
            sports_out[su] = rec

    return {
        "fit_date": fit_date,
        "source": "data/retrain_dataset.csv",
        "excluded_sports": sorted(SKIP_SPORTS),
        "live_excluded_sports": sorted(LIVE_EXCLUDED_SPORTS),
        "sports": sports_out,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default=str(DEFAULT_INPUT), help="Retrain CSV path")
    ap.add_argument(
        "--fit-date",
        default=date.today().isoformat(),
        help="Date stamp for output filename (YYYY-MM-DD)",
    )
    args = ap.parse_args()

    in_path = Path(args.input)
    if not in_path.is_file():
        print(f"[calibration] missing input: {in_path}")
        return 1

    fit_date = str(args.fit_date)[:10]
    df = _load_dataset(in_path)
    payload = fit_curves(df, fit_date=fit_date)

    CALIB_DIR.mkdir(parents=True, exist_ok=True)
    dated = CALIB_DIR / f"calibration_curves_{fit_date}.json"
    latest = CALIB_DIR / "calibration_curves_latest.json"
    text = json.dumps(payload, indent=2)
    dated.write_text(text, encoding="utf-8")
    shutil.copy2(dated, latest)

    print(f"[calibration] wrote {dated} ({len(payload.get('sports') or {})} sports)")
    print(f"[calibration] copied -> {latest}")
    for sp, rec in sorted((payload.get("sports") or {}).items()):
        print(f"  {sp}: n={rec.get('n_total')} ece={rec.get('ece')} bins={rec.get('n_bins')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
