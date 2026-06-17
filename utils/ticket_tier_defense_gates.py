"""
Hard ticket-pool gates: player tier (top/bottom-3) × direction × opponent strength.

Pick-type policy (PrizePicks):
  - Goblin: OVER-only market — Goblin OVER legs bypass these gates (stay on list).
  - Standard: OVER or UNDER — tier×defense gates apply.

Basketball (WNBA, NBA, NBA1H, NBA1Q, WCBB, CBB) — Standard legs only:
  - DROP bottom-3 in prop category + OVER (any defense)
  - DROP top-3 in prop category + OVER vs elite defense (OVERALL_DEF_RANK <= 4)

Tennis — Standard legs only:
  - DROP Standard OVER: bottom-3 in category OR top-3/category-elite vs elite opponent
  - DROP Standard UNDER: top producer vs weak opponent (lean OVER, not UNDER)
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from utils.defense_tiers import normalize_def_tier_label
from utils.prop_category import prop_to_category

BASKETBALL_SPORTS = frozenset({"NBA", "NBA1H", "NBA1Q", "WNBA", "WCBB", "CBB"})
BASKETBALL_ELITE_DEF_RANK = 4

TENNIS_ELITE_RANK = 25
TENNIS_WEAK_RANK = 100

_EXCLUDE_REASON = "tier_defense_gate"


def _norm_sport(s: object) -> str:
    return str(s or "").strip().upper()


def _direction_series(df: pd.DataFrame) -> pd.Series:
    for c in ("direction", "bet_direction", "final_bet_direction", "over_under"):
        if c in df.columns:
            return df[c].astype(str).str.upper().str.strip().replace({"LOWER": "UNDER"})
    return pd.Series("OVER", index=df.index)


def _opp_def_rank_series(df: pd.DataFrame) -> pd.Series:
    for c in ("OVERALL_DEF_RANK", "opponent_def_rank", "opp_def_rank"):
        if c in df.columns:
            r = pd.to_numeric(df[c], errors="coerce")
            if r.notna().any():
                return r
    if "def_tier" in df.columns:
        tier = df["def_tier"].map(normalize_def_tier_label).astype(str).str.upper()
        out = pd.Series(np.nan, index=df.index, dtype=float)
        out = out.where(~tier.eq("ELITE"), float(BASKETBALL_ELITE_DEF_RANK))
        return out
    return pd.Series(np.nan, index=df.index, dtype=float)


def _top3_series(df: pd.DataFrame) -> pd.Series:
    if "team_top3_rank" not in df.columns:
        return pd.Series(False, index=df.index)
    r = pd.to_numeric(df["team_top3_rank"], errors="coerce")
    return r.le(3) & r.notna()


def _bottom3_series(df: pd.DataFrame) -> pd.Series:
    if "team_bottom3_rank" not in df.columns:
        return pd.Series(False, index=df.index)
    r = pd.to_numeric(df["team_bottom3_rank"], errors="coerce")
    return r.le(3) & r.notna()


def _pick_type_series(df: pd.DataFrame) -> pd.Series:
    if "pick_type" not in df.columns:
        return pd.Series("STANDARD", index=df.index)
    return df["pick_type"].astype(str).str.upper().str.strip()


def _goblin_over_mask(df: pd.DataFrame) -> pd.Series:
    """Goblin is OVER-only; these legs bypass tier×defense gates."""
    pick = _pick_type_series(df)
    direction = _direction_series(df)
    return pick.str.contains("GOBLIN", na=False) & direction.eq("OVER")


def _standard_mask(df: pd.DataFrame) -> pd.Series:
    pick = _pick_type_series(df)
    return ~pick.str.contains("GOBLIN", na=False) & ~pick.str.contains("DEMON", na=False)


def _basketball_exclusion_mask(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=bool)
    direction = _direction_series(df)
    over = direction.eq("OVER")
    top3 = _top3_series(df)
    bottom3 = _bottom3_series(df)
    opp_rank = _opp_def_rank_series(df)
    elite = opp_rank.le(BASKETBALL_ELITE_DEF_RANK) & opp_rank.notna()
    violation = over & (bottom3 | (top3 & elite))
    return violation & _standard_mask(df)


def _tennis_category_ranks(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Per prop_norm slate ranks from stat_season_avg (top/bottom 3 in category)."""
    n = len(df)
    top3 = pd.Series(False, index=df.index)
    bot3 = pd.Series(False, index=df.index)
    if n == 0:
        return top3, bot3
    prop_col = next((c for c in ("prop_norm", "prop_type", "prop") if c in df.columns), None)
    avg_col = next(
        (c for c in ("stat_season_avg", "season_avg", "stat_last10_avg", "stat_last5_avg") if c in df.columns),
        None,
    )
    if not prop_col or not avg_col:
        return top3, bot3
    work = df[[prop_col, avg_col]].copy()
    work["_avg"] = pd.to_numeric(work[avg_col], errors="coerce")
    work["_prop"] = work[prop_col].astype(str).str.lower().str.strip()
    work = work[work["_avg"].notna() & work["_prop"].astype(bool)]
    if work.empty:
        return top3, bot3
    for _, grp in work.groupby("_prop", sort=False):
        if len(grp) < 3:
            continue
        ranked = grp["_avg"].rank(method="first", ascending=False)
        top_idx = ranked[ranked <= 3].index
        bot_idx = ranked[ranked > (len(grp) - 3)].index
        top3.loc[top_idx] = True
        bot3.loc[bot_idx] = True
    return top3, bot3


