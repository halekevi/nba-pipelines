#!/usr/bin/env python3
"""
step4_db_reader.py — PropOracle shared DB stat lookup module

Used by all 4 sport step4 scripts. Replaces all live ESPN/NHL API calls
with indexed SQLite reads from data/cache/proporacle_ref.db.

Key functions:
    open_db(db_path)                     → sqlite3.Connection
    get_vals_nba(con, espn_id, prop, n)  → List[float]
    get_vals_cbb(con, espn_id, prop, n)  → List[float]
    get_vals_nhl(con, player, prop, n)   → List[float]
    get_vals_soccer(con, espn_id, prop, n) → List[float]
    attach_stats(slate_df, sport, con, id_col, prop_col, line_col, n)
        → slate_df with stat columns filled in

Output columns added by attach_stats() — identical schema across all sports:
    stat_g1 .. stat_gN
    stat_last5_avg, stat_last10_avg, stat_season_avg
    last5_over, last5_under, last5_push, last5_hit_rate
    line_hit_rate_over_ou_5,  line_hit_rate_under_ou_5
    line_hit_rate_over_ou_10, line_hit_rate_under_ou_10
    stat_status   (OK | NO_ID | NO_DATA | INSUFFICIENT_GAMES)

NBA/CBB also adds:
    min_last5_avg

Soccer also adds:
    avg_minutes, avg_passes
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

# ── Default DB path ────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
DB_PATH = _HERE.parent / "data" / "cache" / "proporacle_ref.db"


def open_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open the PropOracle reference DB (read-only safe, WAL mode)."""
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"PropOracle reference DB not found at {path}\n"
            f"Run: py scripts/build_boxscore_ref.py --backfill --days 30"
        )
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL;")
    con.row_factory = sqlite3.Row
    return con


# ── NBA / CBB prop → DB column mapping ────────────────────────────────────────
# Maps prop_norm strings (lowercase) to one or more DB column expressions.
# Combos use SQL arithmetic directly.
_NBA_PROP_MAP = {
    # Simple stats
    "points":                  "pts",
    "pts":                     "pts",
    "rebounds":                "reb",
    "reb":                     "reb",
    "assists":                 "ast",
    "ast":                     "ast",
    "steals":                  "stl",
    "stl":                     "stl",
    "blocks":                  "blk",
    "blk":                     "blk",
    "turnovers":               "tov",
    "tov":                     "tov",
    "to":                      "tov",
    "fg made":                 "fgm",
    "fgm":                     "fgm",
    "fg attempted":            "fga",
    "fga":                     "fga",
    "3-pt made":               "fg3m",
    "3ptmade":                 "fg3m",
    "3pt made":                "fg3m",
    "fg3m":                    "fg3m",
    "3pm":                     "fg3m",
    "3-pt attempted":          "fg3a",
    "3ptattempted":            "fg3a",
    "3pt attempted":           "fg3a",
    "fg3a":                    "fg3a",
    "3pa":                     "fg3a",
    "two pointers made":       "fg2m",
    "twopointersmade":         "fg2m",
    "fg2m":                    "fg2m",
    "2pm":                     "fg2m",
    "two pointers attempted":  "fg2a",
    "twopointersattempted":    "fg2a",
    "fg2a":                    "fg2a",
    "2pa":                     "fg2a",
    "free throws made":        "ftm",
    "freethrowsmade":          "ftm",
    "ftm":                     "ftm",
    "free throws attempted":   "fta",
    "freethrowsattempted":     "fta",
    "fta":                     "fta",
    "offensive rebounds":      "oreb",
    "oreb":                    "oreb",
    "defensive rebounds":      "dreb",
    "dreb":                    "dreb",
    "personal fouls":          "pf",
    "personalfouls":           "pf",
    "pf":                      "pf",
    "fantasy score":           "fantasy_score",
    "fantasy_score":           "fantasy_score",
    "fantasy":                 "fantasy_score",   # norm_prop maps "Fantasy Score" → "fantasy"
    # Combos
    "pts+rebs+asts":           "pts + reb + ast",
    "pra":                     "pts + reb + ast",
    "pts+rebs":                "pts + reb",
    "pr":                      "pts + reb",
    "pts+asts":                "pts + ast",
    "pa":                      "pts + ast",
    "rebs+asts":               "reb + ast",
    "ra":                      "reb + ast",
    "blks+stls":               "blk + stl",
    "stocks":                  "blk + stl",
    "bs":                      "blk + stl",
    "minutes":                 "minutes",
    "min":                     "minutes",
}

