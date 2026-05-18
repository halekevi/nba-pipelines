#!/usr/bin/env python3
"""NBA Stats API client (stats.nba.com) — usage, pace, positional defense."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests

NBA_STATS = "https://stats.nba.com/stats"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nba.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Connection": "keep-alive",
    "Origin": "https://www.nba.com",
}
TIMEOUT = 10
SLEEP_S = 0.5

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
USAGE_CACHE = _DATA_DIR / "nba_usage_cache.json"
PACE_CACHE = _DATA_DIR / "nba_team_pace_cache.json"
OPP_DEF_CACHE = _DATA_DIR / "nba_opp_defense_by_position.json"

TEAM_ALIAS = {
    "BRK": "BKN",
    "GS": "GSW",
    "NO": "NOP",
    "NOR": "NOP",
    "NY": "NYK",
    "SA": "SAS",
    "PHO": "PHX",
}


def norm_team(abbr: object) -> str:
    s = str(abbr or "").strip().upper()
    if not s or s == "NAN":
        return ""
    return TEAM_ALIAS.get(s, s)


def _get(path: str, params: dict) -> Optional[dict]:
    url = f"{NBA_STATS}/{path.lstrip('/')}"
    try:
        time.sleep(SLEEP_S)
        r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _result_set_to_df(payload: dict, index: int = 0) -> pd.DataFrame:
    sets = payload.get("resultSets") or payload.get("resultSet") or []
    if not sets:
        return pd.DataFrame()
    block = sets[index] if isinstance(sets, list) else sets
    headers = block.get("headers") or []
    rows = block.get("rowSet") or []
    if not headers or not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=headers)


def _cache_stale(entry: dict, hours: float) -> bool:
    ts = entry.get("fetched_at")
    if not ts:
        return True
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt > timedelta(hours=hours)
    except Exception:
        return True


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(path)


def fetch_player_advanced(season: str) -> pd.DataFrame:
    data = _get(
        "leaguedashplayerstats",
        {
            "Season": season,
            "SeasonType": "Regular Season",
            "PerMode": "PerGame",
            "MeasureType": "Advanced",
            "LeagueID": "00",
        },
    )
    return _result_set_to_df(data or {}, 0) if data else pd.DataFrame()


def fetch_player_base(season: str) -> pd.DataFrame:
    data = _get(
        "leaguedashplayerstats",
        {
            "Season": season,
            "SeasonType": "Regular Season",
            "PerMode": "PerGame",
            "MeasureType": "Base",
            "LeagueID": "00",
        },
    )
    return _result_set_to_df(data or {}, 0) if data else pd.DataFrame()


def fetch_team_pace(season: str) -> pd.DataFrame:
    data = _get(
        "leaguedashteamstats",
        {
            "Season": season,
            "SeasonType": "Regular Season",
            "PerMode": "Per100Possessions",
            "MeasureType": "Advanced",
            "LeagueID": "00",
        },
    )
    return _result_set_to_df(data or {}, 0) if data else pd.DataFrame()


def fetch_team_opponent_base(season: str) -> pd.DataFrame:
    data = _get(
        "leaguedashteamstats",
        {
            "Season": season,
            "SeasonType": "Regular Season",
            "PerMode": "PerGame",
            "MeasureType": "Opponent",
            "LeagueID": "00",
        },
    )
    return _result_set_to_df(data or {}, 0) if data else pd.DataFrame()


def refresh_usage_cache(season: str, path: Path = USAGE_CACHE) -> dict:
    adv = fetch_player_advanced(season)
    base = fetch_player_base(season)
    cache = _load_json(path)
    key = f"season_{season}"
    if adv.empty:
        return cache

    base_by_id: dict[str, dict] = {}
    if not base.empty and "PLAYER_ID" in base.columns:
        for _, r in base.iterrows():
            pid = str(r.get("PLAYER_ID", "")).strip()
            if pid:
                base_by_id[pid] = r.to_dict()

    players: dict[str, dict] = {}
    for _, r in adv.iterrows():
        pid = str(r.get("PLAYER_ID", "")).strip()
        name = str(r.get("PLAYER_NAME", "")).strip()
        team = norm_team(r.get("TEAM_ABBREVIATION", r.get("TEAM_ABBREV", "")))
        if not pid or not name:
            continue
        b = base_by_id.get(pid, {})
        players[pid] = {
            "player_id": pid,
            "player_name": name,
            "team": team,
            "usage_pct": _f(r.get("USG_PCT")),
            "min_per_game": _f(r.get("MIN")),
            "pie": _f(r.get("PIE")),
            "reb_pct": _f(b.get("REB_PCT", r.get("REB_PCT"))),
            "ast_pct": _f(b.get("AST_PCT", r.get("AST_PCT"))),
            "stl_pct": _f(b.get("STL_PCT")),
            "blk_pct": _f(b.get("BLK_PCT")),
            "position": str(r.get("PLAYER_POSITION", b.get("PLAYER_POSITION", ""))).strip(),
        }

    cache[key] = {"fetched_at": datetime.now(timezone.utc).isoformat(), "players": players}
    _save_json(path, cache)
    return cache


def refresh_pace_cache(season: str, path: Path = PACE_CACHE) -> dict:
    df = fetch_team_pace(season)
    cache = _load_json(path)
    key = f"season_{season}"
    if df.empty:
        return cache
    teams: dict[str, dict] = {}
    for _, r in df.iterrows():
        abbr = norm_team(r.get("TEAM_ABBREVIATION", r.get("TEAM_ABBREV", "")))
        if not abbr:
            continue
        teams[abbr] = {
            "team_name": str(r.get("TEAM_NAME", "")),
            "pace": _f(r.get("PACE")),
            "def_rating": _f(r.get("DEF_RATING")),
            "off_rating": _f(r.get("OFF_RATING")),
        }
    cache[key] = {"fetched_at": datetime.now(timezone.utc).isoformat(), "teams": teams}
    _save_json(path, cache)
    return cache


def refresh_opp_defense_cache(season: str, path: Path = OPP_DEF_CACHE) -> dict:
    """Team opponent per-game stats; replicated across G/F/C position keys."""
    df = fetch_team_opponent_base(season)
    cache = _load_json(path)
    key = f"season_{season}"
    if df.empty:
        return cache

    entries: dict[str, dict] = {}
    for _, r in df.iterrows():
        abbr = norm_team(r.get("TEAM_ABBREVIATION", r.get("TEAM_ABBREV", "")))
        if not abbr:
            continue
        pts = _f(r.get("OPP_PTS", r.get("PTS")))
        reb = _f(r.get("OPP_REB", r.get("REB")))
        ast = _f(r.get("OPP_AST", r.get("AST")))
        for pos in ("Guard", "Forward", "Center"):
            entries[f"{abbr}_{pos}_{season}"] = {
                "team": abbr,
                "position_group": pos,
                "pts_allowed": pts,
                "reb_allowed": reb,
                "ast_allowed": ast,
            }

    cache[key] = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
        "source": "leaguedashteamstats_opponent",
    }
    _save_json(path, cache)
    return cache


def ensure_caches(season: str, max_age_hours: float = 24.0) -> tuple[dict, dict, dict]:
    usage = _load_json(USAGE_CACHE)
    pace = _load_json(PACE_CACHE)
    opp = _load_json(OPP_DEF_CACHE)
    key = f"season_{season}"
    if key not in usage or _cache_stale(usage.get(key, {}), max_age_hours):
        usage = refresh_usage_cache(season)
    if key not in pace or _cache_stale(pace.get(key, {}), max_age_hours):
        pace = refresh_pace_cache(season)
    if key not in opp or _cache_stale(opp.get(key, {}), 48.0):
        opp = refresh_opp_defense_cache(season)
    return usage, pace, opp


def _f(val: object) -> Optional[float]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def usage_tier(usage_pct: Optional[float]) -> str:
    if usage_pct is None:
        return "medium"
    u = float(usage_pct)
    if u > 1.0:
        u /= 100.0
    if u >= 0.28:
        return "star"
    if u >= 0.22:
        return "high"
    if u >= 0.16:
        return "medium"
    return "role"


def derive_usage_role_type(
    usage_pct: Optional[float],
    ast_pct: Optional[float],
    reb_pct: Optional[float],
) -> str:
    ast = float(ast_pct) if ast_pct is not None else 0.0
    reb = float(reb_pct) if reb_pct is not None else 0.0
    usg = float(usage_pct) if usage_pct is not None else 0.0
    if usg > 1.0:
        usg /= 100.0
    if ast >= 0.25:
        return "playmaker"
    if reb >= 0.18:
        return "rebounder"
    if usg >= 0.25:
        return "scorer"
    return "role_player"


def nba_pace_context(game_pace: Optional[float]) -> str:
    if game_pace is None:
        return "medium_pace"
    if game_pace >= 101:
        return "high_pace"
    if game_pace < 97:
        return "low_pace"
    return "medium_pace"


def position_group_from_pos(pos: object) -> str:
    s = str(pos or "").strip().upper()
    if not s or s == "NAN":
        return "Forward"
    if s.startswith("C") or " C" in f" {s}" or "-C" in s:
        return "Center"
    if any(x in s for x in ("PG", "SG", " G", "G-", "G/")) or s in ("G", "GUARD"):
        return "Guard"
    if any(x in s for x in ("PF", "SF", " F", "F-", "F/")) or s in ("F", "FORWARD"):
        return "Forward"
    return "Forward"


def positional_matchup_tier(
    prop_norm: str,
    allowed: Optional[float],
    league_vals: list[float],
) -> str:
    if allowed is None or len(league_vals) < 10:
        return "neutral"
    prop = str(prop_norm or "").lower()
    vals = sorted(league_vals)
    n = len(vals)
    top10 = vals[int(n * 0.9) - 1] if n >= 10 else vals[-1]
    bot10 = vals[int(n * 0.1)] if n >= 10 else vals[0]
    if prop in ("pts", "points", "fantasy", "pra", "pr", "pa"):
        if allowed >= top10:
            return "favorable"
        if allowed <= bot10:
            return "unfavorable"
    elif prop in ("reb", "rebounds", "rebs"):
        if allowed >= top10:
            return "favorable"
        if allowed <= bot10:
            return "unfavorable"
    elif prop in ("ast", "assists"):
        if allowed >= top10:
            return "favorable"
        if allowed <= bot10:
            return "unfavorable"
    return "neutral"
