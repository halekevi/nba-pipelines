#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from datetime import datetime
import math
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from build_retrain_dataset import (  # noqa: E402
    _prepare_step8,
    load_all_graded_props,
    load_step8_dated_snapshot,
    normalize_direction,
    normalize_line,
    normalize_pick_type,
    player_join_key,
    prop_join_key,
)

SPORTS_ALL = ["NBA", "MLB", "NHL", "Soccer", "Tennis", "WNBA"]
ID_COLS = ["file_date", "sport", "player", "prop", "line", "direction", "pick_type"]


def _repo_root(arg: Path | None) -> Path:
    return Path(arg).resolve() if arg else Path(__file__).resolve().parent.parent


def _canon_step8(df: pd.DataFrame) -> pd.DataFrame:
    ren = {
        "Tier": "tier",
        "Rank Score": "rank_score",
        "Player": "player",
        "Prop": "prop",
        "Pick Type": "pick_type",
        "Line": "line",
        "Direction": "direction",
        "Edge": "edge",
        "ML Prob": "ml_prob",
        "Hit Rate (5g)": "hit_rate",
        "Def Tier": "def_tier",
        "Min Tier": "min_tier",
        "L5 Over": "l5_over",
        "L5 Under": "l5_under",
        "Shot Role": "shot_role",
        "Usage Role": "usage_role",
        "B2B": "b2b",
        "CV%": "cv_pct",
        "Edge vs PP": "edge_vs_pp",
        "#Books": "books_count",
        "L10 Over": "l10_over",
        "L10 Under": "l10_under",
        "L5 Avg": "l5_avg",
        "Szn Avg": "szn_avg",
        "H2H Over%": "h2h_over_pct",
        "H2H GP": "h2h_gp",
    }
    out = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})
    return out


def _build_joined_dataset(root: Path, sports: list[str]) -> pd.DataFrame:
    templates = root / "ui_runner" / "templates"
    graded = load_all_graded_props(templates)
    if graded.empty:
        return pd.DataFrame()

    graded["result_u"] = graded["result"].astype(str).str.strip().str.upper()
    graded = graded[graded["result_u"].isin(("HIT", "MISS"))].copy()
    graded["result_binary"] = (graded["result_u"] == "HIT").astype(int)
    graded["file_date"] = graded["file_date"].astype(str).str.slice(0, 10)
    graded = graded[graded["sport"].isin(sports)].copy()

    if graded.empty:
        return graded

    parts: list[pd.DataFrame] = []
    for (sport, file_date), g in graded.groupby(["sport", "file_date"], sort=True):
        g = g.copy()
        s8_raw, _used_static = load_step8_dated_snapshot(root, sport, file_date)
        if s8_raw is None or len(s8_raw) == 0:
            print(f"[warn] No step8 snapshot for {sport} {file_date}; rows will have NaN score/signal fields.")
            parts.append(g)
            continue

        s8 = _canon_step8(_prepare_step8(s8_raw, anchor_file_date=str(file_date)))
        s8 = s8.drop_duplicates(subset=["_n_player", "_n_prop", "_n_line", "_n_pick", "_n_dir"], keep="first")

        g["_n_player"] = g["player"].map(player_join_key)
        g["_n_prop"] = g["prop"].map(prop_join_key)
        g["_n_line"] = g["line"].map(normalize_line)
        g["_n_pick"] = g["pick_type"].map(normalize_pick_type)
        g["_n_dir"] = g["direction"].map(normalize_direction)

        keep_cols = [
            "_n_player",
            "_n_prop",
            "_n_line",
            "_n_pick",
            "_n_dir",
            "rank_score",
            "tier",
            "edge",
            "hit_rate",
            "ml_prob",
            "def_tier",
            "min_tier",
            "l5_over",
            "l5_under",
            "shot_role",
            "usage_role",
            "b2b",
            "cv_pct",
            "edge_vs_pp",
            "books_count",
            "l10_over",
            "l10_under",
            "l5_avg",
            "szn_avg",
            "h2h_over_pct",
            "h2h_gp",
        ]
        feat_cols = [c for c in keep_cols if c in s8.columns]
        merged = g.merge(
            s8[feat_cols],
            on=["_n_player", "_n_prop", "_n_line", "_n_pick", "_n_dir"],
            how="left",
            suffixes=("", "_s8"),
        )
        merged["step8_matched"] = False
        if "rank_score" in merged.columns:
            merged["step8_matched"] = merged["step8_matched"] | pd.to_numeric(merged["rank_score"], errors="coerce").notna()
        if "edge" in merged.columns:
            merged["step8_matched"] = merged["step8_matched"] | pd.to_numeric(merged["edge"], errors="coerce").notna()
        if "ml_prob" in merged.columns:
            merged["step8_matched"] = merged["step8_matched"] | pd.to_numeric(merged["ml_prob"], errors="coerce").notna()
        parts.append(merged.drop(columns=["_n_player", "_n_prop", "_n_line", "_n_pick", "_n_dir"], errors="ignore"))

    out = pd.concat(parts, ignore_index=True) if parts else graded

    for c in (
        "rank_score",
        "edge",
        "hit_rate",
        "ml_prob",
        "l5_over",
        "l5_under",
        "l10_over",
        "l10_under",
        "cv_pct",
        "edge_vs_pp",
        "l5_avg",
        "szn_avg",
        "h2h_over_pct",
        "h2h_gp",
        "books_count",
    ):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")

    out["tier"] = out.get("tier", out.get("tier_s8", np.nan))
    out["direction"] = out["direction"].astype(str).str.upper().str.strip()
    out["pick_type"] = out["pick_type"].astype(str).str.strip()
    out["prop"] = out["prop"].astype(str).str.strip()
    out["sport"] = out["sport"].astype(str).str.strip()
    out["prop_type"] = out["prop"]
    if "step8_matched" not in out.columns:
        out["step8_matched"] = pd.to_numeric(out.get("rank_score"), errors="coerce").notna()
    return out


