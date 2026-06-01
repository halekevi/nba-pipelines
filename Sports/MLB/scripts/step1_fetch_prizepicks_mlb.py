#!/usr/bin/env python3
"""
step1_fetch_prizepicks_mlb.py  (MLB Pipeline)

Default for run_pipeline.ps1 / run_daily.ps1: Sports/NBA/scripts/step1_fetch_prizepicks_api.py
with --league_id 2 (same flags as NBA). This script remains for manual --cdp / --playwright runs.

Optional legacy path: Playwright intercept (DataDome-heavy). Use only if API fails.

First-time setup (Playwright path only):
    pip install playwright playwright-stealth --break-system-packages
    playwright install chromium
    py -3.14 setup_prizepicks_profile.py

Usage:
    py -3.14 step1_fetch_prizepicks_mlb.py
    py -3.14 step1_fetch_prizepicks_mlb.py --output step1_mlb_props.csv
    py -3.14 step1_fetch_prizepicks_mlb.py --playwright --timeout 90   # legacy browser
    py -3.14 step1_fetch_prizepicks_mlb.py --from-file payload.json
    py -3.14 step1_fetch_prizepicks_mlb.py --cdp http://127.0.0.1:9222   # attach existing Chrome
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

# Ensure repo root is on sys.path so top-level helpers import from any cwd.
_PROPORACLE_ROOT = Path(__file__).resolve().parents[3]
if str(_PROPORACLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROPORACLE_ROOT))

from scripts.db_utils import log_pipeline_health
from utils.step1_slate_date_filter import apply_game_date_filter, no_props_log_line

MLB_LEAGUE_ID = "2"
BOARD_URL     = f"https://app.prizepicks.com/board?league_id={MLB_LEAGUE_ID}"
DEFAULT_TZ = "America/New_York"

TRACKABLE_PROPS = {
    # Hitter
    "hits",
    "total bases",
    "totalbases",
    "home runs",
    "homeruns",
    "rbi",
    "rbis",
    "runs",
    "walks",
    "stolen bases",
    "stolenbases",
    "hitter strikeouts",
    "hitterstrikeouts",
    "hitter ks",
    "fantasy score",
    "fantasyscore",
    "hitter fantasy score",
    "hitterfantasyscore",
    "pitcher fantasy score",
    "pitcherfantasyscore",
    "hits+runs+rbi",
    "hits+runs+rbis",
    "hitsrunsrbi",
    "hitsrunsrbis",
    "singles",
    "doubles",
    "triples",
    "total bases (combo)",
    "plate appearances",
    "plateappearances",
    "pitcher strikeouts + total bases",
    "pitcher strikeouts (combo)",
    # Pitcher
    "strikeouts",
    "pitcher strikeouts",
    "pitching outs",
    "pitchingouts",
    "hits allowed",
    "hitsallowed",
    "earned runs",
    "earned runs allowed",
    "earnedrunsallowed",
    "earnedrunsr",
    "walks allowed",
    "walksallowed",
    "1st inning runs allowed",
    "1stinningrunsallowed",
    "1st inning walks allowed",
    "1stinningwalksallowed",
    "innings pitched",
    "inningspitched",
    "pitches thrown",
    "pitchesthrown",
}

PICKTYPE_MAP = {"standard": "Standard", "goblin": "Goblin", "demon": "Demon"}

EMPTY_COLS = [
    "projection_id", "pp_projection_id", "player_id", "pp_game_id", "start_time",
    "player", "player_name", "image_url", "pos", "team", "opp_team", "pp_home_team", "pp_away_team",
    "prop_type", "line", "line_score", "standard_line", "pick_type", "sport",
]

PROFILE_DIR = Path.home() / ".pp_browser_profile"
SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "outputs" / "step1_snapshots"
SNAPSHOT_LATEST_NAME = "step1_mlb_props_latest.csv"

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

# Omit user_agent: Playwright's real Chromium UA must match TLS fingerprint (DataDome).
CTX_KWARGS = dict(
    locale="en-US",
    timezone_id="America/New_York",
    geolocation={"latitude": 33.7490, "longitude": -84.3880},  # Atlanta, GA — align with typical SE US IP
    permissions=["geolocation", "notifications"],
    color_scheme="dark",
    extra_http_headers={
        "accept-language": "en-US,en;q=0.9",
        "sec-ch-ua-platform": '"Windows"',
    },
)

# PrizePicks API endpoints to intercept
CAPTURE_PATTERNS = [
    "api.prizepicks.com/projections",
    "api.prizepicks.com/boards",
    "api.prizepicks.com/offers",
    "api.prizepicks.com/graphql",
    "api.prizepicks.com/v1/projections",
    "api.prizepicks.com/v2/projections",
]
GAME_PATTERN = "api.prizepicks.com/games"


def _ensure_utf8_stdio() -> None:
    """Avoid UnicodeEncodeError on Windows (cp1252) when logs use emoji."""
    for _stream in (sys.stdout, sys.stderr):
        reconf = getattr(_stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _default_et_date_str() -> str:
    return datetime.now(ZoneInfo(DEFAULT_TZ)).date().isoformat()


# ─── helpers ──────────────────────────────────────────────────────────────────

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


def _try_extract_projections(j: dict) -> Tuple[List[dict], List[dict]]:
    """Try multiple known PrizePicks response shapes to extract (data, included)."""
    # Shape 1: standard JSONAPI  {"data": [...], "included": [...]}
    if isinstance(j.get("data"), list):
        data = j["data"]
        proj = [x for x in data if isinstance(x, dict) and x.get("type") == "projection"]
        if not proj:
            proj = data  # untyped — take all; parse_rows will skip non-projections
        if proj:
            return proj, j.get("included") or []

    # Shape 2: {"projections": [...]}
    if isinstance(j.get("projections"), list) and j["projections"]:
        return j["projections"], j.get("included") or []

    # Shape 3: graphql {"data": {"projections": {"edges": [...]}}}
    gql_data = j.get("data") if isinstance(j.get("data"), dict) else {}
    for key in ("projections", "board", "offers"):
        node = gql_data.get(key)
        if isinstance(node, dict):
            edges = node.get("edges") or node.get("nodes") or []
            items = [e.get("node", e) for e in edges if isinstance(e, dict)]
            if items:
                return items, []
        if isinstance(node, list) and node:
            return node, []

    return [], []


def parse_rows(data: List[dict], included: List[dict]) -> List[dict]:
    idx  = _included_index(included)
    rows = []

    for item in data:
        if item.get("type") != "projection":
            continue

        attrs    = item.get("attributes") or {}
        rels     = item.get("relationships") or {}
        pid      = str(item.get("id", "")).strip()

        prop_type_raw = str(attrs.get("stat_type", "") or attrs.get("prop_type", "")).strip()
        prop_type     = prop_type_raw.lower()
        line          = attrs.get("line_score") or attrs.get("line") or ""
        # PrizePicks API uses "odds_type" (new) — fall back to "pick_type" (legacy)
        pick_type_raw = str(attrs.get("odds_type") or attrs.get("pick_type") or "standard").strip().lower()
        pick_type     = PICKTYPE_MAP.get(pick_type_raw, pick_type_raw.capitalize())
        std_api = attrs.get("standard_line") or attrs.get("standard_score") or attrs.get("baseline")
        if pick_type == "Standard":
            standard_line = std_api if std_api is not None and str(std_api).strip() != "" else line
        else:
            standard_line = std_api if std_api is not None else ""

        # resolve player
        player_rel = rels.get("new_player") or rels.get("player") or {}
        player_id  = ""
        player_obj = None
        pd_data    = player_rel.get("data") or {}
        if isinstance(pd_data, dict):
            player_id  = str(pd_data.get("id", "")).strip()
            player_obj = idx.get(("new_player", player_id)) or idx.get(("player", player_id))

        # resolve game — try new_game first, fall back to game
        game_id   = _safe_get(rels, ["new_game", "data", "id"], "") or _safe_get(rels, ["game", "data", "id"], "")
        game_type = _safe_get(rels, ["new_game", "data", "type"], "") or _safe_get(rels, ["game", "data", "type"], "")
        game_obj  = idx.get((str(game_type), str(game_id))) if game_id and game_type else None

        player_name = pos = team = image_url = ""
        if isinstance(player_obj, dict):
            pa          = player_obj.get("attributes") or {}
            player_name = str(pa.get("display_name", pa.get("name", ""))).strip()
            pos         = str(pa.get("position", "")).strip()
            team        = _norm_team(pa.get("team", ""))
            image_url   = str(pa.get("image_url") or pa.get("image_url_small") or "").strip()

        home = away = start_time = ""
        if isinstance(game_obj, dict):
            ga         = game_obj.get("attributes") or {}
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
            m    = re.search(r"\bvs\.?\s+([A-Za-z]{2,4})\b", desc)
            if m:
                opp_team = _norm_team(m.group(1))

        rows.append({
            "projection_id":    pid,
            "pp_projection_id": pid,
            "player_id":        str(player_id).strip(),
            "pp_game_id":       str(game_id or "").strip(),
            "start_time":       start_time,
            "player":           player_name,
            "player_name":      player_name,
            "image_url":        image_url,
            "pos":              pos,
            "team":             team,
            "opp_team":         opp_team,
            "pp_home_team":     home,
            "pp_away_team":     away,
            "prop_type":        prop_type_raw,
            "line":             line,
            "line_score":       line,
            "standard_line":    standard_line,
            "pick_type":        pick_type,
            "sport":            "MLB",
        })

    return rows


def _load_prizepicks_api_module():
    """Load NBA direct-API fetcher from repo (shared PrizePicks JSONAPI client)."""
    root = Path(__file__).resolve().parents[3]
    candidates = [
        root / "Sports" / "NBA" / "scripts" / "step1_fetch_prizepicks_api.py",
        root / "NBA" / "scripts" / "step1_fetch_prizepicks_api.py",
    ]
    path = next((c for c in candidates if c.exists()), candidates[0])
    spec = importlib.util.spec_from_file_location("pp_fetch_api_mlb", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(
            "Cannot load API module spec. Tried: "
            + ", ".join(str(c) for c in candidates)
        )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fetch_via_direct_api(
    *,
    per_page: int,
    max_pages: int,
    retries: int,
    inter_min: float,
    inter_max: float,
    session_min: float,
    session_max: float,
    first_page_waves: int,
    wave_gap_min: float,
    wave_gap_max: float,
    forbid_cooldown_threshold: int,
    forbid_cooldown_seconds: float,
    forbid_cooldown_jitter_min: float,
    forbid_cooldown_jitter_max: float,
) -> Tuple[List[dict], List[dict]]:
    mod = _load_prizepicks_api_module()
    return mod.fetch_projections(
        league_id=MLB_LEAGUE_ID,
        per_page=per_page,
        max_pages=max_pages,
        retries=retries,
        inter_page_delay=(inter_min, inter_max),
        session_jitter=(session_min, session_max),
        first_page_waves=first_page_waves,
        wave_gap_seconds=(wave_gap_min, wave_gap_max),
        forbid_cooldown_threshold=forbid_cooldown_threshold,
        forbid_cooldown_seconds=forbid_cooldown_seconds,
        forbid_cooldown_jitter=(forbid_cooldown_jitter_min, forbid_cooldown_jitter_max),
    )


def load_payload_from_file(path: str) -> Tuple[List[dict], List[dict]]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        return [], []
    data     = raw.get("data") or []
    included = raw.get("included") or []
    return (data if isinstance(data, list) else []), (included if isinstance(included, list) else [])


def _write_snapshot(df: pd.DataFrame, target_date: str) -> None:
    if df is None or df.empty:
        return
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    dated_path = SNAPSHOT_DIR / f"step1_mlb_props_{target_date}.csv"
    latest_path = SNAPSHOT_DIR / SNAPSHOT_LATEST_NAME
    df.to_csv(dated_path, index=False, encoding="utf-8-sig")
    df.to_csv(latest_path, index=False, encoding="utf-8-sig")


def _backfill_opp_from_game_context(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fill opp_team from pp_game_id + team when upstream payload lacks game home/away.
    Handles both single-team and slash-combo team values.
    """
    if df is None or len(df) == 0:
        return df
    if "pp_game_id" not in df.columns or "team" not in df.columns:
        return df
    if "opp_team" not in df.columns:
        df["opp_team"] = ""

    out = df.copy()
    out["pp_game_id"] = out["pp_game_id"].astype(str).str.strip()
    out["team"] = out["team"].astype(str).str.strip().str.upper()
    out["opp_team"] = out["opp_team"].fillna("").astype(str).str.strip().str.upper()

    singles = out.copy()
    singles["team_single"] = singles["team"].str.split("/").str[0].str.strip()
    valid = singles[singles["pp_game_id"].ne("") & singles["team_single"].ne("")]
    teams_per_game = (
        valid.groupby("pp_game_id")["team_single"]
        .apply(lambda s: sorted({str(v).strip().upper() for v in s if str(v).strip()}))
        .reset_index()
    )
    teams_per_game.columns = ["pp_game_id", "_teams"]
    two_team = teams_per_game[teams_per_game["_teams"].apply(len) == 2].copy()
    two_team["_team_a"] = two_team["_teams"].apply(lambda t: t[0])
    two_team["_team_b"] = two_team["_teams"].apply(lambda t: t[1])

    out = out.merge(two_team[["pp_game_id", "_team_a", "_team_b"]], on="pp_game_id", how="left")
    needs_opp = out["opp_team"].eq("")

    team_single = out["team"].str.split("/").str[0].str.strip()
    team_second = out["team"].str.split("/").str[1].fillna("").str.strip()
    is_combo = out["team"].str.contains("/", regex=False, na=False)

    # Singles
    out.loc[needs_opp & ~is_combo & team_single.eq(out["_team_a"]), "opp_team"] = out["_team_b"]
    out.loc[needs_opp & ~is_combo & team_single.eq(out["_team_b"]), "opp_team"] = out["_team_a"]

    # Slash combos: TEAM1/TEAM2 -> OPP1/OPP2
    combo_ok = needs_opp & is_combo & team_single.ne("") & team_second.ne("")
    opp1 = out["_team_b"].where(team_single.eq(out["_team_a"]), out["_team_a"])
    opp2 = out["_team_b"].where(team_second.eq(out["_team_a"]), out["_team_a"])
    out.loc[combo_ok, "opp_team"] = (
        opp1.fillna("").astype(str).str.strip().str.upper()
        + "/"
        + opp2.fillna("").astype(str).str.strip().str.upper()
    ).str.strip("/")
    out.loc[combo_ok, "opp_team"] = out.loc[combo_ok, "opp_team"].replace("/", "")

    out = out.drop(columns=["_team_a", "_team_b", "_teams"], errors="ignore")
    return out



