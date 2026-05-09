#!/usr/bin/env python3
"""
step1_fetch_prizepicks.py  (WNBA Pipeline)

Fetches WNBA PrizePicks projections from the public API.
League ID: 3 (WNBA)

Identical logic to NbaPropPipelineA/step1_fetch_prizepicks_api.py —
only the default league_id differs.

Run:
  py -3.14 step1_fetch_prizepicks.py
  py -3.14 step1_fetch_prizepicks.py --output step1_wnba_props.csv

scripts/run_wnba_pipeline.ps1 defaults to HTTP (curl_cffi) fetch — no --playwright.
Use --playwright or --cdp for browser/DataDome bypass when API returns 403.
"""

from __future__ import annotations

import argparse
import os
import re
import time
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple, Set
from zoneinfo import ZoneInfo

import pandas as pd
import requests

API_URL   = "https://api.prizepicks.com/projections"
WARMUP_URL = "https://api.prizepicks.com/leagues"

# PrizePicks / Cloudflare: optional browser TLS impersonation (same env as NBA step1).
# WNBA default is chrome131 (not NBA's chrome120): confirmed working with curl_cffi 0.15.0 on this stack.
# chrome132 is not supported by curl_cffi 0.15.0 impersonate — upgrade curl_cffi to use newer tokens.
# Override with PROPORACLE_CURL_IMPERSONATE if needed.
_CURL_IMPERSONATE = (os.environ.get("PROPORACLE_CURL_IMPERSONATE") or "chrome131").strip()
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


# Cohesive browser profiles (copied from Sports/NBA/scripts/step1_fetch_prizepicks_api.py).
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
    edge_needle = f"Edg/{major}."
    matched = [
        p
        for p in _BROWSER_PROFILES
        if needle in p.get("User-Agent", "") or edge_needle in p.get("User-Agent", "")
    ]
    if not matched:
        raise RuntimeError(
            f"No User-Agent profile for curl_cffi impersonate={_CURL_IMPERSONATE!r} "
            f"(expected Chrome/{major}. or Edg/{major}. in _BROWSER_PROFILES)."
        )
    return matched


def _validate_client_hints_match_tls(browser_profile_headers: Dict[str, str]) -> None:
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
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


def _random_browser_headers() -> Dict[str, str]:
    pool = _profiles_for_current_transport()
    headers = _browser_headers_from_profile(random.choice(pool))
    _validate_client_hints_match_tls(headers)
    return headers


def _rotate_session_headers(session: Any) -> None:
    session.headers.clear()
    session.headers.update(_random_browser_headers())


PICKTYPE_MAP = {"standard": "Standard", "goblin": "Goblin", "demon": "Demon"}
WNBA_LEAGUE_ID_DEFAULT = "3"
# When the API returns a full board, skip ET --date row pruning in step1 (write all rows + game_date).
# Downstream steps enforce slate scope once game_date / start_time alignment is stable.
FULL_BOARD_PRE_SLATE_MIN = 100
SNAPSHOT_DIR = Path(__file__).resolve().parent / "outputs" / "step1_snapshots"
SNAPSHOT_LATEST_NAME = "step1_wnba_props_latest.csv"
BROWSER_PROFILE_DIR = Path.home() / ".pp_browser_profile"

# aligned with MLB step1 DataDome bypass (Playwright persistent / fresh context)
LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--start-maximized",
    "--disable-infobars",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
    "--window-size=1920,1080",
]

# Omit user_agent: Playwright real Chromium UA must match TLS fingerprint (DataDome). — MLB step1
CTX_KWARGS = dict(
    locale="en-US",
    timezone_id="America/New_York",
    geolocation={"latitude": 33.7490, "longitude": -84.3880},  # Atlanta, GA — aligned with MLB step1 DataDome bypass
    permissions=["geolocation", "notifications"],
    color_scheme="dark",
    extra_http_headers={
        "accept-language": "en-US,en;q=0.9",
        "sec-ch-ua-platform": '"Windows"',
    },
)


def _warm_session(session: Any) -> None:
    try:
        r = session.get(WARMUP_URL, timeout=15)
        print(f"  🌐 Session warmed ({r.status_code})")
        time.sleep(random.uniform(1.5, 3.0))
    except Exception as e:
        print(f"  ⚠️ Warmup failed: {e} — continuing")


def _align_cdp_context_for_datadome(context: Any) -> None:
    """aligned with MLB step1 DataDome bypass — real Chrome CDP contexts skip CTX_KWARGS; set geo explicitly."""
    try:
        context.grant_permissions(
            ["geolocation", "notifications"],
            origin="https://app.prizepicks.com",
        )
    except Exception:
        pass
    try:
        context.set_geolocation({"latitude": 33.7490, "longitude": -84.3880})
    except Exception:
        pass


