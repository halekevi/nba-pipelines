#!/usr/bin/env python3
"""
Shared edge / ticket ML features for graded history and daily step7 outputs.

The `edge` feature is play-side signed: raw (projection - line) is negated when
bet_direction is UNDER, aligned with prop ML and ml_play_side_edge. Retrain
edge_model_unified after changing this definition.
"""

from __future__ import annotations

import re
from pathlib import Path
import numpy as np
import pandas as pd

from graded_line_quality_features import add_stratification_columns

REPO_ROOT = Path(__file__).resolve().parent.parent

SPORT_ENCODING = {
    "NBA": 0,
    "CBB": 1,
    "NHL": 2,
    "SOCCER": 3,
    "MLB": 4,
    "NBA1H": 5,
    "NBA1Q": 6,
    "WNBA": 7,
    "TENNIS": 8,
}

FEATURE_COLUMNS: list[str] = [
    "composite_hit_rate",
    "tier_encoded",
    "tier_era",
    "pick_type_encoded",
    "direction_encoded",
    "line_score",
    "def_rank",
    "def_tier_encoded",
    "hit_rate_L5",
    "hit_rate_L10",
    "avg_L5_vs_line",
    "avg_L10_vs_line",
    "edge",
    "prop_score",
    "sport_encoded",
    "minutes_tier",
    "minutes_cv",
    "minutes_trend",
    "role_type_encoded",
    "dominance_pct",
    "specialization_rank",
    # ── New archive-derived features (neutral fills until history accumulates) ──
    "abs_edge",              # magnitude of play-side edge (always >= 0)
    "floor_clears_line",     # 1.0/0.0 — historical floor vs line; NaN when no history
    "avg_win_margin",        # avg cushion when HIT historically
    "dir_line_gap_norm",     # normalized (median_actual - line), direction-aware, 0..1
    "opp_hr_historical",     # opponent-specific graded hit rate
    "player_hr_historical",  # player-specific graded hit rate on this prop type
    "l10_over",
    "l10_under",
    "l10_over_pct",
    "l10_streak_encoded",
    # ── Stratification / data-quality (see graded_line_quality_features) ──
    "line_bucket_encoded",
    "context_known",
    "defense_known",
    "minutes_known",
    "role_stability_score",
    # MLB-only (neutral fill on other sports via SPORT_FEATURE_OVERRIDES)
    "batting_order_pos",
    "top_of_order",
    "opp_pitcher_era_vs_batter_hand",
    "opp_pitcher_k9_vs_batter_hand",
    "pitcher_advantage_encoded",
    "park_factor_overall",
    "park_tier_encoded",
    "wind_speed_mph",
    "wind_out_to_cf",
    "weather_flag_encoded",
    # NHL-only (NST / NHL API via SPORT_FEATURE_OVERRIDES)
    "pp_toi_per_game",
    "pp_toi_pct",
    "pp_unit_tier_encoded",
    "line_combo_toi_pct",
    "line_combo_cf_pct",
    "line_combo_xgf_pct",
    "on_pp1_line",
    # WNBA-only
    "usage_pct",
    "usage_tier_encoded",
    "team_pace",
    "opp_pace",
    "pace_delta",
    "pace_context_encoded",
    "star_tier",
    "is_franchise_star",
    "foul_trouble_risk_encoded",
    "b2b_flag",
    "b2b_rest_context_encoded",
    # NBA-only (usage%/pace shared with WNBA columns above)
    "reb_pct",
    "ast_pct",
    "game_pace",
    "usage_role_type_encoded",
    "opp_def_rating",
    "team_implied_total",
    "opp_implied_total",
    "game_script_context_encoded",
    "team_star_out",
    "key_facilitator_out",
    "usage_vacuum",
    "injury_boost_candidate",
    "high_variance_role",
    "minutes_floor_L10",
    "minutes_ceil_L10",
    "minutes_cv_L10",
    "opp_pts_allowed_vs_position",
    "opp_reb_allowed_vs_position",
    "opp_ast_allowed_vs_position",
    "positional_matchup_tier_encoded",
    # Soccer-only (FBref xG)
    "player_xg_per90",
    "player_xag_per90",
    "player_goals_minus_xg",
    "player_shots_per90",
    "xg_tier_encoded",
]

