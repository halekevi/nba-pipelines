#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def _normalize_colname(name: str) -> str:
    return (
        str(name)
        .strip()
        .lower()
        .replace("%", "pct")
        .replace("/", "_")
        .replace("-", "_")
        .replace(" ", "_")
    )


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [_normalize_colname(c) for c in out.columns]
    return out


def _ensure_required_columns(df: pd.DataFrame, is_actuals: bool = False) -> pd.DataFrame:
    out = _normalize_columns(df)
    alias_map = {
        "player": ["player_name", "name"],
        "team": ["team_abbr", "teamcode", "team_code"],
        "prop_type": ["market", "stat_type", "bet_type", "prop", "pick_type"],
        "line": ["prop_line", "line_value", "target_line"],
        "actual": ["value", "result_value", "actual_value"],
    }
    required = ["player", "team", "prop_type", "actual"] if is_actuals else ["player", "team", "prop_type", "line"]

    for canonical in required:
        if canonical in out.columns:
            continue
        for alias in alias_map.get(canonical, []):
            if alias in out.columns:
                out[canonical] = out[alias]
                break

    return out


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _resolve_direction(df: pd.DataFrame) -> pd.Series:
    if "final_bet_direction" in df.columns:
        d = df["final_bet_direction"].astype(str).str.upper().str.strip()
    elif "bet_direction" in df.columns:
        d = df["bet_direction"].astype(str).str.upper().str.strip()
    else:
        d = pd.Series("OVER", index=df.index)
    return d


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path, sheet_name=0, dtype=str).fillna("")
    return pd.read_csv(path, dtype=str).fillna("")


def _grade_hits(df: pd.DataFrame) -> pd.Series:
    direction = _resolve_direction(df)
    over_hit = (direction == "OVER") & (df["actual"] > df["line"])
    under_hit = (direction == "UNDER") & (df["actual"] < df["line"])
    return (over_hit | under_hit).astype(int)


def _top_metrics(df: pd.DataFrame, score_col: str) -> pd.DataFrame:
    pct_buckets = [0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50]
    rows: list[dict] = []
    ranked = df.sort_values(score_col, ascending=False).reset_index(drop=True)
    n = len(ranked)
    for p in pct_buckets:
        k = max(1, int(round(n * p)))
        sub = ranked.head(k)
        rows.append(
            {
                "score_col": score_col,
                "top_pct": p,
                "rows": len(sub),
                "hit_rate": float(sub["hit"].mean()) if len(sub) else np.nan,
            }
        )
    return pd.DataFrame(rows)


def _summary_block(df: pd.DataFrame, score_col: str) -> None:
    top20_n = max(1, int(round(len(df) * 0.20)))
    top20 = df.sort_values(score_col, ascending=False).head(top20_n)
    print(f"\n=== {score_col} ===")
    print(f"Rows: {len(df)}")
    print(f"Overall HR: {df['hit'].mean():.4f}")
    print(f"Top 20% HR: {top20['hit'].mean():.4f} (n={len(top20)})")


