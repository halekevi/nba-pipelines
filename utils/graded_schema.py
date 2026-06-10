from __future__ import annotations

from typing import Any

import pandas as pd


FIELD_ALIASES: dict[str, list[str]] = {
    "direction": [
        "direction",
        "Direction",
        "bet_direction",
        "Dir",
        "DIRECTION",
        "Bet Direction",
        "final_bet_direction",
        "model_dir",
    ],
    "def_tier": [
        "def_tier",
        "Def Tier",
        "DEF_TIER",
        "defense_tier",
        "opp_def_tier",
        "Opp Def Tier",
        "opponent_def_tier",
        "DefTier",
    ],
    "minutes_tier": [
        "minutes_tier",
        "Min Tier",
        "MIN_TIER",
        "min_tier",
        "Minutes Tier",
        "MinTier",
    ],
    "pick_type": [
        "pick_type",
        "Pick Type",
        "PICK_TYPE",
        "pick_cat",
        "PickType",
        "type",
    ],
    "tier": [
        "tier",
        "Tier",
        "rank_tier",
        "Rank Tier",
        "TIER",
        "RankTier",
        "pp_tier",
    ],
    "prop_type": [
        "prop_type",
        "prop_type_norm",
        "Prop",
        "prop",
        "PROP",
        "prop_norm",
        "Prop Type",
        "prop_type_raw",
    ],
    "actual": [
        "actual",
        "Actual",
        "actual_value",
        "Actual Value",
        "ACTUAL",
    ],
    "result": [
        "result",
        "Result",
        "grade_raw",
        "Grade",
        "RESULT",
        "leg_result",
        "actual_status",
    ],
    "player": [
        "player",
        "Player",
        "PLAYER",
        "player_name",
        "PlayerName",
    ],
    "team": [
        "team",
        "Team",
        "TEAM",
        "team_abbr",
    ],
    "line": [
        "line",
        "Line",
        "LINE",
        "prop_line",
        "line_num",
        "standard_line",
    ],
    "sport": [
        "sport",
        "Sport",
        "SPORT",
        "league",
    ],
    "l5_over": [
        "l5_over",
        "L5 Over",
        "last5_over",
    ],
    "l5_under": [
        "l5_under",
        "L5 Under",
        "last5_under",
    ],
    "l10_over": [
        "l10_over",
        "L10 Over",
        "line_hits_over_10",
        "over_L10",
        "over_L10_raw",
    ],
    "l10_under": [
        "l10_under",
        "L10 Under",
        "line_hits_under_10",
        "under_L10",
        "under_L10_raw",
    ],
    "l10_games_played": [
        "l10_games_played",
        "line_games_played_10",
        "Games (10g)",
        "sample_L10",
    ],
    "l10_streak": [
        "l10_streak",
        "L10 Streak",
    ],
    "hit_rate": [
        "hit_rate",
        "Hit Rate",
        "Hit Rate (5g)",
        "last5_hit_rate",
        "line_hit_rate_over_ou_5",
        "composite_hr",
    ],
    "strat_hit_rate": [
        "strat_hit_rate",
    ],
    "strat_n": [
        "strat_n",
    ],
    "hit_rate_l5": [
        "hit_rate_l5",
        "Hit Rate L5",
    ],
    "hit_rate_l10": [
        "hit_rate_l10",
        "Hit Rate L10",
    ],
    "player_hr_historical": [
        "player_hr_historical",
        "Player HR Hist",
    ],
    "opp_hr_historical": [
        "opp_hr_historical",
        "Opp HR Hist",
    ],
    "consistency_grade": [
        "consistency_grade",
        "Consistency Grade",
        "CONSISTENCY_GRADE",
    ],
    "team_top3_rank": [
        "team_top3_rank",
        "Top3 Rank",
        "top3_rank",
    ],
    "team_bottom3_rank": [
        "team_bottom3_rank",
        "Bottom3 Rank",
        "bottom3_rank",
    ],
    "top3_weak_overperformer": [
        "top3_weak_overperformer",
        "Top3 Weak Over",
    ],
    "top3_elite_fader": [
        "top3_elite_fader",
        "Top3 Elite Fade",
    ],
    "def_boost_hist": [
        "def_boost_hist",
        "Def Boost Hist",
        "def_boost",
    ],
}


def _strip_push_void_reason(reason: object) -> str:
    parts = [
        p.strip()
        for p in str(reason or "").replace(",", ";").split(";")
        if p.strip() and p.strip().upper() != "PUSH"
    ]
    return "; ".join(parts)


