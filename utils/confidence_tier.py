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

    maturity: list[str] = []
    tiers: list[str] = []
    scores: list[float] = []
    notes: list[str] = []

    for idx in out.index:
        sport_u = sport_col.loc[idx] if idx in sport_col.index else "NBA"
        mat = sport_signal_maturity(sport_u)

        def_raw = out.at[idx, "def_tier"] if "def_tier" in out.columns else out.get("DEF_TIER", pd.Series(dtype=object)).get(idx, "")
        def_known = bool(_def_tier_known(pd.Series([def_raw])).iloc[0])

        top3_rank = _num(out, "team_top3_rank").get(idx, np.nan)
        bot_rank = _num(out, "team_bottom3_rank").get(idx, np.nan)
        top3_weak = _num(out, "top3_weak_overperformer").get(idx, 0)
        top3_fade = _num(out, "top3_elite_fader").get(idx, 0)
        top3_known = any(
            pd.notna(x) and float(x) != 0
            for x in (top3_rank, bot_rank, top3_weak, top3_fade)
        )

        cg = out.at[idx, "consistency_grade"] if "consistency_grade" in out.columns else ""
        consistency_ok = bool(_consistency_strong(pd.Series([cg])).iloc[0])

        streak = str(out.at[idx, "l10_streak"] if "l10_streak" in out.columns else "").strip().upper()

        score, tags = _row_signal_score(
            strat_hr=_num(out, "strat_hit_rate").get(idx, np.nan),
            strat_n=_num(out, "strat_n").get(idx, np.nan),
            hit_rate=_num(out, "hit_rate").get(idx, np.nan),
            hit_rate_l5=_num(out, "hit_rate_l5").get(idx, np.nan),
            hit_rate_l10=_num(out, "hit_rate_l10").get(idx, np.nan),
            l5_side=l5_side.get(idx, np.nan),
            l10_gp=_num(out, "l10_games_played").get(idx, np.nan),
            l10_streak=streak,
            def_known=def_known,
            top3_known=top3_known,
            consistency_ok=consistency_ok,
            player_hr=_num(out, "player_hr_historical").get(idx, np.nan),
            opp_hr=_num(out, "opp_hr_historical").get(idx, np.nan),
            ml_prob=_num(out, "ml_prob").get(idx, np.nan),
        )

        tier = _signal_tier_from_score(score)
        note_parts = [f"sport={sport_u}", f"data_depth={mat}"] + tags

        maturity.append(mat)
        tiers.append(tier)
        scores.append(round(score, 1))
        notes.append(",".join(note_parts)[:120])

    out["sport_signal_maturity"] = maturity
    out["confidence_tier"] = tiers
    out["confidence_score"] = scores
    out["confidence_note"] = notes
    return out
