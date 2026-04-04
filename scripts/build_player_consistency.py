#!/usr/bin/env python3
"""
Build SQLite player consistency profiles from all graded prop workbooks.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
from ensure_local_cache import ensure_local_cache

ensure_local_cache(str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "cache" / "player_consistency.db"

GRADED_GLOBS: list[tuple[str, str]] = [
    ("NBA", "graded_nba_*.xlsx"),
    ("CBB", "graded_cbb_*.xlsx"),
    ("NHL", "graded_nhl_*.xlsx"),
    ("Soccer", "graded_soccer_*.xlsx"),
]

SYNTHETIC_DIR = REPO_ROOT / "outputs" / "synthetic"
SYNTHETIC_GLOB = "graded_*_synthetic_*.xlsx"
SYNTHETIC_WEIGHT = 0.7
SYNTHETIC_GRADED_DB = REPO_ROOT / "data" / "cache" / "synthetic_graded.db"


def _find_myticket_performance_db() -> Path | None:
    for p in (
        REPO_ROOT / "data" / "db" / "MyTicketPerformance.db",
        REPO_ROOT / "MyTicketPerformance.db",
        REPO_ROOT / "data" / "MyTicketPerformance.db",
        REPO_ROOT / "data" / "cache" / "MyTicketPerformance.db",
    ):
        if p.is_file():
            return p
    return None


def _league_to_sport(league: str | None) -> str | None:
    if not league:
        return None
    u = str(league).upper().strip()
    if "NBA" in u or u == "BASKETBALL":
        return "NBA"
    if "CBB" in u or "NCAAB" in u or "COLLEGE" in u or "NCAA" in u:
        return "CBB"
    if "NHL" in u or "HOCKEY" in u:
        return "NHL"
    if any(x in u for x in ("EPL", "MLS", "UCL", "LALIGA", "BUNDESLIGA", "SOCCER", "SERIE A")):
        return "Soccer"
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strip_ws(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip())


def normalize_column_name(c: str) -> str:
    return str(c).strip().lower().replace(" ", "_")


def _find_column(df: pd.DataFrame, *candidates: str) -> str | None:
    lower = {normalize_column_name(c): c for c in df.columns}
    for cand in candidates:
        k = normalize_column_name(cand)
        if k in lower:
            return lower[k]
    return None


def _normalize_prop_type(raw: str, sport: str) -> str:
    s = _strip_ws(raw).lower()
    if sport in ("NBA", "CBB"):
        nba_map = {
            "pts": "Points",
            "points": "Points",
            "point": "Points",
            "reb": "Rebounds",
            "rebounds": "Rebounds",
            "rebound": "Rebounds",
            "ast": "Assists",
            "assists": "Assists",
            "assist": "Assists",
            "pra": "PRA",
            "pts+reb+ast": "PRA",
            "pts + reb + ast": "PRA",
            "pts+asts": "Pts+Asts",
            "pts+ast": "Pts+Asts",
            "pts asts": "Pts+Asts",
            "pts+rebs": "Pts+Rebs",
            "pts+reb": "Pts+Rebs",
            "pts rebs": "Pts+Rebs",
            "rebs+asts": "Rebs+Asts",
            "reb+ast": "Rebs+Asts",
            "rebs asts": "Rebs+Asts",
            "threes": "Threes",
        "3-pt made": "Threes",
            "3pt": "Threes",
            "3ptm": "Threes",
            "fg3m": "Threes",
            "3pm": "Threes",
            "steals": "Steals",
            "stl": "Steals",
            "blocks": "Blocks",
            "blk": "Blocks",
            "turnovers": "Turnovers",
            "tov": "Turnovers",
            "to": "Turnovers",
            "fantasy_score": "Fantasy Score",
            "fantasy": "Fantasy Score",
            "fantasy score": "Fantasy Score",
            "fta": "FTA",
            "fg_attempted": "FG Attempted",
            "fga": "FG Attempted",
            "fg attempted": "FG Attempted",
            "field goals attempted": "FG Attempted",
            "pa": "Pts+Asts",
            "pr": "Pts+Rebs",
            "ra": "Rebs+Asts",
            "blks+stls": "Blks+Stls",
            "blks stls": "Blks+Stls",
            "stocks": "Blks+Stls",
        }
        # Title-case fallthrough for display names
        for k, v in list(nba_map.items()):
            nba_map[k.replace(" ", "")] = v
        if s in nba_map:
            return nba_map[s]
        t = raw.strip()
        if t:
            tl = t.lower()
            for k, v in nba_map.items():
                if tl == k:
                    return v
        return _strip_ws(raw).title() if raw else "?"

    if sport == "NHL":
        nhl_map = {
            "goals": "Goals",
            "goal": "Goals",
            "assists": "Assists",
            "assist": "Assists",
            "points": "Points",
            "point": "Points",
            "shots": "Shots",
            "shots on goal": "Shots",
            "sog": "Shots",
            "shots_on_goal": "Shots",
            "shot_on_goal": "Shots",
            "saves": "Saves",
            "goalie saves": "Saves",
            "faceoffs": "Faceoffs",
            "faceoffs won": "Faceoffs",
            "faceoff": "Faceoffs",
            "blocked_shots": "Blocked Shots",
            "blocked shots": "Blocked Shots",
            "blocks": "Blocked Shots",
        }
        if s in nhl_map:
            return nhl_map[s]
        return _strip_ws(raw).title() if raw else "?"

    if sport == "Soccer":
        soc_map = {
            "goals": "Goals",
            "goal": "Goals",
            "assists": "Assists",
            "assist": "Assists",
            "shots": "Shots",
            "shots_on_target": "Shots on Target",
            "shots on target": "Shots on Target",
            "sot": "Shots on Target",
            "passes": "Passes",
            "passes attempted": "Passes",
            "tackles": "Tackles",
            "tackle": "Tackles",
        }
        if s in soc_map:
            return soc_map[s]
        return _strip_ws(raw).title() if raw else "?"

    return _strip_ws(raw).title() if raw else "?"


def _bucket_generic(line: float) -> str:
    if line < 10:
        return "<10"
    if line < 20:
        return "10-19.5"
    if line < 30:
        return "20-29.5"
    return "30+"


def _between(line: float, lo: float, hi: float) -> bool:
    return lo <= line <= hi


def get_line_bucket(prop_type: str, line: float, sport: str) -> str:
    pt = _strip_ws(prop_type)
    if sport in ("NBA", "CBB"):
        if pt == "Points":
            if line < 15:
                return "<15"
            if _between(line, 15, 24.5):
                return "15-24.5"
            if _between(line, 25, 34.5):
                return "25-34.5"
            return "35+"
        if pt == "Rebounds":
            if line < 6:
                return "<6"
            if _between(line, 6, 9.5):
                return "6-9.5"
            return "10+"
        if pt == "Assists":
            if line < 4:
                return "<4"
            if _between(line, 4, 7.5):
                return "4-7.5"
            return "8+"
        if pt == "PRA":
            if line < 20:
                return "<20"
            if _between(line, 20, 29.5):
                return "20-29.5"
            if _between(line, 30, 39.5):
                return "30-39.5"
            return "40+"
        if pt == "Pts+Asts":
            if line < 18:
                return "<18"
            if _between(line, 18, 27.5):
                return "18-27.5"
            return "28+"
        if pt == "Pts+Rebs":
            if line < 18:
                return "<18"
            if _between(line, 18, 27.5):
                return "18-27.5"
            return "28+"
        if pt == "Rebs+Asts":
            if line < 10:
                return "<10"
            if _between(line, 10, 13.5):
                return "10-13.5"
            return "14+"
        if pt == "Threes":
            if line < 2:
                return "<2"
            if _between(line, 2, 3.5):
                return "2-3.5"
            return "4+"
        if pt in ("Steals", "Blocks"):
            if line < 1.5:
                return "<1.5"
            return "1.5+"
        if pt == "Turnovers":
            if line < 2:
                return "<2"
            if _between(line, 2, 3.5):
                return "2-3.5"
            return "4+"
        if pt == "Fantasy Score":
            if line < 25:
                return "<25"
            if _between(line, 25, 34.5):
                return "25-34.5"
            if _between(line, 35, 44.5):
                return "35-44.5"
            return "45+"
        if pt == "FTA":
            if line < 3:
                return "<3"
            if _between(line, 3, 5.5):
                return "3-5.5"
            return "6+"
        if pt == "FG Attempted":
            if line < 10:
                return "<10"
            if _between(line, 10, 13.5):
                return "10-13.5"
            return "14+"

    if sport == "NHL":
        if pt in ("Goals", "Assists"):
            if line < 0.5:
                return "<0.5"
            return "0.5+"
        if pt == "Points":
            if line < 1:
                return "<1"
            if _between(line, 1, 1.5):
                return "1-1.5"
            return "2+"
        if pt == "Shots":
            if line < 3:
                return "<3"
            if _between(line, 3, 4.5):
                return "3-4.5"
            return "5+"
        if pt == "Saves":
            if line < 25:
                return "<25"
            if _between(line, 25, 34.5):
                return "25-34.5"
            return "35+"
        if pt == "Faceoffs":
            if line < 8:
                return "<8"
            if _between(line, 8, 11.5):
                return "8-11.5"
            return "12+"
        if pt == "Blocked Shots":
            if line < 1.5:
                return "<1.5"
            return "1.5+"

    if sport == "Soccer":
        if pt in ("Goals", "Assists"):
            if line < 0.5:
                return "<0.5"
            return "0.5+"
        if pt == "Shots":
            if line < 2:
                return "<2"
            if _between(line, 2, 3.5):
                return "2-3.5"
            return "4+"
        if pt == "Shots on Target":
            if line < 1:
                return "<1"
            return "1+"
        if pt == "Passes":
            if line < 30:
                return "<30"
            if _between(line, 30, 49.5):
                return "30-49.5"
            return "50+"
        if pt == "Tackles":
            if line < 2:
                return "<2"
            return "2+"

    return _bucket_generic(line)


def _grade_from_rate(hit_rate: float, decided_count: int) -> str:
    if decided_count < 5:
        return "?"
    if hit_rate >= 0.70 and decided_count >= 8:
        return "S"
    if hit_rate >= 0.62 and decided_count >= 5:
        return "A"
    if hit_rate >= 0.55 and decided_count >= 5:
        return "B"
    if hit_rate >= 0.47 and decided_count >= 5:
        return "C"
    if hit_rate >= 0.40 and decided_count >= 5:
        return "D"
    if hit_rate < 0.40 and decided_count >= 5:
        return "F"
    return "?"


def _trending(last_5_hr: float, last_20_hr: float, n: int) -> str:
    if n < 10:
        return "?"
    if last_5_hr > last_20_hr + 0.10:
        return "UP"
    if last_5_hr < last_20_hr - 0.10:
        return "DOWN"
    return "FLAT"


def _mean(vals: list[int]) -> float:
    return float(sum(vals) / len(vals)) if vals else 0.0


def _weighted_hit_rate(hits: list[int], weights: list[float], upto: int) -> float:
    k = min(upto, len(hits), len(weights))
    if k <= 0:
        return 0.0
    wh = sum(weights[i] * int(hits[i]) for i in range(k))
    return wh / float(k)


def simulate_f_recovery(chrono: list[int], weights: list[float] | None = None) -> tuple:
    if weights is None:
        weights = [1.0] * len(chrono)
    while len(weights) < len(chrono):
        weights.append(1.0)
    weights = weights[: len(chrono)]

    locked = False
    games_since_f = 0
    displayed = "?"
    history: list[int] = []

    for idx, hit in enumerate(chrono):
        history.append(int(hit))
        n = len(history)
        hit_rate = _weighted_hit_rate(history, weights, n)
        last5 = history[-5:]
        w5 = weights[max(0, n - 5) : n]
        l5hr = _weighted_hit_rate(last5, w5, len(last5))
        last20 = history[-20:]
        w20 = weights[max(0, n - 20) : n]
        l20hr = _weighted_hit_rate(last20, w20, len(last20))
        raw = _grade_from_rate(hit_rate, n)

        if locked:
            games_since_f += 1
            if games_since_f >= 3 and l5hr >= 0.50:
                new_g = _grade_from_rate(hit_rate, n)
                if new_g == "F":
                    games_since_f = 0
                    displayed = "F"
                else:
                    locked = False
                    games_since_f = 0
                    displayed = new_g
            else:
                displayed = "F"
        else:
            displayed = raw
            if raw == "F" and n >= 10:
                locked = True
                games_since_f = 0

    n = len(history)
    hit_rate = _weighted_hit_rate(history, weights, n) if n else 0.0
    hit_count = sum(weights[i] * int(history[i]) for i in range(n)) if n else 0.0
    last5 = history[-5:]
    last10 = history[-10:]
    last20 = history[-20:]
    w5 = weights[max(0, n - 5) : n]
    w10 = weights[max(0, n - 10) : n]
    w20 = weights[max(0, n - 20) : n]
    l5hr = _weighted_hit_rate(last5, w5, len(last5))
    l10hr = _weighted_hit_rate(last10, w10, len(last10))
    l20hr = _weighted_hit_rate(last20, w20, len(last20))
    trend = _trending(l5hr, l20hr, n)

    rev5 = list(reversed(last5))
    rev10 = list(reversed(last10))
    rev20 = list(reversed(last20))

    grade_locked = 1 if locked else 0
    final_grade = displayed if n else "?"

    return (
        final_grade,
        trend,
        grade_locked,
        games_since_f,
        hit_rate,
        l5hr,
        l20hr,
        json.dumps(rev5),
        json.dumps(rev10),
        json.dumps(rev20),
        n,
        hit_count,
        l10hr,
    )


def _parse_result(val: Any) -> int | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    t = str(val).strip().upper()
    if t in ("", "NAN", "NONE"):
        return None
    if t in ("HIT", "1", "TRUE", "YES", "W"):
        return 1
    if t in ("MISS", "0", "FALSE", "NO", "L"):
        return 0
    if "HIT" in t and "MISS" not in t:
        return 1
    if "MISS" in t:
        return 0
    if t in ("VOID", "PUSH", "TIE", "NA", "N/A"):
        return None
    return None


def _parse_tier(val: Any) -> str:
    t = str(val or "").strip().lower()
    if "gob" in t:
        return "Goblin"
    if "dem" in t:
        return "Demon"
    return "Standard"


def _parse_date(val: Any) -> pd.Timestamp | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        if isinstance(val, datetime):
            return pd.Timestamp(val)
        ts = pd.to_datetime(val, errors="coerce")
        if pd.isna(ts):
            return None
        return ts
    except Exception:
        return None


def _naive_ts_for_sort(val: Any) -> pd.Timestamp:
    """Tz-naive timestamp for stable sorting (mix of Excel / DB / entry-leg dates)."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return pd.Timestamp.min
    try:
        t = pd.Timestamp(val)
        if pd.isna(t):
            return pd.Timestamp.min
        if t.tzinfo is not None:
            return t.tz_convert("UTC").tz_localize(None)
        return t
    except Exception:
        return pd.Timestamp.min


