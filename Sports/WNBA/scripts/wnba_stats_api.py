#!/usr/bin/env python3
"""WNBA Stats API client (stats.wnba.com) — usage, pace, fouls."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests

WNBA_STATS = "https://stats.wnba.com/stats"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.wnba.com/",
    "Accept": "application/json",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Origin": "https://www.wnba.com",
}
TIMEOUT = 10
SLEEP_S = 0.5

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
USAGE_CACHE = _DATA_DIR / "wnba_usage_cache.json"
PACE_CACHE = _DATA_DIR / "wnba_team_pace_cache.json"
FOUL_CACHE = _DATA_DIR / "wnba_foul_cache.json"


def _get(path: str, params: dict) -> Optional[dict]:
    url = f"{WNBA_STATS}/{path.lstrip('/')}"
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
    params = {
        "Season": season,
        "SeasonType": "Regular Season",
        "PerMode": "PerGame",
        "MeasureType": "Advanced",
        "LeagueId": "10",
    }
    data = _get("leaguedashplayerstats", params)
    if not data:
        return pd.DataFrame()
    return _result_set_to_df(data, 0)


def fetch_player_base(season: str) -> pd.DataFrame:
    params = {
        "Season": season,
        "SeasonType": "Regular Season",
        "PerMode": "PerGame",
        "MeasureType": "Base",
        "LeagueId": "10",
    }
    data = _get("leaguedashplayerstats", params)
    if not data:
        return pd.DataFrame()
    return _result_set_to_df(data, 0)


def fetch_team_pace(season: str) -> pd.DataFrame:
    params = {
        "Season": season,
        "SeasonType": "Regular Season",
        "PerMode": "Per40",
        "MeasureType": "Advanced",
        "LeagueId": "10",
    }
    data = _get("leaguedashteamstats", params)
    if not data:
        return pd.DataFrame()
    return _result_set_to_df(data, 0)


def refresh_usage_cache(season: str, path: Path = USAGE_CACHE) -> dict:
    df = fetch_player_advanced(season)
    cache = _load_json(path)
    key = f"season_{season}"
    if df.empty:
        return cache
    players: dict[str, dict] = {}
    for _, r in df.iterrows():
        name = str(r.get("PLAYER_NAME", "")).strip()
        team = str(r.get("TEAM_ABBREVIATION", r.get("TEAM_ABBREV", ""))).strip().upper()
        if not name:
            continue
        usg = r.get("USG_PCT")
        mn = r.get("MIN")
        players[f"{name}|{team}"] = {
            "player_name": name,
            "team": team,
            "usage_pct": float(usg) if usg is not None and str(usg) != "" else None,
            "min_per_game": float(mn) if mn is not None and str(mn) != "" else None,
        }
    cache[key] = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "players": players,
    }
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
        abbr = str(r.get("TEAM_ABBREVIATION", r.get("TEAM_ABBREV", ""))).strip().upper()
        if not abbr:
            continue
        teams[abbr] = {
            "team_name": str(r.get("TEAM_NAME", "")),
            "pace": float(r["PACE"]) if r.get("PACE") is not None else None,
        }
    cache[key] = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "teams": teams,
    }
    _save_json(path, cache)
    return cache


def refresh_foul_cache(season: str, path: Path = FOUL_CACHE) -> dict:
    df = fetch_player_base(season)
    cache = _load_json(path)
    key = f"season_{season}"
    if df.empty:
        return cache
    players: dict[str, dict] = {}
    for _, r in df.iterrows():
        name = str(r.get("PLAYER_NAME", "")).strip()
        team = str(r.get("TEAM_ABBREVIATION", r.get("TEAM_ABBREV", ""))).strip().upper()
        if not name:
            continue
        players[f"{name}|{team}"] = {
            "player_name": name,
            "team": team,
            "pf": float(r["PF"]) if r.get("PF") is not None else None,
            "min": float(r["MIN"]) if r.get("MIN") is not None else None,
        }
    cache[key] = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "players": players,
    }
    _save_json(path, cache)
    return cache


def ensure_caches(season: str, max_age_hours: float = 24.0) -> tuple[dict, dict, dict]:
    usage = _load_json(USAGE_CACHE)
    pace = _load_json(PACE_CACHE)
    foul = _load_json(FOUL_CACHE)
    key = f"season_{season}"
    if key not in usage or _cache_stale(usage.get(key, {}), max_age_hours):
        usage = refresh_usage_cache(season)
    if key not in pace or _cache_stale(pace.get(key, {}), max_age_hours):
        pace = refresh_pace_cache(season)
    if key not in foul or _cache_stale(foul.get(key, {}), max_age_hours):
        foul = refresh_foul_cache(season)
    return usage, pace, foul


def usage_tier(usage_pct: Optional[float]) -> str:
    if usage_pct is None:
        return "medium"
    u = float(usage_pct)
    if u >= 0.25:
        return "high"
    if u >= 0.18:
        return "medium"
    return "low"


def foul_trouble_risk(pf: Optional[float], minutes: Optional[float]) -> str:
    if pf is None or minutes is None or minutes <= 0:
        return "medium"
    rate = (float(pf) / float(minutes)) * 36.0
    if rate >= 4.5:
        return "high"
    if rate >= 3.0:
        return "medium"
    return "low"


def pace_context(team_pace: Optional[float], opp_pace: Optional[float]) -> str:
    vals = [v for v in (team_pace, opp_pace) if v is not None]
    if not vals:
        return "medium"
    avg = sum(vals) / len(vals)
    if avg >= 100:
        return "high_pace"
    if avg < 94:
        return "low_pace"
    return "medium"