def _fmt_rate(v: float) -> str:
    return "NA" if pd.isna(v) else f"{100.0 * float(v):.2f}%"


def _tier_hit_rate_str(df: pd.DataFrame) -> str:
    if "tier" not in df.columns or df.empty:
        return "NA"
    out: list[str] = []
    for t in ("A", "B", "C", "D"):
        s = df[df["tier"].astype(str).str.upper() == t]
        if len(s) == 0:
            continue
        out.append(f"{t}:{_fmt_rate(float(s['result_binary'].mean()))}")
    return " | ".join(out) if out else "NA"


def _apply_temporal_split(df: pd.DataFrame, train_frac: float) -> pd.DataFrame:
    out = df.copy()
    out["file_date_dt"] = pd.to_datetime(out.get("file_date"), errors="coerce")
    out["is_holdout"] = False
    frac = float(train_frac)
    frac = min(max(frac, 0.5), 0.95)
    for sport, idx in out.groupby("sport").groups.items():
        s = out.loc[idx, "file_date_dt"].dropna()
        if s.empty:
            continue
        cutoff = s.quantile(frac, interpolation="nearest")
        out.loc[idx, "is_holdout"] = out.loc[idx, "file_date_dt"] > cutoff
    return out


def _apply_method_a(df: pd.DataFrame) -> pd.DataFrame:
    need = df.copy()
    need["_tier_u"] = need["tier"].astype(str).str.upper()
    sel = need[
        need["_tier_u"].isin(("A", "B"))
        & (pd.to_numeric(need["hit_rate"], errors="coerce") >= 0.45)
        & (pd.to_numeric(need["edge"], errors="coerce") >= -0.25)
    ].copy()
    sel = sel.sort_values(["sport", "prop_type", "rank_score", "edge"], ascending=[True, True, False, False])
    sel["rank_position"] = sel.groupby(["sport", "prop_type"]).cumcount() + 1
    return sel