# ── NHL prop → DB column ───────────────────────────────────────────────────────
_NHL_PROP_MAP = {
    "shots on goal":           "shots_on_goal",
    "shots_on_goal":           "shots_on_goal",
    "sog":                     "shots_on_goal",
    "shots on goal (combo)":   "shots_on_goal",
    "goals":                   "goals",
    "assists":                 "assists",
    "points":                  "points",
    "hits":                    "hits",
    "blocked shots":           "blocked_shots",
    "blocked_shots":           "blocked_shots",
    "pim":                     "pim",
    "plus/minus":              "plus_minus",
    "plus_minus":              "plus_minus",
    "power play points":       "pp_points",
    "pp_points":               "pp_points",
    "faceoffs won":            "faceoffs_won",
    "faceoffs_won":            "faceoffs_won",
    "time on ice":             "toi",
    "toi":                     "toi",
    "goalie saves":            "shots_on_goal",   # GK: shots_on_goal = saves in NHL context
    "saves":                   "shots_on_goal",
    "goals allowed":           "goals",           # for opposing team context — rare
    "goals_allowed":           "goals",
    "fantasy score":           "goals * 8 + assists * 5 + shots_on_goal * 1.5 + hits * 1.3 + blocked_shots * 1.3",
    "fantasy_score":           "goals * 8 + assists * 5 + shots_on_goal * 1.5 + hits * 1.3 + blocked_shots * 1.3",
}

# ── Soccer prop → DB column ────────────────────────────────────────────────────
_SOCCER_PROP_MAP = {
    "shots on target":         "sog",
    "shots_on_target":         "sog",
    "sog":                     "sog",
    "sot":                     "sog",
    "shots":                   "sh",
    "sh":                      "sh",
    "goals":                   "g",
    "g":                       "g",
    "assists":                 "a",
    "a":                       "a",
    "goalkeeper saves":        "sv",
    "goalie saves":            "sv",
    "saves":                   "sv",
    "sv":                      "sv",
    "passes":                  "pa",
    "pa":                      "pa",
    "key passes":              "kp",
    "kp":                      "kp",
    "tackles":                 "tk",
    "tk":                      "tk",
    "fouls":                   "fc",
    "fc":                      "fc",
    "yellow cards":            "yc",
    "yc":                      "yc",
    "goal+assist":             "g + a",
    "goal_assist":             "g + a",
    "shots assisted":          "kp",
    "minutes":                 "minutes_played",
    "minutes played":          "minutes_played",
}


def _resolve_prop(prop_norm: str, sport: str) -> Optional[str]:
    """Return the SQL column expression for a prop_norm, or None if unknown."""
    p = str(prop_norm or "").lower().strip()
    # Strip trailing (combo) marker
    p = p.replace("(combo)", "").strip()
    if sport in ("nba", "cbb"):
        return _NBA_PROP_MAP.get(p)
    if sport == "nhl":
        return _NHL_PROP_MAP.get(p)
    if sport == "soccer":
        return _SOCCER_PROP_MAP.get(p)
    return None


# ── Core DB query ──────────────────────────────────────────────────────────────
def _query_vals(con: sqlite3.Connection, table: str, where_clause: str,
                stat_expr: str, params: tuple, n: int) -> List[float]:
    """
    Generic indexed stat query.
    Returns up to n most-recent non-null values, newest first.
    """
    sql = f"""
        SELECT {stat_expr} AS val
        FROM {table}
        WHERE {where_clause}
          AND {stat_expr} IS NOT NULL
        ORDER BY game_date DESC
        LIMIT {n}
    """
    try:
        rows = con.execute(sql, params).fetchall()
        return [float(r[0]) for r in rows if r[0] is not None]
    except Exception:
        return []