def normalize_push_results(df: pd.DataFrame) -> pd.DataFrame:
    """
    Upgrade legacy rows where slate_grader used result=VOID + void_reason=PUSH
    to canonical result=PUSH. Clears PUSH from void-reason columns.
    """
    if "result" not in df.columns:
        return df
    out = df.copy()
    res = out["result"].astype(str).str.strip().str.upper()
    reason_col = None
    for c in ("void_reason_grade", "void_reason", "reason"):
        if c in out.columns:
            reason_col = c
            break
    mask = res == "VOID"
    if reason_col:
        vr = out[reason_col].astype(str).str.strip().str.upper()
        mask = mask & vr.str.contains("PUSH", regex=False)
    elif "actual" in out.columns and "line" in out.columns:
        actual = pd.to_numeric(out["actual"], errors="coerce")
        line = pd.to_numeric(out["line"], errors="coerce")
        mask = mask & actual.notna() & line.notna() & (actual == line)
    else:
        return out
    if not mask.any():
        return out
    out.loc[mask, "result"] = "PUSH"
    for c in ("void_reason_grade", "void_reason", "reason"):
        if c in out.columns:
            out.loc[mask, c] = out.loc[mask, c].map(_strip_push_void_reason)
    return out


def normalize_graded_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rename columns in a graded workbook DataFrame to canonical names.
    Only renames if canonical name not already present.
    Does not drop or modify any existing columns.
    """
    out = df
    for canonical, aliases in FIELD_ALIASES.items():
        if canonical in out.columns:
            continue
        for alias in aliases:
            if alias in out.columns:
                out = out.rename(columns={alias: canonical})
                break
    return normalize_push_results(out)


def _known_mask(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip().str.upper()
    return series.notna() & ~s.isin({"", "UNKNOWN", "NAN", "NONE", "NAT"})


def coverage_report(df: pd.DataFrame, fields: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """
    Return % of rows with non-null, non-UNKNOWN values per field.
    """
    if fields is None:
        fields = ["direction", "def_tier", "minutes_tier", "pick_type", "tier", "result"]
    report: dict[str, dict[str, Any]] = {}
    total = len(df)
    for f in fields:
        if f not in df.columns:
            report[f] = {"known": 0, "total": total, "pct": 0.0, "note": "column missing"}
            continue
        known = _known_mask(df[f])
        report[f] = {"known": int(known.sum()), "total": total, "pct": round(float(known.mean() * 100.0), 1)}
    return report


def recover_direction_if_missing(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fill missing direction from bet_direction or derive from actual vs line.
    Adds direction_source: explicit / derived / unknown.
    """
    out = df.copy()
    if "direction" not in out.columns:
        out["direction"] = pd.NA
    if "direction_source" not in out.columns:
        out["direction_source"] = "unknown"

    dir_s = out["direction"].astype(str).str.strip().str.upper()
    missing = out["direction"].isna() | dir_s.isin({"", "UNKNOWN", "NAN", "NONE", "NAT"})

    if "bet_direction" in out.columns:
        bd = out["bet_direction"].astype(str).str.strip().str.upper()
        bd_valid = ~bd.isin({"", "UNKNOWN", "NAN", "NONE", "NAT"})
        use_bd = missing & bd_valid
        out.loc[use_bd, "direction"] = bd.loc[use_bd]
        out.loc[use_bd, "direction_source"] = "explicit"
        missing = out["direction"].isna() | out["direction"].astype(str).str.strip().str.upper().isin(
            {"", "UNKNOWN", "NAN", "NONE", "NAT"}
        )

    if "actual" in out.columns and "line" in out.columns:
        actual = pd.to_numeric(out["actual"], errors="coerce")
        line = pd.to_numeric(out["line"], errors="coerce")
        can_derive = missing & actual.notna() & line.notna()
        out.loc[can_derive, "direction"] = (actual.loc[can_derive] > line.loc[can_derive]).map(
            {True: "OVER", False: "UNDER"}
        )
        out.loc[can_derive, "direction_source"] = "derived"

    dir_s2 = out["direction"].astype(str).str.strip().str.upper()
    explicit = ~dir_s2.isin({"", "UNKNOWN", "NAN", "NONE", "NAT"})
    out.loc[explicit & out["direction_source"].eq("unknown"), "direction_source"] = "explicit"
    out.loc[~explicit, "direction"] = "UNKNOWN"
    out.loc[~explicit, "direction_source"] = "unknown"
    return out
