#!/usr/bin/env python3
"""
step1_fetch_prizepicks_api.py  (NBA Pipeline A - direct API edition)

Fetches PrizePicks projections directly from the API — no browser, no
Playwright, no interception. Harder to detect, faster, and fully headless.

Strategy:
  - Default HTTP: curl_cffi Session(impersonate=chrome120) when installed (browser TLS/JA3); else requests
  - Cohesive browser profiles (User-Agent + Sec-CH-UA + platform); with curl_cffi, pool matches impersonate=chromeNNN; light rotation on repeated 403
  - Optional multi-wave first page: new Session + backoff if all in-wave retries exhaust (MLB uses more waves)
  - Persistent session with app.prizepicks.com-style headers on top of impersonation
  - Conservative delays before the first request and between paginated pages
  - Paginates through all projections (per_page=250)
  - Retries with exponential backoff on 429/5xx
  - Validates output row/team counts before writing
  - Exits non-zero if data is missing so the pipeline halts cleanly

Outputs: step1_pp_props_today.csv  (same schema as before).

WNBA: scripts/run_wnba_pipeline.ps1 invokes this with ``--league_id 3`` and writes
``outputs/<date>/wnba/step1_wnba_props.csv``. Browser/CDP fallback stays in
``Sports/WNBA/step1_fetch_prizepicks.py``.

When --output already exists, this script **merges by default**: rows from this fetch
replace same projection_id; rows only in the old file (IDs not returned today) are kept.
Use **--replace** to write **only** this fetch (full overwrite, no carry-over).

Use **--append** to concatenate this fetch after existing rows and deduplicate
(keep='last') on player + prop_type + line + pp_game_id + pick_type so a later
run wins line updates without dropping non-returned projection_ids from the file.
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

# PrizePicks sits behind Cloudflare; stdlib TLS (requests) is often JA3-flagged.
# curl_cffi impersonates a real browser TLS + HTTP/2 fingerprint (see _make_session).
_CURL_IMPERSONATE = (os.environ.get("PROPORACLE_CURL_IMPERSONATE") or "chrome120").strip()
try:
    from curl_cffi.requests import Session as _CurlCffiSession

    _CURL_CFFI_AVAILABLE = True
except ImportError:
    _CurlCffiSession = None  # type: ignore[misc, assignment]
    _CURL_CFFI_AVAILABLE = False

_HTTP_BACKEND_LOGGED = False


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
        print(f"  [PP] HTTP transport: curl_cffi impersonate={_CURL_IMPERSONATE!r} (browser TLS/JA3)")
    else:
        print(
            "  [PP] HTTP transport: requests (install curl-cffi for Cloudflare-resistant TLS; "
            "pip install curl-cffi)"
        )


def _ensure_utf8_stdio() -> None:
    """Avoid UnicodeEncodeError on Windows (cp1252) when logs use emoji."""
    for _stream in (sys.stdout, sys.stderr):
        reconf = getattr(_stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


# ── constants ─────────────────────────────────────────────────────────────────

BASE_URL = "https://api.prizepicks.com/projections"
DEFAULT_TZ = "America/New_York"

PICKTYPE_MAP = {
    "standard": "Standard",
    "goblin":   "Goblin",
    "demon":    "Demon",
}

# Conservative pacing when callers do not override (least aggressive defaults).
DEFAULT_SESSION_JITTER: Tuple[float, float] = (5.0, 12.0)
DEFAULT_INTER_PAGE_DELAY: Tuple[float, float] = (6.0, 14.0)

# Gap between session waves after page-1 failure (new TCP session).
DEFAULT_WAVE_GAP: Tuple[float, float] = (12.0, 28.0)

# Cohesive browser profiles: Sec-CH-UA major version must match the Chrome/Edg token in User-Agent.
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
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Sec-Ch-Ua": '"Google Chrome";v="120", "Chromium";v="120", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
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
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/132.0.0.0 Safari/537.36"
        ),
        "Sec-Ch-Ua": '"Google Chrome";v="132", "Chromium";v="132", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
        ),
        "Sec-Ch-Ua": '"Microsoft Edge";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
        "Sec-Ch-Ua": '"Google Chrome";v="130", "Chromium";v="130", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/133.0.0.0 Safari/537.36"
        ),
        "Sec-Ch-Ua": '"Google Chrome";v="133", "Chromium";v="133", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/133.0.0.0 Safari/537.36"
        ),
        "Sec-Ch-Ua": '"Google Chrome";v="133", "Chromium";v="133", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
    },
]


def _tls_chrome_major_from_impersonate() -> int | None:
    """Parse chromeNNN from PROPORACLE_CURL_IMPERSONATE / default so UA matches TLS impersonation."""
    m = re.search(r"(?i)chrome[_-]?(\d+)", _CURL_IMPERSONATE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _profiles_for_current_transport() -> List[Dict[str, str]]:
    """
    When curl_cffi is active, only use client hints whose Chrome major matches impersonate=chromeNNN.
    Mismatch (e.g. TLS chrome120 + UA Chrome/131) is a common WAF 403 trigger.
    """
    if not _CURL_CFFI_AVAILABLE:
        return list(_BROWSER_PROFILES)
    major = _tls_chrome_major_from_impersonate()
    if major is None:
        return list(_BROWSER_PROFILES)
    needle = f"Chrome/{major}."
    edge_needle = f"Edg/{major}."
    matched = [
        p
        for p in _BROWSER_PROFILES
        if needle in p.get("User-Agent", "") or edge_needle in p.get("User-Agent", "")
    ]
    if not matched:
        raise RuntimeError(
            f"No User-Agent profile for curl_cffi impersonate={_CURL_IMPERSONATE!r} "
            f"(expected Chrome/{major}. or Edg/{major}. in _BROWSER_PROFILES). "
            "Add a matching profile or set PROPORACLE_CURL_IMPERSONATE to a supported Chrome major."
        )
    return matched


def _validate_client_hints_match_tls(browser_profile_headers: Dict[str, str]) -> None:
    """Fail fast if HTTP client hints contradict curl_cffi TLS impersonation (regression guard)."""
    if not _CURL_CFFI_AVAILABLE:
        return
    major = _tls_chrome_major_from_impersonate()
    if major is None:
        return
    ua = browser_profile_headers.get("User-Agent", "")
    if f"Chrome/{major}." not in ua and f"Edg/{major}." not in ua:
        raise RuntimeError(
            f"PrizePicks client-hint/TLS mismatch: impersonate={_CURL_IMPERSONATE!r} "
            f"requires User-Agent containing Chrome/{major}. or Edg/{major}.; got {ua[:160]!r}"
        )


def _browser_headers_from_profile(profile: Dict[str, str]) -> Dict[str, str]:
    """Full header set for a PrizePicks API XHR (matches app.prizepicks.com origin)."""
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
    pool = _profiles_for_current_transport()
    headers = _browser_headers_from_profile(random.choice(pool))
    _validate_client_hints_match_tls(headers)
    return headers


def _rotate_session_headers(session: Any) -> None:
    session.headers.clear()
    session.headers.update(_random_browser_headers())


def _default_et_date_str() -> str:
    return datetime.now(ZoneInfo(DEFAULT_TZ)).date().isoformat()


def _apply_game_date_filter(
    df: pd.DataFrame,
    target_date: str,
    tz_name: str,
    allow_nearest_future: bool,
) -> tuple[pd.DataFrame, str | None]:
    if df is None or len(df) == 0:
        out = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
        if isinstance(out, pd.DataFrame) and "game_date" not in out.columns:
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

# ── helpers ───────────────────────────────────────────────────────────────────

def _safe_get(d: Any, path: list, default: Any = "") -> Any:
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur if cur is not None else default


def _norm_team(s: Any) -> str:
    return str(s or "").strip().upper()


def _make_session(session_jitter: Tuple[float, float] | None = None) -> Any:
    """Create a session (curl_cffi or requests) with cohesive headers; pause before first request."""
    _log_http_backend_once()
    s = _new_http_session()
    _rotate_session_headers(s)
    lo, hi = session_jitter if session_jitter is not None else DEFAULT_SESSION_JITTER
    time.sleep(random.uniform(lo, hi))
    return s


def _hard_reset_session(
    session: Any,
    *,
    reason: str,
    jitter: Tuple[float, float] = (2.0, 6.5),
) -> None:
    """Aggressive anti-403 reset: close pooled sockets, clear cookies, rotate profile, short cooloff."""
    print(f"    [session-reset] {reason}")
    try:
        session.close()
    except Exception:
        pass
    try:
        session.cookies.clear()
    except Exception:
        pass
    _rotate_session_headers(session)
    time.sleep(random.uniform(*jitter))


def _api_get(
    session: Any,
    url: str,
    params: dict,
    retries: int = 5,
    timeout: Tuple[float, float] = (10.0, 30.0),
    *,
    forbid_cooldown_threshold: int = 3,
    forbid_cooldown_seconds: float = 90.0,
    forbid_cooldown_jitter: Tuple[float, float] = (12.0, 40.0),
    forbid_max_cooldown_windows: int = 3,
) -> dict:
    """
    GET with session headers (cohesive UA + Sec-CH-UA). Builds query string manually
    for the first page to avoid rare encoding-related 403s.

    Retry logic:
      - 429 → long backoff (60-120s) then retry
      - 403 → clear cookies; first 403 keeps UA/client hints (matches TLS impersonation), later 403s rotate within TLS-matched pool + backoff
      - 5xx → exponential backoff
    Raises RuntimeError after all retries exhausted.
    """
    import urllib.parse

    if params:
        qs = urllib.parse.urlencode(params)
        full_url = f"{url}?{qs}" if qs else url
    else:
        full_url = url

    last_exc = None
    consecutive_403 = 0
    cooldown_windows = 0
    for attempt in range(1, retries + 1):
        try:
            if attempt > 1:
                # Gentle jitter before retries to avoid bursty retry signatures.
                time.sleep(random.uniform(2.5, 6.5) + min(5.0, attempt * 0.45))

            r = session.get(full_url, timeout=timeout)

            if r.status_code == 429:
                consecutive_403 = 0
                wait = random.uniform(60.0, 120.0)
                print(f"  [429] Rate limited — waiting {wait:.0f}s (attempt {attempt}/{retries})")
                time.sleep(wait)
                continue

            if r.status_code == 403:
                consecutive_403 += 1
                try:
                    session.cookies.clear()
                except Exception:
                    pass
                # First 403: keep the same UA/client-hint fingerprint (TLS already fixed to impersonate).
                # Later 403s: rotate within the TLS-matched pool only (see _profiles_for_current_transport).
                if consecutive_403 < 2:
                    print(
                        f"  [403] Forbidden on attempt {attempt}/{retries} — "
                        f"same client fingerprint; clearing cookies only"
                    )
                else:
                    print(
                        f"  [403] Forbidden on attempt {attempt}/{retries} — "
                        f"rotating browser profile (TLS-matched pool)"
                    )
                    _rotate_session_headers(session)
                # Softer backoff: avoid hammering; escalate slowly.
                base = min(72.0, 10.0 + float(attempt) * 3.2 + float(consecutive_403) * 4.5)
                wait = random.uniform(base, base + 16.0)
                print(f"    [403] Backing off {wait:.1f}s before retry")
                time.sleep(wait)
                if consecutive_403 >= max(1, int(forbid_cooldown_threshold)):
                    cooldown_windows += 1
                    cd_lo, cd_hi = forbid_cooldown_jitter
                    cooldown_scale = 1.0 + max(0, cooldown_windows - 1) * 0.35
                    cooldown_wait = (
                        max(15.0, float(forbid_cooldown_seconds)) * cooldown_scale
                        + random.uniform(cd_lo, cd_hi)
                    )
                    print(
                        f"    [403] Cooldown window hit ({consecutive_403} consecutive, "
                        f"window {cooldown_windows}/{max(1, int(forbid_max_cooldown_windows))}). "
                        f"Sleeping {cooldown_wait:.1f}s"
                    )
                    time.sleep(cooldown_wait)
                    _hard_reset_session(
                        session,
                        reason="Consecutive 403 threshold reached; rebuilding pooled connection fingerprint",
                    )
                    consecutive_403 = 0
                    if cooldown_windows >= max(1, int(forbid_max_cooldown_windows)):
                        raise RuntimeError(
                            f"HTTP_403_COOLDOWNS_EXHAUSTED: {url} after {cooldown_windows} cooldown windows"
                        )
                continue

            if r.status_code >= 500:
                consecutive_403 = 0
                wait = min(60.0, (2 ** (attempt - 1)) * 3.0) + random.uniform(1.0, 4.0)
                print(f"  [{r.status_code}] Server error — waiting {wait:.1f}s (attempt {attempt}/{retries})")
                time.sleep(wait)
                continue

            consecutive_403 = 0
            r.raise_for_status()
            return r.json()

        except requests.exceptions.ConnectionError as e:
            wait = min(30.0, (2 ** (attempt - 1)) * 2.0) + random.uniform(1.0, 3.0)
            print(f"  [CONN] Connection error attempt {attempt}/{retries}: {e} — waiting {wait:.1f}s")
            last_exc = e
            time.sleep(wait)
        except requests.exceptions.Timeout as e:
            wait = min(30.0, (2 ** (attempt - 1)) * 2.0) + random.uniform(1.0, 3.0)
            print(f"  [TIMEOUT] Timeout attempt {attempt}/{retries} — waiting {wait:.1f}s")
            last_exc = e
            time.sleep(wait)
        except Exception as e:
            wait = min(30.0, (2 ** (attempt - 1)) * 2.0) + random.uniform(1.0, 3.0)
            print(f"  [ERR] Unexpected error attempt {attempt}/{retries}: {type(e).__name__}: {e}")
            last_exc = e
            time.sleep(wait)

    raise RuntimeError(f"API GET failed after {retries} retries: {url} | last={last_exc}")


# ── fetch ─────────────────────────────────────────────────────────────────────

def fetch_projections(
    league_id: str,
    per_page: int = 250,
    max_pages: int = 10,
    retries: int = 5,
    *,
    inter_page_delay: Tuple[float, float] | None = None,
    session_jitter: Tuple[float, float] | None = None,
    first_page_waves: int = 3,
    wave_gap_seconds: Tuple[float, float] | None = None,
    forbid_cooldown_threshold: int = 3,
    forbid_cooldown_seconds: float = 90.0,
    forbid_cooldown_jitter: Tuple[float, float] = (12.0, 40.0),
    forbid_max_cooldown_windows: int = 3,
) -> Tuple[List[dict], List[dict]]:
    """
    Fetch all projections + included sideloads from PrizePicks API.
    Paginates until no more data or max_pages reached.
    Returns (data_list, included_list).

    inter_page_delay: (min_sec, max_sec) random sleep between pagination requests.
    session_jitter: (min_sec, max_sec) sleep before the first request (new session).
    first_page_waves: On repeated 403/429 exhaustion for page 1, discard the session,
        wait, open a fresh session, and retry (helps PrizePicks MLB fetches).
    wave_gap_seconds: Random pause between session waves (min, max); wider = less aggressive.
    """
    all_data: List[dict] = []
    all_included: List[dict] = []
    seen_ids: set = set()

    params = {
        "league_id":   league_id,
        "per_page":    per_page,
        "single_stat": "true",
        "in_game":     "false",
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
            wg_lo, wg_hi = wave_gap_seconds if wave_gap_seconds is not None else DEFAULT_WAVE_GAP
            gap = random.uniform(wg_lo, wg_hi)
            print(f"  [session-wave {wave + 1}/{waves}] New session after page-1 failure; pausing {gap:.0f}s...")
            time.sleep(gap)

        if wave == 0:
            jitter = session_jitter
        elif session_jitter is not None:
            lo, hi = session_jitter
            jitter = (max(1.2, lo * 0.45), max(2.0, hi * 0.55))
        else:
            jitter = (2.0, 6.5)

        session = _make_session(session_jitter=jitter)
        print(f"  Fetching page 1 (league_id={league_id}, per_page={per_page})...")
        try:
            payload = _api_get(
                session,
                BASE_URL,
                params,
                retries=retries,
                forbid_cooldown_threshold=forbid_cooldown_threshold,
                forbid_cooldown_seconds=forbid_cooldown_seconds,
                forbid_cooldown_jitter=forbid_cooldown_jitter,
                forbid_max_cooldown_windows=forbid_max_cooldown_windows,
            )
            break
        except RuntimeError as e:
            if wave + 1 >= waves:
                raise
            print(f"  [WARN] Page 1 wave failed ({wave + 1}/{waves}): {e}")

    if payload is None or session is None:
        raise RuntimeError("fetch_projections: no payload after first-page waves")

    data     = payload.get("data") or []
    included = payload.get("included") or []

    for obj in data:
        oid = str(obj.get("id", ""))
        if oid not in seen_ids:
            all_data.append(obj)
            seen_ids.add(oid)
    all_included.extend(included)
    print(f"    page 1 → {len(data)} projections")

    # Check for pagination — some API responses include links.next
    links = payload.get("links") or {}
    page = 2
    ip_lo, ip_hi = inter_page_delay if inter_page_delay is not None else DEFAULT_INTER_PAGE_DELAY
    while links.get("next") and page <= max_pages:
        next_url = links["next"]
        print(f"  Fetching page {page}...")
        # Inter-page delay (default gentle; MLB caller can pass slower bounds)
        time.sleep(random.uniform(ip_lo, ip_hi))
        try:
            payload  = _api_get(
                session,
                next_url,
                {},
                retries=retries,
                forbid_cooldown_threshold=forbid_cooldown_threshold,
                forbid_cooldown_seconds=forbid_cooldown_seconds,
                forbid_cooldown_jitter=forbid_cooldown_jitter,
                forbid_max_cooldown_windows=forbid_max_cooldown_windows,
            )
            new_data = payload.get("data") or []
            new_inc  = payload.get("included") or []
            added = 0
            for obj in new_data:
                oid = str(obj.get("id", ""))
                if oid not in seen_ids:
                    all_data.append(obj)
                    seen_ids.add(oid)
                    added += 1
            all_included.extend(new_inc)
            print(f"    page {page} → {len(new_data)} projections ({added} new)")
            links = payload.get("links") or {}
            if not new_data:
                break
        except RuntimeError as e:
            msg = str(e)
            if "HTTP_403_COOLDOWNS_EXHAUSTED" in msg:
                print(f"  [WARN] Page {page} hit repeated 403 cooldown limits; opening fresh session and retrying once.")
                try:
                    try:
                        session.close()
                    except Exception:
                        pass
                    session = _make_session(session_jitter=(2.0, 6.0))
                    payload = _api_get(
                        session,
                        next_url,
                        {},
                        retries=max(2, retries - 1),
                        forbid_cooldown_threshold=forbid_cooldown_threshold,
                        forbid_cooldown_seconds=forbid_cooldown_seconds,
                        forbid_cooldown_jitter=forbid_cooldown_jitter,
                        forbid_max_cooldown_windows=max(1, forbid_max_cooldown_windows - 1),
                    )
                    new_data = payload.get("data") or []
                    new_inc = payload.get("included") or []
                    added = 0
                    for obj in new_data:
                        oid = str(obj.get("id", ""))
                        if oid not in seen_ids:
                            all_data.append(obj)
                            seen_ids.add(oid)
                            added += 1
                    all_included.extend(new_inc)
                    print(f"    page {page} retry → {len(new_data)} projections ({added} new)")
                    links = payload.get("links") or {}
                    if not new_data:
                        break
                    page += 1
                    continue
                except Exception as retry_e:
                    print(f"  [WARN] Page {page} retry failed: {retry_e} — stopping pagination")
                    break
            print(f"  [WARN] Page {page} failed: {e} — stopping pagination")
            break
        except Exception as e:
            print(f"  [WARN] Page {page} failed: {e} — stopping pagination")
            break
        page += 1

    session.close()
    return all_data, all_included


# ── parse ─────────────────────────────────────────────────────────────────────

def _included_index(included: List[dict]) -> Dict[Tuple[str, str], dict]:
    idx: Dict[Tuple[str, str], dict] = {}
    for obj in included or []:
        t = str(obj.get("type", "")).strip()
        i = str(obj.get("id", "")).strip()
        if t and i:
            idx[(t, i)] = obj
    return idx


def build_rows(data: List[dict], included: List[dict]) -> List[dict]:
    inc = _included_index(included)
    rows = []

    for d in data:
        if not isinstance(d, dict):
            continue

        pid   = str(d.get("id", "")).strip()
        attrs = d.get("attributes") or {}
        rel   = d.get("relationships") or {}

        line      = attrs.get("line_score", attrs.get("line"))
        prop_type = str(attrs.get("stat_type", attrs.get("projection_type", attrs.get("name", "")))).strip()
        odds_type = str(attrs.get("odds_type", "")).strip().lower()
        pick_type = PICKTYPE_MAP.get(odds_type, "Standard")
        std_api = attrs.get("standard_line") or attrs.get("standard_score") or attrs.get("baseline")
        if pick_type == "Standard":
            standard_line = std_api if std_api is not None and str(std_api).strip() != "" else line
        else:
            standard_line = std_api if std_api is not None else ""

        # Player
        player_id   = _safe_get(rel, ["new_player", "data", "id"], "") or ""
        player_type = _safe_get(rel, ["new_player", "data", "type"], "new_player")
        player_obj  = inc.get((str(player_type), str(player_id))) if player_id else None

        player_name = pos = team = image_url = ""
        if isinstance(player_obj, dict):
            pa = player_obj.get("attributes") or {}
            player_name = str(pa.get("display_name", pa.get("name", ""))).strip()
            pos         = str(pa.get("position", "")).strip()
            team        = _norm_team(pa.get("team", ""))
            image_url   = str(
                pa.get("image_url") or pa.get("image_url_small") or
                pa.get("photo_url") or pa.get("headshot") or
                pa.get("avatar") or ""
            ).strip()

        # Game
        game_id   = _safe_get(rel, ["new_game", "data", "id"], "") or _safe_get(rel, ["game", "data", "id"], "")
        game_type = _safe_get(rel, ["new_game", "data", "type"], "") or _safe_get(rel, ["game", "data", "type"], "")
        game_obj  = inc.get((str(game_type), str(game_id))) if game_id and game_type else None

        home = away = start_time = ""
        if isinstance(game_obj, dict):
            ga         = game_obj.get("attributes") or {}
            home       = _norm_team(ga.get("home_team", ""))
            away       = _norm_team(ga.get("away_team", ""))
            start_time = str(ga.get("start_time", "")).strip()

        if not start_time:
            start_time = str(attrs.get("start_time", "")).strip()

        # Opponent — step2 will also infer this from pp_game_id, but populate if possible
        opp_team = ""
        if team and home and away:
            opp_team = away if team == home else (home if team == away else "")
        elif not opp_team:
            desc = str(attrs.get("description", "") or "")
            m = re.search(r"\bvs\.?\s+([A-Za-z]{2,4})\b", desc)
            if m:
                opp_team = _norm_team(m.group(1))

        rows.append({
            "projection_id":    pid,
            "pp_projection_id": pid,
            "player_id":        str(player_id).strip(),
            "pp_game_id":       str(game_id or "").strip(),
            "start_time":       start_time,
            "player":           player_name,
            "pos":              pos,
            "team":             team,
            "opp_team":         opp_team,
            "prop_type":        prop_type,
            "line":             line,
            "standard_line":    standard_line,
            "pick_type":        pick_type,
            "pp_home_team":     home,
            "pp_away_team":     away,
            "image_url":        image_url,
        })

    return rows


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch PrizePicks props — direct API, no browser")
    ap.add_argument("--output",     default="step1_pp_props_today.csv")
    ap.add_argument("--league_id",  default="7")
    ap.add_argument("--per_page",   type=int, default=250)
    ap.add_argument("--max_pages",  type=int, default=10)
    ap.add_argument("--retries",    type=int, default=5)
    ap.add_argument("--min_rows",   type=int, default=50,  help="Minimum props required to consider fetch valid")
    ap.add_argument("--min_teams",  type=int, default=4,   help="Minimum teams required to consider fetch valid")
    ap.add_argument("--raw_json",   default="",            help="Optional path to dump raw API response")
    ap.add_argument("--history",    default="",            help="Optional path template for history CSV (use {ts})")
    ap.add_argument("--date", default=_default_et_date_str(), help=f"Target game date in {DEFAULT_TZ} (YYYY-MM-DD).")
    ap.add_argument("--tz", default=DEFAULT_TZ, help="Timezone used to derive game_date from start_time.")
    ap.add_argument("--allow-nearest-future", action="store_true", help="If no rows match --date, keep nearest future game_date.")
    ap.add_argument(
        "--merge-existing",
        action="store_true",
        help="If --output already exists: keep rows whose projection_id was not returned "
        "this fetch; fresh rows replace matching projection_id (board updates + prior-only props).",
    )
    ap.add_argument(
        "--replace",
        action="store_true",
        help="Overwrite --output with this fetch only (no merge with an existing CSV).",
    )
    ap.add_argument(
        "--append",
        action="store_true",
        help="Append this fetch after existing CSV rows, then dedupe (keep='last'); "
        "incompatible with --replace.",
    )
    # Legacy args accepted but ignored (were for Playwright version)
    ap.add_argument("--game_mode",        default="pickem")
    ap.add_argument("--sleep",            type=float, default=2.0)
    ap.add_argument("--cooldown_seconds", type=float, default=90.0)
    ap.add_argument("--max_cooldowns",    type=int,   default=3)
    ap.add_argument("--jitter_seconds",   type=float, default=10.0)
    args = ap.parse_args()
    _ensure_utf8_stdio()
    if args.append and args.replace:
        ap.error("Use either --append or --replace, not both.")

    EMPTY_COLS = [
        "projection_id", "pp_projection_id", "player_id", "pp_game_id",
        "start_time", "player", "pos", "team", "opp_team", "prop_type",
        "line", "standard_line", "pick_type", "pp_home_team", "pp_away_team", "image_url",
    ]

    print(f"📡 PrizePicks fetch | league_id={args.league_id} | direct API (no browser)")

    out_path = Path(args.output)
    try:
        data, included = fetch_projections(
            league_id=str(args.league_id),
            per_page=args.per_page,
            max_pages=args.max_pages,
            retries=args.retries,
            forbid_cooldown_threshold=max(1, int(args.max_cooldowns)),
            forbid_cooldown_seconds=max(15.0, float(args.cooldown_seconds)),
            forbid_cooldown_jitter=(max(1.0, float(args.jitter_seconds) * 0.8), max(2.0, float(args.jitter_seconds) * 2.2)),
            forbid_max_cooldown_windows=max(1, int(args.max_cooldowns)),
        )
    except Exception as e:
        print(f"❌ Fetch failed: {e}")
        if not (args.append and out_path.is_file()):
            pd.DataFrame(columns=EMPTY_COLS).to_csv(args.output, index=False, encoding="utf-8-sig")
        else:
            print("   (--append: left existing output file unchanged)")
        sys.exit(1)

    if not data:
        print("❌ No projections returned from API.")
        if not (args.append and out_path.is_file()):
            pd.DataFrame(columns=EMPTY_COLS).to_csv(args.output, index=False, encoding="utf-8-sig")
        else:
            print("   (--append: left existing output file unchanged)")
        sys.exit(1)

    # Optional raw JSON dump
    if args.raw_json:
        try:
            with open(args.raw_json, "w", encoding="utf-8") as f:
                json.dump({"data": data, "included": included}, f, ensure_ascii=False)
            print(f"🧾 Raw JSON saved → {args.raw_json}")
        except Exception as e:
            print(f"  [WARN] raw_json write failed: {e}")

    rows = build_rows(data, included)
    df   = pd.DataFrame(rows).fillna("")
    df["line"] = pd.to_numeric(df["line"], errors="coerce")
    df["standard_line"] = pd.to_numeric(df["standard_line"], errors="coerce")
    _mstd = df["pick_type"].astype(str).str.lower().eq("standard")
    df.loc[_mstd, "standard_line"] = df.loc[_mstd, "standard_line"].fillna(df.loc[_mstd, "line"])

    # Dedupe
    before = len(df)
    df = df.drop_duplicates(subset=["projection_id"], keep="first").reset_index(drop=True)
    if before != len(df):
        print(f"  Deduped: {before} → {len(df)} rows")

    # Enforce column order
    df = df[EMPTY_COLS].copy()

    # ── Validation ────────────────────────────────────────────────────────────
    n_rows  = len(df)
    n_teams = df["team"].astype(str).replace("", pd.NA).dropna().nunique()

    print(f"\n✅ Fetched {n_rows} props across {n_teams} teams")
    print(f"   Pick types : {df['pick_type'].value_counts().to_dict()}")
    print(f"   Prop types : {df['prop_type'].nunique()} unique")

    if n_rows < args.min_rows or n_teams < args.min_teams:
        print(f"\n⛔ BOARD_TOO_SMALL — got {n_rows} rows / {n_teams} teams")
        print(f"   Required: min_rows={args.min_rows}, min_teams={args.min_teams}")
        if args.append and out_path.is_file():
            print("   (--append: left existing output file unchanged; pipeline should halt.)")
        else:
            print(f"   Writing partial CSV and exiting with error so pipeline halts.")
            df.to_csv(args.output, index=False, encoding="utf-8-sig")
        sys.exit(1)

    n_fetched = len(df)

    # ── --append: stack new fetch after existing file, semantic dedupe (keep last) ──
    if args.append and out_path.is_file():
        try:
            existing = pd.read_csv(out_path, encoding="utf-8-sig")
            for c in EMPTY_COLS:
                if c not in existing.columns:
                    existing[c] = ""
            existing = existing[EMPTY_COLS].copy()
            existing["line"] = pd.to_numeric(existing["line"], errors="coerce")
            existing["standard_line"] = pd.to_numeric(existing["standard_line"], errors="coerce")
            _mstd_e = existing["pick_type"].astype(str).str.lower().eq("standard")
            existing.loc[_mstd_e, "standard_line"] = existing.loc[_mstd_e, "standard_line"].fillna(
                existing.loc[_mstd_e, "line"]
            )
            combined = pd.concat([existing, df], ignore_index=True)
            # Align dtypes so CSV int pp_game_id matches API string ids for dedupe.
            combined["player"] = combined["player"].astype(str).str.strip()
            combined["prop_type"] = combined["prop_type"].astype(str).str.strip()
            combined["pick_type"] = combined["pick_type"].astype(str).str.strip()
            combined["pp_game_id"] = combined["pp_game_id"].astype(str).str.strip()
            combined["line"] = pd.to_numeric(combined["line"], errors="coerce")
            dedup_candidates = ("player", "prop_type", "line", "pp_game_id", "pick_type")
            dedup_cols = [c for c in dedup_candidates if c in combined.columns]
            if dedup_cols:
                combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
            df = combined
            print(
                f"\n[step1 append] {len(existing)} existing + {n_fetched} new → {len(df)} "
                f"after dedup (subset={dedup_cols})"
            )
        except Exception as e:
            print(f"\n  [WARN] --append merge skipped: {e}")

    # ── Default: union with prior output when file exists (--replace / --append skip) ───
    do_merge = out_path.is_file() and not args.replace and not args.append
    if do_merge:
        try:
            old = pd.read_csv(out_path, encoding="utf-8-sig")
            for c in EMPTY_COLS:
                if c not in old.columns:
                    old[c] = ""
            old = old[EMPTY_COLS].copy()
            old["line"] = pd.to_numeric(old["line"], errors="coerce")
            old["standard_line"] = pd.to_numeric(old["standard_line"], errors="coerce")
            _mstd_o = old["pick_type"].astype(str).str.lower().eq("standard")
            old.loc[_mstd_o, "standard_line"] = old.loc[_mstd_o, "standard_line"].fillna(
                old.loc[_mstd_o, "line"]
            )
            new_ids = set(df["projection_id"].astype(str).str.strip())
            kept = old[~old["projection_id"].astype(str).str.strip().isin(new_ids)]
            n_kept = len(kept)
            df = pd.concat([df, kept], ignore_index=True)
            print(
                f"\n📎 Merged with existing file: +{n_kept} prior-only projection_id rows "
                f"(not in this fetch) → {len(df)} total rows. Use --replace for fetch-only."
            )
        except Exception as e:
            print(f"\n  [WARN] merge with existing file skipped: {e}")
    elif out_path.is_file() and args.replace:
        print("\n📄 --replace: writing this fetch only (no merge with prior file).")

    # ── Date alignment (folder date vs game date) ───────────────────────────
    fetched_rows = len(df)
    filtered_df, fallback_date = _apply_game_date_filter(
        df,
        target_date=str(args.date).strip(),
        tz_name=str(args.tz).strip() or DEFAULT_TZ,
        allow_nearest_future=bool(args.allow_nearest_future),
    )
    board_dates = sorted({d for d in filtered_df.get("game_date", pd.Series([], dtype=object)).astype(str).tolist() if d and d != "nan"})
    print(
        f"[INFO] NBA step1 fetched={fetched_rows} rows; date_filter={args.date} ({args.tz}); "
        f"survived={len(filtered_df)}"
    )
    if board_dates:
        print(f"[INFO] NBA step1 filtered_game_dates={board_dates}")
    if fallback_date:
        print(f"[WARNING] NBA step1 no rows for requested date; using nearest future game_date={fallback_date}")
    df = filtered_df

    # ── Write output ──────────────────────────────────────────────────────────
    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    if len(df) == 0:
        print(f"\n[INFO] Saved empty date-filtered NBA step1 CSV -> {args.output}")
        sys.exit(0)
    print(f"\n✅ Saved → {args.output}")

    # Optional history snapshot
    if args.history:
        try:
            ts        = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_utc")
            hist_path = args.history.replace("{ts}", ts)
            df.to_csv(hist_path, index=False, encoding="utf-8-sig")
            print(f"   History → {hist_path}")
        except Exception as e:
            print(f"  [WARN] History write failed: {e}")

    print("\n✅ BOARD_OK")


if __name__ == "__main__":
    main()
