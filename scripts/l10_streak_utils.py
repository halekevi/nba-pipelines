#!/usr/bin/env python3
"""L10 hit counts vs today's line — shared by step5 and slate loaders."""

from __future__ import annotations

import numpy as np
import pandas as pd

L10_STREAK_HOT = 7
L10_STREAK_COLD = 7

_L10_COUNT_COLS = (
    "l10_over",
    "l10_under",
    "l10_over_pct",
    "l10_games_played",
    "line_hits_over_10",
    "line_hits_under_10",
)


def _coerce_l10_scalar(raw: object) -> float:
    """Coerce one L10 count/rate to a scalar float (handles array-like Excel cells)."""
    if raw is None:
        return float("nan")
    try:
        if pd.isna(raw):
            return float("nan")
    except (TypeError, ValueError):
        pass
    if isinstance(raw, (list, tuple, np.ndarray)):
        vals = pd.to_numeric(pd.Series(list(raw)), errors="coerce").dropna()
        return float(vals.iloc[0]) if len(vals) else float("nan")
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return float("nan")
        if s.startswith("[") and s.endswith("]"):
            vals = pd.to_numeric(pd.Series(s.strip("[]").split()), errors="coerce").dropna()
            return float(vals.iloc[0]) if len(vals) else float("nan")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float("nan")


def _ensure_l10_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df
    for col in _L10_COUNT_COLS:
        if col not in out.columns:
            out[col] = np.nan
    if "l10_streak" not in out.columns:
        out["l10_streak"] = pd.Series([None] * len(out), dtype=object)
    return out


def _scalarize_l10_count_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = _ensure_l10_columns(df)
    for col in _L10_COUNT_COLS:
        if col in out.columns:
            out[col] = out[col].map(_coerce_l10_scalar)
    return out


def _direction_series(df: pd.DataFrame) -> pd.Series:
    for col in ("direction", "bet_direction", "final_bet_direction", "dir"):
        if col not in df.columns:
            continue
        s = df[col]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        return s.astype(str).str.strip().str.upper()
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
    # HOT = the bet direction is hitting; COLD = the opposite side is hitting.
    if d == "UNDER":
        if un >= L10_STREAK_HOT:
            return "HOT"
        if ov >= L10_STREAK_HOT:
            return "COLD"
        return "NEUTRAL"
    if ov >= L10_STREAK_HOT:
        return "HOT"
    if un >= L10_STREAK_HOT:
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
    out = _scalarize_l10_count_columns(df.copy())
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
    out = _scalarize_l10_count_columns(out)
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
        over_ok = pd.to_numeric(out.loc[ok, "l10_over"], errors="coerce")
        under_ok = pd.to_numeric(out.loc[ok, "l10_under"], errors="coerce")
        total = over_ok + under_ok
        out.loc[ok, "l10_over_pct"] = over_ok / total.replace(0, np.nan)
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


def enrich_graded_l10_columns(df: pd.DataFrame, *, line_col: str = "line") -> pd.DataFrame:
    """Map common slate aliases and compute l10_streak for graded workbook exports."""
    if df is None or df.empty:
        return df
    out = df.copy()
    for i in range(1, 11):
        g, sg = f"G{i}", f"stat_g{i}"
        if g in out.columns and sg not in out.columns:
            out[sg] = out[g]
    return finalize_l10_ui_columns(out, line_col=line_col)


_L10_SLATE_RENAME = {
    "L10 Over": "l10_over",
    "L10 Under": "l10_under",
    "L10 Streak": "l10_streak",
    "l10_over": "l10_over",
    "l10_under": "l10_under",
    "l10_streak": "l10_streak",
    "l10_games_played": "l10_games_played",
    "line_hits_over_10": "l10_over",
    "line_hits_under_10": "l10_under",
    "over_L10": "l10_over",
    "under_L10": "l10_under",
}
