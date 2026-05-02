"""
Per-group A/B/C/D rank tier assignment (Goblin/Demon vs line distance, Standard vs ml_prob).

Cut points from backtest artifact data/reports/tier_criteria.json (commit 20b961f1).

When Goblin/Demon rows lack standard_line (common on Soccer alt-only slates), tier falls back
to the same ml_prob cuts as Standard so tiers remain informative.
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


def _tier_from_ml_scalar(ml_prob: object) -> str:
    """A–D from ml_prob only; NaN / invalid → D."""
    prob = _safe_float_prob(ml_prob)
    if prob >= 0.71:
        return "A"
    if prob >= 0.65:
        return "B"
    if prob >= 0.58:
        return "C"
    return "D"


def _tier_from_ml_array(ml_raw: np.ndarray) -> np.ndarray:
    """Vectorized ml_prob tiers; NaN → D."""
    t = np.full(ml_raw.shape, "D", dtype=object)
    fin = np.isfinite(ml_raw)
    t[fin & (ml_raw >= 0.71)] = "A"
    m = fin & (ml_raw < 0.71) & (ml_raw >= 0.65)
    t[m] = "B"
    m = fin & (ml_raw < 0.65) & (ml_raw >= 0.58)
    t[m] = "C"
    return t


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

    std_ok = standard_line is not None and not (
        isinstance(standard_line, float) and np.isnan(standard_line)
    )
    try:
        ln = float(line)
        ln_ok = np.isfinite(ln)
    except (TypeError, ValueError):
        ln_ok = False

    if pt == "goblin":
        if std_ok and ln_ok:
            dist = abs(float(ln) - float(standard_line))
            if dist >= 3.5:
                return "A"
            if dist >= 2.0:
                return "B"
            if dist >= 1.0:
                return "C"
            return "D"
        return _tier_from_ml_scalar(ml_prob)

    if pt == "demon":
        if std_ok and ln_ok:
            dist = abs(float(ln) - float(standard_line))
            if dist <= 1.0:
                return "A"
            if dist <= 3.0:
                return "B"
            if dist <= 6.0:
                return "C"
            return "D"
        return _tier_from_ml_scalar(ml_prob)

    return _tier_from_ml_scalar(ml_prob)


def _first_col(df: pd.DataFrame, names: list[str]) -> str | None:
    for n in names:
        if n in df.columns:
            return n
    return None


def assign_tier_column(out: pd.DataFrame, *, sport: str = "") -> pd.Series:
    """
    Resolve common column names; set ``tier`` and ``tier_source`` on ``out`` (vectorized).

    tier_source:
      - ``distance``: Goblin/Demon with valid standard_line and line
      - ``ml_prob_fallback``: Goblin/Demon missing usable distance inputs
      - ``ml_prob``: Standard and other pick types
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

    ml_raw = (
        pd.to_numeric(out[mc], errors="coerce").to_numpy(dtype=float)
        if mc
        else np.full(n, np.nan, dtype=float)
    )
    t_ml = _tier_from_ml_array(ml_raw)

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
    tier_src = np.full(n, "ml_prob", dtype=object)

    tier[is_non_gd] = t_ml[is_non_gd]

    has_gd_dist = is_gob & np.isfinite(sl) & np.isfinite(ln)
    t_gob_d = np.where(
        dist >= 3.5,
        "A",
        np.where(dist >= 2.0, "B", np.where(dist >= 1.0, "C", "D")),
    )
    tier[has_gd_dist] = t_gob_d[has_gd_dist]
    tier_src[has_gd_dist] = "distance"

    gob_fb = is_gob & ~has_gd_dist
    tier[gob_fb] = t_ml[gob_fb]
    tier_src[gob_fb] = "ml_prob_fallback"

    has_dd_dist = is_dem & np.isfinite(sl) & np.isfinite(ln)
    t_dem_d = np.where(
        dist <= 1.0,
        "A",
        np.where(dist <= 3.0, "B", np.where(dist <= 6.0, "C", "D")),
    )
    tier[has_dd_dist] = t_dem_d[has_dd_dist]
    tier_src[has_dd_dist] = "distance"

    dem_fb = is_dem & ~has_dd_dist
    tier[dem_fb] = t_ml[dem_fb]
    tier_src[dem_fb] = "ml_prob_fallback"

    out["tier_source"] = pd.Series(tier_src, index=idx, dtype=str)
    return pd.Series(tier, index=idx, dtype=str)


def report_goblin_demon_standard_line_fill(df: pd.DataFrame, tag: str) -> None:
    """Log Goblin/Demon standard_line fill rate; warn when distance tiers are rarely available."""
    pc = _first_col(df, ["pick_type", "Pick Type"])
    sc = _first_col(df, ["standard_line", "Standard Line"])
    if not pc or not sc:
        return
    pt = df[pc].astype(str).str.lower()
    gd_mask = pt.str.contains("gob", regex=False) | pt.str.contains("dem", regex=False)
    gd_rows = df.loc[gd_mask]
    if len(gd_rows) == 0:
        return
    filled = int(pd.to_numeric(gd_rows[sc], errors="coerce").notna().sum())
    total = len(gd_rows)
    pct = filled / total
    if pct < 0.50:
        print(
            f"{tag} WARNING: Goblin/Demon standard_line fill rate "
            f"{filled}/{total} ({pct:.0%}) — distance tiers unavailable for many rows, "
            f"using ml_prob fallback for {total - filled} rows"
        )
    else:
        print(f"{tag} Goblin/Demon standard_line fill: {filled}/{total} ({pct:.0%})")


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
