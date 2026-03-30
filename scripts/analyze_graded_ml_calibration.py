#!/usr/bin/env python3
"""Aggregate graded Box Raw + combined LEG_RESULTS; measure calibration vs model-like fields."""
from __future__ import annotations

import argparse
import warnings

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from pathlib import Path
from scipy import stats


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _is_demon_series(pick_type: pd.Series) -> pd.Series:
    s = pick_type.astype(str).str.lower()
    return s.str.contains("demon", na=False)


def load_box_raw_paths(root: Path) -> list[tuple[Path, str | None]]:
    """One workbook per (folder, graded stem): prefer *_mlbackfill.xlsx when both exist."""
    rows: list[tuple[Path, str | None]] = []
    for p in sorted((root / "outputs").rglob("*.xlsx")):
        ln = p.name.lower()
        if "combined_tickets_graded" in ln or "graded" not in ln:
            continue
        try:
            xl = pd.ExcelFile(p)
        except Exception:
            continue
        if "Box Raw" not in xl.sheet_names:
            continue
        n = p.name.lower()
        sport: str | None = None
        if "nba1q" in n:
            sport = "nba1q"
        elif "nba1h" in n:
            sport = "nba1h"
        elif "wnba" in n:
            sport = "wnba"
        elif "nba" in n:
            sport = "nba"
        elif "wcbb" in n:
            sport = "wcbb"
        elif "cbb" in n:
            sport = "cbb"
        elif "nhl" in n:
            sport = "nhl"
        elif "soccer" in n:
            sport = "soccer"
        elif "mlb" in n:
            sport = "mlb"
        rows.append((p, sport))

    best: dict[tuple[object, str], tuple[Path, str | None]] = {}
    for p, sport in rows:
        stem_l = p.stem.lower()
        base = stem_l[: -len("_mlbackfill")] if stem_l.endswith("_mlbackfill") else stem_l
        key = (p.parent.resolve(), base)
        prev = best.get(key)
        if prev is None:
            best[key] = (p, sport)
            continue
        p0, _ = prev
        if "_mlbackfill" in p.name.lower() and "_mlbackfill" not in p0.name.lower():
            best[key] = (p, sport)
    return list(best.values())


def load_combined_legs(root: Path) -> list[pd.DataFrame]:
    dfs: list[pd.DataFrame] = []
    seen: set[str] = set()
    for p in sorted((root / "outputs").rglob("combined_tickets_graded_*.xlsx")):
        r = str(p.resolve())
        if r in seen:
            continue
        seen.add(r)
        try:
            xl = pd.ExcelFile(p)
        except Exception:
            continue
        if "LEG_RESULTS" not in xl.sheet_names:
            continue
        df = pd.read_excel(p, sheet_name="LEG_RESULTS")
        if len(df) == 0:
            continue
        df["_file"] = p.name
        dfs.append(df)
    return dfs


def num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def load_box_raw_decided(root: Path) -> pd.DataFrame:
    paths = load_box_raw_paths(root)
    chunks: list[pd.DataFrame] = []
    for p, sp in paths:
        df = pd.read_excel(p, sheet_name="Box Raw")
        if len(df) == 0:
            continue
        df["_sport"] = sp
        df["_source"] = "box_raw"
        chunks.append(df)
    box = pd.concat(chunks, ignore_index=True)
    box["result_u"] = box["result"].astype(str).str.strip().str.upper()
    box["is_hit"] = np.where(
        box["result_u"] == "HIT",
        1.0,
        np.where(box["result_u"] == "MISS", 0.0, np.nan),
    )
    decided = box[box["is_hit"].notna()].copy()
    if "pick_type" in decided.columns:
        decided["is_demon"] = _is_demon_series(decided["pick_type"])
    else:
        decided["is_demon"] = False
    for c in ["edge", "rank_score", "projection", "abs_edge", "line", "margin"]:
        if c in decided.columns:
            decided[c] = num(decided[c])
    if "projection" in decided.columns and "line" in decided.columns:
        decided["proj_minus_line"] = decided["projection"] - decided["line"]
    if "edge" in decided.columns:
        decided["abs_edge"] = decided["edge"].abs()
    if "ml_prob" not in decided.columns:
        for alt in ("ML Prob",):
            if alt in decided.columns:
                decided["ml_prob"] = num(decided[alt])
                break
    if "ml_prob" in decided.columns:
        decided["ml_prob"] = num(decided["ml_prob"])
    if "ml_edge" not in decided.columns and "ml_prob" in decided.columns:
        decided["ml_edge"] = decided["ml_prob"] - 0.5
    elif "ml_edge" in decided.columns:
        decided["ml_edge"] = num(decided["ml_edge"])
    for src, dst in (("Edge Score", "edge_score"), ("Blended Score", "blended_score")):
        if dst not in decided.columns and src in decided.columns:
            decided[dst] = num(decided[src])
    for c in ("edge_score", "blended_score"):
        if c in decided.columns:
            decided[c] = num(decided[c])
    return decided


