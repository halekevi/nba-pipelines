"""Apply player_consistency.db grades to ranked prop dataframes."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
_DB = _REPO / "data" / "cache" / "player_consistency.db"

_GRADE_MULTIPLIER = {
    "S": 1.25,
    "A": 1.15,
    "B": 1.05,
    "C": 1.00,
    "D": 0.80,
    "F": 0.00,
    "?": 0.95,
}

_bpc_mod = None


def _load_bpc():
    global _bpc_mod
    if _bpc_mod is None:
        sd = str(_REPO / "scripts")
        if sd not in sys.path:
            sys.path.insert(0, sd)
        try:
            import build_player_consistency as bpc  # noqa: E402

            _bpc_mod = bpc
        except Exception:
            _bpc_mod = False
    return _bpc_mod


def _normalize_prop_type(raw: str, sport: str) -> str:
    m = _load_bpc()
    if not m:
        return str(raw or "").strip()
    return m._normalize_prop_type(str(raw), sport)


def _get_line_bucket(prop_type: str, line: float, sport: str) -> str:
    m = _load_bpc()
    if not m:
        return "<5"
    try:
        ln = float(line)
    except (TypeError, ValueError):
        ln = 0.0
    return m.get_line_bucket(prop_type, ln, sport)


def _to_num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _grade_cache(sport: str) -> dict:
    import sqlite3

    if not _DB.is_file():
        return {}
    try:
        conn = sqlite3.connect(str(_DB))
        cur = conn.execute(
            "SELECT player_name, sport, prop_type, direction, line_bucket, grade "
            "FROM player_consistency WHERE sport = ?",
            (sport,),
        )
        d = {(a, b, c, d0, e): (g if g else "?") for a, b, c, d0, e, g in cur.fetchall()}
        conn.close()
        return d
    except Exception:
        return {}


def apply_consistency_grade_scores(
    df: pd.DataFrame,
    sport: str,
    *,
    score_col: str | None = None,
) -> pd.DataFrame:
    """
    Attach consistency_grade / consistency_multiplier and scale the ranking score.
    Modifies df in place and returns it.
    """
    if score_col is None:
        for c in ("final_score", "rank_score"):
            if c in df.columns:
                score_col = c
                break
        if score_col is None:
            score_col = "rank_score"

    cache = _grade_cache(sport)
    pc = next((c for c in ("player_name", "player_norm", "player", "pp_player") if c in df.columns), None)
    prop_col = next(
        (c for c in ("prop_norm", "stat_norm", "prop_type", "prop_type_normalized") if c in df.columns),
        None,
    )
    if "recommended_side" in df.columns:
        dir_col = "recommended_side"
    elif "bet_direction" in df.columns:
        dir_col = "bet_direction"
    else:
        dir_col = None
    line_col = next((c for c in ("line_score", "line") if c in df.columns), None)

    if pc is None or prop_col is None or dir_col is None or line_col is None:
        df["consistency_grade"] = "?"
        df["consistency_multiplier"] = 0.95
        if score_col in df.columns:
            df[score_col] = _to_num(df[score_col]) * 0.95
        return df

    players = df[pc].astype(str).str.strip()
    prop_raw = df[prop_col].astype(str)
    dirs = df[dir_col].astype(str).str.strip().str.upper()
    linev = _to_num(df[line_col]).fillna(0.0)
    grades: list[str] = []
    for i in range(len(df)):
        ptype = _normalize_prop_type(prop_raw.iloc[i], sport)
        try:
            ln = float(linev.iloc[i])
        except (TypeError, ValueError):
            ln = 0.0
        bkt = _get_line_bucket(ptype, ln, sport)
        g = cache.get((players.iloc[i], sport, ptype, dirs.iloc[i], bkt), "?")
        grades.append(g)
    gser = pd.Series(grades, index=df.index)
    mult = gser.map(lambda x: _GRADE_MULTIPLIER.get(x, 0.95)).astype(float)
    df["consistency_grade"] = gser
    df["consistency_multiplier"] = mult
    if score_col in df.columns:
        df[score_col] = _to_num(df[score_col]).astype(float) * mult
    return df