def _graded_workbook_sheet_order(name: str) -> tuple[int, int]:
    """Sort key: 'graded props' first, then graded_props_1, graded_props_2, ..."""
    low = str(name).strip().lower()
    if low == "graded props":
        return (0, 0)
    if low.startswith("graded_props_"):
        suffix = low.split("_")[-1]
        try:
            return (1, int(suffix))
        except ValueError:
            return (1, 9999)
    return (2, 0)


def _read_graded_frame(path: Path) -> pd.DataFrame | None:
    try:
        xl = pd.ExcelFile(path, engine="openpyxl")
    except Exception:
        return None
    names = list(xl.sheet_names)
    if not names:
        return None

    # Multi-sheet synthetic exports (Excel 1,048,576 row cap per sheet)
    multi = [
        n
        for n in names
        if str(n).strip().lower() == "graded props" or str(n).strip().lower().startswith("graded_props_")
    ]
    if len(multi) > 1 or (len(multi) == 1 and str(multi[0]).strip().lower().startswith("graded_props_")):
        try:
            ordered = sorted(multi, key=_graded_workbook_sheet_order)
            parts = [pd.read_excel(path, sheet_name=n, engine="openpyxl") for n in ordered]
            if not parts:
                return None
            return pd.concat(parts, ignore_index=True)
        except Exception:
            return None

    sheet = None
    for name in names:
        if str(name).strip().lower() == "graded props":
            sheet = name
            break
    if sheet is None:
        sheet = names[0]
    try:
        return pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
    except Exception:
        return None


