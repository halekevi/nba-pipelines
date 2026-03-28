#!/usr/bin/env python3
"""
step1_fetch_prizepicks_mlb.py  (MLB Pipeline — Playwright intercept edition)

Lets the real PrizePicks app make its own API requests and intercepts the response.
No cookies, no headers, no 403 — the browser IS a real user.

First-time setup (run once):
    pip install playwright --break-system-packages
    playwright install chromium

Usage:
    py -3.14 step1_fetch_prizepicks_mlb.py
    py -3.14 step1_fetch_prizepicks_mlb.py --output step1_mlb_props.csv
    py -3.14 step1_fetch_prizepicks_mlb.py --timeout 60
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

# Ensure repo root is on sys.path so top-level helpers import from any cwd.
_PROPORACLE_ROOT = Path(__file__).resolve().parents[2]
if str(_PROPORACLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROPORACLE_ROOT))

from scripts.db_utils import log_pipeline_health

MLB_LEAGUE_ID = "2"
BOARD_URL     = f"https://app.prizepicks.com/board?league_id={MLB_LEAGUE_ID}"

TRACKABLE_PROPS = {
    # Hitter
    "hits",
    "total bases",
    "totalbases",
    "home runs",
    "homeruns",
    "rbi",
    "runs",
    "walks",
    "stolen bases",
    "stolenbases",
    "fantasy score",
    "fantasyscore",
    "hits+runs+rbi",
    "hitsrunsrbi",
    "total bases (combo)",
    # Pitcher
    "strikeouts",
    "pitcher strikeouts",
    "pitching outs",
    "pitchingouts",
    "hits allowed",
    "hitsallowed",
    "earned runs",
    "earnedrunsr",
    "walks allowed",
    "walksallowed",
    "innings pitched",
    "inningspitched",
}

PICKTYPE_MAP = {"standard": "Standard", "goblin": "Goblin", "demon": "Demon"}

EMPTY_COLS = [
    "projection_id", "pp_projection_id", "player_id", "pp_game_id", "start_time",
    "player", "image_url", "pos", "team", "opp_team", "pp_home_team", "pp_away_team",
    "prop_type", "line", "pick_type",
]


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
        pick_type_raw = str(attrs.get("pick_type", "standard") or "standard").lower()
        pick_type     = PICKTYPE_MAP.get(pick_type_raw, pick_type_raw.capitalize())

        # resolve player
        player_rel = rels.get("new_player") or rels.get("player") or {}
        player_id  = ""
        player_obj = None
        pd_data    = player_rel.get("data") or {}
        if isinstance(pd_data, dict):
            player_id  = str(pd_data.get("id", "")).strip()
            player_obj = idx.get(("new_player", player_id)) or idx.get(("player", player_id))

        # resolve game — try new_game first (PrizePicks current API), fall back to game
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
            "image_url":        image_url,
            "pos":              pos,
            "team":             team,
            "opp_team":         opp_team,
            "pp_home_team":     home,
            "pp_away_team":     away,
            "prop_type":        prop_type_raw,   # keep original casing for downstream
            "line":             line,
            "pick_type":        pick_type,
        })

    return rows


# ─── Playwright fetch ──────────────────────────────────────────────────────────

def fetch_via_playwright(timeout_s: int = 45, manual_window: int = 30) -> Tuple[List[dict], List[dict]]:
    """
    Launch a visible Chromium window, navigate to the MLB board, and intercept
    ANY prizepicks API response that contains projection data.

    Broad intercept: captures /projections, /boards, /offers, graphql, or any
    api.prizepicks.com endpoint returning a list of projection-like objects.

    Prints all matched response URLs for debugging.
    Gives a manual_window-second window at the start for user interaction
    (cookie banners, geo prompts, etc.) before auto-scroll triggers.
    """
    from playwright.sync_api import sync_playwright

    captured: dict = {}
    done = False
    have_projections = False
    projections_time: float = 0.0
    seen_urls: list = []

    # Broad patterns — anything on api.prizepicks.com that returns projection data
    CAPTURE_PATTERNS = [
        "api.prizepicks.com/projections",
        "api.prizepicks.com/boards",
        "api.prizepicks.com/offers",
        "api.prizepicks.com/graphql",
        "api.prizepicks.com/v1/projections",
        "api.prizepicks.com/v2/projections",
    ]
    GAME_PATTERN = "api.prizepicks.com/games"

    def _try_extract_projections(j: dict) -> Tuple[List[dict], List[dict]]:
        """Try multiple known shapes to extract (data, included) projection lists."""
        # Shape 1: standard JSONAPI  {"data": [...], "included": [...]}
        if isinstance(j.get("data"), list):
            data = j["data"]
            # filter to projection-type items if typed
            proj = [x for x in data if isinstance(x, dict) and x.get("type") == "projection"]
            if not proj:
                proj = data  # untyped — take all, parse_rows will skip non-projections
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

    def handle_response(response):
        nonlocal have_projections, projections_time
        url = response.url

        # Log ALL api.prizepicks.com responses for debugging
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

            # Capture game objects from the separate /games endpoint (PrizePicks loads
            # home/away team data here, NOT as included sideloads in /projections).
            if GAME_PATTERN in url and isinstance(j.get("data"), list):
                game_objs = [x for x in j["data"] if isinstance(x, dict)
                             and x.get("type") in ("game", "new_game", "scheduled_game")]
                if not game_objs:
                    game_objs = [x for x in j["data"] if isinstance(x, dict) and x.get("id")]
                if game_objs:
                    captured.setdefault("included", []).extend(game_objs)
                    print(f"  ✓ Captured {len(game_objs)} game objects from /games")
                return

            # Only accept projection responses with league_id=2
            if not any(pat in url for pat in CAPTURE_PATTERNS):
                return
            if "league_id=2" not in url and "league_id=2" not in response.url:
                print(f"       └─ skipping (not league_id=2)")
                return

            data, included = _try_extract_projections(j)
            if data:
                captured["data"] = data
                captured.setdefault("included", [])
                captured["included"].extend(included)
                have_projections = True
                projections_time = time.time()
                print(f"  ✓ Captured {len(data)} items from {url}")

    PROFILE_DIR = Path.home() / ".pp_browser_profile"
    use_profile = PROFILE_DIR.exists()

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
    CTX_KWARGS = dict(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        timezone_id="America/Chicago",
        geolocation={"latitude": 29.7604, "longitude": -95.3698},  # Houston, TX
        permissions=["geolocation", "notifications"],
        color_scheme="dark",
    )

    # playwright-stealth patches ~30 automation fingerprint signals DataDome checks
    try:
        from playwright_stealth import stealth_sync
        use_stealth = True
        print("  🛡️  playwright-stealth loaded")
    except ImportError:
        use_stealth = False
        print("  ⚠️  playwright-stealth not found — install with: pip install playwright-stealth")

    with sync_playwright() as p:
        if use_profile:
            print(f"🌐 Launching Chrome with saved profile...")
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=False,
                args=LAUNCH_ARGS,
                **CTX_KWARGS,
            )
            browser = None
        else:
            print("🌐 Launching Chrome (no saved profile — run setup_prizepicks_profile.py once)...")
            browser = p.chromium.launch(headless=False, args=LAUNCH_ARGS)
            context = browser.new_context(viewport=None, **CTX_KWARGS)

        page = context.new_page()

        # Apply stealth BEFORE navigation — patches webdriver, plugins, languages,
        # chrome runtime, permissions API, iframe contentWindow, and ~25 more signals
        if use_stealth:
            stealth_sync(page)

        page.on("response", handle_response)

        print(f"  Loading {BOARD_URL}")
        try:
            page.goto(BOARD_URL, timeout=30_000, wait_until="domcontentloaded")
            # Extra wait for MLB-specific projections to load after NBA default
            time.sleep(4)
        except Exception as e:
            print(f"  ⚠️  Page load timeout (continuing): {e}")

        GAMES_GRACE = 6.0  # seconds to wait after projections for /games response

        def _capture_complete() -> bool:
            """True once we have projections + either game objects or grace period expired."""
            if not have_projections:
                return False
            elapsed = time.time() - projections_time
            have_games = any(
                obj.get("type") in ("game", "new_game", "scheduled_game") or
                ("home_team" in (obj.get("attributes") or {}))
                for obj in captured.get("included", [])
            )
            return have_games or elapsed >= GAMES_GRACE

        # Phase 1: manual interaction window — let user dismiss banners/prompts
        if manual_window > 0:
            print(f"\n  ⏳ Manual window: {manual_window}s — dismiss any popups if needed...")
            deadline_manual = time.time() + manual_window
            while not _capture_complete() and time.time() < deadline_manual:
                time.sleep(1)

        if _capture_complete():
            context.close()
            if browser:
                browser.close()
            return captured.get("data", []), captured.get("included", [])

        # Phase 2: auto-scroll triggers to nudge lazy API calls
        print("  ⚙️  Auto-triggering: clicking board + scrolling...")
        triggers = [
            "window.scrollBy(0, 300)",
            "window.scrollBy(0, 600)",
            "document.body.click()",
            "window.scrollBy(0, -300)",
            "window.dispatchEvent(new Event('resize'))",
        ]
        deadline = time.time() + (timeout_s - manual_window)
        trigger_idx = 0
        while not _capture_complete() and time.time() < deadline:
            if trigger_idx < len(triggers):
                try:
                    page.evaluate(triggers[trigger_idx])
                    trigger_idx += 1
                except Exception:
                    pass
                time.sleep(2)
            else:
                time.sleep(1)

        if not have_projections and seen_urls:
            print("\n  📋 All PrizePicks API calls seen (none had projection data):")
            for u in seen_urls:
                print(u)
        elif not have_projections:
            print("\n  ⚠️  No api.prizepicks.com calls detected at all.")
            print("       PrizePicks may be blocking the browser fingerprint or CDN-caching the board.")

        context.close()
        if browser:
            browser.close()

    return captured.get("data", []), captured.get("included", [])


def load_payload_from_file(path: str) -> Tuple[List[dict], List[dict]]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        return [], []
    data     = raw.get("data") or []
    included = raw.get("included") or []
    return (data if isinstance(data, list) else []), (included if isinstance(included, list) else [])


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = ap = argparse.ArgumentParser()
    ap.add_argument("--output",        default="step1_mlb_props.csv")
    ap.add_argument("--from-file",     default="",   help="Load raw PrizePicks JSON from file instead of live fetch")
    ap.add_argument("--timeout",       type=int, default=75,  help="Total seconds to wait per attempt (default 75)")
    ap.add_argument("--manual_window", type=int, default=30,  help="Seconds before auto-scroll triggers (default 30)")
    ap.add_argument("--retries",       type=int, default=1,   help="Extra browser launch attempts on miss (default 1)")
    ap.add_argument("--retry_delay",   type=int, default=8,   help="Seconds to wait between retry attempts (default 8)")
    ap.add_argument("--min_rows",      type=int, default=30)
    ap.add_argument("--min_teams",     type=int, default=2)
    ap.add_argument("--all_props",     action="store_true",   help="Keep all prop types unfiltered")
    # Compat aliases used by run_pipeline.ps1
    ap.add_argument("--gentle",        action="store_true",   help="(compat) no-op, accepted for pipeline compat")
    ap.add_argument("--manual-seconds",type=int, default=None, help="(compat) alias for --manual_window")
    args     = ap.parse_args()
    # resolve alias
    if args.manual_seconds is not None:
        args.manual_window = args.manual_seconds
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Fetch (with retry) ────────────────────────────────────────────────────
    data: list = []
    included: list = []

    if args.from_file:
        print(f"[step1] Loading MLB payload from file: {args.from_file}")
        data, included = load_payload_from_file(args.from_file)
    else:
        max_attempts = 1 + max(0, args.retries)
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                print(f"\n🔄 Retry {attempt - 1}/{args.retries} — relaunching browser in {args.retry_delay}s...")
                time.sleep(args.retry_delay)
            print(f"[step1] Fetching PrizePicks MLB via browser intercept | league_id={MLB_LEAGUE_ID} | attempt {attempt}/{max_attempts}")
            data, included = fetch_via_playwright(
                timeout_s=args.timeout,
                manual_window=args.manual_window if attempt == 1 else 0,
            )
            if data:
                print(f"  ✅ Captured on attempt {attempt}")
                break
            print(f"  ⚠️  Attempt {attempt} missed intercept.")

    # ── Empty guard ────────────────────────────────────────────────────────────
    if not data:
        pd.DataFrame(columns=EMPTY_COLS).to_csv(out_path, index=False)
        print("❌ No projections intercepted after all attempts. Wrote empty CSV.")
        log_pipeline_health(
            "mlb.step1_fetch_prizepicks",
            "no_projections",
            extra={"league_id": MLB_LEAGUE_ID, "method": "playwright_intercept", "attempts": max_attempts},
            start=Path(__file__),
        )
        sys.exit(1)

    # ── Parse ──────────────────────────────────────────────────────────────────
    rows = parse_rows(data, included)
    df   = pd.DataFrame(rows).fillna("")
    df["line"] = pd.to_numeric(df["line"], errors="coerce")

    # Derive opp_team from game_id groupings when game obj lacks home/away fields.
    # PrizePicks /games endpoint returns lineup data, not team codes, so we infer
    # the opponent as the other team sharing the same pp_game_id.
    # Normalize pp_game_id to string for consistent joining.
    df["pp_game_id"] = df["pp_game_id"].astype(str).str.strip()
    if "opp_team" in df.columns and "pp_game_id" in df.columns and "team" in df.columns:
        # For each game_id, collect the unique teams
        teams_per_game = (
            df[df["pp_game_id"].ne("") & df["team"].astype(str).str.strip().ne("")]
            .groupby("pp_game_id")["team"]
            .apply(lambda s: sorted(s.unique()))
            .reset_index()
        )
        teams_per_game.columns = ["pp_game_id", "_teams"]
        # Only consider games with exactly 2 teams
        two_team = teams_per_game[teams_per_game["_teams"].apply(len) == 2].copy()
        two_team["_team_a"] = two_team["_teams"].apply(lambda t: t[0])
        two_team["_team_b"] = two_team["_teams"].apply(lambda t: t[1])

        df = df.merge(two_team[["pp_game_id", "_team_a", "_team_b"]], on="pp_game_id", how="left")
        needs_opp = df["opp_team"].astype(str).str.strip().eq("")
        df.loc[needs_opp & (df["team"] == df["_team_a"]), "opp_team"] = df["_team_b"]
        df.loc[needs_opp & (df["team"] == df["_team_b"]), "opp_team"] = df["_team_a"]
        df = df.drop(columns=["_team_a", "_team_b", "_teams"], errors="ignore")

    # Bouncer: remove junk rows
    required = ["projection_id", "player", "team", "prop_type"]
    for c in required:
        if c not in df.columns:
            print(f"⚠️  Missing required column: {c}")
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

    df.to_csv(out_path, index=False, encoding="utf-8-sig")

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
