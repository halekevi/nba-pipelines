#!/usr/bin/env python3
"""
Tier-1 stratification helpers: line buckets and data-quality flags.

Use from graded_stratification_report.py for reporting. For training / step7,
call ``add_stratification_columns`` at the end of ``build_feature_vector`` in
``edge_feature_engineering.py`` (after ``line_score`` and ``minutes_tier_label``
are set) and append the four FEATURE_COLUMNS names documented in
``STRAT_FEATURE_COLUMNS``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


STRAT_FEATURE_COLUMNS: tuple[str, ...] = (
    "line_bucket_encoded",
    "context_known",
    "defense_known",
    "minutes_known",
)


def line_bucket(line: object) -> str:
    """Stratification bucket for main line / line_score (sportsbook line)."""
    if line is None or (isinstance(line, float) and np.isnan(line)):
        return "(missing)"
    try:
        x = float(line)
    except (TypeError, ValueError):
        return "(missing)"
    if np.isnan(x):
        return "(missing)"
    if x <= 1.0:
        return "micro"
    if x <= 3.5:
        return "low"
    if x <= 7.5:
        return "mid"
    if x <= 14.5:
        return "high"
    return "xl"


_LINE_BUCKET_ENC = {
    "micro": 0.0,
    "low": 1.0,
    "mid": 2.0,
    "high": 3.0,
    "xl": 4.0,
    "(missing)": -1.0,
}


def _first_col(df: pd.DataFrame, names: tuple[str, ...]) -> pd.Series:
    idx = df.index
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series(np.nan, index=idx)


def add_stratification_columns(out: pd.DataFrame, orig: pd.DataFrame) -> pd.DataFrame:
    """
    Returns a copy of ``out`` with ``line_bucket`` (str), ``line_bucket_encoded``,
    ``context_known``, ``defense_known``, ``minutes_known`` (0/1 float).

    ``orig`` should be the input frame passed into ``build_feature_vector`` so
    pre-numeric ``minutes_tier`` labels are still available when stashing failed.
    """
    df = out.copy()
    ls = pd.to_numeric(_first_col(df, ("line_score", "line")), errors="coerce")
    lb = ls.map(line_bucket).astype(str)
    df["line_bucket"] = lb
    df["line_bucket_encoded"] = lb.map(_LINE_BUCKET_ENC).astype(float)

    pick_raw = _first_col(orig, ("pick_type",)).astype(str).str.strip().str.upper()
    df["context_known"] = (~pick_raw.isin(["", "NAN", "NONE", "(MISSING)"])).astype(float)

    def_raw = _first_col(
        orig,
        ("def_tier", "DEF_TIER", "defense_tier", "OPP_DEF_TIER", "opp_def_tier"),
    )
    sdef = def_raw.astype(str).str.strip().str.upper()
    bad = {"", "NAN", "NONE", "(MISSING)", "UNKNOWN", "NEUTRAL"}
    df["defense_known"] = (~sdef.isin(bad) & def_raw.notna()).astype(float)

    if "minutes_tier_label" in df.columns:
        ml = df["minutes_tier_label"].astype(str).str.strip().str.upper()
        df["minutes_known"] = ml.isin(["HIGH", "MEDIUM", "LOW"]).astype(float)
    else:
        pre = _first_col(orig, ("minutes_tier",)).astype(str).str.strip().str.upper()
        df["minutes_known"] = pre.isin(["HIGH", "MEDIUM", "LOW"]).astype(float)

    return df