def _compute_signal_count(df: pd.DataFrame, sport_filter: str) -> pd.DataFrame:
    out = df.copy()

    has_def = "def_tier" in out.columns and out["def_tier"].notna().any()
    has_min = "min_tier" in out.columns and out["min_tier"].notna().any()
    has_l5o = "l5_over" in out.columns and out["l5_over"].notna().any()
    has_l5u = "l5_under" in out.columns and out["l5_under"].notna().any()
    has_b2b = "b2b" in out.columns and out["b2b"].notna().any()
    has_edge = "edge" in out.columns and out["edge"].notna().any()

    missing = []
    if not has_def:
        missing.append("Def Tier")
    if not has_min:
        missing.append("Min Tier")
    if not (has_l5o and has_l5u):
        missing.append("L5 Over/L5 Under")
    if not has_b2b:
        missing.append("B2B")
    if not has_edge:
        missing.append("Edge")
    if missing:
        print(f"[warn] {sport_filter}: Method B missing signal columns -> {', '.join(missing)} (skipped where unavailable)")

    over_mask = out["direction"].astype(str).str.upper().eq("OVER")
    under_mask = out["direction"].astype(str).str.upper().eq("UNDER")

    sig = pd.Series(0, index=out.index, dtype="int64")
    if has_def:
        def_u = out["def_tier"].astype(str).str.upper().str.strip()
        sig += ((over_mask & def_u.isin(["ELITE", "ABOVE AVG"])) | (under_mask & def_u.eq("ELITE"))).astype(int)
    if has_min:
        sig += out["min_tier"].astype(str).str.upper().str.strip().eq("HIGH").astype(int)
    if has_l5o:
        l5o = pd.to_numeric(out["l5_over"], errors="coerce")
        sig += (over_mask & l5o.eq(5)).astype(int)
    if has_l5u:
        l5u = pd.to_numeric(out["l5_under"], errors="coerce")
        sig += (under_mask & l5u.eq(5)).astype(int)
    if has_b2b:
        sig += out["b2b"].astype(str).str.upper().str.strip().eq("NO").astype(int)
    if has_edge:
        sig += (pd.to_numeric(out["edge"], errors="coerce") > 1.0).astype(int)

    out["signal_count"] = pd.to_numeric(sig, errors="coerce").fillna(0).astype(int)
    return out


