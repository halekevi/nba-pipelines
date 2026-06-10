#!/usr/bin/env python3
"""
High-confidence locks backtest: ml_prob calibration + validated lock tickets.

Uses graded_props JSON (same archive as backtest_matchup_parlay.py).
Standard pick_type only.

Steps:
  1) ml_prob calibration buckets (overall + per sport)
  2) Lock candidates (ml_prob >= 0.60, abs_edge >= 0.05, category HR >= 58%)
  3) Daily top-N lock tickets (N = 3, 4, 5, 6), unique players
  4) Sport × direction breakdown on lock candidates; flag n >= 50 & HR >= 60%

Usage:
  py -3 scripts/backtest_locks.py
  py -3 scripts/backtest_locks.py --from 2026-05-06
  py -3 scripts/backtest_locks.py --all-dates
  py -3 scripts/backtest_locks.py --all-dates --walk-forward
  py -3 scripts/backtest_locks.py --all-dates --locks-2leg
  py -3 scripts/backtest_locks.py --all-dates --sport-breakdown
  py -3 scripts/backtest_locks.py --all-dates --best-locks
  py -3 scripts/backtest_locks.py --all-dates --best-locks-gated
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from analyze_graded_history import (  # noqa: E402
    _GRADED_DIR,
    _norm_dir,
    _norm_pick,
    _norm_prop_type,
    _norm_sport,
    _parse_hit,
)

ML_BUCKETS: tuple[tuple[str, float, float | None], ...] = (
    ("0.50-0.55", 0.50, 0.55),
    ("0.55-0.60", 0.55, 0.60),
    ("0.60-0.65", 0.60, 0.65),
    ("0.65-0.70", 0.65, 0.70),
    ("0.70+", 0.70, None),
)

LOCK_MIN_ML = 0.60
LOCK_MIN_ABS_EDGE = 0.05
LOCK_ML_BUCKETS = frozenset({"0.60-0.65", "0.65-0.70", "0.70+"})
LOCK_CATEGORY_MIN_HR = 0.58
FLAG_MIN_N = 50
FLAG_MIN_HR = 0.60
DAILY_NS = (3, 4, 5, 6)
BEST_LOCKS_NS = (2, 3, 4, 5, 6)
ADDITIVE_EXCLUDED_SPORTS = frozenset({"NBA", "WNBA"})
ADDITIVE_THRESHOLDS = (0.62, 0.65, 0.68, 0.70)
ADDITIVE_HIGH_THRESHOLDS = (0.72, 0.75, 0.78, 0.80, 0.83, 0.85)
ADDITIVE_AVG_LEGS_CROSSOVERS = (4.0, 3.0, 2.5)
ADDITIVE_MIN_LEGS = 2
ADDITIVE_MAX_LEGS = 6
WF_MIN_PAST_DATES = 20
WF_MIN_CATEGORY_N = 30


def load_graded_standard(*, min_date: str | None) -> pd.DataFrame:
    paths = sorted(p for p in _GRADED_DIR.glob("graded_props_*.json") if ".bak_" not in p.name)
    min_d = str(min_date or "").strip()[:10]
    rows: list[dict] = []
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        file_date = str(raw.get("date") or path.stem.replace("graded_props_", ""))[:10]
        if min_d and len(min_d) == 10 and file_date < min_d:
            continue
        chunk = raw.get("props", raw.get("rows", []))
        if not isinstance(chunk, list):
            continue
        for r in chunk:
            if not isinstance(r, dict):
                continue
            if _norm_pick(r.get("pick_type")) != "standard":
                continue
            hit = _parse_hit(r.get("result"))
            if hit is None:
                continue
            ml = pd.to_numeric(r.get("ml_prob"), errors="coerce")
            edge = pd.to_numeric(r.get("edge"), errors="coerce")
            if pd.isna(ml):
                continue
            rows.append(
                {
                    "player": str(r.get("player", "")).strip(),
                    "player_key": str(r.get("player", "")).strip().lower(),
                    "sport": _norm_sport(r.get("sport")),
                    "prop_type": _norm_prop_type(r.get("prop")),
                    "pick_type": "standard",
                    "direction": _norm_dir(r.get("direction") or r.get("over_under")),
                    "line": pd.to_numeric(r.get("line"), errors="coerce"),
                    "ml_prob": float(ml),
                    "edge": float(edge) if pd.notna(edge) else np.nan,
                    "abs_edge": float(abs(edge)) if pd.notna(edge) else np.nan,
                    "hit": int(hit),
                    "file_date": file_date,
                }
            )
    return pd.DataFrame(rows)


def assign_ml_bucket(ml: float) -> str:
    for label, lo, hi in ML_BUCKETS:
        if hi is None:
            if ml >= lo:
                return label
        elif lo <= ml < hi:
            return label
    return "below_0.50"


def add_bucket_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ml_bucket"] = out["ml_prob"].map(assign_ml_bucket)
    out["category_key"] = (
        out["sport"].astype(str)
        + "|"
        + out["direction"].astype(str)
        + "|"
        + out["ml_bucket"].astype(str)
    )
    return out


def build_calibration(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for label, lo, hi in ML_BUCKETS:
        if hi is None:
            sub = df[df["ml_prob"] >= lo]
        else:
            sub = df[(df["ml_prob"] >= lo) & (df["ml_prob"] < hi)]
        if sub.empty:
            continue
        hr = float(sub["hit"].mean())
        avg_ml = float(sub["ml_prob"].mean())
        rows.append(
            {
                "section": "calibration",
                "scope": "ALL",
                "sport": "ALL",
                "ml_bucket": label,
                "direction": "",
                "n": len(sub),
                "hit_rate": round(hr, 4),
                "avg_ml_prob": round(avg_ml, 4),
                "implied_edge_pp": round((hr - avg_ml) * 100, 2),
            }
        )
        for sp, g in sub.groupby("sport"):
            if len(g) < 5:
                continue
            hr_sp = float(g["hit"].mean())
            avg_sp = float(g["ml_prob"].mean())
            rows.append(
                {
                    "section": "calibration",
                    "scope": "sport",
                    "sport": sp,
                    "ml_bucket": label,
                    "direction": "",
                    "n": len(g),
                    "hit_rate": round(hr_sp, 4),
                    "avg_ml_prob": round(avg_sp, 4),
                    "implied_edge_pp": round((hr_sp - avg_sp) * 100, 2),
                }
            )
    return pd.DataFrame(rows)


def print_calibration_table(cal: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("  STEP 1 — ML_PROB CALIBRATION (Standard legs)")
    print("=" * 72)
    overall = cal[(cal["scope"] == "ALL")].sort_values("ml_bucket")
    print(f"\n{'Bucket':<12} {'n':>8} {'Hit%':>8} {'Avg ML%':>9} {'Edge pp':>9}")
    print("-" * 50)
    for _, r in overall.iterrows():
        print(
            f"{r['ml_bucket']:<12} {int(r['n']):>8,} "
            f"{100*r['hit_rate']:>7.1f}% {100*r['avg_ml_prob']:>8.1f}% "
            f"{r['implied_edge_pp']:>+8.1f}"
        )

    print("\n--- Per sport (overall bucket) ---")
    sport_cal = cal[cal["scope"] == "sport"].sort_values(["ml_bucket", "sport"])
    for bucket in sport_cal["ml_bucket"].unique():
        sub = sport_cal[sport_cal["ml_bucket"] == bucket]
        print(f"\n  [{bucket}]")
        for _, r in sub.iterrows():
            print(
                f"    {r['sport']:<8} n={int(r['n']):>6,}  "
                f"hit={100*r['hit_rate']:.1f}%  ml={100*r['avg_ml_prob']:.1f}%  "
                f"edge={r['implied_edge_pp']:+.1f}pp"
            )


def category_hit_rates(df: pd.DataFrame, *, min_n: int) -> pd.DataFrame:
    g = (
        df.groupby(["sport", "direction", "ml_bucket"], dropna=False)
        .agg(n=("hit", "count"), hit_rate=("hit", "mean"), avg_ml=("ml_prob", "mean"))
        .reset_index()
    )
    g["category_key"] = g["sport"] + "|" + g["direction"] + "|" + g["ml_bucket"]
    g["validated"] = (
        (g["n"] >= min_n)
        & (g["hit_rate"] >= LOCK_CATEGORY_MIN_HR)
        & g["ml_bucket"].isin(LOCK_ML_BUCKETS)
    )
    return g


def mark_lock_candidates(df: pd.DataFrame, cat: pd.DataFrame) -> pd.DataFrame:
    valid_keys = set(cat.loc[cat["validated"], "category_key"])
    base = df[
        (df["ml_prob"] >= LOCK_MIN_ML)
        & (df["abs_edge"] >= LOCK_MIN_ABS_EDGE)
        & df["abs_edge"].notna()
    ].copy()
    base["is_lock"] = base["category_key"].isin(valid_keys)
    return base


def qualify_categories_from_history(past: pd.DataFrame, *, min_n: int) -> set[str]:
    """Point-in-time category keys from history strictly before slate date D."""
    if past.empty:
        return set()
    g = (
        past.groupby(["sport", "direction", "ml_bucket"], dropna=False)
        .agg(n=("hit", "count"), hit_rate=("hit", "mean"))
        .reset_index()
    )
    g["category_key"] = g["sport"] + "|" + g["direction"] + "|" + g["ml_bucket"]
    ok = g[
        (g["n"] >= min_n)
        & (g["hit_rate"] >= LOCK_CATEGORY_MIN_HR)
        & g["ml_bucket"].isin(LOCK_ML_BUCKETS)
    ]
    return set(ok["category_key"].astype(str))


def select_daily_locks_from_pool(pool: pd.DataFrame, n: int) -> list[dict]:
    if pool.empty:
        return []
    work = pool.sort_values(["ml_prob", "abs_edge"], ascending=[False, False])
    chosen: list[dict] = []
    players: set[str] = set()
    for _, row in work.iterrows():
        pk = str(row.get("player_key", "")).strip()
        if not pk or pk in players:
            continue
        chosen.append(row.to_dict())
        players.add(pk)
        if len(chosen) >= n:
            break
    return chosen if len(chosen) == n else []


def select_daily_locks(day: pd.DataFrame, n: int) -> list[dict]:
    pool = day[day["is_lock"]].copy()
    return select_daily_locks_from_pool(pool, n)


def run_daily_backtest(locks: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    dates = sorted(locks["file_date"].unique())
    for n in DAILY_NS:
        built = 0
        all_hits = 0
        leg_hits: list[int] = []
        leg_hrs: list[float] = []
        for date in dates:
            day = locks[locks["file_date"] == date]
            legs = select_daily_locks(day, n)
            if len(legs) < n:
                rows.append(
                    {
                        "section": "daily_ticket",
                        "date": date,
                        "n_legs": n,
                        "built": 0,
                        "legs_hit": 0,
                        "all_hit": 0,
                        "leg_hr": None,
                        "reason": "insufficient_locks",
                    }
                )
                continue
            hits = [int(l["hit"]) for l in legs]
            built += 1
            lh = sum(hits)
            leg_hits.append(lh)
            leg_hrs.append(lh / n)
            ah = int(all(hits))
            all_hits += ah
            rows.append(
                {
                    "section": "daily_ticket",
                    "date": date,
                    "n_legs": n,
                    "built": 1,
                    "legs_hit": lh,
                    "all_hit": ah,
                    "leg_hr": round(lh / n, 4),
                    "reason": "",
                    "players": " ; ".join(str(l["player"]) for l in legs),
                }
            )
        n_days = len(dates)
        rows.append(
            {
                "section": "daily_summary",
                "date": "",
                "n_legs": n,
                "built": built,
                "legs_hit": round(sum(leg_hits) / max(built, 1), 2) if built else 0,
                "all_hit": all_hits,
                "leg_hr": round(sum(leg_hrs) / max(built, 1), 4) if built else None,
                "reason": "",
                "n_days": n_days,
                "all_hit_pct": round(100 * all_hits / max(built, 1), 1) if built else 0,
            }
        )
    return pd.DataFrame(rows)


def print_daily_summary(daily: pd.DataFrame, *, title: str = "STEP 3 — DAILY TOP-N LOCK TICKETS") -> None:
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)
    summ = daily[daily["section"] == "daily_summary"].sort_values("n_legs")
    for _, r in summ.iterrows():
        n = int(r["n_legs"])
        built = int(r["built"])
        if built == 0:
            print(f"\n  N={n}: no tickets built")
            continue
        all_hit_days = int(r["all_hit"])
        avg_legs = float(r["legs_hit"])
        leg_hr = float(r["leg_hr"]) if pd.notna(r["leg_hr"]) else 0.0
        n_days = int(r.get("n_days") or built)
        print(f"\n  N={n}:")
        print(f"    Slates: {n_days}  |  Built: {built} ({100*built/max(n_days,1):.0f}%)")
        print(f"    All-{n} hit: {all_hit_days}/{built} = {100*all_hit_days/built:.1f}%")
        print(f"    Avg legs hit: {avg_legs:.2f} / {n}")
        print(f"    Avg leg HR:   {100*leg_hr:.1f}%")


def run_walk_forward(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Chronological OOS lock tickets: categories qualified from past only.
    Returns (daily_rows, category_day_rows, category_tenure_rows).
    """
    dates = sorted(df["file_date"].unique())
    daily_rows: list[dict] = []
    cat_day_rows: list[dict] = []
    cat_days_qualified: dict[str, int] = {}
    cat_first_qualified: dict[str, str] = {}

    # Per-N accumulators for summary
    summary_acc: dict[int, dict[str, list]] = {
        n: {"built": 0, "all_hit": 0, "leg_hits": [], "leg_hrs": [], "eligible_days": 0}
        for n in DAILY_NS
    }

    for i, date in enumerate(dates):
        past_dates = dates[:i]
        if len(past_dates) < WF_MIN_PAST_DATES:
            continue

        past = df[df["file_date"].isin(past_dates)]
        qualified = qualify_categories_from_history(past, min_n=WF_MIN_CATEGORY_N)

        for ck in qualified:
            if ck not in cat_first_qualified:
                cat_first_qualified[ck] = date
            cat_days_qualified[ck] = cat_days_qualified.get(ck, 0) + 1

        cat_day_rows.append(
            {
                "section": "wf_category_day",
                "wf_date": date,
                "wf_n_qualified": len(qualified),
                "wf_qualified_categories": ";".join(sorted(qualified)),
            }
        )

        day = df[df["file_date"] == date]
        pool = day[
            (day["ml_prob"] >= LOCK_MIN_ML)
            & (day["abs_edge"] >= LOCK_MIN_ABS_EDGE)
            & day["abs_edge"].notna()
            & day["category_key"].isin(qualified)
        ].copy()

        for n in DAILY_NS:
            summary_acc[n]["eligible_days"] += 1
            legs = select_daily_locks_from_pool(pool, n)
            if len(legs) < n:
                daily_rows.append(
                    {
                        "section": "wf_daily_ticket",
                        "wf_date": date,
                        "wf_n_legs": n,
                        "wf_built": 0,
                        "wf_legs_hit": 0,
                        "wf_all_hit": 0,
                        "wf_leg_hr": None,
                        "wf_reason": "insufficient_locks",
                        "wf_n_qualified": len(qualified),
                    }
                )
                continue

            hits = [int(l["hit"]) for l in legs]
            lh = sum(hits)
            ah = int(all(hits))
            leg_hr = lh / n
            summary_acc[n]["built"] += 1
            summary_acc[n]["all_hit"] += ah
            summary_acc[n]["leg_hits"].append(lh)
            summary_acc[n]["leg_hrs"].append(leg_hr)

            daily_rows.append(
                {
                    "section": "wf_daily_ticket",
                    "wf_date": date,
                    "wf_n_legs": n,
                    "wf_built": 1,
                    "wf_legs_hit": lh,
                    "wf_all_hit": ah,
                    "wf_leg_hr": round(leg_hr, 4),
                    "wf_reason": "",
                    "wf_n_qualified": len(qualified),
                    "wf_players": " ; ".join(str(l["player"]) for l in legs),
                    "wf_categories_used": ";".join(sorted({str(l["category_key"]) for l in legs})),
                }
            )

    for n in DAILY_NS:
        acc = summary_acc[n]
        built = acc["built"]
        eligible = acc["eligible_days"]
        daily_rows.append(
            {
                "section": "wf_daily_summary",
                "wf_date": "",
                "wf_n_legs": n,
                "wf_built": built,
                "wf_legs_hit": round(sum(acc["leg_hits"]) / max(built, 1), 2) if built else 0,
                "wf_all_hit": acc["all_hit"],
                "wf_leg_hr": round(sum(acc["leg_hrs"]) / max(built, 1), 4) if built else None,
                "wf_n_days": eligible,
                "wf_all_hit_pct": round(100 * acc["all_hit"] / max(built, 1), 1) if built else 0,
            }
        )

    tenure_rows: list[dict] = []
    for ck, first_d in sorted(cat_first_qualified.items(), key=lambda x: x[1]):
        sp, dr, bucket = ck.split("|", 2)
        tenure_rows.append(
            {
                "section": "wf_category_tenure",
                "wf_category_key": ck,
                "wf_sport": sp,
                "wf_direction": dr,
                "wf_ml_bucket": bucket,
                "wf_first_qualified_date": first_d,
                "wf_days_qualified": cat_days_qualified.get(ck, 0),
            }
        )

    return pd.DataFrame(daily_rows), pd.DataFrame(cat_day_rows), pd.DataFrame(tenure_rows)


