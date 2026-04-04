#!/usr/bin/env python3
"""
Stack one or more --export-graded-tickets-csv outputs and summarize
modeled_ticket_objective vs empirical_ticket_paid (UI-aligned flex/power rules).

Example:
  py scripts/backtest_ticket_objectives.py training/graded_tickets_*.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from combined_ticket_grader import build_ticket_objective_decile_summary  # noqa: E402


def _normalize_include_col(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "include_in_ticket_rate" not in out.columns:
        return out
    ic = out["include_in_ticket_rate"]
    if ic.dtype == object or pd.api.types.is_string_dtype(ic):
        sl = ic.astype(str).str.strip().str.lower()
        out["include_in_ticket_rate"] = sl.isin(("true", "1", "t", "yes"))
    else:
        out["include_in_ticket_rate"] = ic.astype(bool)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest stacked graded ticket CSVs.")
    ap.add_argument(
        "csvs",
        nargs="+",
        type=Path,
        help="Paths to graded ticket CSVs (from combined_ticket_grader --export-graded-tickets-csv)",
    )
    args = ap.parse_args()

    frames: list[pd.DataFrame] = []
    for p in args.csvs:
        p = Path(p)
        if not p.exists():
            raise SystemExit(f"missing file: {p}")
        frames.append(pd.read_csv(p))
    df = pd.concat(frames, ignore_index=True)
    df = _normalize_include_col(df)

    need = {"modeled_ticket_objective", "empirical_ticket_paid", "include_in_ticket_rate"}
    missing = need - set(df.columns)
    if missing:
        raise SystemExit(f"CSV missing columns {sorted(missing)}; got {list(df.columns)}")

    sub = df[df["include_in_ticket_rate"]].copy()
    sub = sub[pd.to_numeric(sub["empirical_ticket_paid"], errors="coerce").notna()]
    n = len(sub)
    paid = pd.to_numeric(sub["empirical_ticket_paid"], errors="coerce")
    print(f"rows_total={len(df)} include_in_ticket_rate={n} mean_paid={paid.mean():.4f}")

    dec = build_ticket_objective_decile_summary(df)
    print("\n--- TICKET_OBJ_DECILES (stacked) ---")
    with pd.option_context("display.max_rows", 30, "display.width", 120):
        print(dec.to_string(index=False))

    try:
        from sklearn.metrics import roc_auc_score

        mo = pd.to_numeric(sub["modeled_ticket_objective"], errors="coerce")
        mask = mo.notna()
        y = paid.loc[mask].astype(float).values
        s = mo.loc[mask].astype(float).values
        if len(y) >= 10 and np.unique(y).size >= 2:
            auc = roc_auc_score(y, s)
            print(f"\nROC-AUC(modeled_ticket_objective vs paid): {auc:.4f} (n={len(y)})")
    except ImportError:
        pass


if __name__ == "__main__":
    main()
