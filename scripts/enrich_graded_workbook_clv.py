#!/usr/bin/env python3
"""
Add CLV-related columns to a graded Excel workbook (non-destructive to other sheets).

Adds (when missing): my_odds_implied_prob, closing_implied_prob, clv_delta
using American odds columns if present: my_american_odds, closing_american_odds
(or open_american_odds / close_american_odds).

Usage:
    py -3.14 scripts/enrich_graded_workbook_clv.py --graded path/to/graded_nba_2026-04-04.xlsx
    py -3.14 scripts/enrich_graded_workbook_clv.py --scan-dir outputs/2026-04-03 --sheet "Graded Props"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from utils.clv_tracker import _american_to_implied_prob, compute_clv_delta


def _enrich_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in ("my_odds_implied_prob", "closing_implied_prob", "clv_delta"):
        if c not in out.columns:
            out[c] = pd.NA

    def col(names: tuple[str, ...]):
        for n in names:
            if n in out.columns:
                return n
        return None

    my_c = col(("my_odds_implied_prob", "my_implied_prob", "open_implied_prob"))
    cl_c = col(("closing_implied_prob", "close_implied_prob"))
    my_am = col(("my_american_odds", "open_american_odds", "american_odds_open"))
    cl_am = col(("closing_american_odds", "close_american_odds", "american_odds_close"))

    for i in out.index:
        mi = None
        ci = None
        if my_c:
            v = pd.to_numeric(out.at[i, my_c], errors="coerce")
            if pd.notna(v):
                mi = float(v)
        if cl_c:
            v = pd.to_numeric(out.at[i, cl_c], errors="coerce")
            if pd.notna(v):
                ci = float(v)
        if mi is None and my_am:
            mi = _american_to_implied_prob(out.at[i, my_am])
        if ci is None and cl_am:
            ci = _american_to_implied_prob(out.at[i, cl_am])
        d = compute_clv_delta(mi, ci)
        if mi is not None:
            out.at[i, "my_odds_implied_prob"] = mi
        if ci is not None:
            out.at[i, "closing_implied_prob"] = ci
        if d is not None:
            out.at[i, "clv_delta"] = d
    return out


def _process_file(path: Path, sheet) -> None:
    xl = pd.ExcelFile(path, engine="openpyxl")
    all_names = list(xl.sheet_names)
    if sheet:
        to_edit = [sheet] if sheet in all_names else [all_names[0]]
    else:
        to_edit = ["Graded Props"] if "Graded Props" in all_names else [all_names[0]]
    frames = {s: pd.read_excel(path, sheet_name=s, engine="openpyxl") for s in all_names}
    for s in to_edit:
        if s in frames:
            frames[s] = _enrich_df(frames[s])
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        for s in all_names:
            frames[s].to_excel(w, sheet_name=s, index=False)
    print(f"[clv enrich] updated {path.name} (CLV columns on: {', '.join(to_edit)})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graded", default="", help="Single graded .xlsx path")
    ap.add_argument("--scan-dir", default="", help="Directory; process graded_*.xlsx")
    ap.add_argument("--sheet", default=None, help="Sheet name (default: all sheets)")
    args = ap.parse_args()
    if args.graded:
        p = Path(args.graded)
        if not p.is_file():
            print(f"Not found: {p}")
            return
        _process_file(p, args.sheet)
        return
    if args.scan_dir:
        d = Path(args.scan_dir)
        if not d.is_dir():
            print(f"Not a directory: {d}")
            return
        for p in sorted(d.glob("graded_*.xlsx")):
            try:
                _process_file(p, args.sheet)
            except Exception as e:
                print(f"[clv enrich] skip {p.name}: {e}")
        return
    print("Specify --graded or --scan-dir")


if __name__ == "__main__":
    main()
