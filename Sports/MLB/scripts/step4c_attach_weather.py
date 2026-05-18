#!/usr/bin/env python3
"""
step4c_attach_weather.py — Open-Meteo weather at first pitch for MLB games.

Run after step4b:
  py -3.14 step4c_attach_weather.py \\
    --input step4_mlb_with_stats.csv \\
    --output step4_mlb_with_stats.csv
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import requests

_PROPORACLE_ROOT = Path(__file__).resolve().parents[3]
if str(_PROPORACLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROPORACLE_ROOT))

from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_VENUES_JSON = _DATA_DIR / "mlb_venues.json"
_WEATHER_CACHE = _DATA_DIR / "weather_cache.json"
_PARK_CSV = _DATA_DIR / "park_factors.csv"

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
TIMEOUT = 10

TEAM_ABBREV_ALIAS = {"AZ": "ARI", "OAK": "ATH", "WSN": "WSH", "WAS": "WSH", "SDP": "SD", "SFG": "SF"}

log = logging.getLogger("mlb.step4c")


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


def _norm_team(v: object) -> str:
    s = str(v or "").strip().upper()
    return TEAM_ABBREV_ALIAS.get(s, s)


def _venue_lookup() -> Tuple[dict, dict, dict]:
    venues = _load_json(_VENUES_JSON)
    by_abbrev: Dict[str, dict] = {}
    for name, meta in venues.items():
        if isinstance(meta, dict):
            ab = str(meta.get("team_abbrev", "")).upper()
            if ab:
                by_abbrev[ab] = {**meta, "venue_name": name}
    park_abbrev_to_name: Dict[str, str] = {}
    if _PARK_CSV.exists():
        parks = pd.read_csv(_PARK_CSV, encoding="utf-8-sig")
        for _, r in parks.iterrows():
            park_abbrev_to_name[str(r.get("team_abbrev", "")).upper()] = str(r.get("park_name", ""))
    return venues, by_abbrev, park_abbrev_to_name


def _home_team(row: pd.Series) -> str:
    gh = _norm_team(row.get("game_home_team", ""))
    if gh and gh != "NAN":
        return gh
    team = _norm_team(row.get("team", ""))
    home = _norm_team(row.get("pp_home_team", ""))
    away = _norm_team(row.get("pp_away_team", ""))
    if home and team == home:
        return team
    if away and team != away:
        return away
    return home or team


def _game_hour_local(row: pd.Series) -> int:
    st = str(row.get("start_time", "")).strip()
    if st and st.lower() not in ("nan", ""):
        try:
            ts = pd.to_datetime(st, utc=True, errors="coerce")
            if pd.notna(ts):
                return int(ts.tz_convert("America/New_York").hour)
        except Exception:
            pass
    return 19


def _wind_out_to_cf(wind_dir: float, outfield_deg: float) -> bool:
    if pd.isna(wind_dir) or pd.isna(outfield_deg):
        return False
    diff = abs((float(wind_dir) - float(outfield_deg) + 180) % 360 - 180)
    return diff < 45


def _weather_flag(wind_mph: float, precip_mm: float, is_dome: bool) -> str:
    if is_dome:
        return "dome"
    if precip_mm > 0.5:
        return "rain"
    if wind_mph >= 15:
        return "high_wind"
    if wind_mph >= 8:
        return "moderate_wind"
    return "calm"


def fetch_weather(
    lat: float,
    lon: float,
    game_date: str,
    hour: int,
    cache: dict,
    venue_key: str,
) -> dict:
    ck = f"{venue_key}_{game_date}"
    if ck in cache:
        return cache[ck]

    today = datetime.now().strftime("%Y-%m-%d")
    use_archive = game_date < today
    base_url = ARCHIVE_URL if use_archive else FORECAST_URL
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "windspeed_10m,winddirection_10m,temperature_2m,precipitation",
        "timezone": "America/New_York",
        "start_date": game_date,
        "end_date": game_date,
    }
    result = {
        "wind_speed_mph": None,
        "wind_dir_deg": None,
        "temp_f": None,
        "precip_mm": None,
        "weather_flag": "calm",
    }
    try:
        r = requests.get(base_url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        payload = r.json()
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        if not times:
            cache[ck] = result
            return result
        target_h = f"{game_date}T{hour:02d}:00"
        idx = 0
        for i, t in enumerate(times):
            if str(t).startswith(f"{game_date}T{hour:02d}"):
                idx = i
                break
        ws_kmh = (hourly.get("windspeed_10m") or [None])[idx]
        wd = (hourly.get("winddirection_10m") or [None])[idx]
        temp_c = (hourly.get("temperature_2m") or [None])[idx]
        precip = (hourly.get("precipitation") or [None])[idx]
        if ws_kmh is not None:
            result["wind_speed_mph"] = round(float(ws_kmh) * 0.621371, 2)
        if wd is not None:
            result["wind_dir_deg"] = int(round(float(wd))) % 360
        if temp_c is not None:
            result["temp_f"] = round(float(temp_c) * 9.0 / 5.0 + 32.0, 1)
        if precip is not None:
            result["precip_mm"] = round(float(precip), 2)
    except Exception as exc:
        log.warning("Open-Meteo failed %s %s: %s", venue_key, game_date, exc)

    cache[ck] = result
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="step4_mlb_with_stats.csv")
    ap.add_argument("--output", default="step4_mlb_with_stats.csv")
    args = ap.parse_args()

    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")
    _, by_abbrev, park_names = _venue_lookup()
    wcache = _load_json(_WEATHER_CACHE)

    cols = [
        "wind_speed_mph", "wind_dir_deg", "temp_f", "precip_mm",
        "weather_flag", "wind_out_to_cf",
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = np.nan
    if "weather_flag" in df.columns:
        df["weather_flag"] = df["weather_flag"].astype(object)
    if "wind_out_to_cf" in df.columns:
        df["wind_out_to_cf"] = df["wind_out_to_cf"].astype(object)

    game_weather: Dict[Tuple[str, str], dict] = {}

    for idx, row in df.iterrows():
        gdate = str(row.get("game_date", ""))[:10]
        home = _home_team(row)
        if not gdate or not home:
            continue
        key = (gdate, home)
        if key in game_weather:
            wx = game_weather[key]
        else:
            park_name = park_names.get(home, "")
            venue = by_abbrev.get(home) or {}
            is_dome = bool(venue.get("is_dome", False))
            if is_dome:
                wx = {
                    "wind_speed_mph": 0.0,
                    "wind_dir_deg": 0,
                    "temp_f": 72.0,
                    "precip_mm": 0.0,
                    "weather_flag": "dome",
                    "outfield_direction_deg": venue.get("outfield_direction_deg", 0),
                }
            else:
                lat = venue.get("lat")
                lon = venue.get("lon")
                if lat is None or lon is None:
                    log.warning("No coordinates for home team %s", home)
                    wx = {}
                else:
                    hour = _game_hour_local(row)
                    wx = fetch_weather(
                        float(lat), float(lon), gdate, hour, wcache,
                        venue_key=park_name or home,
                    )
                    wx["outfield_direction_deg"] = venue.get("outfield_direction_deg", 0)
                    wx["weather_flag"] = _weather_flag(
                        float(wx.get("wind_speed_mph") or 0),
                        float(wx.get("precip_mm") or 0),
                        False,
                    )
            game_weather[key] = wx

        if not wx:
            continue
        for c in ("wind_speed_mph", "wind_dir_deg", "temp_f", "precip_mm", "weather_flag"):
            if c in wx:
                df.at[idx, c] = wx[c]
        df.at[idx, "wind_out_to_cf"] = _wind_out_to_cf(
            pd.to_numeric(df.at[idx, "wind_dir_deg"], errors="coerce"),
            wx.get("outfield_direction_deg", 0),
        )

    _save_json(_WEATHER_CACHE, wcache)

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    copy_pipeline_output_to_dated_dirs(
        output_path=args.output,
        df=df,
        sport_dir_name="MLB",
        repo_root=_PROPORACLE_ROOT,
    )
    filled = df["weather_flag"].notna().sum()
    print(f"Weather attached: {filled}/{len(df)} rows")
    print(f"✅ Saved → {args.output}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ MLB step4c failed. {type(e).__name__}: {e}")
        sys.exit(1)