def print_walk_forward_summary(wf_daily: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("  WALK-FORWARD — DAILY TOP-N LOCK TICKETS (OOS categories)")
    print("=" * 72)
    print(
        f"  Gate: >= {WF_MIN_PAST_DATES} past slate dates; category n>={WF_MIN_CATEGORY_N}, "
        f"HR>={100*LOCK_CATEGORY_MIN_HR:.0f}%"
    )
    summ = wf_daily[wf_daily["section"] == "wf_daily_summary"].sort_values("wf_n_legs")
    print(f"\n{'N':>3} | {'days_built':>10} | {'all_hit%':>9} | {'avg_legs_hit':>12} | {'avg_leg_HR':>10}")
    print("-" * 56)
    for _, r in summ.iterrows():
        n = int(r["wf_n_legs"])
        built = int(r["wf_built"])
        if built == 0:
            print(f"{n:>3} | {'0':>10} | {'—':>9} | {'—':>12} | {'—':>10}")
            continue
        all_hit_pct = 100 * int(r["wf_all_hit"]) / built
        avg_legs = float(r["wf_legs_hit"])
        leg_hr = 100 * float(r["wf_leg_hr"]) if pd.notna(r["wf_leg_hr"]) else 0.0
        print(
            f"{n:>3} | {built:>10} | {all_hit_pct:>8.1f}% | {avg_legs:>11.2f} | {leg_hr:>9.1f}%"
        )


def print_walk_forward_categories(wf_tenure: pd.DataFrame, wf_cat_day: pd.DataFrame) -> None:
    print("\n--- Walk-forward category tenure (days qualified once first seen) ---")
    if wf_tenure.empty:
        print("  (none qualified)")
        return
    for _, r in wf_tenure.sort_values("wf_days_qualified", ascending=False).head(20).iterrows():
        print(
            f"  {r['wf_sport']:<8} {r['wf_direction']:<6} {r['wf_ml_bucket']:<10} "
            f"first={r['wf_first_qualified_date']}  days_qualified={int(r['wf_days_qualified'])}"
        )

    if not wf_cat_day.empty:
        recent = wf_cat_day.tail(5)
        print("\n--- Last 5 slate dates: qualified categories ---")
        for _, r in recent.iterrows():
            cats = str(r.get("wf_qualified_categories", "") or "")
            preview = cats[:120] + ("…" if len(cats) > 120 else "")
            print(f"  {r['wf_date']}: n={int(r['wf_n_qualified'])}  {preview}")


def category_display_key(category_key: str) -> str:
    parts = str(category_key).split("|")
    if len(parts) != 3:
        return category_key
    return f"{parts[0]} {parts[1]}"


def combo_key_from_legs(legs: list[dict]) -> str:
    labels = sorted(category_display_key(str(l.get("category_key", ""))) for l in legs)
    return " + ".join(labels)


def run_locks_2leg_walk_forward(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    OOS 2-leg lock ticket per slate: top 2 WF-qualified legs, no cross-sport requirement.
    Returns (daily_rows, sport_mix_rows, combo_freq_rows).
    """
    dates = sorted(df["file_date"].unique())
    daily_rows: list[dict] = []
    built_tickets: list[dict] = []

    eligible_days = 0
    built = 0
    all_hit_count = 0
    leg_hits: list[int] = []
    leg_hrs: list[float] = []

    for i, date in enumerate(dates):
        past_dates = dates[:i]
        if len(past_dates) < WF_MIN_PAST_DATES:
            continue
        eligible_days += 1

        past = df[df["file_date"].isin(past_dates)]
        qualified = qualify_categories_from_history(past, min_n=WF_MIN_CATEGORY_N)

        day = df[df["file_date"] == date]
        pool = day[
            (day["ml_prob"] >= LOCK_MIN_ML)
            & (day["abs_edge"] >= LOCK_MIN_ABS_EDGE)
            & day["abs_edge"].notna()
            & day["category_key"].isin(qualified)
        ].copy()

        legs = select_daily_locks_from_pool(pool, 2)
        if len(legs) < 2:
            daily_rows.append(
                {
                    "section": "locks2_daily_ticket",
                    "locks2_date": date,
                    "locks2_built": 0,
                    "locks2_legs_hit": 0,
                    "locks2_all_hit": 0,
                    "locks2_leg_hr": None,
                    "locks2_cross_sport": "",
                    "locks2_reason": "insufficient_locks",
                }
            )
            continue

        hits = [int(l["hit"]) for l in legs]
        lh = sum(hits)
        ah = int(all(hits))
        sports = {str(l.get("sport", "")).strip().upper() for l in legs if l.get("sport")}
        cross = len(sports) >= 2

        built += 1
        all_hit_count += ah
        leg_hits.append(lh)
        leg_hrs.append(lh / 2)

        combo = combo_key_from_legs(legs)
        ticket_rec = {
            "date": date,
            "all_hit": ah,
            "legs_hit": lh,
            "leg_hr": lh / 2,
            "cross_sport": cross,
            "combo": combo,
            "sports": ",".join(sorted(sports)),
        }
        built_tickets.append(ticket_rec)

        daily_rows.append(
            {
                "section": "locks2_daily_ticket",
                "locks2_date": date,
                "locks2_built": 1,
                "locks2_legs_hit": lh,
                "locks2_all_hit": ah,
                "locks2_leg_hr": round(lh / 2, 4),
                "locks2_cross_sport": "yes" if cross else "no",
                "locks2_sports": ticket_rec["sports"],
                "locks2_combo": combo,
                "locks2_players": " ; ".join(str(l["player"]) for l in legs),
                "locks2_categories": ";".join(sorted(str(l["category_key"]) for l in legs)),
                "locks2_reason": "",
            }
        )

    build_rate = 100 * built / max(eligible_days, 1)
    daily_rows.append(
        {
            "section": "locks2_summary",
            "locks2_date": "",
            "locks2_built": built,
            "locks2_legs_hit": round(sum(leg_hits) / max(built, 1), 2) if built else 0,
            "locks2_all_hit": all_hit_count,
            "locks2_leg_hr": round(sum(leg_hrs) / max(built, 1), 4) if built else None,
            "locks2_all_hit_pct": round(100 * all_hit_count / max(built, 1), 1) if built else 0,
            "locks2_eligible_days": eligible_days,
            "locks2_build_rate_pct": round(build_rate, 1),
        }
    )

    sport_mix_rows: list[dict] = []
    if built_tickets:
        for label, pred in (("same_sport", lambda t: not t["cross_sport"]), ("cross_sport", lambda t: t["cross_sport"])):
            sub = [t for t in built_tickets if pred(t)]
            if not sub:
                sport_mix_rows.append(
                    {
                        "section": "locks2_sport_mix",
                        "locks2_mix": label,
                        "locks2_days": 0,
                        "locks2_pct_of_built": 0.0,
                        "locks2_all_hit_pct": None,
                        "locks2_avg_legs_hit": None,
                        "locks2_avg_leg_hr": None,
                    }
                )
                continue
            n_sub = len(sub)
            sport_mix_rows.append(
                {
                    "section": "locks2_sport_mix",
                    "locks2_mix": label,
                    "locks2_days": n_sub,
                    "locks2_pct_of_built": round(100 * n_sub / built, 1),
                    "locks2_all_hit_pct": round(100 * sum(t["all_hit"] for t in sub) / n_sub, 1),
                    "locks2_avg_legs_hit": round(sum(t["legs_hit"] for t in sub) / n_sub, 2),
                    "locks2_avg_leg_hr": round(sum(t["leg_hr"] for t in sub) / n_sub, 4),
                }
            )

    combo_counts = Counter(t["combo"] for t in built_tickets)
    combo_rows: list[dict] = []
    for combo, cnt in combo_counts.most_common(15):
        sub = [t for t in built_tickets if t["combo"] == combo]
        combo_rows.append(
            {
                "section": "locks2_combo_freq",
                "locks2_combo": combo,
                "locks2_days": cnt,
                "locks2_pct_of_built": round(100 * cnt / max(built, 1), 1),
                "locks2_all_hit_pct": round(100 * sum(t["all_hit"] for t in sub) / len(sub), 1),
                "locks2_avg_leg_hr": round(sum(t["leg_hr"] for t in sub) / len(sub), 4),
            }
        )

    return pd.DataFrame(daily_rows), pd.DataFrame(sport_mix_rows), pd.DataFrame(combo_rows)


def print_locks_2leg_summary(
    locks2_daily: pd.DataFrame,
    locks2_mix: pd.DataFrame,
    locks2_combo: pd.DataFrame,
) -> None:
    print("\n" + "=" * 72)
    print("  LOCKS 2-LEG WALK-FORWARD (OOS categories, no cross-sport requirement)")
    print("=" * 72)
    print(
        f"  Gate: >= {WF_MIN_PAST_DATES} past slate dates; category n>={WF_MIN_CATEGORY_N}, "
        f"HR>={100*LOCK_CATEGORY_MIN_HR:.0f}%"
    )

    summ = locks2_daily[locks2_daily["section"] == "locks2_summary"]
    if summ.empty:
        print("  (no eligible days)")
        return
    r = summ.iloc[0]
    built = int(r["locks2_built"])
    eligible = int(r["locks2_eligible_days"])
    all_hit_pct = float(r["locks2_all_hit_pct"]) if pd.notna(r["locks2_all_hit_pct"]) else 0.0
    avg_legs = float(r["locks2_legs_hit"]) if pd.notna(r["locks2_legs_hit"]) else 0.0
    leg_hr = 100 * float(r["locks2_leg_hr"]) if pd.notna(r["locks2_leg_hr"]) else 0.0
    build_rate = float(r["locks2_build_rate_pct"])

    print(f"\n  days_built:     {built} / {eligible} eligible ({build_rate:.0f}% build rate)")
    print(f"  all_2_hit:      {int(r['locks2_all_hit'])}/{built} = {all_hit_pct:.1f}%")
    print(f"  avg_legs_hit:   {avg_legs:.2f} / 2")
    print(f"  avg_leg_HR:     {leg_hr:.1f}%")

    print("\n  Compare to X-Sport Bracket (in-sample): 37.1% all-2-hit, 36% build rate")

    print("\n--- Same-sport vs cross-sport (built days only) ---")
    if locks2_mix.empty:
        print("  (none)")
    else:
        print(f"  {'Mix':<14} {'days':>6} {'%built':>8} {'all_hit%':>10} {'avg_legs':>9} {'leg_HR%':>9}")
        print("  " + "-" * 58)
        for _, m in locks2_mix.iterrows():
            ah = m["locks2_all_hit_pct"]
            ah_s = f"{float(ah):.1f}%" if pd.notna(ah) else "—"
            al = m["locks2_avg_legs_hit"]
            al_s = f"{float(al):.2f}" if pd.notna(al) else "—"
            hr = m["locks2_avg_leg_hr"]
            hr_s = f"{100*float(hr):.1f}%" if pd.notna(hr) else "—"
            print(
                f"  {str(m['locks2_mix']):<14} {int(m['locks2_days']):>6} "
                f"{float(m['locks2_pct_of_built']):>7.1f}% {ah_s:>10} {al_s:>9} {hr_s:>9}"
            )

    print("\n--- Top category combos on built days ---")
    if locks2_combo.empty:
        print("  (none)")
    else:
        for _, c in locks2_combo.head(10).iterrows():
            print(
                f"  {int(c['locks2_days']):>3}x ({float(c['locks2_pct_of_built']):.0f}%)  "
                f"all_hit={float(c['locks2_all_hit_pct']):.0f}%  "
                f"leg_HR={100*float(c['locks2_avg_leg_hr']):.0f}%  | {c['locks2_combo']}"
            )


def _count_selectable_pool(pool: pd.DataFrame) -> int:
    if pool.empty:
        return 0
    return int(pool["player_key"].nunique())


def run_best_locks_walk_forward(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Unified best-locks ticket: global WF pool, top N legs (N=2..6), all sports.
    Returns (daily, summary, sport_composition, sport_hit_rate, pool_distribution).
    """
    dates = sorted(df["file_date"].unique())
    daily_rows: list[dict] = []
    six_leg_rows: list[dict] = []

    summary_acc: dict[int, dict] = {
        n: {"built": 0, "all_hit": 0, "leg_hits": [], "leg_hrs": [], "eligible_days": 0}
        for n in BEST_LOCKS_NS
    }
    sport_slot_counts: dict[int, Counter[str]] = {n: Counter() for n in BEST_LOCKS_NS}
    sport_leg_stats: dict[int, dict[str, dict[str, int]]] = {
        n: defaultdict(lambda: {"hits": 0, "n": 0}) for n in BEST_LOCKS_NS
    }
    pool_sizes: list[int] = []

    for i, date in enumerate(dates):
        past_dates = dates[:i]
        if len(past_dates) < WF_MIN_PAST_DATES:
            continue

        past = df[df["file_date"].isin(past_dates)]
        qualified = qualify_categories_from_history(past, min_n=WF_MIN_CATEGORY_N)

        day = df[df["file_date"] == date]
        pool = day[
            (day["ml_prob"] >= LOCK_MIN_ML)
            & (day["abs_edge"] >= LOCK_MIN_ABS_EDGE)
            & day["abs_edge"].notna()
            & day["category_key"].isin(qualified)
        ].copy()

        selectable = _count_selectable_pool(pool)
        pool_sizes.append(selectable)

        for n in BEST_LOCKS_NS:
            summary_acc[n]["eligible_days"] += 1
            legs = select_daily_locks_from_pool(pool, n)
            if len(legs) < n:
                daily_rows.append(
                    {
                        "section": "best_locks_daily_ticket",
                        "best_locks_date": date,
                        "best_locks_n_legs": n,
                        "best_locks_built": 0,
                        "best_locks_legs_available": selectable,
                        "best_locks_legs_hit": 0,
                        "best_locks_all_hit": 0,
                        "best_locks_leg_hr": None,
                        "best_locks_reason": "insufficient_locks",
                    }
                )
                continue

            hits = [int(l["hit"]) for l in legs]
            lh = sum(hits)
            ah = int(all(hits))
            leg_hr = lh / n
            summary_acc[n]["built"] += 1
            summary_acc[n]["all_hit"] += ah
            summary_acc[n]["leg_hits"].append(lh)
            summary_acc[n]["leg_hrs"].append(leg_hr)

            sports_used = [str(l.get("sport", "")).strip().upper() for l in legs]
            for sp, hit in zip(sports_used, hits):
                sport_slot_counts[n][sp] += 1
                sport_leg_stats[n][sp]["hits"] += hit
                sport_leg_stats[n][sp]["n"] += 1

            daily_rows.append(
                {
                    "section": "best_locks_daily_ticket",
                    "best_locks_date": date,
                    "best_locks_n_legs": n,
                    "best_locks_built": 1,
                    "best_locks_legs_available": selectable,
                    "best_locks_legs_hit": lh,
                    "best_locks_all_hit": ah,
                    "best_locks_leg_hr": round(leg_hr, 4),
                    "best_locks_reason": "",
                    "best_locks_sports": ",".join(sorted(set(sports_used))),
                    "best_locks_players": " ; ".join(str(l["player"]) for l in legs),
                    "best_locks_categories": ";".join(sorted(str(l["category_key"]) for l in legs)),
                }
            )

            if n == 6:
                mix = Counter(sports_used)
                six_leg_rows.append(
                    {
                        "section": "best_locks_six_leg_day",
                        "best_locks_date": date,
                        "best_locks_n_legs": 6,
                        "best_locks_all_hit": ah,
                        "best_locks_leg_hr": round(leg_hr, 4),
                        "best_locks_legs_available": selectable,
                        "best_locks_sport_mix": ";".join(f"{sp}:{mix[sp]}" for sp in sorted(mix)),
                        "best_locks_sports": ",".join(sorted(mix)),
                    }
                )

    summary_rows: list[dict] = []
    for n in BEST_LOCKS_NS:
        acc = summary_acc[n]
        built = acc["built"]
        eligible = acc["eligible_days"]
        build_rate = round(100 * built / max(eligible, 1), 1)
        summary_rows.append(
            {
                "section": "best_locks_summary",
                "best_locks_n_legs": n,
                "best_locks_built": built,
                "best_locks_eligible_days": eligible,
                "best_locks_build_rate_pct": build_rate,
                "best_locks_legs_hit": round(sum(acc["leg_hits"]) / max(built, 1), 2) if built else 0,
                "best_locks_all_hit": acc["all_hit"],
                "best_locks_all_hit_pct": round(100 * acc["all_hit"] / max(built, 1), 1) if built else 0,
                "best_locks_leg_hr": round(sum(acc["leg_hrs"]) / max(built, 1), 4) if built else None,
            }
        )

    composition_rows: list[dict] = []
    for n in BEST_LOCKS_NS:
        built = summary_acc[n]["built"]
        total_slots = built * n
        if total_slots <= 0:
            continue
        for sp, cnt in sport_slot_counts[n].most_common():
            composition_rows.append(
                {
                    "section": "best_locks_sport_composition",
                    "best_locks_n_legs": n,
                    "best_locks_sport": sp,
                    "best_locks_slot_count": cnt,
                    "best_locks_slot_pct": round(100 * cnt / total_slots, 1),
                }
            )

    hit_rate_rows: list[dict] = []
    for n in BEST_LOCKS_NS:
        for sp in sorted(sport_leg_stats[n]):
            st = sport_leg_stats[n][sp]
            if st["n"] <= 0:
                continue
            hit_rate_rows.append(
                {
                    "section": "best_locks_sport_hit_rate",
                    "best_locks_n_legs": n,
                    "best_locks_sport": sp,
                    "best_locks_leg_n": st["n"],
                    "best_locks_leg_hit_rate": round(st["hits"] / st["n"], 4),
                }
            )

    pool_rows: list[dict] = []
    if pool_sizes:
        arr = np.array(pool_sizes, dtype=float)
        pool_rows.append(
            {
                "section": "best_locks_pool_distribution",
                "best_locks_eligible_days": len(pool_sizes),
                "best_locks_pool_min": int(arr.min()),
                "best_locks_pool_median": float(np.median(arr)),
                "best_locks_pool_max": int(arr.max()),
                "best_locks_pool_p25": float(np.percentile(arr, 25)),
                "best_locks_pool_p75": float(np.percentile(arr, 75)),
                "best_locks_days_ge_2": int((arr >= 2).sum()),
                "best_locks_days_ge_3": int((arr >= 3).sum()),
                "best_locks_days_ge_4": int((arr >= 4).sum()),
                "best_locks_days_ge_5": int((arr >= 5).sum()),
                "best_locks_days_ge_6": int((arr >= 6).sum()),
            }
        )

    return (
        pd.DataFrame(daily_rows + summary_rows),
        pd.DataFrame(composition_rows),
        pd.DataFrame(hit_rate_rows),
        pd.DataFrame(pool_rows),
        pd.DataFrame(six_leg_rows),
    )


def print_best_locks_summary(
    best_daily: pd.DataFrame,
    best_composition: pd.DataFrame,
    best_hit_rate: pd.DataFrame,
    best_pool: pd.DataFrame,
    best_six_leg: pd.DataFrame,
) -> None:
    print("\n" + "=" * 80)
    print("  BEST LOCKS UNIFIED TICKET — walk-forward, global pool, N=2..6")
    print("=" * 80)
    print(
        f"  Gate: >= {WF_MIN_PAST_DATES} past slate dates; category n>={WF_MIN_CATEGORY_N}, "
        f"HR>={100*LOCK_CATEGORY_MIN_HR:.0f}%"
    )

    summ = best_daily[best_daily["section"] == "best_locks_summary"].sort_values("best_locks_n_legs")
    print(
        f"\n{'N':>3} | {'days_built':>10} | {'all_hit%':>9} | {'avg_legs_hit':>12} | "
        f"{'avg_leg_HR':>10} | {'build_rate':>10}"
    )
    print("-" * 68)
    for _, r in summ.iterrows():
        n = int(r["best_locks_n_legs"])
        built = int(r["best_locks_built"])
        if built == 0:
            print(f"{n:>3} | {'0':>10} | {'—':>9} | {'—':>12} | {'—':>10} | {'—':>10}")
            continue
        all_hit_pct = float(r["best_locks_all_hit_pct"])
        avg_legs = float(r["best_locks_legs_hit"])
        leg_hr = 100 * float(r["best_locks_leg_hr"]) if pd.notna(r["best_locks_leg_hr"]) else 0.0
        build_rate = float(r["best_locks_build_rate_pct"])
        print(
            f"{n:>3} | {built:>10} | {all_hit_pct:>8.1f}% | {avg_legs:>11.2f} | "
            f"{leg_hr:>9.1f}% | {build_rate:>9.0f}%"
        )

    if not best_pool.empty:
        p = best_pool.iloc[0]
        print("\n--- Qualified legs available per day (unique players) ---")
        print(
            f"  Eligible days: {int(p['best_locks_eligible_days'])}  |  "
            f"min={int(p['best_locks_pool_min'])}  median={float(p['best_locks_pool_median']):.0f}  "
            f"max={int(p['best_locks_pool_max'])}  "
            f"p25={float(p['best_locks_pool_p25']):.0f}  p75={float(p['best_locks_pool_p75']):.0f}"
        )
        print(
            f"  Days with >=N legs:  N>=2: {int(p['best_locks_days_ge_2'])}  "
            f"N>=3: {int(p['best_locks_days_ge_3'])}  N>=4: {int(p['best_locks_days_ge_4'])}  "
            f"N>=5: {int(p['best_locks_days_ge_5'])}  N>=6: {int(p['best_locks_days_ge_6'])}"
        )

    if not best_composition.empty:
        print("\n--- Sport composition of top-N slots (% of built ticket legs) ---")
        for n in BEST_LOCKS_NS:
            sub = best_composition[best_composition["best_locks_n_legs"] == n]
            if sub.empty:
                continue
            parts = [f"{r['best_locks_sport']} {float(r['best_locks_slot_pct']):.0f}%" for _, r in sub.iterrows()]
            print(f"  N={n}: {', '.join(parts)}")

    if not best_hit_rate.empty:
        print("\n--- Leg hit rate by sport within top-N pool ---")
        for n in BEST_LOCKS_NS:
            sub = best_hit_rate[best_hit_rate["best_locks_n_legs"] == n].sort_values(
                "best_locks_leg_hit_rate", ascending=False
            )
            if sub.empty:
                continue
            parts = [
                f"{r['best_locks_sport']} {100*float(r['best_locks_leg_hit_rate']):.0f}%"
                f" (n={int(r['best_locks_leg_n'])})"
                for _, r in sub.iterrows()
            ]
            print(f"  N={n}: {', '.join(parts)}")

    if not best_six_leg.empty:
        built6 = best_six_leg[best_six_leg["section"] == "best_locks_six_leg_day"]
        if not built6.empty:
            all_hit6 = int(built6["best_locks_all_hit"].sum())
            print(f"\n--- Days where all 6 slots filled ({len(built6)} days, all-6 hit {all_hit6}/{len(built6)}) ---")
            mix_totals: Counter[str] = Counter()
            for mix_str in built6["best_locks_sport_mix"].dropna():
                for part in str(mix_str).split(";"):
                    if ":" not in part:
                        continue
                    sp, cnt = part.split(":", 1)
                    mix_totals[sp] += int(cnt)
            total_slots = sum(mix_totals.values())
            if total_slots:
                mix_parts = [
                    f"{sp} {100*mix_totals[sp]/total_slots:.0f}%"
                    for sp in sorted(mix_totals, key=mix_totals.get, reverse=True)
                ]
                print(f"  Aggregate sport mix: {', '.join(mix_parts)}")
            for _, r in built6.sort_values("best_locks_date").iterrows():
                ah = "HIT" if int(r["best_locks_all_hit"]) else "miss"
                avail = int(r.get("best_locks_legs_available") or 0)
                print(
                    f"  {r['best_locks_date']}  {ah:<4}  avail={avail:>2}  "
                    f"mix={r['best_locks_sport_mix']}"
                )


def select_additive_locks_from_pool(
    pool: pd.DataFrame,
    threshold: float,
    *,
    min_legs: int = ADDITIVE_MIN_LEGS,
    max_legs: int = ADDITIVE_MAX_LEGS,
) -> list[dict]:
    """Add legs greedily while next leg ml_prob >= threshold; cap at max_legs."""
    if pool.empty:
        return []
    work = pool.sort_values(["ml_prob", "abs_edge"], ascending=[False, False])
    chosen: list[dict] = []
    players: set[str] = set()
    for _, row in work.iterrows():
        pk = str(row.get("player_key", "")).strip()
        if not pk or pk in players:
            continue
        ml = float(row["ml_prob"])
        if chosen and ml < threshold:
            break
        chosen.append(row.to_dict())
        players.add(pk)
        if len(chosen) >= max_legs:
            break
    return chosen if len(chosen) >= min_legs else []


def _build_additive_pool(day: pd.DataFrame, qualified: set[str]) -> pd.DataFrame:
    return day[
        (day["ml_prob"] >= LOCK_MIN_ML)
        & (day["abs_edge"] >= LOCK_MIN_ABS_EDGE)
        & day["abs_edge"].notna()
        & day["category_key"].isin(qualified)
        & ~day["sport"].isin(ADDITIVE_EXCLUDED_SPORTS)
    ].copy()


def run_additive_gated_walk_forward(
    df: pd.DataFrame,
    *,
    thresholds: tuple[float, ...],
    col_prefix: str,
    section_tag: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Gated additive best-locks with configurable thresholds and CSV column prefix.
    Returns (daily+summary rows, length distribution rows, crossover rows).
    """
    p = col_prefix
    dates = sorted(df["file_date"].unique())
    daily_rows: list[dict] = []
    length_rows: list[dict] = []
    crossover_rows: list[dict] = []
    threshold_avg_legs: list[tuple[float, float]] = []

    for threshold in thresholds:
        built = 0
        all_hit_count = 0
        leg_counts: list[int] = []
        leg_hrs: list[float] = []
        length_dist: Counter[int] = Counter()
        length_all_hit: dict[int, list[int]] = defaultdict(list)
        eligible_days = 0

        for i, date in enumerate(dates):
            past_dates = dates[:i]
            if len(past_dates) < WF_MIN_PAST_DATES:
                continue
            eligible_days += 1

            past = df[df["file_date"].isin(past_dates)]
            qualified = qualify_categories_from_history(past, min_n=WF_MIN_CATEGORY_N)
            day = df[df["file_date"] == date]
            pool = _build_additive_pool(day, qualified)

            legs = select_additive_locks_from_pool(pool, threshold)
            if len(legs) < ADDITIVE_MIN_LEGS:
                daily_rows.append(
                    {
                        "section": f"{section_tag}_daily_ticket",
                        f"{p}threshold": threshold,
                        f"{p}date": date,
                        f"{p}built": 0,
                        f"{p}n_legs": 0,
                        f"{p}legs_hit": 0,
                        f"{p}all_hit": 0,
                        f"{p}leg_hr": None,
                        f"{p}reason": "insufficient_locks",
                    }
                )
                continue

            hits = [int(l["hit"]) for l in legs]
            n_legs = len(legs)
            lh = sum(hits)
            ah = int(all(hits))
            leg_hr = lh / n_legs
            built += 1
            all_hit_count += ah
            leg_counts.append(n_legs)
            leg_hrs.append(leg_hr)
            length_dist[n_legs] += 1
            length_all_hit[n_legs].append(ah)

            sports_used = [str(l.get("sport", "")).strip().upper() for l in legs]
            daily_rows.append(
                {
                    "section": f"{section_tag}_daily_ticket",
                    f"{p}threshold": threshold,
                    f"{p}date": date,
                    f"{p}built": 1,
                    f"{p}n_legs": n_legs,
                    f"{p}legs_hit": lh,
                    f"{p}all_hit": ah,
                    f"{p}leg_hr": round(leg_hr, 4),
                    f"{p}reason": "",
                    f"{p}sports": ",".join(sorted(set(sports_used))),
                    f"{p}players": " ; ".join(str(l["player"]) for l in legs),
                    f"{p}categories": ";".join(sorted(str(l["category_key"]) for l in legs)),
                    f"{p}min_ml": round(min(float(l["ml_prob"]) for l in legs), 4),
                    f"{p}max_ml": round(max(float(l["ml_prob"]) for l in legs), 4),
                }
            )

        build_rate = round(100 * built / max(eligible_days, 1), 1)
        avg_legs = round(sum(leg_counts) / max(built, 1), 2) if built else 0.0
        threshold_avg_legs.append((threshold, avg_legs))

        all_2 = length_all_hit.get(2, [])
        all_3 = length_all_hit.get(3, [])
        all_2_hit_pct = round(100 * sum(all_2) / len(all_2), 1) if all_2 else None
        all_3_hit_pct = round(100 * sum(all_3) / len(all_3), 1) if all_3 else None

        daily_rows.append(
            {
                "section": f"{section_tag}_summary",
                f"{p}threshold": threshold,
                f"{p}built": built,
                f"{p}eligible_days": eligible_days,
                f"{p}build_rate_pct": build_rate,
                f"{p}avg_legs_built": avg_legs,
                f"{p}all_hit": all_hit_count,
                f"{p}all_hit_pct": round(100 * all_hit_count / max(built, 1), 1) if built else 0,
                f"{p}avg_leg_hr": round(sum(leg_hrs) / max(built, 1), 4) if built else None,
                f"{p}all_2_hit_pct": all_2_hit_pct,
                f"{p}all_3_hit_pct": all_3_hit_pct,
                f"{p}days_2_legs": len(all_2),
                f"{p}days_3_legs": len(all_3),
            }
        )

        for n_legs in range(ADDITIVE_MIN_LEGS, ADDITIVE_MAX_LEGS + 1):
            cnt = length_dist.get(n_legs, 0)
            length_rows.append(
                {
                    "section": f"{section_tag}_length_distribution",
                    f"{p}threshold": threshold,
                    f"{p}n_legs": n_legs,
                    f"{p}days": cnt,
                    f"{p}pct_of_built": round(100 * cnt / max(built, 1), 1) if built else 0,
                }
            )

    for target in ADDITIVE_AVG_LEGS_CROSSOVERS:
        first_t = None
        for threshold, avg_legs in threshold_avg_legs:
            if avg_legs > 0 and avg_legs < target:
                first_t = threshold
                break
        crossover_rows.append(
            {
                "section": f"{section_tag}_crossover",
                f"{p}avg_legs_target": target,
                f"{p}first_threshold_below": first_t,
            }
        )

    return pd.DataFrame(daily_rows), pd.DataFrame(length_rows), pd.DataFrame(crossover_rows)


def _extract_threshold_avg_pairs(
    daily: pd.DataFrame,
    col_prefix: str,
    section_tag: str,
) -> list[tuple[float, float]]:
    p = col_prefix
    summ = daily[daily["section"] == f"{section_tag}_summary"].sort_values(f"{p}threshold")
    pairs: list[tuple[float, float]] = []
    for _, r in summ.iterrows():
        avg = r.get(f"{p}avg_legs_built")
        if pd.notna(avg) and float(avg) > 0:
            pairs.append((float(r[f"{p}threshold"]), float(avg)))
    return pairs


def build_avg_legs_crossover_rows(
    threshold_avg_pairs: list[tuple[float, float]],
    *,
    col_prefix: str,
    section_tag: str,
) -> pd.DataFrame:
    p = col_prefix
    pairs = sorted(threshold_avg_pairs)
    rows: list[dict] = []
    for target in ADDITIVE_AVG_LEGS_CROSSOVERS:
        first_t = None
        for threshold, avg_legs in pairs:
            if avg_legs < target:
                first_t = threshold
                break
        rows.append(
            {
                "section": f"{section_tag}_crossover",
                f"{p}avg_legs_target": target,
                f"{p}first_threshold_below": first_t,
            }
        )
    return pd.DataFrame(rows)


def run_additive_best_locks_walk_forward(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Standard T grid (0.62–0.70). Returns (daily+summary, length distribution)."""
    daily, lengths, _cross = run_additive_gated_walk_forward(
        df,
        thresholds=ADDITIVE_THRESHOLDS,
        col_prefix="additive_",
        section_tag="additive",
    )
    return daily, lengths


def run_additive_highT_walk_forward(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """High T grid (0.72–0.85). Returns (daily+summary, length distribution, crossovers)."""
    return run_additive_gated_walk_forward(
        df,
        thresholds=ADDITIVE_HIGH_THRESHOLDS,
        col_prefix="additive_highT_",
        section_tag="additive_highT",
    )


def _print_additive_gated_table(
    additive_daily: pd.DataFrame,
    additive_lengths: pd.DataFrame,
    *,
    thresholds: tuple[float, ...],
    col_prefix: str,
    section_tag: str,
    title: str,
    crossover: pd.DataFrame | None = None,
) -> None:
    p = col_prefix
    print("\n" + "=" * 96)
    print(f"  {title}")
    print("=" * 96)
    print(
        f"  Gate: >= {WF_MIN_PAST_DATES} past dates; category n>={WF_MIN_CATEGORY_N}, "
        f"HR>={100*LOCK_CATEGORY_MIN_HR:.0f}%  |  min legs={ADDITIVE_MIN_LEGS}"
    )
    print(f"  Excluded sports: {', '.join(sorted(ADDITIVE_EXCLUDED_SPORTS))}")
    print(
        f"\n  {'T':>5} | {'avg_legs':>8} | {'all_hit%':>8} | {'avg_leg_HR':>10} | "
        f"{'build_rate':>10} | {'all_2_hit%':>10} | {'all_3_hit%':>10} | length_mix"
    )
    print("  " + "-" * 92)

    for threshold in thresholds:
        summ = additive_daily[
            (additive_daily["section"] == f"{section_tag}_summary")
            & (additive_daily[f"{p}threshold"] == threshold)
        ]
        if summ.empty:
            print(f"  {threshold:>5.2f} | {'—':>8} | {'—':>8} | {'—':>10} | {'—':>10} | {'—':>10} | {'—':>10} |")
            continue

        r = summ.iloc[0]
        avg_legs = float(r[f"{p}avg_legs_built"]) if pd.notna(r[f"{p}avg_legs_built"]) else 0.0
        all_hit_pct = float(r[f"{p}all_hit_pct"]) if pd.notna(r[f"{p}all_hit_pct"]) else 0.0
        leg_hr = 100 * float(r[f"{p}avg_leg_hr"]) if pd.notna(r[f"{p}avg_leg_hr"]) else 0.0
        build_rate = float(r[f"{p}build_rate_pct"])
        a2 = r.get(f"{p}all_2_hit_pct")
        a3 = r.get(f"{p}all_3_hit_pct")
        a2_s = f"{float(a2):.1f}%" if pd.notna(a2) else "—"
        a3_s = f"{float(a3):.1f}%" if pd.notna(a3) else "—"

        sub = additive_lengths[
            (additive_lengths[f"{p}threshold"] == threshold)
            & (additive_lengths[f"{p}days"] > 0)
        ].sort_values(f"{p}n_legs")
        mix_parts = [
            f"{int(row[f'{p}n_legs'])}={int(row[f'{p}days'])}d"
            for _, row in sub.iterrows()
        ]
        mix_s = ", ".join(mix_parts) if mix_parts else "—"

        print(
            f"  {threshold:>5.2f} | {avg_legs:>8.2f} | {all_hit_pct:>7.1f}% | {leg_hr:>9.1f}% | "
            f"{build_rate:>9.0f}% | {a2_s:>10} | {a3_s:>10} | {mix_s}"
        )

    if crossover is not None and not crossover.empty:
        print("\n--- avg_legs crossover (first T where avg_legs drops below target) ---")
        for _, row in crossover.iterrows():
            target = float(row[f"{p}avg_legs_target"])
            first_t = row.get(f"{p}first_threshold_below")
            t_s = f"{float(first_t):.2f}" if pd.notna(first_t) else "never"
            print(f"  avg_legs < {target:.1f}: T = {t_s}")


def print_additive_best_locks_summary(
    additive_daily: pd.DataFrame,
    additive_lengths: pd.DataFrame,
) -> None:
    _print_additive_gated_table(
        additive_daily,
        additive_lengths,
        thresholds=ADDITIVE_THRESHOLDS,
        col_prefix="additive_",
        section_tag="additive",
        title="ADDITIVE BEST LOCKS — gated build, exclude NBA/WNBA, cap 6 legs",
    )


def print_additive_highT_summary(
    additive_daily: pd.DataFrame,
    additive_lengths: pd.DataFrame,
    crossover: pd.DataFrame,
) -> None:
    _print_additive_gated_table(
        additive_daily,
        additive_lengths,
        thresholds=ADDITIVE_HIGH_THRESHOLDS,
        col_prefix="additive_highT_",
        section_tag="additive_highT",
        title="ADDITIVE BEST LOCKS — HIGH T EXTENSION (0.72–0.85)",
        crossover=crossover,
    )


def build_sport_direction_calibration(df: pd.DataFrame) -> pd.DataFrame:
    """Step 1: per sport × direction × ml_bucket calibration."""
    rows: list[dict] = []
    for (sp, dr, bucket), g in df.groupby(["sport", "direction", "ml_bucket"], dropna=False):
        if bucket == "below_0.50":
            continue
        hr = float(g["hit"].mean())
        avg_ml = float(g["ml_prob"].mean())
        rows.append(
            {
                "section": "calibration",
                "sport": sp,
                "direction": dr,
                "ml_bucket": bucket,
                "n": len(g),
                "hit_rate": round(hr, 4),
                "avg_ml_prob": round(avg_ml, 4),
                "edge_pp": round((hr - avg_ml) * 100, 2),
            }
        )
    return pd.DataFrame(rows)


def _sport_from_category_key(category_key: str) -> str:
    return str(category_key).split("|", 1)[0]


def _category_hit_rates_from_past(past: pd.DataFrame) -> pd.DataFrame:
    g = (
        past.groupby(["sport", "direction", "ml_bucket"], dropna=False)
        .agg(n=("hit", "count"), hit_rate=("hit", "mean"))
        .reset_index()
    )
    g["category_key"] = g["sport"] + "|" + g["direction"] + "|" + g["ml_bucket"]
    return g


def run_sport_wf_qualification(df: pd.DataFrame) -> pd.DataFrame:
    """Step 2: per-sport walk-forward category qualification summary."""
    dates = sorted(df["file_date"].unique())
    sports = sorted(df["sport"].unique())

    meta: dict[str, dict] = {
        sp: {
            "ever_qualified": False,
            "first_qualified_date": None,
            "peak_hit_rate": 0.0,
            "days_qualified": 0,
            "legs_available": [],
        }
        for sp in sports
    }

    for i, date in enumerate(dates):
        past_dates = dates[:i]
        if len(past_dates) < WF_MIN_PAST_DATES:
            continue

        past = df[df["file_date"].isin(past_dates)]
        cat_stats = _category_hit_rates_from_past(past)
        qualified = qualify_categories_from_history(past, min_n=WF_MIN_CATEGORY_N)
        if not qualified:
            continue

        qual_stats = cat_stats[cat_stats["category_key"].isin(qualified)]
        day = df[df["file_date"] == date]

        for sp in sports:
            sport_keys = {ck for ck in qualified if _sport_from_category_key(ck) == sp}
            if not sport_keys:
                continue

            m = meta[sp]
            m["ever_qualified"] = True
            if m["first_qualified_date"] is None:
                m["first_qualified_date"] = date
            m["days_qualified"] += 1

            sp_stats = qual_stats[qual_stats["sport"] == sp]
            if not sp_stats.empty:
                m["peak_hit_rate"] = max(m["peak_hit_rate"], float(sp_stats["hit_rate"].max()))

            pool = day[
                (day["sport"] == sp)
                & (day["ml_prob"] >= LOCK_MIN_ML)
                & (day["abs_edge"] >= LOCK_MIN_ABS_EDGE)
                & day["abs_edge"].notna()
                & day["category_key"].isin(sport_keys)
            ]
            m["legs_available"].append(len(pool))

    rows: list[dict] = []
    for sp in sports:
        m = meta[sp]
        avg_legs = round(sum(m["legs_available"]) / len(m["legs_available"]), 2) if m["legs_available"] else None
        rows.append(
            {
                "section": "wf_qualification",
                "sport": sp,
                "ever_qualified": int(m["ever_qualified"]),
                "first_qualified_date": m["first_qualified_date"] or "",
                "peak_hit_rate": round(m["peak_hit_rate"], 4) if m["ever_qualified"] else None,
                "total_days_qualified": m["days_qualified"],
                "avg_legs_available_per_day": avg_legs,
            }
        )
    return pd.DataFrame(rows)


def run_sport_locks_2leg_independent(df: pd.DataFrame) -> pd.DataFrame:
    """Step 3: per-sport independent 2-leg WF lock tickets."""
    dates = sorted(df["file_date"].unique())
    sports = sorted(df["sport"].unique())
    rows: list[dict] = []

    for sp in sports:
        built = 0
        all_hit_count = 0
        leg_hrs: list[float] = []
        combos: Counter[str] = Counter()

        for i, date in enumerate(dates):
            past_dates = dates[:i]
            if len(past_dates) < WF_MIN_PAST_DATES:
                continue

            past = df[df["file_date"].isin(past_dates)]
            qualified = qualify_categories_from_history(past, min_n=WF_MIN_CATEGORY_N)
            sport_qualified = {ck for ck in qualified if _sport_from_category_key(ck) == sp}
            if not sport_qualified:
                continue

            day = df[df["file_date"] == date]
            pool = day[
                (day["sport"] == sp)
                & (day["ml_prob"] >= LOCK_MIN_ML)
                & (day["abs_edge"] >= LOCK_MIN_ABS_EDGE)
                & day["abs_edge"].notna()
                & day["category_key"].isin(sport_qualified)
            ].copy()

            legs = select_daily_locks_from_pool(pool, 2)
            if len(legs) < 2:
                continue

            hits = [int(l["hit"]) for l in legs]
            ah = int(all(hits))
            built += 1
            all_hit_count += ah
            leg_hrs.append(sum(hits) / 2)
            combos[combo_key_from_legs(legs)] += 1

        most_common = combos.most_common(1)[0][0] if combos else ""
        rows.append(
            {
                "section": "locks2_by_sport",
                "sport": sp,
                "days_built": built,
                "all_2_hit": all_hit_count,
                "all_2_hit_pct": round(100 * all_hit_count / max(built, 1), 1) if built else None,
                "avg_leg_hr": round(sum(leg_hrs) / max(built, 1), 4) if built else None,
                "most_common_combo": most_common,
            }
        )
    return pd.DataFrame(rows)


def run_sport_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """Full per-sport breakdown: calibration, WF qualification, independent locks2."""
    cal = build_sport_direction_calibration(df)
    wf = run_sport_wf_qualification(df)
    locks2 = run_sport_locks_2leg_independent(df)
    return pd.concat([cal, wf, locks2], ignore_index=True, sort=False)


def print_sport_breakdown_summary(breakdown: pd.DataFrame) -> None:
    wf = breakdown[breakdown["section"] == "wf_qualification"][
        [
            "sport",
            "ever_qualified",
            "first_qualified_date",
            "total_days_qualified",
            "avg_legs_available_per_day",
        ]
    ].copy()
    locks2 = breakdown[breakdown["section"] == "locks2_by_sport"][
        ["sport", "days_built", "all_2_hit_pct", "avg_leg_hr", "most_common_combo"]
    ].copy()
    if wf.empty and locks2.empty:
        print("  (no sport breakdown data)")
        return

    merged = wf.merge(locks2, on="sport", how="outer")
    merged["all_2_hit_pct"] = pd.to_numeric(merged["all_2_hit_pct"], errors="coerce")
    merged = merged.sort_values("all_2_hit_pct", ascending=False, na_position="last")

    print("\n" + "=" * 96)
    print("  SPORT BREAKDOWN — WF qualification + independent 2-leg locks")
    print("=" * 96)
    print(
        f"  WF gate: >= {WF_MIN_PAST_DATES} past dates; category n>={WF_MIN_CATEGORY_N}, "
        f"HR>={100*LOCK_CATEGORY_MIN_HR:.0f}%"
    )
    print(
        f"\n  {'Sport':<8} {'Qual?':>5} {'1stQual':>10} {'DaysQual':>8} {'AvgLegs':>8} "
        f"{'Built':>6} {'All2Hit%':>9} {'LegHR%':>7} {'Top combo'}"
    )
    print("  " + "-" * 92)

    for _, r in merged.iterrows():
        sp = str(r["sport"])
        qual = "yes" if int(r.get("ever_qualified") or 0) else "no"
        first = str(r.get("first_qualified_date") or "")[:10] or "—"
        days_q = int(r.get("total_days_qualified") or 0)
        avg_legs = r.get("avg_legs_available_per_day")
        avg_legs_s = f"{float(avg_legs):.1f}" if pd.notna(avg_legs) else "—"
        built = int(r.get("days_built") or 0)
        ah_pct = r.get("all_2_hit_pct")
        ah_s = f"{float(ah_pct):.1f}%" if pd.notna(ah_pct) and built > 0 else "—"
        leg_hr = r.get("avg_leg_hr")
        leg_hr_s = f"{100*float(leg_hr):.1f}%" if pd.notna(leg_hr) and built > 0 else "—"
        combo = str(r.get("most_common_combo") or "")[:36]
        print(
            f"  {sp:<8} {qual:>5} {first:>10} {days_q:>8} {avg_legs_s:>8} "
            f"{built:>6} {ah_s:>9} {leg_hr_s:>7}  {combo}"
        )

    cal = breakdown[breakdown["section"] == "calibration"]
    if not cal.empty:
        print("\n--- Step 1 sample: best calibrated buckets (n>=30, |edge| sorted) ---")
        big = cal[cal["n"] >= 30].copy()
        big["abs_edge"] = big["edge_pp"].abs()
        for _, r in big.sort_values("abs_edge", ascending=False).head(12).iterrows():
            print(
                f"  {r['sport']:<8} {r['direction']:<6} {r['ml_bucket']:<10} "
                f"n={int(r['n']):>5} hit={100*r['hit_rate']:.1f}% "
                f"ml={100*r['avg_ml_prob']:.1f}% edge={r['edge_pp']:+.1f}pp"
            )


def sport_direction_breakdown(locks: pd.DataFrame) -> pd.DataFrame:
    sub = locks[locks["is_lock"]].copy()
    rows: list[dict] = []
    if sub.empty:
        return pd.DataFrame(rows)
    for (sp, dr), g in sub.groupby(["sport", "direction"]):
        hr = float(g["hit"].mean())
        flagged = len(g) >= FLAG_MIN_N and hr >= FLAG_MIN_HR
        rows.append(
            {
                "section": "sport_direction",
                "scope": "lock_candidates",
                "sport": sp,
                "direction": dr,
                "ml_bucket": "",
                "n": len(g),
                "hit_rate": round(hr, 4),
                "avg_ml_prob": round(float(g["ml_prob"].mean()), 4),
                "implied_edge_pp": round((hr - float(g["ml_prob"].mean())) * 100, 2),
                "flagged": int(flagged),
            }
        )
    return pd.DataFrame(rows)


def print_validated_categories(cat: pd.DataFrame, *, min_category_n: int) -> None:
    print("\n" + "=" * 72)
    print("  STEP 2 — VALIDATED LOCK CATEGORIES (sport × direction × ml_bucket)")
    print("=" * 72)
    val = cat[cat["validated"]].sort_values("hit_rate", ascending=False)
    print(
        "\nNote: category validation uses in-sample sport×direction×bucket HR "
        f"(min n={min_category_n}). Daily ticket stats are exploratory."
    )
    print(
        f"  Categories passing n>={min_category_n} & HR>={100*LOCK_CATEGORY_MIN_HR:.0f}%: {len(val)}"
    )
    for _, r in val.head(25).iterrows():
        print(
            f"    {r['sport']:<8} {r['direction']:<6} {r['ml_bucket']:<10} "
            f"n={int(r['n']):>5} HR={100*r['hit_rate']:.1f}%"
        )


def print_sport_direction_flags(sd: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print(
        f"  STEP 4 — LOCK CANDIDATES BY SPORT × DIRECTION "
        f"(flag: n>={FLAG_MIN_N}, HR>={100*FLAG_MIN_HR:.0f}%)"
    )
    print("=" * 72)
    if sd.empty:
        print("  (no lock candidates)")
        return
    for _, r in sd.sort_values(["flagged", "hit_rate"], ascending=[False, False]).iterrows():
        flag = " *** FLAG" if int(r.get("flagged", 0)) else ""
        print(
            f"  {r['sport']:<8} {r['direction']:<6} n={int(r['n']):>5}  "
            f"HR={100*r['hit_rate']:.1f}%  ml={100*r['avg_ml_prob']:.1f}%  "
            f"edge={r['implied_edge_pp']:+.1f}pp{flag}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="min_date", default="2026-05-06", metavar="DATE")
    ap.add_argument("--all-dates", action="store_true")
    ap.add_argument("--min-category-n", type=int, default=20, help="Min n for in-sample category validation")
    ap.add_argument(
        "--walk-forward",
        action="store_true",
        help="Run OOS walk-forward lock tickets (append wf_* rows to CSV)",
    )
    ap.add_argument(
        "--locks-2leg",
        action="store_true",
        help="Run OOS 2-leg locks walk-forward (append locks2_* rows to CSV)",
    )
    ap.add_argument(
        "--sport-breakdown",
        action="store_true",
        help="Per-sport calibration + WF qualification + independent locks2 (writes locks_sport_breakdown.csv)",
    )
    ap.add_argument(
        "--best-locks",
        action="store_true",
        help="Unified best-locks ticket WF backtest N=2..6 (append best_locks_* rows to CSV)",
    )
    ap.add_argument(
        "--best-locks-gated",
        action="store_true",
        help="Additive gated best-locks WF backtest (append additive_* rows to CSV)",
    )
    args = ap.parse_args()

    min_date = None if args.all_dates else str(args.min_date).strip()[:10]

    print("Loading graded Standard legs…")
    df = load_graded_standard(min_date=min_date)
    if df.empty:
        print("No rows.", file=sys.stderr)
        return 1

    df = add_bucket_columns(df)
    # Analysis focuses on modeled probability >= 0.50 (calibration buckets start there).
    df = df[df["ml_prob"] >= 0.50].copy()
    print(f"Rows (ml_prob >= 0.50): {len(df):,}  dates: {df['file_date'].min()} → {df['file_date'].max()}")
    sports_present = sorted(df["sport"].unique())
    print(f"Sports in archive: {', '.join(sports_present)}")

    best_daily = pd.DataFrame()
    best_composition = pd.DataFrame()
    best_hit_rate = pd.DataFrame()
    best_pool = pd.DataFrame()
    best_six_leg = pd.DataFrame()
    additive_daily = pd.DataFrame()
    additive_lengths = pd.DataFrame()
    additive_highT_daily = pd.DataFrame()
    additive_highT_lengths = pd.DataFrame()
    additive_highT_crossover = pd.DataFrame()

    if args.best_locks:
        print("\nRunning best locks unified ticket backtest…")
        best_daily, best_composition, best_hit_rate, best_pool, best_six_leg = run_best_locks_walk_forward(df)
        print_best_locks_summary(best_daily, best_composition, best_hit_rate, best_pool, best_six_leg)

    if args.best_locks_gated:
        print("\nRunning additive best locks gated backtest…")
        additive_daily, additive_lengths = run_additive_best_locks_walk_forward(df)
        print_additive_best_locks_summary(additive_daily, additive_lengths)
        print("\nRunning additive high-T extension…")
        additive_highT_daily, additive_highT_lengths, _highT_cross = run_additive_highT_walk_forward(df)
        combined_pairs = _extract_threshold_avg_pairs(additive_daily, "additive_", "additive") + _extract_threshold_avg_pairs(
            additive_highT_daily, "additive_highT_", "additive_highT"
        )
        additive_highT_crossover = build_avg_legs_crossover_rows(
            combined_pairs,
            col_prefix="additive_highT_",
            section_tag="additive_highT",
        )
        print_additive_highT_summary(additive_highT_daily, additive_highT_lengths, additive_highT_crossover)

    if args.sport_breakdown:
        print("\nRunning per-sport breakdown…")
        sport_breakdown = run_sport_breakdown(df)
        print_sport_breakdown_summary(sport_breakdown)
        sport_out = _REPO / "data" / "reports" / "locks_sport_breakdown.csv"
        sport_out.parent.mkdir(parents=True, exist_ok=True)
        sport_breakdown.to_csv(sport_out, index=False, encoding="utf-8-sig")
        print(f"\nWrote {sport_out}")
        if not args.walk_forward and not args.locks_2leg and not args.best_locks and not args.best_locks_gated:
            return 0

    _focused_only = not args.walk_forward and not args.locks_2leg and not args.sport_breakdown
    if args.best_locks and _focused_only and not args.best_locks_gated:
        best_parts = [best_daily, best_composition, best_hit_rate, best_pool, best_six_leg]
        best_out = pd.concat(best_parts, ignore_index=True, sort=False)
        out_path = _REPO / "data" / "reports" / "locks_backtest.csv"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        best_out.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\nWrote {out_path}")
        return 0

    if args.best_locks_gated and _focused_only and not args.best_locks:
        additive_parts = [
            additive_daily,
            additive_lengths,
            additive_highT_daily,
            additive_highT_lengths,
            additive_highT_crossover,
        ]
        additive_out = pd.concat(additive_parts, ignore_index=True, sort=False)
        out_path = _REPO / "data" / "reports" / "locks_backtest.csv"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        additive_out.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\nWrote {out_path}")
        return 0

    if args.best_locks and args.best_locks_gated and _focused_only:
        combined_parts = [
            best_daily,
            best_composition,
            best_hit_rate,
            best_pool,
            best_six_leg,
            additive_daily,
            additive_lengths,
            additive_highT_daily,
            additive_highT_lengths,
            additive_highT_crossover,
        ]
        combined_out = pd.concat(combined_parts, ignore_index=True, sort=False)
        out_path = _REPO / "data" / "reports" / "locks_backtest.csv"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        combined_out.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\nWrote {out_path}")
        return 0

    cal = build_calibration(df)
    print_calibration_table(cal)

    cat = category_hit_rates(df, min_n=int(args.min_category_n))
    locks = mark_lock_candidates(df, cat)
    n_lock = int(locks["is_lock"].sum())
    print(f"\nLock candidates (individual + validated category): {n_lock:,} / {len(df):,}")

    print_validated_categories(cat, min_category_n=int(args.min_category_n))

    sd = sport_direction_breakdown(locks)
    print_sport_direction_flags(sd)

    daily = run_daily_backtest(locks)
    print_daily_summary(daily)

    wf_daily = pd.DataFrame()
    wf_cat_day = pd.DataFrame()
    wf_tenure = pd.DataFrame()
    if args.walk_forward:
        print("\nRunning walk-forward validation…")
        wf_daily, wf_cat_day, wf_tenure = run_walk_forward(df)
        print_walk_forward_summary(wf_daily)
        print_walk_forward_categories(wf_tenure, wf_cat_day)

    locks2_daily = pd.DataFrame()
    locks2_mix = pd.DataFrame()
    locks2_combo = pd.DataFrame()
    if args.locks_2leg:
        print("\nRunning locks 2-leg walk-forward…")
        locks2_daily, locks2_mix, locks2_combo = run_locks_2leg_walk_forward(df)
        print_locks_2leg_summary(locks2_daily, locks2_mix, locks2_combo)

    out_cat = cat.rename(columns={"validated": "category_validated"}).copy()
    out_cat["section"] = "category_validation"
    parts = [cal, out_cat, sd, daily]
    if args.walk_forward:
        parts.extend([wf_daily, wf_cat_day, wf_tenure])
    if args.locks_2leg:
        parts.extend([locks2_daily, locks2_mix, locks2_combo])
    if args.best_locks:
        parts.extend([best_daily, best_composition, best_hit_rate, best_pool, best_six_leg])
    if args.best_locks_gated:
        parts.extend([
            additive_daily,
            additive_lengths,
            additive_highT_daily,
            additive_highT_lengths,
            additive_highT_crossover,
        ])
    out_rows = pd.concat(parts, ignore_index=True, sort=False)
    out_path = _REPO / "data" / "reports" / "locks_backtest.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_rows.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
