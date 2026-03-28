#!/usr/bin/env python3
"""
step7_rank_props_soccer.py  (Soccer Pipeline)

Mirrors NBA step7_rank_props.py with soccer-specific:
  - Prop weights tuned for soccer variance
  - Hit rate priors from soccer data
  - Position-aware defense adjustment
  - GK props (saves) treated separately

Run:
  py -3.14 step7_rank_props_soccer.py \
    --input step6_soccer_role_context.csv \
    --output step7_soccer_ranked.xlsx
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd


def _to_num(s):
    return pd.to_numeric(s, errors="coerce")


# --- Player consistency (data/cache/player_consistency.db) ---
import sys as _sys_pc_soc


def _repo_root_pc_soc() -> Path:
    here = Path(__file__).resolve()
    for anc in here.parents:
        if (anc / "scripts" / "build_player_consistency.py").is_file():
            return anc
    return here.parents[2]


def _load_bpc_pc_soc():
    root = _repo_root_pc_soc()
    sd = str(root / "scripts")
    if sd not in _sys_pc_soc.path:
        _sys_pc_soc.path.insert(0, sd)
    import build_player_consistency as bpc  # noqa: E402

    return bpc


_bpc_pc_soc_mod = None


def _bpc_pc_soc():
    global _bpc_pc_soc_mod
    if _bpc_pc_soc_mod is None:
        try:
            _bpc_pc_soc_mod = _load_bpc_pc_soc()
        except Exception:
            _bpc_pc_soc_mod = False
    return _bpc_pc_soc_mod


def _normalize_prop_type(raw: str) -> str:
    m = _bpc_pc_soc()
    if not m:
        return str(raw or "").strip()
    return m._normalize_prop_type(str(raw), "Soccer")


def _get_line_bucket(prop_type: str, line: float, sport: str) -> str:
    m = _bpc_pc_soc()
    if not m:
        return "<5"
    try:
        ln = float(line)
    except (TypeError, ValueError):
        ln = 0.0
    return m.get_line_bucket(prop_type, ln, sport)


def _get_consistency_grade(player: str, sport: str, prop_type: str, direction: str, line: float) -> str:
    import sqlite3

    repo_root = _repo_root_pc_soc()
    db_path = repo_root / "data" / "cache" / "player_consistency.db"
    if not db_path.exists():
        return "?"

    prop_type = _normalize_prop_type(prop_type)
    direction = direction.upper().strip()
    bucket = _get_line_bucket(prop_type, line, sport)

    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute(
            """
            SELECT grade, grade_locked, games_since_F
            FROM player_consistency
            WHERE player_name = ?
              AND sport = ?
              AND prop_type = ?
              AND direction = ?
              AND line_bucket = ?
        """,
            (player, sport, prop_type, direction, bucket),
        )
        row = cur.fetchone()
        conn.close()
        if row:
            grade, locked, games_since_F = row
            return grade or "?"
        return "?"
    except Exception:
        return "?"


def _pc_grade_cache_soc(sport: str) -> dict:
    import sqlite3

    dbp = _repo_root_pc_soc() / "data" / "cache" / "player_consistency.db"
    if not dbp.is_file():
        return {}
    try:
        conn = sqlite3.connect(str(dbp))
        cur = conn.execute(
            "SELECT player_name, sport, prop_type, direction, line_bucket, grade FROM player_consistency WHERE sport = ?",
            (sport,),
        )
        d = {(a, b, c, d0, e): (g if g else "?") for a, b, c, d0, e, g in cur.fetchall()}
        conn.close()
        return d
    except Exception:
        return {}


def _apply_consistency_grade_scores_soc(out: pd.DataFrame, sport: str) -> None:
    grade_multiplier = {
        "S": 1.25,
        "A": 1.15,
        "B": 1.05,
        "C": 1.00,
        "D": 0.80,
        "F": 0.00,
        "?": 0.95,
    }
    cache = _pc_grade_cache_soc(sport)
    pc = next((c for c in ("player_norm", "player", "pp_player", "player_name") if c in out.columns), None)
    prop_col = "prop_norm" if "prop_norm" in out.columns else ("prop_type" if "prop_type" in out.columns else None)
    if pc is None or prop_col is None or "bet_direction" not in out.columns or "line" not in out.columns:
        out["consistency_grade"] = "?"
        out["consistency_multiplier"] = 0.95
        out["final_score"] = _to_num(out.get("final_score", pd.Series(np.nan, index=out.index))) * 0.95
        return

    players = out[pc].astype(str).str.strip()
    prop_raw = out[prop_col].astype(str)
    dirs = out["bet_direction"].astype(str).str.strip().str.upper()
    linev = _to_num(out["line"]).fillna(0.0)
    grades: list[str] = []
    for i in range(len(out)):
        ptype = _normalize_prop_type(prop_raw.iloc[i])
        try:
            ln = float(linev.iloc[i])
        except (TypeError, ValueError):
            ln = 0.0
        bkt = _get_line_bucket(ptype, ln, sport)
        g = cache.get((players.iloc[i], sport, ptype, dirs.iloc[i], bkt), "?")
        grades.append(g)
    gser = pd.Series(grades, index=out.index)
    mult = gser.map(lambda x: grade_multiplier.get(x, 0.95)).astype(float)
    out["consistency_grade"] = gser
    out["consistency_multiplier"] = mult
    out["final_score"] = _to_num(out["final_score"]).astype(float) * mult


def _norm_pick_type(x: str) -> str:
    t = (str(x) if x is not None else "").strip().lower()
    if "gob" in t: return "Goblin"
    if "dem" in t: return "Demon"
    return "Standard"


def _forced_over_only(pick_type: str) -> int:
    return 1 if _norm_pick_type(pick_type) in ("Goblin", "Demon") else 0


# ── Soccer prop weights ───────────────────────────────────────────────────────
# Higher = more predictable/valuable prop type

# ── Head-to-Head (H2H) utility ────────────────────────────────────────────────
def _attach_h2h(df: "pd.DataFrame", cache_path: str, sport: str,
                player_col: str, opp_col: str, prop_col: str, line_col: str) -> "pd.DataFrame":
    """
    Attach H2H stats per row: how did this player perform vs this opponent
    historically in the boxscore cache?

    Adds columns:
      h2h_games      – number of H2H games found
      h2h_avg        – player's average stat value vs this opp
      h2h_over_rate  – fraction of those games where they hit OVER the current line
      h2h_last        – most recent game value vs this opp
    """
    import os, pandas as pd, numpy as np

    if not cache_path or not os.path.exists(cache_path):
        df["h2h_games"]     = 0
        df["h2h_avg"]       = np.nan
        df["h2h_over_rate"] = np.nan
        df["h2h_last"]      = np.nan
        return df

    try:
        cache = pd.read_csv(cache_path, low_memory=False)
    except Exception:
        df["h2h_games"]     = 0
        df["h2h_avg"]       = np.nan
        df["h2h_over_rate"] = np.nan
        df["h2h_last"]      = np.nan
        return df

    # Normalise cache columns: need player, opponent, stat_type, value, date
    cache.columns = [c.lower().strip() for c in cache.columns]

    # Detect player col in cache
    p_col  = next((c for c in ["player_norm","player_name","player","name"] if c in cache.columns), None)
    o_col  = next((c for c in ["opp_team","opp","opponent","opp_team_abbr"] if c in cache.columns), None)
    s_col  = next((c for c in ["stat_type","stat","prop_type","stat_norm"]   if c in cache.columns), None)
    v_col  = next((c for c in ["value","stat_value","actual","val"]          if c in cache.columns), None)
    d_col  = next((c for c in ["date","game_date","event_date"]              if c in cache.columns), None)

    if not all([p_col, o_col, v_col]):
        df["h2h_games"]     = 0
        df["h2h_avg"]       = np.nan
        df["h2h_over_rate"] = np.nan
        df["h2h_last"]      = np.nan
        return df

    cache[v_col] = pd.to_numeric(cache[v_col], errors="coerce")

    def _norm(x):
        return str(x).strip().lower() if x and str(x).strip() else ""

    # Build lookup: (player_norm, opp_norm) -> list of (value, date)
    lookup: dict = {}
    for row in cache.itertuples(index=False):
        pk = (_norm(getattr(row, p_col)), _norm(getattr(row, o_col)))
        v  = getattr(row, v_col)
        dt = getattr(row, d_col, "") if d_col else ""
        if pk not in lookup:
            lookup[pk] = []
        lookup[pk].append((v, str(dt)))

    h2h_games, h2h_avg, h2h_over, h2h_last = [], [], [], []

    for _, r in df.iterrows():
        player = _norm(r.get(player_col, ""))
        opp    = _norm(r.get(opp_col, ""))
        line   = r.get(line_col, None)
        try:
            line_f = float(line)
        except (TypeError, ValueError):
            line_f = None

        entries = lookup.get((player, opp), [])
        # sort by date desc, take up to 10
        try:
            entries_sorted = sorted(entries, key=lambda x: x[1], reverse=True)[:10]
        except Exception:
            entries_sorted = entries[:10]

        vals = [v for v, _ in entries_sorted if v is not None and not (isinstance(v, float) and v != v)]

        if not vals:
            h2h_games.append(0)
            h2h_avg.append(np.nan)
            h2h_over.append(np.nan)
            h2h_last.append(np.nan)
        else:
            avg = round(float(np.mean(vals)), 2)
            last = vals[0]
            over_rate = round(sum(1 for v in vals if line_f is not None and v > line_f) / len(vals), 3) if line_f else np.nan
            h2h_games.append(len(vals))
            h2h_avg.append(avg)
            h2h_over.append(over_rate)
            h2h_last.append(round(float(last), 2))

    df["h2h_games"]     = h2h_games
    df["h2h_avg"]       = h2h_avg
    df["h2h_over_rate"] = h2h_over
    df["h2h_last"]      = h2h_last
    return df
# ─────────────────────────────────────────────────────────────────────────────


_PROP_WEIGHTS = {
    "passes":          1.08,   # most stable, high volume
    "saves":           1.06,   # GK saves fairly predictable
    "shots_on_target": 1.05,
    "assists":         1.04,
    "shots":           1.03,
    "goals":           0.95,   # high variance
    "goal_assist":     0.97,
    "clearances":      1.02,
    "tackles":         1.01,
    "fouls":           0.98,
    "goals_allowed":   0.96,
    "shots_assisted":  1.00,
    "crosses":         1.02,
    "attempted_dribbles": 1.01,
    "goals_allowed_first30": 0.94,
}

def _prop_weight(prop_norm: str) -> float:
    return float(_PROP_WEIGHTS.get(str(prop_norm).lower().strip(), 0.93))


# ── Soccer hit rate priors (OVER) ─────────────────────────────────────────────
# Based on general soccer prop market tendencies

_PROP_HIT_RATE_PRIOR = {
    "passes":          0.620,
    "saves":           0.600,
    "shots_on_target": 0.580,
    "clearances":      0.570,
    "tackles":         0.560,
    "shots":           0.555,
    "assists":         0.540,
    "goal_assist":     0.530,
    "shots_assisted":  0.540,
    "fouls":           0.520,
    "goals_allowed":   0.510,
    "goals":           0.490,   # goals are low-frequency, slight under bias
    "crosses":         0.545,
    "attempted_dribbles": 0.550,
    "goals_allowed_first30": 0.505,
}

def _prop_hit_rate_prior(prop_norm: str, direction: str, pick_type: str = "Standard", deviation_level: float = 0.0) -> float:
    key  = str(prop_norm).lower().strip()
    base = _PROP_HIT_RATE_PRIOR.get(key, 0.530)
    if direction == "UNDER":
        if key == "goals":        return 0.620
        if key == "goals_allowed": return 0.600
        return float(1.0 - base)
    pt = _norm_pick_type(pick_type)
    dev = int(deviation_level) if not (deviation_level != deviation_level) else 0  # nan check
    if pt == "Demon":
        # Demon lines are set above expected outcome — each deviation level drops hit rate
        penalty = {1: 0.08, 2: 0.14, 3: 0.20}.get(dev, 0.08)
        return float(max(base - penalty, 0.30))
    if pt == "Goblin":
        # Goblin lines are set below expected outcome — each level boosts hit rate
        bonus = {1: 0.06, 2: 0.10, 3: 0.14}.get(dev, 0.06)
        return float(min(base + bonus, 0.90))
    return float(base)


def _reliability_mult(pick_type: str) -> float:
    pt = _norm_pick_type(pick_type)
    return {"Standard": 1.00, "Goblin": 1.06, "Demon": 0.75}.get(pt, 0.97)


def _safe_float(x) -> float:
    v = pd.to_numeric(pd.Series([x]), errors="coerce").iloc[0]
    return float(v) if not pd.isna(v) else np.nan


def _edge_transform(edge: float, cap: float = 3.0, power: float = 0.85) -> float:
    if np.isnan(edge):
        return np.nan
    s = 1.0 if edge >= 0 else -1.0
    x = min(abs(edge), cap)
    return s * (x ** power)


def _tier_from_score(score: float) -> str:
    """
    Tier thresholds calibrated for soccer pipeline.
    Note: when many rows lack ESPN IDs/stats, most eligible rows will have
    partial signals. Thresholds are intentionally modest so A/B/C tiers
    still populate when data is partially available.
    """
    if np.isnan(score): return "D"
    if score >= 1.20:   return "A"
    if score >= 0.50:   return "B"
    if score >= 0.10:   return "C"
    return "D"


def _tier_from_score_by_picktype(score: float, pick_type: str) -> str:
    """
    Pick-type-aware tier assignment.
    Demons have structurally lower scores (edge is zeroed, prop_hr_prior is penalized)
    so they use compressed thresholds relative to their own score distribution.
    Goblins and Standards use standard thresholds.
    """
    if np.isnan(score):
        return "D"
    pt = _norm_pick_type(pick_type)
    if pt == "Demon":
        # Demon scores cluster between -0.4 and +0.5 — use relative thresholds
        if score >= 0.30:   return "A"
        if score >= 0.10:   return "B"
        if score >= -0.10:  return "C"
        return "D"
    # Goblin / Standard use original thresholds
    if score >= 1.20:   return "A"
    if score >= 0.50:   return "B"
    if score >= 0.10:   return "C"
    return "D"


def _projection_from_row(row: pd.Series) -> float:
    """Build projection from stat averages if available, else estimate from standard_line / line + offset."""
    # 1. Prefer real stat averages (populated when ESPN data is available)
    for c in ("stat_last5_avg", "stat_last10_avg", "stat_season_avg"):
        v = _safe_float(row.get(c, np.nan))
        if not np.isnan(v):
            return v

    # 2. Use standard_line as projection if present
    std_line = _safe_float(row.get("standard_line", np.nan))
    if not np.isnan(std_line):
        return std_line

    # 3. Estimate standard_line from current line using pick_type + deviation_level offsets
    # These offsets are derived from observed data:
    #   Demon  dev1 → standard ≈ line - 1.0
    #   Demon  dev2 → standard ≈ line - 2.0
    #   Demon  dev3 → standard ≈ line - 3.0
    #   Goblin dev1 → standard ≈ line + 1.0
    #   Goblin dev2 → standard ≈ line + 1.5
    #   Standard    → projection = line (no deviation)
    line_val = _safe_float(row.get("line", np.nan))
    if np.isnan(line_val):
        return np.nan

    pick_type = _norm_pick_type(str(row.get("pick_type", "")))
    dev_level = _safe_float(row.get("deviation_level", np.nan))
    dev = int(dev_level) if not np.isnan(dev_level) else 1

    if pick_type == "Standard":
        return line_val
    elif pick_type == "Goblin":
        offset_map = {1: 1.0, 2: 1.5, 3: 2.0}
        return line_val + offset_map.get(dev, 1.0)
    elif pick_type == "Demon":
        offset_map = {1: -1.0, 2: -2.0, 3: -3.0}
        return line_val + offset_map.get(dev, -1.0)

    return np.nan


def _line_hit_rate_from_row(row: pd.Series) -> float:
    direction = str(row.get("bet_direction", "OVER")).upper()
    hr5 = hr10 = np.nan

    if direction == "UNDER":
        for c in ("line_hit_rate_under_ou_5", "line_hit_rate_under_5"):
            if c in row.index:
                v = _safe_float(row.get(c))
                if not np.isnan(v):
                    hr5 = v; break
        if np.isnan(hr5):
            o = _safe_float(row.get("last5_over",  np.nan))
            u = _safe_float(row.get("last5_under", np.nan))
            if not np.isnan(o) and not np.isnan(u):
                denom = o + u
                hr5 = u / denom if denom > 0 else np.nan
        for c in ("line_hit_rate_under_ou_10", "line_hit_rate_under_10"):
            if c in row.index:
                v = _safe_float(row.get(c))
                if not np.isnan(v):
                    hr10 = v; break
    else:
        for c in ("line_hit_rate_over_ou_5", "line_hit_rate_over_5", "last5_hit_rate"):
            if c in row.index:
                v = _safe_float(row.get(c))
                if not np.isnan(v):
                    hr5 = v; break
        for c in ("line_hit_rate_over_ou_10", "line_hit_rate_over_10"):
            if c in row.index:
                v = _safe_float(row.get(c))
                if not np.isnan(v):
                    hr10 = v; break

    if not np.isnan(hr5) and not np.isnan(hr10):
        return hr5 * 0.50 + hr10 * 0.50
    if not np.isnan(hr5):  return hr5
    if not np.isnan(hr10): return hr10
    return np.nan


def _minutes_certainty(row: pd.Series) -> float:
    tier = str(row.get("minutes_tier", "")).upper()
    if tier in ("HIGH", "MEDIUM", "LOW"):
        return {"HIGH": 1.00, "MEDIUM": 0.90, "LOW": 0.75}[tier]
    # Fallback: infer from pick_type and position when minutes_tier is UNKNOWN
    pick_type = _norm_pick_type(str(row.get("pick_type", "")))
    pos = str(row.get("position_group", "")).upper()
    base = {"Standard": 0.95, "Goblin": 0.85, "Demon": 0.80}.get(pick_type, 0.80)
    if pos in ("GK", "DEF"):
        base = min(base + 0.05, 1.00)
    return base


def _def_adjustment(row: pd.Series, n_teams: int = 15) -> float:
    """Soccer defense adjustment — scale around midpoint of n_teams."""
    rank = _safe_float(row.get("OVERALL_DEF_RANK", np.nan))
    if np.isnan(rank):
        return 0.0
    mid = (n_teams + 1) / 2.0
    return float((rank - mid) / mid * 0.06)


def _repo_root_ml_soc() -> Path:
    return Path(__file__).resolve().parents[2]


_sd_ml_soc = str(_repo_root_ml_soc() / "scripts")
if _sd_ml_soc not in sys.path:
    sys.path.insert(0, _sd_ml_soc)
try:
    from ml_blend_weight import load_ml_blend_weight  # noqa: E402

    ML_BLEND_WEIGHT = float(load_ml_blend_weight(_repo_root_ml_soc(), "soccer"))
except Exception:
    ML_BLEND_WEIGHT = 0.30


def _ml_defense_tier_series(out: pd.DataFrame, n_teams: int) -> pd.Series:
    if "def_tier" in out.columns:
        s = out["def_tier"].astype(str).str.strip().str.lower()
        return pd.Series(
            np.where(
                s.str.contains("weak"),
                0,
                np.where(
                    s.str.contains("avg|average|mid"),
                    1,
                    np.where(s.str.contains("elite|strong|solid|above"), 2, 1),
                ),
            ),
            index=out.index,
        )
    if "OVERALL_DEF_RANK" in out.columns:
        r = _to_num(out["OVERALL_DEF_RANK"]).fillna((n_teams + 1) / 2.0)
        return pd.Series(np.where(r <= max(1, n_teams * 0.33), 2, np.where(r <= max(2, n_teams * 0.66), 1, 0)), index=out.index)
    return pd.Series(1, index=out.index)


def _apply_ml_blend(out: pd.DataFrame, existing_score: pd.Series, n_teams: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    root = Path(__file__).resolve().parents[2]
    model_path = root / "models" / "prop_model_soccer.pkl"
    feat_path = root / "models" / "prop_model_soccer_features.json"
    if not (model_path.exists() and feat_path.exists()):
        print(f"⚠️  Soccer ML model missing at {model_path} — skipping ML blend")
        return pd.Series(np.nan, index=out.index), pd.Series(np.nan, index=out.index), existing_score.copy()

    try:
        model = joblib.load(model_path)
        model_features = json.loads(feat_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⚠️  Failed loading Soccer ML model: {e} — skipping ML blend")
        return pd.Series(np.nan, index=out.index), pd.Series(np.nan, index=out.index), existing_score.copy()

    calibrator = None
    calib_path = root / "models" / "prop_model_soccer_calibrator.pkl"
    try:
        if calib_path.exists():
            calibrator = joblib.load(calib_path)
    except Exception:
        calibrator = None

    try:
        from ml_blend_weight import load_ml_blend_weight as _load_soc_blend

        blend_w = float(_load_soc_blend(root, "soccer"))
    except Exception:
        blend_w = float(ML_BLEND_WEIGHT)

    pick = out.get("pick_type", pd.Series("Standard", index=out.index)).astype(str).str.lower()
    tier_num = pd.Series(np.where(pick.str.contains("gob"), 2, np.where(pick.str.contains("dem"), 0, 1)), index=out.index)
    dir_num = pd.Series(
        np.where(out.get("bet_direction", pd.Series("OVER", index=out.index)).astype(str).str.upper().eq("OVER"), 1, 0),
        index=out.index,
    )
    prop_norm = out.get("prop_norm", out.get("prop_type", pd.Series("unknown", index=out.index))).astype(str).str.lower().str.strip()
    prop_dummies = pd.get_dummies(prop_norm, prefix="prop", dtype=float)
    X_base = pd.DataFrame(
        {
            "edge": _to_num(out.get("edge", pd.Series(np.nan, index=out.index))).fillna(0.0),
            "hit_rate_l10": _to_num(out.get("line_hit_rate", pd.Series(np.nan, index=out.index))).fillna(0.5),
            "defense_tier": _to_num(_ml_defense_tier_series(out, n_teams)).fillna(1.0),
            "tier": _to_num(tier_num).fillna(1.0),
            "intel_shr_z": _to_num(out.get("intel_shr_z", pd.Series(np.nan, index=out.index))).fillna(0.0),
            "direction": _to_num(dir_num).fillna(0.0),
        },
        index=out.index,
    )
    X = pd.concat([X_base, prop_dummies], axis=1).reindex(columns=model_features, fill_value=0.0)
    try:
        raw_prob = pd.Series(model.predict_proba(X)[:, 1], index=out.index, dtype=float)
        if calibrator is not None:
            try:
                if hasattr(calibrator, "predict_proba"):
                    cal_vals = calibrator.predict_proba(raw_prob.values.reshape(-1, 1))[:, 1]
                else:
                    cal_vals = calibrator.predict(raw_prob.values)
                ml_prob = pd.Series(cal_vals, index=out.index, dtype=float).clip(0.001, 0.999)
            except Exception:
                ml_prob = raw_prob
        else:
            ml_prob = raw_prob
    except Exception as e:
        print(f"⚠️  Soccer ML inference failed: {e} — skipping ML blend")
        return pd.Series(np.nan, index=out.index), pd.Series(np.nan, index=out.index), existing_score.copy()

    ml_edge = ml_prob - 0.5
    final_score = (1.0 - blend_w) * existing_score + blend_w * ml_edge
    print(f"✅ Soccer ML blend applied (weight={blend_w:.2f})")
    return ml_prob, ml_edge, final_score


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--n_teams", type=int, default=15, help="Number of teams in defense file")
    ap.add_argument("--cache", default="", help="Path to Soccer boxscore cache CSV")
    ap.add_argument(
        "--injuries-csv",
        default="",
        help="injuries_soccer_*.csv (optional; populate manually or from your feed)",
    )
    ap.add_argument(
        "--slate-date",
        default="",
        help="YYYY-MM-DD; auto-load outputs/<date>/injuries_soccer_<date>.csv if injuries-csv omitted",
    )
    args = ap.parse_args()

    print(f"→ Loading: {args.input}")
    out = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig").fillna("")
    _repo_usage = Path(__file__).resolve().parents[2]
    _sd_usage = str(_repo_usage / "scripts")
    if _sd_usage not in sys.path:
        sys.path.insert(0, _sd_usage)
    try:
        from usage_redistribution import apply_usage_redistribution  # noqa: E402
        run_date = (
            str(out["game_date"].dropna().iloc[0])[:10]
            if "game_date" in out.columns and not out["game_date"].dropna().empty
            else pd.Timestamp.today().strftime("%Y-%m-%d")
        )
        out = apply_usage_redistribution(out, sport="Soccer", date=run_date, repo_root=str(_repo_usage))
    except Exception as e:
        print(f"⚠️ usage redistribution skipped: {e}")

    if out.empty:
        print("❌ [PropOracle-Soccer-S7] Empty input from S6 — aborting.")
        sys.exit(1)

    out["pick_type"] = out.get("pick_type", pd.Series(["Standard"] * len(out))).astype(str).apply(_norm_pick_type)

    # Prop norm map
    _PROP_NORM_MAP = {
        "shots on target":   "shots_on_target",
        "shotsontarget":     "shots_on_target",
        "goalie saves":      "saves",
        "goaliesaves":       "saves",
        "passes attempted":  "passes",
        "passesattempted":   "passes",
        "goals allowed":     "goals_allowed",
        "goalsallowed":      "goals_allowed",
        "goal + assist":     "goal_assist",
        "goalassist":        "goal_assist",
        "shots assisted":    "shots_assisted",
        "shotsassisted":     "shots_assisted",
        "attempted dribbles": "attempted_dribbles",
        "attempteddribbles": "attempted_dribbles",
        "crosses":           "crosses",
        "goals allowed in first 30 minutes": "goals_allowed_first30",
        "goalsallowedinfirst30minutes": "goals_allowed_first30",
    }
    out["prop_norm"] = out["prop_norm"].astype(str).str.lower().str.strip().map(
        lambda x: _PROP_NORM_MAP.get(x, x)
    )

    line_num = _to_num(out["line"])
    proj     = out.apply(_projection_from_row, axis=1)
    out["projection"] = proj
    if "usage_boost_proj" in out.columns:
        out["projection"] = _to_num(out["projection"]).fillna(0.0) + _to_num(out["usage_boost_proj"]).fillna(0.0)
    out["edge"]       = proj - line_num
    out["abs_edge"]   = out["edge"].abs()

    forced = out["pick_type"].apply(_forced_over_only).astype(int)
    out["forced_over_only"] = forced

    bet_dir = np.where(forced.eq(1), "OVER", np.where(out["edge"] >= 0, "OVER", "UNDER"))
    out["bet_direction"] = bet_dir

    eligible    = pd.Series(True,  index=out.index)
    void_reason = pd.Series("",    index=out.index)

    miss = line_num.isna() | pd.isna(out["projection"])
    eligible.loc[miss]    = False
    void_reason.loc[miss] = "NO_PROJECTION_OR_LINE"

    # Only void Goblins with negative edge.
    # Goblin lines are set LOW — neg edge means projection is BELOW an already-easy line = bad data.
    # Demons are NOT voided on neg edge: Soccer projections are mostly estimated from line offsets
    # (ESPN stat coverage is sparse), so virtually every Demon shows negative edge by construction.
    # Voiding on edge < 0 would eliminate ~80%+ of the slate. Demons are scored on hit rate,
    # defense, and prop weight instead — edge signal is zeroed out in the scoring formula below.
    goblin_neg = (out["pick_type"] == "Goblin") & (out["edge"] < 0)
    eligible.loc[goblin_neg]    = False
    void_reason.loc[goblin_neg] = "FORCED_OVER_NEG_EDGE"

    out["eligible"]    = eligible.astype(int)
    out["void_reason"] = void_reason

    out["edge_dr"]          = out["edge"].apply(_edge_transform)
    out["line_hit_rate"]    = out.apply(_line_hit_rate_from_row, axis=1)
    # When step4/step5 can't attach enough game logs, keep downstream flows alive by
    # seeding hit-rate columns from calibrated prop priors (instead of leaving all NaN).
    missing_hr = pd.to_numeric(out["line_hit_rate"], errors="coerce").isna()
    if missing_hr.any():
        prior_fallback = out.apply(
            lambda r: _prop_hit_rate_prior(
                r.get("prop_norm", ""),
                str(r.get("bet_direction", "OVER")).upper(),
                str(r.get("pick_type", "Standard")),
                float(r.get("deviation_level", 0) or 0),
            ),
            axis=1,
        )
        out.loc[missing_hr, "line_hit_rate"] = prior_fallback[missing_hr]

    # Ensure step8/combined have a usable "Hit Rate (5g)/(10g)" surface even when
    # historical stat_g* windows are sparse.
    if "line_hit_rate_over_ou_5" not in out.columns:
        out["line_hit_rate_over_ou_5"] = np.nan
    if "line_hit_rate_over_ou_10" not in out.columns:
        out["line_hit_rate_over_ou_10"] = np.nan
    out["line_hit_rate_over_ou_5"] = pd.to_numeric(
        out["line_hit_rate_over_ou_5"], errors="coerce"
    ).fillna(pd.to_numeric(out["line_hit_rate"], errors="coerce"))
    out["line_hit_rate_over_ou_10"] = pd.to_numeric(
        out["line_hit_rate_over_ou_10"], errors="coerce"
    ).fillna(pd.to_numeric(out["line_hit_rate"], errors="coerce"))
    out["minutes_certainty"] = out.apply(_minutes_certainty, axis=1)
    out["prop_weight"]      = out["prop_norm"].astype(str).apply(_prop_weight)
    out["reliability_mult"] = out["pick_type"].astype(str).apply(_reliability_mult)

    elig_mask = out["eligible"].astype(int).eq(1)

    def zcol(s: pd.Series, direction_aware: bool = False) -> pd.Series:
        x      = pd.to_numeric(s, errors="coerce")
        result = pd.Series([0.0] * len(x), index=x.index)
        if direction_aware and "bet_direction" in out.columns:
            for direction in ("OVER", "UNDER"):
                dir_mask = elig_mask & (out["bet_direction"].astype(str).str.upper() == direction)
                if dir_mask.sum() < 2: continue
                mu = x[dir_mask].mean()
                sd = x[dir_mask].std()
                if sd and not np.isnan(sd) and sd > 1e-9:
                    z_vals = (x[dir_mask] - mu) / sd
                    result.loc[dir_mask.index[dir_mask]] = z_vals.values
            return result
        mu = x[elig_mask].mean()
        sd = x[elig_mask].std()
        if sd and not np.isnan(sd) and sd > 1e-9:
            return (x - mu) / sd
        return result

    out["edge_z"]      = zcol(out["edge"],           direction_aware=True)
    out["line_hit_z"]  = zcol(out["line_hit_rate"],  direction_aware=True)
    out["min_z"]       = zcol(out["minutes_certainty"])

    def_adj = out.apply(lambda r: _def_adjustment(r, args.n_teams), axis=1)
    out["def_adj"]         = def_adj
    out["projection_adj"]  = pd.to_numeric(out["projection"], errors="coerce") * (1.0 + def_adj.astype(float))
    out["edge_adj"]        = out["projection_adj"] - line_num

    def _edge_adj_dr(row_idx):
        x = out["edge_adj"].iloc[row_idx]
        if pd.isna(x): return np.nan
        direction = str(out["bet_direction"].iloc[row_idx]).upper()
        signed = -float(x) if direction == "UNDER" else float(x)
        return _edge_transform(signed)

    out["edge_adj_dr"] = pd.Series([_edge_adj_dr(i) for i in range(len(out))], index=out.index)

    def _def_rank_signal(row: pd.Series) -> float:
        rank      = _safe_float(row.get("OVERALL_DEF_RANK", np.nan))
        direction = str(row.get("bet_direction", "OVER")).upper()
        if np.isnan(rank): return 0.0
        signal = (rank - 1.0) / (args.n_teams - 1.0) * 2.0 - 1.0
        return float(signal if direction == "OVER" else -signal)

    def_signal = out.apply(_def_rank_signal, axis=1)
    out["def_rank_signal"] = def_signal
    out["def_rank_z"]      = zcol(def_signal, direction_aware=True)

    line_num_filled = line_num.fillna(0)
    for col in ("stat_last5_avg", "stat_last10_avg", "stat_season_avg"):
        num_col = col + "_num"
        out[num_col] = _to_num(out[col]) if col in out.columns else pd.Series([np.nan] * len(out), index=out.index)

    def _avg_vs_line(row_idx):
        l = line_num_filled.iloc[row_idx]
        if l == 0 or np.isnan(l): return 0.0
        direction = str(out["bet_direction"].iloc[row_idx]).upper()
        score = total_w = 0.0
        for col, w in [("stat_last5_avg_num", 0.50), ("stat_last10_avg_num", 0.30), ("stat_season_avg_num", 0.20)]:
            v = out[col].iloc[row_idx]
            if not np.isnan(v):
                raw = np.clip((v - l) / l, -1.0, 1.0)
                if direction == "UNDER": raw = -raw
                score   += raw * w
                total_w += w
        return float(score / total_w) if total_w > 0.1 else 0.0

    avg_vs_line = pd.Series([_avg_vs_line(i) for i in range(len(out))], index=out.index)
    out["avg_vs_line"]   = avg_vs_line
    out["avg_vs_line_z"] = zcol(avg_vs_line, direction_aware=True)

    prop_hr_prior = out.apply(
        lambda r: _prop_hit_rate_prior(
            r.get("prop_norm", ""),
            str(r.get("bet_direction", "OVER")).upper(),
            str(r.get("pick_type", "Standard")),
            float(r.get("deviation_level", 0) or 0)
        ),
        axis=1
    )
    out["prop_hr_prior"] = prop_hr_prior
    out["prop_hr_z"]     = zcol(prop_hr_prior, direction_aware=True)

    # For Demon picks: edge is always negative by design (line set high, forced OVER).
    # Edge signal is not informative for Demons — zero it out so they rank on
    # hit rate, defense, prop weight, and minutes certainty instead.
    is_demon = (out["pick_type"].astype(str) == "Demon")
    edge_component = out["edge_adj_dr"].astype(float).where(~is_demon, 0.0).fillna(0.0)

    score = (
        edge_component                                     * 0.85
        + out["line_hit_z"].astype(float).fillna(0.0)    * 0.85
        + out["avg_vs_line_z"].astype(float).fillna(0.0) * 0.75
        + out["def_rank_z"].astype(float).fillna(0.0)    * 0.80
        + out["prop_hr_z"].astype(float).fillna(0.0)     * 0.50
        + out["min_z"].astype(float).fillna(0.0)         * 0.25
    )
    usage_bonus = np.clip(_to_num(out.get("usage_boost", pd.Series(0.0, index=out.index))).fillna(0.0) * 5.0, 0.0, 0.5)
    out["usage_bonus"] = usage_bonus
    score = score + usage_bonus
    score = (
        score
        * out["prop_weight"].astype(float).fillna(1.0)
        * out["reliability_mult"].astype(float).fillna(1.0)
    )

    # Penalty for rows with no real player stats (no hit rate AND no stat averages).
    # These are scored purely on priors/defense — reduce score so they max out at Tier B.
    has_real_stats = (
        pd.to_numeric(out.get("line_hit_rate", pd.Series(dtype=float)), errors="coerce").notna()
        | pd.to_numeric(out.get("stat_last5_avg", pd.Series(dtype=float)), errors="coerce").notna()
    )
    score = score.where(has_real_stats, score * 0.60)
    out["has_real_stats"] = has_real_stats.astype(int)

    # Direction-aware edge gate (matches NBA step7). Demons exempt — edge component is zeroed above.
    _eadr = pd.to_numeric(out["edge_adj_dr"], errors="coerce").fillna(-999.0)
    _is_dem = out["pick_type"].astype(str).str.lower().str.contains("dem")
    score = score.where(elig_mask & ((_eadr > 0.0) | _is_dem), np.nan)

    out["rank_score"] = score
    out["ml_prob"], out["ml_edge"], out["final_score"] = _apply_ml_blend(out, out["rank_score"], args.n_teams)
    _apply_consistency_grade_scores_soc(out, "Soccer")

    # Game script risk adjustment
    from datetime import datetime, timezone

    _repo_gs = _repo_root_pc_soc()
    _sd_gs = str(_repo_gs / "scripts")
    if _sd_gs not in sys.path:
        sys.path.insert(0, _sd_gs)
    from game_script_risk import get_game_script_multiplier  # noqa: E402

    _fallback_gd = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if "start_time" in out.columns:
        _st = out["start_time"].astype(str).str.strip()
        _dp = _st.str[:10]
        _gd_series = _dp.where(_dp.str.match(r"^\d{4}-\d{2}-\d{2}$"), _fallback_gd)
    else:
        _gd_series = pd.Series([_fallback_gd] * len(out), index=out.index)
    _team_col = next((c for c in ("team", "Team") if c in out.columns), "team")
    _prop_gs = "prop_norm" if "prop_norm" in out.columns else "prop_type"
    _gmults: list[float] = []
    _gnotes: list[str] = []
    for _i in range(len(out)):
        _r = out.iloc[_i]
        _gd = str(_gd_series.iloc[_i])
        _tm = str(_r.get(_team_col, "") or "").strip()
        _pt = str(_r.get(_prop_gs, "") or "").strip()
        _gm, _gn = get_game_script_multiplier(_tm, "Soccer", _pt, _gd)
        _gmults.append(round(float(_gm), 3))
        _gnotes.append(_gn)
    out["game_script_mult"] = _gmults
    out["game_script_note"] = _gnotes
    out["final_score"] = _to_num(out["final_score"]).astype(float) * pd.Series(_gmults, dtype=float).values

    _repo_soc_inj = _repo_root_pc_soc()
    _sd_soc_inj = str(_repo_soc_inj / "scripts")
    if _sd_soc_inj not in sys.path:
        sys.path.insert(0, _sd_soc_inj)
    from espn_injuries import auto_injuries_csv_from_outputs, penalty_series_for_slate  # noqa: E402

    _inj_soc_path = str(args.injuries_csv or "").strip()
    if not _inj_soc_path and str(getattr(args, "slate_date", "") or "").strip():
        _csp = auto_injuries_csv_from_outputs(_repo_soc_inj, str(args.slate_date).strip(), "SOCCER")
        _inj_soc_path = str(_csp) if _csp else ""
    if _inj_soc_path:
        _p_soc_col = next((c for c in ("player", "player_norm", "pp_player") if c in out.columns), "player")
        _t_soc_col = next((c for c in ("team", "pp_team", "Team") if c in out.columns), "team")
        _pen_soc = penalty_series_for_slate(out, _p_soc_col, _t_soc_col, "SOCCER", _inj_soc_path)
        out["final_score"] = _to_num(out["final_score"]).astype(float) + _pen_soc.values

    out["rank_score"] = out["final_score"]
    out["tier"]       = out.apply(
        lambda r: _tier_from_score_by_picktype(r["rank_score"], str(r.get("pick_type", "Standard"))),
        axis=1
    )

    # ── Tier A / B sheets so step8/step9 always have a usable sheet ──
    # ── Head-to-Head stats ───────────────────────────────────────────────────
    player_col = next((c for c in ["player_norm","player","pp_player","player_name"] if c in out.columns), "")
    opp_col    = next((c for c in ["pp_opp_team","opp_team_abbr","opp_team","opp"] if c in out.columns), "")
    prop_col   = next((c for c in ["prop_norm","prop_type"] if c in out.columns), "prop_norm")
    if player_col and opp_col and args.cache:
        out = _attach_h2h(out, args.cache, "soccer", player_col, opp_col, prop_col, "line")
        print(f"  H2H: {(out['h2h_games'] > 0).sum()}/{len(out)} rows matched")

    with pd.ExcelWriter(args.output, engine="openpyxl") as w:
        out.to_excel(w, sheet_name="ALL",      index=False)
        out.loc[elig_mask].to_excel(w, sheet_name="ELIGIBLE", index=False)
        for _tier in ["A", "B", "C", "D"]:
            _mask = out["tier"] == _tier
            if _mask.any():
                out.loc[_mask].to_excel(w, sheet_name=f"Tier {_tier}", index=False)

    if elig_mask.sum() == 0:
        print("❌ [PropOracle-Soccer-S7] No eligible props after scoring — aborting.")
        sys.exit(1)

    print(f"✅ Saved → {args.output}")
    print(f"ALL rows: {len(out)}")
    print("\nTier counts:")
    print(out["tier"].value_counts().to_string())
    print("\nIneligible reasons:")
    vr = out.loc[~elig_mask, "void_reason"].value_counts()
    print(vr.to_string() if len(vr) else "(none)")
    print("\nScore percentiles (eligible):")
    rs = pd.to_numeric(out.loc[elig_mask, "rank_score"], errors="coerce")
    print(rs.quantile([0.50, 0.70, 0.80, 0.85, 0.90, 0.95]).round(3).to_string())


if __name__ == "__main__":
    main()