def _append_rows_from_graded_frame(
    df: pd.DataFrame,
    sport: str,
    since_ts: pd.Timestamp | None,
    weight: float,
    rows: list[dict[str, Any]],
) -> None:
    c_player = _find_column(df, "player", "player_name", "Player")
    c_prop = _find_column(df, "prop_type", "stat_type", "Prop Type", "prop type")
    c_line = _find_column(df, "line", "Line")
    c_dir = _find_column(df, "direction", "Direction")
    c_res = _find_column(df, "result", "Result")
    c_date = _find_column(df, "date", "created_at", "Date", "game_date")
    c_tier = _find_column(df, "tier", "Tier")
    c_weight = _find_column(df, "weight")

    if not all([c_player, c_prop, c_line, c_dir, c_res]):
        return

    cols = [c_player, c_prop, c_line, c_dir, c_res, c_date]
    if c_tier:
        cols.append(c_tier)
    if c_weight:
        cols.append(c_weight)
    try:
        sub = df.loc[:, cols]
    except KeyError:
        return

    # itertuples is far faster than iterrows() on 500k+ synthetic rows
    for tup in sub.itertuples(index=False, name=None):
        vals = list(tup)
        player_v = vals[0]
        prop_v = vals[1]
        line_v = vals[2]
        dir_v = vals[3]
        res_v = vals[4]
        date_v = vals[5]
        i = 6
        tier_v = vals[i] if c_tier else "Standard"
        if c_tier:
            i += 1
        row_w: float | None = None
        if c_weight:
            try:
                row_w = float(vals[i])
            except (TypeError, ValueError, IndexError):
                row_w = None
        use_weight = float(row_w) if row_w is not None else float(weight)
        hit = _parse_result(res_v)
        if hit is None:
            continue
        d = _parse_date(date_v)
        if since_ts is not None and d is not None and d < since_ts:
            continue
        try:
            line = float(line_v)
        except (TypeError, ValueError):
            continue
        direction = str(dir_v or "").strip().upper()
        if direction not in ("OVER", "UNDER"):
            continue
        prop_raw = str(prop_v or "").strip()
        pnorm = _normalize_prop_type(prop_raw, sport)
        bucket = get_line_bucket(pnorm, line, sport)
        player = _strip_ws(str(player_v or ""))
        if not player:
            continue
        tier = _parse_tier(tier_v)
        rows.append(
            {
                "player_name": player,
                "sport": sport,
                "prop_type": pnorm,
                "direction": direction,
                "line_bucket": bucket,
                "line": line,
                "hit": hit,
                "date": d,
                "tier": tier,
                "weight": use_weight,
            }
        )