# ─── Playwright fetch — fully headless, no manual window ──────────────────────

# CDP USAGE — DataDome bypass procedure:
# 1. Start Chrome with --remote-debugging-port=9222 using a profile
#    that has valid PrizePicks cookies (e.g. --profile-directory=Default).
# 2. Open app.prizepicks.com in that window. If DataDome shows a
#    "press and hold" challenge, solve it manually until the board loads.
# 3. Browse normally for ~1 min if challenges repeat (lets risk scoring settle).
# 4. Without closing Chrome, run this script with --cdp http://127.0.0.1:9222
# 5. Confirm capture succeeds (projection rows). On failure the script exits
#    non-zero (no stale snapshot fallback).
# The in-page fetch() inherits the authenticated session and DataDome trust
# from the open tab — closing or relaunching Chrome resets that trust.

def fetch_via_playwright(timeout_s: int = 90, cdp_url: str | None = None) -> Tuple[List[dict], List[dict]]:
    """
    Either launch Chromium (saved ~/.pp_browser_profile when present) or attach
    to an existing Chrome via CDP (--cdp) after you have solved login/DataDome.

    Strategy:
      1. Navigate to the MLB board URL
      2. Intercept any api.prizepicks.com response with projection data
      3. After projections land, wait up to GAMES_GRACE seconds for /games objects
      4. Trigger scroll/click events to nudge lazy API calls if needed
      5. Return (data, included) — empty lists if nothing intercepted
    """
    _ensure_utf8_stdio()
    from playwright.sync_api import sync_playwright

    captured:          dict  = {}
    have_projections:  bool  = False
    projections_time:  float = 0.0
    seen_urls:         list  = []

    # playwright-stealth: legacy `stealth_sync` (old wheels) vs v2 `Stealth().apply_stealth_sync` (pypi 2.x).
    _apply_stealth_fn = None
    try:
        from playwright_stealth import stealth_sync as _stealth_sync_legacy

        def _apply_stealth_fn(page):  # type: ignore[no-redef]
            _stealth_sync_legacy(page)

        print("  🛡️  playwright-stealth loaded (legacy stealth_sync)")
    except ImportError:
        try:
            from playwright_stealth import Stealth

            def _apply_stealth_fn(page):  # type: ignore[no-redef]
                Stealth().apply_stealth_sync(page)

            print("  🛡️  playwright-stealth loaded (Stealth v2 API)")
        except ImportError:
            print("  ⚠️  playwright-stealth not installed — run: py -3.14 -m pip install playwright-stealth")

    def handle_response(response):
        nonlocal have_projections, projections_time
        url = response.url

        if "api.prizepicks.com" in url or "prizepicks.com/api" in url:
            status = response.status
            seen_urls.append(f"  [{status}] {url}")
            print(f"  🔍 PP response: [{status}] {url}")

            if status != 200:
                return
            try:
                j = response.json()
            except Exception:
                print(f"       └─ not JSON, skipping")
                return

            # Capture game objects from the /games endpoint
            if GAME_PATTERN in url and isinstance(j.get("data"), list):
                game_objs = [x for x in j["data"] if isinstance(x, dict)
                             and x.get("type") in ("game", "new_game", "scheduled_game")]
                if not game_objs:
                    game_objs = [x for x in j["data"] if isinstance(x, dict) and x.get("id")]
                if game_objs:
                    captured.setdefault("included", []).extend(game_objs)
                    print(f"  ✓ Captured {len(game_objs)} game objects from /games")
                return

            # Only accept projection responses with league_id=2 (MLB)
            if not any(pat in url for pat in CAPTURE_PATTERNS):
                return
            if "league_id=2" not in url:
                print(f"       └─ skipping (not league_id=2)")
                return

            data, included = _try_extract_projections(j)
            if data:
                captured["data"] = data
                captured.setdefault("included", [])
                captured["included"].extend(included)
                have_projections  = True
                projections_time  = time.time()
                print(f"  ✓ Captured {len(data)} projections from {url}")

    def _capture_complete() -> bool:
        if not have_projections:
            return False
        elapsed    = time.time() - projections_time
        have_games = any(
            obj.get("type") in ("game", "new_game", "scheduled_game") or
            ("home_team" in (obj.get("attributes") or {}))
            for obj in captured.get("included", [])
        )
        return have_games or elapsed >= GAMES_GRACE

    GAMES_GRACE = 10.0  # seconds to wait after projections for /games response

    cdp = (cdp_url or "").strip()
    use_cdp = bool(cdp)

    use_profile = PROFILE_DIR.exists()
    cold_context = False  # True when not using persistent profile (no cookies / cold DataDome score)
    if not use_cdp and not use_profile:
        print(f"  ⚠️  No saved profile found at {PROFILE_DIR}")
        print(f"       Run: py -3.14 setup_prizepicks_profile.py")
        print(f"       Falling back to fresh browser (may hit DataDome challenge)...")

    with sync_playwright() as p:
        browser = None
        if use_cdp:
            print(f"🌐 Connecting to existing Chrome via CDP: {cdp}")
            browser = p.chromium.connect_over_cdp(cdp)
            if not browser.contexts:
                raise RuntimeError("CDP browser has no contexts (is Chrome running with --remote-debugging-port?)")
            context = browser.contexts[0]
            print(f"  Using browser context[0] (existing session / cookies).")
        elif use_profile:
            print(f"🌐 Launching Chromium with saved profile: {PROFILE_DIR}")
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(PROFILE_DIR),
                    headless=False,         # visible browser — bypasses DataDome fingerprinting
                    args=LAUNCH_ARGS,
                    **CTX_KWARGS,
                )
                browser = None
            except Exception as e:
                print(f"  ⚠️  Profile launch failed ({type(e).__name__}). Falling back to fresh browser context.")
                cold_context = True
                browser  = p.chromium.launch(headless=False, args=LAUNCH_ARGS)
                context  = browser.new_context(viewport={"width": 1920, "height": 1080}, **CTX_KWARGS)
        else:
            cold_context = True
            print("🌐 Launching Chromium (no saved profile)...")
            browser  = p.chromium.launch(headless=False, args=LAUNCH_ARGS)
            context  = browser.new_context(viewport={"width": 1920, "height": 1080}, **CTX_KWARGS)

        page = context.new_page()

        if _apply_stealth_fn is not None:
            _apply_stealth_fn(page)

        page.on("response", handle_response)

        if cold_context:
            # Let first-party cookies / DataDome challenge settle before board XHRs.
            print("  Warming fresh context: https://app.prizepicks.com/ …")
            try:
                page.goto("https://app.prizepicks.com/", timeout=30_000, wait_until="domcontentloaded")
                time.sleep(5)
            except Exception as e:
                print(f"  ⚠️  Warm navigation warning (continuing): {e}")

        print(f"  Loading {BOARD_URL}")
        try:
            page.goto(BOARD_URL, timeout=30_000, wait_until="domcontentloaded")
            # Let DataDome / board JS settle before scroll nudges (cold sessions need longer).
            time.sleep(12)
        except Exception as e:
            print(f"  ⚠️  Page load warning (continuing): {e}")

        if os.environ.get("PROPORACLE_LOG_PLAYWRIGHT_UA", "").strip().lower() in ("1", "true", "yes"):
            try:
                ua = page.evaluate("navigator.userAgent")
                print(f"  [step1 MLB] navigator.userAgent={ua}")
            except Exception as ex:
                print(f"  [step1 MLB] navigator.userAgent (unavailable): {type(ex).__name__}: {ex}")

        # Auto-scroll/trigger sequence — nudges lazy API calls immediately
        # No manual window: scheduled runs can't have human interaction
        triggers = [
            "window.scrollBy(0, 300)",
            "window.scrollBy(0, 600)",
            "document.body.click()",
            "window.scrollBy(0, -300)",
            "window.dispatchEvent(new Event('resize'))",
            "window.scrollBy(0, 900)",
            "window.scrollBy(0, 0)",
        ]

        deadline    = time.time() + timeout_s
        trigger_idx = 0

        while not _capture_complete() and time.time() < deadline:
            if trigger_idx < len(triggers):
                try:
                    page.evaluate(triggers[trigger_idx])
                    trigger_idx += 1
                except Exception:
                    pass
                time.sleep(3.5)
            else:
                time.sleep(2.0)

        if _capture_complete():
            print("  ✅ Capture complete.")
        elif have_projections:
            print("  ✅ Have projections (no game objects within grace window — continuing).")
        else:
            if seen_urls:
                print("\n  📋 All PrizePicks API calls seen (none had projection data):")
                for u in seen_urls:
                    print(u)
            else:
                print("\n  ⚠️  No api.prizepicks.com calls detected at all.")
                print("       Profile may need refreshing: py -3.14 setup_prizepicks_profile.py")

        if use_cdp:
            try:
                page.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
        else:
            context.close()
            if browser:
                browser.close()

    return captured.get("data", []), captured.get("included", [])


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output",         default="step1_mlb_props.csv")
    ap.add_argument("--from-file",      default="",   help="Load raw PrizePicks JSON from file instead of live fetch")
    ap.add_argument("--timeout",        type=int, default=90,  help="Total seconds to wait for intercept (default 90)")
    ap.add_argument("--retries",        type=int, default=2,   help="Extra browser launch attempts on miss (default 2)")
    ap.add_argument("--retry_delay",    type=int, default=10,  help="Seconds to wait between retry attempts (default 10)")
    ap.add_argument("--min_rows",       type=int, default=30)
    ap.add_argument("--min_teams",      type=int, default=2)
    ap.add_argument("--all_props",      action="store_true",   help="Keep all prop types unfiltered")
    ap.add_argument("--date", default=_default_et_date_str(), help=f"Target game date in {DEFAULT_TZ} (YYYY-MM-DD).")
    ap.add_argument("--tz", default=DEFAULT_TZ, help="Timezone used to derive game_date from start_time.")
    ap.add_argument(
        "--allow-nearest-future",
        action="store_true",
        help="Skip same-day date filter (keep full API board; explicit opt-in only).",
    )
    # Compat aliases — accepted silently so existing pipeline calls don't break
    ap.add_argument("--gentle",         action="store_true",   help="(compat) no-op")
    ap.add_argument("--manual-seconds", type=int, default=None, help="(compat) no-op — manual window removed")
    ap.add_argument("--manual_window",  type=int, default=None, help="(compat) no-op — manual window removed")
    ap.add_argument(
        "--append",
        action="store_true",
        help="Append this fetch after existing CSV rows, then dedupe (keep='last').",
    )
    ap.add_argument(
        "--cdp",
        default="",
        metavar="URL",
        help="Attach to existing Chrome via CDP (e.g. http://127.0.0.1:9222). "
        "Start Chrome with --remote-debugging-port; skips launching a new browser.",
    )
    ap.add_argument(
        "--playwright",
        action="store_true",
        help="Force Playwright intercept instead of direct API (legacy / DataDome fallback).",
    )
    ap.add_argument("--per-page", type=int, default=250, help="Direct API: per_page (default 250).")
    ap.add_argument("--max-pages", type=int, default=8, help="Direct API: max pagination pages (default 8).")
    ap.add_argument("--api-retries", type=int, default=4, help="Direct API: retries per GET inside each session wave (default 4).")
    ap.add_argument(
        "--api-session-waves",
        type=int,
        default=2,
        help="Direct API: fresh TCP session attempts if page-1 still fails after all retries (default 2).",
    )
    ap.add_argument("--api-inter-min", type=float, default=8.0, help="Direct API: min seconds between paginated requests.")
    ap.add_argument("--api-inter-max", type=float, default=18.0, help="Direct API: max seconds between paginated requests.")
    ap.add_argument("--api-session-min", type=float, default=8.0, help="Direct API: min seconds before first request.")
    ap.add_argument("--api-session-max", type=float, default=18.0, help="Direct API: max seconds before first request.")
    ap.add_argument(
        "--api-wave-gap-min",
        type=float,
        default=22.0,
        help="Direct API: min seconds between session waves after page-1 failure.",
    )
    ap.add_argument(
        "--api-wave-gap-max",
        type=float,
        default=48.0,
        help="Direct API: max seconds between session waves after page-1 failure.",
    )
    ap.add_argument(
        "--api-403-cooldown-after",
        type=int,
        default=5,
        help="Direct API: trigger cooldown window after this many consecutive 403s.",
    )
    ap.add_argument(
        "--api-403-cooldown-seconds",
        type=float,
        default=90.0,
        help="Direct API: base cooldown duration once consecutive 403 threshold is reached.",
    )
    ap.add_argument(
        "--api-403-cooldown-jitter-min",
        type=float,
        default=12.0,
        help="Direct API: extra random cooldown seconds (min) after repeated 403s.",
    )
    ap.add_argument(
        "--api-403-cooldown-jitter-max",
        type=float,
        default=40.0,
        help="Direct API: extra random cooldown seconds (max) after repeated 403s.",
    )
    args     = ap.parse_args()
    _ensure_utf8_stdio()
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Fetch ────────────────────────────────────────────────────────────────
    data: list     = []
    included: list = []
    max_attempts   = 1
    fetch_method   = "direct_api"

    use_playwright = bool((args.cdp or "").strip()) or bool(args.playwright)

    if args.from_file:
        print(f"[step1] Loading MLB payload from file: {args.from_file}")
        data, included = load_payload_from_file(args.from_file)
        fetch_method = "from_file"
    elif not use_playwright:
        print(
            f"[step1] MLB fetch (direct_api) | league_id={MLB_LEAGUE_ID} | "
            f"per_page={args.per_page} max_pages={args.max_pages} | "
            f"retries={args.api_retries} session_waves={args.api_session_waves}"
        )
        try:
            wave_lo, wave_hi = sorted(
                (float(args.api_wave_gap_min), float(args.api_wave_gap_max))
            )
            data, included = fetch_via_direct_api(
                per_page=int(args.per_page),
                max_pages=int(args.max_pages),
                retries=int(args.api_retries),
                inter_min=float(args.api_inter_min),
                inter_max=float(args.api_inter_max),
                session_min=float(args.api_session_min),
                session_max=float(args.api_session_max),
                first_page_waves=int(args.api_session_waves),
                wave_gap_min=wave_lo,
                wave_gap_max=wave_hi,
                forbid_cooldown_threshold=int(args.api_403_cooldown_after),
                forbid_cooldown_seconds=float(args.api_403_cooldown_seconds),
                forbid_cooldown_jitter_min=float(args.api_403_cooldown_jitter_min),
                forbid_cooldown_jitter_max=float(args.api_403_cooldown_jitter_max),
            )
        except Exception as e:
            data, included = [], []
            print(f"  ❌ Direct API fetch failed: {type(e).__name__}: {e}")
            print(
                "  [HINT] Persistent 403: use Chrome remote debugging (real session) — "
                "pwsh -NoProfile -File scripts\\run_mlb_step1_chrome_debug.ps1 "
                "(see docs\\chrome_debug_setup.md)"
            )
        max_attempts = 1
    else:
        fetch_method = "cdp" if (args.cdp or "").strip() else "playwright"
        max_attempts = 1 + max(0, args.retries)
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                print(f"\n🔄 Retry {attempt - 1}/{args.retries} — relaunching in {args.retry_delay}s...")
                time.sleep(args.retry_delay)
            mode = "cdp" if (args.cdp or "").strip() else "playwright"
            print(f"[step1] MLB fetch ({mode}) | league_id={MLB_LEAGUE_ID} | attempt {attempt}/{max_attempts}")
            try:
                cdp = (args.cdp or "").strip() or None
                data, included = fetch_via_playwright(timeout_s=args.timeout, cdp_url=cdp)
            except Exception as e:
                # Do not preserve stale boards when browser launch/intercept crashes.
                # Treat this attempt as a miss so the empty-file guard can run if all retries fail.
                data, included = [], []
                print(f"  ⚠️  Attempt {attempt} crashed: {type(e).__name__}: {e}")
            if data:
                print(f"  ✅ Captured on attempt {attempt}")
                break
            print(f"  ⚠️  Attempt {attempt} missed intercept.")

    # ── Empty guard ──────────────────────────────────────────────────────────
    if not data:
        print(
            "❌ FETCH_FAILED: No projections returned after all attempts (403 / intercept miss / API error). "
            "Use CDP with a DataDome-cleared Chrome session (see script header). "
            "Stale snapshot fallback is disabled — do not ship picks from old step1."
        )
        if args.append and out_path.is_file():
            print("   (--append: left existing output file unchanged)")
        else:
            pd.DataFrame(columns=EMPTY_COLS).to_csv(out_path, index=False, encoding="utf-8-sig")
            print("   Wrote empty CSV.")
        log_pipeline_health(
            "mlb.step1_fetch_prizepicks",
            "no_projections",
            extra={"league_id": MLB_LEAGUE_ID, "method": fetch_method, "attempts": max_attempts},
            start=Path(__file__),
        )
        sys.exit(1)

    # ── Parse ────────────────────────────────────────────────────────────────
    rows = parse_rows(data, included)
    df   = pd.DataFrame(rows).fillna("")
    df["line"] = pd.to_numeric(df["line"], errors="coerce")
    df["standard_line"] = pd.to_numeric(df["standard_line"], errors="coerce")
    _mstd = df["pick_type"].astype(str).str.lower().eq("standard")
    df.loc[_mstd, "standard_line"] = df.loc[_mstd, "standard_line"].fillna(df.loc[_mstd, "line"])

    # Fill missing opponents from game context as a robust fallback.
    df = _backfill_opp_from_game_context(df)

    # Bouncer: remove junk rows
    required = ["projection_id", "player", "team", "prop_type"]
    for c in required:
        if c not in df.columns:
            print(f"⚠️  Missing required column: {c}")
            print("❌ FETCH_FAILED: invalid projection payload — stale snapshot fallback disabled.")
            if not (args.append and out_path.is_file()):
                df.to_csv(out_path, index=False, encoding="utf-8-sig")
            sys.exit(1)

    before_bounce = len(df)
    df = df[df["projection_id"].astype(str).str.strip() != ""].copy()
    df = df[df["player"].astype(str).str.strip()        != ""].copy()
    df = df[df["team"].astype(str).str.strip()          != ""].copy()
    df = df[(df["line"].isna()) | (df["line"] >= 0)].copy()
    bounced = before_bounce - len(df)
    if bounced:
        print(f"  🧹 Bouncer: removed {bounced} junk rows")

    # Dedupe
    before = len(df)
    df = df.drop_duplicates(subset=["projection_id"], keep="first").reset_index(drop=True)
    if before != len(df):
        print(f"  Deduped: {before} → {len(df)}")

    # Prop filter
    if not args.all_props:
        mask    = df["prop_type"].str.lower().str.replace(" ", "").isin(
            {p.replace(" ", "") for p in TRACKABLE_PROPS}
        )
        dropped = (~mask).sum()
        df      = df[mask].reset_index(drop=True)
        if dropped:
            print(f"  Filtered out {dropped} non-trackable props (use --all_props to keep all)")

    rows_n_new = len(df)
    teams_n_new = df["team"].astype(str).nunique()
    small_board = rows_n_new < args.min_rows or teams_n_new < args.min_teams

    if args.append and out_path.is_file():
        if small_board:
            print(
                f"\n⛔ BOARD_TOO_SMALL on new fetch — got {rows_n_new} rows / {teams_n_new} teams "
                f"(need min_rows={args.min_rows}, min_teams={args.min_teams})"
            )
            print("❌ FETCH_FAILED: stale snapshot fallback disabled.")
            print("   (--append: left existing output file unchanged)")
            log_pipeline_health(
                "mlb.step1_fetch_prizepicks",
                "board_too_small",
                extra={"rows": rows_n_new, "teams": teams_n_new, "append": True},
                start=Path(__file__),
            )
            sys.exit(1)
        try:
            existing = pd.read_csv(out_path, encoding="utf-8-sig")
            n_existing = len(existing)
            for c in EMPTY_COLS:
                if c not in existing.columns:
                    existing[c] = ""
            existing = existing[EMPTY_COLS].copy()
            existing["line"] = pd.to_numeric(existing["line"], errors="coerce")
            existing["standard_line"] = pd.to_numeric(existing["standard_line"], errors="coerce")
            _eo = existing["pick_type"].astype(str).str.lower().eq("standard")
            existing.loc[_eo, "standard_line"] = existing.loc[_eo, "standard_line"].fillna(
                existing.loc[_eo, "line"]
            )

            for c in EMPTY_COLS:
                if c not in df.columns:
                    df[c] = ""
            df = df[EMPTY_COLS].copy()
            df["line"] = pd.to_numeric(df["line"], errors="coerce")
            df["standard_line"] = pd.to_numeric(df["standard_line"], errors="coerce")
            _dn = df["pick_type"].astype(str).str.lower().eq("standard")
            df.loc[_dn, "standard_line"] = df.loc[_dn, "standard_line"].fillna(df.loc[_dn, "line"])

            combined = pd.concat([existing, df], ignore_index=True)
            for col in ("player", "prop_type", "pick_type", "pp_game_id"):
                if col in combined.columns:
                    combined[col] = combined[col].astype(str).str.strip()
            combined["line"] = pd.to_numeric(combined["line"], errors="coerce")
            dedup_cols = [
                c for c in ("player", "prop_type", "line", "pp_game_id", "pick_type") if c in combined.columns
            ]
            if dedup_cols:
                combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
            df = combined
            print(
                f"[step1 MLB append] {n_existing} existing + {rows_n_new} new → "
                f"{len(df)} after dedup (subset={dedup_cols})"
            )
        except Exception as e:
            print(f"  [WARN] --append merge failed ({e}); writing new fetch only")

    fetched_rows = len(df)
    filtered_df, fallback_date = apply_game_date_filter(
        df,
        target_date=str(args.date).strip(),
        tz_name=str(args.tz).strip() or DEFAULT_TZ,
        allow_nearest_future=bool(args.allow_nearest_future),
    )
    game_dates = sorted({d for d in filtered_df.get("game_date", pd.Series([], dtype=object)).astype(str).tolist() if d and d != "nan"})
    print(
        f"[INFO] MLB step1 fetched={fetched_rows} rows; date_filter={args.date} ({args.tz}); "
        f"survived={len(filtered_df)}"
    )
    if game_dates:
        print(f"[INFO] MLB step1 filtered_game_dates={game_dates}")
    if fallback_date:
        print("[WARNING] MLB step1 allow-nearest-future: skipping date filter")
    df = filtered_df

    if len(df) == 0:
        print(no_props_log_line("MLB", str(args.date).strip()))
        pd.DataFrame(columns=EMPTY_COLS).to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n[INFO] Saved empty date-filtered MLB step1 CSV -> {out_path}")
        sys.exit(0)

    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    try:
        if str(_PROPORACLE_ROOT) not in sys.path:
            sys.path.insert(0, str(_PROPORACLE_ROOT))
        from scripts.line_history_archive import archive_lines
        archive_lines(df, sport="MLB")
    except Exception as _arch_exc:
        print(f"  [WARN] line_history archive skipped: {_arch_exc}")
    _write_snapshot(df, target_date=str(args.date).strip())

    rows_n  = len(df)
    teams_n = df["team"].astype(str).nunique()
    print(f"\n✅ Saved → {args.output}  rows={rows_n}  teams={teams_n}")

    if rows_n > 0:
        print("\nProp type breakdown:")
        print(df["prop_type"].value_counts().to_string())

    if rows_n < args.min_rows or teams_n < args.min_teams:
        print(f"\n⛔ BOARD_TOO_SMALL (need min_rows={args.min_rows}, min_teams={args.min_teams})")
        log_pipeline_health(
            "mlb.step1_fetch_prizepicks",
            "board_too_small",
            extra={"rows": rows_n, "teams": teams_n},
            start=Path(__file__),
        )
        sys.exit(1)
    else:
        print("\n✅ BOARD_OK")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log_pipeline_health(
            "mlb.step1_fetch_prizepicks",
            "run_failed",
            extra={"error": f"{type(e).__name__}: {e}"},
            start=Path(__file__),
        )
        print(f"[step1] MLB failed (logged). {type(e).__name__}: {e}")
        sys.exit(1)