def _tennis_exclusion_mask(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=bool)
    direction = _direction_series(df)
    player_rank = pd.to_numeric(df.get("player_atp_rank"), errors="coerce")
    opp_rank = pd.to_numeric(df.get("opponent_rank"), errors="coerce")
    cat_top3, cat_bot3 = _tennis_category_ranks(df)

    atp_top = player_rank.le(TENNIS_ELITE_RANK) & player_rank.notna()
    atp_bottom = player_rank.ge(TENNIS_WEAK_RANK) & player_rank.notna()
    opp_elite = opp_rank.le(TENNIS_ELITE_RANK) & opp_rank.notna()
    opp_weak = opp_rank.ge(TENNIS_WEAK_RANK) & opp_rank.notna()

    is_top = cat_top3 | atp_top
    is_bottom = cat_bot3 | atp_bottom

    standard = _standard_mask(df)

    # Standard OVER — tier×defense (Goblin OVER bypasses)
    standard_over = standard & direction.eq("OVER")
    bad_over = standard_over & (is_bottom | (is_top & opp_elite))

    # Standard UNDER — avoid fading weak opponents with elite/top producers
    standard_under = standard & direction.eq("UNDER")
    bad_under = standard_under & is_top & opp_weak

    return bad_over | bad_under


def _basketball_blanket_exclusion_mask(df: pd.DataFrame) -> pd.Series:
    """Pre-Goblin-exempt: tier×def on all OVER legs (backtest legacy comparison)."""
    if df.empty:
        return pd.Series(dtype=bool)
    direction = _direction_series(df)
    over = direction.eq("OVER")
    top3 = _top3_series(df)
    bottom3 = _bottom3_series(df)
    opp_rank = _opp_def_rank_series(df)
    elite = opp_rank.le(BASKETBALL_ELITE_DEF_RANK) & opp_rank.notna()
    return over & (bottom3 | (top3 & elite))


def _tennis_blanket_exclusion_mask(df: pd.DataFrame) -> pd.Series:
    """Pre-Goblin-exempt tennis mask (Goblin OVER included in bad_over)."""
    if df.empty:
        return pd.Series(dtype=bool)
    direction = _direction_series(df)
    pick = _pick_type_series(df)
    player_rank = pd.to_numeric(df.get("player_atp_rank"), errors="coerce")
    opp_rank = pd.to_numeric(df.get("opponent_rank"), errors="coerce")
    cat_top3, cat_bot3 = _tennis_category_ranks(df)
    atp_top = player_rank.le(TENNIS_ELITE_RANK) & player_rank.notna()
    atp_bottom = player_rank.ge(TENNIS_WEAK_RANK) & player_rank.notna()
    opp_elite = opp_rank.le(TENNIS_ELITE_RANK) & opp_rank.notna()
    opp_weak = opp_rank.ge(TENNIS_WEAK_RANK) & opp_rank.notna()
    is_top = cat_top3 | atp_top
    is_bottom = cat_bot3 | atp_bottom
    goblin_over = pick.str.contains("GOBLIN", na=False) & direction.eq("OVER")
    bad_over = goblin_over & (is_bottom | (is_top & opp_elite))
    standard_under = pick.str.contains("STANDARD", na=False) & direction.eq("UNDER")
    bad_under = standard_under & is_top & opp_weak
    return bad_over | bad_under