SPORT_FEATURE_OVERRIDES: dict[str, list[str]] = {
    "MLB": [
        "batting_order_pos",
        "top_of_order",
        "opp_pitcher_era_vs_batter_hand",
        "opp_pitcher_k9_vs_batter_hand",
        "pitcher_advantage_encoded",
        "park_factor_overall",
        "park_tier_encoded",
        "wind_speed_mph",
        "wind_out_to_cf",
        "weather_flag_encoded",
    ],
    "NHL": [
        "pp_toi_per_game",
        "pp_toi_pct",
        "pp_unit_tier_encoded",
        "line_combo_toi_pct",
        "line_combo_cf_pct",
        "line_combo_xgf_pct",
        "on_pp1_line",
    ],
    "WNBA": [
        "usage_pct",
        "usage_tier_encoded",
        "team_pace",
        "opp_pace",
        "pace_delta",
        "pace_context_encoded",
        "star_tier",
        "is_franchise_star",
        "foul_trouble_risk_encoded",
        "b2b_flag",
        "b2b_rest_context_encoded",
    ],
    "Soccer": [
        "player_xg_per90",
        "player_xag_per90",
        "player_goals_minus_xg",
        "player_shots_per90",
        "xg_tier_encoded",
    ],
    "Tennis": [
        "surface_encoded",
        "aces_per_match_mean",
        "first_serve_pct",
        "win_rate_on_surface",
        "games_won_per_match",
        "surface_specialist",
        "surface_struggle",
        "n_matches_on_surface",
    ],
    "NBA": [
        "usage_pct",
        "usage_tier_encoded",
        "usage_role_type_encoded",
        "reb_pct",
        "ast_pct",
        "team_pace",
        "opp_pace",
        "game_pace",
        "pace_delta",
        "pace_context_encoded",
        "opp_def_rating",
        "team_implied_total",
        "opp_implied_total",
        "game_script_context_encoded",
        "team_star_out",
        "key_facilitator_out",
        "usage_vacuum",
        "injury_boost_candidate",
        "role_stability_score",
        "high_variance_role",
        "minutes_floor_L10",
        "minutes_ceil_L10",
        "minutes_cv_L10",
        "opp_pts_allowed_vs_position",
        "opp_reb_allowed_vs_position",
        "opp_ast_allowed_vs_position",
        "positional_matchup_tier_encoded",
        "l10_over",
        "l10_under",
        "l10_over_pct",
        "l10_streak",
    ],
}

_USAGE_TIER_ENC = {"low": 0.0, "medium": 1.0, "high": 2.0}
_NBA_USAGE_TIER_ENC = {"role": 0.0, "medium": 1.0, "high": 2.0, "star": 3.0}
_USAGE_ROLE_TYPE_ENC = {
    "role_player": 0.0,
    "scorer": 1.0,
    "rebounder": 2.0,
    "playmaker": 3.0,
}
_PACE_CONTEXT_ENC = {
    "low_pace": 0.0,
    "medium": 1.0,
    "medium_pace": 1.0,
    "high_pace": 2.0,
}
_GAME_SCRIPT_ENC = {
    "underdog": 0.0,
    "pick_em": 1.0,
    "slight_favorite": 2.0,
    "heavy_favorite": 3.0,
}
_POS_MATCHUP_ENC = {"unfavorable": 0.0, "neutral": 1.0, "favorable": 2.0}
_FOUL_RISK_ENC = {"low": 0.0, "medium": 1.0, "high": 2.0}
_L10_STREAK_ENC = {"COLD": 0.0, "NEUTRAL": 1.0, "HOT": 2.0}

_B2B_REST_ENC = {"normal_rest": 0.0, "b2b_second": 1.0}

WNBA_FEATURE_COLUMNS: list[str] = list(SPORT_FEATURE_OVERRIDES.get("WNBA", []))
WNBA_MIN_FEATURE_FILL_FRAC = 0.50


def wnba_features_with_sufficient_fill(
    df: pd.DataFrame,
    *,
    min_frac: float = WNBA_MIN_FEATURE_FILL_FRAC,
) -> set[str]:
    """Return WNBA override columns with non-null rate >= min_frac on WNBA rows."""
    if not WNBA_FEATURE_COLUMNS:
        return set()
    wnba = df.loc[df["sport"].astype(str).str.strip().str.upper().eq("WNBA")]
    if wnba.empty:
        return set(WNBA_FEATURE_COLUMNS)
    ok: set[str] = set()
    for col in WNBA_FEATURE_COLUMNS:
        if col not in wnba.columns:
            continue
        rate = float(pd.to_numeric(wnba[col], errors="coerce").notna().mean())
        if rate >= float(min_frac):
            ok.add(col)
    # star_tier always registered (step4b defaults missing players to tier 2)
    if "star_tier" in WNBA_FEATURE_COLUMNS:
        ok.add("star_tier")
    if "is_franchise_star" in WNBA_FEATURE_COLUMNS:
        ok.add("is_franchise_star")
    return ok


def drop_wnba_features_below_fill_threshold(
    feature_cols: list[str],
    df: pd.DataFrame,
    *,
    min_frac: float = WNBA_MIN_FEATURE_FILL_FRAC,
) -> tuple[list[str], list[str]]:
    """Remove WNBA-only columns from training when fill rate is below threshold."""
    allowed = wnba_features_with_sufficient_fill(df, min_frac=min_frac)
    dropped = [c for c in WNBA_FEATURE_COLUMNS if c in feature_cols and c not in allowed]
    kept = [c for c in feature_cols if c not in WNBA_FEATURE_COLUMNS or c in allowed]
    return kept, dropped


NBA_FEATURE_COLUMNS: list[str] = list(SPORT_FEATURE_OVERRIDES.get("NBA", []))
NBA_MIN_FEATURE_FILL_FRAC = 0.60


def nba_features_with_sufficient_fill(
    df: pd.DataFrame,
    *,
    min_frac: float = NBA_MIN_FEATURE_FILL_FRAC,
) -> set[str]:
    if not NBA_FEATURE_COLUMNS:
        return set()
    nba = df.loc[df["sport"].astype(str).str.strip().str.upper().eq("NBA")]
    if nba.empty:
        return set(NBA_FEATURE_COLUMNS)
    ok: set[str] = set()
    for col in NBA_FEATURE_COLUMNS:
        if col not in nba.columns:
            continue
        rate = float(pd.to_numeric(nba[col], errors="coerce").notna().mean())
        if rate >= float(min_frac):
            ok.add(col)
    for col in ("usage_role_type_encoded", "positional_matchup_tier_encoded"):
        if col in NBA_FEATURE_COLUMNS:
            ok.add(col)
    return ok


