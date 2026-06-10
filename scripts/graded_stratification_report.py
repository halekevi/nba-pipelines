#!/usr/bin/env python3
"""
Weekly / post-retrain: walk graded XLSX discovery tree, emit stratified CSV + HTML.

Tier 1: hit rates by (sport, prop, direction, pick_type, line_bucket) plus
context_known / defense_known / minutes_known. Demon pick types are excluded
from all hit-rate rating (data collection only). Goblin UNDER rows are also
dropped — Goblin is OVER-only on PrizePicks (not a valid market side).

Segment and prop-type tier scores blend every discoverable numeric graded/pipeline
column (IDs, labels, and outcomes excluded) with min–max normalization within
market segment, not only hit rate / edge / L5 / consistency.

Calibration: per (sport, direction, pick_type) with n>=100, decile bins on
ml_prob vs result_binary; flag bins where |mean_pred - mean_true| > 0.08.

Default roots: <repo>/ui_runner/graded_slate and <repo>/outputs.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from graded_line_quality_features import (  # noqa: E402
    STRAT_FEATURE_COLUMNS,
    add_stratification_columns,
    line_bucket,
)

try:
    from sklearn.calibration import calibration_curve
except ImportError:
    calibration_curve = None  # type: ignore[misc, assignment]

try:
    from analyze_graded_prop_winners import (  # type: ignore[import-not-found]
        exclude_non_rating_legs,
        is_demon_pick_type,
        load_unified,
        normalize_decided,
    )
except ImportError:
    load_unified = None  # type: ignore[misc, assignment]
    normalize_decided = None  # type: ignore[misc, assignment]
    exclude_non_rating_legs = None  # type: ignore[misc, assignment]
    is_demon_pick_type = None  # type: ignore[misc, assignment]

try:
    from edge_predict_utils import (  # type: ignore[import-not-found]
        augment_graded_box_raw_for_edge,
        graded_filename_sport_to_train_sport,
    )
except ImportError:
    augment_graded_box_raw_for_edge = None  # type: ignore[misc, assignment]
    graded_filename_sport_to_train_sport = None  # type: ignore[misc, assignment]

try:
    from edge_feature_engineering import build_feature_vector  # type: ignore[import-not-found]
except ImportError:
    build_feature_vector = None  # type: ignore[misc, assignment]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _sport_display(s: str) -> str:
    u = str(s or "").strip().lower()
    mapping = {
        "nba": "NBA",
        "nhl": "NHL",
        "mlb": "MLB",
        "soccer": "Soccer",
        "football": "Soccer",
        "cbb": "CBB",
        "wcbb": "CBB",
        "wnba": "WNBA",
        "tennis": "Tennis",
        "nba1h": "NBA1H",
        "nba1q": "NBA1Q",
    }
    return mapping.get(u, str(s or "").strip().upper() or "UNKNOWN")


def _line_series(df: pd.DataFrame) -> pd.Series:
    for c in ("line_score", "line", "Line", "LINE"):
        if c in df.columns:
            return pd.to_numeric(df[c], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def _home_away_series(df: pd.DataFrame) -> pd.Series:
    for c in ("home_away", "home_away_flag", "is_home", "venue_side", "HA"):
        if c not in df.columns:
            continue
        s = df[c].astype(str).str.strip().str.upper()
        s = s.replace({"NAN": "", "NONE": "", "NULL": ""})
        if s.notna().any() and (s != "").any():
            return s.mask(s.eq(""), "(missing)")
    return pd.Series("(not in graded export)", index=df.index, dtype=str)


def _pick_type_norm(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower()


def _pick_type_base(raw: pd.Series) -> pd.Series:
    s = raw.astype(str).str.strip().str.lower()
    out = pd.Series("(missing)", index=raw.index, dtype=str)
    out.loc[s.isin(["goblin"])] = "Goblin"
    out.loc[s.isin(["demon"])] = "Demon"
    out.loc[s.isin(["standard", "std", "—", "-", "–", "", "nan", "none", "null"])] = "Standard"
    return out


def _pick_type_dir_group(pick_base: pd.Series, direction: pd.Series) -> pd.Series:
    d = direction.astype(str).str.strip().str.upper()
    out = pick_base.copy()
    std = pick_base.eq("Standard")
    out.loc[std & d.eq("OVER")] = "Standard OVER"
    out.loc[std & d.eq("UNDER")] = "Standard UNDER"
    out.loc[std & ~d.isin(["OVER", "UNDER"])] = "Standard (unknown dir)"
    return out


def _normalize_def_tier(raw: pd.Series) -> pd.Series:
    s = raw.astype(str).str.strip().str.lower()
    out = pd.Series("(missing)", index=raw.index, dtype=str)
    elite = {"elite", "el", "top", "strongest"}
    above = {"above avg", "above average", "above_avg", "good", "solid", "plus"}
    avg = {"avg", "average", "neutral", "mid", "medium"}
    below = {"below avg", "below average", "below_avg", "poor"}
    weak = {"weak", "bottom", "bad"}
    out.loc[s.isin(elite)] = "Elite"
    out.loc[s.isin(above)] = "Above Avg"
    out.loc[s.isin(avg)] = "Avg"
    out.loc[s.isin(below)] = "Below Avg"
    out.loc[s.isin(weak)] = "Weak"
    return out


def _attach_features_via_build_vector(decided: pd.DataFrame) -> pd.DataFrame:
    """Adds line_bucket*, context_known, defense_known, minutes_known when stack is present."""
    if (
        build_feature_vector is None
        or augment_graded_box_raw_for_edge is None
        or graded_filename_sport_to_train_sport is None
    ):
        out = decided.copy()
        ls = _line_series(out)
        out["line_bucket"] = ls.map(line_bucket).astype(str)
        enc = {
            "micro": 0.0,
            "low": 1.0,
            "mid": 2.0,
            "high": 3.0,
            "xl": 4.0,
            "(missing)": -1.0,
        }
        out["line_bucket_encoded"] = out["line_bucket"].map(enc).astype(float)
        pick_raw = out.get("pick_type", pd.Series("", index=out.index)).astype(str).str.strip().str.upper()
        out["context_known"] = (~pick_raw.isin(["", "NAN", "(MISSING)"])).astype(float)
        def_raw = out.get("def_tier", pd.Series(np.nan, index=out.index))
        sdef = def_raw.astype(str).str.strip().str.upper()
        bad = {"", "NAN", "(MISSING)", "UNKNOWN", "NEUTRAL"}
        out["defense_known"] = (~sdef.isin(bad) & def_raw.notna()).astype(float)
        mt = out.get("minutes_tier", pd.Series("", index=out.index)).astype(str).str.strip().str.upper()
        out["minutes_known"] = mt.isin(["HIGH", "MEDIUM", "LOW"]).astype(float)
        return out

    parts: list[pd.DataFrame] = []
    for sp in sorted(decided["_sport"].dropna().unique(), key=str):
        m = decided["_sport"] == sp
        sub = decided.loc[m].copy()
        orig_sub = sub.copy()
        aug = augment_graded_box_raw_for_edge(sub)
        model_sp = graded_filename_sport_to_train_sport(str(sp))
        built = build_feature_vector(aug, model_sp)
        merged = add_stratification_columns(built, orig_sub)
        keep = ["line_bucket", *STRAT_FEATURE_COLUMNS]
        keep = [c for c in keep if c in merged.columns]
        parts.append(merged[keep])
    feat = pd.concat(parts, axis=0)
    # Graded exports may already include e.g. line_bucket; drop overlaps before join.
    overlap = [c for c in feat.columns if c in decided.columns]
    if overlap:
        decided = decided.drop(columns=overlap, errors="ignore")
    return decided.join(feat, how="left")


def _drop_invalid_goblin_under(df: pd.DataFrame) -> pd.DataFrame:
    """Goblin UNDER is not a valid PrizePicks market (Goblin is OVER-only)."""
    try:
        from utils.stack_70_eligible import exclude_invalid_market_sides_from_rating

        return exclude_invalid_market_sides_from_rating(df)
    except ImportError:
        pt = _pick_type_norm(df.get("pick_type", pd.Series("", index=df.index)))
        d = df.get("direction", pd.Series("", index=df.index)).astype(str).str.strip().str.upper()
        bad = pt.eq("goblin") & d.eq("UNDER")
        return df.loc[~bad].copy()


def _result_binary(s: pd.Series) -> pd.Series:
    u = s.astype(str).str.strip().str.upper()
    return pd.Series(np.where(u == "HIT", 1.0, np.where(u == "MISS", 0.0, np.nan)), index=s.index)


_TIER_SIGNAL_LABEL_BLOCKLIST = frozenset(
    {
        "is_hit",
        "result",
        "result_u",
        "result_binary",
        "prop",
        "prop_type",
        "prop_type_norm",
        "prop_norm",
        "pick_type",
        "pick_type_base",
        "pick_type_group",
        "direction",
        "bet_direction",
        "final_bet_direction",
        "tier",
        "tier_group",
        "def_tier",
        "def_tier_norm",
        "minutes_tier",
        "pipeline",
        "pipeline_name",
        "pipeline_group",
        "sport_disp",
        "sport",
        "_sport",
        "home_away",
        "h2h_bucket",
        "role",
        "line_bucket",
        "prop_type_label",
        "void_reason",
        "description",
        "game_start",
        "start_time",
        "fetched_at",
        "hits",
        "n",
    }
)


def _is_blocked_tier_signal_column(name: str) -> bool:
    nl = str(name).strip().lower()
    if not nl or nl in _TIER_SIGNAL_LABEL_BLOCKLIST:
        return True
    if nl.startswith("_"):
        return True
    if nl.endswith("_id") or nl.endswith("_ids"):
        return True
    if "url" in nl or "slug" in nl or "token" in nl:
        return True
    return False


def _discover_tier_numeric_columns(
    df: pd.DataFrame,
    *,
    min_nonnull_frac: float = 0.05,
    min_nunique: int = 2,
) -> list[str]:
    """
    Numeric pipeline / graded columns to blend into tier scores (row-level).
    Excludes labels, IDs, and outcome columns.
    """
    if df.empty:
        return []
    n = len(df)
    out: list[str] = []
    for c in df.columns:
        if _is_blocked_tier_signal_column(c):
            continue
        s = pd.to_numeric(df[c], errors="coerce")
        if float(s.notna().sum()) / float(n) < float(min_nonnull_frac):
            continue
        if int(s.nunique(dropna=True)) < int(min_nunique):
            continue
        out.append(str(c))
    return sorted(out)


def _minmax_norm_series_by_group(
    df: pd.DataFrame,
    value_col: str,
    group_col: str,
) -> pd.Series:
    """Per-group min–max to [0,1]; degenerate groups -> 0.5."""
    out = pd.Series(0.5, index=df.index, dtype=float)
    if value_col not in df.columns or group_col not in df.columns:
        return out
    v = pd.to_numeric(df[value_col], errors="coerce")
    for gval, idx in df.groupby(group_col, dropna=False).groups.items():
        sub = v.loc[idx]
        lo, hi = float(np.nanmin(sub.to_numpy(dtype=float))), float(np.nanmax(sub.to_numpy(dtype=float)))
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            out.loc[idx] = 0.5
        else:
            out.loc[idx] = ((sub - lo) / (hi - lo)).clip(0.0, 1.0)
    return out


def _calibration_rows(
    df: pd.DataFrame,
    *,
    min_stratum: int,
    flag_eps: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (all_bins, flagged_bins)."""
    rows: list[dict[str, object]] = []
    flagged: list[dict[str, object]] = []
    if calibration_curve is None or df.empty or "ml_prob" not in df.columns:
        return pd.DataFrame(rows), pd.DataFrame(flagged)

    need = ["result_binary", "ml_prob", "sport_disp", "direction", "pick_type_base", "prop"]
    for c in need:
        if c not in df.columns:
            return pd.DataFrame(rows), pd.DataFrame(flagged)

    sub = df[df["result_binary"].notna() & df["ml_prob"].notna()].copy()
    # Keep calibration policy slices on explicit pick types only.
    sub = sub[sub["pick_type_base"].astype(str) != "(missing)"].copy()
    grouped = sub.groupby(["sport_disp", "pick_type_base", "direction", "prop"], dropna=False)
    for (sp, pick_type, direction, prop), ddf in grouped:
        if len(ddf) < min_stratum:
            continue
        y = ddf["result_binary"].to_numpy(dtype=float)
        p = ddf["ml_prob"].to_numpy(dtype=float)
        try:
            prob_true, prob_pred = calibration_curve(y, p, n_bins=10, strategy="quantile")
        except TypeError:
            prob_true, prob_pred = calibration_curve(y, p, n_bins=10)
        for i, (t, pr) in enumerate(zip(prob_true, prob_pred)):
            gap = abs(float(pr) - float(t))
            row = {
                "sport": str(sp),
                "direction": str(direction),
                "pick_type": str(pick_type),
                "prop": str(prop),
                "bin_index": i,
                "mean_pred": float(pr),
                "mean_true": float(t),
                "gap": gap,
                "n_stratum": len(ddf),
            }
            rows.append(row)
            if gap > flag_eps:
                flagged.append({**row, "flagged": True})
    return pd.DataFrame(rows), pd.DataFrame(flagged)


