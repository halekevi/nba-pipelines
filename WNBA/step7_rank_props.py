#!/usr/bin/env python3
"""
step7_rank_props.py  (WNBA Pipeline)

Ranks WNBA props using the same 5-signal scoring engine as NBA,
adapted for WNBA-specific characteristics:

  - 13 teams (not 30) → defense rank signal scaled to 13
  - Smaller slate sizes → tier thresholds adjusted down
  - Same prop types as NBA (pts/reb/ast/stl/blk/combos/shooting)
  - Same projection formula (last5×0.50 + last10×0.30 + season×0.20)

Scoring signals (same formula as NBA):
  edge_adj_dr    × 0.85  — direction-aware defense-adjusted edge
  line_hit_z     × 0.85  — blended L5/L10 hit rate
  avg_vs_line_z  × 0.75  — weighted avg vs line
  def_rank_z     × 0.80  — opponent defense signal (13-team scale)
  prop_hr_z      × 0.50  — Bayesian prop hit-rate prior
  min_z          × 0.25  — minutes certainty

Run:
  py -3.14 step7_rank_props.py \
      --input  step6_wnba_context.csv \
      --output step7_wnba_ranked.xlsx
"""

from __future__ import annotations

import argparse
import numpy as np
import pandas as pd


def _to_num(s):
    return pd.to_numeric(s, errors="coerce")


def _norm_pick_type(x: str) -> str:
    t = (str(x) if x is not None else "").strip().lower()
    if "gob" in t: return "Goblin"
    if "dem" in t: return "Demon"
    return "Standard"


def _forced_over_only(pick_type: str) -> int:
    return 1 if _norm_pick_type(pick_type) in ("Goblin","Demon") else 0


# ── WNBA prop weights (same as NBA — identical prop types) ────────────────────

_PROP_WEIGHTS = {
    "pts":                   1.03,
    "reb":                   1.06,
    "ast":                   1.05,
    "stl":                   1.08,
    "blk":                   1.02,
    "stocks":                1.04,
    "fg3m":                  1.03,
    "fg3a":                  0.88,
    "fg2m":                  1.01,
    "fg2a":                  0.92,
    "fgm":                   0.99,
    "fga":                   0.99,
    "ftm":                   1.01,
    "fta":                   0.98,
    "tov":                   0.94,
    "pr":                    1.01,
    "pa":                    1.01,
    "ra":                    1.02,
    "pra":                   0.99,
    "fantasy":               0.91,
}

def _prop_weight(prop_norm: str) -> float:
    return float(_PROP_WEIGHTS.get(str(prop_norm).lower().strip(), 0.93))


# ── WNBA hit-rate priors (using NBA priors as baseline — update as data grows) ─

_PROP_HIT_RATE_PRIOR = {
    "stl":    0.697,
    "fantasy":0.595,
    "fg3m":   0.623,
    "reb":    0.617,
    "ra":     0.600,
    "ast":    0.593,
    "ftm":    0.583,
    "pr":     0.568,
    "pts":    0.566,
    "stocks": 0.547,
    "blk":    0.545,
    "pra":    0.545,
    "fga":    0.558,
    "pa":     0.557,
    "fgm":    0.519,
    "fg2m":   0.528,
    "fg2a":   0.463,
    "tov":    0.484,
    "fg3a":   0.444,
    "fta":    0.545,
}

def _prop_hit_rate_prior(prop_norm: str, direction: str) -> float:
    key  = str(prop_norm).lower().strip()
    base = _PROP_HIT_RATE_PRIOR.get(key, 0.545)
    if direction == "UNDER":
        if key == "fantasy":         return 0.371
        if key in ("fga","fg2a"):    return 0.645
        if key == "reb":             return 0.591
        if key == "pa":              return 0.590
        if key in ("pts","pr","pra"):return 0.540
        return float(1.0 - base)
    return float(base)


def _reliability_mult(pick_type: str) -> float:
    return {"Standard": 1.00, "Goblin": 1.06, "Demon": 0.75}.get(_norm_pick_type(pick_type), 0.97)


def _safe_float(x):
    v = pd.to_numeric(pd.Series([x]), errors="coerce").iloc[0]
    return float(v) if not pd.isna(v) else np.nan


# ── WNBA tier thresholds (adjusted down from NBA due to smaller slate sizes) ──

