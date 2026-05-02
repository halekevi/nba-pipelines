#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
OUTPUTS_DIR = REPO_ROOT / "outputs"


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


def _load_step8_nba_family(today: str) -> pd.DataFrame:
    candidates: dict[str, list[Path]] = {
        "NBA": [
            OUTPUTS_DIR / today / f"step8_nba_direction_clean_{today}.xlsx",
            REPO_ROOT / "NBA" / "data" / "outputs" / "step8_all_direction_clean.xlsx",
        ],
        "NBA1H": [
            OUTPUTS_DIR / today / f"step8_nba1h_direction_clean_{today}.xlsx",
            REPO_ROOT / "NBA" / "step8_nba1h_direction_clean.xlsx",
        ],
        "NBA1Q": [
            OUTPUTS_DIR / today / f"step8_nba1q_direction_clean_{today}.xlsx",
            REPO_ROOT / "NBA" / "step8_nba1q_direction_clean.xlsx",
        ],
    }
    rename_map = {
        "Pick Type": "pick_type",
        "Direction": "direction",
        "Hit Rate (5g)": "hit_rate",
        "L5 Over": "l5_over",
        "L5 Under": "l5_under",
        "Edge": "edge",
        "Tier": "tier",
        "Prop": "prop_type",
        "Rank Score": "rank_score",
        "Void Reason": "void_reason",
        "ML Prob": "ml_prob",
    }
    frames: list[pd.DataFrame] = []
    for sport, paths in candidates.items():
        src = next((p for p in paths if p.exists()), None)
        if src is None:
            continue
        df = pd.read_excel(src)
        if df.empty:
            continue
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        for col in [
            "pick_type",
            "direction",
            "hit_rate",
            "l5_over",
            "l5_under",
            "edge",
            "tier",
            "prop_type",
            "rank_score",
            "void_reason",
            "ml_prob",
        ]:
            if col not in df.columns:
                df[col] = pd.NA
        df["sport"] = sport
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _counts_by_group(df: pd.DataFrame) -> dict[str, int]:
    if df is None or df.empty:
        return {}
    pt = df["pick_type"].apply(_norm_pick_type)
    dr = df["direction"].apply(_norm_direction)
    out: dict[str, int] = {}
    for (p, d), n in (pd.DataFrame({"p": pt, "d": dr}).value_counts().items()):
        out[f"{p}_{d}"] = int(n)
    return dict(sorted(out.items()))


def _counts_by_sport_and_group(df: pd.DataFrame) -> dict[str, dict[str, int]]:
    if df is None or df.empty:
        return {}
    out: dict[str, dict[str, int]] = {}
    for sport, grp in df.groupby(df["sport"].astype(str).str.upper().str.strip(), dropna=False):
        out[str(sport)] = _counts_by_group(grp)
    return out


def build_current_population_snapshot(today: str) -> dict[str, Any]:
    import sys

    sys.path.insert(0, str(SCRIPTS_DIR))
    import combined_slate_tickets as c  # type: ignore

    pool = _load_step8_nba_family(today)
    if pool.empty:
        return {"error": "no_step8_pool_found"}

    base_kwargs = dict(
        min_hit_rate=0.55,
        min_edge=0.0,
        min_rank=None,
        tiers=["A", "B", "C", "D"],
        pick_types=["Goblin", "Standard"],
        allow_strong_l5_bypass=True,
        min_prop_quality=-1.0,
        discard_sport="NBAFAMILY",
    )

    original_thresholds = copy.deepcopy(c.DIRECTIONAL_HR_THRESHOLDS)

    old_thresholds = copy.deepcopy(original_thresholds)
    for sp in ("NBA", "NBA1H", "NBA1Q"):
        old_thresholds.setdefault(sp, {})["standard_over_min_edge"] = 0.80
        old_thresholds[sp].pop("standard_under_min_edge", None)

    new_thresholds = copy.deepcopy(original_thresholds)
    for sp in ("NBA", "NBA1H", "NBA1Q"):
        new_thresholds.setdefault(sp, {})["standard_over_min_edge"] = 2.45
        new_thresholds[sp]["standard_under_min_edge"] = 1.33

    c.DIRECTIONAL_HR_THRESHOLDS = old_thresholds
    old_discard = c.DiscardTracker()
    old_funnel = c.FunnelTracker()
    old_out = c.filter_eligible(pool.copy(), discard_tracker=old_discard, funnel_tracker=old_funnel, **base_kwargs)

    c.DIRECTIONAL_HR_THRESHOLDS = new_thresholds
    new_discard = c.DiscardTracker()
    new_funnel = c.FunnelTracker()
    new_out = c.filter_eligible(pool.copy(), discard_tracker=new_discard, funnel_tracker=new_funnel, **base_kwargs)
    c.DIRECTIONAL_HR_THRESHOLDS = original_thresholds

    return {
        "input_rows": int(len(pool)),
        "old": {
            "eligible_rows": int(len(old_out)),
            "counts_by_group": _counts_by_group(old_out),
            "counts_by_sport_group": _counts_by_sport_and_group(old_out),
            "discard_reasons": old_discard.to_dict(),
            "funnel": old_funnel.to_dict(),
        },
        "new": {
            "eligible_rows": int(len(new_out)),
            "counts_by_group": _counts_by_group(new_out),
            "counts_by_sport_group": _counts_by_sport_and_group(new_out),
            "discard_reasons": new_discard.to_dict(),
            "funnel": new_funnel.to_dict(),
        },
    }