def _group_min_threshold_rows(df: pd.DataFrame, min_group_n: int = 120) -> pd.DataFrame:
    """
    Recommend ml_prob minimum thresholds by sport x pipeline x pick_type x tier x direction x prop.
    This does not gate or retier rows; it is an analysis output only.
    """
    need = [
        "sport_disp",
        "pipeline_group",
        "pick_type_base",
        "tier_group",
        "direction",
        "prop",
        "ml_prob",
        "result_binary",
    ]
    for c in need:
        if c not in df.columns:
            return pd.DataFrame()
    sub = df[df["ml_prob"].notna() & df["result_binary"].notna()].copy()
    # Do not recommend policy thresholds for slices with unknown pick_type.
    sub = sub[sub["pick_type_base"].astype(str) != "(missing)"].copy()
    def _threshold_floor(sport: str, pick_type: str, direction: str) -> float:
        sp = str(sport or "").strip().upper()
        pt = str(pick_type or "").strip().upper()
        dr = str(direction or "").strip().upper()
        # Demon OVER in low-scoring sports can produce unrealistically tiny floors from noisy bins.
        if dr == "OVER" and pt == "DEMON" and sp in {"SOCCER", "NHL"}:
            return 0.10
        return 0.0

    out_rows: list[dict[str, object]] = []
    for (sp, pipe, pt, tr, d, prop), g in sub.groupby(
        ["sport_disp", "pipeline_group", "pick_type_base", "tier_group", "direction", "prop"],
        dropna=False,
    ):
        n = int(len(g))
        if n < int(min_group_n):
            continue
        base_hr = float(g["result_binary"].mean())
        qvals = np.quantile(g["ml_prob"].to_numpy(dtype=float), [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7])
        floor = _threshold_floor(sp, pt, d)
        qvals = [max(float(x), floor) for x in qvals]
        best_thr = None
        best_hr = base_hr
        best_n = n
        min_keep = max(25, int(round(0.15 * n)))
        for thr in sorted(set(float(x) for x in qvals)):
            kept = g[g["ml_prob"] >= thr]
            nk = int(len(kept))
            if nk < min_keep:
                continue
            hr = float(kept["result_binary"].mean())
            if hr > best_hr + 1e-12 or (abs(hr - best_hr) <= 1e-12 and nk > best_n):
                best_thr = thr
                best_hr = hr
                best_n = nk
        out_rows.append(
            {
                "sport": str(sp),
                "pipeline": str(pipe),
                "pick_type": str(pt),
                "tier": str(tr),
                "direction": str(d),
                "prop": str(prop),
                "n_total": n,
                "base_hit_rate": base_hr,
                "recommended_min_ml_prob": (max(float(best_thr), floor) if best_thr is not None else None),
                "recommended_n": best_n,
                "recommended_hit_rate": best_hr,
                "lift_vs_base": (best_hr - base_hr) if best_thr is not None else 0.0,
            }
        )
    if not out_rows:
        return pd.DataFrame()
    out = pd.DataFrame(out_rows)
    return out.sort_values(["lift_vs_base", "n_total"], ascending=[False, False]).reset_index(drop=True)