def _safe_get(d: dict, path: List[str], default=""):
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur if cur is not None else default


def _norm_team(s: str) -> str:
    return str(s or "").strip().upper()


def _included_index(included: List[dict]) -> Dict[Tuple[str, str], dict]:
    idx: Dict[Tuple[str, str], dict] = {}
    for obj in included or []:
        t = str(obj.get("type", "")).strip()
        i = str(obj.get("id",   "")).strip()
        if t and i:
            idx[(t, i)] = obj
    return idx


def fetch_pages(
    league_id: str,
    game_mode: str,
    per_page: int,
    max_pages: int,
    sleep: float,
    cooldown_seconds: float,
    max_cooldowns: int,
    jitter_seconds: float,
    max_403_retries: int = 3,
    forbidden_backoff_base: float = 15.0,
) -> Tuple[List[dict], List[dict]]:
    all_data: List[dict] = []
    all_included: List[dict] = []
    cooldowns_used = 0
    forbidden_retries = 0
    stop_paging = False
    seen_ids: Set[str] = set()

    _log_http_backend_once()
    session = _new_http_session()
    _rotate_session_headers(session)
    _warm_session(session)

    for page in range(1, max_pages + 1):
        if stop_paging:
            break
        params = {
            "league_id": str(league_id),
            "game_mode": str(game_mode),
            "per_page": int(per_page),
            "page": int(page),
            "page[number]": int(page),
            "page[size]": int(per_page),
        }
        for attempt in range(1, 9):
            r = session.get(API_URL, params=params, timeout=30)

            if r.status_code == 429:
                cooldowns_used += 1
                if cooldowns_used > max_cooldowns:
                    print(f"🛑 429 persists after {max_cooldowns} cooldowns. Stopping early.")
                    stop_paging = True
                    break
                sleep_s = cooldown_seconds + random.uniform(0, jitter_seconds)
                print(f"⏸️ 429 cooldown {cooldowns_used}/{max_cooldowns}: sleeping {sleep_s:.1f}s...")
                time.sleep(sleep_s)
                continue

            if r.status_code == 403:
                forbidden_retries += 1
                if forbidden_retries > max_403_retries:
                    print(f"🛑 403 persists. Stopping early.")
                    stop_paging = True
                    break
                try:
                    session.cookies.clear()
                except Exception:
                    pass
                if forbidden_retries >= 2:
                    print(f"⏸️ 403 retry {forbidden_retries}/{max_403_retries}: rotating TLS-matched browser profile…")
                    _rotate_session_headers(session)
                else:
                    print(
                        f"⏸️ 403 retry {forbidden_retries}/{max_403_retries}: "
                        "same client fingerprint; cookies cleared only"
                    )
                backoff = forbidden_backoff_base * (2 ** (forbidden_retries - 1)) + random.uniform(2, 8)
                print(f"⏸️ sleeping {backoff:.1f}s...")
                time.sleep(backoff)
                _warm_session(session)
                continue

            if r.status_code >= 500:
                time.sleep(5.0 * attempt)
                continue

            r.raise_for_status()
            j = r.json()
            page_data = j.get("data") or []
            page_new = [x for x in page_data if str(x.get("id","")) not in seen_ids]
            if not page_new:
                print(f"  Page {page}: 0 new rows — stopping pagination")
                stop_paging = True
                break

            for x in page_new:
                seen_ids.add(str(x.get("id","")))
            all_data.extend(page_new)
            all_included.extend(j.get("included") or [])
            print(f"  Page {page}: +{len(page_new)} rows (total={len(all_data)})")
            time.sleep(sleep + random.uniform(0, 0.5))
            break

    session.close()
    return all_data, all_included


# CDP USAGE — DataDome bypass procedure:
# 1. Start Chrome with --remote-debugging-port=9222 using a profile
#    that has valid PrizePicks cookies (e.g. --profile-directory=Default).
# 2. Open app.prizepicks.com in that window. If DataDome shows a
#    "press and hold" challenge, solve it manually until the board loads.
# 3. Browse normally for ~1 min if challenges repeat (lets risk scoring settle).
# 4. Without closing Chrome, run this script with --cdp http://127.0.0.1:9222
# 5. Confirm log shows projections_status=200. On failure the script exits
#    non-zero (no stale snapshot fallback).
# The in-page fetch() inherits the authenticated session and DataDome trust
# from the open tab — closing or relaunching Chrome resets that trust.

