"""
Shared prop-quality signals for all sports: L10, ml_prob, def_tier, minutes,
cross-book edge, line movement, and graded-history boosts.

Used by combined_slate_tickets pool/filter/ticket sort and sport step8 exports.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.l10_streak_utils import (  # noqa: E402
    compute_l10_streak_label,
    finalize_l10_ui_columns,
)
from utils.defense_tiers import normalize_def_tier_label  # noqa: E402

# Graded-backed ticket scoring constants (all sports).
HOT_L10_BOOST = 0.12
COLD_L10_PENALTY = -0.08
DEMON_OVER_PENALTY = -0.18
WNBA_STD_OVER_D_PENALTY = -0.12


def _direction_series(df: pd.DataFrame) -> pd.Series:
    if "direction" in df.columns:
        return df["direction"].astype(str).str.upper().str.strip()
    if "bet_direction" in df.columns:
        return df["bet_direction"].astype(str).str.upper().str.strip()
    if "final_bet_direction" in df.columns:
        return df["final_bet_direction"].astype(str).str.upper().str.strip()
    return pd.Series("OVER", index=df.index)


def _norm_def_tier_upper(raw: object) -> str:
    base = normalize_def_tier_label(raw)
    return str(base or "").strip().upper()


def ensure_prop_signal_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Canonical l10_*, ml_prob aliases, and streak labels for any sport slate."""
    if df is None or df.empty:
        return df
    out = df.copy()
    line_col = "line" if "line" in out.columns else ("line_score" if "line_score" in out.columns else None)
    if line_col:
        out = finalize_l10_ui_columns(out, line_col=line_col)

    # L5 aliases used by some boards
    if "l5_over" not in out.columns and "last5_over" in out.columns:
        out["l5_over"] = pd.to_numeric(out["last5_over"], errors="coerce")
    if "l5_under" not in out.columns and "last5_under" in out.columns:
        out["l5_under"] = pd.to_numeric(out["last5_under"], errors="coerce")

    if "minutes_tier" not in out.columns and "min_tier" in out.columns:
        out["minutes_tier"] = out["min_tier"]

    if "cross_edge_vs_pp" in out.columns:
        out["cross_edge_vs_pp"] = pd.to_numeric(out["cross_edge_vs_pp"], errors="coerce")
    if "line_movement" in out.columns:
        out["line_movement"] = pd.to_numeric(out["line_movement"], errors="coerce")

    return out


def directional_l10_side_series(df: pd.DataFrame) -> pd.Series:
    """Hit count on the picked side over last 10 games vs today's line."""
    direction = _direction_series(df)
    l10_over = _num_col(df, "l10_over")
    l10_under = _num_col(df, "l10_under")
    return pd.Series(
        np.where(direction.eq("UNDER"), l10_under, l10_over),
        index=df.index,
        dtype=float,
    )


def directional_l10_rate_series(df: pd.DataFrame) -> pd.Series:
    """L10 side hit rate in [0,1] for the picked direction."""
    side = directional_l10_side_series(df)
    gp = _num_col(df, "l10_games_played")
    gp = gp.where(gp.notna() & (gp > 0), side.notna().astype(float) * 10.0)
    gp = gp.clip(lower=1.0)
    return (side / gp).clip(0.0, 1.0)


def l10_streak_for_row(row: dict | pd.Series) -> str | None:
    """HOT / COLD / NEUTRAL from l10 counts and direction."""
    if isinstance(row, pd.Series):
        row = row.to_dict()
    existing = str(row.get("l10_streak") or "").strip().upper()
    if existing in {"HOT", "COLD", "NEUTRAL"}:
        return existing
    direction = str(
        row.get("direction") or row.get("bet_direction") or row.get("final_bet_direction") or "OVER"
    ).strip().upper()
    return compute_l10_streak_label(
        row.get("l10_over"),
        row.get("l10_under"),
        direction,
    )


