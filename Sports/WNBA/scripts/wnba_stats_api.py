#!/usr/bin/env python3
"""WNBA Stats API client — usage, pace, fouls via nba_api (LeagueId=10).

Direct requests to stats.wnba.com return Akamai 403/500 from typical server IPs.
The NBA fix uses nba_api's shared STATS_HEADERS session against stats.nba.com;
WNBA uses the same endpoints with league_id_nullable='10' (same JSON shape as before).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from nba_api.stats.endpoints import leaguedashplayerstats, leaguedashteamstats
from nba_api.stats.static import teams as static_teams

WNBA_LEAGUE_ID = "10"
TIMEOUT = 45
SLEEP_S = 0.5

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
USAGE_CACHE = _DATA_DIR / "wnba_usage_cache.json"
PACE_CACHE = _DATA_DIR / "wnba_team_pace_cache.json"
FOUL_CACHE = _DATA_DIR / "wnba_foul_cache.json"


def _wnba_team_id_to_abbr() -> dict[int, str]:
    return {int(t["id"]): str(t["abbreviation"]).strip().upper() for t in static_teams.get_wnba_teams()}


def _team_abbr_from_row(row: pd.Series, id_to_abbr: dict[int, str]) -> str:
    abbr = str(row.get("TEAM_ABBREVIATION", row.get("TEAM_ABBREV", ""))).strip().upper()
    if abbr:
        return abbr
    try:
        return id_to_abbr.get(int(row.get("TEAM_ID")), "")
    except (TypeError, ValueError):
        return ""


def _dash_player_df(season: str, measure_type: str) -> pd.DataFrame:
    time.sleep(SLEEP_S)
    try:
        ep = leaguedashplayerstats.LeagueDashPlayerStats(
            season=season,
            season_type_all_star="Regular Season",
            per_mode_detailed="PerGame",
            measure_type_detailed_defense=measure_type,
            league_id_nullable=WNBA_LEAGUE_ID,
            timeout=TIMEOUT,
        )
        frames = ep.get_data_frames()
        return frames[0] if frames else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _dash_team_df(season: str, per_mode: str, measure_type: str) -> pd.DataFrame:
    time.sleep(SLEEP_S)
    try:
        ep = leaguedashteamstats.LeagueDashTeamStats(
            season=season,
            season_type_all_star="Regular Season",
            per_mode_detailed=per_mode,
            measure_type_detailed_defense=measure_type,
            league_id_nullable=WNBA_LEAGUE_ID,
            timeout=TIMEOUT,
        )
        frames = ep.get_data_frames()
        return frames[0] if frames else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


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
    return _dash_player_df(season, "Advanced")


def fetch_player_base(season: str) -> pd.DataFrame:
    return _dash_player_df(season, "Base")


def fetch_team_pace(season: str) -> pd.DataFrame:
    return _dash_team_df(season, "Per40", "Advanced")


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
    id_to_abbr = _wnba_team_id_to_abbr()
    for _, r in df.iterrows():
        abbr = _team_abbr_from_row(r, id_to_abbr)
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
