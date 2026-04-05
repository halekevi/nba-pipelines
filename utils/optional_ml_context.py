"""
Optional graded-row context features for prop ML training (NBA/CBB/NHL/Soccer/MLB).

Columns produced (aligned to df.index):
  pace_percentile (0–1), days_rest, line_move_direction (-1/0/1), is_back_to_back (0/1)

Missing inputs are filled neutrally so older workbooks still train.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


def _first_present(df: pd.DataFrame, options: Iterable[str]) -> str | None:
    lookup = {str(c).lower(): c for c in df.columns}
    for c in options:
        if str(c).lower() in lookup:
            return lookup[str(c).lower()]
    return None


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def optional_context_features(df: pd.DataFrame) -> pd.DataFrame:
    idx = df.index
    pace_col = _first_present(
        df,
        ("pace_percentile", "pace_pct", "Pace Percentile", "pace_vs_league_pct"),
    )
    rest_col = _first_present(df, ("days_rest", "rest_days", "Days Rest", "team_rest_days"))
    lmd_col = _first_present(
        df,
        ("line_move_direction", "line_move_toward_over", "Line Move Direction", "line_move"),
    )
    b2b_col = _first_present(df, ("is_back_to_back", "b2b", "is_b2b", "back_to_back"))

    if pace_col:
        pp = _to_num(df[pace_col])
        if pp.notna().any() and float(pp.dropna().median()) > 1.0:
            pp = pp / 100.0
        pace = pp.fillna(0.5)
    else:
        pace = pd.Series(0.5, index=idx)

    if rest_col:
        dr = _to_num(df[rest_col]).fillna(1.0)
    else:
        dr = pd.Series(1.0, index=idx)

    if lmd_col:
        raw = df[lmd_col]
        if raw.notna().any() and pd.api.types.is_numeric_dtype(raw):
            lmd = _to_num(raw).fillna(0.0)
        else:
            lm = raw.astype(str).str.lower()
            lmd = pd.Series(
                np.where(
                    lm.str.contains(r"toward|favor|over|harder", regex=True, na=False),
                    1.0,
                    np.where(
                        lm.str.contains(r"against|under|softer|easier", regex=True, na=False),
                        -1.0,
                        0.0,
                    ),
                ),
                index=idx,
            )
    else:
        lmd = pd.Series(0.0, index=idx)

    if b2b_col:
        raw_b = df[b2b_col]
        if pd.api.types.is_numeric_dtype(raw_b):
            b2b = (_to_num(raw_b).fillna(0) >= 1).astype(float)
        else:
            bb = raw_b.astype(str).str.upper().str.strip()
            b2b = pd.Series(np.where(bb.isin(["1", "TRUE", "Y", "YES", "T"]), 1.0, 0.0), index=idx)
    else:
        b2b = pd.Series(0.0, index=idx)

    return pd.DataFrame(
        {
            "pace_percentile": pace,
            "days_rest": dr,
            "line_move_direction": lmd,
            "is_back_to_back": b2b,
        },
        index=idx,
    )