def l10_streak_series(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=object)
    direction = _direction_series(df)
    l10_over = _num_col(df, "l10_over")
    l10_under = _num_col(df, "l10_under")
    streaks: list[str | None] = []
    for idx in df.index:
        if "l10_streak" in df.columns:
            existing = str(df.at[idx, "l10_streak"] or "").strip().upper()
            if existing in {"HOT", "COLD", "NEUTRAL"}:
                streaks.append(existing)
                continue
        streaks.append(
            compute_l10_streak_label(l10_over.at[idx], l10_under.at[idx], direction.at[idx])
        )
    return pd.Series(streaks, index=df.index, dtype=object).astype(str).str.upper().str.strip()


def row_hot_l10_streak(row: dict | pd.Series) -> bool:
    direction = str(
        row.get("direction") or row.get("bet_direction") or row.get("final_bet_direction") or "OVER"
    ).strip().upper()
    if direction == "UNDER":
        pct = pd.to_numeric(row.get("l10_under_pct"), errors="coerce")
        raw = pd.to_numeric(row.get("l10_under"), errors="coerce")
    else:
        pct = pd.to_numeric(row.get("l10_over_pct"), errors="coerce")
        raw = pd.to_numeric(row.get("l10_over"), errors="coerce")
    if pd.notna(pct) and float(pct) >= 0.70:
        return True
    if pd.notna(raw) and float(raw) >= 7.0:
        return True
    return False


def context_signal_adjustment_series(df: pd.DataFrame) -> pd.Series:
    """
    Soft boost/penalty from L10, ml_prob, def_tier, minutes, cross-book edge,
    and line movement. Same signals as ticket 'rule' sort, usable for all sports.
    """
    if df is None or df.empty:
        return pd.Series(dtype=float)
    out = df
    direction = _direction_series(out)
    over_mask = direction.eq("OVER")
    under_mask = direction.eq("UNDER")

    adj = pd.Series(0.0, index=out.index)

    l5_over = _num_col(out, "l5_over")
    l5_under = _num_col(out, "l5_under")
    side_l5 = np.where(under_mask, l5_under, l5_over)
    adj = adj + np.where(
        pd.isna(side_l5),
        0.0,
        np.where(side_l5 >= 4, 0.06, np.where(side_l5 <= 2, -0.05, 0.0)),
    )

    streak = l10_streak_series(out)
    adj = adj + np.where(streak.eq("HOT"), HOT_L10_BOOST, 0.0)
    adj = adj + np.where(streak.eq("COLD"), COLD_L10_PENALTY, 0.0)

    pick_raw = out.get("pick_type", pd.Series("Standard", index=out.index)).astype(str).str.lower()
    is_demon = pick_raw.str.contains("demon", na=False)
    is_standard = ~pick_raw.str.contains("goblin", na=False) & ~is_demon
    adj = adj + np.where(is_demon & over_mask, DEMON_OVER_PENALTY, 0.0)

    sport_u = out.get("sport", pd.Series("", index=out.index)).astype(str).str.upper().str.strip()
    tier_u = out.get("tier", pd.Series("", index=out.index)).astype(str).str.upper().str.strip()
    adj = adj + np.where(
        sport_u.eq("WNBA") & is_standard & over_mask & tier_u.eq("D"),
        WNBA_STD_OVER_D_PENALTY,
        0.0,
    )

    mlp = _num_col(out, "ml_prob")
    adj = adj + np.where(mlp.notna(), (mlp.clip(0.35, 0.92) - 0.55) * 0.25, 0.0)

    def_tier = out.get("def_tier", pd.Series("", index=out.index)).map(_norm_def_tier_upper)
    adj = adj + np.where(over_mask & def_tier.eq("WEAK"), 0.04, 0.0)
    adj = adj + np.where(over_mask & def_tier.isin(["ABOVE AVG", "ELITE"]), -0.03, 0.0)
    adj = adj + np.where(under_mask & def_tier.isin(["ELITE", "ABOVE AVG"]), 0.04, 0.0)
    adj = adj + np.where(under_mask & def_tier.eq("WEAK"), -0.03, 0.0)

    min_tier = out.get("minutes_tier", out.get("min_tier", pd.Series("", index=out.index)))
    min_tier = min_tier.astype(str).str.upper().str.strip()
    adj = adj + np.where(over_mask & min_tier.isin(["HIGH"]), 0.03, 0.0)
    adj = adj + np.where(over_mask & min_tier.isin(["LOW"]), -0.03, 0.0)
    adj = adj + np.where(under_mask & min_tier.isin(["LOW"]), 0.03, 0.0)
    adj = adj + np.where(under_mask & min_tier.isin(["HIGH"]), -0.03, 0.0)

    cross = _num_col(out, "cross_edge_vs_pp")
    adj = adj + np.where(cross.notna() & (cross > 0.05), np.clip(cross / 20.0, 0.0, 0.05), 0.0)

    lm = _num_col(out, "line_movement")
    # Favorable movement: OVER line dropped (negative) or UNDER line rose (positive).
    fav_move = np.where(
        over_mask,
        lm < -0.25,
        np.where(under_mask, lm > 0.25, False),
    )
    adj = adj + np.where(fav_move, 0.02, 0.0)

    return adj.astype(float)


