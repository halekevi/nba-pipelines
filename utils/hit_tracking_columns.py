"""
Standard hit-tracking columns for all sport pipeline exports (step8 / step6).

Ensures every slate row carries:
  - L5/L10 directional hit counts vs today's line
  - direction-aware hit_rate
  - graded strat segment rates (strat_hit_rate / strat_n)
  - archived player / opponent historical hit rates
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.l10_streak_utils import finalize_l10_ui_columns  # noqa: E402

HIT_WINDOW_COLS: tuple[str, ...] = (
    "l5_over",
    "l5_under",
    "l10_over",
    "l10_under",
    "l10_games_played",
    "l10_streak",
)

HIT_TRACKING_RENAME: dict[str, str] = {
    "hit_rate": "Hit Rate",
    "hit_rate_l5": "Hit Rate L5",
    "hit_rate_l10": "Hit Rate L10",
    "strat_hit_rate": "Strat Hit Rate",
    "strat_n": "Strat N",
    "player_hr_historical": "Player HR Hist",
    "opp_hr_historical": "Opp HR Hist",
    "l10_over": "L10 Over",
    "l10_under": "L10 Under",
    "l10_games_played": "L10 Games",
    "l10_streak": "L10 Streak",
    "l10_over_pct": "L10 Over%",
}

HIT_TRACKING_EXPORT_COLS: tuple[str, ...] = (
    *HIT_WINDOW_COLS,
    "hit_rate",
    "hit_rate_l5",
    "hit_rate_l10",
    "strat_hit_rate",
    "strat_n",
    "player_hr_historical",
    "opp_hr_historical",
)

_COALESCE_PAIRS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("l5_over", ("l5_over", "last5_over", "L5 Over", "over_L5_raw", "over_L5", "line_hits_over_5")),
    ("l5_under", ("l5_under", "last5_under", "L5 Under", "under_L5_raw", "under_L5", "line_hits_under_5")),
    ("l10_over", ("l10_over", "L10 Over", "line_hits_over_10", "over_L10", "over_L10_raw")),
    ("l10_under", ("l10_under", "L10 Under", "line_hits_under_10", "under_L10", "under_L10_raw")),
    ("l10_games_played", ("l10_games_played", "line_games_played_10", "Games (10g)", "sample_L10")),
)


def _direction_series(df: pd.DataFrame) -> pd.Series:
    for col in (
        "final_bet_direction",
        "bet_direction",
        "direction",
        "recommended_side",
        "Direction",
    ):
        if col in df.columns:
            return df[col].astype(str).str.strip().str.upper()
    return pd.Series(["OVER"] * len(df), index=df.index)


def _first_numeric(df: pd.DataFrame, names: tuple[str, ...]) -> pd.Series:
    out = pd.Series(np.nan, index=df.index, dtype=float)
    for name in names:
        if name not in df.columns:
            continue
        val = pd.to_numeric(df[name], errors="coerce")
        if isinstance(val, pd.DataFrame):
            val = val.iloc[:, 0]
        out = out.combine_first(val)
    return out


def _normalize_rate(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    return np.where(s > 1.0, s / 100.0, s)


def attach_hit_window_columns(df: pd.DataFrame, *, line_col: str = "line") -> pd.DataFrame:
    """Coalesce L5/L10 aliases and run finalize_l10_ui_columns when line + stat_g* exist."""
    if df is None or df.empty:
        return df
    out = df.copy()
    for target, alts in _COALESCE_PAIRS:
        if target not in out.columns:
            out[target] = np.nan
        series = pd.to_numeric(out[target], errors="coerce")
        for alt in alts:
            if alt in out.columns and alt != target:
                series = series.combine_first(pd.to_numeric(out[alt], errors="coerce"))
        out[target] = series

    if line_col in out.columns:
        out = finalize_l10_ui_columns(out, line_col=line_col)
    return out


def attach_direction_hit_rate(df: pd.DataFrame) -> pd.DataFrame:
    """Set hit_rate / hit_rate_l5 / hit_rate_l10 for the picked direction."""
    if df is None or df.empty:
        return df
    out = df.copy()
    direction = _direction_series(out)
    is_under = direction.isin({"UNDER", "LOWER"})

    l5o = _first_numeric(out, ("l5_over", "last5_over", "over_L5", "line_hits_over_5"))
    l5u = _first_numeric(out, ("l5_under", "last5_under", "under_L5", "line_hits_under_5"))
    hr5o = pd.Series(_normalize_rate(_first_numeric(out, ("line_hit_rate_over_ou_5", "hit_rate_over_L5", "hr_L5"))), index=out.index)
    hr5u = pd.Series(_normalize_rate(_first_numeric(out, ("line_hit_rate_under_ou_5",))), index=out.index)
    hr5o = hr5o.combine_first((l5o / 5.0).clip(0, 1))
    hr5u = hr5u.combine_first((l5u / 5.0).clip(0, 1))

    gp10 = _first_numeric(out, ("l10_games_played", "line_games_played_10", "sample_L10")).replace(0, np.nan)
    l10o = _first_numeric(out, ("l10_over", "over_L10", "line_hits_over_10"))
    l10u = _first_numeric(out, ("l10_under", "under_L10", "line_hits_under_10"))
    hr10o = pd.Series(_normalize_rate(_first_numeric(out, ("line_hit_rate_over_ou_10", "hit_rate_over_L10", "hr_L10"))), index=out.index)
    hr10u = pd.Series(_normalize_rate(_first_numeric(out, ("line_hit_rate_under_ou_10",))), index=out.index)
    hr10o = hr10o.combine_first((l10o / gp10).clip(0, 1))
    hr10u = hr10u.combine_first((l10u / gp10).clip(0, 1))

    side_l5 = pd.Series(np.where(is_under, hr5u, hr5o), index=out.index, dtype=float)
    side_l10 = pd.Series(np.where(is_under, hr10u, hr10o), index=out.index, dtype=float)

    comp = _first_numeric(out, ("composite_hit_rate", "composite_hr", "line_hit_rate"))
    comp = pd.Series(_normalize_rate(comp), index=out.index)

    blended = np.where(
        side_l5.notna() & side_l10.notna(),
        0.5 * side_l5 + 0.5 * side_l10,
        np.where(side_l5.notna(), side_l5, np.where(side_l10.notna(), side_l10, np.nan)),
    )
    blended = pd.Series(blended, index=out.index, dtype=float)
    hit_rate = comp.combine_first(blended)

    if "hit_rate" not in out.columns:
        out["hit_rate"] = hit_rate
    else:
        existing = pd.to_numeric(out["hit_rate"], errors="coerce")
        existing = pd.Series(_normalize_rate(existing), index=out.index)
        out["hit_rate"] = existing.combine_first(hit_rate)

    out["hit_rate_l5"] = side_l5
    out["hit_rate_l10"] = side_l10
    return out


def _fill_archive_hr_block(
    out: pd.DataFrame,
    idx: pd.Index,
    sport_u: str,
    *,
    player_col: str,
    prop_col: str,
    opp_col: str | None,
) -> None:
    try:
        from scripts.step_archive import (  # noqa: WPS433
            _norm_player,
            _norm_prop,
            get_bulk_stats,
            get_opp_historical_hr,
        )
    except Exception:
        return

    block = out.loc[idx]
    players = block[player_col].astype(str)
    props = block[prop_col].astype(str)
    dirs = _direction_series(block)
    pairs = list(zip(players.tolist(), props.tolist()))
    dir_triples = list(zip(players.tolist(), props.tolist(), dirs.tolist()))
    if not pairs:
        return

    try:
        bulk = get_bulk_stats(sport_u, pairs, dir_triples)
        pstats = bulk.get("player_stats", {})

        phr_vals = []
        for player, prop_type in pairs:
            key = (_norm_player(player), _norm_prop(prop_type))
            phr_vals.append(pstats.get(key, {}).get("player_hr"))

        opphr_vals = []
        if opp_col:
            opps = block[opp_col].astype(str)
            for opp, prop_type, direction in zip(opps, props, dirs):
                opphr_vals.append(
                    get_opp_historical_hr(sport_u, str(opp), str(prop_type), str(direction))
                )
        else:
            opphr_vals = [np.nan] * len(block)

        out.loc[idx, "player_hr_historical"] = pd.to_numeric(
            pd.Series(phr_vals, index=idx), errors="coerce"
        )
        out.loc[idx, "opp_hr_historical"] = pd.to_numeric(
            pd.Series(opphr_vals, index=idx), errors="coerce"
        )
    except Exception:
        pass


def attach_archive_historical_hr(df: pd.DataFrame, sport: str) -> pd.DataFrame:
    """Attach player_hr_historical / opp_hr_historical from step_archive SQLite."""
    if df is None or df.empty:
        return df
    out = df.copy()

    for col in ("player_hr_historical", "opp_hr_historical"):
        if col not in out.columns:
            out[col] = np.nan

    player_col = next((c for c in ("player", "player_name", "Player") if c in out.columns), None)
    prop_col = next((c for c in ("prop_type", "prop_type_norm", "prop", "Prop", "stat_norm") if c in out.columns), None)
    opp_col = next((c for c in ("opp_team", "opp", "Opp", "opponent") if c in out.columns), None)
    if not player_col or not prop_col:
        return out

    if "sport" in out.columns:
        sports = out["sport"].astype(str).str.strip().str.upper()
        for sport_u in sports.dropna().unique():
            if not sport_u or sport_u == "NAN":
                continue
            mask = sports == sport_u
            _fill_archive_hr_block(
                out, out.index[mask], sport_u,
                player_col=player_col, prop_col=prop_col, opp_col=opp_col,
            )
    else:
        sport_u = str(sport or "").strip().upper()
        if sport_u:
            _fill_archive_hr_block(
                out, out.index, sport_u,
                player_col=player_col, prop_col=prop_col, opp_col=opp_col,
            )
    return out


def resolve_sport_code(hint: str) -> str:
    """Map path / filename hints to canonical sport codes."""
    h = str(hint or "").lower()
    if "nba1q" in h or "1q" in h and "nba" in h:
        return "NBA1Q"
    if "nba1h" in h or "1h" in h and "nba" in h:
        return "NBA1H"
    if "wnba" in h:
        return "WNBA"
    if "wcbb" in h:
        return "WCBB"
    if "cbb" in h or "ncaab" in h:
        return "CBB"
    if "cfb" in h or "ncaaf" in h:
        return "CFB"
    if "nhl" in h:
        return "NHL"
    if "mlb" in h:
        return "MLB"
    if "soccer" in h:
        return "SOCCER"
    if "tennis" in h:
        return "TENNIS"
    if "nfl" in h:
        return "NFL"
    if "golf" in h or "pga" in h or "lpga" in h or "livgolf" in h:
        return "GOLF"
    return "NBA"


def attach_hit_tracking_columns(df: pd.DataFrame, sport: str, *, line_col: str = "line") -> pd.DataFrame:
    """Full hit-tracking bundle for step8/step6 export rows."""
    if df is None or df.empty:
        return df
    sport_u = str(sport or "").strip().upper() or "NBA"
    out = attach_hit_window_columns(df, line_col=line_col)
    out = attach_direction_hit_rate(out)
    out["sport"] = sport_u
    if "def_tier" not in out.columns and "DEF_TIER" in out.columns:
        out["def_tier"] = out["DEF_TIER"]
    from utils.graded_enrichment import attach_strat_hit_rates  # noqa: WPS433

    out = attach_strat_hit_rates(out)
    out = attach_archive_historical_hr(out, sport_u)
    return out