_TIER_ORDER: dict[str, int] = {"D": 0, "C": 1, "B": 2, "A": 3}
_TIER_BY_SCORE: dict[int, str] = {v: k for k, v in _TIER_ORDER.items()}
_N_GATES: tuple[int, int, int] = (600, 350, 200)
_LIFT_GATES: tuple[float, float, float] = (0.03, 0.02, 0.00)
# Direction-separated Standard profiles (requested): OVER vs UNDER are evaluated independently.
_HIT_GATES_GOBLIN: tuple[float, float, float] = (0.72, 0.68, 0.62)
_HIT_GATES_STANDARD_OVER: tuple[float, float, float] = (0.73, 0.69, 0.63)
_HIT_GATES_STANDARD_UNDER: tuple[float, float, float] = (0.71, 0.67, 0.61)


def _tier_from_gates(
    recommended_n: float,
    recommended_hit_rate: float,
    lift_vs_base: float,
    *,
    hit_gates: tuple[float, float, float] = _HIT_GATES_GOBLIN,
) -> str:
    """Return best tier achieved under configured gates."""
    n = float(pd.to_numeric(recommended_n, errors="coerce") or 0.0)
    hr = float(pd.to_numeric(recommended_hit_rate, errors="coerce") or 0.0)
    lift = float(pd.to_numeric(lift_vs_base, errors="coerce") or 0.0)
    if n >= _N_GATES[0] and hr >= hit_gates[0] and lift >= _LIFT_GATES[0]:
        return "A"
    if n >= _N_GATES[1] and hr >= hit_gates[1] and lift >= _LIFT_GATES[1]:
        return "B"
    if n >= _N_GATES[2] and hr >= hit_gates[2] and lift >= _LIFT_GATES[2]:
        return "C"
    return "D"