def _num_col(df: pd.DataFrame, col: str, default: float = np.nan) -> pd.Series:
    if col not in df.columns:
        return pd.Series(default, index=df.index, dtype=float)
    val = df[col]
    if isinstance(val, pd.DataFrame):
        val = val.iloc[:, 0]
    return pd.to_numeric(val, errors="coerce")


def compute_prop_quality_score(df: pd.DataFrame) -> pd.Series:
    """
    Unified [0,1] leg quality score for all sports (Goblin + Standard tickets).
    Weights: hit_rate 24%, ml_prob 18%, edge 16%, rank 14%, L10 12%, L5 sample 8%, tier 8%.
    """
    if df is None or df.empty:
        return pd.Series(dtype=float)

    direction = _direction_series(df)
    is_under = direction.isin({"UNDER", "LOWER"})

    hr = _num_col(df, "hit_rate")
    hr = hr.apply(lambda x: x / 100.0 if pd.notna(x) and abs(float(x)) > 1.0 else x)
    hr = pd.to_numeric(hr, errors="coerce").clip(0.0, 1.0)

    l5o = _num_col(df, "l5_over", 0.0).fillna(0.0)
    l5u = _num_col(df, "l5_under", 0.0).fillna(0.0)
    l5_side = np.maximum(l5o, l5u)
    l5_side_rate = np.clip(l5_side / 5.0, 0.0, 1.0)
    hr = hr.fillna(pd.Series(l5_side_rate, index=df.index))
    hr_eff = pd.Series(np.where(is_under, 1.0 - hr, hr), index=df.index).clip(0.0, 1.0)

    mlp = _num_col(df, "ml_prob", 0.5).clip(0.0, 1.0).fillna(0.5)

    edge_dir = _num_col(df, "edge", 0.0).fillna(0.0)
    edge_dir = pd.Series(np.where(is_under, -edge_dir, edge_dir), index=df.index)
    edge_norm = np.clip(edge_dir.abs() / 15.0, 0.0, 1.0)

    rs = _num_col(df, "rank_score")
    rank_prob = rs.rank(pct=True).fillna(0.5)

    l10_rate = directional_l10_rate_series(df).fillna(l5_side_rate)

    sample_strength = pd.Series(np.clip(l5_side / 5.0, 0.0, 1.0), index=df.index)

    tier_raw = (
        df["tier"].astype(str).str.upper().str.strip()
        if "tier" in df.columns
        else pd.Series("", index=df.index)
    )
    tier_norm = tier_raw.map({"A": 1.00, "B": 0.86, "C": 0.70, "D": 0.45}).fillna(0.55)

    score = (
        0.24 * hr_eff
        + 0.18 * mlp
        + 0.16 * edge_norm
        + 0.14 * rank_prob
        + 0.12 * l10_rate
        + 0.08 * sample_strength
        + 0.08 * tier_norm
    )

    ctx = context_signal_adjustment_series(df)
    score = score + np.clip(ctx, -0.12, 0.12)

    rel = (
        df["reliability_note"].astype(str).str.upper()
        if "reliability_note" in df.columns
        else pd.Series("", index=df.index)
    )
    hs = (
        df["hit_rate_status"].astype(str).str.upper()
        if "hit_rate_status" in df.columns
        else pd.Series("", index=df.index)
    )
    score = score - np.where(rel.str.contains("THIN_SAMPLE_", na=False), 0.08, 0.0)
    score = score - np.where(hs.str.startswith("BLENDED_N"), 0.05, 0.0)

    return pd.Series(np.clip(score, 0.0, 1.0), index=df.index)