def _run_single_backtest(
    slate: pd.DataFrame,
    actuals_path: Path,
    out_dir: Path,
    write_details: bool = True,
) -> dict:
    actuals = _ensure_required_columns(pd.read_csv(actuals_path, dtype=str).fillna(""), is_actuals=True)
    key_cols = ["player", "team", "prop_type"]
    missing_actuals = [c for c in key_cols + ["actual"] if c not in actuals.columns]
    if missing_actuals:
        raise ValueError(f"Actuals missing required columns: {missing_actuals}")

    merged = slate.merge(actuals[key_cols + ["actual"]], on=key_cols, how="inner")
    merged["line"] = _to_num(merged["line"])
    merged["actual"] = _to_num(merged["actual"])
    merged = merged[merged["line"].notna() & merged["actual"].notna()].copy()
    if merged.empty:
        return {
            "actuals_file": str(actuals_path),
            "error": "No matched rows between slate and actuals (or all line/actual invalid).",
            "matched_rows": 0,
        }

    direction = _resolve_direction(merged)
    merged = merged[direction.isin(["OVER", "UNDER"])].copy()
    merged["direction_used"] = direction[direction.isin(["OVER", "UNDER"])].values
    merged["hit"] = _grade_hits(merged)

    if "rank_score" in merged.columns:
        merged["rank_score"] = _to_num(merged["rank_score"])
    if "final_score" in merged.columns:
        merged["final_score"] = _to_num(merged["final_score"])
    if "ml_prob" in merged.columns:
        merged["ml_prob"] = _to_num(merged["ml_prob"])

    score_cols = [c for c in ["rank_score", "final_score", "ml_prob"] if c in merged.columns]
    score_cols = [c for c in score_cols if merged[c].notna().any()]
    if not score_cols:
        return {
            "actuals_file": str(actuals_path),
            "error": "No score columns found. Need at least one of: rank_score, final_score, ml_prob",
            "matched_rows": int(len(merged)),
        }

    summary: dict[str, float | int | str] = {
        "actuals_file": str(actuals_path),
        "matched_rows": int(len(merged)),
    }
    for col in score_cols:
        valid = merged[merged[col].notna()].copy()
        if len(valid) == 0:
            continue
        top20_n = max(1, int(round(len(valid) * 0.20)))
        top20 = valid.sort_values(col, ascending=False).head(top20_n)
        summary[f"{col}_rows"] = int(len(valid))
        summary[f"{col}_overall_hr"] = float(valid["hit"].mean())
        summary[f"{col}_top20_hr"] = float(top20["hit"].mean())

    if write_details:
        stem = actuals_path.stem.replace(" ", "_")
        merged_out = out_dir / f"backtest_nba_graded_rows_{stem}.csv"
        merged.to_csv(merged_out, index=False)

    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest NBA ranked props against actuals.")
    ap.add_argument(
        "--slate",
        default="data/outputs/step8_all_direction.csv",
        help="Path to ranked slate CSV (step8 recommended).",
    )
    ap.add_argument(
        "--actuals",
        default="data/inputs/actuals_nba_2026-02-24.csv",
        help="Path to actuals CSV with columns: player, team, prop_type, actual",
    )
    ap.add_argument(
        "--out-dir",
        default="data/outputs",
        help="Directory where backtest outputs are saved.",
    )
    ap.add_argument(
        "--batch-actuals-glob",
        default="",
        help="Optional glob for batch backtest (example: archive/legacy/actuals_nba*.csv).",
    )
    args = ap.parse_args()

    slate_path = Path(args.slate)
    actuals_path = Path(args.actuals)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not slate_path.exists():
        raise FileNotFoundError(f"Slate file not found: {slate_path}")
    if not actuals_path.exists():
        raise FileNotFoundError(f"Actuals file not found: {actuals_path}")

    slate = _ensure_required_columns(_read_table(slate_path), is_actuals=False)
    key_cols = ["player", "team", "prop_type"]
    missing_slate = [c for c in key_cols + ["line"] if c not in slate.columns]
    if missing_slate:
        raise ValueError(f"Slate missing required columns: {missing_slate}")
    # Batch mode: run multiple actuals files against the same slate.
    if args.batch_actuals_glob:
        root = Path(__file__).resolve().parents[3]
        actuals_files = sorted(root.glob(args.batch_actuals_glob))
        if not actuals_files:
            raise FileNotFoundError(f"No files matched --batch-actuals-glob={args.batch_actuals_glob}")

        rows: list[dict] = []
        for p in actuals_files:
            try:
                rows.append(_run_single_backtest(slate, p, out_dir, write_details=False))
            except Exception as e:
                rows.append({"actuals_file": str(p), "error": str(e), "matched_rows": 0})

        batch_df = pd.DataFrame(rows)
        batch_out = out_dir / "backtest_nba_batch_summary.csv"
        batch_df.to_csv(batch_out, index=False)
        print(f"Batch backtest complete. Files tested: {len(actuals_files)}")
        print(f"Saved: {batch_out}")
        print(batch_df.to_string(index=False))
        return

    actuals = _ensure_required_columns(_read_table(actuals_path), is_actuals=True)
    missing_actuals = [c for c in key_cols + ["actual"] if c not in actuals.columns]
    if missing_actuals:
        raise ValueError(f"Actuals missing required columns: {missing_actuals}")

    merged = slate.merge(actuals[key_cols + ["actual"]], on=key_cols, how="inner")
    merged["line"] = _to_num(merged["line"])
    merged["actual"] = _to_num(merged["actual"])
    merged = merged[merged["line"].notna() & merged["actual"].notna()].copy()
    if merged.empty:
        print(
            "[backtest_nba] SKIP daily backtest: no rows after merging slate with actuals "
            "(empty slate or no overlap)."
        )
        merged.to_csv(out_dir / "backtest_nba_graded_rows.csv", index=False)
        return

    direction = _resolve_direction(merged)
    merged = merged[direction.isin(["OVER", "UNDER"])].copy()
    merged["direction_used"] = direction[direction.isin(["OVER", "UNDER"])].values
    merged["hit"] = _grade_hits(merged)

    if "rank_score" in merged.columns:
        merged["rank_score"] = _to_num(merged["rank_score"])
    if "final_score" in merged.columns:
        merged["final_score"] = _to_num(merged["final_score"])
    if "ml_prob" in merged.columns:
        merged["ml_prob"] = _to_num(merged["ml_prob"])

    score_cols = [c for c in ["rank_score", "final_score", "ml_prob"] if c in merged.columns]
    score_cols = [c for c in score_cols if merged[c].notna().any()]
    if not score_cols:
        print(
            "[backtest_nba] SKIP daily backtest: no usable score columns "
            f"(matched_rows={len(merged)}). Slate needs rank_score, final_score, or ml_prob with at least one non-null value."
        )
        merged.to_csv(out_dir / "backtest_nba_graded_rows.csv", index=False)
        return

    print(f"Matched graded rows: {len(merged)}")
    print(f"Available scoring columns: {score_cols}")

    tops: list[pd.DataFrame] = []
    for col in score_cols:
        valid = merged[merged[col].notna()].copy()
        _summary_block(valid, col)
        tops.append(_top_metrics(valid, col))

    top_curve = pd.concat(tops, ignore_index=True)
    by_prop = (
        merged.groupby("prop_type", dropna=False)["hit"]
        .agg(["mean", "count"])
        .rename(columns={"mean": "hit_rate", "count": "rows"})
        .sort_values("rows", ascending=False)
        .reset_index()
    )
    by_dir = (
        merged.groupby("direction_used", dropna=False)["hit"]
        .agg(["mean", "count"])
        .rename(columns={"mean": "hit_rate", "count": "rows"})
        .reset_index()
    )
    by_tier = pd.DataFrame()
    if "tier" in merged.columns:
        by_tier = (
            merged.groupby("tier", dropna=False)["hit"]
            .agg(["mean", "count"])
            .rename(columns={"mean": "hit_rate", "count": "rows"})
            .sort_values("rows", ascending=False)
            .reset_index()
        )

    merged_out = out_dir / "backtest_nba_graded_rows.csv"
    top_out = out_dir / "backtest_nba_top_curve.csv"
    prop_out = out_dir / "backtest_nba_by_prop.csv"
    dir_out = out_dir / "backtest_nba_by_direction.csv"
    tier_out = out_dir / "backtest_nba_by_tier.csv"

    merged.to_csv(merged_out, index=False)
    top_curve.to_csv(top_out, index=False)
    by_prop.to_csv(prop_out, index=False)
    by_dir.to_csv(dir_out, index=False)
    if not by_tier.empty:
        by_tier.to_csv(tier_out, index=False)

    print("\nSaved backtest outputs:")
    print(f"- {merged_out}")
    print(f"- {top_out}")
    print(f"- {prop_out}")
    print(f"- {dir_out}")
    if not by_tier.empty:
        print(f"- {tier_out}")


if __name__ == "__main__":
    main()
