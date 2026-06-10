#!/usr/bin/env python3
"""
Measure hit-rate lift from each 70%% stack layer on graded history.

Layers (cumulative):
  0. baseline — decided non-Demon, valid market side
  1. + strat/row hit rate >= 0.70
  2. + L5 side hits >= 4
  3. + opponent/top-3 alignment (when context known)
  4. + consistency grade S/A/B (when grade known)
  5. full stack (all layers + Goblin OVER-only)

Usage:
  python scripts/backtest_stack_70_lift.py
  python scripts/backtest_stack_70_lift.py --sport NBA --min-n 30
  python scripts/backtest_stack_70_lift.py --out data/reports/stack_70_lift.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.analyze_graded_prop_winners import (  # noqa: E402
    dedupe_graded_workbooks,
    discover_graded_workbooks,
    exclude_non_rating_legs,
    load_unified,
    normalize_decided,
)
from utils.defense_tiers import normalize_def_tier_label  # noqa: E402
from utils.graded_enrichment import enrich_graded_for_analysis, is_empty_def_tier  # noqa: E402
from utils.stack_70_eligible import (  # noqa: E402
    _norm_direction,
    _norm_pick_type,
    is_invalid_market_side,
)

_OVER_FAVOR_DEF = frozenset({"WEAK", "BELOW AVG", "AVG"})
_UNDER_FAVOR_DEF = frozenset({"ELITE", "ABOVE AVG"})

_STRONG_CONSISTENCY = frozenset({"S", "A", "B"})
_DEFAULT_ROOTS = [
    _REPO / "ui_runner" / "graded_slate",
    _REPO / "outputs",
]


def _hr(sub: pd.DataFrame) -> tuple[float, int]:
    if sub.empty:
        return float("nan"), 0
    n = len(sub)
    return float(sub["is_hit"].mean()), n


def _layer_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    pick = df.get("pick_type", pd.Series("", index=df.index)).map(_norm_pick_type)
    direction = df.get("direction", pd.Series("", index=df.index)).map(_norm_direction)

    invalid = df.apply(lambda r: is_invalid_market_side(r.get("pick_type", ""), r.get("direction", "")), axis=1)
    base = ~invalid & ~pick.eq("DEMON")

    if "hit_rate" in df.columns:
        hr = pd.to_numeric(df["hit_rate"], errors="coerce")
    else:
        hr = pd.Series(np.nan, index=df.index, dtype=float)
    if hr.notna().sum() == 0 and "last5_hit_rate" in df.columns:
        hr = pd.to_numeric(df["last5_hit_rate"], errors="coerce")
    strat_hr = pd.to_numeric(df.get("strat_hit_rate"), errors="coerce")
    strat_n = pd.to_numeric(df.get("strat_n"), errors="coerce")
    hr_ok = (
        (strat_hr >= 0.70) & (strat_n >= 30)
    ) | (hr >= 0.70)
    l1 = base & hr_ok.fillna(False)

    l5o = pd.to_numeric(df.get("l5_over"), errors="coerce")
    l5u = pd.to_numeric(df.get("l5_under"), errors="coerce")
    side_l5 = np.where(direction.eq("UNDER"), l5u, l5o)
    l2 = l1 & (pd.Series(side_l5, index=df.index) >= 4)

    over = direction.eq("OVER")
    def_tier = df.get("def_tier", pd.Series("", index=df.index)).map(normalize_def_tier_label).astype(str).str.upper()
    def_tier = def_tier.where(~def_tier.map(is_empty_def_tier), "")
    weak_over = pd.to_numeric(df.get("top3_weak_overperformer"), errors="coerce").fillna(0).astype(int).eq(1)
    elite_fade = pd.to_numeric(df.get("top3_elite_fader"), errors="coerce").fillna(0).astype(int).eq(1)
    top_rank = pd.to_numeric(df.get("team_top3_rank"), errors="coerce")
    bot_rank = pd.to_numeric(df.get("team_bottom3_rank"), errors="coerce")
    boost = pd.to_numeric(df.get("def_boost_hist"), errors="coerce")
    has_ctx = (
        def_tier.ne("")
        | top_rank.notna()
        | bot_rank.notna()
        | boost.notna()
        | weak_over
        | elite_fade
    )
    over_ok = (
        def_tier.isin(_OVER_FAVOR_DEF)
        | weak_over
        | (top_rank <= 3)
        | (boost > 0)
    )
    under_ok = (
        def_tier.isin(_UNDER_FAVOR_DEF)
        | elite_fade
        | (bot_rank <= 3)
        | (boost < 0)
    )
    matchup = (~has_ctx) | (over & over_ok) | (~over & under_ok)
    l3 = l2 & matchup

    cg = df.get("consistency_grade", pd.Series("?", index=df.index)).astype(str).str.strip().str.upper()
    cg_ok = cg.isin(_STRONG_CONSISTENCY) | cg.eq("?") | cg.eq("")
    l4 = l3 & cg_ok

    goblin_over = ~pick.eq("GOBLIN") | direction.eq("OVER")
    full_stack = l4 & goblin_over

    return {
        "baseline": base,
        "hit_rate_70": l1,
        "l5_side_4": l2,
        "matchup_aligned": l3,
        "consistency_sab": l4,
        "full_stack": full_stack,
    }


def run_backtest(
    roots: list[Path],
    *,
    sport: str,
    min_n: int,
    out_path: Path | None,
) -> pd.DataFrame:
    raw = load_unified(roots, sport=sport or None)
    if raw.empty:
        print("No graded rows found.")
        return pd.DataFrame()

    decided = normalize_decided(raw)
    decided = exclude_non_rating_legs(decided)
    if sport:
        decided = decided[decided["_sport"].astype(str).str.upper().eq(sport.upper())]
    decided = enrich_graded_for_analysis(decided, stack_eligible=False)

    cov = decided.copy()
    if "def_tier" in cov.columns:
        known_def = (~cov["def_tier"].map(is_empty_def_tier)).mean()
        print(f"def_tier coverage after enrichment: {100*known_def:.1f}%")
    if "team_top3_rank" in cov.columns:
        known_t3 = pd.to_numeric(cov["team_top3_rank"], errors="coerce").notna().mean()
        print(f"top3 coverage after enrichment: {100*known_t3:.1f}%")

    rows: list[dict] = []
    layers = _layer_masks(decided)

    def _emit(scope: str, sub: pd.DataFrame) -> None:
        prev_hr, prev_n = float("nan"), 0
        for layer_name, mask in layers.items():
            cell = sub[mask.reindex(sub.index, fill_value=False)]
            hr, n = _hr(cell)
            lift = hr - prev_hr if prev_n >= min_n and n >= min_n and pd.notna(prev_hr) and pd.notna(hr) else float("nan")
            rows.append(
                {
                    "scope": scope,
                    "layer": layer_name,
                    "hit_rate": round(hr, 4) if pd.notna(hr) else np.nan,
                    "n": n,
                    "lift_vs_prev": round(lift, 4) if pd.notna(lift) else np.nan,
                }
            )
            if n >= min_n:
                prev_hr, prev_n = hr, n

    _emit("ALL", decided)
    for sp in sorted(decided["_sport"].dropna().unique()):
        _emit(str(sp).upper(), decided[decided["_sport"] == sp])

    out = pd.DataFrame(rows)
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"Wrote {out_path}")

    print("\n=== Stack 70 lift (ALL) ===")
    all_rows = out[out["scope"].eq("ALL") & (out["n"] >= min_n)]
    for _, r in all_rows.iterrows():
        lift = r["lift_vs_prev"]
        lift_s = f"{100*lift:+.1f}pp" if pd.notna(lift) else "—"
        print(f"  {r['layer']:<22} HR={100*r['hit_rate']:.1f}%  n={int(r['n']):,}  lift={lift_s}")

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--roots", nargs="*", default=[str(p) for p in _DEFAULT_ROOTS])
    ap.add_argument("--sport", default="", help="Filter one sport (NBA, NHL, …)")
    ap.add_argument("--min-n", type=int, default=50, help="Min n to print lift vs previous layer")
    ap.add_argument("--out", default=str(_REPO / "data" / "reports" / "stack_70_lift.csv"))
    args = ap.parse_args()

    roots = [Path(r) for r in args.roots]
    out_path = Path(args.out) if args.out else None
    run_backtest(roots, sport=str(args.sport or "").strip(), min_n=int(args.min_n), out_path=out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