def _norm_pick_key(v: object) -> str:
    s = str(v or "").strip().lower()
    if "goblin" in s:
        return "goblin"
    if "demon" in s:
        return "demon"
    return "standard"


def graded_analysis_boost_series(df: pd.DataFrame, ctx: dict[str, Any] | None) -> pd.Series:
    """Graded-history boost (slice priority, players, HOT L10) for all sports."""
    if df is None or df.empty or not ctx:
        return pd.Series(0.0, index=df.index)

    boost = pd.Series(0.0, index=df.index)
    slice_pri = ctx.get("slice_priority") or {}
    top_players = ctx.get("top_players") or set()
    bottom_players = ctx.get("bottom_players") or set()

    sport = df.get("sport", pd.Series("", index=df.index)).astype(str).str.upper().str.strip()
    pick_raw = df.get("pick_type", pd.Series("Standard", index=df.index))
    direction = _direction_series(df)
    tier = df.get("tier", pd.Series("", index=df.index)).astype(str).str.upper().str.strip()
    player_col = df.get("player", pd.Series("", index=df.index)).astype(str)

    for i in df.index:
        pk = _norm_pick_key(pick_raw.at[i])
        sk = (sport.at[i], pk, direction.at[i], tier.at[i])
        pri = slice_pri.get(sk)
        if pri is not None:
            boost.at[i] += max(0.0, 0.15 - 0.01 * float(pri))
        player_key = (player_col.at[i].strip().casefold(), sport.at[i])
        if player_key in top_players:
            boost.at[i] += 0.04
        if player_key in bottom_players:
            boost.at[i] -= 0.06
        row = df.loc[i]
        if row_hot_l10_streak(row.to_dict() if hasattr(row, "to_dict") else dict(row)):
            boost.at[i] += HOT_L10_BOOST
        try:
            ln = float(row.get("line") or row.get("line_score") or 0)
        except (TypeError, ValueError):
            ln = 0.0
        if sk[0] in ("NBA", "NBA1H") and sk[1] == "goblin" and ln >= 3.0:
            boost.at[i] += 0.02

    return boost


def apply_ml_rank_blend(
    out: pd.DataFrame,
    *,
    rank_col: str = "rank_score",
    blend_weight: float = 0.20,
    composite_hr_col: str = "line_hit_rate",
    label: str = "",
) -> pd.DataFrame:
    """
    Fuse ml_prob + composite hit rate into rank_score (WNBA/Tennis/NFL parity with NBA/MLB).
  """
    if out is None or out.empty:
        return out
    df = out.copy()
    rs = pd.to_numeric(df.get(rank_col), errors="coerce").fillna(0.0)
    rs_pct = rs.rank(method="average", pct=True).fillna(0.5)

    ml = pd.to_numeric(df.get("ml_prob"), errors="coerce")
    comp = pd.to_numeric(df.get(composite_hr_col), errors="coerce")
    if comp.isna().all() and "composite_hit_rate" in df.columns:
        comp = pd.to_numeric(df["composite_hit_rate"], errors="coerce")
    comp = comp.fillna(0.5).clip(0.001, 0.999)
    ml = ml.where(ml.notna(), 0.45 + 0.40 * rs_pct).clip(0.001, 0.999)
    df["ml_prob"] = ml
    df["ml_edge"] = ml - 0.5

    blend = (
        0.20 * ml.clip(0, 1)
        + 0.35 * comp
        + 0.25 * rs_pct
        + 0.20 * directional_l10_rate_series(df).fillna(comp)
    ).clip(0.0, 1.0)
    df["final_score"] = rs * (1.0 + 0.30 * (blend - 0.5))
    df[rank_col] = df["final_score"].where(df["final_score"].notna(), rs)
    if label:
        print(f"  [ml-rank-blend] {label} weight={blend_weight:.2f}")
    return df
