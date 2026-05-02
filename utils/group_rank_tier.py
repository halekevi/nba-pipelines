"""
Per-group A/B/C/D rank tier assignment (Goblin/Demon vs line distance, Standard vs ml_prob).

Cut points from backtest artifact data/reports/tier_criteria.json (commit 20b961f1).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _safe_float_prob(ml_prob: object) -> float:
    if ml_prob is None:
        return 0.0
    try:
        v = float(ml_prob)
    except (TypeError, ValueError):
        return 0.0
    if np.isnan(v):
        return 0.0
    return v


def _tier_from_group(
    pick_type: str,
    direction: str,
    ml_prob: float,
    line: float,
    standard_line: float | None,
) -> str:
    """
    Assign rank tier A–D based on pick_type × direction group.
    Cuts derived from analyze_tier_criteria_by_group backtest (20b961f1).
    """
    pt_raw = (pick_type or "").strip().lower()
    if "dem" in pt_raw:
        pt = "demon"
    elif "gob" in pt_raw:
        pt = "goblin"
    else:
        pt = "standard"
    _ = (direction or "").strip().upper()

    if pt == "goblin":
        if standard_line is None or (isinstance(standard_line, float) and np.isnan(standard_line)):
            return "D"
        try:
            ln = float(line)
        except (TypeError, ValueError):
            return "D"
        if np.isnan(ln):
            return "D"
        dist = abs(ln - float(standard_line))
        if dist >= 3.5:
            return "A"
        if dist >= 2.0:
            return "B"
        if dist >= 1.0:
            return "C"
        return "D"

    if pt == "demon":
        if standard_line is None or (isinstance(standard_line, float) and np.isnan(standard_line)):
            return "D"
        try:
            ln = float(line)
        except (TypeError, ValueError):
            return "D"
        if np.isnan(ln):
            return "D"
        dist = abs(ln - float(standard_line))
        if dist <= 1.0:
            return "A"
        if dist <= 3.0:
            return "B"
        if dist <= 6.0:
            return "C"
        return "D"

    prob = _safe_float_prob(ml_prob)
    if prob >= 0.71:
        return "A"
    if prob >= 0.65:
        return "B"
    if prob >= 0.58:
        return "C"
    return "D"


def _first_col(df: pd.DataFrame, names: list[str]) -> str | None:
    for n in names:
        if n in df.columns:
            return n
    return None


def assign_tier_column(out: pd.DataFrame, *, sport: str = "") -> pd.Series:
    """
    Resolve common column names and return tier labels (vectorized).

    Goblin/Demon: abs(line - standard_line) with group-specific cut directions.
    All other pick types: ml_prob thresholds (Standard-only slates, Tennis, etc.).
    """
    _ = sport
    idx = out.index
    n = len(out)

    pc = _first_col(out, ["pick_type", "Pick Type"])
    mc = _first_col(out, ["ml_prob"])
    lc = _first_col(out, ["line", "Line", "line_score"])
    sc = _first_col(out, ["standard_line", "Standard Line"])

    pt_s = out[pc].astype(str) if pc else pd.Series("Standard", index=idx)
    pt_lower = pt_s.str.strip().str.lower()
    is_dem = pt_lower.str.contains("dem", regex=False).to_numpy()
    is_gob = pt_lower.str.contains("gob", regex=False).to_numpy() & ~is_dem
    is_non_gd = ~(is_gob | is_dem)

    ml = (
        pd.to_numeric(out[mc], errors="coerce").to_numpy(dtype=float)
        if mc
        else np.full(n, np.nan, dtype=float)
    )
    ml = np.where(np.isfinite(ml), ml, 0.0)

    ln = (
        pd.to_numeric(out[lc], errors="coerce").to_numpy(dtype=float)
        if lc
        else np.full(n, np.nan, dtype=float)
    )
    sl = (
        pd.to_numeric(out[sc], errors="coerce").to_numpy(dtype=float)
        if sc
        else np.full(n, np.nan, dtype=float)
    )

    dist = np.abs(ln - sl)
    tier = np.full(n, "D", dtype=object)

    t_std = np.where(
        ml >= 0.71,
        "A",
        np.where(ml >= 0.65, "B", np.where(ml >= 0.58, "C", "D")),
    )
    tier[is_non_gd] = t_std[is_non_gd]

    g_ok = is_gob & np.isfinite(ln) & np.isfinite(sl)
    t_gob = np.where(
        dist >= 3.5,
        "A",
        np.where(dist >= 2.0, "B", np.where(dist >= 1.0, "C", "D")),
    )
    tier[g_ok] = t_gob[g_ok]

    d_ok = is_dem & np.isfinite(ln) & np.isfinite(sl)
    t_dem = np.where(
        dist <= 1.0,
        "A",
        np.where(dist <= 3.0, "B", np.where(dist <= 6.0, "C", "D")),
    )
    tier[d_ok] = t_dem[d_ok]

    return pd.Series(tier, index=idx, dtype=str)


def print_tier_distribution_by_pick_direction_group(
    out: pd.DataFrame, *, label: str = "[NBA step7]"
) -> None:
    """Console validation: tier counts for Goblin OVER / Demon OVER / Standard OVER / UNDER."""
    pc = _first_col(out, ["pick_type", "Pick Type"])
    dc = _first_col(out, ["bet_direction", "Direction", "direction", "recommended_side"])
    if "tier" not in out.columns or not pc or not dc:
        print(f"{label} Tier distribution skipped (missing columns).")
        return
    pt = out[pc].astype(str).str.lower()
    dr = out[dc].astype(str).str.upper().str.strip()
    tcol = out["tier"].astype(str).str.upper().str.strip()

    def _line(mask: pd.Series) -> str:
        vc = tcol[mask].value_counts()
        parts = [f"{k}={int(vc.get(k, 0))}" for k in ("A", "B", "C", "D")]
        return "  ".join(parts)

    print(f"{label} Tier distribution by group:")
    m_go = pt.str.contains("gob") & dr.eq("OVER")
    m_de = pt.str.contains("dem") & dr.eq("OVER")
    m_so = (~pt.str.contains("gob")) & (~pt.str.contains("dem")) & dr.eq("OVER")
    m_su = (~pt.str.contains("gob")) & (~pt.str.contains("dem")) & dr.eq("UNDER")
    print(f"  Goblin OVER:    {_line(m_go)}")
    print(f"  Demon OVER:     {_line(m_de)}")
    print(f"  Standard OVER:  {_line(m_so)}")
    print(f"  Standard UNDER: {_line(m_su)}")
