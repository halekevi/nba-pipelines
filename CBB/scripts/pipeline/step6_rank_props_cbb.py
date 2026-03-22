#!/usr/bin/env python3
"""
cbb_step6_rank_props.py  (v3 — under-direction fixes)
-------------------------------------------------------
Ranks CBB props using the same signal set as NBA step7:
  - Weighted projection blend: last5 (50%) + last10 (30%) + season (20%)
  - Direction-aware avg_vs_line signal
  - Blended hit rate (last5 50% + last10 50%)
  - Defense-adjusted edge  (divisor fixed for full D1 ranking scale)
  - Prop-type weight  (same table as NBA)
  - Bayesian prop hit-rate prior  (same table as NBA)
  - Reliability multiplier  (consistent with NBA)

Input : step5b_with_stats_cbb.csv  (or any step5b_cbb.csv)
Output: step6_ranked_props_cbb.xlsx + optional CSV
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Dict, Optional
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

# ── Head-to-Head (H2H) utility ────────────────────────────────────────────────
def _attach_h2h(
    df: "pd.DataFrame",
    cache_path: str,
    sport: str,
    player_col: str,
    opp_col: str,
    prop_col: str,
    line_col: str,
    tid_to_abbr: Optional[Dict[str, str]] = None,
) -> "pd.DataFrame":
    """
    Attach H2H stats per row using the boxscore cache (step5b format).
    Cache columns: player_norm, opp_team_abbr, game_date, PTS, REB, AST, STL, BLK, TO, 3PM, MIN

    Adds columns:
      h2h_games      – number of H2H games found vs this opponent
      h2h_avg        – player's average stat value vs this opponent
      h2h_over_rate  – fraction of those games over the current line
      h2h_last       – most recent game value vs this opponent
    """
    import os

    df["h2h_games"]     = 0
    df["h2h_avg"]       = np.nan
    df["h2h_over_rate"] = np.nan
    df["h2h_last"]      = np.nan

    if not cache_path or not os.path.exists(cache_path):
        print(f"  [H2H] cache: {os.path.abspath(cache_path)}  exists=False — skipping")
        return df

    try:
        cache = pd.read_csv(cache_path, low_memory=False)
    except Exception:
        print(f"  [H2H] cache: {os.path.abspath(cache_path)}  read failed — skipping")
        return df

    print(f"  [H2H] cache: {os.path.abspath(cache_path)}  exists=True")

    cache.columns = [c.lower().strip() for c in cache.columns]

    # data/cache CSVs from older step5b runs may omit opp_team_abbr; align with step5b Phase 3 backfill.
    tmap = tid_to_abbr if tid_to_abbr is not None else {}
    if "opp_team_id" in cache.columns:
        if "opp_team_abbr" not in cache.columns:
            cache["opp_team_abbr"] = ""
        oid = cache["opp_team_id"].astype(str).str.strip()
        ab0 = cache["opp_team_abbr"].astype(str).str.strip()
        blank_opp = ab0.eq("") | ab0.str.lower().isin(["nan", "none"])
        if blank_opp.any():
            def _oid_to_abbr(x: str) -> str:
                x = str(x).strip()
                if not x or x.lower() in ("", "nan", "none"):
                    return ""
                return str(tmap.get(x, x))

            mapped = oid.map(_oid_to_abbr)
            cache = cache.copy()
            cache.loc[blank_opp, "opp_team_abbr"] = mapped.loc[blank_opp].astype(str).values

    print(f"  [H2H] cache rows={len(cache):,}  cols={list(cache.columns)[:12]}{'...' if len(cache.columns) > 12 else ''}")

    # Need player, opponent, and stat columns
    p_col = next((c for c in ["player_norm","player_name","player","name"] if c in cache.columns), None)
    o_col = next((c for c in ["opp_team_abbr","opp_team","opp","opponent"] if c in cache.columns), None)

    if not p_col or not o_col:
        print(f"  [H2H] Cache missing player ({p_col}) or opp ({o_col}) column — skipping")
        return df

    # Stat column map matching prop_value() logic
    stat_cols = {c: c for c in ["pts","reb","ast","stl","blk","to","3pm"] if c in cache.columns}
    if not stat_cols:
        print(f"  [H2H] Cache has no stat columns — skipping")
        return df

    def _norm(x):
        return str(x).strip().lower() if x and str(x).strip() else ""

    def _cache_prop_value(row, prop_norm: str):
        """Compute the stat value for a prop type from a cache row."""
        p = str(prop_norm).lower().strip()
        pts = float(row.get("pts") or 0)
        reb = float(row.get("reb") or 0)
        ast = float(row.get("ast") or 0)
        stl = float(row.get("stl") or 0)
        blk = float(row.get("blk") or 0)
        tov = float(row.get("to") or 0)
        tpm = row.get("3pm")
        tpm = float(tpm) if tpm not in (None, "", "nan") else None

        m = {"pts": pts, "reb": reb, "ast": ast, "stl": stl, "blk": blk,
             "tov": tov, "to": tov, "3pm": tpm, "fg3m": tpm,
             "stocks": stl + blk,
             "pra": pts + reb + ast, "pr": pts + reb,
             "pa": pts + ast, "ra": reb + ast,
             "fantasy": pts + 1.2*reb + 1.5*ast + 3*stl + 3*blk - tov}
        return m.get(p)

    # Reverse map: opponent abbr (lower) -> ESPN team_id for rows on this slate
    abbr_to_tid: Dict[str, str] = {}
    if tid_to_abbr:
        for tid, ab in tid_to_abbr.items():
            k = str(ab).strip().lower()
            if k and str(tid).strip().lower() not in ("", "nan"):
                abbr_to_tid[k] = str(tid).strip()

    # (player_norm, opp_abbr_norm) -> cache rows; also (player_norm, opp_team_id) for numeric cache opps
    lookup: dict = {}
    lookup_oid: dict = {}
    oid_key_col = "opp_team_id" if "opp_team_id" in cache.columns else None
    for _, row in cache.iterrows():
        pn = _norm(row.get(p_col, ""))
        oa = _norm(row.get(o_col, ""))
        if pn and oa:
            lookup.setdefault((pn, oa), []).append(row)
        if oid_key_col and pn:
            oid = str(row.get(oid_key_col, "")).strip()
            if oid and oid.lower() not in ("", "nan", "none"):
                lookup_oid.setdefault((pn, oid), []).append(row)

    matched = 0
    for idx, r in df.iterrows():
        player   = _norm(r.get(player_col, ""))
        opp      = _norm(r.get(opp_col, ""))
        prop     = str(r.get(prop_col, "")).lower().strip()
        line_val = r.get(line_col, None)
        try:
            line_f = float(line_val)
        except (TypeError, ValueError):
            line_f = None

        entries = lookup.get((player, opp), [])
        if not entries and abbr_to_tid:
            oid = abbr_to_tid.get(opp, "")
            if oid:
                entries = lookup_oid.get((player, oid), [])
        if not entries:
            continue

        # Sort by date desc, take up to 10
        try:
            entries_sorted = sorted(entries, key=lambda x: str(x.get("game_date", "")), reverse=True)[:10]
        except Exception:
            entries_sorted = entries[:10]

        vals = [v for e in entries_sorted
                if (v := _cache_prop_value(e, prop)) is not None
                and float(e.get("min") or e.get("MIN") or 0) > 0]

        if not vals:
            continue

        matched += 1
        avg  = round(float(np.mean(vals)), 2)
        last = round(float(vals[0]), 2)
        over_rate = (round(sum(1 for v in vals if line_f is not None and v > line_f) / len(vals), 3)
                     if line_f is not None else np.nan)

        df.at[idx, "h2h_games"]     = len(vals)
        df.at[idx, "h2h_avg"]       = avg
        df.at[idx, "h2h_over_rate"] = over_rate
        df.at[idx, "h2h_last"]      = last

    print(f"  [H2H] matched {matched}/{len(df)} slate rows (player+opp keys vs boxscore cache)")
    return df
# ─────────────────────────────────────────────────────────────────────────────



def _to_num(s):
    return pd.to_numeric(s, errors="coerce")


# ── Player consistency (data/cache/player_consistency.db) ──────────────────────
import sys as _sys_pc_cbb


def _repo_root_pc_cbb() -> Path:
    here = Path(__file__).resolve()
    for anc in here.parents:
        if (anc / "scripts" / "build_player_consistency.py").is_file():
            return anc
    return here.parents[3]


def _load_bpc_pc_cbb():
    root = _repo_root_pc_cbb()
    sd = str(root / "scripts")
    if sd not in _sys_pc_cbb.path:
        _sys_pc_cbb.path.insert(0, sd)
    import build_player_consistency as bpc  # noqa: E402

    return bpc


_bpc_pc_cbb_mod = None


def _bpc_pc_cbb():
    global _bpc_pc_cbb_mod
    if _bpc_pc_cbb_mod is None:
        try:
            _bpc_pc_cbb_mod = _load_bpc_pc_cbb()
        except Exception:
            _bpc_pc_cbb_mod = False
    return _bpc_pc_cbb_mod


def _normalize_prop_type(raw: str) -> str:
    m = _bpc_pc_cbb()
    if not m:
        return str(raw or "").strip()
    return m._normalize_prop_type(str(raw), "CBB")


def _get_line_bucket(prop_type: str, line: float, sport: str) -> str:
    m = _bpc_pc_cbb()
    if not m:
        return "<5"
    try:
        ln = float(line)
    except (TypeError, ValueError):
        ln = 0.0
    return m.get_line_bucket(prop_type, ln, sport)


def _get_consistency_grade(player: str, sport: str, prop_type: str, direction: str, line: float) -> str:
    import sqlite3

    repo_root = _repo_root_pc_cbb()
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


def _pc_grade_cache_cbb(sport: str) -> dict:
    import sqlite3

    dbp = _repo_root_pc_cbb() / "data" / "cache" / "player_consistency.db"
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


def _apply_consistency_grade_scores_cbb(out: pd.DataFrame, sport: str) -> None:
    grade_multiplier = {
        "S": 1.25,
        "A": 1.15,
        "B": 1.05,
        "C": 1.00,
        "D": 0.80,
        "F": 0.00,
        "?": 0.95,
    }
    cache = _pc_grade_cache_cbb(sport)
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
    t = str(x or "").strip().lower()
    if "gob" in t: return "Goblin"
    if "dem" in t: return "Demon"
    return "Standard"


def _forced_over(pick_type: str) -> int:
    return 1 if _norm_pick_type(pick_type) in ("Goblin", "Demon") else 0


# ── Prop weights (same as NBA step7) ─────────────────────────────────────────
_PROP_WEIGHTS = {
    "pts":   1.03, "reb":   1.06, "ast":   1.05,
    "stl":   1.08, "blk":   1.02, "stocks": 1.04,
    "fg3m":  1.03, "fg3a":  0.88, "fg2m":  1.01,
    "fg2a":  0.92, "fgm":   0.99, "fga":   0.99,
    "ftm":   1.01, "fta":   0.98, "tov":   0.94,
    "pf":    0.85, "pr":    1.01, "pa":    1.01,
    "pra":   0.99, "ra":    1.02, "fantasy": 0.93,
}

def _prop_weight(prop_norm: str) -> float:
    return float(_PROP_WEIGHTS.get(str(prop_norm).lower().strip(), 0.93))


# ── Bayesian prior hit rates (same as NBA step7) ──────────────────────────────
_PROP_HIT_RATE_PRIOR = {
    "stl": 0.697, "fantasy": 0.60,
    "fg3m": 0.623, "reb": 0.617, "ast": 0.593,
    "ftm": 0.583, "pr": 0.568,  "pts": 0.566,
    "stocks": 0.547, "blk": 0.545, "pra": 0.545,
    "fga": 0.558, "pa": 0.557,  "fgm": 0.519,
    "fg2m": 0.528, "fg2a": 0.463, "tov": 0.484,
    "fg3a": 0.444, "pf": 0.424,  "fta": 0.545,
}

def _prop_hr_prior(prop_norm: str, direction: str) -> float:
    key = str(prop_norm).lower().strip()
    base = _PROP_HIT_RATE_PRIOR.get(key, 0.545)
    if direction == "UNDER":
        if key == "fantasy":     return 0.371
        if key in ("fga","fg2a"): return 0.645
        if key == "reb":          return 0.591
        if key in ("pts","pr","pra"): return 0.540
        return float(1.0 - base)
    return float(base)


def _reliability_mult(pick_type: str) -> float:
    """Consistent with NBA step7: Goblin lines are easier so slight boost,
    Demon lines are harder so penalty."""
    return {"Standard": 1.00, "Goblin": 1.06, "Demon": 0.75}.get(
        _norm_pick_type(pick_type), 0.97
    )



def rank_to_tier(rank, n_teams):
    """Map numeric defense rank to tier using percentile bands (rank 1 = best)."""
    try:
        r = float(rank)
        nt = float(n_teams)
        if nt <= 0 or np.isnan(r) or np.isnan(nt):
            return ""
    except (TypeError, ValueError):
        return ""
    pct = r / nt
    if pct <= 0.25:
        return "elite"
    elif pct <= 0.50:
        return "good"
    elif pct <= 0.75:
        return "average"
    else:
        return "weak"


def _infer_cbb_n_teams(out: pd.DataFrame) -> float:
    col = next(
        (c for c in ["OVERALL_DEF_RANK", "OPP_OVERALL_DEF_RANK", "opp_def_rank"] if c in out.columns),
        None,
    )
    if not col:
        return 362.0
    mx = _to_num(out[col]).max()
    if pd.isna(mx):
        return 362.0
    return 362.0 if float(mx) > 40 else 30.0


def _edge_transform(edge: float, cap=3.0, power=0.85) -> float:
    if np.isnan(edge): return np.nan
    s = 1.0 if edge >= 0 else -1.0
    return s * (min(abs(edge), cap) ** power)


def _tier(score: float, eligible_scores=None) -> str:
    """Assign tier based on rank_score.
    Thresholds calibrated to actual CBB score distribution:
      scores range ~-1.2 to +1.6, median ~-0.42
      A = top ~5%  (score >= 0.96)
      B = top ~10% (score >= 0.68)
      C = top ~25% (score >= 0.13)
      D = everything else
    """
    if np.isnan(score): return "D"
    if score >= 0.96:  return "A"
    if score >= 0.68:  return "B"
    if score >= 0.13:  return "C"
    return "D"


def _repo_root_ml_cbb() -> Path:
    return Path(__file__).resolve().parents[3]


_sd_ml_cbb = str(_repo_root_ml_cbb() / "scripts")
if _sd_ml_cbb not in sys.path:
    sys.path.insert(0, _sd_ml_cbb)
try:
    from ml_blend_weight import load_ml_blend_weight  # noqa: E402

    ML_BLEND_WEIGHT = float(load_ml_blend_weight(_repo_root_ml_cbb(), "cbb"))
except Exception:
    ML_BLEND_WEIGHT = 0.30


def _ml_defense_tier_series(out: pd.DataFrame, n_teams: float) -> pd.Series:
    """Numeric defense toughness for ML features — always from rank quartiles, not CSV tier text."""
    col = next(
        (c for c in ["OVERALL_DEF_RANK", "OPP_OVERALL_DEF_RANK", "opp_def_rank"] if c in out.columns),
        None,
    )
    if not col:
        return pd.Series(1, index=out.index)

    def _to_ml_tier(r):
        if pd.isna(r):
            return 1.0
        lbl = rank_to_tier(float(r), float(n_teams))
        if lbl == "weak":
            return 0.0
        if lbl == "average":
            return 1.0
        if lbl in ("good", "elite"):
            return 2.0
        return 1.0

    return _to_num(out[col]).apply(_to_ml_tier)


def _apply_ml_blend(out: pd.DataFrame, existing_score: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    root = Path(__file__).resolve().parents[3]
    model_path = root / "models" / "prop_model_cbb.pkl"
    feat_path = root / "models" / "prop_model_cbb_features.json"
    if not (model_path.exists() and feat_path.exists()):
        print(f"⚠️  CBB ML model missing at {model_path} — skipping ML blend")
        return pd.Series(np.nan, index=out.index), pd.Series(np.nan, index=out.index), existing_score.copy()

    try:
        model = joblib.load(model_path)
        model_features = json.loads(feat_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⚠️  Failed loading CBB ML model: {e} — skipping ML blend")
        return pd.Series(np.nan, index=out.index), pd.Series(np.nan, index=out.index), existing_score.copy()

    try:
        from ml_blend_weight import load_ml_blend_weight as _load_cbb_blend

        blend_w = float(_load_cbb_blend(root, "cbb"))
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
            "defense_tier": _to_num(
                _ml_defense_tier_series(out, _infer_cbb_n_teams(out))
            ).fillna(1.0),
            "tier": _to_num(tier_num).fillna(1.0),
            "intel_shr_z": _to_num(out.get("intel_shr_z", pd.Series(np.nan, index=out.index))).fillna(0.0),
            "direction": _to_num(dir_num).fillna(0.0),
        },
        index=out.index,
    )
    X = pd.concat([X_base, prop_dummies], axis=1).reindex(columns=model_features, fill_value=0.0)
    try:
        ml_prob = pd.Series(model.predict_proba(X)[:, 1], index=out.index, dtype=float)
    except Exception as e:
        print(f"⚠️  CBB ML inference failed: {e} — skipping ML blend")
        return pd.Series(np.nan, index=out.index), pd.Series(np.nan, index=out.index), existing_score.copy()

    ml_edge = ml_prob - 0.5
    final_score = (1.0 - blend_w) * existing_score + blend_w * ml_edge
    print(f"✅ CBB ML blend applied (weight={blend_w:.2f})")
    return ml_prob, ml_edge, final_score


def _pick_cbb_boxscore_cache(explicit: str, input_csv: str) -> str:
    """Prefer the largest on-disk CBB boxscore CSV so H2H sees full history (two-cache layout)."""
    import os
    from pathlib import Path

    root = Path(input_csv).resolve().parent
    cands: list[Path] = []
    if explicit and str(explicit).strip():
        ep = Path(explicit)
        cands.append(ep if ep.is_absolute() else (root / ep))
    cands.append(root / "data" / "cache" / "cbb_boxscore_cache.csv")
    cands.append(root / "cbb_boxscore_cache.csv")
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in cands:
        try:
            rp = str(p.resolve())
        except OSError:
            continue
        if rp not in seen:
            seen.add(rp)
            uniq.append(p)
    existing = [p for p in uniq if p.is_file()]
    if not existing:
        return (explicit or "").strip()
    if len(existing) == 1:
        return str(existing[0])
    best = max(existing, key=lambda p: p.stat().st_size)
    chosen = str(best)
    exp_res = ""
    if explicit and str(explicit).strip():
        try:
            exp_res = str((root / explicit).resolve()) if not Path(explicit).is_absolute() else str(Path(explicit).resolve())
        except OSError:
            exp_res = str(explicit)
    if exp_res and str(best.resolve()) != exp_res:
        print(
            f"  [H2H] Using largest boxscore cache ({best.stat().st_size:,} bytes): {chosen}"
            f"  (override {explicit})"
        )
    return chosen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",      required=True)
    ap.add_argument("--output",     default="step6_ranked_props_cbb.xlsx")
    ap.add_argument("--output_csv", default="")
    ap.add_argument("--cache", default="cbb_boxscore_cache.csv", help="Path to CBB boxscore cache CSV")
    ap.add_argument("--date", default="", help="Filter to YYYY-MM-DD using start_time")
    args = ap.parse_args()

    df = pd.read_csv(args.input, dtype=str).fillna("")
    print(f"→ Loaded: {args.input} | rows={len(df)}")

    slate_game_date = (args.date or datetime.now().strftime("%Y-%m-%d")).strip()

    # Full-slate ESPN team_id -> PP abbr (needed to backfill opp_team_abbr in boxscore cache for H2H).
    tid_to_abbr_for_h2h: Dict[str, str] = {}
    if "team_id" in df.columns and "team_abbr" in df.columns:
        for _, r in df.iterrows():
            tid = str(r.get("team_id", "")).strip()
            abbr = str(r.get("team_abbr", "")).strip()
            if tid and abbr and tid.lower() != "nan":
                tid_to_abbr_for_h2h[tid] = abbr

    if "start_time" in df.columns:
        target_date = slate_game_date
        start_dt = pd.to_datetime(df["start_time"], errors="coerce")
        keep_mask = start_dt.dt.strftime("%Y-%m-%d").eq(target_date)
        kept = int(keep_mask.sum())
        total = len(df)
        df = df.loc[keep_mask].copy()
        print(f"[DateFilter] Kept {kept}/{total} rows for {target_date} (dropped {total - kept} rows)")
        if df.empty:
            print("⚠️ Date filter returned no rows; writing empty outputs.")

    # Only rank OK rows
    ok = df["stat_status"].astype(str).str.upper().eq("OK") if "stat_status" in df.columns else \
         df.get("status3", pd.Series([""] * len(df))).astype(str).str.upper().eq("OK")

    out = df.copy()

    if "line" not in out.columns:
        raise ValueError(
            f"step6: 'line' column missing. Available columns: {out.columns.tolist()}"
        )

    line_num = _to_num(out["line"])

    # ── Projection: weighted blend last5/last10/season ──────────────────────
    l5  = _to_num(out.get("stat_last5_avg",  pd.Series([""] * len(out))))
    l10 = _to_num(out.get("stat_last10_avg", pd.Series([""] * len(out))))
    ssn = _to_num(out.get("stat_season_avg", pd.Series([""] * len(out))))

    def blend_proj(row_idx):
        weights = [(l5.iloc[row_idx], 0.50), (l10.iloc[row_idx], 0.30), (ssn.iloc[row_idx], 0.20)]
        tv = tw = 0.0
        for v, w in weights:
            if not np.isnan(v): tv += v * w; tw += w
        return tv / tw if tw >= 0.1 else np.nan

    proj = pd.Series([blend_proj(i) for i in range(len(out))], index=out.index)
    out["projection"] = proj

    # ── Edge ────────────────────────────────────────────────────────────────
    out["edge"]     = proj - line_num
    out["abs_edge"] = out["edge"].abs()

    # ── Direction / eligibility ──────────────────────────────────────────────
    pick_type = out.get("pick_type", pd.Series(["Standard"] * len(out))).astype(str)
    forced    = pick_type.apply(_forced_over).astype(int)
    out["forced_over_only"] = forced

    bet_dir = np.where(forced.eq(1), "OVER",
              np.where(out["edge"] >= 0, "OVER", "UNDER"))
    out["bet_direction"] = bet_dir

    eligible   = pd.Series(True,  index=out.index)
    void_reason= pd.Series("",    index=out.index)

    miss = line_num.isna() | proj.isna()
    eligible.loc[miss]   = False
    void_reason.loc[miss] = "NO_PROJECTION_OR_LINE"

    # Drop Demon entirely + neg-edge Goblin to audit sheet (not eligible)
    is_demon     = pick_type.apply(lambda x: _norm_pick_type(x) == "Demon")
    goblin_neg   = pick_type.apply(lambda x: _norm_pick_type(x) == "Goblin") & (out.get("edge", pd.Series(0.0, index=out.index)).pipe(lambda s: pd.to_numeric(s, errors="coerce")).fillna(0) < 0)
    drop_mask    = is_demon | goblin_neg
    eligible.loc[drop_mask]    = False
    void_reason.loc[is_demon]  = "DROPPED_DEMON_AUDIT"
    void_reason.loc[goblin_neg & ~is_demon] = "DROPPED_NEG_EDGE_GOBDEM"

    # also mark non-OK rows ineligible
    eligible.loc[~ok] = False
    void_reason.loc[~ok & void_reason.eq("")] = "STAT_NOT_OK"

    out["eligible"]    = eligible.astype(int)
    out["void_reason"] = void_reason

    elig_mask = eligible

    # ── Defense adjustment (CBB: D1 has ~362 teams, NOT 30) ─────────────────
    # Try multiple possible column names for defense rank
    def_rank_col = next((c for c in ["OVERALL_DEF_RANK","OPP_OVERALL_DEF_RANK","opp_def_rank"] if c in out.columns), "")
    if def_rank_col:
        def_rank_num = _to_num(out[def_rank_col])
        # Auto-detect scale: if max rank > 40, assume full D1 (~362 teams)
        max_rank = def_rank_num.max()
        n_teams  = 362.0 if max_rank > 40 else 30.0
        mid_rank = (n_teams + 1.0) / 2.0
        nt = int(n_teams) if not pd.isna(n_teams) else 362
        tier_strs = def_rank_num.apply(
            lambda r: rank_to_tier(r, nt) if pd.notna(r) else ""
        )
        out["def_tier"] = tier_strs
        out["opp_def_tier"] = tier_strs
    else:
        def_rank_num = pd.Series([np.nan] * len(out), index=out.index)
        n_teams  = 362.0
        mid_rank = 181.5

    def _def_adj(row_idx):
        rank = def_rank_num.iloc[row_idx]
        if np.isnan(rank): return 0.0
        # Scale: best defense (rank=1) gives -6% boost to opposing scorer,
        # worst defense (rank=n_teams) gives +6% boost
        return float((rank - mid_rank) / mid_rank * 0.06)

    def_adj = pd.Series([_def_adj(i) for i in range(len(out))], index=out.index)
    out["def_adj"] = def_adj

    proj_adj = proj * (1 + def_adj)
    out["projection_adj"] = proj_adj
    out["edge_adj"]       = proj_adj - line_num
    # For UNDERs, a negative edge_adj is actually favourable — flip sign so the
    # score contribution is positive when projection < line (correct UNDER direction).
    def _edge_adj_dr_directional(row_idx):
        x = out["edge_adj"].iloc[row_idx]
        if isinstance(x, float) and np.isnan(x):
            return np.nan
        direction = str(out["bet_direction"].iloc[row_idx]).upper()
        signed = -float(x) if direction == "UNDER" else float(x)
        return _edge_transform(signed)

    out["edge_adj_dr"] = pd.Series(
        [_edge_adj_dr_directional(i) for i in range(len(out))], index=out.index
    )

    def _def_signal(row_idx):
        rank = def_rank_num.iloc[row_idx]
        direction = str(out["bet_direction"].iloc[row_idx]).upper()
        if np.isnan(rank): return 0.0
        # Normalize to [-1, +1] using full D1 scale
        signal = (rank - 1.0) / (n_teams - 1.0) * 2.0 - 1.0
        return float(signal if direction == "OVER" else -signal)

    def_signal = pd.Series([_def_signal(i) for i in range(len(out))], index=out.index)
    out["def_rank_signal"] = def_signal

    # ── Hit rate: blend last5 + last10 (direction-aware) ────────────────────
    # Pre-load both OVER and UNDER columns so we can pick the right one per row
    hr_over5   = _to_num(out.get("line_hit_rate_over_ou_5",  pd.Series([np.nan]*len(out))))
    hr_over10  = _to_num(out.get("line_hit_rate_over_ou_10", pd.Series([np.nan]*len(out))))
    hr_under5  = _to_num(out.get("line_hit_rate_under_ou_5",  pd.Series([np.nan]*len(out))))
    hr_under10 = _to_num(out.get("line_hit_rate_under_ou_10", pd.Series([np.nan]*len(out))))

    def blend_hr(row_idx):
        direction = str(out["bet_direction"].iloc[row_idx]).upper()
        if direction == "UNDER":
            h5  = hr_under5.iloc[row_idx]
            h10 = hr_under10.iloc[row_idx]
            # Fallback: derive UNDER rate as 1 - OVER rate if UNDER columns missing
            if np.isnan(h5) and not np.isnan(hr_over5.iloc[row_idx]):
                h5  = 1.0 - hr_over5.iloc[row_idx]
            if np.isnan(h10) and not np.isnan(hr_over10.iloc[row_idx]):
                h10 = 1.0 - hr_over10.iloc[row_idx]
        else:
            h5  = hr_over5.iloc[row_idx]
            h10 = hr_over10.iloc[row_idx]
        if not np.isnan(h5) and not np.isnan(h10): return h5 * 0.50 + h10 * 0.50
        if not np.isnan(h5):  return h5
        if not np.isnan(h10): return h10
        return np.nan

    line_hit_rate = pd.Series([blend_hr(i) for i in range(len(out))], index=out.index)
    out["line_hit_rate"] = line_hit_rate

    # ── Avg vs line (direction-aware) ────────────────────────────────────────
    for col in ("stat_last5_avg","stat_last10_avg","stat_season_avg"):
        out[f"_{col}_n"] = _to_num(out.get(col, pd.Series([""] * len(out))))

    line_filled = line_num.fillna(0)

    def _avg_vs_line(row_idx):
        ln = line_filled.iloc[row_idx]
        if ln == 0 or np.isnan(ln): return 0.0
        direction = str(out["bet_direction"].iloc[row_idx]).upper()
        score = tw = 0.0
        for col, w in [("_stat_last5_avg_n",0.50),("_stat_last10_avg_n",0.30),("_stat_season_avg_n",0.20)]:
            v = out[col].iloc[row_idx]
            if not np.isnan(v):
                raw = np.clip((v - ln) / ln, -1.0, 1.0)
                score += (-raw if direction == "UNDER" else raw) * w
                tw += w
        return float(score / tw) if tw > 0.1 else 0.0

    avg_vs_line = pd.Series([_avg_vs_line(i) for i in range(len(out))], index=out.index)
    out["avg_vs_line"] = avg_vs_line

    # ── Composite score (mirrors NBA step7) ─────────────────────────────────
    prop_norm_col = out.get("prop_norm", out.get("prop_type", pd.Series([""] * len(out)))).astype(str)
    prop_w   = prop_norm_col.apply(_prop_weight)
    rel_mult = pick_type.apply(_reliability_mult)

    hr_signal = (line_hit_rate - 0.5) * 2.0   # centre on 0, range ~[-1, +1]

    def _prior_signal(row_idx):
        pn  = str(prop_norm_col.iloc[row_idx])
        bd  = str(out["bet_direction"].iloc[row_idx]).upper()
        hr  = line_hit_rate.iloc[row_idx]
        pri = _prop_hr_prior(pn, bd)
        if np.isnan(hr): return float((pri - 0.5) * 2.0)
        return float(((hr + pri) / 2.0 - 0.5) * 2.0)

    prior_signal = pd.Series([_prior_signal(i) for i in range(len(out))], index=out.index)

    # Weighted composite
    raw_score = (
        out["edge_adj_dr"].fillna(0)  * 0.35
        + avg_vs_line                  * 0.20
        + def_signal                   * 0.15
        + hr_signal.fillna(0)          * 0.15
        + prior_signal                 * 0.15
    ) * prop_w * rel_mult

    # Zero out ineligible rows
    score = raw_score.where(elig_mask, other=np.nan)

    out["rank_score"] = score
    out["ml_prob"], out["ml_edge"], out["final_score"] = _apply_ml_blend(out, out["rank_score"])
    _apply_consistency_grade_scores_cbb(out, "CBB")

    # Game script risk adjustment
    _root_gs = Path(__file__).resolve().parents[3]
    _sd_gs = str(_root_gs / "scripts")
    if _sd_gs not in sys.path:
        sys.path.insert(0, _sd_gs)
    from game_script_risk import get_game_script_multiplier  # noqa: E402

    if "start_time" in out.columns:
        _st = out["start_time"].astype(str).str.strip()
        _dp = _st.str[:10]
        _gd_series = _dp.where(_dp.str.match(r"^\d{4}-\d{2}-\d{2}$"), slate_game_date)
    else:
        _gd_series = pd.Series([slate_game_date] * len(out), index=out.index)
    _team_col = next((c for c in ("team", "pp_team", "Team") if c in out.columns), "team")
    _prop_gs = "prop_norm" if "prop_norm" in out.columns else "prop_type"
    _gmults: list[float] = []
    _gnotes: list[str] = []
    for _i in range(len(out)):
        _r = out.iloc[_i]
        _gd = str(_gd_series.iloc[_i])
        _tm = str(_r.get(_team_col, "") or "").strip()
        _pt = str(_r.get(_prop_gs, "") or "").strip()
        _gm, _gn = get_game_script_multiplier(_tm, "CBB", _pt, _gd)
        _gmults.append(round(float(_gm), 3))
        _gnotes.append(_gn)
    out["game_script_mult"] = _gmults
    out["game_script_note"] = _gnotes
    out["final_score"] = pd.to_numeric(out["final_score"], errors="coerce").astype(float) * pd.Series(
        _gmults, dtype=float
    ).values

    out["rank_score"] = out["final_score"]
    out["tier"]       = out["rank_score"].apply(
        lambda x: _tier(x) if not (isinstance(x, float) and np.isnan(x)) else "D")

    # ── Final bet direction (step8-style logic inline) ────────────────────────
    final_dir = np.where(forced.eq(1), "OVER",
                np.where(out["edge"] >= 0, "OVER", "UNDER"))
    out["final_bet_direction"] = final_dir

    # ── Clean up temp columns ─────────────────────────────────────────────────
    drop_cols = [c for c in out.columns if c.startswith("_stat_")]
    # Remove always-blank ESPN ID columns (populated by step5 which is not part of CBB pipeline)
    drop_cols += [c for c in ("team_id", "espn_athlete_id", "attach_status") if c in out.columns]
    out.drop(columns=drop_cols, inplace=True)

    # ── Sort ──────────────────────────────────────────────────────────────────
    drop_mask_final = out["void_reason"].isin(["DROPPED_DEMON_AUDIT", "DROPPED_NEG_EDGE_GOBDEM"])
    dropped_df  = out[drop_mask_final].copy()
    out_active  = out[~drop_mask_final].copy()
    out_sorted  = out_active.sort_values("final_score", ascending=False, na_position="last")
    elig_sorted = elig_mask.reindex(out_sorted.index).fillna(False)

    # ── Head-to-Head stats ───────────────────────────────────────────────────
    player_col = next((c for c in ["player_norm","player","pp_player","player_name"] if c in out.columns), "")
    # Prefer opp_team_abbr to match boxscore cache keys (pp_opp_team can differ in formatting).
    opp_col    = next((c for c in ["opp_team_abbr","pp_opp_team","opp_team","opp"] if c in out.columns), "")
    prop_col   = next((c for c in ["prop_norm","prop_type"] if c in out.columns), "prop_norm")
    if player_col and opp_col:
        cache_for_h2h = _pick_cbb_boxscore_cache(args.cache, args.input)
        out = _attach_h2h(
            out,
            cache_for_h2h,
            "cbb",
            player_col,
            opp_col,
            prop_col,
            "line",
            tid_to_abbr=tid_to_abbr_for_h2h,
        )

    # ── Write Excel ───────────────────────────────────────────────────────────
    with pd.ExcelWriter(args.output, engine="openpyxl") as xw:
        out_sorted.to_excel(xw, index=False, sheet_name="ALL")
        out_sorted[elig_sorted].to_excel(xw, index=False, sheet_name="ELIGIBLE")
        for t in ["A","B","C","D"]:
            sub = out_sorted[out_sorted["tier"] == t]
            if len(sub): sub.to_excel(xw, index=False, sheet_name=f"TIER_{t}")
        if not dropped_df.empty:
            dropped_df.to_excel(xw, index=False, sheet_name="DROPPED")

    print(f"✅ Saved → {args.output}")
    print(f"ALL rows (active) : {len(out_sorted)}")
    print(f"DROPPED rows      : {len(dropped_df)}  (Demon + neg-edge Goblin, audit only)")
    print("Tier breakdown:")
    print(out_sorted["tier"].value_counts().to_string())
    print("\nVoid reasons (active):")
    vr = out_sorted.loc[~elig_sorted, "void_reason"].value_counts()
    print(vr.to_string() if len(vr) else "(none)")

    if args.output_csv:
        out_sorted.to_csv(args.output_csv, index=False)
        print(f"✅ Saved CSV → {args.output_csv}")


if __name__ == "__main__":
    main()