def _apply_tier_gate_recommendations(threshold_rows: pd.DataFrame) -> pd.DataFrame:
    """
    Add actionable gate recommendations per group:
    - Goblin/Standard: one-step promote/demote suggestions from A/B/C/D gates
    - Demon: keep tier unchanged; floor-only suggestion (apply/watch/hold)
    """
    if threshold_rows.empty:
        return threshold_rows
    out = threshold_rows.copy()
    for c in ("n_total", "recommended_n", "recommended_hit_rate", "lift_vs_base"):
        out[c] = pd.to_numeric(out.get(c), errors="coerce")
    out["tier"] = out.get("tier", pd.Series("", index=out.index)).astype(str).str.upper().str.strip()
    out["pick_type"] = out.get("pick_type", pd.Series("", index=out.index)).astype(str).str.upper().str.strip()
    out["policy_status"] = out.get("policy_status", pd.Series("", index=out.index)).astype(str).str.upper().str.strip()
    out["gate_tier_target"] = "D"
    out["gate_action"] = "HOLD"
    out["gate_tier_proposed"] = out["tier"]
    out["gate_profile"] = ""
    out["gate_notes"] = ""
    out["demon_floor_action"] = ""

    is_demon = out["pick_type"].eq("DEMON")
    is_standard = out["pick_type"].eq("STANDARD")
    is_ok = out["policy_status"].eq("OK")

    # Goblin / Standard gate targets.
    for idx, r in out.loc[~is_demon].iterrows():
        pt = str(r.get("pick_type", "")).upper().strip()
        dr = str(r.get("direction", "")).upper().strip()
        if pt == "STANDARD":
            if dr == "UNDER":
                hit_gates = _HIT_GATES_STANDARD_UNDER
                gate_profile = "STANDARD_UNDER"
            else:
                hit_gates = _HIT_GATES_STANDARD_OVER
                gate_profile = "STANDARD_OVER"
        else:
            hit_gates = _HIT_GATES_GOBLIN
            gate_profile = "GOBLIN"
        target = _tier_from_gates(
            r.get("recommended_n"),
            r.get("recommended_hit_rate"),
            r.get("lift_vs_base"),
            hit_gates=hit_gates,
        )
        cur = str(r.get("tier", "D")).upper().strip()
        cur_s = _TIER_ORDER.get(cur, 0)
        tgt_s = _TIER_ORDER.get(target, 0)
        out.at[idx, "gate_tier_target"] = target
        out.at[idx, "gate_profile"] = gate_profile
        if not str(r.get("policy_status", "")).upper().strip() == "OK":
            out.at[idx, "gate_action"] = "HOLD"
            out.at[idx, "gate_tier_proposed"] = cur
            out.at[idx, "gate_notes"] = "policy_status_not_ok"
            continue
        if tgt_s > cur_s:
            # One-step promotion per cycle.
            prop_s = min(cur_s + 1, tgt_s)
            out.at[idx, "gate_action"] = "PROMOTE_1"
            out.at[idx, "gate_tier_proposed"] = _TIER_BY_SCORE[prop_s]
            out.at[idx, "gate_notes"] = "meets_higher_tier_gates"
        elif tgt_s < cur_s:
            # One-step demotion per cycle.
            prop_s = max(cur_s - 1, tgt_s)
            out.at[idx, "gate_action"] = "DEMOTE_1"
            out.at[idx, "gate_tier_proposed"] = _TIER_BY_SCORE[prop_s]
            out.at[idx, "gate_notes"] = "below_current_tier_gates"
        else:
            out.at[idx, "gate_action"] = "HOLD"
            out.at[idx, "gate_tier_proposed"] = cur
            out.at[idx, "gate_notes"] = "at_target"

    # Demon: keep tier unchanged, floor-only recommendation buckets.
    for idx, r in out.loc[is_demon].iterrows():
        cur = str(r.get("tier", "D")).upper().strip()
        out.at[idx, "gate_tier_target"] = cur if cur in _TIER_ORDER else "D"
        out.at[idx, "gate_tier_proposed"] = cur if cur in _TIER_ORDER else "D"
        out.at[idx, "gate_action"] = "DEMON_KEEP"
        out.at[idx, "gate_profile"] = "DEMON_KEEP"
        if str(r.get("policy_status", "")).upper().strip() != "OK":
            out.at[idx, "demon_floor_action"] = "HOLDOUT"
            out.at[idx, "gate_notes"] = "demon_no_retier_holdout"
            continue
        rn = float(pd.to_numeric(r.get("recommended_n"), errors="coerce") or 0.0)
        floor = pd.to_numeric(r.get("recommended_min_ml_prob"), errors="coerce")
        if rn >= 500 and pd.notna(floor):
            out.at[idx, "demon_floor_action"] = "APPLY_FLOOR"
            out.at[idx, "gate_notes"] = "demon_floor_confident"
        elif rn >= 300:
            out.at[idx, "demon_floor_action"] = "WATCHLIST"
            out.at[idx, "gate_notes"] = "demon_floor_watchlist"
        else:
            out.at[idx, "demon_floor_action"] = "HOLD"
            out.at[idx, "gate_notes"] = "demon_floor_thin_sample"

    return out


