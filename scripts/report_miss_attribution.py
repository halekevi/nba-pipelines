#!/usr/bin/env python3
"""
Daily miss/hit attribution from graded prop workbooks (Box Raw / GRADED sheets).

Outputs per-leg rows with margin vs line, near-miss flag, ml_prob decile, and
slice tags so we can see why legs hit or missed and tune win-rate filters.

Usage:
  py -3.14 scripts/report_miss_attribution.py --date 2026-06-08
  py -3.14 scripts/report_miss_attribution.py --date 2026-06-08 --repo-root .
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.graded_schema import normalize_graded_df, recover_direction_if_missing  # noqa: E402

NEAR_LINE_EPS = 0.5
GRADED_GLOB = "graded_*_{date}.xlsx"


def _sport_from_filename(name: str) -> str:
    n = name.lower()
    for token in ("nba1q", "nba1h", "wnba", "wcbb", "cbb", "nba", "nhl", "mlb", "soccer", "tennis"):
        if token in n:
            return token.upper() if token != "soccer" else "SOCCER"
    return ""


def _load_graded_sheet(path: Path) -> pd.DataFrame:
    xls = pd.ExcelFile(path, engine="openpyxl")
    for sh in ("Box Raw", "graded", "GRADED", "Graded"):
        if sh in xls.sheet_names:
            df = pd.read_excel(path, sheet_name=sh, engine="openpyxl")
            if "result" in {c.lower() for c in df.columns}:
                return df
    for sh in xls.sheet_names:
        df = pd.read_excel(path, sheet_name=sh, engine="openpyxl")
        cols = {str(c).lower() for c in df.columns}
        if "result" in cols or "leg_result" in cols:
            return df
    return pd.read_excel(path, sheet_name=xls.sheet_names[0], engine="openpyxl")


def _result_col(df: pd.DataFrame) -> str:
    for c in df.columns:
        if str(c).strip().lower() in ("result", "leg_result"):
            return str(c)
    raise KeyError("no result column")


def _first_col(df: pd.DataFrame, names: tuple[str, ...]) -> pd.Series:
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series(np.nan, index=df.index)


def _margin(actual: float, line: float, direction: str) -> float | None:
    if not np.isfinite(actual) or not np.isfinite(line):
        return None
    m = float(actual) - float(line)
    d = str(direction or "").strip().upper()
    if d == "UNDER":
        return -m
    return m


def _build_rows(df: pd.DataFrame, *, sport: str, source: str, slate_date: str) -> pd.DataFrame:
    df = normalize_graded_df(df.copy())
    df = recover_direction_if_missing(df)
    rc = _result_col(df)
    res = df[rc].astype(str).str.strip().str.upper()
    sub = df[res.isin(["HIT", "MISS"])].copy()
    if sub.empty:
        return pd.DataFrame()

    actual = pd.to_numeric(_first_col(sub, ("actual", "Actual", "actual_value")), errors="coerce")
    line = pd.to_numeric(_first_col(sub, ("line", "Line", "line_score")), errors="coerce")
    direction = _first_col(sub, ("direction", "bet_direction", "final_bet_direction")).astype(str).str.upper()
    ml_prob = pd.to_numeric(_first_col(sub, ("ml_prob",)), errors="coerce")
    hit_rate = pd.to_numeric(_first_col(sub, ("hit_rate", "composite_hit_rate")), errors="coerce")
    tier = _first_col(sub, ("tier", "Tier")).astype(str).str.upper()
    pick_type = _first_col(sub, ("pick_type", "Pick Type")).astype(str)
    prop_type = _first_col(sub, ("prop_type", "Prop Type")).astype(str)
    player = _first_col(sub, ("player", "Player")).astype(str)
    team = _first_col(sub, ("team", "Team")).astype(str)

    margins = [
        _margin(float(a) if pd.notna(a) else np.nan, float(ln) if pd.notna(ln) else np.nan, d)
        for a, ln, d in zip(actual, line, direction, strict=False)
    ]
    margin_s = pd.Series(margins, index=sub.index, dtype=float)
    near = margin_s.abs() < NEAR_LINE_EPS

    out = pd.DataFrame(
        {
            "slate_date": slate_date,
            "source_file": source,
            "sport": sport or sub.get("sport", pd.Series("", index=sub.index)).astype(str).str.upper(),
            "player": player,
            "team": team,
            "prop_type": prop_type,
            "pick_type": pick_type,
            "tier": tier,
            "direction": direction,
            "line": line,
            "actual": actual,
            "result": res.loc[sub.index],
            "margin": margin_s,
            "near_miss": near.astype(int),
            "ml_prob": ml_prob,
            "hit_rate": hit_rate,
        }
    )
    if out["sport"].astype(str).str.strip().eq("").all() and sport:
        out["sport"] = sport
    return out


def _summarize(legs: pd.DataFrame) -> pd.DataFrame:
    if legs.empty:
        return pd.DataFrame()
    legs = legs.copy()
    legs["outcome"] = legs["result"].astype(str).str.upper()
    rows: list[dict] = []

    def _agg(g: pd.DataFrame, label: str, filt: pd.Series) -> None:
        sub = g.loc[filt]
        if sub.empty:
            return
        rows.append(
            {
                "slice": label,
                "n": int(len(sub)),
                "hit_rate": float((sub["outcome"] == "HIT").mean()),
                "avg_margin": float(pd.to_numeric(sub["margin"], errors="coerce").mean()),
                "near_miss_pct": float(pd.to_numeric(sub["near_miss"], errors="coerce").mean()),
                "avg_ml_prob": float(pd.to_numeric(sub["ml_prob"], errors="coerce").mean()),
            }
        )

    for sport, g in legs.groupby(legs["sport"].astype(str).str.upper()):
        _agg(g, f"{sport}|ALL", pd.Series(True, index=g.index))
        for outcome in ("HIT", "MISS"):
            _agg(g, f"{sport}|{outcome}", g["outcome"].eq(outcome))
        miss = g[g["outcome"] == "MISS"]
        if len(miss):
            _agg(miss, f"{sport}|MISS|near", miss["near_miss"].astype(bool))
        hit = g[g["outcome"] == "HIT"]
        if len(hit):
            _agg(hit, f"{sport}|HIT|strong_ml", pd.to_numeric(hit["ml_prob"], errors="coerce") >= 0.65)

    by_prop = (
        legs.groupby(["sport", "prop_type", "outcome"], dropna=False)
        .agg(n=("outcome", "size"), hit_rate=("outcome", lambda s: float((s == "HIT").mean())))
        .reset_index()
    )
    top_miss = by_prop[by_prop["outcome"] == "MISS"].sort_values("n", ascending=False).head(25)
    summary = pd.DataFrame(rows)
    summary.attrs["top_miss_props"] = top_miss
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Build miss/hit attribution report from graded workbooks.")
    ap.add_argument("--date", required=True, help="Slate date YYYY-MM-DD")
    ap.add_argument("--repo-root", default=str(ROOT))
    ap.add_argument("--out-dir", default="", help="Default: data/reports/miss_attribution")
    args = ap.parse_args()

    repo = Path(args.repo_root).resolve()
    date_str = str(args.date).strip()[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        raise SystemExit(f"Invalid --date: {args.date}")

    out_dir = Path(args.out_dir) if args.out_dir else repo / "data" / "reports" / "miss_attribution"
    out_dir.mkdir(parents=True, exist_ok=True)

    date_dir = repo / "outputs" / date_str
    paths = sorted(date_dir.glob(f"graded_*_{date_str}.xlsx")) if date_dir.is_dir() else []
    frames: list[pd.DataFrame] = []
    for p in paths:
        try:
            raw = _load_graded_sheet(p)
            sport = _sport_from_filename(p.name)
            part = _build_rows(raw, sport=sport, source=p.name, slate_date=date_str)
            if not part.empty:
                frames.append(part)
        except Exception as exc:
            print(f"[miss-attribution] skip {p.name}: {type(exc).__name__}: {exc}")

    legs = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    detail_csv = out_dir / f"miss_attribution_{date_str}.csv"
    summary_csv = out_dir / f"miss_attribution_{date_str}_summary.csv"
    json_out = out_dir / f"miss_attribution_{date_str}.json"

    if legs.empty:
        print(f"[miss-attribution] no HIT/MISS legs for {date_str}")
        legs.to_csv(detail_csv, index=False, encoding="utf-8-sig")
        return 0

    if legs["ml_prob"].notna().any():
        legs["ml_prob_decile"] = pd.qcut(
            legs["ml_prob"].fillna(legs["ml_prob"].median()),
            q=10,
            duplicates="drop",
            labels=False,
        )
    else:
        legs["ml_prob_decile"] = np.nan

    legs.to_csv(detail_csv, index=False, encoding="utf-8-sig")
    summary = _summarize(legs)
    top_miss = summary.attrs.get("top_miss_props", pd.DataFrame())
    summary.drop(columns=[], errors="ignore").to_csv(summary_csv, index=False, encoding="utf-8-sig")

    payload = {
        "date": date_str,
        "leg_rows": int(len(legs)),
        "hit_rate": float((legs["result"].astype(str).str.upper() == "HIT").mean()),
        "miss_near_line_pct": float(
            legs.loc[legs["result"].astype(str).str.upper() == "MISS", "near_miss"].mean()
        )
        if (legs["result"].astype(str).str.upper() == "MISS").any()
        else 0.0,
        "summary_slices": summary.to_dict(orient="records"),
        "top_miss_props": top_miss.to_dict(orient="records") if not top_miss.empty else [],
    }
    json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[miss-attribution] detail -> {detail_csv} ({len(legs)} legs)")
    print(f"[miss-attribution] summary -> {summary_csv}")
    print(f"[miss-attribution] json -> {json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