def fetch_via_playwright_session(league_id: str, timeout_s: int, cdp_url: str = "") -> Tuple[List[dict], List[dict], List[dict]]:
    from playwright.sync_api import sync_playwright

    _apply_stealth_fn = None
    try:
        from playwright_stealth import stealth_sync as _stealth_sync_legacy  # type: ignore

        def _apply_stealth_fn(page):  # type: ignore[no-redef]
            _stealth_sync_legacy(page)

        print("  🛡️  playwright-stealth loaded (legacy stealth_sync)")
    except ImportError:
        try:
            from playwright_stealth import Stealth  # type: ignore

            def _apply_stealth_fn(page):  # type: ignore[no-redef]
                Stealth().apply_stealth_sync(page)

            print("  🛡️  playwright-stealth loaded (Stealth v2 API)")
        except ImportError:
            print("  ⚠️  playwright-stealth not installed — run: py -3.14 -m pip install playwright-stealth")

    if not BROWSER_PROFILE_DIR.exists():
        raise RuntimeError(
            f"Browser profile not found at {BROWSER_PROFILE_DIR}. "
            "Run MLB/scripts/setup_prizepicks_profile.py after logging into PrizePicks in Chrome."
        )

    with sync_playwright() as p:
        context = None
        browser = None
        cdp = (cdp_url or "").strip()
        if cdp:
            print(f"🌐 Connecting to existing Chrome via CDP: {cdp}")
            browser = p.chromium.connect_over_cdp(cdp)
            if not browser.contexts:
                raise RuntimeError("CDP browser has no contexts; start Chrome with --remote-debugging-port.")
            context = browser.contexts[0]
            print("  Using browser context[0] (existing session / cookies).")
            # aligned with MLB step1 DataDome bypass — do not override UA; grant Atlanta geo on attached context.
            _align_cdp_context_for_datadome(context)
            page = context.new_page()
        else:
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(BROWSER_PROFILE_DIR),
                    channel="chrome",
                    headless=False,  # aligned with MLB step1 DataDome bypass
                    args=LAUNCH_ARGS,
                    **CTX_KWARGS,
                )
            except Exception:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(BROWSER_PROFILE_DIR),
                    headless=False,
                    args=LAUNCH_ARGS,
                    **CTX_KWARGS,
                )
            page = context.new_page()
        if _apply_stealth_fn is not None:
            _apply_stealth_fn(page)

        page.set_default_timeout(max(30000, int(timeout_s) * 1000))
        page.goto("https://app.prizepicks.com/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)
        page.goto(f"https://app.prizepicks.com/board?league_id={league_id}", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)

        leagues = page.evaluate(
            """async () => {
                const r = await fetch("https://api.prizepicks.com/leagues", { credentials: "include" });
                if (!r.ok) return { data: [], status: r.status };
                return await r.json();
            }"""
        )

        payload = page.evaluate(
            """async ({ leagueId }) => {
                const url = `https://api.prizepicks.com/projections?league_id=${leagueId}&per_page=250&single_stat=true`;
                const r = await fetch(url, { credentials: "include" });
                if (!r.ok) return { data: [], included: [], status: r.status };
                const j = await r.json();
                return {
                    data: Array.isArray(j?.data) ? j.data : [],
                    included: Array.isArray(j?.included) ? j.included : [],
                    status: r.status,
                };
            }""",
            {"leagueId": str(league_id)},
        )
        if cdp:
            page.close()
            browser.close()
        else:
            context.close()

    league_rows = list((leagues or {}).get("data") or [])
    print(f"  [playwright] leagues_status={(leagues or {}).get('status', 200)} rows={len(league_rows)}")
    print(f"  [playwright] projections_status={(payload or {}).get('status', 200)} rows={len((payload or {}).get('data') or [])}")
    return (
        list((payload or {}).get("data") or []),
        list((payload or {}).get("included") or []),
        league_rows,
    )


_ET = ZoneInfo("America/New_York")


def _wnba_start_time_to_et_date_str(ser: pd.Series) -> pd.Series:
    """PrizePicks ISO start_time -> YYYY-MM-DD in America/New_York; empty if unparseable."""
    dt = pd.to_datetime(ser.astype(str).str.strip().replace("", pd.NA), utc=True, errors="coerce")
    et = dt.dt.tz_convert(_ET)
    return et.dt.strftime("%Y-%m-%d").fillna("").astype(str)


def _apply_wnba_slate_date(df: pd.DataFrame, args: Any, *, skip_row_filter: bool = False) -> pd.DataFrame:
    """Optionally filter to --date (ET); set game_date for downstream + combined_slate_tickets."""
    if df is None or df.empty:
        return df
    df = df.copy()
    if "start_time" not in df.columns:
        df["start_time"] = ""
    slate = str(args.date).strip()[:10]
    cal = _wnba_start_time_to_et_date_str(df["start_time"])
    if (
        not skip_row_filter
        and not bool(getattr(args, "no_slate_filter", False))
        and slate
    ):
        keep = cal.eq(slate)
        n0 = len(df)
        df = df.loc[keep].copy().reset_index(drop=True)
        n1 = len(df)
        if n0 != n1:
            print(
                f"  [slate-date] kept {n1}/{n0} rows for slate {slate} "
                f"(start_time ET calendar must match --date)"
            )
    cal = _wnba_start_time_to_et_date_str(df["start_time"])
    if skip_row_filter and slate:
        # Full PrizePicks board spans multiple ET calendar days. combined_slate_tickets keeps rows
        # where game_date == pipeline --date; anchor to that date so props are not dropped there.
        # True tip-off remains in start_time for audits and ESPN alignment.
        df["game_date"] = slate
        print(
            f"  [slate-date] full board: game_date anchored to pipeline date {slate!r} "
            f"(start_time still carries ET tip-off)"
        )
    else:
        df["game_date"] = cal.where(cal.str.len() > 0, slate)
    return df


def _write_snapshots(df: pd.DataFrame, date_tag: str) -> None:
    if df is None or df.empty:
        return
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    dated_path = SNAPSHOT_DIR / f"step1_wnba_props_{date_tag}.csv"
    latest_path = SNAPSHOT_DIR / SNAPSHOT_LATEST_NAME
    df.to_csv(dated_path, index=False, encoding="utf-8-sig")
    df.to_csv(latest_path, index=False, encoding="utf-8-sig")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output",           default="step1_wnba_props.csv")
    ap.add_argument("--league_id",        default=WNBA_LEAGUE_ID_DEFAULT)   # WNBA = 3 (legacy)
    ap.add_argument("--game_mode",        default="pickem")
    ap.add_argument("--per_page",         type=int,   default=250)
    ap.add_argument("--max_pages",        type=int,   default=20)
    ap.add_argument("--sleep",            type=float, default=1.2)
    ap.add_argument("--cooldown_seconds", type=float, default=60.0)
    ap.add_argument("--max_cooldowns",    type=int,   default=2)
    ap.add_argument("--jitter_seconds",   type=float, default=7.0)
    ap.add_argument("--max_403_retries",  type=int,   default=3)
    ap.add_argument("--min_rows",         type=int,   default=30)
    ap.add_argument("--min_teams",        type=int,   default=2)
    ap.add_argument("--date",             default=time.strftime("%Y-%m-%d"))
    ap.add_argument(
        "--no-slate-filter",
        action="store_true",
        help="Keep all PrizePicks rows (multi-day board). Default: keep rows for --date ET calendar only.",
    )
    ap.add_argument("--playwright",       action="store_true")
    ap.add_argument("--cdp",              default="", help="Attach to existing Chrome via CDP URL")
    ap.add_argument("--timeout",          type=int,   default=90)
    ap.add_argument("--print-leagues",    action="store_true")
    args = ap.parse_args()
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = Path(__file__).resolve().parent / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"📡 Fetching PrizePicks WNBA | league_id={args.league_id}")

    data: List[dict] = []
    included: List[dict] = []
    use_playwright = bool(args.playwright) or bool((args.cdp or "").strip())
    if use_playwright:
        try:
            data, included, leagues = fetch_via_playwright_session(
                league_id=str(args.league_id).strip(),
                timeout_s=int(args.timeout),
                cdp_url=str(args.cdp).strip(),
            )
            if args.print_leagues:
                items = []
                for o in leagues:
                    if not isinstance(o, dict):
                        continue
                    lid = str(o.get("id", "")).strip()
                    attr = o.get("attributes") or {}
                    name = str(attr.get("name") or attr.get("abbr") or "").strip()
                    if lid and name:
                        items.append((lid, name))
                items = sorted(items, key=lambda t: t[0])
                print("Active leagues:")
                for lid, name in items:
                    print(f"  - {lid}: {name}")
                if not any("wnba" in n.lower() for _, n in items):
                    print("⚠️ WNBA not present in active leagues payload.")
        except Exception as e:
            print(f"❌ FETCH_FAILED: Playwright fetch failed: {e}")
            print(
                "❌ No projections returned (403 or error). Solve the DataDome challenge in CDP Chrome "
                "and retry with --cdp http://127.0.0.1:9222, or fix HTTP/Playwright. "
                "Do not use stale on-disk step1 in production."
            )
            sys.exit(1)
    else:
        try:
            data, included = fetch_pages(
                league_id=args.league_id,
                game_mode=args.game_mode,
                per_page=args.per_page,
                max_pages=args.max_pages,
                sleep=args.sleep,
                cooldown_seconds=args.cooldown_seconds,
                max_cooldowns=args.max_cooldowns,
                jitter_seconds=args.jitter_seconds,
                max_403_retries=args.max_403_retries,
            )
        except Exception as e:
            print(f"❌ FETCH_FAILED: HTTP fetch failed: {e}")
            print(
                "❌ Solve DataDome / auth (e.g. CDP with --cdp). "
                "Do not use stale on-disk step1 in production."
            )
            sys.exit(1)

    if not data:
        print(
            "❌ FETCH_FAILED: No projections returned (403 or empty API payload). "
            "Solve the DataDome challenge in CDP Chrome and retry with --cdp, or fix HTTP. "
            "Do not use stale on-disk step1 in production."
        )
        sys.exit(1)

    inc = _included_index(included)
    rows: List[dict] = []

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

        player_id   = _safe_get(rel, ["new_player", "data", "id"], "")
        player_type = _safe_get(rel, ["new_player", "data", "type"], "new_player")
        game_id     = _safe_get(rel, ["new_game", "data", "id"], "") or _safe_get(rel, ["game", "data", "id"], "")
        game_type   = _safe_get(rel, ["new_game", "data", "type"], "") or _safe_get(rel, ["game", "data", "type"], "")

        player_obj = inc.get((player_type, str(player_id))) if player_id else None
        game_obj   = inc.get((game_type, str(game_id)))     if game_id and game_type else None

        player_name = pos = team = image_url = ""
        if isinstance(player_obj, dict):
            pa = player_obj.get("attributes") or {}
            player_name = str(pa.get("display_name", pa.get("name", ""))).strip()
            pos         = str(pa.get("position", "")).strip()
            team        = _norm_team(pa.get("team", ""))
            image_url   = str(pa.get("image_url") or pa.get("image_url_small") or "").strip()

        home = away = start_time = ""
        if isinstance(game_obj, dict):
            ga = game_obj.get("attributes") or {}
            home       = _norm_team(ga.get("home_team", ""))
            away       = _norm_team(ga.get("away_team", ""))
            start_time = str(ga.get("start_time", "")).strip()

        if not start_time:
            start_time = str(attrs.get("start_time", "")).strip()

        opp_team = ""
        if team and home and away:
            opp_team = away if team == home else (home if team == away else "")
        else:
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
            "image_url":        image_url,
            "pos":              pos,
            "team":             team,
            "opp_team":         opp_team,
            "pp_home_team":     home,
            "pp_away_team":     away,
            "prop_type":        prop_type,
            "line":             line,
            "pick_type":        pick_type,
        })

    df = pd.DataFrame(rows).fillna("")
    df["line"] = pd.to_numeric(df["line"], errors="coerce")

    before = len(df)
    df = df.drop_duplicates(subset=["projection_id"], keep="first").reset_index(drop=True)
    after = len(df)
    if before != after:
        print(f"  Deduped: {before} → {after}")

    n_pre_slate = len(df)
    skip_slate_row_filter = n_pre_slate >= FULL_BOARD_PRE_SLATE_MIN
    if skip_slate_row_filter:
        print(
            f"  [slate-date] skipping ET date row filter (rows={n_pre_slate} >= {FULL_BOARD_PRE_SLATE_MIN}); "
            "writing full API board — downstream may filter by game_date"
        )
    df = _apply_wnba_slate_date(df, args, skip_row_filter=skip_slate_row_filter)

    rows_n  = len(df)
    teams_n = df["team"].astype(str).nunique()

    if rows_n < args.min_rows or teams_n < args.min_teams:
        print(
            f"❌ FETCH_FAILED: BOARD_TOO_SMALL after slate filter "
            f"(rows={rows_n}, teams={teams_n}; need min_rows={args.min_rows}, min_teams={args.min_teams}). "
            "Stale snapshot fallback is disabled — fix fetch or --date / --no-slate-filter."
        )
        sys.exit(1)

    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    _write_snapshots(df, str(args.date).strip())
    print(f"✅ Saved → {out_path}  rows={rows_n}  teams={teams_n}")

    if rows_n < args.min_rows or teams_n < args.min_teams:
        print(f"⛔ BOARD_TOO_SMALL (need min_rows={args.min_rows}, min_teams={args.min_teams})")
    else:
        print("✅ BOARD_OK")


if __name__ == "__main__":
    main()