def _tier_from_score(score: float) -> str:
    if np.isnan(score): return "D"
    if score >= 2.00:   return "A"   # NBA=2.50
    if score >= 1.40:   return "B"   # NBA=1.75
    if score >= 0.85:   return "C"   # NBA=1.10
    return "D"


# ── projection builder ────────────────────────────────────────────────────────

_PLAYER_PREFIX_BY_PROP = {
    "fga":"fga","fgm":"fgm","fg2a":"fg2a","fg2m":"fg2m",
    "fg3a":"fg3a","fg3m":"fg3m","fta":"fta","ftm":"ftm",
}

_COMBO_CORRECTIONS = {"pr":1.05,"pa":1.06,"ra":1.08,"pra":1.07,"fantasy":1.04}

def _get_player_avg(row: pd.Series, prefix: str, window: str) -> float:
    candidates = {
        "last5":  [f"{prefix}_player_last5_avg",  f"{prefix}_last5_avg"],
        "last10": [f"{prefix}_player_last10_avg", f"{prefix}_last10_avg"],
        "season": [f"{prefix}_player_season_avg", f"{prefix}_season_avg"],
    }.get(window, [])
    for c in candidates:
        if c in row.index:
            v = _safe_float(row.get(c))
            if not np.isnan(v): return v
    return np.nan


def _projection_from_row(row: pd.Series) -> float:
    prop_norm = str(row.get("prop_norm","")).lower().strip()
    weights   = [("stat_last5_avg",0.50),("stat_last10_avg",0.30),("stat_season_avg",0.20)]
    total_w = total_v = 0.0
    for col, w in weights:
        if col in row.index:
            v = _safe_float(row.get(col))
            if not np.isnan(v):
                total_v += v * w
                total_w += w
    if total_w < 0.1:
        base_prefix = _PLAYER_PREFIX_BY_PROP.get(prop_norm)
        if base_prefix:
            for win, w in [("last5",0.50),("last10",0.30),("season",0.20)]:
                v = _get_player_avg(row, base_prefix, win)
                if not np.isnan(v):
                    total_v += v * w
                    total_w += w
    if total_w < 0.1:
        return np.nan
    raw = float(total_v / total_w)
    return raw * _COMBO_CORRECTIONS.get(prop_norm, 1.0)


# ── hit rate helper ───────────────────────────────────────────────────────────

def _line_hit_rate_from_row(row: pd.Series) -> float:
    direction = str(row.get("bet_direction","OVER")).upper()
    hr5 = hr10 = np.nan
    if direction == "UNDER":
        for c in ("line_hit_rate_under_ou_5","line_hit_rate_under_5"):
            if c in row.index:
                v = _safe_float(row.get(c))
                if not np.isnan(v): hr5 = v; break
        for c in ("line_hit_rate_under_ou_10","line_hit_rate_under_10"):
            if c in row.index:
                v = _safe_float(row.get(c))
                if not np.isnan(v): hr10 = v; break
    else:
        for c in ("line_hit_rate_over_ou_5","line_hit_rate_over_5","last5_hit_rate"):
            if c in row.index:
                v = _safe_float(row.get(c))
                if not np.isnan(v): hr5 = v; break
        for c in ("line_hit_rate_over_ou_10","line_hit_rate_over_10"):
            if c in row.index:
                v = _safe_float(row.get(c))
                if not np.isnan(v): hr10 = v; break
    if not np.isnan(hr5) and not np.isnan(hr10): return hr5*0.50 + hr10*0.50
    if not np.isnan(hr5):  return hr5
    if not np.isnan(hr10): return hr10
    return np.nan


def _minutes_certainty(row: pd.Series) -> float:
    return {"HIGH":1.00,"MEDIUM":0.90,"LOW":0.75}.get(str(row.get("minutes_tier","")).upper(), 0.80)


def _edge_transform(edge: float, cap: float = 3.0, power: float = 0.85) -> float:
    if np.isnan(edge): return np.nan
    s = 1.0 if edge >= 0 else -1.0
    return s * (min(abs(edge), cap) ** power)


# ── WNBA defense rank signal (13-team scale) ──────────────────────────────────

_N_TEAMS_WNBA = 13

def _def_adjustment(row: pd.Series) -> float:
    rank = pd.to_numeric(pd.Series([row.get("OVERALL_DEF_RANK", np.nan)]), errors="coerce").iloc[0]
    if pd.isna(rank): return 0.0
    # Scale to 13 teams: midpoint = 7.0
    return float((rank - 7.0) / 7.0 * 0.06)


