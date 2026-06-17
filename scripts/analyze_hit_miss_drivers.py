#!/usr/bin/env python3
"""
Cross-sport hit vs miss driver analysis from graded_props JSON history.

Loads all graded_props since combined-slate era, enriches with top-3 context,
and reports lift/correlation by sport across tiers, defenses, L5/L10, edges, etc.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils.graded_schema import normalize_graded_df  # noqa: E402
from utils.stack_70_eligible import attach_stack_70_columns  # noqa: E402

COMBINED_SLATE_START = "2026-02-24"
MIN_BUCKET_N = 40


def _prop_category(prop: str) -> str:
    p = re.sub(r"\s+", " ", str(prop or "").lower().strip())
    p = re.sub(r"\(combo\)\s*$", "", p).strip()
    if any(x in p for x in ("point", "pts")) and "rebound" not in p and "assist" not in p:
        return "points"
    if "rebound" in p or p in {"reb", "rebs"}:
        return "rebounds"
    if "assist" in p or p in {"ast", "asts"}:
        return "assists"
    if "3" in p and ("pt" in p or "pointer" in p or "made" in p):
        return "threes"
    if "steal" in p or "block" in p or "stock" in p:
        return "stocks"
    if "goal" in p or "shot" in p:
        return "scoring"
    if "save" in p or "goalie" in p:
        return "goalie"
    if "game" in p or "set" in p or "ace" in p:
        return "tennis_totals"
    return p[:40] or "(unknown)"


def load_graded_props(
    roots: list[Path],
    date_from: str,
    date_to: str | None,
) -> pd.DataFrame:
    rows: list[dict] = []
    seen_paths: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for p in sorted(root.glob("graded_props_*.json")):
            if p in seen_paths:
                continue
            seen_paths.add(p)
            d = p.stem.replace("graded_props_", "")
            if d < date_from or (date_to and d > date_to):
                continue
            try:
                payload = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for r in payload.get("props") or []:
                row = dict(r)
                row["grade_date"] = d
                rows.append(row)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = normalize_graded_df(df)
    return df


def _dedupe_legs(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    key = [
        "grade_date",
        "sport",
        "player",
        "prop_type",
        "line",
        "direction",
    ]
    for c in key:
        if c not in df.columns:
            df[c] = ""
    return df.drop_duplicates(subset=key, keep="first").copy()


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["sport"] = out["sport"].astype(str).str.upper().str.strip()
    res = out["result"].astype(str).str.upper().str.strip()
    out = out[res.isin(["HIT", "MISS"])].copy()
    out["is_hit"] = (out["result"].astype(str).str.upper() == "HIT").astype(int)
    out["prop_cat"] = out["prop_type"].map(_prop_category)

    for c in ("pick_type", "tier", "def_tier", "direction", "l10_streak", "consistency_grade",
              "minutes_tier", "usage_tier", "role_tier", "game_total_bucket", "h2h_bucket",
              "confidence_tier"):
        if c in out.columns:
            out[c] = out[c].astype(str).str.strip()
            out[c] = out[c].replace({"": np.nan, "nan": np.nan, "None": np.nan})

    for c in ("l5_over", "l5_under", "l10_over", "l10_under", "hit_rate", "edge", "ml_prob",
              "strat_hit_rate", "strat_n", "team_top3_rank", "team_bottom3_rank",
              "hit_rate_l5", "hit_rate_l10", "usage_pct", "margin"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")

    top3 = pd.to_numeric(out.get("team_top3_rank"), errors="coerce")
    bot3 = pd.to_numeric(out.get("team_bottom3_rank"), errors="coerce")
    out["player_tier_bucket"] = np.where(
        top3.le(3) & top3.notna(), "top3",
        np.where(bot3.le(3) & bot3.notna(), "bottom3", "mid/other"),
    )
    out["on_ticket_flag"] = out.get("on_ticket", pd.Series(False, index=out.index)).fillna(False).astype(bool)

    # Directional L5/L10 support for the bet side
    direction = out["direction"].astype(str).str.upper()
    l5o = pd.to_numeric(out.get("l5_over"), errors="coerce")
    l5u = pd.to_numeric(out.get("l5_under"), errors="coerce")
    l10o = pd.to_numeric(out.get("l10_over"), errors="coerce")
    l10u = pd.to_numeric(out.get("l10_under"), errors="coerce")
    out["l5_side"] = np.where(direction == "UNDER", l5u, l5o)
    out["l10_side"] = np.where(direction == "UNDER", l10u, l10o)

    def _l5_bucket(v: float) -> str:
        if pd.isna(v):
            return "(missing)"
        if v >= 4:
            return "4-5 hot"
        if v >= 3:
            return "3 warm"
        return "0-2 cold"

    out["l5_side_bucket"] = out["l5_side"].map(_l5_bucket)
    out["l10_side_bucket"] = out["l10_side"].map(_l5_bucket)

    pt = out.get("pick_type", pd.Series("", index=out.index)).astype(str).str.lower()
    out["pick_type_norm"] = np.where(
        pt.str.contains("goblin"), "Goblin",
        np.where(pt.str.contains("demon"), "Demon", "Standard"),
    )

    for col, bins, labels in (
        ("hit_rate", [0, 0.5, 0.55, 0.6, 0.65, 0.7, 1.01], ["<50", "50-55", "55-60", "60-65", "65-70", "70+"]),
        ("edge", [-99, 0, 0.5, 1.0, 1.5, 2.0, 99], ["<0", "0-0.5", "0.5-1", "1-1.5", "1.5-2", "2+"]),
        ("ml_prob", [0, 0.5, 0.6, 0.7, 0.8, 1.01], ["<50", "50-60", "60-70", "70-80", "80+"]),
    ):
        if col in out.columns:
            out[f"{col}_bin"] = pd.cut(
                pd.to_numeric(out[col], errors="coerce"),
                bins=bins,
                labels=labels,
                include_lowest=True,
            ).astype(str)

    return out


def _lift_table(df: pd.DataFrame, dim: str, sport: str, min_bucket: int = MIN_BUCKET_N) -> pd.DataFrame:
    if dim not in df.columns or df.empty:
        return pd.DataFrame()
    base_hr = df["is_hit"].mean()
    g = (
        df.groupby(dim, dropna=False)
        .agg(n=("is_hit", "count"), hits=("is_hit", "sum"), hr=("is_hit", "mean"))
        .reset_index()
    )
    g["sport"] = sport
    g["dimension"] = dim
    g["lift_pp"] = (g["hr"] - base_hr) * 100
    g = g[g["n"] >= min_bucket].sort_values("lift_pp", ascending=False)
    return g


def _numeric_compare(df: pd.DataFrame, col: str, sport: str, min_bucket: int = MIN_BUCKET_N) -> dict:
    s = pd.to_numeric(df.get(col), errors="coerce")
    mask = s.notna()
    if mask.sum() < min_bucket:
        return {}
    hit_mean = s[mask & df["is_hit"].eq(1)].mean()
    miss_mean = s[mask & df["is_hit"].eq(0)].mean()
    corr = s[mask].corr(df.loc[mask, "is_hit"])
    return {
        "sport": sport,
        "feature": col,
        "n": int(mask.sum()),
        "hit_mean": float(hit_mean) if pd.notna(hit_mean) else None,
        "miss_mean": float(miss_mean) if pd.notna(miss_mean) else None,
        "delta": float(hit_mean - miss_mean) if pd.notna(hit_mean) and pd.notna(miss_mean) else None,
        "corr_with_hit": float(corr) if pd.notna(corr) else None,
    }


def analyze_sport(df: pd.DataFrame, sport: str, *, min_bucket: int = MIN_BUCKET_N) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    sub = df[df["sport"] == sport].copy()
    if sub.empty:
        return pd.DataFrame(), pd.DataFrame(), {}
    base = {
        "sport": sport,
        "n_decided": len(sub),
        "hits": int(sub["is_hit"].sum()),
        "hit_rate": float(sub["is_hit"].mean()),
        "date_min": str(sub["grade_date"].min()),
        "date_max": str(sub["grade_date"].max()),
    }

    cat_dims = [
        "prop_cat", "pick_type_norm", "direction", "tier", "def_tier",
        "player_tier_bucket", "l5_side_bucket", "l10_side_bucket", "l10_streak",
        "consistency_grade", "minutes_tier", "usage_tier", "game_total_bucket",
        "h2h_bucket", "confidence_tier", "on_ticket_flag",
        "hit_rate_bin", "edge_bin", "ml_prob_bin",
    ]
    # boolean flags
    for flag in ("top3_weak_overperformer", "top3_elite_fader", "injury_boost_candidate",
                 "team_star_out", "usage_vacuum", "stack_70_eligible"):
        if flag in sub.columns:
            raw = sub[flag].astype(str).str.strip().str.lower()
            sub[flag] = np.where(
                raw.isin({"1", "true", "yes", "y"}), "yes", "no"
            )
            cat_dims.append(flag)

    lift_parts = [_lift_table(sub, d, sport, min_bucket) for d in cat_dims]
    lift = pd.concat([p for p in lift_parts if not p.empty], ignore_index=True)

    num_cols = [
        "hit_rate", "edge", "ml_prob", "strat_hit_rate", "strat_n",
        "l5_side", "l10_side", "l5_over", "l5_under", "l10_over", "l10_under",
        "hit_rate_l5", "hit_rate_l10", "usage_pct", "margin",
        "team_top3_rank", "team_bottom3_rank",
    ]
    num_rows = [_numeric_compare(sub, c, sport, min_bucket) for c in num_cols]
    num_df = pd.DataFrame([r for r in num_rows if r])

    # Top combos: pick_type × direction × def_tier
    combo_dim = "pick_dir_def"
    sub[combo_dim] = (
        sub["pick_type_norm"].fillna("?") + " | "
        + sub["direction"].fillna("?") + " | def="
        + sub["def_tier"].fillna("(missing)")
    )
    combo_lift = _lift_table(sub, combo_dim, sport, min_bucket)
    if not combo_lift.empty:
        combo_lift["dimension"] = combo_dim
        lift = pd.concat([lift, combo_lift], ignore_index=True)

    tier_dir = "tier_dir_player"
    sub[tier_dir] = (
        sub["tier"].fillna("?") + " | " + sub["direction"].fillna("?")
        + " | " + sub["player_tier_bucket"]
    )
    td_lift = _lift_table(sub, tier_dir, sport, min_bucket)
    if not td_lift.empty:
        td_lift["dimension"] = tier_dir
        lift = pd.concat([lift, td_lift], ignore_index=True)

    return lift, num_df, base


def main() -> None:
    ap = argparse.ArgumentParser(description="Hit vs miss driver analysis across sports")
    ap.add_argument("--date-from", default=COMBINED_SLATE_START)
    ap.add_argument("--date-to", default=None)
    ap.add_argument("--out-dir", default="data/reports/hit_miss_drivers")
    ap.add_argument("--min-bucket", type=int, default=MIN_BUCKET_N)
    ap.add_argument(
        "--on-ticket-only",
        action="store_true",
        help="Analyze only legs flagged on_ticket in graded_props (actionable ticket pool).",
    )
    args = ap.parse_args()
    min_bucket = int(args.min_bucket)

    roots = [
        REPO / "mobile" / "www",
    ]
    print(f"Loading graded_props from {args.date_from} ...")
    raw = load_graded_props(roots, args.date_from, args.date_to)
    if raw.empty:
        print("No graded props found.")
        return

    raw = _dedupe_legs(raw)
    print(f"  rows after dedupe: {len(raw):,}")
    df = attach_stack_70_columns(raw, repo=REPO, compute_eligible=True)
    df = _prepare(df)
    if args.on_ticket_only:
        df = df[df["on_ticket_flag"]].copy()
        print(f"  on_ticket legs (HIT/MISS): {len(df):,}")
    if df.empty:
        print("No decided legs after filters.")
        return
    print(f"  decided legs (HIT/MISS): {len(df):,}  overall HR {df['is_hit'].mean():.1%}")

    out_dir = REPO / args.out_dir
    if args.on_ticket_only:
        out_dir = out_dir / "on_ticket"
    out_dir.mkdir(parents=True, exist_ok=True)

    sports = sorted(df["sport"].unique())
    all_lift: list[pd.DataFrame] = []
    all_num: list[pd.DataFrame] = []
    summaries: list[dict] = []

    for sp in sports:
        lift, num_df, base = analyze_sport(df, sp, min_bucket=min_bucket)
        summaries.append(base)
        if not lift.empty:
            all_lift.append(lift)
        if not num_df.empty:
            all_num.append(num_df)
        print(f"\n{'='*60}\n{sp}: {base['n_decided']:,} legs, {base['hit_rate']:.1%} HR")
        if not lift.empty:
            dim_col = [c for c in lift.columns if c not in {"sport", "dimension", "n", "hits", "hr", "lift_pp"}][0]
            print("  Top positive lifts (min n):")
            for _, r in lift.head(5).iterrows():
                print(f"    {r['dimension']}={r[dim_col]}: {r['hr']:.1%} ({int(r['hits'])}/{int(r['n'])}) lift {r['lift_pp']:+.1f}pp")
            print("  Worst lifts:")
            for _, r in lift.tail(5).iterrows():
                print(f"    {r['dimension']}={r[dim_col]}: {r['hr']:.1%} ({int(r['hits'])}/{int(r['n'])}) lift {r['lift_pp']:+.1f}pp")
        if not num_df.empty:
            top = num_df.reindex(num_df["corr_with_hit"].abs().sort_values(ascending=False).index).head(5)
            print("  Strongest numeric correlations with hit:")
            for _, r in top.iterrows():
                print(f"    {r['feature']}: corr={r['corr_with_hit']:+.3f} hit_mean={r['hit_mean']} miss_mean={r['miss_mean']}")

    if all_lift:
        lift_out = pd.concat(all_lift, ignore_index=True)
        lift_out.to_csv(out_dir / "hit_miss_lift_by_dimension.csv", index=False)
    if all_num:
        num_out = pd.concat(all_num, ignore_index=True)
        num_out.to_csv(out_dir / "hit_miss_numeric_compare.csv", index=False)

    summary_path = out_dir / "hit_miss_summary.json"
    payload = {
        "date_from": args.date_from,
        "date_to": args.date_to,
        "on_ticket_only": bool(args.on_ticket_only),
        "overall": {
            "n_decided": len(df),
            "hit_rate": float(df["is_hit"].mean()),
            "sports": summaries,
        },
    }
    summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"\nWrote reports -> {out_dir}")


if __name__ == "__main__":
    main()
