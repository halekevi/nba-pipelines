"""
Per-group A/B/C/D rank tier assignment (Goblin/Demon vs line distance, Standard vs ml_prob).

Default cut points from backtest artifact data/reports/tier_criteria.json (commit 20b961f1).
MLB Demon ml_prob fallback cuts from data/reports/tier_criteria_mlb_per_date.json (commit 25e0bd78).
MLB Standard (+ Goblin fallback) cuts are lower than DEFAULT: MLB prop_model + calibrator outputs are
compressed vs NBA-scale probs — DEFAULT A_cut 0.71 produced no Standard tier-A rows.

When Goblin/Demon rows lack standard_line (common on Soccer alt-only slates), tier falls back
to ml_prob cuts (sport- and group-specific where configured).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Default A/B/C thresholds on ml_prob (sports without overrides).
DEFAULT_ML_PROB_CUTS: tuple[float, float, float] = (0.71, 0.65, 0.58)
# Direction-separated Standard profiles.
DEFAULT_STANDARD_OVER_CUTS: tuple[float, float, float] = (0.73, 0.69, 0.63)
DEFAULT_STANDARD_UNDER_CUTS: tuple[float, float, float] = (0.71, 0.67, 0.61)

# Format: sport_slug → {group: (A_cut, B_cut, C_cut)} | (A_cut, B_cut, C_cut)
# Plain tuple → all groups for that sport. Dict → group-specific; missing groups → DEFAULT.
SPORT_ML_PROB_CUTS: dict[str, tuple[float, float, float] | dict[str, tuple[float, float, float]]] = {
    "soccer": (0.45, 0.35, 0.25),  # all groups; soccer base rates are low
    "mlb": {
        # Demon fallback when standard_line missing; post-prop-model threshold scan (2026-05)
        # validated ~+0.15 lift at >=0.65, ~+0.11 at >=0.58 — see tier_criteria_mlb_per_date.json decision_notes
        "demon": (0.65, 0.60, 0.58),
        # Same MLB probability scale as Demon fallback; DEFAULT (0.71,…) yields ~zero Standard A’s.
        "standard": (0.58, 0.52, 0.47),
        "goblin": (0.58, 0.52, 0.47),  # distance fallback when no standard_line; same scale as Standard
    },
}

# Optional Standard direction overrides by sport.
SPORT_STANDARD_DIRECTION_CUTS: dict[str, dict[str, tuple[float, float, float]]] = {
    # MLB standard probabilities are compressed vs NBA-scale outputs.
    "mlb": {
        "OVER": (0.58, 0.52, 0.47),
        "UNDER": (0.56, 0.50, 0.45),
    },
}


def _resolve_ml_prob_cuts(sport: str, pick_type: str | None) -> tuple[float, float, float]:
    sport_key = (sport or "").strip().lower()
    sport_entry = SPORT_ML_PROB_CUTS.get(sport_key)
    if sport_entry is None:
        return DEFAULT_ML_PROB_CUTS
    if isinstance(sport_entry, tuple):
        return sport_entry
    if isinstance(sport_entry, dict):
        pt_key = (pick_type or "").lower()
        # MLB/SPORT_ML_PROB_CUTS dict keys (standard, goblin, demon) are explicit; missing key → DEFAULT
        return sport_entry.get(pt_key, DEFAULT_ML_PROB_CUTS)
    return DEFAULT_ML_PROB_CUTS


def _resolve_standard_direction_cuts(sport: str, direction: str | None) -> tuple[float, float, float]:
    sport_key = (sport or "").strip().lower()
    d = (direction or "").strip().upper()
    sp = SPORT_STANDARD_DIRECTION_CUTS.get(sport_key, {})
    if d in sp:
        return sp[d]
    if d == "UNDER":
        return DEFAULT_STANDARD_UNDER_CUTS
    return DEFAULT_STANDARD_OVER_CUTS


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


def _tier_from_ml_scalar(
    ml_prob: object, a_cut: float, b_cut: float, c_cut: float
) -> str:
    """A–D from ml_prob only; NaN / invalid → D."""
    prob = _safe_float_prob(ml_prob)
    if prob >= a_cut:
        return "A"
    if prob >= b_cut:
        return "B"
    if prob >= c_cut:
        return "C"
    return "D"


def _tier_from_ml_array(
    ml_raw: np.ndarray, a_cut: float, b_cut: float, c_cut: float
) -> np.ndarray:
    """Vectorized ml_prob tiers; NaN → D."""
    t = np.full(ml_raw.shape, "D", dtype=object)
    fin = np.isfinite(ml_raw)
    t[fin & (ml_raw >= a_cut)] = "A"
    m = fin & (ml_raw < a_cut) & (ml_raw >= b_cut)
    t[m] = "B"
    m = fin & (ml_raw < b_cut) & (ml_raw >= c_cut)
    t[m] = "C"
    return t


def _tier_from_group(
    pick_type: str,
    direction: str,
    ml_prob: float,
    line: float,
    standard_line: float | None,
    *,
    sport: str = "",
) -> str:
    """
    Assign rank tier A–D based on pick_type × direction group.
    Cuts derived from analyze_tier_criteria_by_group backtest (20b961f1);
    MLB Demon ml_prob fallback from tier_criteria_mlb_per_date (25e0bd78).
    """
    pt_raw = (pick_type or "").strip().lower()
    if "dem" in pt_raw:
        pt = "demon"
    elif "gob" in pt_raw:
        pt = "goblin"
    else:
        pt = "standard"
    _ = (direction or "").strip().upper()
    cuts = _resolve_ml_prob_cuts(sport, pt)

    std_ok = standard_line is not None and not (
        isinstance(standard_line, float) and np.isnan(standard_line)
    )
    try:
        ln = float(line)
        ln_ok = np.isfinite(ln)
    except (TypeError, ValueError):
        ln_ok = False
        ln = float("nan")

    if pt == "goblin":
        if std_ok and ln_ok:
            dist = abs(ln - float(standard_line))
            if dist >= 3.5:
                return "A"
            if dist >= 2.0:
                return "B"
            if dist >= 1.0:
                return "C"
            return "D"
        return _tier_from_ml_scalar(ml_prob, *cuts)

    if pt == "demon":
        if std_ok and ln_ok:
            dist = abs(ln - float(standard_line))
            if dist <= 1.0:
                return "A"
            if dist <= 3.0:
                return "B"
            if dist <= 6.0:
                return "C"
            return "D"
        return _tier_from_ml_scalar(ml_prob, *cuts)

    return _tier_from_ml_scalar(ml_prob, *_resolve_standard_direction_cuts(sport, direction))


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
    idx = out.index
    n = len(out)

    pc = _first_col(out, ["pick_type", "Pick Type"])
    mc = _first_col(out, ["ml_prob"])
    lc = _first_col(out, ["line", "Line", "line_score"])
    sc = _first_col(out, ["standard_line", "Standard Line"])
    dc = _first_col(out, ["bet_direction", "Direction", "direction", "recommended_side"])

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
    c_std = _resolve_ml_prob_cuts(sport, "standard")
    c_gob = _resolve_ml_prob_cuts(sport, "goblin")
    c_dem = _resolve_ml_prob_cuts(sport, "demon")
    t_std = _tier_from_ml_array(ml_raw, *c_std)
    c_std_over = _resolve_standard_direction_cuts(sport, "OVER")
    c_std_under = _resolve_standard_direction_cuts(sport, "UNDER")
    t_std_over = _tier_from_ml_array(ml_raw, *c_std_over)
    t_std_under = _tier_from_ml_array(ml_raw, *c_std_under)
    t_gob_fb = _tier_from_ml_array(ml_raw, *c_gob)
    t_dem_fb = _tier_from_ml_array(ml_raw, *c_dem)
    dr = (
        out[dc].astype(str).str.upper().str.strip().to_numpy()
        if dc
        else np.full(n, "OVER", dtype=object)
    )

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

    std_over = is_non_gd & (dr != "UNDER")
    std_under = is_non_gd & (dr == "UNDER")
    tier[std_over] = t_std_over[std_over]
    tier[std_under] = t_std_under[std_under]

    has_gd_dist = is_gob & np.isfinite(sl) & np.isfinite(ln)
    t_gob_d = np.where(
        dist >= 3.5,
        "A",
        np.where(dist >= 2.0, "B", np.where(dist >= 1.0, "C", "D")),
    )
    tier[has_gd_dist] = t_gob_d[has_gd_dist]
    tier_src[has_gd_dist] = "distance"

    gob_fb = is_gob & ~has_gd_dist
    tier[gob_fb] = t_gob_fb[gob_fb]
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
    tier[dem_fb] = t_dem_fb[dem_fb]
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
