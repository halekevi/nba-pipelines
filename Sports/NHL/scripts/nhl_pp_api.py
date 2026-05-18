#!/usr/bin/env python3
"""NHL Stats API — power-play TOI and related skater splits (no API key)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

NHL_STATS = "https://api.nhle.com/stats/rest/en"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
TIMEOUT = 15
SLEEP_S = 0.35

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_CACHE = _DATA_DIR / "nhl_pp_skater_cache.csv"


def season_id_from_year(year: int) -> int:
    """Calendar year of season start -> NHL seasonId (e.g. 2025 -> 20252026)."""
    y = int(year)
    return y * 10000 + (y + 1)


def current_season_id() -> int:
    now = datetime.now(timezone.utc)
    start_year = now.year if now.month >= 7 else now.year - 1
    return season_id_from_year(start_year)


def _get(url: str) -> Optional[dict]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def fetch_skater_powerplay(season_id: int, start: int = 0, limit: int = 100) -> list[dict]:
    cayenne = f"seasonId={season_id} and gameTypeId=2"
    url = (
        f"{NHL_STATS}/skater/powerplay"
        f"?cayenneExp={requests.utils.quote(cayenne)}&start={start}&limit={limit}"
    )
    time.sleep(SLEEP_S)
    data = _get(url)
    return (data or {}).get("data") or []


def fetch_all_powerplay(season_id: int) -> pd.DataFrame:
    rows: list[dict] = []
    start = 0
    limit = 100
    while True:
        batch = fetch_skater_powerplay(season_id, start=start, limit=limit)
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < limit:
            break
        start += limit
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["season_id"] = season_id
    df["fetched_at"] = datetime.now(timezone.utc).isoformat()
    keep = [
        "playerId", "skaterFullName", "teamAbbrevs", "positionCode", "gamesPlayed",
        "ppTimeOnIce", "ppTimeOnIcePerGame", "ppTimeOnIcePctPerGame",
        "ppGoals", "ppPoints", "ppShots", "ppPointsPer60",
        "season_id", "fetched_at",
    ]
    cols = [c for c in keep if c in df.columns]
    return df[cols].copy()


def load_pp_cache(path: Path = DEFAULT_CACHE) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


def save_pp_cache(df: pd.DataFrame, path: Path = DEFAULT_CACHE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.csv")
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    tmp.replace(path)


def refresh_pp_cache(season_id: Optional[int] = None, path: Path = DEFAULT_CACHE) -> pd.DataFrame:
    sid = int(season_id or current_season_id())
    fresh = fetch_all_powerplay(sid)
    if fresh.empty:
        return load_pp_cache(path)
    existing = load_pp_cache(path)
    if not existing.empty and "season_id" in existing.columns:
        existing = existing[existing["season_id"].astype(int) != sid]
        combined = pd.concat([existing, fresh], ignore_index=True)
    else:
        combined = fresh
    save_pp_cache(combined, path)
    return combined


def pp_unit_tier(pp_toi_pg: float) -> str:
    if pp_toi_pg >= 2.5:
        return "PP1"
    if pp_toi_pg >= 1.0:
        return "PP2"
    if pp_toi_pg >= 0.25:
        return "PP_FRINGE"
    return "NO_PP"
