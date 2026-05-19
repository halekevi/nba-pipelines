#!/usr/bin/env python3
"""L10 hit counts vs today's line — shared by step5 and slate loaders."""

from __future__ import annotations

import numpy as np
import pandas as pd

L10_STREAK_HOT = 7
L10_STREAK_COLD = 7


def _direction_series(df: pd.DataFrame) -> pd.Series:
    if "direction" in df.columns:
        return df["direction"].astype(str).str.strip().str.upper()
    if "dir" in df.columns:
        return df["dir"].astype(str).str.strip().str.upper()
    return pd.Series(["OVER"] * len(df), index=df.index)


def compute_l10_streak_label(
    l10_over: float | None,
    l10_under: float | None,
    direction: str = "OVER",
) -> str | None:
    """HOT/COLD/NEUTRAL from hit counts and pick direction."""
    try:
        ov = float(l10_over) if l10_over is not None and not pd.isna(l10_over) else None
        un = float(l10_under) if l10_under is not None and not pd.isna(l10_under) else None
    except (TypeError, ValueError):
        return None
    if ov is None or un is None:
        return None
    total = ov + un
    if total <= 0:
        return None
    d = str(direction or "OVER").strip().upper()
    if d == "UNDER":
        if un >= L10_STREAK_COLD:
            return "COLD"
        if ov >= L10_STREAK_HOT:
            return "HOT"
    else:
        if ov >= L10_STREAK_HOT:
            return "HOT"
        if un >= L10_STREAK_COLD:
            return "COLD"
    return "NEUTRAL"


def sanitize_l10_streak_label(streak: object) -> str | None:
    """Normalize streak for JSON/UI; pandas NaN must not become the string 'NAN'."""
    if streak is None:
        return None
    try:
        if pd.isna(streak):
            return None
    except (TypeError, ValueError):
        pass
    s = str(streak).strip().upper()
    if s in ("", "NAN", "NONE"):
        return None
    return s or None


def add_l10_ui_columns(
    df: pd.DataFrame,
    *,
    line_col: str = "line",
    direction_col: str | None = None,
    min_games: int = 1,
) -> pd.DataFrame:
    """
    From stat_g1..stat_g10 vs current line, set:
      l10_over, l10_under, l10_over_pct, l10_streak, l10_games_played
    Also aliases line_hits_over_10 / line_hits_under_10 when missing.
    """
    if df is None or df.empty:
        return df
    out = df.copy()
    stat_cols = [c for c in [f"stat_g{i}" for i in range(1, 11)] if c in out.columns]
    if not stat_cols or line_col not in out.columns:
        return out

    vals = out[stat_cols].apply(pd.to_numeric, errors="coerce")
    line = pd.to_numeric(out[line_col], errors="coerce")
    played = vals.notna().sum(axis=1)
    ok = (played >= int(min_games)) & line.notna()

    over = vals.gt(line, axis=0).sum(axis=1).astype(float)
    under = vals.lt(line, axis=0).sum(axis=1).astype(float)
    total_ou = over + under
    pct = over.divide(total_ou.where(total_ou > 0))

    for col in (
        "l10_over",
        "l10_under",
        "l10_over_pct",
        "l10_games_played",
        "line_hits_over_10",
        "line_hits_under_10",
    ):
        if col not in out.columns:
            out[col] = np.nan
        else:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "l10_streak" not in out.columns:
        out["l10_streak"] = pd.Series([None] * len(out), dtype=object)
    else:
        out["l10_streak"] = out["l10_streak"].astype(object)

    out.loc[ok, "l10_over"] = over[ok]
    out.loc[ok, "l10_under"] = under[ok]
    out.loc[ok, "l10_over_pct"] = pct[ok]
    out.loc[ok, "l10_games_played"] = played[ok].astype(float)
    out.loc[ok, "line_hits_over_10"] = over[ok]
    out.loc[ok, "line_hits_under_10"] = under[ok]

    direction = _direction_series(out) if direction_col is None else out[direction_col].astype(str).str.upper()
    streaks = []
    for idx in out.index[ok]:
        streaks.append(
            compute_l10_streak_label(
                out.at[idx, "l10_over"],
                out.at[idx, "l10_under"],
                str(direction.at[idx]),
            )
        )
    out.loc[ok, "l10_streak"] = streaks
    return out


def finalize_l10_ui_columns(df: pd.DataFrame, *, line_col: str = "line") -> pd.DataFrame:
    """
    Ensure l10_over/under/pct/streak exist: prefer stat_g* vs line, else alias
    line_hits_over_10 / over_L10 style columns already on the frame.
    """
    if df is None or df.empty:
        return df
    out = add_l10_ui_columns(df, line_col=line_col, min_games=1)
    if out is df:
        out = df.copy()
    if "l10_streak" in out.columns:
        out["l10_streak"] = out["l10_streak"].astype(object)

    alias_pairs = (
        ("line_hits_over_10", "line_hits_under_10"),
        ("over_L10", "under_L10"),
        ("L10 Over", "L10 Under"),
    )
    for over_col, under_col in alias_pairs:
        if over_col not in out.columns:
            continue
        ov = pd.to_numeric(out[over_col], errors="coerce")
        un = (
            pd.to_numeric(out[under_col], errors="coerce")
            if under_col in out.columns
            else np.nan
        )
        mask = ov.notna() & out["l10_over"].isna()
        if mask.any():
            out.loc[mask, "l10_over"] = ov[mask]
            if under_col in out.columns:
                out.loc[mask, "l10_under"] = un[mask]
            else:
                out.loc[mask, "l10_under"] = 10.0 - ov[mask]

    direction = _direction_series(out)
    if "l10_over_pct" not in out.columns:
        out["l10_over_pct"] = np.nan
    if "l10_streak" not in out.columns:
        out["l10_streak"] = pd.Series([None] * len(out), dtype=object)
    else:
        out["l10_streak"] = out["l10_streak"].astype(object)

    ok = out["l10_over"].notna() & out["l10_under"].notna()
    if ok.any():
        total = out.loc[ok, "l10_over"] + out.loc[ok, "l10_under"]
        out.loc[ok, "l10_over_pct"] = out.loc[ok, "l10_over"] / total.replace(0, np.nan)
        streaks = []
        for idx in out.index[ok]:
            streaks.append(
                compute_l10_streak_label(
                    out.at[idx, "l10_over"],
                    out.at[idx, "l10_under"],
                    str(direction.at[idx]),
                )
            )
        out.loc[ok, "l10_streak"] = streaks
    return out
