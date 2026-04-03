#!/usr/bin/env python3
"""
For each calendar date under outputs/<date>/, pick the single best row per sport
from graded_*.xlsx (by rank_score, else blended_score, else |ml_prob-0.5|, else confidence_score).
Report HIT rate on decided (HIT+MISS) top picks — excludes VOID top picks from denominator
unless you only have VOID (then skip that sport for the day).

Usage:
  py -3 scripts/analyze_top_pick_per_pipeline.py
  py -3 scripts/analyze_top_pick_per_pipeline.py --date 2026-04-01
  py -3 scripts/analyze_top_pick_per_pipeline.py --from-date 2026-03-26
  py -3 scripts/analyze_top_pick_per_pipeline.py --from-date 2026-03-26 --to-date 2026-04-02
"""
from __future__ import annotations

import argparse
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
OUTPUTS = REPO / "outputs"
DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")

PIPES = ("nba", "cbb", "wcbb", "nhl", "soccer", "mlb", "nba1h", "nba1q", "nba2q")


def _read_graded(path: Path) -> pd.DataFrame:
    xf = pd.ExcelFile(path)
    for name in ("Box Raw", "GRADED", "Sheet1"):
        if name in xf.sheet_names:
            return pd.read_excel(path, sheet_name=name)
    return pd.read_excel(path, sheet_name=xf.sheet_names[0])


def _score_column(df: pd.DataFrame) -> tuple[str, bool]:
    """Return (column_name, higher_is_better)."""
    if "rank_score" in df.columns and pd.to_numeric(df["rank_score"], errors="coerce").notna().any():
        return "rank_score", True
    if "blended_score" in df.columns and pd.to_numeric(df["blended_score"], errors="coerce").notna().any():
        return "blended_score", True
    if "ml_prob" in df.columns and pd.to_numeric(df["ml_prob"], errors="coerce").notna().any():
        return "ml_prob", True  # use |p-0.5| for ordering
    if "confidence_score" in df.columns and pd.to_numeric(df["confidence_score"], errors="coerce").notna().any():
        return "confidence_score", True
    return "", True


def best_row(df: pd.DataFrame) -> pd.Series | None:
    if df.empty or "result" not in df.columns:
        return None
    sub = df[df["result"].isin(["HIT", "MISS"])].copy()
    if sub.empty:
        return None
    col, _hi = _score_column(sub)
    if not col:
        return sub.iloc[0]
    v = pd.to_numeric(sub[col], errors="coerce")
    if col == "ml_prob":
        v = (v - 0.5).abs()
    sub = sub.assign(_rank=v)
    sub = sub.sort_values("_rank", ascending=False, na_position="last")
    return sub.iloc[0]


def analyze_date(d: str) -> dict:
    folder = OUTPUTS / d
    out: dict = {"date": d, "picks": [], "hits": 0, "decided": 0}
    if not folder.is_dir():
        return out
    for pipe in PIPES:
        path = folder / f"graded_{pipe}_{d}.xlsx"
        if not path.is_file():
            continue
        try:
            df = _read_graded(path)
        except Exception as e:
            out["picks"].append({"sport": pipe, "error": str(e)})
            continue
        row = best_row(df)
        if row is None:
            out["picks"].append({"sport": pipe, "result": "NO_DECIDED"})
            continue
        r = str(row.get("result", "")).upper()
        player = str(row.get("player", row.get("Player", "")))[:40]
        prop = str(row.get("prop_type_norm", row.get("Prop", "")))[:50]
        out["picks"].append(
            {
                "sport": pipe,
                "result": r,
                "player": player,
                "prop": prop,
                "tier": str(row.get("tier", "")),
            }
        )
        if r in ("HIT", "MISS"):
            out["decided"] += 1
            out["hits"] += int(r == "HIT")
    return out


def _parse_ymd(s: str) -> date:
    return datetime.strptime(s.strip()[:10], "%Y-%m-%d").date()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="Single YYYY-MM-DD; default = scan all dates")
    ap.add_argument("--from-date", default="", help="Inclusive lower bound YYYY-MM-DD (chronological table)")
    ap.add_argument("--to-date", default="", help="Inclusive upper bound YYYY-MM-DD (default: today)")
    ap.add_argument("--top-days", type=int, default=0, help="Print only last N rows when >0 (default: all in range)")
    args = ap.parse_args()

    if args.date.strip():
        dates = [args.date.strip()[:10]]
        chronological = False
    else:
        dates = sorted(
            p.name for p in OUTPUTS.iterdir() if p.is_dir() and DATE_DIR.match(p.name)
        )
        if args.from_date.strip():
            lo = _parse_ymd(args.from_date)
            hi = _parse_ymd(args.to_date) if args.to_date.strip() else date.today()
            dates = [d for d in dates if lo <= _parse_ymd(d) <= hi]
            chronological = True
        else:
            chronological = False

    rows = []
    for d in dates:
        r = analyze_date(d)
        if not r["picks"]:
            continue
        hr = r["hits"] / r["decided"] if r["decided"] else float("nan")
        rows.append((d, r["decided"], r["hits"], hr, len(r["picks"])))

    if chronological:
        rows.sort(key=lambda x: x[0])
    else:
        rows.sort(key=lambda x: (-(x[1] if x[1] else -1), -(x[2] if x[2] else -1), x[0]))

    title = "Top-pick-per-pipeline simulation (best rank_score / blended / ml edge / confidence)"
    if args.from_date.strip() or args.to_date.strip():
        lo = args.from_date.strip() or "(start)"
        hi = args.to_date.strip() or str(date.today())
        title += f"\nWindow: {lo} .. {hi}"
    print(title + "\n")

    print(f"{'date':12} {'sports_w_file':>14} {'decided_tops':>13} {'hits':>5} {'HR':>8}")
    print("-" * 56)
    out_rows = rows if args.top_days <= 0 else rows[-args.top_days :]
    for d, dec, h, hr, nfiles in out_rows:
        hr_s = f"{hr:.1%}" if dec else "n/a"
        print(f"{d:12} {nfiles:14} {dec:13} {h:5} {hr_s:>8}")

    tot_dec = sum(x[1] for x in rows)
    tot_hit = sum(x[2] for x in rows)
    days_with_dec = sum(1 for x in rows if x[1] > 0)
    print("-" * 56)
    overall = tot_hit / tot_dec if tot_dec else float("nan")
    print(
        f"{'WINDOW TOTAL':12} {days_with_dec:14} {tot_dec:13} {tot_hit:5} "
        f"{overall:7.1%}  (hit / decided top picks)"
    )

    if not args.date.strip() and rows and not chronological:
        best = max((x for x in rows if x[1] > 0), key=lambda x: (x[1], x[3]), default=None)
        if best:
            d = best[0]
            print(f"\n--- Detail for richest date: {d} (decided top picks: {best[1]}, HR {best[3]:.1%}) ---")
            r = analyze_date(d)
            for p in r["picks"]:
                print(p)


if __name__ == "__main__":
    main()
