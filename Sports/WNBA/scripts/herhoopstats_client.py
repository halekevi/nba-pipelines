#!/usr/bin/env python3
"""Her Hoop Stats scraper (supplemental advanced metrics)."""

from __future__ import annotations

import io
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

log = logging.getLogger("wnba.herhoopstats")

HHS_URLS = (
    "https://herhoopstats.com/stats/wnba/league/2025/",
    "https://herhoopstats.com/stats/wnba/player_stats/stats/",
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}
TIMEOUT = 10

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
HHS_CACHE = _DATA_DIR / "herhoopstats_cache.json"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import json
        with path.open(encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_json(path: Path, data: dict) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(path)


def _cache_stale(entry: dict, hours: float = 48.0) -> bool:
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


def fetch_player_table() -> Optional[pd.DataFrame]:
    last_status: int | None = None
    for url in HHS_URLS:
        try:
            time.sleep(0.5)
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            last_status = r.status_code
            if r.status_code != 200:
                continue
            if "Just a moment" in r.text[:500] or "cloudflare" in r.text[:800].lower():
                log.warning("HerHoopStats blocked — falling back to WNBA API usage%%")
                return None
            tables = pd.read_html(io.StringIO(r.text))
            for tbl in tables:
                cols = [str(c).strip().lower() for c in tbl.columns]
                if any("player" in c for c in cols):
                    df = tbl.copy()
                    df.columns = [str(c).strip() for c in df.columns]
                    return df
        except Exception as exc:
            log.warning("HerHoopStats fetch failed (%s): %s", url, exc)
    if last_status is not None:
        log.warning("HerHoopStats HTTP %s — falling back to WNBA API usage%%", last_status)
    return None


def refresh_cache(season: str = "") -> dict:
    df = fetch_player_table()
    cache = _load_json(HHS_CACHE)
    key = season or "latest"
    if df is None or df.empty:
        return cache
    players: dict[str, dict] = {}
    name_col = next((c for c in df.columns if "player" in c.lower()), df.columns[0])
    team_col = next((c for c in df.columns if "team" in c.lower()), None)
    efg_col = next((c for c in df.columns if "efg" in c.lower().replace(" ", "")), None)
    ts_col = next((c for c in df.columns if c.lower() in ("ts%", "ts_pct", "ts") or "true" in c.lower()), None)
    per_col = next((c for c in df.columns if c.lower() in ("per", "per_rating")), None)

    for _, row in df.iterrows():
        name = str(row.get(name_col, "")).strip()
        if not name or name.lower() == "nan":
            continue
        entry: dict = {"player_name": name}
        if team_col:
            entry["team"] = str(row.get(team_col, "")).strip()
        if efg_col is not None:
            entry["hhs_efg_pct"] = row.get(efg_col)
        if ts_col is not None:
            entry["hhs_ts_pct"] = row.get(ts_col)
        if per_col is not None:
            entry["hhs_per"] = row.get(per_col)
        players[name.lower()] = entry

    cache[key] = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "players": players,
    }
    _save_json(HHS_CACHE, cache)
    return cache


def load_player_index(season: str = "", max_age_hours: float = 48.0) -> dict[str, dict]:
    cache = _load_json(HHS_CACHE)
    key = season or "latest"
    block = cache.get(key, {})
    if not block or _cache_stale(block, max_age_hours):
        cache = refresh_cache(season)
        block = cache.get(key, {})
    return block.get("players") or {}
