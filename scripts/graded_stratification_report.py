#!/usr/bin/env python3
"""
Weekly / post-retrain: walk graded XLSX discovery tree, emit stratified CSV + HTML.

Tier 1: hit rates by (sport, prop, direction, pick_type, line_bucket) plus
context_known / defense_known / minutes_known. Goblin/Demon UNDER rows are
dropped (invalid market side).

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
    from analyze_graded_prop_winners import load_unified, normalize_decided  # type: ignore[import-not-found]
except ImportError:
    load_unified = None  # type: ignore[misc, assignment]
    normalize_decided = None  # type: ignore[misc, assignment]

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


def _drop_invalid_goblin_demon_under(df: pd.DataFrame) -> pd.DataFrame:
    pt = _pick_type_norm(df.get("pick_type", pd.Series("", index=df.index)))
    d = df.get("direction", pd.Series("", index=df.index)).astype(str).str.strip().str.upper()
    bad = pt.isin(["goblin", "demon"]) & d.eq("UNDER")
    return df.loc[~bad].copy()


def _result_binary(s: pd.Series) -> pd.Series:
    u = s.astype(str).str.strip().str.upper()
    return pd.Series(np.where(u == "HIT", 1.0, np.where(u == "MISS", 0.0, np.nan)), index=s.index)


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
    decided = _drop_invalid_goblin_demon_under(decided)
    if decided.empty:
        print("No decided graded rows after filters.")
        return

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
<p class="note">Goblin/Demon UNDER rows removed. {html.escape(ha_note)}</p>

<h2>Primary stratification (min n = {min_cell_n})</h2>
{_html_escape_df(hit_tbl)}

<h2>Calibration bins flagged (|pred - true| &gt; {cal_flag_eps})</h2>
{_html_escape_df(cal_flag)}

<h2>Recommended group minimum ml_prob (analysis only)</h2>
<p class="note">Global tiers are unchanged; this is a per-group recommendation table by sport × pipeline × pick_type × tier × direction × prop.</p>
{_html_escape_df(threshold_rows)}

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
