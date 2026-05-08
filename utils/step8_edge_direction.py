"""
Shared step8 reconciliation: signed Edge from Projection − Line, Abs Edge for sorts/UI.

All sport step8 scripts should refresh ``edge`` and ``abs_edge`` from projection/line when
both parse, so Direction (sign of edge) cannot drift when upstream XLSX carries a
mis-signed magnitude-only Edge.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def reconcile_signed_edge_abs_dataframe(
    out: pd.DataFrame,
    *,
    projection_col: str = "projection",
    line_col: str = "line",
    edge_col: str = "edge",
    abs_edge_col: str = "abs_edge",
) -> pd.DataFrame:
    """
    Prefer signed ``edge_col`` = projection − line when both parse as numbers.
    Rows missing either revert to whatever ``edge_col`` already contained (if present).
    Always sets ``abs_edge_col`` = |edge| afterward.

    Mutates ``out`` in place and returns ``out``.
    """
    idx = out.index

    proj = (
        pd.to_numeric(out[projection_col], errors="coerce")
        if projection_col in out.columns
        else pd.Series(np.nan, index=idx)
    )
    line = (
        pd.to_numeric(out[line_col], errors="coerce")
        if line_col in out.columns
        else pd.Series(np.nan, index=idx)
    )
    signed_gap = proj - line
    has_pl = proj.notna() & line.notna()

    if edge_col not in out.columns:
        out[edge_col] = signed_gap
    else:
        existing = pd.to_numeric(out[edge_col], errors="coerce")
        out[edge_col] = signed_gap.where(has_pl, existing)

    edge_num = pd.to_numeric(out[edge_col], errors="coerce")
    out[abs_edge_col] = edge_num.abs()
    return out
