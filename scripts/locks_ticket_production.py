#!/usr/bin/env python3
"""
Production best-locks ticket: WF-qualified categories, fixed N=3, global rank.

Used by build_ultimate_tickets.py (ticket_group=best_locks).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd

_SCRIPTS = Path(__file__).resolve().parent
_REPO = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import backtest_locks as bl  # noqa: E402
from build_ultimate_tickets import (  # noqa: E402
    bracket_source_path,
    display_sport,
    load_bracket_rows_from_workbook,
    norm_bracket_sport,
)

BEST_LOCKS_N_LEGS = 3
BEST_LOCKS_GROUP = "best_locks"
BEST_LOCKS_WEB_NAME = "Best Locks"

LOCKS_SPORT_LOAD_ORDER = (
    "NBA",
    "NBA1H",
    "NBA1Q",
    "MLB",
    "NHL",
    "SOCCER",
    "TENNIS",
    "WNBA",
    "CBB",
)


def category_display(category_key: str) -> str:
    parts = str(category_key).split("|")
    if len(parts) == 3:
        return f"{parts[0]} {parts[1]} {parts[2]}"
    return str(category_key)


def load_slate_lock_rows(date_str: str) -> pd.DataFrame:
    """Standard legs from step8 workbooks with ml_prob / edge for lock pooling."""
    rows: list[dict[str, Any]] = []
    for sport in LOCKS_SPORT_LOAD_ORDER:
        path = bracket_source_path(sport, date_str)
        if path is None:
            # NHL / TENNIS fallbacks not in bracket_source_path
            path = _locks_extra_source_path(sport, date_str)
        if path is None:
            continue
        rows.extend(load_bracket_rows_from_workbook(path, sport, date_str))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["sport"] = df["sport"].map(norm_bracket_sport)
    df["ml_prob"] = pd.to_numeric(df["ml_prob"], errors="coerce")
    edge = pd.to_numeric(df.get("edge", pd.Series(dtype=float)), errors="coerce")
    if "edge" not in df.columns:
        edge = pd.to_numeric(df.get("abs_edge", pd.Series(dtype=float)), errors="coerce")
    df["abs_edge"] = pd.to_numeric(df.get("abs_edge"), errors="coerce")
    if df["abs_edge"].isna().all() and edge.notna().any():
        df["abs_edge"] = edge.abs()
    df = bl.add_bucket_columns(df)
    return df


def _locks_extra_source_path(sport: str, date_str: str) -> Path | None:
    sp = norm_bracket_sport(sport)
    od = _REPO / "outputs" / date_str
    extra: dict[str, list[Path]] = {
        "NHL": [
            od / "nhl" / "step8_nhl_direction_clean.xlsx",
            od / f"step8_nhl_direction_clean_{date_str}.xlsx",
            _REPO / "Sports" / "NHL" / "outputs" / "step8_nhl_direction_clean.xlsx",
            _REPO / "Sports" / "NHL" / "step8_nhl_direction_clean.xlsx",
        ],
        "TENNIS": [
            od / "tennis" / "step8_tennis_direction_clean.xlsx",
            od / f"step8_tennis_direction_clean_{date_str}.xlsx",
            _REPO / "Sports" / "Tennis" / "step8_tennis_direction_clean.xlsx",
        ],
    }
    for p in extra.get(sp, []):
        if p.is_file():
            return p
    return None


def load_graded_before(date_str: str) -> pd.DataFrame:
    """Graded Standard legs strictly before slate date (for WF category qualification)."""
    df = bl.load_graded_standard(min_date=None)
    if df.empty:
        return df
    df = df[df["file_date"] < date_str].copy()
    df = bl.add_bucket_columns(df)
    return df[df["ml_prob"] >= 0.50].copy()


def wf_context(date_str: str) -> tuple[set[str], int, pd.DataFrame]:
    """
    Returns (qualified_category_keys, n_past_dates, past_graded_df).
    Empty qualified set if history gate not met.
    """
    past = load_graded_before(date_str)
    past_dates = sorted(past["file_date"].unique()) if not past.empty else []
    n_past = len(past_dates)
    if n_past < bl.WF_MIN_PAST_DATES:
        return set(), n_past, past
    qualified = bl.qualify_categories_from_history(past, min_n=bl.WF_MIN_CATEGORY_N)
    return qualified, n_past, past


def build_lock_pool(date_str: str, qualified: set[str]) -> pd.DataFrame:
    slate = load_slate_lock_rows(date_str)
    if slate.empty or not qualified:
        return pd.DataFrame()
    pool = slate[
        (slate["ml_prob"] >= bl.LOCK_MIN_ML)
        & (slate["abs_edge"] >= bl.LOCK_MIN_ABS_EDGE)
        & slate["abs_edge"].notna()
        & slate["category_key"].isin(qualified)
        & ~slate["sport"].isin(bl.ADDITIVE_EXCLUDED_SPORTS)
    ].copy()
    return pool


def _pool_counts_by_sport(pool: pd.DataFrame) -> dict[str, int]:
    if pool.empty:
        return {}
    return pool.groupby("sport").size().sort_values(ascending=False).to_dict()


def build_best_locks_group(date_str: str, *, n_legs: int = BEST_LOCKS_N_LEGS) -> dict[str, Any] | None:
    """
    Fixed N-leg best-locks ticket from WF-qualified global pool.
    Returns tickets_latest-style group dict, or None to skip silently.
    """
    from build_ultimate_tickets import leg_detail_to_jsonable  # noqa: WPS433
    from combined_slate_tickets import compute_ticket_ev  # noqa: WPS433

    qualified, n_past, _past = wf_context(date_str)

    if n_past < bl.WF_MIN_PAST_DATES:
        print(
            f"[BEST LOCKS] Skipped: insufficient WF history "
            f"({n_past} past dates < {bl.WF_MIN_PAST_DATES})"
        )
        return None

    qual_display = sorted(category_display(ck) for ck in qualified)
    print(f"[BEST LOCKS] WF past dates: {n_past} | qualified categories ({len(qualified)}):")
    for label in qual_display:
        print(f"  - {label}")
    if not qualified:
        print("[BEST LOCKS] Skipped: no categories qualified")
        return None

    pool = build_lock_pool(date_str, qualified)
    sport_counts = _pool_counts_by_sport(pool)
    count_parts = ", ".join(f"{sp}={cnt}" for sp, cnt in sport_counts.items())
    print(f"[BEST LOCKS] Pool: {len(pool)} legs" + (f" ({count_parts})" if count_parts else ""))

    legs_raw = bl.select_daily_locks_from_pool(pool, n_legs)
    if len(legs_raw) < n_legs:
        print(f"[BEST LOCKS] Skipped: only {len(legs_raw)} legs available (need {n_legs})")
        return None

    legs: list[dict[str, Any]] = []
    for raw in legs_raw:
        leg = dict(raw)
        leg["sport"] = display_sport(leg.get("sport"))
        ck = str(leg.get("category_key", ""))
        leg["qualified_category"] = category_display(ck)
        try:
            ml = float(leg.get("ml_prob") or 0)
            leg["hit_prob"] = round(min(0.95, ml), 4) if ml > 0 else 0.5
        except (TypeError, ValueError):
            leg["hit_prob"] = 0.5
        legs.append(leg)

    legs_for_ev = [
        {
            "pick_type": "standard",
            "line_distance": float(l.get("line_distance") or 0.0),
            "hit_prob": float(l.get("hit_prob") or 0.5),
        }
        for l in legs
    ]
    ev_result = compute_ticket_ev(legs_for_ev, "power", n_legs)
    p_win = float(ev_result["p_all_win"])
    payout = float(ev_result["first_place_payout"])
    flex_ev = compute_ticket_ev(legs_for_ev, "flex", n_legs)
    flex_payout = float(flex_ev["first_place_payout"])

    leg_strs = [f"{l['player']} {l['prop_type']} {l['line']} {l['direction']}" for l in legs]
    json_legs = leg_detail_to_jsonable(legs)
    for jl, leg in zip(json_legs, legs):
        jl["ml_prob"] = leg.get("ml_prob")
        jl["abs_edge"] = leg.get("abs_edge")
        jl["qualified_category"] = leg.get("qualified_category")
        jl["category_key"] = leg.get("category_key")
        jl["ticket_group"] = BEST_LOCKS_GROUP

    sports = sorted({str(l["sport"]) for l in legs})
    cats = [str(l.get("qualified_category", "")) for l in legs]
    print(
        f"[BEST LOCKS] Built {n_legs}-leg ticket ({' + '.join(cats)}) "
        f"sports={sports} P(win)={p_win * 100:.1f}%"
    )

    ticket = {
        "web_group_name": BEST_LOCKS_WEB_NAME,
        "ticket_group": BEST_LOCKS_GROUP,
        "ticket_id": f"{date_str}|{BEST_LOCKS_WEB_NAME}|1",
        "ticket_no": 1,
        "n_legs": n_legs,
        "legs": json_legs,
        "rows": json_legs,
        "legs_detail": json_legs,
        "legs_text": leg_strs,
        "player_keys": [str(l["player"]).strip() for l in legs],
        "sports": sports,
        "p_win": round(p_win, 4),
        "p_win_pct": round(p_win * 100, 1),
        "payout": round(payout, 2),
        "power_payout": round(payout, 2),
        "flex_payout": round(flex_payout, 2),
        "ev": round(float(ev_result["ev"]), 4),
        "ev_power": round(float(ev_result["ev"]), 4),
        "recommendation": ev_result["recommendation"],
        "pick_types": ["standard"] * n_legs,
        "n_goblins": 0,
        "n_demons": 0,
        "wf_qualified_categories": qual_display,
        "wf_n_past_dates": n_past,
        "wf_pool_size": len(pool),
    }

    return {
        "group_name": BEST_LOCKS_WEB_NAME,
        "n_legs": n_legs,
        "power_payout": ticket["power_payout"],
        "flex_payout": ticket["flex_payout"],
        "tickets": [ticket],
    }
