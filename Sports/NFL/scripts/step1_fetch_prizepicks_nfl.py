#!/usr/bin/env python3
"""
NFL step1 — PrizePicks direct API fetch (league_id=9).

Cloned from Sports/NBA/scripts/step1_fetch_prizepicks_api.py (curl_cffi + pagination).
Writes: Sports/NFL/data/step1_pp_nfl_{YYYY-MM-DD}.csv

Usage:
  py Sports/NFL/scripts/step1_fetch_prizepicks_nfl.py --date today
  py Sports/NFL/scripts/step1_fetch_prizepicks_nfl.py --date 2026-09-07 --replace
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import requests

_CURL_IMPERSONATE = (os.environ.get("PROPORACLE_CURL_IMPERSONATE") or "chrome120").strip()
try:
    from curl_cffi.requests import Session as _CurlCffiSession

    _CURL_CFFI_AVAILABLE = True
except ImportError:
    _CurlCffiSession = None  # type: ignore[misc, assignment]
    _CURL_CFFI_AVAILABLE = False

_HTTP_BACKEND_LOGGED = False

_SCRIPT_DIR = Path(__file__).resolve().parent
_NFL_ROOT = _SCRIPT_DIR.parent
_REPO_ROOT = _SCRIPT_DIR.resolve().parents[2]
_NFL_DATA_DIR = _NFL_ROOT / "data"

LEAGUE_ID = "9"
DEFAULT_TZ = "America/New_York"
BOARD_SIZE_MIN = 5

OUTPUT_COLS = [
    "player_id",
    "player_name",
    "team",
    "opp_team",
    "prop_type",
    "line",
    "start_time",
    "game_id",
    "position",
    "league_id",
    "fetch_date",
]

# Explicit PP display names → snake_case (unmapped → lowercased + underscored)
NFL_PROP_TYPE_MAP: Dict[str, str] = {
    "passing yards": "passing_yards",
    "pass yards": "passing_yards",
    "rushing yards": "rushing_yards",
    "rush yards": "rushing_yards",
    "receiving yards": "receiving_yards",
    "rec yards": "receiving_yards",
    "receptions": "receptions",
    "rec": "receptions",
    "touchdowns": "touchdowns",
    "tds": "touchdowns",
    "pass attempts": "pass_attempts",
    "passing attempts": "pass_attempts",
    "pass completions": "pass_completions",
    "completions": "pass_completions",
    "interceptions thrown": "interceptions_thrown",
    "interceptions": "interceptions_thrown",
    "sacks": "sacks",
    "passing tds": "passing_tds",
    "pass tds": "passing_tds",
    "rushing tds": "rushing_tds",
    "receiving tds": "receiving_tds",
    "kicking points": "kicking_points",
    "fantasy score": "fantasy_score",
    "tackles assists": "tackles_assists",
    "tackles + assists": "tackles_assists",
}

BASE_URL = "https://api.prizepicks.com/projections"
DEFAULT_SESSION_JITTER: Tuple[float, float] = (5.0, 12.0)
DEFAULT_INTER_PAGE_DELAY: Tuple[float, float] = (6.0, 14.0)
DEFAULT_WAVE_GAP: Tuple[float, float] = (12.0, 28.0)

_BROWSER_PROFILES: List[Dict[str, str]] = [
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Sec-Ch-Ua": '"Google Chrome";v="120", "Chromium";v="120", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
    },
]


def _new_http_session() -> Any:
    if _CURL_CFFI_AVAILABLE and _CurlCffiSession is not None:
        return _CurlCffiSession(impersonate=_CURL_IMPERSONATE)
    return requests.Session()


def _log_http_backend_once() -> None:
    global _HTTP_BACKEND_LOGGED
    if _HTTP_BACKEND_LOGGED:
        return
    _HTTP_BACKEND_LOGGED = True
    if _CURL_CFFI_AVAILABLE:
        print(f"  [NFL PP] HTTP: curl_cffi impersonate={_CURL_IMPERSONATE!r}")
    else:
        print("  [NFL PP] HTTP: requests (pip install curl-cffi recommended)")


def _ensure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _default_et_date_str() -> str:
    return datetime.now(ZoneInfo(DEFAULT_TZ)).date().isoformat()


def _resolve_date_arg(raw: str) -> str:
    s = str(raw or "").strip()
    if not s or s.lower() == "today":
        return _default_et_date_str()
    return s


def _default_output_path(fetch_date: str) -> Path:
    return _NFL_DATA_DIR / f"step1_pp_nfl_{fetch_date}.csv"


def norm_nfl_prop_type(raw: str) -> str:
    key = re.sub(r"\s+", " ", str(raw or "").strip().lower())
    if key in NFL_PROP_TYPE_MAP:
        return NFL_PROP_TYPE_MAP[key]
    s = re.sub(r"[^a-z0-9]+", "_", key)
    return re.sub(r"_+", "_", s).strip("_")


def _tls_chrome_major_from_impersonate() -> int | None:
    m = re.search(r"(?i)chrome[_-]?(\d+)", _CURL_IMPERSONATE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _profiles_for_current_transport() -> List[Dict[str, str]]:
    if not _CURL_CFFI_AVAILABLE:
        return list(_BROWSER_PROFILES)
    major = _tls_chrome_major_from_impersonate()
    if major is None:
        return list(_BROWSER_PROFILES)
    needle = f"Chrome/{major}."
    matched = [p for p in _BROWSER_PROFILES if needle in p.get("User-Agent", "")]
    return matched or list(_BROWSER_PROFILES)


def _browser_headers_from_profile(profile: Dict[str, str]) -> Dict[str, str]:
    return {
        **profile,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://app.prizepicks.com/",
        "Origin": "https://app.prizepicks.com",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "Priority": "u=1, i",
    }


def _random_browser_headers() -> Dict[str, str]:
    return _browser_headers_from_profile(random.choice(_profiles_for_current_transport()))


def _rotate_session_headers(session: Any) -> None:
    session.headers.clear()
    session.headers.update(_random_browser_headers())


def _make_session(session_jitter: Tuple[float, float] | None = None) -> Any:
    _log_http_backend_once()
    s = _new_http_session()
    _rotate_session_headers(s)
    lo, hi = session_jitter if session_jitter is not None else DEFAULT_SESSION_JITTER
    time.sleep(random.uniform(lo, hi))
    return s


def _api_get(
    session: Any,
    url: str,
    params: dict,
    retries: int = 5,
    timeout: Tuple[float, float] = (10.0, 30.0),
) -> dict:
    import urllib.parse

    full_url = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    last_exc = None
    consecutive_403 = 0
    for attempt in range(1, retries + 1):
        try:
            if attempt > 1:
                time.sleep(random.uniform(2.5, 6.5) + min(5.0, attempt * 0.45))
            r = session.get(full_url, timeout=timeout)
            if r.status_code == 429:
                wait = random.uniform(60.0, 120.0)
                print(f"  [429] Rate limited — waiting {wait:.0f}s ({attempt}/{retries})")
                time.sleep(wait)
                continue
            if r.status_code == 403:
                consecutive_403 += 1
                try:
                    session.cookies.clear()
                except Exception:
                    pass
                if consecutive_403 >= 2:
                    _rotate_session_headers(session)
                wait = random.uniform(10.0 + attempt * 3.0, 26.0 + attempt * 4.0)
                print(f"  [403] Backing off {wait:.1f}s ({attempt}/{retries})")
                time.sleep(wait)
                continue
            if r.status_code >= 500:
                wait = min(60.0, (2 ** (attempt - 1)) * 3.0) + random.uniform(1.0, 4.0)
                print(f"  [{r.status_code}] Server error — wait {wait:.1f}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_exc = e
            time.sleep(min(30.0, (2 ** (attempt - 1)) * 2.0) + random.uniform(1.0, 3.0))
    raise RuntimeError(f"API GET failed after {retries} retries: {url} | last={last_exc}")


def fetch_projections(
    league_id: str,
    per_page: int = 250,
    max_pages: int = 10,
    retries: int = 5,
    *,
    first_page_waves: int = 3,
) -> Tuple[List[dict], List[dict]]:
    all_data: List[dict] = []
    all_included: List[dict] = []
    seen_ids: set = set()
    params = {
        "league_id": league_id,
        "per_page": per_page,
        "single_stat": "true",
        "in_game": "false",
    }
    waves = max(1, int(first_page_waves))
    session: Any | None = None
    payload: dict | None = None
    for wave in range(waves):
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
            session = None
        if wave > 0:
            gap = random.uniform(*DEFAULT_WAVE_GAP)
            print(f"  [session-wave {wave + 1}/{waves}] New session; pausing {gap:.0f}s...")
            time.sleep(gap)
        jitter = DEFAULT_SESSION_JITTER if wave == 0 else (2.0, 6.5)
        session = _make_session(session_jitter=jitter)
        print(f"  Fetching page 1 (league_id={league_id}, wave {wave + 1}/{waves})...")
        try:
            payload = _api_get(session, BASE_URL, params, retries=retries)
            break
        except RuntimeError as e:
            if wave + 1 >= waves:
                raise
            print(f"  [WARN] Page 1 wave failed ({wave + 1}/{waves}): {e}")
    if payload is None or session is None:
        raise RuntimeError("fetch_projections: no payload after first-page waves")
    data = payload.get("data") or []
    included = payload.get("included") or []
    for obj in data:
        oid = str(obj.get("id", ""))
        if oid not in seen_ids:
            all_data.append(obj)
            seen_ids.add(oid)
    all_included.extend(included)
    print(f"    page 1 → {len(data)} projections")
    links = payload.get("links") or {}
    page = 2
    while links.get("next") and page <= max_pages:
        next_url = links["next"]
        print(f"  Fetching page {page}...")
        time.sleep(random.uniform(*DEFAULT_INTER_PAGE_DELAY))
        try:
            payload = _api_get(session, next_url, {}, retries=retries)
            new_data = payload.get("data") or []
            all_included.extend(payload.get("included") or [])
            added = 0
            for obj in new_data:
                oid = str(obj.get("id", ""))
                if oid not in seen_ids:
                    all_data.append(obj)
                    seen_ids.add(oid)
                    added += 1
            print(f"    page {page} → {len(new_data)} ({added} new)")
            links = payload.get("links") or {}
            if not new_data:
                break
        except Exception as e:
            print(f"  [WARN] Page {page} failed: {e}")
            break
        page += 1
    try:
        session.close()
    except Exception:
        pass
    return all_data, all_included


def _safe_get(d: Any, path: list, default: Any = "") -> Any:
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur if cur is not None else default


def _norm_team(s: Any) -> str:
    return str(s or "").strip().upper()


def _included_index(included: List[dict]) -> Dict[Tuple[str, str], dict]:
    idx: Dict[Tuple[str, str], dict] = {}
    for obj in included or []:
        t = str(obj.get("type", "")).strip()
        i = str(obj.get("id", "")).strip()
        if t and i:
            idx[(t, i)] = obj
    return idx


def build_nfl_rows(data: List[dict], included: List[dict], *, fetch_date: str) -> List[dict]:
    inc = _included_index(included)
    rows: List[dict] = []

    for d in data:
        if not isinstance(d, dict):
            continue
        pid = str(d.get("id", "")).strip()
        attrs = d.get("attributes") or {}
        rel = d.get("relationships") or {}

        raw_prop = str(attrs.get("stat_type", attrs.get("projection_type", attrs.get("name", "")))).strip()
        prop_type = norm_nfl_prop_type(raw_prop)
        line = attrs.get("line_score", attrs.get("line"))

        player_id = _safe_get(rel, ["new_player", "data", "id"], "") or ""
        player_type = _safe_get(rel, ["new_player", "data", "type"], "new_player")
        player_obj = inc.get((str(player_type), str(player_id))) if player_id else None

        player_name = position = team = ""
        if isinstance(player_obj, dict):
            pa = player_obj.get("attributes") or {}
            player_name = str(pa.get("display_name", pa.get("name", ""))).strip()
            position = str(pa.get("position", "")).strip()
            team = _norm_team(pa.get("team", ""))

        game_id = _safe_get(rel, ["new_game", "data", "id"], "") or _safe_get(rel, ["game", "data", "id"], "")
        game_type = _safe_get(rel, ["new_game", "data", "type"], "") or _safe_get(rel, ["game", "data", "type"], "")
        game_obj = inc.get((str(game_type), str(game_id))) if game_id and game_type else None

        home = away = start_time = ""
        if isinstance(game_obj, dict):
            ga = game_obj.get("attributes") or {}
            home = _norm_team(ga.get("home_team", ""))
            away = _norm_team(ga.get("away_team", ""))
            start_time = str(ga.get("start_time", "")).strip()
        if not start_time:
            start_time = str(attrs.get("start_time", "")).strip()

        opp_team = ""
        if team and home and away:
            opp_team = away if team == home else (home if team == away else "")
        elif not opp_team:
            desc = str(attrs.get("description", "") or "")
            m = re.search(r"\bvs\.?\s+([A-Za-z]{2,4})\b", desc)
            if m:
                opp_team = _norm_team(m.group(1))

        rows.append(
            {
                "projection_id": pid,
                "player_id": str(player_id).strip(),
                "player_name": player_name,
                "team": team,
                "opp_team": opp_team,
                "prop_type": prop_type,
                "line": line,
                "start_time": start_time,
                "game_id": str(game_id or "").strip(),
                "position": position,
                "league_id": LEAGUE_ID,
                "fetch_date": fetch_date,
            }
        )
    return rows


def _apply_game_date_filter(
    df: pd.DataFrame,
    target_date: str,
    tz_name: str,
    allow_nearest_future: bool,
) -> tuple[pd.DataFrame, str | None]:
    if df is None or len(df) == 0:
        out = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
        if "game_date" not in out.columns:
            out["game_date"] = ""
        return out, None
    tz = ZoneInfo(tz_name)
    ts = pd.to_datetime(df.get("start_time", pd.Series([], dtype=object)), errors="coerce", utc=True)
    game_date = ts.dt.tz_convert(tz).dt.date.astype("string")
    out = df.copy()
    out["game_date"] = game_date.fillna("")
    keep = out["game_date"].eq(target_date)
    if keep.any():
        return out.loc[keep].copy(), None
    if not allow_nearest_future:
        return out.head(0).copy(), None
    available = sorted({d for d in out["game_date"].astype(str).tolist() if d and d != "nan"})
    future = [d for d in available if d >= target_date]
    if not future:
        return out.head(0).copy(), None
    chosen = future[0]
    return out.loc[out["game_date"].eq(chosen)].copy(), chosen


def _write_empty(out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(columns=OUTPUT_COLS).to_csv(out_path, index=False, encoding="utf-8-sig")


def _log_health(df: pd.DataFrame) -> None:
    n = len(df)
    n_players = df["player_name"].astype(str).replace("", pd.NA).dropna().nunique() if n else 0
    print(f"\n[NFL step1] Health: total_props={n} unique_players={n_players}")
    if n:
        breakdown = df["prop_type"].value_counts().to_dict()
        print(f"[NFL step1] Prop type breakdown ({len(breakdown)} types):")
        for prop, cnt in sorted(breakdown.items(), key=lambda x: (-x[1], x[0])):
            print(f"    {prop}: {cnt}")


def main() -> int:
    ap = argparse.ArgumentParser(description="NFL PrizePicks step1 — direct API (league_id=9)")
    ap.add_argument("--output", default="", help="Output CSV (default: Sports/NFL/data/step1_pp_nfl_{date}.csv)")
    ap.add_argument("--date", default="today", help="Target slate date (YYYY-MM-DD or 'today')")
    ap.add_argument("--tz", default=DEFAULT_TZ)
    ap.add_argument("--per_page", type=int, default=250)
    ap.add_argument("--max_pages", type=int, default=10)
    ap.add_argument("--retries", type=int, default=5)
    ap.add_argument("--allow-nearest-future", action="store_true")
    ap.add_argument(
        "--merge",
        "--merge-existing",
        dest="merge_existing",
        action="store_true",
        help="Keep prior projection_ids not in this fetch when output exists.",
    )
    ap.add_argument("--replace", action="store_true", help="Overwrite output with this fetch only.")
    ap.add_argument("--raw_json", default="", help="Optional raw API JSON dump path")
    args = ap.parse_args()
    _ensure_utf8_stdio()

    fetch_date = _resolve_date_arg(args.date)
    out_path = Path(args.output) if str(args.output).strip() else _default_output_path(fetch_date)
    if not out_path.is_absolute():
        out_path = (_REPO_ROOT / out_path).resolve() if str(out_path).startswith("Sports") else (_NFL_ROOT / out_path).resolve()

    print(f"[NFL step1] PrizePicks fetch | league_id={LEAGUE_ID} | date={fetch_date}")
    print(f"[NFL step1] Output → {out_path}")

    try:
        data, included = fetch_projections(
            league_id=LEAGUE_ID,
            per_page=args.per_page,
            max_pages=args.max_pages,
            retries=args.retries,
        )
    except Exception as e:
        print(f"[NFL step1] WARN: fetch failed ({e}) — writing empty board CSV")
        _write_empty(out_path)
        return 0

    if not data:
        print("[NFL step1] WARN: empty board — API returned 0 projections (off-season or no lines)")
        _write_empty(out_path)
        return 0

    if args.raw_json:
        try:
            with open(args.raw_json, "w", encoding="utf-8") as f:
                json.dump({"data": data, "included": included}, f, ensure_ascii=False)
            print(f"[NFL step1] Raw JSON → {args.raw_json}")
        except Exception as e:
            print(f"  [WARN] raw_json write failed: {e}")

    rows = build_nfl_rows(data, included, fetch_date=fetch_date)
    df = pd.DataFrame(rows).fillna("")
    if "projection_id" in df.columns:
        df = df.drop_duplicates(subset=["projection_id"], keep="first").reset_index(drop=True)

    # Date filter
    fetched_rows = len(df)
    filtered_df, fallback_date = _apply_game_date_filter(
        df,
        target_date=fetch_date,
        tz_name=str(args.tz).strip() or DEFAULT_TZ,
        allow_nearest_future=bool(args.allow_nearest_future),
    )
    print(
        f"[NFL step1] Date filter {fetch_date}: fetched={fetched_rows} survived={len(filtered_df)}"
    )
    if fallback_date:
        print(f"[NFL step1] WARN: using nearest future game_date={fallback_date}")
    df = filtered_df

    if len(df) == 0:
        print("[NFL step1] WARN: empty board after date filter — writing 0-row CSV")
        _write_empty(out_path)
        return 0

    # Merge / replace (uses projection_id)
    merge_default = out_path.is_file() and not args.replace
    do_merge = bool(args.merge_existing or merge_default) and not args.replace
    if do_merge and out_path.is_file():
        try:
            old = pd.read_csv(out_path, encoding="utf-8-sig")
            if "projection_id" not in old.columns:
                old["projection_id"] = ""
            new_ids = set(df["projection_id"].astype(str).str.strip())
            kept = old[~old["projection_id"].astype(str).str.strip().isin(new_ids)]
            if len(kept):
                for c in df.columns:
                    if c not in kept.columns:
                        kept[c] = ""
                df = pd.concat([df, kept[df.columns.intersection(kept.columns)]], ignore_index=True)
                print(f"[NFL step1] Merged +{len(kept)} prior-only rows → {len(df)} total")
        except Exception as e:
            print(f"  [WARN] merge skipped: {e}")
    elif args.replace:
        print("[NFL step1] --replace: fetch-only write")

    n_props = len(df)
    if n_props < BOARD_SIZE_MIN:
        print(
            f"[NFL step1] WARN: board below minimum ({n_props} < {BOARD_SIZE_MIN}) — "
            "writing CSV anyway (off-season / light board)"
        )

    out_df = df[[c for c in OUTPUT_COLS if c in df.columns]].copy()
    for c in OUTPUT_COLS:
        if c not in out_df.columns:
            out_df[c] = ""
    out_df = out_df[OUTPUT_COLS]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    _log_health(out_df)
    print(f"\n[NFL step1] Saved → {out_path} ({len(out_df)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