def point_biserial(y: np.ndarray, x: np.ndarray, min_n: int) -> tuple[float, float, int] | None:
    m = np.isfinite(x) & np.isfinite(y)
    n = int(m.sum())
    if n < min_n:
        return None
    rpb, p = stats.pointbiserialr(y[m], x[m])
    return float(rpb), float(p), n


def print_feature_correlations(decided: pd.DataFrame, title: str, min_n: int) -> None:
    y = decided["is_hit"].values
    # margin is outcome-derived; list separately
    predictors = [
        c
        for c in [
            "edge",
            "rank_score",
            "projection",
            "abs_edge",
            "proj_minus_line",
            "ml_prob",
            "ml_edge",
            "edge_score",
            "blended_score",
            "line",
        ]
        if c in decided.columns
    ]
    print(f"\n--- Point-biserial r vs hit ({title}) ---")
    rows: list[tuple[str, float, float, int]] = []
    for c in predictors:
        pb = point_biserial(y, decided[c].values, min_n)
        if pb:
            rows.append((c, pb[0], pb[1], pb[2]))
    rows.sort(key=lambda t: abs(t[1]), reverse=True)
    for c, rpb, p, n in rows:
        print(f"  {c:16s} r_pb={rpb:+.4f}  p={p:.2e}  n={n}")
    if "margin" in decided.columns:
        pb = point_biserial(y, num(decided["margin"]).values, min_n)
        if pb:
            c, rpb, p, n = "margin", pb[0], pb[1], pb[2]
            print(f"  {c:16s} r_pb={rpb:+.4f}  p={p:.2e}  n={n}  (post-hoc: actual vs line)")


def print_edge_by_direction(decided: pd.DataFrame, min_n: int) -> None:
    if "bet_direction" not in decided.columns or "edge" not in decided.columns:
        return
    print("\n--- Edge vs hit by bet_direction ---")
    for d in ("OVER", "UNDER"):
        sub = decided[decided["bet_direction"].astype(str).str.upper().str.strip() == d]
        if len(sub) < min_n:
            continue
        y2 = sub["is_hit"].values
        for label, col in (
            ("edge", "edge"),
            ("|edge|", "abs_edge"),
            ("rank_score", "rank_score"),
        ):
            if col not in sub.columns:
                continue
            pb = point_biserial(y2, sub[col].values, min_n)
            if pb:
                print(f"  {d:5s} {label:12s} r_pb={pb[0]:+.4f} p={pb[1]:.2e} n={pb[2]}")


