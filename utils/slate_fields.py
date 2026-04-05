"""
Read line and OVER/UNDER from pipeline slate rows (pandas Series from iterrows).

Avoids slate_row.get("line", fallback) when "line" exists but is NaN — same pitfall
fixed in slate_grader.grade().
"""
from __future__ import annotations

import pandas as pd


def first_numeric_in_slate_row(row: pd.Series, keys: tuple[str, ...]) -> float:
    """First non-null numeric among keys; NaN if none."""
    if not isinstance(row, pd.Series):
        return float("nan")
    for k in keys:
        if k not in row.index:
            continue
        x = pd.to_numeric(row[k], errors="coerce")
        if pd.notna(x):
            return float(x)
    return float("nan")


def first_over_under_in_slate_row(row: pd.Series, keys: tuple[str, ...]) -> str:
    """First OVER/UNDER among keys; empty string if none."""
    if not isinstance(row, pd.Series):
        return ""
    for k in keys:
        if k not in row.index:
            continue
        raw = row[k]
        if pd.isna(raw):
            continue
        v = str(raw).strip().upper()
        if v in ("OVER", "UNDER"):
            return v
    return ""
