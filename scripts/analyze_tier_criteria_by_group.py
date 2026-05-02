#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = REPO_ROOT / "outputs"

STEP8_PATHS: dict[str, Path] = {
    "NBA": REPO_ROOT / "Sports" / "NBA" / "data" / "outputs" / "step8_all_direction_clean.xlsx",
    "NBA1H": REPO_ROOT / "Sports" / "NBA" / "step8_nba1h_direction_clean.xlsx",
    "NBA1Q": REPO_ROOT / "Sports" / "NBA" / "step8_nba1q_direction_clean.xlsx",
}


@dataclass
class GroupSpec:
    pick_type: str
    direction: str
    feature: str
    higher_is_better: bool = True


GROUP_SPECS: list[GroupSpec] = [
    GroupSpec("GOBLIN", "OVER", "tier_distance_score", True),
    GroupSpec("DEMON", "OVER", "tier_distance_score", True),
    GroupSpec("STANDARD", "OVER", "effective_edge", True),
    GroupSpec("STANDARD", "UNDER", "effective_edge", True),
]


def _norm_pick_type(v: Any) -> str:
    s = str(v or "").strip().upper()
    if "GOBLIN" in s:
        return "GOBLIN"
    if "DEMON" in s:
        return "DEMON"
    return "STANDARD"


def _norm_direction(v: Any) -> str:
    s = str(v or "").strip().upper()
    if s in {"UNDER", "LOWER"}:
        return "UNDER"
    return "OVER"


def _norm_prop(v: Any) -> str:
    return str(v or "").strip().lower()


def _result_to_hit(v: Any) -> float | None:
    s = str(v or "").strip().upper()
    if s == "HIT":
        return 1.0
    if s == "MISS":
        return 0.0
    return None


def _pick_col(df: pd.DataFrame, names: list[str]) -> str | None:
    cols = {c.lower(): c for c in df.columns}
    for n in names:
        c = cols.get(n.lower())
        if c is not None:
            return c
    return None