def drop_nba_features_below_fill_threshold(
    feature_cols: list[str],
    df: pd.DataFrame,
    *,
    min_frac: float = NBA_MIN_FEATURE_FILL_FRAC,
) -> tuple[list[str], list[str]]:
    allowed = nba_features_with_sufficient_fill(df, min_frac=min_frac)
    dropped = [c for c in NBA_FEATURE_COLUMNS if c in feature_cols and c not in allowed]
    kept = [c for c in feature_cols if c not in NBA_FEATURE_COLUMNS or c in allowed]
    return kept, dropped


_PP_UNIT_ENC = {"NO_PP": 0.0, "PP_FRINGE": 1.0, "PP2": 2.0, "PP1": 3.0}

_PITCHER_ADV_ENC = {"favor_pitcher": 0.0, "neutral": 1.0, "favor_batter": 2.0}
_PARK_TIER_ENC = {"pitcher": 0.0, "neutral": 1.0, "hitter": 2.0}
_XG_TIER_ENC = {"cache_miss": 0.0, "low": 1.0, "mid": 2.0, "high": 3.0}
_WEATHER_FLAG_ENC = {
    "dome": 0.0,
    "calm": 1.0,
    "moderate_wind": 2.0,
    "high_wind": 3.0,
    "rain": 4.0,
}


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _first_col(df: pd.DataFrame, names: tuple[str, ...]) -> pd.Series:
    idx = df.index
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series(np.nan, index=idx)


def _norm_sport(sport: str) -> str:
    x = str(sport or "").strip().upper()
    if x == "FOOTBALL" or x == "SOC":
        return "SOCCER"
    return x


def parse_toi_to_minutes(val: object) -> float:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return np.nan
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    st = str(val).strip()
    if not st or st.lower() in ("nan", "none", ""):
        return np.nan
    if ":" in st:
        parts = st.split(":")
        try:
            m = float(parts[0])
            sec = float(parts[1]) if len(parts) > 1 else 0.0
            return m + sec / 60.0
        except (ValueError, TypeError):
            return np.nan
    try:
        return float(st)
    except (ValueError, TypeError):
        return np.nan


def _parse_toi_series(s: pd.Series) -> pd.Series:
    return s.map(parse_toi_to_minutes)


def _direction_series(df: pd.DataFrame) -> pd.Series:
    """Coalesce direction fields; graded exports often leave `direction` blank but set `bet_direction`."""
    idx = df.index
    out = pd.Series("", index=idx, dtype=str)
    # Prefer explicit pick-side columns before a sparse/legacy `direction` column.
    for n in (
        "recommended_side",
        "bet_direction",
        "final_bet_direction",
        "direction_used",
        "direction",
    ):
        if n not in df.columns:
            continue
        s = df[n].astype(str).str.strip().str.upper()
        s = s.replace({"NAN": "", "NONE": "", "NULL": ""})
        fill = out.eq("") & s.ne("")
        out = out.where(~fill, s)
    out = out.replace({"NAN": "", "NONE": "", "NULL": ""})
    return out


def _pick_type_encoded_series(df: pd.DataFrame) -> pd.Series:
    pt = _first_col(df, ("pick_type",)).astype(str).str.lower()
    return pd.Series(
        np.where(pt.str.contains("gob"), 2, np.where(pt.str.contains("dem"), 0, 1)),
        index=df.index,
    ).astype(float)


def _tier_encoded_series(df: pd.DataFrame) -> pd.Series:
    t = _first_col(df, ("tier",)).astype(str).str.strip().str.upper()
    return t.map({"A": 3.0, "B": 2.0, "C": 1.0, "D": 0.0}).fillna(0.0).astype(float)


def _def_tier_encoded_series(df: pd.DataFrame) -> pd.Series:
    raw = _first_col(df, ("def_tier", "DEF_TIER", "defense_tier"))
    s = raw.astype(str).str.strip().str.upper()
    s = s.replace({"NAN": "", "NONE": ""})
    val = np.full(len(s), np.nan)
    el = s.str.contains("ELITE", na=False)
    val = np.where(el, 4.0, val)
    val = np.where(~el & s.eq("A"), 3.0, val)
    val = np.where(~el & s.eq("B"), 2.0, val)
    val = np.where(~el & s.eq("C"), 1.0, val)
    val = np.where(~el & s.eq("D"), 0.0, val)
    # Below Avg opponent defense (softer than league avg) — between AVERAGE and WEAK
    below_avg = ~el & s.str.contains("BELOW", na=False) & ~s.str.contains("ABOVE", na=False)
    val = np.where(below_avg, 0.5, val)
    val = np.where(~el & s.str.contains("WEAK", na=False), 0.0, val)
    val = np.where(
        ~el & s.str.contains("STRONG|SOLID|GOOD|ABOVE", na=False) & ~s.str.contains("WEAK", na=False),
        2.0,
        val,
    )
    # Plain avg/mid — exclude "above avg" / "below avg" (those match AVG substring)
    avg_mid = (
        ~el
        & ~below_avg
        & ~s.str.contains("WEAK", na=False)
        & s.str.contains("AVERAGE|AVG|MID", na=False)
        & ~s.str.contains("ABOVE", na=False)
        & ~s.str.contains("BELOW", na=False)
    )
    val = np.where(avg_mid, 1.0, val)
    return pd.Series(val, index=df.index)