def print_quintiles(
    decided: pd.DataFrame,
    col: str,
    label: str,
    q: int,
    min_n: int,
) -> None:
    if col not in decided.columns:
        return
    sub = decided[np.isfinite(decided[col])].copy()
    if len(sub) < min_n:
        return
    try:
        sub["bin"] = pd.qcut(sub[col], q=q, duplicates="drop")
    except ValueError as e:
        print(f"{label} quintiles skipped: {e}")
        return
    g = sub.groupby("bin", observed=True)["is_hit"].agg(["mean", "count"])
    g = g[g["count"] >= max(1, min_n // 20)]
    print(f"\nHit rate by {label} ({q}-bin quantiles, {len(sub)} rows):")
    print(g.to_string())


def print_edge_direction_buckets(decided: pd.DataFrame, min_n: int) -> None:
    if "edge" not in decided.columns or "bet_direction" not in decided.columns:
        return
    sub = decided[np.isfinite(decided["edge"])].copy()
    print("\n--- Hit rate: edge quintile x bet_direction ---")
    for d in ("OVER", "UNDER"):
        s = sub[sub["bet_direction"].astype(str).str.upper().str.strip() == d]
        if len(s) < min_n:
            continue
        try:
            s = s.copy()
            s["eq"] = pd.qcut(s["edge"], q=5, duplicates="drop")
        except ValueError:
            continue
        g = s.groupby("eq", observed=True)["is_hit"].agg(["mean", "count"])
        print(f"\n  {d} (n={len(s)}):")
        print(g.to_string())


def print_demon_split(decided: pd.DataFrame) -> None:
    if "is_demon" not in decided.columns:
        return
    print("\n--- Demon vs non-Demon (Box Raw decided) ---")
    g = decided.groupby(decided["is_demon"].map({True: "demon", False: "non-demon"}))["is_hit"].agg(
        ["mean", "count"]
    )
    print(g.to_string())


def print_sport_pick_matrix(decided: pd.DataFrame) -> None:
    if "_sport" not in decided.columns or "pick_type" not in decided.columns:
        return
    print("\n--- Hit rate: sport x demon (volume) ---")
    decided = decided.copy()
    decided["_dlabel"] = np.where(decided["is_demon"], "demon", "non-demon")
    g = decided.groupby(["_sport", "_dlabel"], dropna=False)["is_hit"].agg(["mean", "count"])
    print(g.sort_values("count", ascending=False).to_string())


def analyze_nba_backtest_csv(root: Path, min_n: int) -> None:
    path = root / "NBA" / "data" / "outputs" / "backtest_nba_graded_rows.csv"
    if not path.is_file():
        print(f"\n(No NBA backtest CSV at {path})")
        return
    df = pd.read_csv(path, low_memory=False)
    if "hit" not in df.columns or "ml_prob" not in df.columns:
        print("\n(NBA backtest CSV missing hit / ml_prob)")
        return
    df["hit"] = num(df["hit"])
    df["ml_prob"] = num(df["ml_prob"])
    d = df[df["hit"].isin([0.0, 1.0]) & df["ml_prob"].notna()].copy()
    if len(d) < 5:
        print(f"\nNBA backtest: only {len(d)} rows with hit+ml_prob (need >= 5)")
        return
    y = d["hit"].values
    x = d["ml_prob"].values
    m = np.isfinite(x) & np.isfinite(y)
    rpb, p = stats.pointbiserialr(y[m], x[m])
    brier = float(np.mean((x[m] - y[m]) ** 2))
    print(f"\n=== NBA backtest graded rows ({path.name}): n={m.sum()} ===")
    if int(m.sum()) < min_n:
        print(f"(note: n < --min-n {min_n}; treat correlation / bins as exploratory)")
    print(f"ml_prob vs hit: r_pb={rpb:+.4f}  p={p:.2e}")
    print(f"Brier score (ml_prob vs binary hit): {brier:.4f}")
    try:
        d2 = d.loc[m].copy()
        d2["pq"] = pd.qcut(d2["ml_prob"], q=5, duplicates="drop")
        cal = d2.groupby("pq", observed=True).agg(pred=("ml_prob", "mean"), actual=("hit", "mean"), n=("hit", "size"))
        cal["gap"] = cal["actual"] - cal["pred"]
        print("\nReliability (quintiles): pred = mean ml_prob, actual = hit rate")
        print(cal.to_string())
    except ValueError as e:
        print("ml_prob reliability bins skipped:", e)


def print_box_ml_prob_calibration(decided: pd.DataFrame, min_n: int) -> None:
    if "ml_prob" not in decided.columns:
        return
    d = decided.copy()
    d["ml_prob"] = num(d["ml_prob"])
    msk = d["ml_prob"].notna() & d["is_hit"].notna()
    d = d.loc[msk]
    if len(d) < 5:
        return
    y = d["is_hit"].values
    x = d["ml_prob"].values
    finite = np.isfinite(x) & np.isfinite(y)
    if int(finite.sum()) < 5:
        return
    rpb, p = stats.pointbiserialr(y[finite], x[finite])
    brier = float(np.mean((x[finite] - y[finite]) ** 2))
    print(f"\n=== Box Raw ml_prob calibration: n={int(finite.sum())} rows with ml_prob ===")
    if int(finite.sum()) < min_n:
        print(f"(note: n < --min-n {min_n}; exploratory)")
    print(f"ml_prob vs hit: r_pb={rpb:+.4f}  p={p:.2e}")
    print(f"Brier score: {brier:.4f}")
    try:
        d2 = d.loc[finite].copy()
        d2["pq"] = pd.qcut(d2["ml_prob"], q=5, duplicates="drop")
        cal = d2.groupby("pq", observed=True).agg(
            pred=("ml_prob", "mean"),
            actual=("is_hit", "mean"),
            n=("is_hit", "size"),
        )
        cal["gap"] = cal["actual"] - cal["pred"]
        print("\nReliability (quintiles): pred = mean ml_prob, actual = hit rate")
        print(cal.to_string())
    except ValueError as e:
        print("ml_prob reliability bins skipped:", e)


def print_under_favorable_margin(decided: pd.DataFrame, min_n: int) -> None:
    """UNDER-only: line - projection ('cushion') vs hit — signed distance favorable to under."""
    sub = decided[decided["bet_direction"].astype(str).str.upper().str.strip() == "UNDER"].copy()
    if len(sub) < min_n or "line" not in sub.columns or "projection" not in sub.columns:
        return
    sub["line"] = num(sub["line"])
    sub["projection"] = num(sub["projection"])
    sub["under_cushion"] = sub["line"] - sub["projection"]
    m = sub["under_cushion"].notna() & sub["is_hit"].notna()
    if int(m.sum()) < min_n:
        return
    y = sub.loc[m, "is_hit"].values
    x = sub.loc[m, "under_cushion"].values
    rpb, p = stats.pointbiserialr(y, x)
    print(f"\n--- UNDER only: (line - projection) vs hit, n={len(y)} ---")
    print(f"  r_pb={rpb:+.4f}  p={p:.2e}  (expect + if higher line-minus-proj => more UNDER hits)")
    try:
        b = sub.loc[m].copy()
        b["bin"] = pd.qcut(b["under_cushion"], q=5, duplicates="drop")
        g = b.groupby("bin", observed=True)["is_hit"].agg(["mean", "count"])
        print("\nHit rate by under_cushion = (line - projection) quintile:")
        print(g.to_string())
    except ValueError as e:
        print("under_cushion quintiles skipped:", e)


def main() -> None:
    ap = argparse.ArgumentParser(description="Graded props vs model-field calibration audit.")
    ap.add_argument(
        "--exclude-demon",
        action="store_true",
        help="Restrict Box Raw analysis to non-Demon pick_type rows.",
    )
    ap.add_argument(
        "--demon-only",
        action="store_true",
        help="Restrict Box Raw analysis to Demon pick_type rows.",
    )
    ap.add_argument(
        "--under-only",
        action="store_true",
        help="Restrict Box Raw analysis to bet_direction UNDER.",
    )
    ap.add_argument("--min-n", type=int, default=80, help="Minimum rows for correlations / buckets.")
    args = ap.parse_args()

    root = _repo_root()
    decided = load_box_raw_decided(root)

    if args.exclude_demon and args.demon_only:
        print("Use only one of --exclude-demon / --demon-only")
        return
    if args.exclude_demon:
        decided = decided[~decided["is_demon"]].copy()
        filt = "non-Demon only"
    elif args.demon_only:
        decided = decided[decided["is_demon"]].copy()
        filt = "Demon only"
    else:
        filt = "all pick types"

    if args.under_only:
        if "bet_direction" not in decided.columns:
            print("ERROR: --under-only requires bet_direction on Box Raw rows.")
            return
        decided = decided[decided["bet_direction"].astype(str).str.upper().str.strip() == "UNDER"].copy()
        filt = f"{filt} | UNDER only"

    print("=== BOX RAW decided legs:", len(decided), f"| filter: {filt}")
    if len(decided) == 0:
        return
    print("baseline hit rate:", round(float(decided["is_hit"].mean()), 4))

    print("\nBy sport (filename hint):")
    by_sp = decided.groupby("_sport", dropna=False)["is_hit"].agg(["mean", "count"])
    print(by_sp.sort_values("count", ascending=False).to_string())

    print_demon_split(decided)
    print_sport_pick_matrix(decided)

    print_feature_correlations(decided, filt, args.min_n)
    print_box_ml_prob_calibration(decided, args.min_n)
    print_under_favorable_margin(decided, args.min_n)
    print_edge_by_direction(decided, args.min_n)
    print_quintiles(decided, "edge", "edge", 5, args.min_n)
    print_quintiles(decided, "rank_score", "rank_score", 5, args.min_n)
    if "proj_minus_line" in decided.columns:
        print_quintiles(decided, "proj_minus_line", "projection minus line", 5, args.min_n)

    print_edge_direction_buckets(decided, args.min_n)

    for tcol in ("tier", "def_tier", "minutes_tier", "pick_type"):
        if tcol not in decided.columns or decided[tcol].notna().sum() < args.min_n:
            continue
        g = (
            decided.groupby(tcol, dropna=False)["is_hit"]
            .agg(["mean", "count"])
            .sort_values("count", ascending=False)
            .head(15)
        )
        print(f"\n{tcol} (top 15 by volume):")
        print(g.to_string())

    analyze_nba_backtest_csv(root, args.min_n)

    clegs = load_combined_legs(root)
    if clegs:
        cdf = pd.concat(clegs, ignore_index=True)
        lr = cdf["leg_result"].astype(str).str.strip().str.upper()
        cdf["is_hit"] = np.where(lr == "HIT", 1.0, np.where(lr == "MISS", 0.0, np.nan))
        cdec = cdf[cdf["is_hit"].notna()].copy()
        print("\n=== COMBINED LEG_RESULTS HIT+MISS only:", len(cdec))
        if "ml_prob" in cdec.columns:
            cdec["ml_prob"] = num(cdec["ml_prob"])
            pb = point_biserial(cdec["is_hit"].values, cdec["ml_prob"].values, args.min_n)
            if pb:
                print(f"ml_prob vs hit: r_pb={pb[0]:+.4f} p={pb[1]:.2e} n={pb[2]}")
                try:
                    cdec["mq"] = pd.qcut(cdec["ml_prob"], q=5, duplicates="drop")
                    print("Win rate by ml_prob quintile:")
                    print(cdec.groupby("mq", observed=True)["is_hit"].agg(["mean", "count"]).to_string())
                except ValueError as e:
                    print("ml_prob quintiles skipped:", e)
        else:
            print("(no ml_prob column in combined LEG_RESULTS exports)")
        if "sport" in cdec.columns:
            print("\nCombined legs by sport column:")
            print(
                cdec.groupby(cdec["sport"].fillna("(blank)"))["is_hit"]
                .agg(["mean", "count"])
                .sort_values("count", ascending=False)
                .head(20)
                .to_string()
            )


if __name__ == "__main__":
    main()