def _def_rank_signal(row: pd.Series) -> float:
    rank      = pd.to_numeric(pd.Series([row.get("OVERALL_DEF_RANK", np.nan)]), errors="coerce").iloc[0]
    direction = str(row.get("bet_direction","OVER")).upper()
    if pd.isna(rank): return 0.0
    # Normalize to [-1, 1] using 13-team scale
    signal = (rank - 1.0) / (_N_TEAMS_WNBA - 1.0) * 2.0 - 1.0
    return float(signal if direction == "OVER" else -signal)


# ── prop norm map ─────────────────────────────────────────────────────────────

_PROP_NORM_MAP = {
    "3-pt made":"fg3m","3-pt attempted":"fg3a","3pt made":"fg3m","3pt attempted":"fg3a",
    "three pointers made":"fg3m","three pointers attempted":"fg3a",
    "two pointers made":"fg2m","two pointers attempted":"fg2a",
    "2pt made":"fg2m","2pt attempted":"fg2a",
    "free throws made":"ftm","free throws attempted":"fta",
    "freethrowsmade":"ftm","freethrowsattempted":"fta",
    "fg attempted":"fga","fg made":"fgm",
    "field goals attempted":"fga","field goals made":"fgm",
    "fg3a":"fg3a","fg3m":"fg3m","fg2a":"fg2a","fg2m":"fg2m","fga":"fga","fgm":"fgm",
    "ftm":"ftm","fta":"fta",
}


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  default="step6_wnba_context.csv")
    ap.add_argument("--output", default="step7_wnba_ranked.xlsx")
    args = ap.parse_args()

    df  = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig").fillna("")
    out = df.copy()

    for col, default in [("line",""),("pick_type","Standard")]:
        if col not in out.columns: out[col] = default

    if "prop_norm" not in out.columns:
        out["prop_norm"] = out.get("prop_type", pd.Series([""] * len(out))).astype(str).str.lower()

    out["prop_norm"] = out["prop_norm"].astype(str).str.lower().str.strip().map(
        lambda x: _PROP_NORM_MAP.get(x, x)
    )

    line_num = _to_num(out["line"])
    proj     = out.apply(_projection_from_row, axis=1)
    out["projection"] = proj
    out["edge"]       = proj - line_num
    out["abs_edge"]   = out["edge"].abs()

    forced = out["pick_type"].apply(_forced_over_only).astype(int)
    out["forced_over_only"] = forced

    bet_dir = np.where(forced.eq(1), "OVER", np.where(out["edge"] >= 0, "OVER", "UNDER"))
    out["bet_direction"] = bet_dir

    eligible    = pd.Series(True, index=out.index)
    void_reason = pd.Series("", index=out.index)

    miss = line_num.isna() | pd.isna(out["projection"])
    eligible.loc[miss]    = False
    void_reason.loc[miss] = "NO_PROJECTION_OR_LINE"

    neg_forced = forced.eq(1) & (out["edge"] < 0)
    eligible.loc[neg_forced]    = False
    void_reason.loc[neg_forced] = "FORCED_OVER_NEG_EDGE"

    out["eligible"]    = eligible.astype(int)
    out["void_reason"] = void_reason

    out["edge_dr"]          = out["edge"].apply(_edge_transform)
    out["line_hit_rate"]    = out.apply(_line_hit_rate_from_row, axis=1)
    out["minutes_certainty"]= out.apply(_minutes_certainty, axis=1)
    out["prop_weight"]      = out["prop_norm"].astype(str).apply(_prop_weight)
    out["reliability_mult"] = out["pick_type"].astype(str).apply(_reliability_mult)

    elig_mask = out["eligible"].astype(int).eq(1)

    def zcol(s: pd.Series, direction_aware: bool = False) -> pd.Series:
        x      = pd.to_numeric(s, errors="coerce")
        result = pd.Series([0.0] * len(x), index=x.index)
        if direction_aware and "bet_direction" in out.columns:
            for direction in ("OVER","UNDER"):
                dm = elig_mask & (out["bet_direction"].astype(str).str.upper() == direction)
                if dm.sum() < 2: continue
                mu = x[dm].mean(); sd = x[dm].std()
                if sd and not np.isnan(sd) and sd > 1e-9:
                    result.loc[dm.index[dm]] = ((x[dm] - mu) / sd).values
            return result
        mu = x[elig_mask].mean(); sd = x[elig_mask].std()
        if sd and not np.isnan(sd) and sd > 1e-9:
            return (x - mu) / sd
        return result

    out["edge_z"]      = zcol(out["edge"], direction_aware=True)
    out["line_hit_z"]  = zcol(out["line_hit_rate"], direction_aware=True)
    out["min_z"]       = zcol(out["minutes_certainty"])

    def_adj = out.apply(_def_adjustment, axis=1)
    out["def_adj"] = def_adj

    proj_base = pd.to_numeric(out["projection"], errors="coerce")
    out["projection_adj"] = proj_base * (1.0 + def_adj.astype(float))
    out["edge_adj"]       = out["projection_adj"] - line_num

    def _edge_adj_dr(row_idx):
        x         = out["edge_adj"].iloc[row_idx]
        if pd.isna(x): return np.nan
        direction = str(out["bet_direction"].iloc[row_idx]).upper()
        signed    = -float(x) if direction == "UNDER" else float(x)
        return _edge_transform(signed)

    out["edge_adj_dr"] = pd.Series([_edge_adj_dr(i) for i in range(len(out))], index=out.index)

    def_signal = out.apply(_def_rank_signal, axis=1)
    out["def_rank_signal"] = def_signal
    out["def_rank_z"]      = zcol(def_signal, direction_aware=True)

    line_num_filled = line_num.fillna(0)
    for col in ("stat_last5_avg","stat_last10_avg","stat_season_avg"):
        out[col+"_num"] = _to_num(out[col]) if col in out.columns else _to_num(pd.Series([""] * len(out)))

    def _avg_vs_line(row_idx):
        line      = line_num_filled.iloc[row_idx]
        if line == 0 or np.isnan(line): return 0.0
        direction = str(out["bet_direction"].iloc[row_idx]).upper()
        score = total_w = 0.0
        for col, w in [("stat_last5_avg_num",0.50),("stat_last10_avg_num",0.30),("stat_season_avg_num",0.20)]:
            v = out[col].iloc[row_idx]
            if not np.isnan(v):
                raw = np.clip((v - line) / line, -1.0, 1.0)
                if direction == "UNDER": raw = -raw
                score += raw * w; total_w += w
        return float(score / total_w) if total_w > 0.1 else 0.0

    avg_vs_line = pd.Series([_avg_vs_line(i) for i in range(len(out))], index=out.index)
    out["avg_vs_line"]   = avg_vs_line
    out["avg_vs_line_z"] = zcol(avg_vs_line, direction_aware=True)

    prop_hr_prior = out.apply(
        lambda r: _prop_hit_rate_prior(r.get("prop_norm",""), str(r.get("bet_direction","OVER")).upper()),
        axis=1
    )
    out["prop_hr_prior"] = prop_hr_prior
    out["prop_hr_z"]     = zcol(prop_hr_prior, direction_aware=True)

    score = (
        out["edge_adj_dr"].astype(float).fillna(0.0)      * 0.85
        + out["line_hit_z"].astype(float).fillna(0.0)     * 0.85
        + out["avg_vs_line_z"].astype(float).fillna(0.0)  * 0.75
        + out["def_rank_z"].astype(float).fillna(0.0)     * 0.80
        + out["prop_hr_z"].astype(float).fillna(0.0)      * 0.50
        + out["min_z"].astype(float).fillna(0.0)          * 0.25
    )
    score = (
        score
        * out["prop_weight"].astype(float).fillna(1.0)
        * out["reliability_mult"].astype(float).fillna(1.0)
    )
    score = score.where(elig_mask, np.nan)

    out["rank_score"] = score
    out["tier"]       = out["rank_score"].apply(_tier_from_score)

    with pd.ExcelWriter(args.output, engine="openpyxl") as w:
        out.to_excel(w, sheet_name="ALL", index=False)
        out.loc[elig_mask].to_excel(w, sheet_name="ELIGIBLE", index=False)
        for tier in ["A","B","C","D"]:
            sub = out.loc[elig_mask & (out["tier"] == tier)]
            if len(sub):
                sub.to_excel(w, sheet_name=f"Tier {tier}", index=False)

    print(f"✅ Saved → {args.output}  ALL={len(out)}  ELIGIBLE={int(elig_mask.sum())}")
    print("Tier counts:", out["tier"].value_counts().to_dict())
    print("Void reasons:", out.loc[~elig_mask,"void_reason"].value_counts().to_dict())


if __name__ == "__main__":
    main()