def _scale_hit_pct(s: pd.Series) -> pd.Series:
    s = _to_num(s)
    if s.notna().any():
        med = s.dropna().median()
        if med > 1.0:
            return s / 100.0
    return s


def _is_nhl_goalie_row(df: pd.DataFrame) -> pd.Series:
    role = _first_col(df, ("player_role", "position_group", "position")).astype(str).str.upper()
    prop = _first_col(df, ("stat_norm", "prop_type", "stat_type")).astype(str).str.lower()
    goalie_prop = prop.str.contains("save|goalie|goals allowed|goals_allowed", regex=True)
    goalie_role = role.str.contains("GOALIE|G |^G$")
    return goalie_role | goalie_prop


def _minutes_base_value(df: pd.DataFrame, sport: str) -> pd.Series:
    sp = _norm_sport(sport)
    idx = df.index
    avg_l10 = _to_num(_first_col(df, ("avg_L10", "stat_last10_avg")))
    avg_l5 = _to_num(_first_col(df, ("avg_L5", "stat_last5_avg")))
    avg_min = _to_num(_first_col(df, ("avg_minutes", "minutes")))
    toi_avg = _parse_toi_series(_first_col(df, ("toi_avg_L10", "toi_per_game_api", "Time On Ice")))

    if sp == "NHL":
        is_g = _is_nhl_goalie_row(df)
        toi = _parse_toi_series(_first_col(df, ("toi_avg_L10", "toi_per_game_api")))
        sk = avg_l10.fillna(avg_l5).fillna(toi)
        g = avg_l10.fillna(toi).fillna(avg_l5)
        return pd.Series(np.where(is_g, g, sk), index=idx).astype(float)

    base = avg_l10.fillna(avg_min).fillna(avg_l5)
    if sp == "SOCCER":
        base = base.fillna(_to_num(_first_col(df, ("minutes_per_game", "avg_minutes_L10"))))
    return base.astype(float)


def _minutes_tier_numeric(df: pd.DataFrame, sport: str) -> pd.Series:
    sp = _norm_sport(sport)
    m = _minutes_base_value(df, sport)
    out = pd.Series(0.0, index=df.index)
    if sp in ("NBA", "CBB"):
        if sp == "NBA":
            out = np.where(m >= 28, 3.0, np.where(m >= 22, 2.0, np.where(m >= 15, 1.0, 0.0)))
        else:
            out = np.where(m >= 25, 3.0, np.where(m >= 20, 2.0, np.where(m >= 12, 1.0, 0.0)))
        return pd.Series(out, index=df.index)
    if sp == "NHL":
        is_g = _is_nhl_goalie_row(df)
        out = np.where(
            is_g,
            np.where(m >= 50, 3.0, np.where(m >= 40, 2.0, np.where(m >= 20, 1.0, 0.0))),
            np.where(m >= 14, 3.0, np.where(m >= 10, 2.0, np.where(m >= 6, 1.0, 0.0))),
        )
        return pd.Series(out, index=df.index)
    if sp == "SOCCER":
        out = np.where(m >= 70, 3.0, np.where(m >= 55, 2.0, np.where(m >= 30, 1.0, 0.0)))
        return pd.Series(out, index=df.index)
    # MLB + default
    out = np.where(m >= 28, 3.0, np.where(m >= 22, 2.0, np.where(m >= 15, 1.0, 0.0)))
    return pd.Series(out, index=df.index)


def _collect_last_minutes(df: pd.DataFrame, sport: str) -> list[pd.Series]:
    sp = _norm_sport(sport)
    cands: list[tuple[str, ...]] = [
        ("last1_minutes", "minutes_last1", "min_l1", "l1_minutes"),
        ("last2_minutes", "minutes_last2", "min_l2", "l2_minutes"),
        ("last3_minutes", "minutes_last3", "min_l3", "l3_minutes"),
    ]
    if sp == "NHL":
        cands = [
            ("last1_time_on_ice", "last1_toi", "toi_last1", "g1_toi"),
            ("last2_time_on_ice", "last2_toi", "toi_last2", "g2_toi"),
            ("last3_time_on_ice", "last3_toi", "toi_last3", "g3_toi"),
        ]
    series_list: list[pd.Series] = []
    for names in cands:
        col = _first_col(df, names)
        if sp == "NHL":
            col = _parse_toi_series(col)
        else:
            col = _to_num(col)
        series_list.append(col)
    return series_list


