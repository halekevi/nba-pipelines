#!/usr/bin/env python3
"""
step7_rank_props_mlb.py  (MLB Pipeline)

Mirrors NBA step7 with MLB-tuned:
  - Prop weights (pitcher Ks most stable, HR most volatile)
  - Hit rate priors by prop type
  - Separate hitter/pitcher defense adjustment logic

Run:
  py -3.14 step7_rank_props_mlb.py \
    --input step6_mlb_role_context.csv \
    --output step7_mlb_ranked.xlsx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
import numpy as np
import pandas as pd

for _efe_anc in Path(__file__).resolve().parents:
    if (_efe_anc / "scripts" / "edge_feature_engineering.py").is_file():
        _efe_sd = str(_efe_anc / "scripts")
        if _efe_sd not in sys.path:
            sys.path.insert(0, _efe_sd)
        break
from edge_feature_engineering import apply_ticket_eligibility_voids, build_feature_vector  # noqa: E402


def _to_num(s):
    return pd.to_numeric(s, errors="coerce")


def _norm_pick_type(x: str) -> str:
    t = str(x or "").strip().lower()
    if "gob" in t: return "Goblin"
    if "dem" in t: return "Demon"
    return "Standard"


def _forced_over_only(pick_type: str) -> int:
    return 1 if _norm_pick_type(pick_type) in ("Goblin", "Demon") else 0


# ── MLB prop weights ──────────────────────────────────────────────────────────
# Higher = more stable/predictable

_PROP_WEIGHTS = {
    # Pitcher (most predictable → least)
    "strikeouts":      1.10,   # pitcher Ks very stable
    "pitching_outs":   1.08,
    "innings_pitched": 1.08,
    "batters_faced":   1.06,
    "hits_allowed":    0.98,
    "walks_allowed":   0.97,
    "earned_runs":     0.92,   # ER very noisy

    # Hitter
    "hits":            1.05,
    "total_bases":     1.03,
    "hits_runs_rbi":   1.02,
    "runs":            1.00,
    "rbi":             0.99,
    "walks":           1.02,
    "stolen_bases":    1.01,
    "singles":         1.03,
    "doubles":         0.95,
    "triples":         0.80,   # very rare, high variance
    "home_runs":       0.85,   # HR very high variance
    "fantasy_score":   1.00,
}

def _prop_weight(prop_norm: str) -> float:
    return float(_PROP_WEIGHTS.get(str(prop_norm).lower().strip(), 0.93))


# ── MLB hit rate priors (OVER direction) ─────────────────────────────────────

_PROP_HIT_RATE_PRIOR = {
    # Pitcher
    "strikeouts":      0.630,
    "pitching_outs":   0.620,
    "innings_pitched": 0.610,
    "batters_faced":   0.600,
    "hits_allowed":    0.530,
    "walks_allowed":   0.520,
    "earned_runs":     0.490,

    # Hitter
    "hits":            0.570,
    "total_bases":     0.560,
    "hits_runs_rbi":   0.555,
    "fantasy_score":   0.545,
    "runs":            0.540,
    "rbi":             0.535,
    "walks":           0.540,
    "stolen_bases":    0.530,
    "singles":         0.560,
    "doubles":         0.500,
    "triples":         0.450,
    "home_runs":       0.470,
}

def _prop_hit_rate_prior(prop_norm: str, direction: str) -> float:
    key  = str(prop_norm).lower().strip()
    base = _PROP_HIT_RATE_PRIOR.get(key, 0.530)
    if direction == "UNDER":
        # Earned runs under is great — pitchers tend to be set low
        if key == "earned_runs":    return 0.640
        if key == "home_runs":      return 0.630
        if key == "hits_allowed":   return 0.570
        if key == "triples":        return 0.670
        return float(1.0 - base)
    return float(base)


def _reliability_mult(pick_type: str) -> float:
    pt = _norm_pick_type(pick_type)
    return {"Standard": 1.00, "Goblin": 1.06, "Demon": 0.75}.get(pt, 0.97)


def _safe_float(x) -> float:
    v = pd.to_numeric(pd.Series([x]), errors="coerce").iloc[0]
    return float(v) if not pd.isna(v) else np.nan


def _edge_transform(edge: float, cap: float = 3.0, power: float = 0.85) -> float:
    if np.isnan(edge): return np.nan
    s = 1.0 if edge >= 0 else -1.0
    x = min(abs(edge), cap)
    return s * (x ** power)


def _tier_from_score(score: float) -> str:
    if np.isnan(score): return "D"
    if score >= 1.25:   return "A"
    if score >= 0.75:   return "B"
    if score >= 0.40:   return "C"
    return "D"


def _projection_from_row(row: pd.Series) -> float:
    for c in ("stat_last5_avg", "stat_last10_avg", "stat_season_avg"):
        v = _safe_float(row.get(c, np.nan))
        if not np.isnan(v):
            return v
    return np.nan


def _line_hit_rate_from_row(row: pd.Series) -> float:
    direction = str(row.get("bet_direction", "OVER")).upper()
    hr5 = hr10 = np.nan
    if direction == "UNDER":
        for c in ("line_hit_rate_under_ou_5", "line_hit_rate_under_5"):
            if c in row.index:
                v = _safe_float(row.get(c))
                if not np.isnan(v): hr5 = v; break
        if np.isnan(hr5):
            o = _safe_float(row.get("last5_over",  np.nan))
            u = _safe_float(row.get("last5_under", np.nan))
            if not np.isnan(o) and not np.isnan(u):
                d = o + u
                hr5 = u / d if d > 0 else np.nan
        for c in ("line_hit_rate_under_ou_10", "line_hit_rate_under_10"):
            if c in row.index:
                v = _safe_float(row.get(c))
                if not np.isnan(v): hr10 = v; break
    else:
        for c in ("line_hit_rate_over_ou_5", "line_hit_rate_over_5", "last5_hit_rate"):
            if c in row.index:
                v = _safe_float(row.get(c))
                if not np.isnan(v): hr5 = v; break
        for c in ("line_hit_rate_over_ou_10", "line_hit_rate_over_10"):
            if c in row.index:
                v = _safe_float(row.get(c))
                if not np.isnan(v): hr10 = v; break

    if not np.isnan(hr5) and not np.isnan(hr10): return hr5 * 0.50 + hr10 * 0.50
    if not np.isnan(hr5):  return hr5
    if not np.isnan(hr10): return hr10
    return np.nan


def _minutes_certainty(row: pd.Series) -> float:
    tier = str(row.get("minutes_tier", "")).upper()
    return {"HIGH": 1.00, "MEDIUM": 0.90, "LOW": 0.75, "UNKNOWN": 0.80}.get(tier, 0.85)


def _def_adjustment(row: pd.Series, n_teams: int = 30) -> float:
    rank = _safe_float(row.get("OVERALL_DEF_RANK", np.nan))
    if np.isnan(rank): return 0.0
    mid = (n_teams + 1) / 2.0
    return float((rank - mid) / mid * 0.06)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",   default="MLB/scripts/step6_mlb_team_role.csv")
    ap.add_argument("--output",  default="MLB/scripts/step7_mlb_ranked.xlsx")
    ap.add_argument("--n_teams", type=int, default=30)
    args = ap.parse_args()

    print("[PropORACLE-step7_rank_props_mlb] Starting...")
    print(f"→ Loading: {args.input}")
    out = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig").fillna("")
    REPO_ROOT = Path(__file__).resolve().parents[2]
    _sd_usage = str(REPO_ROOT / "scripts")
    if _sd_usage not in sys.path:
        sys.path.insert(0, _sd_usage)
    try:
        from usage_redistribution import apply_usage_redistribution  # noqa: E402
        run_date = (
            str(out["game_date"].dropna().iloc[0])[:10]
            if "game_date" in out.columns and not out["game_date"].dropna().empty
            else pd.Timestamp.today().strftime("%Y-%m-%d")
        )
        out = apply_usage_redistribution(out, sport="MLB", date=run_date, repo_root=str(REPO_ROOT))
    except Exception as e:
        print(f"⚠️ usage redistribution skipped: {e}")

    out["pick_type"] = out.get("pick_type", pd.Series(["Standard"] * len(out))).astype(str).apply(_norm_pick_type)

    # MLB prop norm alias map
    _PROP_NORM_MAP = {
        "total bases":        "total_bases",
        "totalbases":         "total_bases",
        "home runs":          "home_runs",
        "homeruns":           "home_runs",
        "stolen bases":       "stolen_bases",
        "stolenbases":        "stolen_bases",
        "fantasy score":      "fantasy_score",
        "fantasyscore":       "fantasy_score",
        "hits+runs+rbi":      "hits_runs_rbi",
        "hitsrunsrbi":        "hits_runs_rbi",
        "pitching outs":      "pitching_outs",
        "pitchingouts":       "pitching_outs",
        "innings pitched":    "innings_pitched",
        "inningspitched":     "innings_pitched",
        "hits allowed":       "hits_allowed",
        "hitsallowed":        "hits_allowed",
        "earned runs":        "earned_runs",
        "earnedrunsr":        "earned_runs",
        "walks allowed":      "walks_allowed",
        "walksallowed":       "walks_allowed",
        "batters faced":      "batters_faced",
        "battersfaced":       "batters_faced",
        "pitcher strikeouts": "strikeouts",
    }
    out["prop_norm"] = out["prop_norm"].astype(str).str.lower().str.strip().map(
        lambda x: _PROP_NORM_MAP.get(x, x)
    )

    line_num = _to_num(out["line"])
    proj     = out.apply(_projection_from_row, axis=1)
    out["projection"] = proj
    if "usage_boost_proj" in out.columns:
        out["projection"] = _to_num(out["projection"]).fillna(0.0) + _to_num(out["usage_boost_proj"]).fillna(0.0)
    out["edge"]       = _to_num(out["projection"]) - line_num
    out["abs_edge"]   = out["edge"].abs()

    forced = out["pick_type"].apply(_forced_over_only).astype(int)
    out["forced_over_only"] = forced

    bet_dir = np.where(forced.eq(1), "OVER", np.where(out["edge"] >= 0, "OVER", "UNDER"))
    out["bet_direction"] = bet_dir

    eligible    = pd.Series(True, index=out.index)
    void_reason = pd.Series("",   index=out.index)

    # Note: "NO_PROJECTION_OR_LINE" is often a downstream symptom of earlier pipeline cache misses
    # (step4/step5 not finding MLB stat values / hit rates), not an intrinsic step7 ranking issue.
    miss = line_num.isna() | pd.isna(out["projection"])
    eligible.loc[miss]    = False
    void_reason.loc[miss] = "NO_PROJECTION_OR_LINE"

    neg_forced = forced.eq(1) & (out["edge"] < 0)
    eligible.loc[neg_forced]    = False
    void_reason.loc[neg_forced] = "FORCED_OVER_NEG_EDGE"

    out["eligible"]    = eligible.astype(int)
    out["void_reason"] = void_reason

    out["edge_dr"]           = out["edge"].apply(_edge_transform)
    out["line_hit_rate"]     = out.apply(_line_hit_rate_from_row, axis=1)
    def _line_hit_over_only_row(row: pd.Series) -> float:
        hr5 = hr10 = np.nan
        for c in ("line_hit_rate_over_ou_5", "line_hit_rate_over_5", "last5_hit_rate"):
            if c in row.index:
                v = _safe_float(row.get(c))
                if not np.isnan(v):
                    hr5 = v
                    break
        for c in ("line_hit_rate_over_ou_10", "line_hit_rate_over_10"):
            if c in row.index:
                v = _safe_float(row.get(c))
                if not np.isnan(v):
                    hr10 = v
                    break
        if not np.isnan(hr5) and not np.isnan(hr10):
            return hr5 * 0.50 + hr10 * 0.50
        if not np.isnan(hr5):
            return hr5
        if not np.isnan(hr10):
            return hr10
        return np.nan

    _lho = out.apply(_line_hit_over_only_row, axis=1)
    _bu = out["bet_direction"].astype(str).str.upper().str.strip().eq("UNDER")
    out["composite_hit_rate"] = np.where(_bu, 1.0 - _lho, _lho)
    out["minutes_certainty"] = out.apply(_minutes_certainty, axis=1)
    out["prop_weight"]       = out["prop_norm"].astype(str).apply(_prop_weight)
    out["reliability_mult"]  = out["pick_type"].astype(str).apply(_reliability_mult)

    elig_mask = out["eligible"].astype(int).eq(1)

    def zcol(s: pd.Series, direction_aware: bool = False) -> pd.Series:
        x      = pd.to_numeric(s, errors="coerce")
        result = pd.Series([0.0] * len(x), index=x.index)
        if direction_aware and "bet_direction" in out.columns:
            for direction in ("OVER", "UNDER"):
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

    out["edge_z"]      = zcol(out["edge"],           direction_aware=True)
    out["line_hit_z"]  = zcol(out["line_hit_rate"],  direction_aware=True)
    out["min_z"]       = zcol(out["minutes_certainty"])

    def_adj = out.apply(lambda r: _def_adjustment(r, args.n_teams), axis=1)
    out["def_adj"]        = def_adj
    out["projection_adj"] = pd.to_numeric(out["projection"], errors="coerce") * (1.0 + def_adj.astype(float))
    out["edge_adj"]       = out["projection_adj"] - line_num

    def _edge_adj_dr(i):
        x = out["edge_adj"].iloc[i]
        if pd.isna(x): return np.nan
        direction = str(out["bet_direction"].iloc[i]).upper()
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
        out[col + "_num"] = _to_num(out[col]) if col in out.columns else pd.Series([np.nan] * len(out), index=out.index)

    def _avg_vs_line(i):
        l = line_num_filled.iloc[i]
        if l == 0 or np.isnan(l): return 0.0
        direction = str(out["bet_direction"].iloc[i]).upper()
        score = total_w = 0.0
        for col, w in [("stat_last5_avg_num", 0.50), ("stat_last10_avg_num", 0.30), ("stat_season_avg_num", 0.20)]:
            v = out[col].iloc[i]
            if not np.isnan(v):
                raw = np.clip((v - l) / l, -1.0, 1.0)
                if direction == "UNDER": raw = -raw
                score += raw * w; total_w += w
        return float(score / total_w) if total_w > 0.1 else 0.0

    avg_vs_line = pd.Series([_avg_vs_line(i) for i in range(len(out))], index=out.index)
    out["avg_vs_line"]   = avg_vs_line
    out["avg_vs_line_z"] = zcol(avg_vs_line, direction_aware=True)

    prop_hr_prior = out.apply(
        lambda r: _prop_hit_rate_prior(r.get("prop_norm", ""), str(r.get("bet_direction", "OVER")).upper()), axis=1
    )
    out["prop_hr_prior"] = prop_hr_prior
    out["prop_hr_z"]     = zcol(prop_hr_prior, direction_aware=True)

    score = (
        out["edge_adj_dr"].astype(float).fillna(0.0)     * 0.85
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
    # Direction-aware edge gate (matches NBA step7): good UNDERs have edge_adj < 0 but edge_adj_dr > 0.
    _eadr = pd.to_numeric(out["edge_adj_dr"], errors="coerce").fillna(-999.0)
    _is_dem = out["pick_type"].astype(str).str.lower().str.contains("dem")
    score = score.where(elig_mask & ((_eadr > 0.0) | _is_dem), np.nan)

    out["rank_score"] = score
    out["tier"]       = out["rank_score"].apply(_tier_from_score)
    if "recommended_side" not in out.columns:
        out["recommended_side"] = out["bet_direction"]
    out = build_feature_vector(out, "MLB")
    out = apply_ticket_eligibility_voids(out, "MLB")
    elig_mask = out["eligible"].astype(int).eq(1)

    with pd.ExcelWriter(args.output, engine="openpyxl") as w:
        out.to_excel(w, sheet_name="ALL",      index=False)
        out.loc[elig_mask].to_excel(w, sheet_name="ELIGIBLE", index=False)

    print(f"✅ Saved → {args.output}")
    print(f"ALL rows: {len(out)}")
    print("\nTier counts:"); print(out["tier"].value_counts().to_string())
    print("\nIneligible reasons:")
    vr = out.loc[~elig_mask, "void_reason"].value_counts()
    print(vr.to_string() if len(vr) else "(none)")
    print("\nScore percentiles (eligible):")
    rs = pd.to_numeric(out.loc[elig_mask, "rank_score"], errors="coerce")
    print(rs.quantile([0.50, 0.70, 0.80, 0.85, 0.90, 0.95]).round(3).to_string())


if __name__ == "__main__":
    main()