def blanket_tier_defense_exclusion_mask(df: pd.DataFrame, *, sport: str | None = None) -> pd.Series:
    """Legacy mask: tier×def applied to Goblin OVER too (pre pick-type split)."""
    if df is None or df.empty:
        return pd.Series(dtype=bool)
    sp = _norm_sport(sport) if sport else ""
    if not sp and "sport" in df.columns:
        sports = df["sport"].astype(str).str.upper().unique()
        sp = sports[0] if len(sports) == 1 else ""
    out = pd.Series(False, index=df.index)
    if sp in BASKETBALL_SPORTS:
        out = out | _basketball_blanket_exclusion_mask(df)
    elif sp == "TENNIS":
        out = out | _tennis_blanket_exclusion_mask(df)
    elif "sport" in df.columns:
        for s in BASKETBALL_SPORTS:
            sm = df["sport"].astype(str).str.upper().eq(s)
            if sm.any():
                out.loc[sm] = _basketball_blanket_exclusion_mask(df.loc[sm])
        sm = df["sport"].astype(str).str.upper().eq("TENNIS")
        if sm.any():
            out.loc[sm] = _tennis_blanket_exclusion_mask(df.loc[sm])
    return out.fillna(False)


def tier_defense_exclusion_mask(df: pd.DataFrame, *, sport: str | None = None) -> pd.Series:
    """True = exclude leg from main ticket pool."""
    if df is None or df.empty:
        return pd.Series(dtype=bool)
    sp = _norm_sport(sport) if sport else ""
    if not sp and "sport" in df.columns:
        sports = df["sport"].astype(str).str.upper().unique()
        sp = sports[0] if len(sports) == 1 else ""
    out = pd.Series(False, index=df.index)
    if sp in BASKETBALL_SPORTS:
        out = out | _basketball_exclusion_mask(df)
    elif sp == "TENNIS":
        out = out | _tennis_exclusion_mask(df)
    elif "sport" in df.columns:
        for s in BASKETBALL_SPORTS:
            sm = df["sport"].astype(str).str.upper().eq(s)
            if sm.any():
                chunk = df.loc[sm]
                out.loc[sm] = _basketball_exclusion_mask(chunk)
        sm = df["sport"].astype(str).str.upper().eq("TENNIS")
        if sm.any():
            out.loc[sm] = _tennis_exclusion_mask(df.loc[sm])
    return out.fillna(False)


def apply_tier_defense_ticket_pool_filter(
    df: pd.DataFrame,
    *,
    sport: str | None = None,
) -> tuple[pd.DataFrame, int, dict[str, int]]:
    """
    Remove legs that violate tier×defense ticket gates.
    Returns (filtered_df, n_removed, reason_counts).
    """
    if df is None or df.empty:
        return df, 0, {}
    mask = tier_defense_exclusion_mask(df, sport=sport)
    n_removed = int(mask.sum())
    if not n_removed:
        return df, 0, {}
    reasons: dict[str, int] = {}
    sp = _norm_sport(sport or (df["sport"].iloc[0] if "sport" in df.columns else ""))
    if sp in BASKETBALL_SPORTS or any(df.get("sport", pd.Series()).astype(str).str.upper().isin(BASKETBALL_SPORTS)):
        sub = df.loc[mask]
        direction = _direction_series(sub)
        bottom3 = _bottom3_series(sub)
        top3 = _top3_series(sub)
        elite = _opp_def_rank_series(sub).le(BASKETBALL_ELITE_DEF_RANK)
        std = _standard_mask(sub)
        reasons["bottom3_OVER"] = int((direction.eq("OVER") & bottom3 & std).sum())
        reasons["top3_OVER_vs_elite"] = int((direction.eq("OVER") & top3 & elite & std).sum())
    if sp == "TENNIS" or (df.get("sport", pd.Series()).astype(str).str.upper() == "TENNIS").any():
        ex_t = df.loc[mask]
        if len(ex_t):
            direction = _direction_series(ex_t)
            reasons["tennis_std_OVER"] = int(direction.eq("OVER").sum())
            reasons["tennis_std_UNDER"] = int(direction.eq("UNDER").sum())
    return df.loc[~mask].copy(), n_removed, reasons


def leg_passes_tier_defense_gate(row: pd.Series | dict[str, Any], *, sport: str | None = None) -> bool:
    """Single-leg check for structured ticket builders."""
    if isinstance(row, dict):
        row = pd.Series(row)
    one = pd.DataFrame([row.to_dict()])
    if sport:
        one["sport"] = sport
    elif "sport" not in one.columns:
        one["sport"] = ""
    if bool(_goblin_over_mask(one).iloc[0]):
        return True
    return not bool(tier_defense_exclusion_mask(one, sport=sport).iloc[0])