def _minutes_cv_series(df: pd.DataFrame, sport: str) -> pd.Series:
    last3 = _collect_last_minutes(df, sport)
    avg_l5 = _to_num(_first_col(df, ("avg_L5", "stat_last5_avg")))
    avg_l10 = _to_num(_first_col(df, ("avg_L10", "stat_last10_avg")))
    idx = df.index
    cv_vals = []
    for i in range(len(df)):
        vals = []
        for s in last3:
            v = s.iloc[i] if i < len(s) else np.nan
            if pd.notna(v):
                vals.append(float(v))
        for s in (avg_l5, avg_l10):
            v = s.iloc[i] if i < len(s) else np.nan
            if pd.notna(v):
                vals.append(float(v))
        if len(vals) < 3:
            cv_vals.append(np.nan)
            continue
        arr = np.array(vals, dtype=float)
        mu = float(np.mean(arr))
        if mu == 0 or np.isnan(mu):
            cv_vals.append(np.nan)
        else:
            cv_vals.append(float(np.std(arr, ddof=0) / mu))
    return pd.Series(cv_vals, index=idx)


def _minutes_trend_series(df: pd.DataFrame) -> pd.Series:
    avg_l5 = _to_num(_first_col(df, ("avg_L5", "stat_last5_avg")))
    avg_season = _to_num(_first_col(df, ("avg_season", "stat_season_avg")))
    ratio = avg_l5 / avg_season.replace(0, np.nan)
    return ratio.clip(0.5, 2.0)


def _prop_type_key(df: pd.DataFrame) -> pd.Series:
    return (
        _first_col(df, ("stat_type", "stat_norm", "prop_type", "prop_norm"))
        .astype(str)
        .str.strip()
        .str.lower()
    )


def _team_key(df: pd.DataFrame) -> pd.Series:
    return _first_col(df, ("team", "Team", "pp_team")).astype(str).str.strip().str.lower()


