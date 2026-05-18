#!/usr/bin/env python3
"""
Natural Stat Trick client (data.naturalstattrick.com).

Requires free NST access key: set NST_ACCESS_KEY or NST_KEY in the environment.
Caches parsed tables under Sports/NHL/data/ — never deletes prior seasons.
"""

from __future__ import annotations

import io
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

log = logging.getLogger("nhl.nst")

NST_DATA = "https://data.naturalstattrick.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (PropORACLE/1.0)"}
TIMEOUT = 20
SLEEP_S = 0.4

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
LINE_CACHE = _DATA_DIR / "nst_line_combos_cache.csv"
PLAYER_PP_CACHE = _DATA_DIR / "nst_player_pp_cache.csv"


def nst_key() -> str:
    return (os.environ.get("NST_ACCESS_KEY") or os.environ.get("NST_KEY") or "").strip()


def _season_param(season_id: int) -> str:
    """NHL seasonId 20242025 -> NST fromseason 20242025."""
    return str(int(season_id))


def fetch_html(path: str, params: dict) -> Optional[str]:
    key = nst_key()
    if not key:
        log.warning("NST_ACCESS_KEY not set — skipping live NST fetch")
        return None
    q = dict(params)
    q["key"] = key
    url = f"{NST_DATA}/{path.lstrip('/')}"
    try:
        time.sleep(SLEEP_S)
        r = requests.get(url, params=q, headers={**HEADERS, "nst-key": key}, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning("NST HTTP %s for %s", r.status_code, path)
            return None
        if "Just a moment" in r.text[:800]:
            log.warning("NST Cloudflare challenge — check access key or rate limits")
            return None
        return r.text
    except Exception as exc:
        log.warning("NST fetch failed: %s", exc)
        return None


def parse_tables(html: str) -> list[pd.DataFrame]:
    if not html:
        return []
    try:
        return pd.read_html(io.StringIO(html))
    except Exception as exc:
        log.warning("NST table parse failed: %s", exc)
        return []


def fetch_line_combos(season_id: int, team: str = "all", sit: str = "5v5") -> pd.DataFrame:
    """
  sit: 5v5 | pp | etc. (NST sit codes)
  lines: pair | trio | all
    """
    params = {
        "fromseason": _season_param(season_id),
        "thruseason": _season_param(season_id),
        "stype": "2",
        "sit": sit,
        "score": "all",
        "rate": "n",
        "team": team,
        "pos": "S",
        "loc": ["B", "7", "0"],
        "lines": "pair",
        "draftteam": "all",
    }
    html = fetch_html("linestats.php", params)
    tables = parse_tables(html or "")
    if not tables:
        return pd.DataFrame()
    df = tables[0].copy()
    df.columns = [str(c).strip() for c in df.columns]
    df["season_id"] = season_id
    df["sit"] = sit
    df["team_filter"] = team
    df["fetched_at"] = datetime.now(timezone.utc).isoformat()
    if "Line" not in df.columns and "line" not in str(df.columns[0]).lower():
        for c in df.columns:
            if "line" in str(c).lower():
                df = df.rename(columns={c: "Line"})
                break
    return df


def fetch_player_pp(season_id: int, team: str = "all") -> pd.DataFrame:
    params = {
        "fromseason": _season_param(season_id),
        "thruseason": _season_param(season_id),
        "stype": "2",
        "sit": "pp",
        "score": "all",
        "rate": "n",
        "team": team,
        "pos": "S",
        "loc": ["B", "7", "0"],
        "lines": "single",
        "draftteam": "all",
    }
    html = fetch_html("playerteams.php", params)
    tables = parse_tables(html or "")
    if not tables:
        return pd.DataFrame()
    df = tables[0].copy()
    df.columns = [str(c).strip() for c in df.columns]
    df["season_id"] = season_id
    df["team_filter"] = team
    df["fetched_at"] = datetime.now(timezone.utc).isoformat()
    return df


def _append_cache(path: Path, fresh: pd.DataFrame, key_cols: list[str]) -> pd.DataFrame:
    if fresh.empty:
        return load_cache(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = load_cache(path)
    if old.empty:
        combined = fresh
    else:
        combined = pd.concat([old, fresh], ignore_index=True)
        subset = [c for c in key_cols if c in combined.columns]
        if subset:
            combined = combined.drop_duplicates(subset=subset, keep="last")
    tmp = path.with_suffix(".tmp.csv")
    combined.to_csv(tmp, index=False, encoding="utf-8-sig")
    tmp.replace(path)
    return combined


def load_cache(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


def refresh_line_cache(
    season_id: int,
    teams: Optional[list[str]] = None,
) -> pd.DataFrame:
    teams = teams or ["all"]
    parts: list[pd.DataFrame] = []
    for team in teams:
        for sit in ("5v5", "pp"):
            df = fetch_line_combos(season_id, team=team, sit=sit)
            if not df.empty:
                parts.append(df)
    if not parts:
        return load_cache(LINE_CACHE)
    fresh = pd.concat(parts, ignore_index=True)
    return _append_cache(
        LINE_CACHE,
        fresh,
        key_cols=["season_id", "sit", "team_filter", "Line"],
    )


def refresh_player_pp_cache(season_id: int, teams: Optional[list[str]] = None) -> pd.DataFrame:
    teams = teams or ["all"]
    parts = []
    for team in teams:
        df = fetch_player_pp(season_id, team=team)
        if not df.empty:
            parts.append(df)
    if not parts:
        return load_cache(PLAYER_PP_CACHE)
    fresh = pd.concat(parts, ignore_index=True)
    player_col = next((c for c in fresh.columns if str(c).lower() == "player"), None)
    key_cols = ["season_id", "team_filter"]
    if player_col:
        key_cols.append(player_col)
    return _append_cache(PLAYER_PP_CACHE, fresh, key_cols=key_cols)