def _load_graded_box_raw(path: Path, sport: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_excel(path, sheet_name="Box Raw")
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
    cols = {c.lower(): c for c in df.columns}

    def get_col(*names: str) -> str | None:
        for n in names:
            c = cols.get(n.lower())
            if c is not None:
                return c
        return None

    c_pick = get_col("pick_type", "Pick Type")
    c_dir = get_col("bet_direction", "direction", "Direction")
    c_edge = get_col("edge", "Edge")
    c_res = get_col("result", "Result")
    if not all([c_pick, c_dir, c_edge, c_res]):
        return pd.DataFrame()
    out = pd.DataFrame(index=df.index)
    out["sport"] = sport
    out["pick_type"] = df[c_pick].apply(_norm_pick_type)
    out["direction"] = df[c_dir].apply(_norm_direction)
    out["edge"] = pd.to_numeric(df[c_edge], errors="coerce")
    out["result"] = df[c_res].astype(str).str.upper().str.strip()
    out = out[out["result"].isin(["HIT", "MISS"])].copy()
    out["hit"] = (out["result"] == "HIT").astype(int)
    return out.dropna(subset=["edge"])


def _discover_dates(start_date: str, end_date: str) -> list[str]:
    lo = pd.to_datetime(start_date).date()
    hi = pd.to_datetime(end_date).date()
    out: list[str] = []
    if not OUTPUTS_DIR.exists():
        return out
    for p in OUTPUTS_DIR.iterdir():
        if not p.is_dir():
            continue
        try:
            d = pd.to_datetime(p.name).date()
        except Exception:
            continue
        if lo <= d <= hi:
            out.append(p.name)
    return sorted(out)


def build_historical_backtest(start_date: str, end_date: str) -> dict[str, Any]:
    sports = ["NBA", "NBA1H", "NBA1Q"]
    rows: list[dict[str, Any]] = []
    for ds in _discover_dates(start_date, end_date):
        day_frames: list[pd.DataFrame] = []
        for sp in sports:
            p = OUTPUTS_DIR / ds / f"graded_{sp.lower()}_{ds}.xlsx"
            g = _load_graded_box_raw(p, sp)
            if not g.empty:
                day_frames.append(g)
        if not day_frames:
            continue
        day = pd.concat(day_frames, ignore_index=True)
        day = day[day["pick_type"].eq("STANDARD")].copy()
        if day.empty:
            continue
        is_over = day["direction"].eq("OVER")
        is_under = day["direction"].eq("UNDER")
        effective_edge = pd.Series(day["edge"], index=day.index)
        effective_edge.loc[is_under] = -effective_edge.loc[is_under]

        old_pass = (~is_over) | (day["edge"] >= 0.80)
        new_pass = ((~is_over) | (day["edge"] >= 2.45)) & ((~is_under) | (effective_edge >= 1.33))

        def hr(mask: pd.Series) -> float | None:
            sel = day[mask]
            if sel.empty:
                return None
            return float(sel["hit"].mean())

        rows.append(
            {
                "date": ds,
                "n_standard": int(len(day)),
                "n_old_pass": int(old_pass.sum()),
                "n_new_pass": int(new_pass.sum()),
                "hr_all_standard": hr(pd.Series([True] * len(day), index=day.index)),
                "hr_old_pass": hr(old_pass),
                "hr_new_pass": hr(new_pass),
            }
        )

    if not rows:
        return {"per_date": [], "aggregate": {}}

    rdf = pd.DataFrame(rows)
    n_old = int(rdf["n_old_pass"].sum())
    n_new = int(rdf["n_new_pass"].sum())
    weighted_old = float((rdf["hr_old_pass"] * rdf["n_old_pass"]).sum() / n_old) if n_old > 0 else None
    weighted_new = float((rdf["hr_new_pass"] * rdf["n_new_pass"]).sum() / n_new) if n_new > 0 else None
    return {
        "per_date": rows,
        "aggregate": {
            "dates": int(len(rdf)),
            "n_old_pass_total": n_old,
            "n_new_pass_total": n_new,
            "weighted_hr_old_pass": weighted_old,
            "weighted_hr_new_pass": weighted_new,
            "weighted_hr_delta": (weighted_new - weighted_old) if (weighted_new is not None and weighted_old is not None) else None,
            "net_population_change": int(n_new - n_old),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build prop population state report (current + historical backtest).")
    ap.add_argument("--date", required=True, help="Run date YYYY-MM-DD for current population snapshot")
    ap.add_argument("--backtest-from", default="", help="Historical backtest start date YYYY-MM-DD")
    ap.add_argument("--backtest-to", default="", help="Historical backtest end date YYYY-MM-DD")
    ap.add_argument("--out-dir", default="", help="Output directory (default: outputs/<date>)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else (OUTPUTS_DIR / args.date)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "date": args.date,
        "current_population": build_current_population_snapshot(args.date),
    }
    if args.backtest_from and args.backtest_to:
        payload["historical_backtest"] = build_historical_backtest(args.backtest_from, args.backtest_to)

    out_path = out_dir / f"prop_population_state_report_{args.date}.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved -> {out_path}")


if __name__ == "__main__":
    main()

