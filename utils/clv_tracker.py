"""
Closing Line Value (CLV) helpers: compute deltas and prepare rows for SQLite clv_log.

Expect graded sheets to optionally carry any of:
  - my_odds_implied_prob, closing_implied_prob  -> clv_delta = closing - my (side-specific)
  - clv_delta (precomputed)
  - american odds columns (optional): my_american_odds, closing_american_odds
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd


def _american_to_implied_prob(american: float) -> Optional[float]:
    """Convert American odds to implied probability (0-1), incl. vig-naive."""
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    if a > 0:
        return 100.0 / (a + 100.0)
    return (-a) / ((-a) + 100.0)


def compute_clv_delta(
    my_implied: Optional[float],
    closing_implied: Optional[float],
) -> Optional[float]:
    if my_implied is None or closing_implied is None:
        return None
    try:
        mi = float(my_implied)
        ci = float(closing_implied)
    except (TypeError, ValueError):
        return None
    if not (0 < mi < 1 and 0 < ci < 1):
        return None
    return round(ci - mi, 6)


def graded_rows_to_clv_log(
    sport: str,
    grade_date: str,
    df: pd.DataFrame,
) -> list[tuple[Any, ...]]:
    """
    Build insert tuples for clv_log table (matches scripts/step_archive.py schema).

    Tuple order:
      sport, grade_date, prop_label, player_name, prop_type, line, direction,
      my_odds_implied_prob, closing_implied_prob, clv_delta, pick_type, tier, result, archived_at
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    sport_u = str(sport).strip().upper()

    def col(df_: pd.DataFrame, names: tuple[str, ...]) -> Optional[pd.Series]:
        for n in names:
            if n in df_.columns:
                return df_[n]
        return None

    player_s = col(df, ("player", "player_name", "pp_player"))
    prop_s = col(df, ("prop_type_norm", "prop_type", "stat_norm", "prop_norm", "Prop"))
    if player_s is None or prop_s is None:
        return []

    my_i = col(df, ("my_odds_implied_prob", "my_implied_prob", "open_implied_prob"))
    cl_i = col(df, ("closing_implied_prob", "close_implied_prob"))
    my_am = col(df, ("my_american_odds", "open_american_odds", "american_odds_open"))
    cl_am = col(df, ("closing_american_odds", "close_american_odds", "american_odds_close"))
    dlt = col(df, ("clv_delta",))
    line_s = col(df, ("line", "line_score"))
    dir_s = col(df, ("final_bet_direction", "bet_direction", "direction"))
    pick_s = col(df, ("pick_type", "Pick Type"))
    tier_s = col(df, ("tier", "Tier"))
    res_s = col(df, ("result", "outcome"))

    rows: list[tuple[Any, ...]] = []
    for i in range(len(df)):
        mi = None
        ci = None
        if my_i is not None:
            try:
                v = float(pd.to_numeric(my_i.iloc[i], errors="coerce"))
                if not math.isnan(v):
                    mi = v
            except Exception:
                pass
        if cl_i is not None:
            try:
                v = float(pd.to_numeric(cl_i.iloc[i], errors="coerce"))
                if not math.isnan(v):
                    ci = v
            except Exception:
                pass
        if mi is None and my_am is not None:
            mi = _american_to_implied_prob(my_am.iloc[i])
        if ci is None and cl_am is not None:
            ci = _american_to_implied_prob(cl_am.iloc[i])

        cd = None
        if dlt is not None:
            try:
                v = float(pd.to_numeric(dlt.iloc[i], errors="coerce"))
                if not math.isnan(v):
                    cd = v
            except Exception:
                pass
        if cd is None:
            cd = compute_clv_delta(mi, ci)
        if cd is None and mi is None and ci is None:
            continue

        line = None
        if line_s is not None:
            try:
                line = float(pd.to_numeric(line_s.iloc[i], errors="coerce"))
            except Exception:
                line = None
        direction = ""
        if dir_s is not None:
            direction = str(dir_s.iloc[i] or "").strip().upper()

        player_name = str(player_s.iloc[i] or "").strip()
        prop_type = str(prop_s.iloc[i] or "").strip().lower()
        prop_label = " | ".join(
            x for x in (player_name, prop_type, "" if line is None or math.isnan(line) else str(line), direction) if x
        )
        pick_type = str(pick_s.iloc[i] or "").strip() if pick_s is not None else ""
        tier = str(tier_s.iloc[i] or "").strip() if tier_s is not None else ""
        result = str(res_s.iloc[i] or "").strip().upper() if res_s is not None else ""

        rows.append(
            (
                sport_u,
                grade_date,
                prop_label,
                player_name,
                prop_type,
                None if line is None or (isinstance(line, float) and math.isnan(line)) else line,
                direction,
                mi,
                ci,
                cd,
                pick_type,
                tier,
                result,
                now_iso,
            )
        )
    return rows
