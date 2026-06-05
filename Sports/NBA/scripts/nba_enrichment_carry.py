"""Preserve step4b/4d usage/pace/injury columns through NBA1H and NBA1Q pipeline steps 5-8."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

ENRICHMENT_CARRY_COLS = [
    "usage_pct",
    "usage_tier",
    "usage_role_type",
    "usage_vacuum",
    "ast_pct",
    "reb_pct",
    "team_pace",
    "game_pace",
    "pace_context",
    "pace_delta",
    "opp_pace",
    "opp_def_rating",
    "opp_pts_allowed_vs_position",
    "opp_ast_allowed_vs_position",
    "opp_reb_allowed_vs_position",
    "positional_matchup_tier",
    "team_star_out",
    "key_facilitator_out",
    "injury_boost_candidate",
    "high_variance_role",
    "deviation_level",
]


def is_nba_period_pipeline(source: str | Path, df: pd.DataFrame) -> bool:
    """True for NBA1H or NBA1Q period sub-slate paths (injury/usage carry)."""
    hint = str(source or "").lower()
    if "nba1h" in hint or "nba1q" in hint:
        return True
    if "sport" in df.columns:
        sports = df["sport"].astype(str).str.strip().str.upper()
        if len(sports) and sports.isin(["NBA1H", "NBA1Q"]).all():
            return True
    return False


def is_nba1h_pipeline(source: str | Path, df: pd.DataFrame) -> bool:
    """Backward-compatible alias for is_nba_period_pipeline."""
    return is_nba_period_pipeline(source, df)


def _player_team_cols(df: pd.DataFrame) -> tuple[str | None, str | None]:
    player_col = next((c for c in ("player", "Player") if c in df.columns), None)
    team_col = next((c for c in ("team", "Team", "team_abbr") if c in df.columns), None)
    return player_col, team_col


def snapshot_enrichment_carry(df: pd.DataFrame) -> tuple[list[str], pd.DataFrame | None]:
    player_col, team_col = _player_team_cols(df)
    if not player_col or not team_col:
        return [], None
    carry_cols = [c for c in ENRICHMENT_CARRY_COLS if c in df.columns]
    if not carry_cols:
        return [], None
    carry_df = df[[player_col, team_col] + carry_cols].copy()
    carry_df = carry_df.rename(columns={player_col: "player", team_col: "team"})
    carry_df = carry_df.drop_duplicates(subset=["player", "team"], keep="first")
    return carry_cols, carry_df


def _col_effectively_empty(series: pd.Series) -> bool:
    if series is None:
        return True
    return int(pd.to_numeric(series, errors="coerce").notna().sum()) == 0


def reattach_enrichment_carry(
    out_df: pd.DataFrame,
    carry_df: pd.DataFrame | None,
    carry_cols: list[str],
    *,
    label: Any = "",
) -> pd.DataFrame:
    if carry_df is None or not carry_cols:
        return out_df

    restore_cols = [
        c
        for c in carry_cols
        if c in carry_df.columns
        and (c not in out_df.columns or _col_effectively_empty(out_df[c]))
    ]
    if not restore_cols:
        return out_df

    player_col, team_col = _player_team_cols(out_df)
    if not player_col or not team_col:
        print(f"⚠️  [NBA period carry] skip reattach ({label}): no player/team on output")
        return out_df

    drop_restore = [c for c in restore_cols if c in out_df.columns]
    merge_left = out_df.drop(columns=drop_restore, errors="ignore")
    merged = merge_left.merge(
        carry_df[["player", "team"] + restore_cols],
        left_on=[player_col, team_col],
        right_on=["player", "team"],
        how="left",
    )
    lead_col = restore_cols[0]
    filled = int(pd.to_numeric(merged[lead_col], errors="coerce").notna().sum())
    if filled == 0:
        print(f"⚠️  [NBA period carry] no matches for {restore_cols} ({label})")
    else:
        print(f"[NBA period carry] reattached {restore_cols} ({filled}/{len(out_df)} rows) — {label}")

    out = out_df.copy()
    for col in restore_cols:
        out[col] = merged[col].values
    return out