def _segment_tiering_scores(
    decided: pd.DataFrame,
    threshold_rows: pd.DataFrame,
    cal_all: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build independent tiering scorecards for:
    Goblin, Demon, Standard OVER, Standard UNDER.
    """
    if decided.empty:
        return pd.DataFrame()

    base = decided.copy()
    base["pick_type_group"] = _pick_type_dir_group(
        _pick_type_base(base.get("pick_type", pd.Series("", index=base.index))),
        base.get("direction", pd.Series("", index=base.index)),
    )
    keep_segments = {"Goblin", "Demon", "Standard OVER", "Standard UNDER"}
    base = base[base["pick_type_group"].isin(keep_segments)].copy()
    if base.empty:
        return pd.DataFrame()

    agg_map: dict[str, tuple[str, str]] = {
        "n_total": ("is_hit", "size"),
        "hit_rate": ("is_hit", "mean"),
        "mean_ml_prob": ("ml_prob", "mean"),
    }
    # Blend every discoverable numeric stat (row-level) into segment scoring.
    extra_sig = [
        c
        for c in _discover_tier_numeric_columns(base)
        if c != "ml_prob" and c in base.columns
    ]
    for c in extra_sig:
        agg_map[f"sig_mean__{c}"] = (c, "mean")
    seg = base.groupby(["sport_disp", "pick_type_group"], dropna=False).agg(**agg_map).reset_index()

    # Aggregate calibration gap by market segment.
    cal_seg = pd.DataFrame(columns=["sport_disp", "pick_type_group", "avg_cal_gap"])
    if not cal_all.empty:
        c = cal_all.copy()
        c["sport_disp"] = c.get("sport", "").astype(str)
        c["pick_type_group"] = c.get("pick_type", "").astype(str).str.upper().str.strip()
        c["direction"] = c.get("direction", "").astype(str).str.upper().str.strip()
        c["pick_type_group"] = np.where(
            c["pick_type_group"].eq("STANDARD"),
            "Standard " + c["direction"].replace({"": "(unknown dir)"}),
            c["pick_type_group"].str.title(),
        )
        c = c[c["pick_type_group"].isin(keep_segments)].copy()
        if not c.empty:
            cal_seg = (
                c.groupby(["sport_disp", "pick_type_group"], dropna=False)["gap"]
                .mean()
                .reset_index()
                .rename(columns={"gap": "avg_cal_gap"})
            )

    # Aggregate threshold-policy signal by market segment.
    pol_seg = pd.DataFrame(columns=["sport_disp", "pick_type_group", "avg_lift", "ok_rate"])
    if not threshold_rows.empty:
        t = threshold_rows.copy()
        t["sport_disp"] = t.get("sport", "").astype(str)
        t["pick_type"] = t.get("pick_type", "").astype(str).str.upper().str.strip()
        t["direction"] = t.get("direction", "").astype(str).str.upper().str.strip()
        t["pick_type_group"] = np.where(
            t["pick_type"].eq("STANDARD"),
            "Standard " + t["direction"].replace({"": "(unknown dir)"}),
            t["pick_type"].str.title(),
        )
        t = t[t["pick_type_group"].isin(keep_segments)].copy()
        if not t.empty:
            t["ok_flag"] = t.get("policy_status", "").astype(str).str.upper().eq("OK").astype(float)
            t["n_total"] = pd.to_numeric(t.get("n_total"), errors="coerce").fillna(0.0)
            t["lift_vs_base"] = pd.to_numeric(t.get("lift_vs_base"), errors="coerce").fillna(0.0)
            grouped = []
            for (sp, pg), d in t.groupby(["sport_disp", "pick_type_group"], dropna=False):
                w = d["n_total"].to_numpy(dtype=float)
                wt = float(np.nansum(w))
                if wt <= 0:
                    avg_lift = float(d["lift_vs_base"].mean()) if len(d) else 0.0
                    ok_rate = float(d["ok_flag"].mean()) if len(d) else 0.0
                else:
                    avg_lift = float(np.nansum(d["lift_vs_base"].to_numpy(dtype=float) * w) / wt)
                    ok_rate = float(np.nansum(d["ok_flag"].to_numpy(dtype=float) * w) / wt)
                grouped.append(
                    {
                        "sport_disp": sp,
                        "pick_type_group": pg,
                        "avg_lift": avg_lift,
                        "ok_rate": ok_rate,
                    }
                )
            pol_seg = pd.DataFrame(grouped)

    out = seg.merge(cal_seg, on=["sport_disp", "pick_type_group"], how="left").merge(
        pol_seg, on=["sport_disp", "pick_type_group"], how="left"
    )
    out["avg_cal_gap"] = pd.to_numeric(out.get("avg_cal_gap"), errors="coerce").fillna(0.0)
    out["avg_lift"] = pd.to_numeric(out.get("avg_lift"), errors="coerce").fillna(0.0)
    out["ok_rate"] = pd.to_numeric(out.get("ok_rate"), errors="coerce").fillna(0.0)

    sig_cols = [c for c in out.columns if str(c).startswith("sig_mean__")]
    out["stat_signal_composite"] = 0.0
    if sig_cols:
        norm_sum = pd.Series(0.0, index=out.index, dtype=float)
        norm_cnt = pd.Series(0.0, index=out.index, dtype=float)
        tmp_sig = out[["pick_type_group"] + sig_cols].copy()
        for c in sig_cols:
            nn = _minmax_norm_series_by_group(tmp_sig, c, "pick_type_group")
            norm_sum = norm_sum + nn.fillna(0.5)
            norm_cnt = norm_cnt + 1.0
        out["stat_signal_composite"] = np.where(norm_cnt > 0, norm_sum / norm_cnt, 0.0)
    out["segment_signal_n_features"] = int(len(sig_cols))

    def rate_row(r: pd.Series) -> str:
        seg_name = str(r.get("pick_type_group", ""))
        n = float(r.get("n_total", 0.0))
        hr = float(r.get("hit_rate", 0.0))
        gap = float(r.get("avg_cal_gap", 0.0))
        lift = float(r.get("avg_lift", 0.0))
        ok_rate = float(r.get("ok_rate", 0.0))

        if seg_name == "Demon":
            # Demon is intentionally hard to hit; judge more by lift and policy consistency.
            if n >= 600 and lift >= 0.015 and gap <= 0.24 and ok_rate >= 0.70:
                return "A"
            if n >= 350 and lift >= 0.008 and gap <= 0.30 and ok_rate >= 0.60:
                return "B"
            if n >= 150 and lift >= 0.000 and gap <= 0.36 and ok_rate >= 0.45:
                return "C"
            return "D"

        if seg_name == "Goblin":
            # Goblin should keep a high floor; calibration matters but is secondary to hit-rate floor.
            if n >= 600 and hr >= 0.62 and gap <= 0.24 and ok_rate >= 0.70:
                return "A"
            if n >= 350 and hr >= 0.57 and gap <= 0.30 and ok_rate >= 0.60:
                return "B"
            if n >= 150 and hr >= 0.52 and gap <= 0.36 and ok_rate >= 0.45:
                return "C"
            return "D"

        # Standard OVER / Standard UNDER: target stable, calibrated near coin-flip.
        dist = abs(hr - 0.50)
        if n >= 600 and dist <= 0.08 and gap <= 0.24 and ok_rate >= 0.70:
            return "A"
        if n >= 350 and dist <= 0.12 and gap <= 0.30 and ok_rate >= 0.60:
            return "B"
        if n >= 150 and dist <= 0.16 and gap <= 0.36 and ok_rate >= 0.45:
            return "C"
        return "D"

    out["segment_rating"] = out.apply(rate_row, axis=1)

    # Ensure every pick-type segment uses a full A/B/C/D ladder by adding
    # a relative tier computed within each segment across sports.
    # This keeps the absolute rating above, but guarantees an explicit ABCD
    # stratification view even when absolute gates cluster in one bucket.
    out["segment_score"] = 0.0
    comp_series = pd.to_numeric(out["stat_signal_composite"], errors="coerce").fillna(0.0)
    for idx, r in out.iterrows():
        seg_name = str(r.get("pick_type_group", ""))
        hr = float(r.get("hit_rate", 0.0))
        gap = float(r.get("avg_cal_gap", 0.0))
        lift = float(r.get("avg_lift", 0.0))
        ok_rate = float(r.get("ok_rate", 0.0))
        n = float(r.get("n_total", 0.0))
        n_factor = min(1.0, n / 5000.0)
        stat_c = float(comp_series.loc[idx])
        if seg_name == "Demon":
            # Demon: prioritize lift + policy stability, mildly penalize calibration gap.
            score = (1.8 * lift) + (0.9 * ok_rate) - (0.45 * gap) + (0.2 * n_factor)
        elif seg_name == "Goblin":
            # Goblin: prioritize sustained hit floor + policy stability.
            score = (1.3 * hr) + (0.8 * ok_rate) + (0.35 * lift) - (0.35 * gap) + (0.15 * n_factor)
        else:
            # Standard OVER/UNDER: prioritize calibrated near-coinflip behavior.
            dist = abs(hr - 0.50)
            score = (1.0 - dist) + (0.8 * ok_rate) + (0.35 * lift) - (0.45 * gap) + (0.15 * n_factor)
        # Mean of min–max-normalized discoverable numeric stats (full graded/pipeline surface).
        score += 0.40 * stat_c
        out.at[idx, "segment_score"] = float(score)

    def _abcd_by_rank(group: pd.DataFrame) -> pd.Series:
        g = group.copy()
        n = len(g)
        if n <= 1:
            return pd.Series(["A"] * n, index=g.index)
        if n == 2:
            labs = ["A", "C"]
        elif n == 3:
            labs = ["A", "B", "D"]
        else:
            labs = ["A", "B", "C", "D"]
        # Higher score => better tier
        order = g["segment_score"].rank(method="first", ascending=False)
        q = pd.qcut(order, q=len(labs), labels=labs, duplicates="drop")
        return pd.Series(q.astype(str).values, index=g.index)

    out["segment_tier_abcd"] = (
        out.groupby("pick_type_group", group_keys=False).apply(_abcd_by_rank)
    )
    out = out.sort_values(["sport_disp", "pick_type_group"]).reset_index(drop=True)
    return out


def _prop_type_tiers(decided: pd.DataFrame, min_group_n: int = 30) -> pd.DataFrame:
    """
    Tier prop types using every discoverable numeric graded/pipeline column:
    per-group means, min–max normalized within pick_type_group, then tier_score
    is the unweighted mean of those normalized signals (plus legacy diagnostics).
    """
    if decided.empty:
        return pd.DataFrame()

    d = decided.copy()
    d["sport_disp"] = d.get("sport_disp", d.get("_sport", "")).astype(str)
    if "prop" in d.columns:
        d["prop_type_label"] = d["prop"].astype(str).str.strip()
    elif "prop_type" in d.columns:
        d["prop_type_label"] = d["prop_type"].astype(str).str.strip()
    else:
        d["prop_type_label"] = "(missing)"
    d["pick_type_group"] = _pick_type_dir_group(
        _pick_type_base(d.get("pick_type", pd.Series("", index=d.index))),
        d.get("direction", pd.Series("", index=d.index)),
    )
    keep_segments = {"Goblin", "Demon", "Standard OVER", "Standard UNDER"}
    d = d[d["pick_type_group"].isin(keep_segments)].copy()
    if d.empty:
        return pd.DataFrame()

    d["is_hit"] = pd.to_numeric(d.get("is_hit"), errors="coerce")
    d["edge"] = pd.to_numeric(d.get("edge"), errors="coerce")
    d["edge_abs"] = d["edge"].abs()

    # Try to recover date ordering for L5 from common date columns or file names.
    if "_date" in d.columns:
        d["_tier_date"] = pd.to_datetime(d["_date"], errors="coerce")
    elif "date" in d.columns:
        d["_tier_date"] = pd.to_datetime(d["date"], errors="coerce")
    else:
        d["_tier_date"] = pd.NaT
        if "_file" in d.columns:
            ds = (
                d["_file"]
                .astype(str)
                .str.extract(r"(\d{4}-\d{2}-\d{2})", expand=False)
            )
            d["_tier_date"] = pd.to_datetime(ds, errors="coerce")

    feat_cols = _discover_tier_numeric_columns(d)
    key_cols = ["sport_disp", "prop_type_label", "pick_type_group"]
    rows: list[dict[str, object]] = []
    for keys, g in d.groupby(key_cols, dropna=False):
        g2 = g[g["is_hit"].notna()].copy()
        n = int(len(g2))
        if n < int(min_group_n):
            continue
        hit_rate = float(g2["is_hit"].mean())
        edge_mean = float(g2["edge_abs"].mean()) if g2["edge_abs"].notna().any() else 0.0
        std = float(g2["is_hit"].std(ddof=0)) if n > 1 else 0.0
        consistency = float(max(0.0, min(1.0, 1.0 - (2.0 * std))))

        g3 = g2.sort_values("_tier_date", na_position="last")
        l5 = g3["is_hit"].tail(5)
        l5_hit_rate = float(l5.mean()) if len(l5) else hit_rate

        row: dict[str, object] = {
            "sport_disp": keys[0],
            "prop_type": keys[1],
            "pick_type_group": keys[2],
            "n_total": n,
            "hit_rate": hit_rate,
            "l5_hit_rate": l5_hit_rate,
            "edge_abs_mean": edge_mean,
            "consistency": consistency,
        }
        for c in feat_cols:
            if c not in g2.columns:
                continue
            row[f"_m_{c}"] = float(pd.to_numeric(g2[c], errors="coerce").mean())
        rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    norm_names: list[str] = []
    for c in feat_cols:
        mc = f"_m_{c}"
        if mc not in out.columns:
            continue
        nn = _minmax_norm_series_by_group(out, mc, "pick_type_group")
        ncol = f"_nm_{c}"
        out[ncol] = nn
        norm_names.append(ncol)

    if norm_names:
        out["tier_score"] = out[norm_names].mean(axis=1, skipna=True)
        out["prop_tier_signal_n_features"] = len(norm_names)
    else:
        # Fallback: legacy four-metric blend when no numeric pipeline columns survive discovery.
        norm_cols = ["hit_rate", "l5_hit_rate", "edge_abs_mean", "consistency"]
        for col in norm_cols:
            out[f"{col}_norm"] = 0.0
        for _, g in out.groupby("pick_type_group", dropna=False):
            idx = g.index
            for col in norm_cols:
                v = pd.to_numeric(g[col], errors="coerce")
                lo, hi = float(v.min()), float(v.max())
                if np.isfinite(lo) and np.isfinite(hi) and hi > lo:
                    out.loc[idx, f"{col}_norm"] = ((v - lo) / (hi - lo)).astype(float)
                else:
                    out.loc[idx, f"{col}_norm"] = 0.5
        out["tier_score"] = (
            (0.35 * out["hit_rate_norm"])
            + (0.30 * out["l5_hit_rate_norm"])
            + (0.20 * out["edge_abs_mean_norm"])
            + (0.15 * out["consistency_norm"])
        )
        out["prop_tier_signal_n_features"] = 0

    drop_internal = [c for c in out.columns if c.startswith("_m_") or c.startswith("_nm_")]
    out = out.drop(columns=drop_internal, errors="ignore")

    def _abcd_rank(group: pd.DataFrame) -> pd.Series:
        g = group.copy()
        n = len(g)
        if n <= 1:
            return pd.Series(["A"] * n, index=g.index)
        if n == 2:
            labs = ["A", "C"]
        elif n == 3:
            labs = ["A", "B", "D"]
        else:
            labs = ["A", "B", "C", "D"]
        order = g["tier_score"].rank(method="first", ascending=False)
        q = pd.qcut(order, q=len(labs), labels=labs, duplicates="drop")
        return pd.Series(q.astype(str).values, index=g.index)

    out["tier_abcd"] = out.groupby("pick_type_group", group_keys=False).apply(_abcd_rank)
    out = out.sort_values(["pick_type_group", "tier_score"], ascending=[True, False]).reset_index(drop=True)
    return out


def _html_escape_df(tab: pd.DataFrame, max_rows: int = 50) -> str:
    if tab.empty:
        return "<p>(no rows)</p>"
    t = tab.head(max_rows).copy()
    th = "".join(f"<th>{html.escape(str(c))}</th>" for c in t.columns)
    body_rows: list[str] = []
    for _, r in t.iterrows():
        cells = "".join(f"<td>{html.escape(str(v))}</td>" for v in r)
        body_rows.append(f"<tr>{cells}</tr>")
    return (
        "<table class='grid'><thead><tr>"
        + th
        + "</tr></thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table>"
    )


def run(
    roots: list[Path],
    out_dir: Path,
    *,
    min_cell_n: int,
    min_cal_stratum: int,
    cal_flag_eps: float,
    min_threshold_group_n: int,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    if load_unified is None or normalize_decided is None:
        raise SystemExit(
            "Missing analyze_graded_prop_winners.py on PYTHONPATH; "
            "place it next to this script under scripts/."
        )

    raw = load_unified(roots)
    decided = normalize_decided(raw)
    goblin_under_n = 0
    if "pick_type" in decided.columns and "direction" in decided.columns:
        try:
            from utils.stack_70_eligible import is_invalid_market_side

            goblin_under_n = int(
                decided.apply(
                    lambda r: is_invalid_market_side(r["pick_type"], r["direction"]),
                    axis=1,
                ).sum()
            )
        except ImportError:
            goblin_under_n = 0
    demon_n = (
        int(is_demon_pick_type(decided["pick_type"]).sum())
        if is_demon_pick_type is not None and "pick_type" in decided.columns
        else 0
    )
    decided = exclude_non_rating_legs(decided) if exclude_non_rating_legs is not None else _drop_invalid_goblin_under(decided)
    if decided.empty:
        print("No decided graded rows after filters.")
        return
    if goblin_under_n:
        print(f"Excluded {goblin_under_n:,} Goblin UNDER rows (not a valid market).")
    if demon_n:
        print(f"Excluded {demon_n:,} Demon rows from hit-rate rating (data collection only).")

    decided["sport_disp"] = decided["_sport"].map(_sport_display)
    if "pipeline" in decided.columns:
        decided["pipeline_group"] = decided["pipeline"].astype(str).str.strip().replace("", "(missing)")
    elif "pipeline_name" in decided.columns:
        decided["pipeline_group"] = decided["pipeline_name"].astype(str).str.strip().replace("", "(missing)")
    else:
        # Fallback: pipeline label aligns with sport export when no explicit pipeline column exists.
        decided["pipeline_group"] = decided["sport_disp"].astype(str).str.strip()
    decided["pick_type_base"] = _pick_type_base(decided.get("pick_type", pd.Series("", index=decided.index)))
    decided["tier_group"] = (
        decided.get("tier", pd.Series("", index=decided.index))
        .astype(str)
        .str.strip()
        .replace({"": "(missing)", "nan": "(missing)", "None": "(missing)", "<NA>": "(missing)"})
    )
    decided["pick_type_group"] = _pick_type_dir_group(
        decided["pick_type_base"],
        decided.get("direction", pd.Series("", index=decided.index)),
    )
    decided["def_tier_norm"] = _normalize_def_tier(decided.get("def_tier", pd.Series("", index=decided.index)))
    decided["home_away"] = _home_away_series(decided)
    decided = _attach_features_via_build_vector(decided)

    if "result" in decided.columns:
        decided["result_binary"] = _result_binary(decided["result"])
    else:
        decided["result_binary"] = decided.get("is_hit", np.nan)

    gcols = [
        "sport_disp",
        "pipeline_group",
        "tier_group",
        "prop",
        "direction",
        "pick_type_group",
        "def_tier_norm",
        "line_bucket",
    ]
    gcols = [c for c in gcols if c in decided.columns]
    hit_tbl = (
        decided.groupby(gcols, dropna=False)
        .agg(
            n=("is_hit", "size"),
            hits=("is_hit", "sum"),
            mean_ml=("ml_prob", "mean"),
            context_known_rate=("context_known", "mean"),
            defense_known_rate=("defense_known", "mean"),
            minutes_known_rate=("minutes_known", "mean"),
        )
        .reset_index()
    )
    hit_tbl["hit_rate"] = hit_tbl["hits"] / hit_tbl["n"]
    hit_tbl = hit_tbl[hit_tbl["n"] >= min_cell_n].sort_values(
        ["sport_disp", "n"], ascending=[True, False]
    )

    path_main = out_dir / "graded_strat_hit_rates.csv"
    hit_tbl.to_csv(path_main, index=False)

    cal_all, cal_flag = _calibration_rows(
        decided,
        min_stratum=min_cal_stratum,
        flag_eps=cal_flag_eps,
    )
    cal_all.to_csv(out_dir / "graded_calibration_bins.csv", index=False)
    cal_flag.to_csv(out_dir / "graded_calibration_flagged.csv", index=False)
    threshold_rows = _group_min_threshold_rows(decided, min_group_n=min_threshold_group_n)
    if not threshold_rows.empty and not cal_all.empty:
        cal_gap = (
            cal_all.groupby(["sport", "pick_type", "direction", "prop"], dropna=False)["gap"]
            .mean()
            .reset_index()
            .rename(columns={"gap": "avg_calibration_gap"})
        )
        # Replicate calibration gap across pipeline/tier slices of the same core group.
        tkeys = ["sport", "pipeline", "pick_type", "tier", "direction", "prop"]
        if not threshold_rows.empty:
            base_keys = threshold_rows[tkeys].drop_duplicates()
            cal_gap = base_keys.merge(cal_gap, on=["sport", "pick_type", "direction", "prop"], how="left")
        threshold_rows = threshold_rows.merge(
            cal_gap,
            how="left",
            on=tkeys,
        )
        gap = pd.to_numeric(threshold_rows.get("avg_calibration_gap"), errors="coerce").fillna(0.0)
        sp = threshold_rows["sport"].astype(str).str.upper().str.strip()
        pt = threshold_rows["pick_type"].astype(str).str.upper().str.strip()
        dr = threshold_rows["direction"].astype(str).str.upper().str.strip()
        prop = threshold_rows["prop"].astype(str).str.upper().str.strip()
        blocked_known = (
            ((sp == "NBA1Q") & (pt == "DEMON") & (dr == "OVER") & prop.isin({"POINTS", "REBOUNDS", "ASSISTS"}))
            | ((sp == "NHL") & (pt == "DEMON") & (dr == "OVER") & (prop == "POWER PLAY POINTS"))
            | (
                (sp == "SOCCER")
                & (pt == "DEMON")
                & (dr == "OVER")
                & prop.isin({"SHOTS ON TARGET", "TACKLES"})
            )
            | (
                (sp == "NBA")
                & (pt == "DEMON")
                & (dr == "OVER")
                & prop.isin(
                    {
                        "DOUBLE-DOUBLE",
                        "PRA",
                        "PTS+REBS+ASTS",
                        "POINTS",
                        "REBS+ASTS",
                        "PTS+REBS",
                        "PTS+ASTS",
                        "FANTASY SCORE",
                    }
                )
            )
            | (
                (sp == "SOCCER")
                & (pt == "(MISSING)")
                & (dr == "OVER")
                & prop.isin({"GOALS", "GOAL + ASSIST", "ASSISTS"})
            )
        )
        # Keep a high-gap fail-safe, but avoid globally suppressing most groups.
        blocked_gap = gap >= 0.45
        blocked = blocked_known | blocked_gap
        threshold_rows["policy_status"] = np.where(blocked, "HOLDOUT_RECALIBRATE", "OK")
        threshold_rows.loc[blocked, "recommended_min_ml_prob"] = np.nan
        threshold_rows.loc[blocked, "recommended_n"] = threshold_rows.loc[blocked, "n_total"]
        threshold_rows.loc[blocked, "recommended_hit_rate"] = threshold_rows.loc[blocked, "base_hit_rate"]
        threshold_rows.loc[blocked, "lift_vs_base"] = 0.0
    threshold_rows = _apply_tier_gate_recommendations(threshold_rows)
    threshold_rows.to_csv(out_dir / "graded_group_min_thresholds.csv", index=False)
    segment_tiers = _segment_tiering_scores(decided, threshold_rows, cal_all)
    segment_tiers.to_csv(out_dir / "graded_segment_tiering_scores.csv", index=False)
    prop_type_tiers = _prop_type_tiers(decided, min_group_n=max(15, min_cell_n))
    prop_type_tiers.to_csv(out_dir / "graded_prop_type_tiers.csv", index=False)

    tier_a = hit_tbl[
        (hit_tbl["context_known_rate"] >= 0.99)
        & (hit_tbl["defense_known_rate"] >= 0.99)
        & (hit_tbl["minutes_known_rate"] >= 0.99)
    ].copy()
    tier_a.to_csv(out_dir / "graded_strat_tier_a_slice.csv", index=False)

    ha_vc = (
        decided.groupby(["sport_disp", "home_away"], dropna=False)
        .size()
        .reset_index(name="n")
        .sort_values(["sport_disp", "n"], ascending=[True, False])
    )
    ha_vc.to_csv(out_dir / "graded_home_away_by_sport.csv", index=False)

    gen_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ha_note = (
        "home_away from graded columns when present (home_away, is_home, venue_side, …); "
        "otherwise tagged (not in graded export)."
    )
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Graded stratification report</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 1.5rem; color: #111; }}
h1,h2 {{ font-weight: 600; }}
.grid {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; }}
.grid th, .grid td {{ border: 1px solid #ccc; padding: 0.35rem 0.5rem; text-align: left; }}
.grid th {{ background: #f4f4f4; }}
.note {{ color: #444; font-size: 0.9rem; max-width: 52rem; }}
</style>
</head>
<body>
<h1>Graded stratification report</h1>
<p class="note">Generated {html.escape(gen_at)}. Tier A requires context_known, defense_known, and minutes_known near 1.0 on the slice (unknown-context props excluded).</p>
<p class="note">Demon pick types excluded from hit-rate rating (data collection only). Goblin UNDER removed (Goblin is OVER-only on PrizePicks). {html.escape(ha_note)}</p>

<h2>Primary stratification (min n = {min_cell_n})</h2>
{_html_escape_df(hit_tbl)}

<h2>Calibration bins flagged (|pred - true| &gt; {cal_flag_eps})</h2>
{_html_escape_df(cal_flag)}

<h2>Recommended group minimum ml_prob (analysis only)</h2>
<p class="note">Global tiers are unchanged; this is a per-group recommendation table by sport × pipeline × pick_type × tier × direction × prop.</p>
{_html_escape_df(threshold_rows)}

<h2>Independent market segment ratings</h2>
<p class="note">Each sport is scored independently for Goblin, Standard OVER, and Standard UNDER (Demon excluded from rating).</p>
{_html_escape_df(segment_tiers)}

<h2>Prop-type tiering by requested metrics</h2>
<p class="note">Tiered by prop_type × hit_rate × L5 directional hit_rate × edge × consistency, independently within each pick-type segment.</p>
{_html_escape_df(prop_type_tiers)}

<h2>Tier A (all three known-rates ≥ 0.99)</h2>
{_html_escape_df(tier_a, max_rows=40)}

<h2>Tier 2 (blocked)</h2>
<p class="note">Ticket leg structure (n_legs, prob_std, all_over, dominant_sport) is deferred until graded ticket set exceeds 500 rows.</p>
</body>
</html>
"""
    (out_dir / "graded_stratification_report.html").write_text(html_doc, encoding="utf-8")

    print(f"Wrote {path_main} rows={len(hit_tbl)}")
    print(f"Wrote calibration: {out_dir / 'graded_calibration_bins.csv'} flagged={len(cal_flag)}")
    print(f"Wrote threshold recommendations: {out_dir / 'graded_group_min_thresholds.csv'} rows={len(threshold_rows)}")
    print(f"Wrote segment ratings: {out_dir / 'graded_segment_tiering_scores.csv'} rows={len(segment_tiers)}")
    print(f"Wrote prop-type tiers: {out_dir / 'graded_prop_type_tiers.csv'} rows={len(prop_type_tiers)}")
    print(f"Wrote {out_dir / 'graded_stratification_report.html'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: <repo>/data/reports/graded_stratification)",
    )
    ap.add_argument(
        "--roots",
        type=Path,
        nargs="*",
        default=None,
        help="Extra roots to scan for graded_*.xlsx (defaults: ui_runner/graded_slate, outputs)",
    )
    ap.add_argument("--min-cell-n", type=int, default=15, help="Min rows per stratification cell")
    ap.add_argument("--min-cal-stratum", type=int, default=100, help="Min rows for calibration_curve slice")
    ap.add_argument("--cal-flag-eps", type=float, default=0.08, help="Flag calibration bin if gap exceeds this")
    ap.add_argument(
        "--min-threshold-group-n",
        type=int,
        default=120,
        help="Min rows for group-specific threshold recommendation rows.",
    )
    args = ap.parse_args()
    root = _repo_root()
    roots = list(args.roots) if args.roots else []
    for rel in (root / "ui_runner" / "graded_slate", root / "outputs"):
        if rel not in roots:
            roots.append(rel)
    out_dir = args.out_dir or (root / "data" / "reports" / "graded_stratification")
    run(
        roots,
        out_dir,
        min_cell_n=args.min_cell_n,
        min_cal_stratum=args.min_cal_stratum,
        cal_flag_eps=args.cal_flag_eps,
        min_threshold_group_n=args.min_threshold_group_n,
    )


if __name__ == "__main__":
    main()