def load_real_graded_rows(sport_filter: str | None, since: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    since_ts = pd.to_datetime(since, errors="coerce") if since else None

    for sport, pattern in GRADED_GLOBS:
        if sport_filter and sport.upper() != sport_filter.upper():
            continue
        out_dir = REPO_ROOT / "outputs"
        if not out_dir.is_dir():
            continue
        for path in out_dir.rglob(pattern):
            if not path.is_file():
                continue
            if "synthetic" in path.as_posix().lower():
                continue
            df = _read_graded_frame(path)
            if df is None or df.empty:
                continue
            _append_rows_from_graded_frame(df, sport, since_ts, 1.0, rows)
    return rows


def _sport_from_synthetic_filename(name: str) -> str | None:
    low = name.lower()
    if "_nba_" in low or low.startswith("graded_nba_"):
        return "NBA"
    if "_cbb_" in low or low.startswith("graded_cbb_"):
        return "CBB"
    if "_nhl_" in low or low.startswith("graded_nhl_"):
        return "NHL"
    if "_soccer_" in low or low.startswith("graded_soccer_"):
        return "Soccer"
    return None


def load_synthetic_from_db(
    db_path: str,
    sport: str | None = None,
    since: str | None = None,
) -> pd.DataFrame:
    p = Path(db_path)
    if not p.is_file():
        print("  Synthetic DB not found — skipping")
        return pd.DataFrame()

    conn = sqlite3.connect(str(p))
    try:
        query = "SELECT * FROM synthetic_graded_props WHERE 1=1"
        params: list[Any] = []
        if sport:
            query += " AND sport = ?"
            params.append(sport)
        if since:
            query += " AND game_date >= ?"
            params.append(since)
        try:
            df = pd.read_sql_query(query, conn, params=params)
        except Exception as e:
            print(f"  Synthetic DB read failed — skipping ({e})")
            df = pd.DataFrame()
    finally:
        conn.close()
    note = f" ({sport})" if sport else ""
    print(f"  Synthetic DB: {len(df)} rows loaded{note}")
    return df


def load_synthetic_graded_rows(sport_filter: str | None, since: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    since_ts = pd.to_datetime(since, errors="coerce") if since else None
    df = load_synthetic_from_db(str(SYNTHETIC_GRADED_DB), sport_filter, since)
    if df.empty:
        return rows
    for sport_val in df["sport"].dropna().unique():
        sport = str(sport_val).strip()
        if sport_filter and sport.upper() != sport_filter.upper():
            continue
        sub = df.loc[df["sport"].astype(str).str.strip() == sport].copy()
        if sub.empty:
            continue
        _append_rows_from_graded_frame(sub, sport, since_ts, SYNTHETIC_WEIGHT, rows)
    return rows


def load_entry_leg_result_rows(sport_filter: str | None, since: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    since_ts = pd.to_datetime(since, errors="coerce") if since else None
    dbp = _find_myticket_performance_db()
    if not dbp:
        return rows
    try:
        conn = sqlite3.connect(str(dbp))
        try:
            cur = conn.execute(
                """
                SELECT player_name, stat_type, line, direction, league, game_date, result
                FROM entry_leg_results
                WHERE result IN ('HIT', 'MISS')
                """
            )
            for player_name, stat_type, line, direction, league, game_date, result in cur.fetchall():
                sport = _league_to_sport(league)
                if not sport or not player_name or line is None:
                    continue
                if sport_filter and sport.upper() != sport_filter.upper():
                    continue
                d = _parse_date(game_date)
                if since_ts is not None and d is not None and d < since_ts:
                    continue
                direction_u = str(direction or "").strip().upper()
                if direction_u not in ("OVER", "UNDER"):
                    continue
                hit = _parse_result(result)
                if hit is None:
                    continue
                try:
                    ln = float(line)
                except (TypeError, ValueError):
                    continue
                pnorm = _normalize_prop_type(str(stat_type or ""), sport)
                bucket = get_line_bucket(pnorm, ln, sport)
                rows.append(
                    {
                        "player_name": _strip_ws(str(player_name)),
                        "sport": sport,
                        "prop_type": pnorm,
                        "direction": direction_u,
                        "line_bucket": bucket,
                        "line": ln,
                        "hit": hit,
                        "date": d,
                        "tier": "Standard",
                        "weight": 1.0,
                    }
                )
        finally:
            conn.close()
    except sqlite3.Error:
        return rows
    return rows


def load_all_graded_rows(
    sport_filter: str | None,
    since: str | None,
    sources: str = "real",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if sources in ("all", "real"):
        rows.extend(load_real_graded_rows(sport_filter, since))
    if sources in ("all", "synthetic"):
        rows.extend(load_synthetic_graded_rows(sport_filter, since))
    if sources == "all":
        rows.extend(load_entry_leg_result_rows(sport_filter, since))
    for r in rows:
        if "weight" not in r:
            r["weight"] = 1.0
    rows.sort(key=lambda x: (x["date"] is not None, _naive_ts_for_sort(x["date"])))
    return rows


def _dedupe_key(r: dict[str, Any]) -> tuple:
    ds = ""
    if r["date"] is not None:
        ds = r["date"].strftime("%Y-%m-%d")
    return (
        r["player_name"],
        r["sport"],
        r["prop_type"],
        r["direction"],
        r["line_bucket"],
        ds,
        round(float(r["line"]), 4),
    )


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        k = _dedupe_key(r)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS player_consistency")
    conn.execute(
        """
        CREATE TABLE player_consistency (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_name TEXT NOT NULL,
            sport TEXT NOT NULL,
            prop_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            line_bucket TEXT NOT NULL,
            decided_count INTEGER DEFAULT 0,
            hit_count INTEGER DEFAULT 0,
            hit_rate REAL DEFAULT 0.0,
            last_5_results TEXT,
            last_10_results TEXT,
            last_20_results TEXT,
            last_5_hit_rate REAL DEFAULT 0.0,
            last_10_hit_rate REAL DEFAULT 0.0,
            last_20_hit_rate REAL DEFAULT 0.0,
            trending TEXT DEFAULT '?',
            grade TEXT DEFAULT '?',
            grade_locked INTEGER DEFAULT 0,
            games_since_F INTEGER DEFAULT 0,
            first_seen TEXT,
            last_seen TEXT,
            last_updated TEXT,
            UNIQUE(player_name, sport, prop_type, direction, line_bucket)
        )
        """
    )


def upsert_cell(
    conn: sqlite3.Connection,
    player_name: str,
    sport: str,
    prop_type: str,
    direction: str,
    line_bucket: str,
    chrono_hits: list[int],
    dates: list[pd.Timestamp | None],
    weights: list[float] | None = None,
) -> None:
    (
        grade,
        trending,
        grade_locked,
        games_since_f,
        hit_rate,
        l5hr,
        l20hr,
        j5,
        j10,
        j20,
        decided_count,
        hit_count,
        l10hr,
    ) = simulate_f_recovery(chrono_hits, weights)

    first_seen = None
    last_seen = None
    for d in dates:
        if d is None:
            continue
        ds = d.strftime("%Y-%m-%d")
        if first_seen is None or ds < first_seen:
            first_seen = ds
        if last_seen is None or ds > last_seen:
            last_seen = ds

    now = _now_iso()
    conn.execute(
        """
        INSERT INTO player_consistency (
            player_name, sport, prop_type, direction, line_bucket,
            decided_count, hit_count, hit_rate,
            last_5_results, last_10_results, last_20_results,
            last_5_hit_rate, last_10_hit_rate, last_20_hit_rate,
            trending, grade, grade_locked, games_since_F,
            first_seen, last_seen, last_updated
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(player_name, sport, prop_type, direction, line_bucket) DO UPDATE SET
            decided_count = excluded.decided_count,
            hit_count = excluded.hit_count,
            hit_rate = excluded.hit_rate,
            last_5_results = excluded.last_5_results,
            last_10_results = excluded.last_10_results,
            last_20_results = excluded.last_20_results,
            last_5_hit_rate = excluded.last_5_hit_rate,
            last_10_hit_rate = excluded.last_10_hit_rate,
            last_20_hit_rate = excluded.last_20_hit_rate,
            trending = excluded.trending,
            grade = excluded.grade,
            grade_locked = excluded.grade_locked,
            games_since_F = excluded.games_since_F,
            first_seen = CASE
                WHEN player_consistency.first_seen IS NULL THEN excluded.first_seen
                WHEN excluded.first_seen IS NULL THEN player_consistency.first_seen
                WHEN excluded.first_seen < player_consistency.first_seen THEN excluded.first_seen
                ELSE player_consistency.first_seen
            END,
            last_seen = excluded.last_seen,
            last_updated = excluded.last_updated
        """,
        (
            player_name,
            sport,
            prop_type,
            direction,
            line_bucket,
            decided_count,
            hit_count,
            hit_rate,
            j5,
            j10,
            j20,
            l5hr,
            l10hr,
            l20hr,
            trending,
            grade,
            grade_locked,
            games_since_f,
            first_seen,
            last_seen,
            now,
        ),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Build player_consistency.db from graded workbooks.")
    ap.add_argument("--sport", default=None, help="NBA, CBB, NHL, or Soccer")
    ap.add_argument("--since", default=None, help="Only include graded rows on/after this date (YYYY-MM-DD)")
    ap.add_argument("--rebuild", action="store_true", help="Drop and recreate table from all history")
    ap.add_argument(
        "--sources",
        choices=("all", "real", "synthetic"),
        default="real",
        help="Data sources: real graded xlsx only (default), synthetic only, or all (real+synthetic+entry legs)",
    )
    args = ap.parse_args()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    rows = load_all_graded_rows(args.sport, args.since, args.sources)
    rows = dedupe_rows(rows)

    by_cell: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        key = (r["player_name"], r["sport"], r["prop_type"], r["direction"], r["line_bucket"])
        by_cell[key].append(r)

    conn = sqlite3.connect(str(DB_PATH))
    try:
        if args.rebuild:
            create_schema(conn)
        else:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS player_consistency (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_name TEXT NOT NULL,
                    sport TEXT NOT NULL,
                    prop_type TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    line_bucket TEXT NOT NULL,
                    decided_count INTEGER DEFAULT 0,
                    hit_count INTEGER DEFAULT 0,
                    hit_rate REAL DEFAULT 0.0,
                    last_5_results TEXT,
                    last_10_results TEXT,
                    last_20_results TEXT,
                    last_5_hit_rate REAL DEFAULT 0.0,
                    last_10_hit_rate REAL DEFAULT 0.0,
                    last_20_hit_rate REAL DEFAULT 0.0,
                    trending TEXT DEFAULT '?',
                    grade TEXT DEFAULT '?',
                    grade_locked INTEGER DEFAULT 0,
                    games_since_F INTEGER DEFAULT 0,
                    first_seen TEXT,
                    last_seen TEXT,
                    last_updated TEXT,
                    UNIQUE(player_name, sport, prop_type, direction, line_bucket)
                )
                """
            )

        players_updated: set[str] = set()
        cells_updated = 0
        grade_counts: dict[str, int] = defaultdict(int)

        for key, cell_rows in by_cell.items():
            cell_rows.sort(key=lambda x: (x["date"] is not None, x["date"] or pd.Timestamp.min))
            chrono = [int(x["hit"]) for x in cell_rows]
            wts = [float(x.get("weight", 1.0)) for x in cell_rows]
            dates = [x["date"] for x in cell_rows]
            player_name, sport, prop_type, direction, line_bucket = key

            upsert_cell(conn, player_name, sport, prop_type, direction, line_bucket, chrono, dates, wts)
            players_updated.add(player_name)
            cells_updated += 1

        conn.commit()

        cur = conn.execute("SELECT grade, COUNT(*) FROM player_consistency GROUP BY grade")
        for g, c in cur.fetchall():
            grade_counts[str(g or "?")] = int(c)

        print("Player consistency build complete.")
        print(f"  Sources: {args.sources}")
        print(f"  Players updated: {len(players_updated)}")
        print(f"  Cells updated: {cells_updated}")
        for label, gkey in [
            ("Grade S", "S"),
            ("Grade A", "A"),
            ("Grade B", "B"),
            ("Grade C", "C"),
            ("Grade D", "D"),
            ("Grade F", "F"),
            ("Grade ?", "?"),
        ]:
            n = grade_counts.get(gkey, 0)
            extra = " (BLACKLISTED)" if gkey == "F" else ""
            if gkey == "?":
                extra = " (insufficient sample)"
            print(f"  {label}: {n} cells{extra}")
        total_cells = sum(grade_counts.values())
        q_n = grade_counts.get("?", 0)
        if total_cells > 0 and (q_n / total_cells) > 0.80:
            print("\nDiagnostic: Grade ? is >80% of cells - top 20 by decided_count:")
            diag = conn.execute(
                """
                SELECT player_name, sport, prop_type, direction, line_bucket,
                       decided_count, hit_rate, grade
                FROM player_consistency
                ORDER BY decided_count DESC
                LIMIT 20
                """
            ).fetchall()
            print(
                f"  {'player':<24} {'sport':<6} {'prop':<14} {'dir':<6} "
                f"{'bucket':<12} {'n':>4} {'hr':>6} {'gr':>3}"
            )
            for row in diag:
                p, sp, pr, di, bk, dc, hr, gr = row
                print(
                    f"  {str(p)[:23]:<24} {str(sp)[:5]:<6} {str(pr)[:13]:<14} "
                    f"{str(di)[:5]:<6} {str(bk)[:11]:<12} {int(dc):4d} {float(hr):6.3f} {str(gr):>3}"
                )
        print(f"  Database: {DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