def _apply_method_b(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    sel = out[out["signal_count"] >= 3].copy()
    sel = sel.sort_values(["sport", "prop_type", "signal_count", "edge"], ascending=[True, True, False, False])
    sel["rank_position"] = sel.groupby(["sport", "prop_type"]).cumcount() + 1
    return sel


def _apply_method_c(sel_a: pd.DataFrame) -> pd.DataFrame:
    out = sel_a.copy()
    out = out.sort_values(["sport", "prop_type", "signal_count", "edge"], ascending=[True, True, False, False])
    out["rank_position"] = out.groupby(["sport", "prop_type"]).cumcount() + 1
    return out


def _load_prism_profiles(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw.get("profiles", {}) if isinstance(raw, dict) else {}


def _sigmoid_rank(rs: pd.Series) -> pd.Series:
    x = pd.to_numeric(rs, errors="coerce")
    return 1.0 / (1.0 + np.exp(-x.fillna(0.0)))


def _norm01(s: pd.Series) -> pd.Series:
    if s is None:
        return pd.Series(np.zeros(0), dtype=float)
    x = pd.to_numeric(s, errors="coerce")
    lo = x.min()
    hi = x.max()
    if pd.isna(lo) or pd.isna(hi) or float(hi) <= float(lo):
        return pd.Series(np.zeros(len(x)), index=x.index, dtype=float)
    return ((x - lo) / (hi - lo)).fillna(0.0)


def _context_from_signals(df: pd.DataFrame, signal_weights: dict[str, float]) -> pd.Series:
    out = pd.Series(np.zeros(len(df)), index=df.index, dtype=float)
    direction = df["direction"].astype(str).str.upper().str.strip()
    over = direction.eq("OVER")
    under = direction.eq("UNDER")

    def add_flag(key: str, flag: pd.Series) -> None:
        w = float(signal_weights.get(key, 0.0) or 0.0)
        if w != 0.0:
            nonlocal out
            out = out + (flag.astype(float) * w)

    if "def_tier" in df.columns:
        d = df["def_tier"].astype(str).str.upper().str.strip()
        add_flag("def_tier", (over & d.isin(["ELITE", "ABOVE AVG"])) | (under & d.eq("ELITE")))
    if "min_tier" in df.columns:
        add_flag("min_tier", df["min_tier"].astype(str).str.upper().str.strip().eq("HIGH"))
    if "l5_over" in df.columns and "l5_under" in df.columns:
        l5o = pd.to_numeric(df["l5_over"], errors="coerce")
        l5u = pd.to_numeric(df["l5_under"], errors="coerce")
        add_flag("l5_alignment", (over & l5o.eq(5)) | (under & l5u.eq(5)))
    if "l10_over" in df.columns and "l10_under" in df.columns:
        l10o = pd.to_numeric(df["l10_over"], errors="coerce")
        l10u = pd.to_numeric(df["l10_under"], errors="coerce")
        add_flag("l10_alignment", (over & l10o.ge(8)) | (under & l10u.ge(8)))
    if "b2b" in df.columns:
        add_flag("b2b", df["b2b"].astype(str).str.upper().str.strip().eq("NO"))
    if "edge" in df.columns:
        add_flag("edge_gt_1", pd.to_numeric(df["edge"], errors="coerce").gt(1.0))
    if "shot_role" in df.columns:
        add_flag("shot_role", df["shot_role"].astype(str).str.upper().str.contains("HIGH|PRIMARY|LEAD", na=False))
    if "usage_role" in df.columns:
        add_flag("usage_role", df["usage_role"].astype(str).str.upper().str.contains("HIGH|PRIMARY|LEAD", na=False))

    return _norm01(out)


def _tier_modifier_score(df: pd.DataFrame, tier_mod: dict[str, float]) -> pd.Series:
    # All component blocks are optional and only activate when columns exist.
    market = pd.Series(np.zeros(len(df)), index=df.index, dtype=float)
    if "edge_vs_pp" in df.columns:
        market = market + _norm01(df.get("edge_vs_pp"))
    if "books_count" in df.columns:
        market = market + _norm01(df.get("books_count"))
    market = _norm01(market)

    recent = pd.Series(np.zeros(len(df)), index=df.index, dtype=float)
    if "l5_over" in df.columns and "l5_under" in df.columns:
        d = df["direction"].astype(str).str.upper().str.strip()
        over = d.eq("OVER")
        under = d.eq("UNDER")
        l5o = pd.to_numeric(df.get("l5_over"), errors="coerce")
        l5u = pd.to_numeric(df.get("l5_under"), errors="coerce")
        recent = recent + np.where(over, l5o.fillna(0.0), np.where(under, l5u.fillna(0.0), 0.0))
    if "l10_over" in df.columns and "l10_under" in df.columns:
        d = df["direction"].astype(str).str.upper().str.strip()
        over = d.eq("OVER")
        under = d.eq("UNDER")
        l10o = pd.to_numeric(df.get("l10_over"), errors="coerce")
        l10u = pd.to_numeric(df.get("l10_under"), errors="coerce")
        recent = recent + np.where(over, l10o.fillna(0.0), np.where(under, l10u.fillna(0.0), 0.0))
    recent = _norm01(recent)

    opp = pd.Series(np.zeros(len(df)), index=df.index, dtype=float)
    if "def_tier" in df.columns:
        d = df["def_tier"].astype(str).str.upper().str.strip()
        opp = opp + d.map({"ELITE": 1.0, "ABOVE AVG": 0.75, "AVG": 0.5, "BELOW AVG": 0.25}).fillna(0.0)
    if "h2h_over_pct" in df.columns:
        opp = opp + _norm01(df.get("h2h_over_pct"))
    if "h2h_gp" in df.columns:
        opp = opp + 0.25 * _norm01(df.get("h2h_gp"))
    opp = _norm01(opp)

    role = pd.Series(np.zeros(len(df)), index=df.index, dtype=float)
    if "min_tier" in df.columns:
        role = role + df["min_tier"].astype(str).str.upper().str.strip().map({"HIGH": 1.0, "MED": 0.5, "LOW": 0.0}).fillna(0.0)
    if "shot_role" in df.columns:
        role = role + df["shot_role"].astype(str).str.upper().str.contains("HIGH|PRIMARY|LEAD", na=False).astype(float)
    if "usage_role" in df.columns:
        role = role + df["usage_role"].astype(str).str.upper().str.contains("HIGH|PRIMARY|LEAD", na=False).astype(float)
    role = _norm01(role)

    vol = pd.Series(np.zeros(len(df)), index=df.index, dtype=float)
    if "cv_pct" in df.columns:
        vol = vol + _norm01(df.get("cv_pct"))
    if "l5_avg" in df.columns and "szn_avg" in df.columns:
        l5 = pd.to_numeric(df.get("l5_avg"), errors="coerce")
        szn = pd.to_numeric(df.get("szn_avg"), errors="coerce")
        vol = vol + _norm01((l5 - szn).abs())
    vol = _norm01(vol)

    score = (
        float(tier_mod.get("market_alignment_weight", 0.35)) * market
        + float(tier_mod.get("recent_form_weight", 0.20)) * recent
        + float(tier_mod.get("opponent_context_weight", 0.20)) * opp
        + float(tier_mod.get("role_minutes_weight", 0.15)) * role
        - float(tier_mod.get("volatility_weight", 0.10)) * vol
    )
    return pd.to_numeric(score, errors="coerce").fillna(0.0)


def _apply_method_prism(df: pd.DataFrame, profiles: dict[str, Any]) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for sport, g in df.groupby("sport", sort=False):
        prof = profiles.get(str(sport), {})
        gate = prof.get("selection_gate", {})
        rw = prof.get("ranking_weights", {})
        sw = prof.get("signal_weights", {})
        tm = prof.get("tier_modifiers", {})

        tier_allow = [str(x).upper() for x in gate.get("tier_allow", ["A", "B"])]
        min_hr = float(gate.get("min_hit_rate", 0.45))
        min_edge = float(gate.get("min_edge", -0.25))

        sel = g.copy()
        sel["_tier_u"] = sel["tier"].astype(str).str.upper()
        sel = sel[
            sel["_tier_u"].isin(tier_allow)
            & (pd.to_numeric(sel["hit_rate"], errors="coerce") >= min_hr)
            & (pd.to_numeric(sel["edge"], errors="coerce") >= min_edge)
        ].copy()
        if sel.empty:
            continue

        p_hit = _norm01(pd.to_numeric(sel.get("hit_rate"), errors="coerce"))
        p_ml = _norm01(pd.to_numeric(sel.get("ml_prob"), errors="coerce"))
        p_rank = _sigmoid_rank(sel.get("rank_score"))
        p_cal = (0.5 * p_hit) + (0.3 * p_ml) + (0.2 * p_rank)

        edge_norm = _norm01(sel.get("edge"))
        market_norm = _norm01(sel.get("edge_vs_pp"))
        if "books_count" in sel.columns:
            market_norm = 0.7 * market_norm + 0.3 * _norm01(sel.get("books_count"))
        context_norm = _context_from_signals(sel, sw if isinstance(sw, dict) else {})
        tier_mod_score = _tier_modifier_score(sel, tm if isinstance(tm, dict) else {})
        uncertainty = _norm01(sel.get("cv_pct")) if "cv_pct" in sel.columns else pd.Series(np.zeros(len(sel)), index=sel.index)

        score = (
            float(rw.get("p_calibrated", 0.55)) * p_cal
            + float(rw.get("edge_norm", 0.25)) * edge_norm
            + float(rw.get("market_norm", 0.15)) * market_norm
            + float(rw.get("context_norm", 0.05)) * context_norm
            + 0.10 * tier_mod_score
            + float(rw.get("uncertainty_penalty", -0.10)) * uncertainty
        )
        sel["prism_score"] = pd.to_numeric(score, errors="coerce").fillna(0.0)
        sel = sel.sort_values(["sport", "prop_type", "prism_score", "edge"], ascending=[True, True, False, False])
        sel["rank_position"] = sel.groupby(["sport", "prop_type"]).cumcount() + 1
        parts.append(sel)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=df.columns)


def _row_return_unit(row: pd.Series, roi_mode: str) -> float:
    hit = int(row.get("result_binary", 0)) == 1
    if roi_mode == "ev":
        mult = np.nan
        for c in ("line_payout", "payout_multiplier", "payout_mult", "multiplier"):
            if c in row.index:
                mult = pd.to_numeric(pd.Series([row.get(c)]), errors="coerce").iloc[0]
                if pd.notna(mult):
                    break
        if pd.notna(mult):
            return float(mult - 1.0) if hit else -1.0
    return 1.0 if hit else -1.0


def _calc_metrics(selected: pd.DataFrame, method: str, roi_mode: str) -> pd.DataFrame:
    if selected.empty:
        return pd.DataFrame(columns=["method", "sport", "prop_type"])

    rows: list[dict[str, Any]] = []

    def _top_metrics(g: pd.DataFrame, n_top: int) -> tuple[float, float]:
        if g.empty:
            return np.nan, np.nan
        top = g.nsmallest(n_top, "rank_position")
        if top.empty:
            return np.nan, np.nan
        hr = float(top["result_binary"].mean())
        roi = float(top.apply(lambda r: _row_return_unit(r, roi_mode), axis=1).mean())
        return hr, roi

    for (sport, prop_type), g in selected.groupby(["sport", "prop_type"], dropna=False):
        n = int(len(g))
        hits = int(g["result_binary"].sum())
        misses = int(n - hits)
        hit_rate = (hits / n) if n else np.nan
        if n:
            returns = g.apply(lambda r: _row_return_unit(r, roi_mode), axis=1)
            roi = float(returns.mean())
        else:
            roi = np.nan
        avg_hit_rank = float(g.loc[g["result_binary"] == 1, "rank_position"].mean()) if hits > 0 else np.nan
        t5_hr, t5_roi = _top_metrics(g, 5)
        t10_hr, t10_roi = _top_metrics(g, 10)
        hold = g[g.get("is_holdout", False)].copy()
        h5_hr, h5_roi = _top_metrics(hold, 5)
        h10_hr, h10_roi = _top_metrics(hold, 10)
        rank_corr_hold = np.nan
        if "signal_count" in hold.columns and len(hold) >= 10 and hold["signal_count"].notna().any():
            rank_corr_hold = hold["signal_count"].corr(hold["result_binary"], method="spearman")
        elif "prism_score" in hold.columns and len(hold) >= 10 and hold["prism_score"].notna().any():
            rank_corr_hold = hold["prism_score"].corr(hold["result_binary"], method="spearman")
        rows.append(
            {
                "method": method,
                "sport": sport,
                "prop_type": str(prop_type),
                "total_selected": n,
                "hits": hits,
                "decided": n,
                "hit_rate": hit_rate,
                "roi_flat_1u": roi,
                "avg_rank_pos_hits": avg_hit_rank,
                "top_5_hit_rate": t5_hr,
                "top_10_hit_rate": t10_hr,
                "top_5_roi": t5_roi,
                "top_10_roi": t10_roi,
                "top_5_hit_rate_holdout": h5_hr,
                "top_10_hit_rate_holdout": h10_hr,
                "top_5_roi_holdout": h5_roi,
                "top_10_roi_holdout": h10_roi,
                "rank_correlation_holdout": rank_corr_hold,
                "hit_rate_by_tier": _tier_hit_rate_str(g),
            }
        )
    return pd.DataFrame(rows)


def _attach_overlap(
    metrics: pd.DataFrame,
    sel_a: pd.DataFrame,
    sel_b: pd.DataFrame,
) -> pd.DataFrame:
    if metrics.empty:
        return metrics

    key = ID_COLS + ["prop_type"]
    a_key = sel_a[key].drop_duplicates() if not sel_a.empty else pd.DataFrame(columns=key)
    b_key = sel_b[key].drop_duplicates() if not sel_b.empty else pd.DataFrame(columns=key)
    ov = b_key.merge(a_key, on=key, how="inner")

    ov_stats = (
        ov.groupby(["sport", "prop_type"]).size().rename("overlap_n").to_frame().reset_index()
        if not ov.empty
        else pd.DataFrame(columns=["sport", "prop_type", "overlap_n"])
    )
    b_stats = (
        b_key.groupby(["sport", "prop_type"]).size().rename("b_n").to_frame().reset_index()
        if not b_key.empty
        else pd.DataFrame(columns=["sport", "prop_type", "b_n"])
    )
    ovm = b_stats.merge(ov_stats, on=["sport", "prop_type"], how="left")
    ovm["overlap_n"] = ovm["overlap_n"].fillna(0)
    ovm["overlap_pct_b_in_a"] = np.where(ovm["b_n"] > 0, ovm["overlap_n"] / ovm["b_n"], np.nan)
    metrics = metrics.merge(ovm[["sport", "prop_type", "overlap_pct_b_in_a"]], on=["sport", "prop_type"], how="left")
    return metrics


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _prop_test_p_value_larger(b_hits: int, b_n: int, a_hits: int, a_n: int) -> float:
    if b_n <= 0 or a_n <= 0:
        return float("nan")
    p_pool = (b_hits + a_hits) / (b_n + a_n)
    se = math.sqrt(max(0.0, p_pool * (1.0 - p_pool) * ((1.0 / b_n) + (1.0 / a_n))))
    if se <= 0:
        return float("nan")
    z = ((b_hits / b_n) - (a_hits / a_n)) / se
    return 1.0 - _norm_cdf(z)


def _winner_for_group(sub: pd.DataFrame, min_n: int, p_alpha: float, challenger_method: str = "C") -> str:
    a = sub[sub["method"] == "A"]
    b = sub[sub["method"] == challenger_method]
    if a.empty or b.empty:
        return "INCONCLUSIVE"
    a_n = int(a["total_selected"].iloc[0])
    b_n = int(b["total_selected"].iloc[0])
    a_hr = float(a["hit_rate"].iloc[0]) if pd.notna(a["hit_rate"].iloc[0]) else np.nan
    b_hr = float(b["hit_rate"].iloc[0]) if pd.notna(b["hit_rate"].iloc[0]) else np.nan
    if np.isfinite(a_hr) and np.isfinite(b_hr) and a_n >= min_n and b_n >= min_n and (b_hr - a_hr) >= 0.02:
        p_val = _prop_test_p_value_larger(
            b_hits=int(b["hits"].iloc[0]),
            b_n=b_n,
            a_hits=int(a["hits"].iloc[0]),
            a_n=a_n,
        )
        if np.isfinite(p_val) and p_val < p_alpha:
            return "B"
        return "INCONCLUSIVE"
    if a_n >= min_n and b_n >= min_n:
        return "A"
    return "INCONCLUSIVE"


def _print_side_by_side(metrics: pd.DataFrame) -> None:
    if metrics.empty:
        print("\nNo selected props after filters.")
        return

    show = metrics.copy()
    show["hit_rate"] = show["hit_rate"].map(_fmt_rate)
    show["roi_flat_1u"] = show["roi_flat_1u"].map(_fmt_rate)
    show["overlap_pct_b_in_a"] = show["overlap_pct_b_in_a"].map(_fmt_rate)
    show["avg_rank_pos_hits"] = show["avg_rank_pos_hits"].round(2)
    for c in ("top_5_hit_rate", "top_10_hit_rate", "top_5_roi", "top_10_roi", "top_5_hit_rate_holdout", "top_10_hit_rate_holdout"):
        if c in show.columns:
            show[c] = show[c].map(_fmt_rate)
    for c in ("top_5_roi_holdout", "top_10_roi_holdout"):
        if c in show.columns:
            show[c] = show[c].map(_fmt_rate)
    if "rank_correlation_holdout" in show.columns:
        show["rank_correlation_holdout"] = show["rank_correlation_holdout"].round(4)
    cols = [
        "sport",
        "prop_type",
        "method",
        "total_selected",
        "hit_rate",
        "roi_flat_1u",
        "avg_rank_pos_hits",
        "top_5_hit_rate",
        "top_10_hit_rate",
        "top_5_roi",
        "top_10_roi",
        "top_5_hit_rate_holdout",
        "top_10_hit_rate_holdout",
        "top_5_roi_holdout",
        "top_10_roi_holdout",
        "rank_correlation_holdout",
        "hit_rate_by_tier",
        "overlap_pct_b_in_a",
        "method_winner",
    ]
    print("\n=== A/B/C Backtest Results ===")
    print(show[cols].sort_values(["sport", "prop_type", "method"]).to_string(index=False))


def _print_cross_sport_summary(metrics: pd.DataFrame, min_n: int, p_alpha: float, challenger_method: str = "C") -> None:
    if metrics.empty:
        return
    rows: list[dict[str, Any]] = []
    for (sport, method), g in metrics.groupby(["sport", "method"], dropna=False):
        n = int(g["total_selected"].sum())
        hits = int(g["hits"].sum())
        hr = (hits / n) if n else np.nan
        rows.append({"sport": sport, "method": method, "total_selected": n, "hits": hits, "hit_rate": hr})
    sp = pd.DataFrame(rows)
    if sp.empty:
        return
    winner_rows: list[dict[str, str]] = []
    for sport, g in sp.groupby("sport"):
        a = g[g["method"] == "A"]
        b = g[g["method"] == challenger_method]
        if a.empty or b.empty:
            winner_rows.append({"sport": sport, "method_winner": "INCONCLUSIVE"})
            continue
        a_n = int(a["total_selected"].iloc[0])
        b_n = int(b["total_selected"].iloc[0])
        a_hr = float(a["hit_rate"].iloc[0]) if pd.notna(a["hit_rate"].iloc[0]) else np.nan
        b_hr = float(b["hit_rate"].iloc[0]) if pd.notna(b["hit_rate"].iloc[0]) else np.nan
        if np.isfinite(a_hr) and np.isfinite(b_hr) and a_n >= min_n and b_n >= min_n and (b_hr - a_hr) >= 0.02:
            p_val = _prop_test_p_value_larger(
                b_hits=int(b["hits"].iloc[0]),
                b_n=b_n,
                a_hits=int(a["hits"].iloc[0]),
                a_n=a_n,
            )
            if np.isfinite(p_val) and p_val < p_alpha:
                winner_rows.append({"sport": sport, "method_winner": "B"})
            else:
                winner_rows.append({"sport": sport, "method_winner": "INCONCLUSIVE"})
        elif a_n >= min_n and b_n >= min_n:
            winner_rows.append({"sport": sport, "method_winner": "A"})
        else:
            winner_rows.append({"sport": sport, "method_winner": "INCONCLUSIVE"})
    sp = sp.sort_values(["sport", "method"]).reset_index(drop=True)
    wdf = pd.DataFrame(winner_rows)
    sp = sp.merge(wdf, on="sport", how="left")
    sp["hit_rate"] = sp["hit_rate"].map(_fmt_rate)
    print("\n=== Cross-Sport Summary ===")
    print(sp.to_string(index=False))


def main() -> int:
    ap = argparse.ArgumentParser(description="A/B backtest for prop selection/ranking methods.")
    ap.add_argument("--sport", default="NBA", help="One of NBA, MLB, NHL, Soccer, Tennis, WNBA, ALL")
    ap.add_argument("--min_n", type=int, default=50, help="Minimum sample size for winner call")
    ap.add_argument("--p_alpha", type=float, default=0.10, help="One-sided z-test p-value threshold for challenger winner")
    ap.add_argument("--roi-mode", choices=["binary", "ev"], default="binary", help="ROI mode: binary or payout-weighted ev")
    ap.add_argument("--temporal-split", action="store_true", default=True, help="Enable chronological split for holdout metrics (default: on)")
    ap.add_argument("--no-temporal-split", action="store_false", dest="temporal_split", help="Disable holdout split metrics")
    ap.add_argument("--train-frac", type=float, default=0.70, help="Chronological train fraction per sport (default 0.70)")
    ap.add_argument("--profiles-config", type=Path, default=Path("config/test_model_profiles.json"), help="PRISM profile config path")
    ap.add_argument("--repo-root", type=Path, default=None, help="Repo root (default: parent of scripts/)")
    args = ap.parse_args()

    sport_arg = str(args.sport).strip()
    if sport_arg.upper() == "ALL":
        sports = SPORTS_ALL
    else:
        if sport_arg not in SPORTS_ALL:
            print(f"Invalid --sport={sport_arg}. Expected one of {SPORTS_ALL + ['ALL']}.", file=sys.stderr)
            return 2
        sports = [sport_arg]

    root = _repo_root(args.repo_root)
    dataset = _build_joined_dataset(root, sports)
    if dataset.empty:
        print("No graded decided rows found for selected sport(s).", file=sys.stderr)
        return 1
    qa = dataset.groupby("sport", dropna=False).agg(
        decided=("sport", "size"),
        step8_matched=("step8_matched", "sum"),
    )
    qa["match_rate_pct"] = np.where(qa["decided"] > 0, 100.0 * qa["step8_matched"] / qa["decided"], 0.0)
    print("\n=== Join Quality (graded -> step8) ===")
    print(qa.reset_index().to_string(index=False))
    low_cov = qa[qa["match_rate_pct"] < 60.0]
    if len(low_cov) > 0:
        print(f"[warn] Low join coverage detected (<60%): {', '.join(low_cov.index.astype(str).tolist())}")
    if args.roi_mode == "ev":
        payout_cols = [c for c in ("line_payout", "payout_multiplier", "payout_mult", "multiplier") if c in dataset.columns]
        if not payout_cols:
            print("[warn] --roi-mode ev requested but no payout columns found; falling back to binary ROI per row.")

    if args.temporal_split:
        dataset = _apply_temporal_split(dataset, args.train_frac)
    else:
        dataset["is_holdout"] = False

    sel_a = _apply_method_a(dataset)
    with_signals = _compute_signal_count(dataset, sport_arg.upper())
    sel_b = _apply_method_b(with_signals)
    sel_c = _apply_method_c(_compute_signal_count(sel_a, sport_arg.upper()))
    prism_profiles = _load_prism_profiles(root / args.profiles_config)
    sel_p = _apply_method_prism(with_signals, prism_profiles)

    m_a = _calc_metrics(sel_a, "A", args.roi_mode)
    m_b = _calc_metrics(sel_b, "B", args.roi_mode)
    m_c = _calc_metrics(sel_c, "C", args.roi_mode)
    m_p = _calc_metrics(sel_p, "P", args.roi_mode)
    metrics = pd.concat([m_a, m_b, m_c, m_p], ignore_index=True, sort=False)
    metrics = _attach_overlap(metrics, sel_a, sel_b)

    if not metrics.empty:
        winners = (
            metrics.groupby(["sport", "prop_type"], dropna=False)
            .apply(lambda g: _winner_for_group(g, args.min_n, args.p_alpha, challenger_method="P"))
            .rename("method_winner")
            .reset_index()
        )
        metrics = metrics.merge(winners, on=["sport", "prop_type"], how="left")
    else:
        metrics["method_winner"] = "INCONCLUSIVE"

    _print_side_by_side(metrics)
    if sport_arg.upper() == "ALL":
        _print_cross_sport_summary(metrics, args.min_n, args.p_alpha, challenger_method="P")

    outdir = root / "data" / "backtest"
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    out_path = outdir / f"ab_test_signal_stack_{stamp}.csv"
    metrics.sort_values(["sport", "prop_type", "method"]).to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\nSaved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