def _load_graded_box_raw(path: Path, sport: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_excel(path, sheet_name="Box Raw")
    if df.empty:
        return df
    out = pd.DataFrame(index=df.index)
    out["sport"] = sport
    out["player"] = df[_pick_col(df, ["player"])].astype(str).str.strip()
    out["prop_type_norm"] = df[_pick_col(df, ["prop_type_norm", "prop type", "prop"])].apply(_norm_prop)
    out["pick_type"] = df[_pick_col(df, ["pick_type", "Pick Type"])].apply(_norm_pick_type)
    out["direction"] = df[_pick_col(df, ["bet_direction", "direction", "Direction"])].apply(_norm_direction)
    out["line"] = pd.to_numeric(df[_pick_col(df, ["line", "Line"])], errors="coerce")
    out["tier"] = df[_pick_col(df, ["tier", "Tier"])].astype(str).str.upper().str.strip()
    out["edge"] = pd.to_numeric(df[_pick_col(df, ["edge", "Edge"])], errors="coerce")
    out["result_hit"] = df[_pick_col(df, ["result", "Result"])].apply(_result_to_hit)
    out = out.dropna(subset=["result_hit", "line"])
    return out


def _load_step8(path: Path, sport: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_excel(path)
    if df.empty:
        return df
    out = pd.DataFrame(index=df.index)
    out["sport"] = sport
    out["player"] = df[_pick_col(df, ["Player", "player"])].astype(str).str.strip()
    out["prop_type_norm"] = df[_pick_col(df, ["Prop", "prop_type_norm", "prop"])].apply(_norm_prop)
    out["pick_type"] = df[_pick_col(df, ["Pick Type", "pick_type"])].apply(_norm_pick_type)
    out["direction"] = df[_pick_col(df, ["Direction", "direction"])].apply(_norm_direction)
    out["line"] = pd.to_numeric(df[_pick_col(df, ["Line", "line"])], errors="coerce")
    out["standard_line"] = pd.to_numeric(df[_pick_col(df, ["Standard Line", "standard_line"])], errors="coerce")
    out["hit_rate"] = pd.to_numeric(df[_pick_col(df, ["Hit Rate (5g)", "hit_rate"])], errors="coerce")
    return out.dropna(subset=["line"])


def _find_best_breakpoint(
    grp: pd.DataFrame,
    feature: str,
    *,
    higher_is_better: bool,
    min_n: int,
) -> dict[str, Any]:
    base_n = int(len(grp))
    base_hit = float(grp["result_hit"].mean()) if base_n else float("nan")
    s = pd.to_numeric(grp[feature], errors="coerce")
    valid = grp[s.notna()].copy()
    if valid.empty:
        return {"threshold": None, "n": 0, "hit_rate": np.nan, "lift": np.nan}

    values = pd.to_numeric(valid[feature], errors="coerce")
    qvals = values.quantile([0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80]).dropna().unique()
    best: dict[str, Any] | None = None
    for thr in sorted(qvals):
        if higher_is_better:
            cut = valid[values >= thr]
        else:
            cut = valid[values <= thr]
        n = int(len(cut))
        if n < min_n:
            continue
        hr = float(cut["result_hit"].mean())
        cand = {"threshold": float(thr), "n": n, "hit_rate": hr, "lift": hr - base_hit}
        if best is None or cand["lift"] > best["lift"] or (cand["lift"] == best["lift"] and cand["n"] > best["n"]):
            best = cand
    if best is None:
        return {"threshold": None, "n": 0, "hit_rate": np.nan, "lift": np.nan}
    return best


def analyze(date_str: str, min_n: int) -> pd.DataFrame:
    sports = ["NBA", "NBA1H", "NBA1Q"]
    graded_frames: list[pd.DataFrame] = []
    step8_frames: list[pd.DataFrame] = []

    for sport in sports:
        graded_path = OUTPUTS_DIR / date_str / f"graded_{sport.lower()}_{date_str}.xlsx"
        g = _load_graded_box_raw(graded_path, sport)
        if not g.empty:
            graded_frames.append(g)
        s = _load_step8(STEP8_PATHS[sport], sport)
        if not s.empty:
            step8_frames.append(s)

    if not graded_frames:
        raise FileNotFoundError(f"No graded Box Raw files found for date {date_str}")
    graded = pd.concat(graded_frames, ignore_index=True)
    step8 = pd.concat(step8_frames, ignore_index=True) if step8_frames else pd.DataFrame()

    if not step8.empty:
        merge_keys = ["sport", "player", "prop_type_norm", "pick_type", "direction", "line"]
        step8_dedup = step8.sort_values(["sport", "player"]).drop_duplicates(subset=merge_keys, keep="first")
        df = graded.merge(
            step8_dedup[merge_keys + ["standard_line", "hit_rate"]],
            on=merge_keys,
            how="left",
        )
    else:
        df = graded.copy()
        df["standard_line"] = np.nan
        df["hit_rate"] = np.nan

    df["goblin_distance"] = (pd.to_numeric(df["line"], errors="coerce") - pd.to_numeric(df["standard_line"], errors="coerce")).abs()
    distance_source = "line_vs_standard"
    if pd.to_numeric(df["goblin_distance"], errors="coerce").notna().sum() == 0:
        # Historical graded files do not always carry Standard Line snapshots.
        # Fall back to abs(edge) so group-wise tier analysis still runs.
        df["goblin_distance"] = pd.to_numeric(df["edge"], errors="coerce").abs()
        distance_source = "abs_edge_proxy"
    df["tier_distance_score"] = df.apply(
        lambda r: (
            r["goblin_distance"] if r["pick_type"] == "GOBLIN"
            else (-r["goblin_distance"] if r["pick_type"] == "DEMON" else 0.0)
        ),
        axis=1,
    )

    df["effective_edge"] = df.apply(
        lambda r: (-float(r["edge"]) if r["direction"] == "UNDER" else float(r["edge"])) if pd.notna(r["edge"]) else np.nan,
        axis=1,
    )
    hr01 = pd.to_numeric(df["hit_rate"], errors="coerce")
    hr01 = np.where((hr01 > 1.0) & (hr01 <= 100.0), hr01 / 100.0, hr01)
    df["hit_rate_01"] = hr01
    df["effective_hit_rate"] = np.where(df["direction"].eq("UNDER"), 1.0 - df["hit_rate_01"], df["hit_rate_01"])

    rows: list[dict[str, Any]] = []
    for spec in GROUP_SPECS:
        grp = df[(df["pick_type"] == spec.pick_type) & (df["direction"] == spec.direction)].copy()
        n = int(len(grp))
        if n == 0:
            rows.append(
                {
                    "date": date_str,
                    "group": f"{spec.pick_type} {spec.direction}",
                    "feature": spec.feature,
                    "n": 0,
                    "base_hit_rate": np.nan,
                    "corr_feature_vs_hit": np.nan,
                    "best_threshold": np.nan,
                    "best_n": 0,
                    "best_hit_rate": np.nan,
                    "lift_vs_base": np.nan,
                }
            )
            continue
        base_hr = float(grp["result_hit"].mean())
        feature_vals = pd.to_numeric(grp[spec.feature], errors="coerce")
        corr = float(feature_vals.corr(grp["result_hit"])) if feature_vals.notna().sum() >= 3 else np.nan
        best = _find_best_breakpoint(grp, spec.feature, higher_is_better=spec.higher_is_better, min_n=min_n)
        rows.append(
            {
                "date": date_str,
                "group": f"{spec.pick_type} {spec.direction}",
                "feature": spec.feature,
                "n": n,
                "base_hit_rate": base_hr,
                "corr_feature_vs_hit": corr,
                "best_threshold": best["threshold"],
                "best_n": best["n"],
                "best_hit_rate": best["hit_rate"],
                "lift_vs_base": best["lift"],
                "distance_source": distance_source,
            }
        )
    return pd.DataFrame(rows)


def _parse_date_token(s: str) -> pd.Timestamp:
    return pd.to_datetime(str(s).strip(), format="%Y-%m-%d", errors="raise")


def _discover_dates(from_date: str | None, to_date: str | None) -> list[str]:
    lo = _parse_date_token(from_date) if from_date else None
    hi = _parse_date_token(to_date) if to_date else None
    out: list[str] = []
    if not OUTPUTS_DIR.exists():
        return out
    for p in OUTPUTS_DIR.iterdir():
        if not p.is_dir():
            continue
        name = p.name
        try:
            d = _parse_date_token(name)
        except Exception:
            continue
        if lo is not None and d < lo:
            continue
        if hi is not None and d > hi:
            continue
        out.append(name)
    out.sort()
    return out


def _aggregate_across_dates(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    rows: list[dict[str, Any]] = []
    for group, grp in df.groupby("group", dropna=False):
        n_total = int(pd.to_numeric(grp["n"], errors="coerce").fillna(0).sum())
        if n_total <= 0:
            continue
        base_weighted = float(
            (
                pd.to_numeric(grp["base_hit_rate"], errors="coerce").fillna(0.0)
                * pd.to_numeric(grp["n"], errors="coerce").fillna(0.0)
            ).sum()
            / n_total
        )
        best_n_total = int(pd.to_numeric(grp["best_n"], errors="coerce").fillna(0).sum())
        best_weighted = (
            float(
                (
                    pd.to_numeric(grp["best_hit_rate"], errors="coerce").fillna(0.0)
                    * pd.to_numeric(grp["best_n"], errors="coerce").fillna(0.0)
                ).sum()
                / best_n_total
            )
            if best_n_total > 0
            else float("nan")
        )
        corr_vals = pd.to_numeric(grp["corr_feature_vs_hit"], errors="coerce")
        corr_med = float(corr_vals.median()) if corr_vals.notna().any() else float("nan")
        thr_vals = pd.to_numeric(grp["best_threshold"], errors="coerce")
        thr_med = float(thr_vals.median()) if thr_vals.notna().any() else float("nan")
        rows.append(
            {
                "group": group,
                "feature": str(grp["feature"].iloc[0]),
                "dates": int(grp["date"].nunique()),
                "n_total": n_total,
                "base_hit_rate_weighted": base_weighted,
                "best_threshold_median": thr_med,
                "best_n_total": best_n_total,
                "best_hit_rate_weighted": best_weighted,
                "lift_vs_base_weighted": (best_weighted - base_weighted) if pd.notna(best_weighted) else float("nan"),
                "corr_feature_vs_hit_median": corr_med,
            }
        )
    return pd.DataFrame(rows).sort_values(["group"]).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyze tier criteria by pick-type + direction group.")
    ap.add_argument("--date", default=pd.Timestamp.now().strftime("%Y-%m-%d"), help="Date in YYYY-MM-DD")
    ap.add_argument("--all-dates", action="store_true", help="Analyze all dates under outputs/YYYY-MM-DD")
    ap.add_argument("--from", dest="from_date", default="", help="Start date inclusive (YYYY-MM-DD)")
    ap.add_argument("--to", dest="to_date", default="", help="End date inclusive (YYYY-MM-DD)")
    ap.add_argument("--group", default="", help='Optional group filter, e.g. "STANDARD UNDER"')
    ap.add_argument("--by-date", action="store_true", help="Print/report per-date rows (optionally filtered by --group)")
    ap.add_argument("--min-n-per-day", type=int, default=0, help="Optional minimum per-date group sample size (n)")
    ap.add_argument("--min-n", type=int, default=25, help="Minimum sample size for breakpoint candidate")
    ap.add_argument("--output", default="", help="Optional output JSON path")
    args = ap.parse_args()

    if args.all_dates or args.from_date or args.to_date:
        dates = _discover_dates(args.from_date or None, args.to_date or None)
    else:
        dates = [str(args.date)]

    all_rows: list[pd.DataFrame] = []
    for d in dates:
        try:
            r = analyze(d, args.min_n)
            if not r.empty:
                all_rows.append(r)
        except FileNotFoundError:
            continue

    report = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()
    if args.group and not report.empty:
        group_norm = str(args.group).strip().upper()
        report = report[report["group"].astype(str).str.upper() == group_norm].copy()
    if args.min_n_per_day and not report.empty:
        n_s = pd.to_numeric(report["n"], errors="coerce").fillna(0)
        report = report[n_s >= int(args.min_n_per_day)].copy()
    agg = _aggregate_across_dates(report) if not report.empty else pd.DataFrame()

    pd.options.display.width = 240
    pd.options.display.max_columns = 24
    if args.by_date:
        print(
            f"Tier criteria by-date rows={len(report)} "
            f"group={args.group or 'ALL'} min_n={args.min_n}"
        )
        if not report.empty:
            by_date = report.sort_values(["date", "group"]).reset_index(drop=True)
            print(by_date.to_string(index=False))
        else:
            print("No matching rows found for requested by-date view.")
    elif args.all_dates or args.from_date or args.to_date:
        print(
            f"Tier criteria analysis dates={len(dates)} considered, "
            f"rows={len(report)} min_n={args.min_n}"
        )
        if not agg.empty:
            print(agg.to_string(index=False))
        else:
            print("No analyzable rows found in selected date range.")
    else:
        print(f"Tier criteria analysis date={args.date} min_n={args.min_n}")
        print(report.to_string(index=False))

    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = REPO_ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "params": {
                "date": args.date,
                "all_dates": bool(args.all_dates),
                "from": args.from_date or None,
                "to": args.to_date or None,
                "min_n": int(args.min_n),
                "dates_considered": dates,
                "group": args.group or None,
                "by_date": bool(args.by_date),
                "min_n_per_day": int(args.min_n_per_day),
            },
            "aggregate": agg.to_dict(orient="records") if not agg.empty else [],
            "per_date_rows": report.to_dict(orient="records") if not report.empty else [],
        }
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Saved -> {out_path}")


if __name__ == "__main__":
    main()

