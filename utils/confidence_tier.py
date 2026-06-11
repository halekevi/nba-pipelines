"""
Per-row confidence tier for hit-rate / stack decisions.

All sports use the **same** signal criteria and score thresholds. Sparse boards
(Tennis, Golf, etc.) score lower naturally when strat/L5/def fields are missing —
no sport-specific caps or score penalties.

Outputs:
  - sport_signal_maturity: HIGH | MED | LOW (informational: graded-history depth for the sport)
  - confidence_tier: HIGH | MED | LOW (unified signal score → tier)
  - confidence_score: 0–100
  - confidence_note: short tag string for audits
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from utils.defense_tiers import normalize_def_tier_label

_EMPTY_DEF = frozenset({"", "N/A", "NA", "LEAGUE AVG", "UNKNOWN", "?", "NAN", "NONE", "NULL"})

# Informational only — does NOT alter confidence_tier (same rules for every sport).
SPORT_SIGNAL_MATURITY: dict[str, str] = {
    "NBA": "HIGH",
    "WNBA": "HIGH",
    "NBA1Q": "HIGH",
    "NBA1H": "HIGH",
    "NHL": "MED",
    "MLB": "MED",
    "CBB": "MED",
    "CFB": "MED",
    "WCBB": "MED",
    "SOCCER": "LOW",
    "TENNIS": "LOW",
    "GOLF": "LOW",
    "NFL": "LOW",
}

# Unified thresholds (all sports).
_TIER_HIGH_MIN = 48.0
_TIER_MED_MIN = 26.0

CONFIDENCE_TIER_COLS: tuple[str, ...] = (
    "sport_signal_maturity",
    "confidence_tier",
    "confidence_score",
    "confidence_note",
)

CONFIDENCE_TIER_RENAME: dict[str, str] = {
    "sport_signal_maturity": "Sport Maturity",
    "confidence_tier": "Confidence Tier",
    "confidence_score": "Confidence Score",
    "confidence_note": "Confidence Note",
}


def sport_signal_maturity(sport: str) -> str:
    """Informational label for how deep graded PP history is for a sport."""
    s = str(sport or "").strip().upper()
    if s in SPORT_SIGNAL_MATURITY:
        return SPORT_SIGNAL_MATURITY[s]
    if s.startswith("NBA"):
        return "HIGH"
    if s in {"SOC", "SOCCER", "FIFA", "EPL"}:
        return "LOW"
    return "MED"


def _direction_series(df: pd.DataFrame) -> pd.Series:
    for col in ("final_bet_direction", "bet_direction", "direction", "Direction"):
        if col in df.columns:
            return df[col].astype(str).str.strip().str.upper()
    return pd.Series(["OVER"] * len(df), index=df.index)


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    val = df[col]
    if isinstance(val, pd.DataFrame):
        val = val.iloc[:, 0]
    return pd.to_numeric(val, errors="coerce")


def _def_tier_known(series: pd.Series) -> pd.Series:
    def _known(x: object) -> bool:
        raw = str(x or "").strip().upper()
        if raw in _EMPTY_DEF:
            return False
        norm = normalize_def_tier_label(x).upper()
        return norm not in _EMPTY_DEF

    return series.map(_known)


def _consistency_strong(series: pd.Series) -> pd.Series:
    g = series.astype(str).str.strip().str.upper()
    return g.isin({"S", "A", "B"})


def _row_signal_score(
    *,
    strat_hr: float,
    strat_n: float,
    hit_rate: float,
    hit_rate_l5: float,
    hit_rate_l10: float,
    l5_side: float,
    l10_gp: float,
    l10_streak: str,
    def_known: bool,
    top3_known: bool,
    consistency_ok: bool,
    player_hr: float,
    opp_hr: float,
    ml_prob: float,
) -> tuple[float, list[str]]:
    """Unified scoring rubric — identical for every sport."""
    tags: list[str] = []
    score = 0.0

    if pd.notna(strat_n) and strat_n >= 30 and pd.notna(strat_hr):
        score += 28.0
        tags.append(f"strat_n={int(strat_n)}")
    elif pd.notna(strat_n) and strat_n >= 10 and pd.notna(strat_hr):
        score += 14.0
        tags.append(f"strat_n={int(strat_n)}")

    if pd.notna(hit_rate) and hit_rate >= 0.65:
        score += 14.0
        tags.append("hr65")
    elif pd.notna(hit_rate) and hit_rate >= 0.55:
        score += 8.0
        tags.append("hr55")

    if pd.notna(l5_side) and l5_side >= 4:
        score += 14.0
        tags.append("l5_4+")
    elif pd.notna(l5_side) and l5_side >= 3:
        score += 7.0
        tags.append("l5_3")

    if pd.notna(hit_rate_l5) and hit_rate_l5 >= 0.60:
        score += 6.0
    if pd.notna(hit_rate_l10) and hit_rate_l10 >= 0.60 and pd.notna(l10_gp) and l10_gp >= 5:
        score += 6.0
        tags.append("l10")

    if l10_streak == "HOT":
        score += 4.0
        tags.append("hot_l10")

    if def_known:
        score += 8.0
        tags.append("def")

    if top3_known:
        score += 6.0
        tags.append("top3")

    if consistency_ok:
        score += 6.0
        tags.append("cons")

    if pd.notna(player_hr):
        score += 4.0
        tags.append("phr")
    if pd.notna(opp_hr):
        score += 4.0
        tags.append("ohr")

    if pd.notna(ml_prob) and abs(float(ml_prob) - 0.5) >= 0.06:
        score += 5.0
        tags.append("ml")

    if pd.isna(strat_hr) and pd.isna(hit_rate) and (pd.isna(l5_side) or l5_side < 2):
        score -= 18.0
        tags.append("thin")

    return float(np.clip(score, 0.0, 100.0)), tags


def _signal_tier_from_score(score: float) -> str:
    if score >= _TIER_HIGH_MIN:
        return "HIGH"
    if score >= _TIER_MED_MIN:
        return "MED"
    return "LOW"


def _vector_signal_scores(out: pd.DataFrame, l5_side: pd.Series) -> pd.Series:
    """Vectorized twin of _row_signal_score for large graded archives."""
    strat_hr = _num(out, "strat_hit_rate")
    strat_n = _num(out, "strat_n")
    hit_rate = _num(out, "hit_rate")
    hit_rate_l5 = _num(out, "hit_rate_l5")
    hit_rate_l10 = _num(out, "hit_rate_l10")
    l10_gp = _num(out, "l10_games_played")
    player_hr = _num(out, "player_hr_historical")
    opp_hr = _num(out, "opp_hr_historical")
    ml_prob = _num(out, "ml_prob")

    score = pd.Series(0.0, index=out.index, dtype=float)
    strat30 = strat_n.ge(30) & strat_hr.notna()
    strat10 = strat_n.ge(10) & strat_hr.notna() & ~strat30
    score = score + np.where(strat30, 28.0, np.where(strat10, 14.0, 0.0))

    hr65 = hit_rate.ge(0.65)
    hr55 = hit_rate.ge(0.55) & ~hr65
    score = score + np.where(hr65, 14.0, np.where(hr55, 8.0, 0.0))

    l5_4 = l5_side.ge(4)
    l5_3 = l5_side.ge(3) & ~l5_4
    score = score + np.where(l5_4, 14.0, np.where(l5_3, 7.0, 0.0))

    score = score + np.where(hit_rate_l5.ge(0.60), 6.0, 0.0)
    l10_ok = hit_rate_l10.ge(0.60) & l10_gp.ge(5)
    score = score + np.where(l10_ok, 6.0, 0.0)

    streak = (
        out["l10_streak"].astype(str).str.strip().str.upper()
        if "l10_streak" in out.columns
        else pd.Series("", index=out.index)
    )
    score = score + np.where(streak.eq("HOT"), 4.0, 0.0)

    if "def_tier" in out.columns:
        def_known = _def_tier_known(out["def_tier"])
    else:
        def_known = pd.Series(False, index=out.index)
    score = score + np.where(def_known, 8.0, 0.0)

    top3_rank = _num(out, "team_top3_rank")
    bot_rank = _num(out, "team_bottom3_rank")
    top3_weak = _num(out, "top3_weak_overperformer").fillna(0)
    top3_fade = _num(out, "top3_elite_fader").fillna(0)
    top3_known = (
        (top3_rank.notna() & top3_rank.ne(0))
        | (bot_rank.notna() & bot_rank.ne(0))
        | top3_weak.ne(0)
        | top3_fade.ne(0)
    )
    score = score + np.where(top3_known, 6.0, 0.0)

    if "consistency_grade" in out.columns:
        consistency_ok = _consistency_strong(out["consistency_grade"])
    else:
        consistency_ok = pd.Series(False, index=out.index)
    score = score + np.where(consistency_ok, 6.0, 0.0)

    score = score + np.where(player_hr.notna(), 4.0, 0.0)
    score = score + np.where(opp_hr.notna(), 4.0, 0.0)
    ml_edge = ml_prob.notna() & (ml_prob.sub(0.5).abs().ge(0.06))
    score = score + np.where(ml_edge, 5.0, 0.0)

    thin = strat_hr.isna() & hit_rate.isna() & (l5_side.isna() | l5_side.lt(2))
    score = score - np.where(thin, 18.0, 0.0)
    return score.clip(0.0, 100.0).round(1)


def attach_confidence_tier(df: pd.DataFrame) -> pd.DataFrame:
    """Attach sport_signal_maturity (info) + unified confidence_tier/score/note."""
    if df is None or df.empty:
        return df
    out = df.copy()

    sport_col = out.get("sport", pd.Series("NBA", index=out.index)).astype(str).str.strip().str.upper()
    direction = _direction_series(out)
    is_under = direction.isin({"UNDER", "LOWER"})

    l5o = _num(out, "l5_over")
    l5u = _num(out, "l5_under")
    l5_side = pd.Series(np.where(is_under, l5u, l5o), index=out.index, dtype=float)

    maturity = sport_col.map(sport_signal_maturity)
    scores = _vector_signal_scores(out, l5_side)
    tiers = np.where(
        scores.ge(_TIER_HIGH_MIN),
        "HIGH",
        np.where(scores.ge(_TIER_MED_MIN), "MED", "LOW"),
    )
    notes = (
        "sport="
        + sport_col.astype(str)
        + ",data_depth="
        + maturity.astype(str)
        + ",score="
        + scores.astype(str)
    ).str.slice(0, 120)

    out["sport_signal_maturity"] = maturity
    out["confidence_tier"] = tiers
    out["confidence_score"] = scores
    out["confidence_note"] = notes
    return out