def _dominance_and_role(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    avg_l10 = _to_num(_first_col(df, ("avg_L10", "stat_last10_avg")))
    team = _team_key(df)
    ptype = _prop_type_key(df)
    tmp = pd.DataFrame({"_t": team.values, "_p": ptype.values, "_a": avg_l10.values}, index=df.index)
    grp_mean = tmp.groupby(["_t", "_p"], sort=False)["_a"].transform("mean")
    dom = avg_l10 / grp_mean.replace(0, np.nan)
    dv = dom.to_numpy(dtype=float)
    role = np.zeros(len(dv))
    role = np.where(dv >= 2.0, 4.0, role)
    role = np.where((dv >= 1.5) & (dv < 2.0), 3.0, role)
    role = np.where((dv >= 1.1) & (dv < 1.5), 2.0, role)
    role = np.where((dv >= 0.8) & (dv < 1.1), 1.0, role)
    role = np.where((dv < 0.8) & np.isfinite(dv), 0.0, role)
    role = np.where(~np.isfinite(dv), 0.0, role)
    rk = tmp.groupby(["_t", "_p"], sort=False)["_a"].rank(ascending=False, method="min")
    rk = rk.where(avg_l10.notna(), np.nan).fillna(99.0)
    return dom.astype(float), pd.Series(role, index=df.index), rk.astype(float)


def build_feature_vector(df: pd.DataFrame, sport: str) -> pd.DataFrame:
    """Returns df with all feature columns added. Original columns preserved."""
    out = df.copy()
    sp = _norm_sport(sport)

    # Preserve pipeline minutes labels (HIGH/MEDIUM/LOW) before overwriting
    # `minutes_tier` with ML numeric buckets — step8 / exports use minutes_tier_label.
    _stash_mt_labels = None
    if "minutes_tier" in out.columns:
        s = out["minutes_tier"].astype(str).str.strip().str.upper()
        if s.isin(["HIGH", "MEDIUM", "LOW", "UNKNOWN"]).any():
            _stash_mt_labels = out["minutes_tier"].astype(str)

    comp = _to_num(_first_col(out, ("composite_hit_rate", "composite_hr")))
    if comp.isna().all():
        comp = _to_num(_first_col(out, ("line_hit_rate",)))

    tier_e = _tier_encoded_series(out)
    pick_e = _pick_type_encoded_series(out)
    dirs = _direction_series(out)
    dir_e = pd.Series(np.where(dirs.eq("OVER"), 1.0, 0.0), index=out.index).astype(float)
    is_under = dirs.eq("UNDER")

    line_score = _to_num(_first_col(out, ("line_score", "line")))
    def_rank = _to_num(_first_col(out, ("def_rank", "OVERALL_DEF_RANK", "OPP_OVERALL_DEF_RANK")))
    def_te = _def_tier_encoded_series(out)

    hr5 = _scale_hit_pct(_first_col(out, ("hit_rate_over_L5", "line_hit_rate_over_ou_5")))
    hr10 = _scale_hit_pct(_first_col(out, ("hit_rate_over_L10", "line_hit_rate_over_ou_10")))
    hr5 = np.where(is_under, 1.0 - hr5, hr5)
    hr10 = np.where(is_under, 1.0 - hr10, hr10)
    hr5 = pd.Series(hr5, index=out.index)
    hr10 = pd.Series(hr10, index=out.index)

    ls_safe = line_score.replace(0, np.nan)
    avg_l5 = _to_num(_first_col(out, ("avg_L5", "stat_last5_avg")))
    avg_l10 = _to_num(_first_col(out, ("avg_L10", "stat_last10_avg")))
    av5vl = ((avg_l5 - line_score) / ls_safe).clip(-2.0, 2.0)
    av10vl = ((avg_l10 - line_score) / ls_safe).clip(-2.0, 2.0)

    # Play-side edge: same convention as prop ML (ml_play_side_edge / step7 _build_*_ml_X).
    # Raw slate edge is projection - line; good UNDERs are negative raw — flip so the feature
    # aligns with "edge toward the pick". Only explicit UNDER rows flip (missing direction unchanged).
    edge_raw = _to_num(_first_col(out, ("edge",)))
    abs_edge_in = _to_num(_first_col(out, ("abs_edge",)))
    if abs_edge_in.notna().any():
        out["abs_edge"] = abs_edge_in
    else:
        out["abs_edge"] = edge_raw.abs()
    edge = edge_raw.where(~is_under, -edge_raw)
    prop_score = _to_num(_first_col(out, ("prop_score", "rank_score", "final_score")))

    sp_e = float(SPORT_ENCODING.get(sp, 0))
    sport_enc = pd.Series(sp_e, index=out.index)

    min_tier = _minutes_tier_numeric(out, sp)
    min_cv = _minutes_cv_series(out, sp)
    min_tr = _minutes_trend_series(out)
    dom, role_e, spec_rk = _dominance_and_role(out)

    out["composite_hit_rate"] = comp
    out["tier_encoded"] = tier_e
    # 0 = pre per-group tier overhaul, 1 = post (see build_retrain_dataset.TIER_OVERHAUL_DATE). Default 0 at inference.
    era = _to_num(_first_col(out, ("tier_era",))).fillna(0.0).clip(0.0, 1.0)
    out["tier_era"] = era
    out["pick_type_encoded"] = pick_e
    out["direction_encoded"] = dir_e
    out["line_score"] = line_score
    out["def_rank"] = def_rank
    out["def_tier_encoded"] = def_te
    out["hit_rate_L5"] = hr5
    out["hit_rate_L10"] = hr10

    l10_over = _to_num(_first_col(out, ("l10_over", "line_hits_over_10", "over_L10")))
    l10_under = _to_num(_first_col(out, ("l10_under", "line_hits_under_10", "under_L10")))
    l10_pct = _to_num(_first_col(out, ("l10_over_pct",)))
    l10_total = l10_over + l10_under
    l10_pct = l10_pct.where(l10_pct.notna(), l10_over / l10_total.replace(0, np.nan))
    streak_raw = _first_col(out, ("l10_streak",)).astype(str).str.strip().str.upper()
    streak_enc = streak_raw.map(_L10_STREAK_ENC).astype(float)
    out["l10_over"] = l10_over
    out["l10_under"] = l10_under
    out["l10_over_pct"] = l10_pct
    out["l10_streak_encoded"] = streak_enc

    out["avg_L5_vs_line"] = av5vl
    out["avg_L10_vs_line"] = av10vl
    out["edge"] = edge
    out["prop_score"] = prop_score
    out["sport_encoded"] = sport_enc
    out["minutes_tier"] = min_tier
    out["minutes_cv"] = min_cv
    out["minutes_trend"] = min_tr
    out["role_type_encoded"] = role_e
    out["dominance_pct"] = dom
    out["specialization_rank"] = spec_rk

    if _stash_mt_labels is not None:
        out["minutes_tier_label"] = _stash_mt_labels

    # ── New archive-derived features: pass through if already set by step7,
    #    otherwise fill with neutral defaults so edge model inference is not broken.
    for _new_col, _neutral in (
        ("abs_edge",             None),    # already set above from edge_raw.abs()
        ("floor_clears_line",    np.nan),
        ("avg_win_margin",       np.nan),
        ("dir_line_gap_norm",    0.5),
        ("opp_hr_historical",    np.nan),
        ("player_hr_historical", np.nan),
    ):
        if _new_col not in out.columns:
            out[_new_col] = _neutral

    out = add_stratification_columns(out, df)

    # MLB enrichment encodings (NaN / 0 on non-MLB rows)
    pa = _first_col(out, ("pitcher_advantage",)).astype(str).str.strip().str.lower()
    out["pitcher_advantage_encoded"] = pa.map(_PITCHER_ADV_ENC).astype(float)

    pt = _first_col(out, ("park_tier",)).astype(str).str.strip().str.lower()
    out["park_tier_encoded"] = pt.map(_PARK_TIER_ENC).astype(float)

    wf = _first_col(out, ("weather_flag",)).astype(str).str.strip().str.lower()
    out["weather_flag_encoded"] = wf.map(_WEATHER_FLAG_ENC).astype(float)

    for col in (
        "batting_order_pos",
        "top_of_order",
        "opp_pitcher_era_vs_batter_hand",
        "opp_pitcher_k9_vs_batter_hand",
        "park_factor_overall",
        "wind_speed_mph",
        "wind_out_to_cf",
        "role_stability_score",
    ):
        if col not in out.columns:
            out[col] = np.nan
        if sp != "MLB":
            out[col] = np.nan
        elif col in ("top_of_order", "wind_out_to_cf"):
            out[col] = _to_num(out[col]).fillna(0.0)

    if sp != "MLB":
        out["pitcher_advantage_encoded"] = np.nan
        out["park_tier_encoded"] = np.nan
        out["weather_flag_encoded"] = np.nan

    pu = _first_col(out, ("pp_unit_tier",)).astype(str).str.strip().str.upper()
    out["pp_unit_tier_encoded"] = pu.map(_PP_UNIT_ENC).astype(float)

    for col in (
        "pp_toi_per_game",
        "pp_toi_pct",
        "line_combo_toi_pct",
        "line_combo_cf_pct",
        "line_combo_xgf_pct",
        "on_pp1_line",
    ):
        if col not in out.columns:
            out[col] = np.nan
        if sp != "NHL":
            out[col] = np.nan
        elif col == "on_pp1_line":
            out[col] = _to_num(out[col]).fillna(0.0)

    if sp != "NHL":
        out["pp_unit_tier_encoded"] = np.nan

    ut = _first_col(out, ("usage_tier",)).astype(str).str.strip().str.lower()
    if sp == "NBA":
        out["usage_tier_encoded"] = ut.map(_NBA_USAGE_TIER_ENC).astype(float)
    elif sp == "WNBA":
        out["usage_tier_encoded"] = ut.map(_USAGE_TIER_ENC).astype(float)
    else:
        out["usage_tier_encoded"] = np.nan

    ur = _first_col(out, ("usage_role_type",)).astype(str).str.strip().str.lower()
    out["usage_role_type_encoded"] = ur.map(_USAGE_ROLE_TYPE_ENC).astype(float)

    pc = _first_col(out, ("pace_context",)).astype(str).str.strip().str.lower()
    out["pace_context_encoded"] = pc.map(_PACE_CONTEXT_ENC).astype(float)

    gs = _first_col(out, ("game_script_context",)).astype(str).str.strip().str.lower()
    out["game_script_context_encoded"] = gs.map(_GAME_SCRIPT_ENC).astype(float)

    pm = _first_col(out, ("positional_matchup_tier",)).astype(str).str.strip().str.lower()
    out["positional_matchup_tier_encoded"] = pm.map(_POS_MATCHUP_ENC).astype(float)
    if sp == "NBA":
        out["positional_matchup_tier_encoded"] = out["positional_matchup_tier_encoded"].fillna(1.0)

    fr = _first_col(out, ("foul_trouble_risk",)).astype(str).str.strip().str.lower()
    out["foul_trouble_risk_encoded"] = fr.map(_FOUL_RISK_ENC).astype(float)

    br = _first_col(out, ("b2b_rest_context",)).astype(str).str.strip().str.lower()
    out["b2b_rest_context_encoded"] = br.map(_B2B_REST_ENC).astype(float)

    for col in ("usage_pct", "team_pace", "opp_pace", "pace_delta"):
        if col not in out.columns:
            out[col] = np.nan
        if sp not in ("NBA", "WNBA"):
            out[col] = np.nan

    for col in ("star_tier", "is_franchise_star", "b2b_flag"):
        if col not in out.columns:
            out[col] = np.nan
        if sp != "WNBA":
            out[col] = np.nan
        elif col in ("is_franchise_star", "b2b_flag"):
            out[col] = _to_num(out[col]).fillna(0.0)
        elif col == "star_tier":
            out[col] = _to_num(out[col]).fillna(2.0)

    nba_cols = (
        "reb_pct",
        "ast_pct",
        "game_pace",
        "opp_def_rating",
        "team_implied_total",
        "opp_implied_total",
        "usage_vacuum",
        "minutes_floor_L10",
        "minutes_ceil_L10",
        "minutes_cv_L10",
        "opp_pts_allowed_vs_position",
        "opp_reb_allowed_vs_position",
        "opp_ast_allowed_vs_position",
    )
    for col in nba_cols:
        if col not in out.columns:
            out[col] = np.nan
        if sp != "NBA":
            out[col] = np.nan

    for col in ("team_star_out", "key_facilitator_out", "injury_boost_candidate", "high_variance_role"):
        if col not in out.columns:
            out[col] = np.nan
        if sp != "NBA":
            out[col] = np.nan
        else:
            out[col] = _to_num(out[col]).fillna(0.0)

    if sp != "WNBA":
        out["foul_trouble_risk_encoded"] = np.nan
        out["b2b_rest_context_encoded"] = np.nan

    if sp != "NBA":
        out["usage_role_type_encoded"] = np.nan
        out["game_script_context_encoded"] = np.nan
        out["positional_matchup_tier_encoded"] = np.nan

    if sp not in ("NBA", "WNBA"):
        out["usage_tier_encoded"] = np.nan
        out["pace_context_encoded"] = np.nan

    xt = _first_col(out, ("xg_tier",)).astype(str).str.strip().str.lower()
    out["xg_tier_encoded"] = xt.map(_XG_TIER_ENC).astype(float)

    for col in ("player_xg_per90", "player_xag_per90", "player_goals_minus_xg", "player_shots_per90"):
        if col not in out.columns:
            out[col] = np.nan
        if sp != "SOCCER":
            out[col] = np.nan

    if sp != "SOCCER":
        out["xg_tier_encoded"] = np.nan

    return out


def apply_ticket_eligibility_voids(df: pd.DataFrame, sport: str) -> pd.DataFrame:
    """Set eligible=0 for ticket exclusions, including NBA directional guards."""
    out = df.copy()
    if "eligible" not in out.columns:
        return out
    sp = _norm_sport(sport)
    prev = out["eligible"].astype(int).eq(1)
    tier_u = out["tier"].astype(str).str.strip().str.upper()
    rec = _direction_series(out)
    pick_u = out.get("pick_type", pd.Series("", index=out.index)).astype(str).str.strip().str.upper()
    is_standard_pick = pick_u.eq("STANDARD")
    ml_prob = _to_num(out.get("ml_prob", pd.Series(np.nan, index=out.index))).fillna(-1.0)
    def_src = out.get("DEF_TIER", out.get("def_tier", out.get("defense_tier", pd.Series("", index=out.index))))
    def_u = def_src.astype(str).str.strip().str.upper()

    mt_raw = out.get("minutes_tier", pd.Series("", index=out.index))
    mt_num = pd.to_numeric(mt_raw, errors="coerce")
    mt_map = (
        mt_raw.astype(str).str.strip().str.upper()
        .map({"HIGH": 2.0, "MEDIUM": 1.0, "LOW": 0.0, "UNKNOWN": 0.0, "": 0.0})
    )
    mt = mt_num.where(mt_num.notna(), mt_map).fillna(0.0)

    # Global UNDER-safe policy:
    # Tier/minutes voids are over-side quality controls and should not auto-void UNDER picks.
    # Apply these only to OVER across sports.
    is_under = rec.eq("UNDER")

    # Tier D is usually excluded, but for NBA OVER allow a high-confidence exception.
    # Exception mirrors the calibrated "premium" intersection:
    #   OVER + ml_prob Q5 (>=0.71) + Above Avg defense tier.
    nba_over_tierd_exception = (
        pd.Series(False, index=out.index)
        if sp != "NBA"
        else rec.eq("OVER") & ml_prob.ge(0.71) & def_u.str.contains("ABOVE")
    )
    void_tier_d = tier_u.eq("D") & ~nba_over_tierd_exception & ~is_under
    void_min = (mt == 0) & (~tier_u.eq("A")) & ~is_under
    # NHL OVER no longer gets blanket-voided. Keep only genuinely low-confidence OVERs out.
    void_nhl_over = (
        rec.eq("OVER") & is_standard_pick & ml_prob.ge(0.0) & ml_prob.lt(0.58)
        if sp == "NHL"
        else pd.Series(False, index=out.index)
    )

    # NBA directional guards:
    # - Standard OVERs need stronger model confidence.
    # - Elite defenses suppress OVER outcomes unless probability is very high.
    # - Weak defenses hurt UNDER outcomes unless probability is very high.
    void_std_over_ml = (
        pd.Series(False, index=out.index)
        if sp != "NBA"
        else rec.eq("OVER") & pick_u.eq("STANDARD") & ml_prob.lt(0.65)
    )
    void_over_elite_def = (
        pd.Series(False, index=out.index)
        if sp != "NBA"
        else rec.eq("OVER") & def_u.str.contains("ELITE") & ml_prob.lt(0.71)
    )
    void_under_weak_def = (
        pd.Series(False, index=out.index)
        if sp != "NBA"
        else rec.eq("UNDER") & def_u.str.contains("WEAK") & ml_prob.lt(0.71)
    )

    comb = (
        void_tier_d
        | void_min
        | void_nhl_over
        | void_std_over_ml
        | void_over_elite_def
        | void_under_weak_def
    )
    hit = prev & comb
    if not hit.any():
        return out
    tags: list[str] = []
    for i in range(len(out)):
        parts: list[str] = []
        if bool(void_nhl_over.iloc[i]):
            parts.append("VOID_NHL_OVER_TICKET")
        if bool(void_tier_d.iloc[i]):
            parts.append("VOID_TIER_D")
        if bool(void_min.iloc[i]):
            parts.append("VOID_LOW_MINUTES_NON_A")
        if bool(void_std_over_ml.iloc[i]):
            parts.append("VOID_STD_OVER_LOW_ML_PROB")
        if bool(void_over_elite_def.iloc[i]):
            parts.append("VOID_OVER_ELITE_DEF_NON_Q5")
        if bool(void_under_weak_def.iloc[i]):
            parts.append("VOID_UNDER_WEAK_DEF_NON_Q5")
        tags.append(";".join(parts))
    tag_ser = pd.Series(tags, index=out.index)
    vr = out["void_reason"].astype(str).fillna("")
    new_vr = vr.copy()
    for idx in out.index[hit.to_numpy(dtype=bool)]:
        t = tag_ser.loc[idx]
        c = str(vr.loc[idx]).strip()
        if not c:
            new_vr.loc[idx] = t
            continue
        existing = [p for p in c.split(";") if p]
        incoming = [p for p in t.split(";") if p]
        merged = []
        seen = set()
        for p in (*existing, *incoming):
            if p in seen:
                continue
            seen.add(p)
            merged.append(p)
        new_vr.loc[idx] = ";".join(merged)
    out.loc[hit, "eligible"] = 0
    out["void_reason"] = new_vr
    return out


def fill_minutes_cv_median_by_sport(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "sport_encoded" not in out.columns or "minutes_cv" not in out.columns:
        return out
    for code in sorted(out["sport_encoded"].dropna().unique()):
        mask = out["sport_encoded"] == code
        med = out.loc[mask, "minutes_cv"].median()
        if pd.isna(med):
            med = out["minutes_cv"].median()
        if pd.isna(med):
            med = 0.0
        sub = mask & out["minutes_cv"].isna()
        out.loc[sub, "minutes_cv"] = med
    return out
