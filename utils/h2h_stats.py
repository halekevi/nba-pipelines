"""
Head-to-head stat lookup from proporacle_ref.db (WNBA / Soccer).

MLB mlb_gamelog has no team column — use init_mlb_h2h_placeholder() in step5.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = _REPO_ROOT / "data" / "cache" / "proporacle_ref.db"

H2H_COLUMNS = ("h2h_avg", "h2h_over_pct", "h2h_games", "h2h_last")
MIN_H2H_GAMES = 2
_MAX_H2H_GAMES = 10

# Slate abbrev / name -> DB team code (keep in sync with sport step4 maps).
WNBA_TEAM_KEY_MAP: dict[str, str] = {
    "LAS": "LV",
    "LVA": "LV",
    "NYL": "NY",
    "CON": "CON",
    "DAL": "DAL",
    "IND": "IND",
    "PHX": "PHX",
    "SEA": "SEA",
    "CHI": "CHI",
    "ATL": "ATL",
    "MIN": "MIN",
    "WSH": "WSH",
    "POR": "POR",
    "GS": "GS",
}

SOCCER_TEAM_KEY_MAP: dict[str, str] = {
    "GIBRALTAR": "GIB",
    "ALBANIA": "ALB",
    "BOSNIA": "BIH",
    "CZECHIA": "CZE",
    "DENMARK": "DEN",
    "ITALY": "ITA",
    "KOSOVO": "KOS",
    "LATVIA": "LAT",
    "N. IRELAND": "NIR",
    "N. MACEDONIA": "MKD",
    "POLAND": "POL",
    "ROMANIA": "ROU",
    "SLOVAKIA": "SVK",
    "SWEDEN": "SWE",
    "UKRAINE": "UKR",
    "WALES": "WAL",
    "TÜRKIYE": "TUR",
    "REP. IRELAND": "IRL",
    "SAN LORENZO": "SLO",
    "ARGENTINOS": "ARGJ",
    "RIESTRA": "RIE",
    "LANÚS": "LAN",
    "CHICAGO STARS": "CHI",
    "KC CURRENT": "KC",
    "PRIDE": "ORL",
    "REIGN": "SEA",
    "ROYALS": "UTA",
    "SAN DIEGO WAVE": "SD",
    "SPIRIT": "WAS",
    "THORNS": "POR",
}

WNBA_PROP_TO_COL: dict[str, str] = {
    "Points": "pts",
    "Rebounds": "reb",
    "Assists": "ast",
    "Steals": "stl",
    "Blocks": "blk",
    "Turnovers": "tov",
    "3-Pt Made": "fg3m",
    "Free Throws Made": "ftm",
    "Fantasy Score": "fantasy_score",
    "Pts+Rebs+Asts": "pra",
    "Pts+Rebs": "pr",
    "Pts+Asts": "pa",
    "Rebs+Asts": "ra",
    "Reb+Ast": "ra",
    "Blks+Stls": "bs",
    "pts": "pts",
    "reb": "reb",
    "ast": "ast",
    "stl": "stl",
    "blk": "blk",
    "tov": "tov",
    "fg3m": "fg3m",
    "ftm": "ftm",
    "fantasy": "fantasy_score",
    "fantasy_score": "fantasy_score",
    "pra": "pra",
    "pr": "pr",
    "pa": "pa",
    "ra": "ra",
    "bs": "bs",
    "3ptmade": "fg3m",
    "threes": "fg3m",
}

SOCCER_PROP_TO_COL: dict[str, str] = {
    "shots": "sh",
    "shots on target": "sog",
    "shots on goal": "sog",
    "goals": "g",
    "assists": "a",
    "goalie saves": "sv",
    "goalkeeper saves": "sv",
    "passes": "pa",
    "passes attempted": "pa",
    "clearances": "clearances",
    "tackles": "tk",
    "attempted dribbles": "dribble_attempts",
    "shots assisted": "kp",
    "fouls": "fc",
    "sog": "sog",
    "g": "g",
    "a": "a",
    "sv": "sv",
    "pa": "pa",
    "kp": "kp",
    "tk": "tk",
    "fc": "fc",
}

SPORT_H2H_CONFIG: dict[str, dict[str, Any]] = {
    "WNBA": {
        "table": "wnba",
        "team_map": WNBA_TEAM_KEY_MAP,
        "prop_map": WNBA_PROP_TO_COL,
    },
    "Soccer": {
        "table": "soccer",
        "team_map": SOCCER_TEAM_KEY_MAP,
        "prop_map": SOCCER_PROP_TO_COL,
    },
}


def _norm_name(n: str) -> str:
    if not n or pd.isna(n):
        return ""
    s = unicodedata.normalize("NFD", str(n).strip().lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _norm_team(raw: str, team_map: dict[str, str]) -> str:
    t = str(raw or "").strip().upper()
    if not t:
        return ""
    return team_map.get(t, t)


def _resolve_stat_col(row: pd.Series, prop_map: dict[str, str]) -> str | None:
    for key in ("prop_norm", "prop_type"):
        raw = str(row.get(key, "") or "").strip()
        if not raw:
            continue
        col = prop_map.get(raw) or prop_map.get(raw.lower())
        if col:
            return col
    return None


def _parse_before_date(row: pd.Series) -> str:
    for key in ("game_date", "slate_game_date", "start_time", "fetched_at"):
        raw = str(row.get(key, "") or "").strip()
        if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
            return raw[:10]
        ts = pd.to_datetime(raw, utc=True, errors="coerce")
        if pd.notna(ts):
            return ts.strftime("%Y-%m-%d")
    return ""


def _get_h2h_games(
    con: sqlite3.Connection,
    *,
    table: str,
    player_norm: str,
    team: str,
    opp_team: str,
    stat_col: str,
    before_date: str = "",
) -> pd.DataFrame:
    """
    Games where player on `team` shared an event with `opp_team`.
    Uses event_id join — WNBA rows often have NULL home_team/away_team.
    """
    if not player_norm or not team or not opp_team or not stat_col:
        return pd.DataFrame()

    date_clause = "AND w1.game_date < ?" if (before_date and len(before_date) == 10) else ""
    params: list[Any] = [player_norm, team, opp_team]
    if date_clause:
        params.append(before_date)

    sql = f"""
        SELECT w1.game_date, w1.{stat_col}
        FROM {table} w1
        WHERE lower(w1.player) = ?
          AND upper(w1.team) = ?
          AND EXISTS (
              SELECT 1 FROM {table} w2
              WHERE w2.event_id = w1.event_id
                AND w2.game_date = w1.game_date
                AND upper(w2.team) = ?
          )
          {date_clause}
        ORDER BY w1.game_date ASC
    """
    try:
        rows = con.execute(sql, params).fetchall()
    except Exception:
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["game_date", stat_col])
    df[stat_col] = pd.to_numeric(df[stat_col], errors="coerce")
    return df.drop_duplicates(subset=["game_date"], keep="last")


def compute_h2h_stats(
    player: str,
    team: str,
    opp_team: str,
    prop_type: str,
    line: float | None,
    *,
    con: sqlite3.Connection,
    table: str,
    team_map: dict[str, str],
    prop_map: dict[str, str],
    before_date: str = "",
    prop_norm: str = "",
) -> dict[str, Any]:
    """Return h2h_avg / h2h_over_pct / h2h_games / h2h_last (None if < MIN_H2H_GAMES)."""
    empty = {c: None for c in H2H_COLUMNS}
    player_norm = _norm_name(player)
    db_team = _norm_team(team, team_map)
    db_opp = _norm_team(opp_team, team_map)
    pseudo = pd.Series({"prop_type": prop_type, "prop_norm": prop_norm or prop_type})
    stat_col = _resolve_stat_col(pseudo, prop_map)
    if not player_norm or not db_team or not db_opp or not stat_col:
        return empty

    games = _get_h2h_games(
        con,
        table=table,
        player_norm=player_norm,
        team=db_team,
        opp_team=db_opp,
        stat_col=stat_col,
        before_date=before_date,
    )
    if games.empty or len(games) < MIN_H2H_GAMES:
        return empty

    recent = games.tail(_MAX_H2H_GAMES)
    vals = recent[stat_col].dropna()
    if vals.empty:
        return empty

    result = dict(empty)
    result["h2h_games"] = len(recent)
    result["h2h_avg"] = round(float(vals.mean()), 2)
    result["h2h_last"] = recent.iloc[-1][stat_col]
    if line is not None and pd.notna(line):
        try:
            result["h2h_over_pct"] = round(float((vals > float(line)).mean()), 4)
        except (TypeError, ValueError):
            pass
    return result


def attach_h2h_columns(
    df: pd.DataFrame,
    sport: str,
    *,
    db_path: Path | str | None = None,
    player_col: str = "player",
    team_col: str = "team",
    opp_col: str = "opp_team",
    line_col: str = "line",
) -> pd.DataFrame:
    """Attach H2H columns from proporacle_ref.db. Never raises."""
    out = df.copy()
    for col in H2H_COLUMNS:
        if col not in out.columns:
            out[col] = None

    cfg = SPORT_H2H_CONFIG.get(sport)
    if cfg is None or out.empty:
        return out

    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        print(f"[H2H] {sport}: DB missing at {path} — skipping")
        return out

    try:
        con = sqlite3.connect(path)
    except Exception as exc:
        print(f"[H2H] {sport}: DB open failed — {exc}")
        return out

    avgs: list[Any] = []
    overs: list[Any] = []
    games: list[Any] = []
    lasts: list[Any] = []

    for _, row in out.iterrows():
        line_val = pd.to_numeric(row.get(line_col), errors="coerce")
        line = float(line_val) if pd.notna(line_val) else None
        stats = compute_h2h_stats(
            str(row.get(player_col, "") or ""),
            str(row.get(team_col, "") or ""),
            str(row.get(opp_col, "") or ""),
            str(row.get("prop_type", "") or ""),
            line,
            con=con,
            table=cfg["table"],
            team_map=cfg["team_map"],
            prop_map=cfg["prop_map"],
            before_date=_parse_before_date(row),
            prop_norm=str(row.get("prop_norm", "") or ""),
        )
        avgs.append(stats["h2h_avg"])
        overs.append(stats["h2h_over_pct"])
        games.append(stats["h2h_games"])
        lasts.append(stats["h2h_last"])

    con.close()
    out["h2h_avg"] = avgs
    out["h2h_over_pct"] = overs
    out["h2h_games"] = games
    out["h2h_last"] = lasts
    return out


def init_mlb_h2h_placeholder(df: pd.DataFrame) -> pd.DataFrame:
    """
    MLB H2H not wired — mlb_gamelog has game_id but no team/opponent column.
    TODO: H2H requires opp column in mlb_gamelog DB table.
    """
    out = df.copy()
    for col in H2H_COLUMNS:
        out[col] = None
    print("[H2H] MLB: skipped — mlb_gamelog has no team column (TODO: boxscore join)")
    return out


def print_h2h_stats(df: pd.DataFrame, sport: str) -> None:
    total = len(df)
    if total == 0 or "h2h_games" not in df.columns:
        print(f"[H2H] {sport}: 0/{total} rows with h2h_games > 0")
        return
    filled = int(pd.to_numeric(df["h2h_games"], errors="coerce").fillna(0).gt(0).sum())
    print(f"[H2H] {sport}: {filled}/{total} rows with h2h_games > 0")