def get_vals_nba(con: sqlite3.Connection, espn_id: str,
                  prop_norm: str, n: int = 10,
                  player_name: str = "") -> List[float]:
    expr = _resolve_prop(prop_norm, "nba")
    if not expr:
        return []
    # Primary: lookup by ESPN athlete ID
    vals = _query_vals(con, "nba",
                       "ESPN_ATHLETE_ID = ?", expr, (str(espn_id),), n)
    # Fallback: name-based lookup (when slate uses nba_player_id instead of ESPN ID)
    if not vals and player_name:
        norm = player_name.strip().lower()
        vals = _query_vals(con, "nba",
                           "lower(player) = ?", expr, (norm,), n)
    return vals


def get_vals_cbb(con: sqlite3.Connection, espn_id: str,
                  prop_norm: str, n: int = 10, player_name: str = "") -> List[float]:
    expr = _resolve_prop(prop_norm, "cbb")
    if not expr:
        return []
    return _query_vals(con, "cbb",
                       "ESPN_ATHLETE_ID = ?", expr, (str(espn_id),), n)


def get_vals_nhl(con: sqlite3.Connection, player: str,
                  prop_norm: str, n: int = 10) -> List[float]:
    """
    NHL lookup by player name (no ESPN ID in NHL pipeline).
    Exact match first, then case-insensitive fallback.
    """
    expr = _resolve_prop(prop_norm, "nhl")
    if not expr:
        return []
    vals = _query_vals(con, "nhl",
                       "player = ?", expr, (str(player),), n)
    if not vals:
        vals = _query_vals(con, "nhl",
                           "lower(player) = lower(?)", expr, (str(player),), n)
    return vals


def get_vals_soccer(con: sqlite3.Connection, espn_player_id: str,
                     prop_norm: str, n: int = 10,
                     player_name: str = "") -> List[float]:
    expr = _resolve_prop(prop_norm, "soccer")
    if not expr:
        return []
    # Primary: lookup by ESPN player ID
    vals = _query_vals(con, "soccer",
                       "espn_player_id = ?", expr, (str(espn_player_id),), n)
    # Fallback: name-based lookup for FBref-sourced rows
    if not vals and player_name:
        norm = player_name.strip().lower()
        vals = _query_vals(con, "soccer",
                           "lower(player) = ? AND espn_player_id LIKE 'fbref_%'",
                           expr, (norm,), n)
    return vals


# ── Minutes / passes lookups (soccer context columns) ─────────────────────────
def get_avg_minutes_soccer(con: sqlite3.Connection, espn_player_id: str,
                            n: int = 5, player_name: str = "") -> Optional[float]:
    vals = get_vals_soccer(con, espn_player_id, "minutes", n, player_name=player_name)
    return float(np.mean(vals)) if vals else None


def get_avg_passes_soccer(con: sqlite3.Connection, espn_player_id: str,
                           n: int = 5, player_name: str = "") -> Optional[float]:
    vals = get_vals_soccer(con, espn_player_id, "passes", n, player_name=player_name)
    return float(np.mean(vals)) if vals else None


def get_avg_minutes_nba(con: sqlite3.Connection, espn_id: str,
                         n: int = 5, table: str = "nba",
                         player_name: str = "") -> Optional[float]:
    vals = _query_vals(con, table, "ESPN_ATHLETE_ID = ?",
                       "minutes", (str(espn_id),), n)
    # Fallback: name-based lookup (when slate passes nba_player_id instead of ESPN ID)
    if not vals and player_name:
        norm = player_name.strip().lower()
        vals = _query_vals(con, table, "lower(player) = ?",
                           "minutes", (norm,), n)
    return float(np.mean(vals)) if vals else None


# ── Combo player support ───────────────────────────────────────────────────────
def get_vals_combo(get_fn, con, ids: List[str], prop_norm: str, n: int) -> List[float]:
    """
    Sum stats across multiple players (combo props).
    Aligns by game index (most recent game 1, etc.).
    Returns summed values for games where ALL players have data.
    """
    per_player = []
    for pid in ids:
        vals = get_fn(con, pid, prop_norm, n)
        if not vals:
            return []
        per_player.append(vals)
    min_games = min(len(v) for v in per_player)
    if min_games == 0:
        return []
    return [sum(v[i] for v in per_player) for i in range(min_games)]


