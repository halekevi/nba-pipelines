#!/usr/bin/env python3
"""
Recompute Tier from Rank Score on dated NBA / NBA1H / NBA1Q step8 *clean* workbooks
(outputs/.../step8_*_direction_clean_DATE.xlsx).

Mirrors NBA/scripts/step7_rank_props.py tier bands (rank_score → A/B/C/D).
Rebuilds ALL + Tier A/B/C/D sheets (Tier sheets omitted when empty — same pattern as step8).

Usage:
  py -3.14 scripts/re_apply_nba_tiers_dated_step8_clean.py path1.xlsx path2.xlsx
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def _tier_from_scores(score: pd.Series) -> pd.Series:
    s = pd.to_numeric(score, errors="coerce")
    return pd.Series(
        np.where(
            s >= 1.25,
            "A",
            np.where(s >= 0.75, "B", np.where(s >= 0.40, "C", "D")),
        ),
        index=score.index,
    )


def retier_one(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)

    xl = pd.ExcelFile(path)
    if "ALL" not in xl.sheet_names:
        raise SystemExit(f"{path}: missing ALL sheet")

    df = pd.read_excel(path, sheet_name="ALL")
    rs_candidates = ["Rank Score", "rank_score", "RankScore"]
    rs_col = next((c for c in rs_candidates if c in df.columns), None)
    if rs_col is None:
        raise SystemExit(f"{path}: need one of {rs_candidates} on ALL")

    tier_col = "Tier" if "Tier" in df.columns else next((c for c in df.columns if str(c).lower() == "tier"), None)
    if tier_col is None:
        df["Tier"] = ""
        tier_col = "Tier"

    df[tier_col] = _tier_from_scores(df[rs_col])

    with pd.ExcelWriter(path, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="ALL", index=False)
        for letter in ("A", "B", "C", "D"):
            sub = df[df[tier_col].astype(str).str.strip().str.upper().eq(letter)]
            if sub.empty:
                continue
            sub.to_excel(w, sheet_name=f"Tier {letter}", index=False)

    vc = df[tier_col].value_counts()
    print(f"OK -> {path.name} ({len(df)} rows)")
    print(vc.to_string())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", help="step8_*_direction_clean_*.xlsx files")
    args = ap.parse_args()

    for raw in args.paths:
        retier_one(Path(raw))


if __name__ == "__main__":
    main()