# ── Hit rate math ──────────────────────────────────────────────────────────────
def calc_hit_context(vals: List[float], line: float, k: int = 5):
    """
    Returns (over, under, push, hit_rate_all, hit_rate_ou, under_rate_ou)
    for the first k values (most-recent first).
    """
    over = under = push = 0
    for v in vals[:k]:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        if v > line:
            over += 1
        elif v < line:
            under += 1
        else:
            push += 1
    total_all = over + under + push
    total_ou  = over + under
    hr_all  = (over / total_all) if total_all > 0 else np.nan
    hr_ou   = (over / total_ou)  if total_ou  > 0 else np.nan
    ur_ou   = (under / total_ou) if total_ou  > 0 else np.nan
    return over, under, push, hr_all, hr_ou, ur_ou


def fmt_num(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    return f"{float(x):.3f}".rstrip("0").rstrip(".")


# ── Main attach function ───────────────────────────────────────────────────────
def attach_stats(
    slate: pd.DataFrame,
    sport: str,                   # "nba" | "cbb" | "nhl" | "soccer"
    con: sqlite3.Connection,
    id_col: str,                  # column with player ID (espn_id or player name for NHL)
    prop_col: str = "prop_norm",
    line_col: str = "line",
    n: int = 10,
    combo_sep: str = "|",
) -> pd.DataFrame:
    """
    Attach stat columns to slate DataFrame from the SQLite reference DB.

    id_col:   for NBA/CBB/Soccer: ESPN athlete ID column
              for NHL: player name column (no ESPN ID in NHL pipeline)
    """
    sport = sport.lower()

    # Determine which get_fn to use
    _get_fns = {
        "nba":    get_vals_nba,
        "cbb":    get_vals_cbb,
        "nhl":    get_vals_nhl,
        "soccer": get_vals_soccer,
    }
    get_fn = _get_fns.get(sport)
    if get_fn is None:
        raise ValueError(f"Unknown sport: {sport}. Must be one of: nba, cbb, nhl, soccer")

    # Ensure output columns exist
    stat_cols = [f"stat_g{i}" for i in range(1, n + 1)]
    out_cols = stat_cols + [
        "stat_last5_avg", "stat_last10_avg", "stat_season_avg",
        "last5_over", "last5_under", "last5_push", "last5_hit_rate",
        "line_hit_rate_over_ou_5",  "line_hit_rate_under_ou_5",
        "line_hit_rate_over_ou_10", "line_hit_rate_under_ou_10",
        "stat_status",
    ]
    if sport in ("nba", "cbb"):
        out_cols.append("min_last5_avg")
    if sport == "soccer":
        out_cols += ["avg_minutes", "avg_passes"]

    for c in out_cols:
        if c not in slate.columns:
            slate[c] = ""

    slate["_line_num"] = pd.to_numeric(slate.get(line_col, ""), errors="coerce")

    status_counts = {"OK": 0, "NO_ID": 0, "NO_DATA": 0,
                     "UNSUPPORTED_PROP": 0, "INSUFFICIENT_GAMES": 0}

    for idx, row in slate.iterrows():
        prop     = str(row.get(prop_col, "")).lower().strip()
        raw_id   = str(row.get(id_col,   "")).strip()
        line     = row.get("_line_num", np.nan)
        try:
            line = float(line)
        except Exception:
            line = np.nan

        # Check prop is supported
        if not _resolve_prop(prop, sport):
            slate.at[idx, "stat_status"] = "UNSUPPORTED_PROP"
            status_counts["UNSUPPORTED_PROP"] += 1
            continue

        # Parse IDs — support combo players (pipe-separated)
        if not raw_id or raw_id in ("nan", ""):
            slate.at[idx, "stat_status"] = "NO_ID"
            status_counts["NO_ID"] += 1
            continue

        is_combo = (combo_sep in raw_id) or (
            str(row.get("is_combo_player", "")).strip().lower() in ("1", "true", "yes")
        )

        if is_combo:
            ids = [p.strip() for p in raw_id.split(combo_sep) if p.strip()]
            vals = get_vals_combo(get_fn, con, ids, prop, n)
        else:
            # Pass player_name for NBA name-based fallback when ESPN ID is missing
            player_name = str(row.get("player", "")).strip()
            if sport in ("nba", "cbb") and player_name:
                vals = get_fn(con, raw_id, prop, n, player_name=player_name)
            else:
                vals = get_fn(con, raw_id, prop, n)

        if not vals:
            slate.at[idx, "stat_status"] = "NO_DATA"
            status_counts["NO_DATA"] += 1
            continue

        if len(vals) < 2:
            slate.at[idx, "stat_status"] = "INSUFFICIENT_GAMES"
            status_counts["INSUFFICIENT_GAMES"] += 1
            # Still attach what we have rather than voiding
            # (1-game players are better than no data)

        # ── Fill per-game columns ──────────────────────────────────────────
        for i in range(1, n + 1):
            v = vals[i - 1] if (i - 1) < len(vals) else np.nan
            slate.at[idx, f"stat_g{i}"] = fmt_num(v)

        def avg_k(k):
            s = vals[:k] if len(vals) >= k else vals
            return float(np.mean(s)) if s else np.nan

        slate.at[idx, "stat_last5_avg"]  = fmt_num(avg_k(5))
        slate.at[idx, "stat_last10_avg"] = fmt_num(avg_k(10))
        slate.at[idx, "stat_season_avg"] = fmt_num(float(np.mean(vals)))

        # ── Hit rates ──────────────────────────────────────────────────────
        if not np.isnan(line):
            o5, u5, p5, hr5, hr5_ou, ur5_ou = calc_hit_context(vals, line, k=5)
            slate.at[idx, "last5_over"]               = str(o5)
            slate.at[idx, "last5_under"]              = str(u5)
            slate.at[idx, "last5_push"]               = str(p5)
            slate.at[idx, "last5_hit_rate"]           = fmt_num(hr5)
            slate.at[idx, "line_hit_rate_over_ou_5"]  = fmt_num(hr5_ou)
            slate.at[idx, "line_hit_rate_under_ou_5"] = fmt_num(ur5_ou)
            _, _, _, _, hr10_ou, ur10_ou = calc_hit_context(vals, line, k=10)
            slate.at[idx, "line_hit_rate_over_ou_10"]  = fmt_num(hr10_ou)
            slate.at[idx, "line_hit_rate_under_ou_10"] = fmt_num(ur10_ou)

        # ── Sport-specific context columns ─────────────────────────────────
        if sport in ("nba", "cbb") and not is_combo:
            player_name = str(row.get("player", "")).strip()
            min_avg = get_avg_minutes_nba(con, raw_id, n=5, table=sport, player_name=player_name)
            if min_avg is not None:
                slate.at[idx, "min_last5_avg"] = fmt_num(min_avg)

        if sport == "soccer" and not is_combo:
            min_avg  = get_avg_minutes_soccer(con, raw_id, n=5)
            pass_avg = get_avg_passes_soccer(con, raw_id, n=5)
            if min_avg  is not None: slate.at[idx, "avg_minutes"] = fmt_num(min_avg)
            if pass_avg is not None: slate.at[idx, "avg_passes"]  = fmt_num(pass_avg)

        if len(vals) >= 2:
            slate.at[idx, "stat_status"] = "OK"
            status_counts["OK"] += 1

    slate = slate.drop(columns=["_line_num"], errors="ignore")
    return slate, status_counts


# ── DB health check ────────────────────────────────────────────────────────────
def db_summary(con: sqlite3.Connection) -> None:
    print("\n── PropOracle Ref DB Summary ──────────────────────")
    for table in ("nba", "cbb", "nhl", "soccer"):
        try:
            total   = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            dates   = con.execute(f"SELECT MIN(game_date), MAX(game_date) FROM {table}").fetchone()
            players = con.execute(f"SELECT COUNT(DISTINCT player) FROM {table}").fetchone()[0]
            print(f"  {table:8s}  {total:>7,} rows  {players:>5,} players  "
                  f"{dates[0] or '—'} → {dates[1] or '—'}")
        except Exception:
            print(f"  {table:8s}  (not found)")
    print()
