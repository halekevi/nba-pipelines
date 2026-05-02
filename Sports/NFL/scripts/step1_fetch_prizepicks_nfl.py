#!/usr/bin/env python3
"""
step1_fetch_prizepicks_nfl.py  (NFL Pipeline — PrizePicks board fetch)

Launches a headless Chromium using your saved ~/.pp_browser_profile so
PrizePicks sees a real logged-in session — no manual interaction needed.

First-time setup (run once):
    pip install playwright playwright-stealth --break-system-packages
    playwright install chromium
    py -3.14 setup_prizepicks_profile.py   ← copies your Chrome profile/cookies

After that, step1 runs fully unattended in the scheduled pipeline.

Usage:
    py -3.14 NFL/scripts/step1_fetch_prizepicks_nfl.py
    py -3.14 NFL/scripts/step1_fetch_prizepicks_nfl.py --output NFL/data/outputs/step1_pp_props_today.csv
    py -3.14 NFL/scripts/step1_fetch_prizepicks_nfl.py --timeout 90
    py -3.14 NFL/scripts/step1_fetch_prizepicks_nfl.py --from-file payload.json
"""

from __future__ import annotations

import argparse
import json
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

# PrizePicks internal IDs (see scripts/capture_entries.py): NBA=7, MLB=2, NFL=9, NHL=8
NFL_LEAGUE_ID = "9"
BOARD_URL     = f"https://app.prizepicks.com/board?league_id={NFL_LEAGUE_ID}"
DEFAULT_TZ = "America/New_York"

TRACKABLE_PROPS = {
    # QB
    "passing yards",
    "passingyards",
    "passing tds",
    "passingtds",
    "passing touchdowns",
    "pass attempts",
    "passattempts",
    "completions",
    "interceptions",
    # RB
    "rushing yards",
    "rushingyards",
    "rushing attempts",
    "rushingattempts",
    "carries",
    "rushing tds",
    "rushingtds",
    # WR / TE
    "receiving yards",
    "receivingyards",
    "receptions",
    "targets",
    "receiving tds",
    "receivingtds",
    # Scoring
    "anytime td",
    "anytimetd",
    "anytime touchdown",
    "1st touchdown",
    "first touchdown",
    # IDP / defense props (when offered)
    "sacks",
    "tackles",
    "solo tackles",
    "assisted tackles",
    "defensive interceptions",
    "fantasy score",
    "fantasyscore",
}

PICKTYPE_MAP = {"standard": "Standard", "goblin": "Goblin", "demon": "Demon"}

EMPTY_COLS = [
    "projection_id", "pp_projection_id", "player_id", "pp_game_id", "start_time",
    "player", "player_name", "image_url", "pos", "team", "opp_team", "pp_home_team", "pp_away_team",
    "prop_type", "line", "line_score", "standard_line", "pick_type", "sport",
    "game_id", "game_time", "opponent",
]

PROFILE_DIR = Path.home() / ".pp_browser_profile"

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
            "sport":            "NFL",
        })

    return rows


def load_payload_from_file(path: str) -> Tuple[List[dict], List[dict]]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        return [], []
    data     = raw.get("data") or []
    included = raw.get("included") or []
    return (data if isinstance(data, list) else []), (included if isinstance(included, list) else [])


# ─── Playwright fetch — fully headless, no manual window ──────────────────────

def fetch_via_playwright(timeout_s: int = 90) -> Tuple[List[dict], List[dict]]:
    """
    Launch headless Chromium using the saved ~/.pp_browser_profile so
    PrizePicks sees a real authenticated session. No popups, no manual steps.

    Strategy:
      1. Navigate to the NFL board URL
      2. Intercept any api.prizepicks.com response with projection data
      3. After projections land, wait up to GAMES_GRACE seconds for /games objects
      4. Trigger scroll/click events to nudge lazy API calls if needed
      5. Return (data, included) — empty lists if nothing intercepted
    """
    from playwright.sync_api import sync_playwright

    captured:          dict  = {}
    have_projections:  bool  = False
    projections_time:  float = 0.0
    seen_urls:         list  = []

    # playwright-stealth patches ~30 automation fingerprint signals DataDome checks
    try:
        from playwright_stealth import stealth_sync
        use_stealth = True
        print("  🛡️  playwright-stealth loaded")
    except ImportError:
        use_stealth = False
        print("  ⚠️  playwright-stealth not installed — run: pip install playwright-stealth --break-system-packages")

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

            # Only accept projection responses with league_id=9 (NFL)
            if not any(pat in url for pat in CAPTURE_PATTERNS):
                return
            if f"league_id={NFL_LEAGUE_ID}" not in url:
                print(f"       └─ skipping (not league_id={NFL_LEAGUE_ID})")
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

    GAMES_GRACE = 6.0  # seconds to wait after projections for /games response

    use_profile = PROFILE_DIR.exists()
    if not use_profile:
        print(f"  ⚠️  No saved profile found at {PROFILE_DIR}")
        print(f"       Run: py -3.14 setup_prizepicks_profile.py")
        print(f"       Falling back to fresh browser (may hit DataDome challenge)...")

    with sync_playwright() as p:
        if use_profile:
            print(f"🌐 Launching Chromium with saved profile: {PROFILE_DIR}")
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=False,         # visible browser — bypasses DataDome fingerprinting
                args=LAUNCH_ARGS,
                **CTX_KWARGS,
            )
            browser = None
        else:
            print("🌐 Launching Chromium (no saved profile)...")
            browser  = p.chromium.launch(headless=False, args=LAUNCH_ARGS)
            context  = browser.new_context(viewport={"width": 1920, "height": 1080}, **CTX_KWARGS)

        page = context.new_page()

        if use_stealth:
            stealth_sync(page)

        page.on("response", handle_response)

        print(f"  Loading {BOARD_URL}")
        try:
            page.goto(BOARD_URL, timeout=30_000, wait_until="domcontentloaded")
            # Give the NFL board a moment to fire its API calls after page load
            time.sleep(5)
        except Exception as e:
            print(f"  ⚠️  Page load warning (continuing): {e}")

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
                time.sleep(2)
            else:
                time.sleep(1)

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

        context.close()
        if browser:
            browser.close()

    return captured.get("data", []), captured.get("included", [])


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output",         default="data/outputs/step1_pp_props_today.csv")
    ap.add_argument("--from-file",      default="",   help="Load raw PrizePicks JSON from file instead of live fetch")
    ap.add_argument("--timeout",        type=int, default=90,  help="Total seconds to wait for intercept (default 90)")
    ap.add_argument("--retries",        type=int, default=2,   help="Extra browser launch attempts on miss (default 2)")
    ap.add_argument("--retry_delay",    type=int, default=10,  help="Seconds to wait between retry attempts (default 10)")
    ap.add_argument("--min_rows",       type=int, default=30)
    ap.add_argument("--min_teams",      type=int, default=2)
    ap.add_argument("--all_props",      action="store_true",   help="Keep all prop types unfiltered")
    ap.add_argument("--date", default=_default_et_date_str(), help=f"Target game date in {DEFAULT_TZ} (YYYY-MM-DD).")
    ap.add_argument("--tz", default=DEFAULT_TZ, help="Timezone used to derive game_date from start_time.")
    ap.add_argument("--allow-nearest-future", action="store_true", help="If no rows match --date, keep nearest future game_date.")
    # Compat aliases — accepted silently so existing pipeline calls don't break
    ap.add_argument("--gentle",         action="store_true",   help="(compat) no-op")
    ap.add_argument("--manual-seconds", type=int, default=None, help="(compat) no-op — manual window removed")
    ap.add_argument("--manual_window",  type=int, default=None, help="(compat) no-op — manual window removed")
    ap.add_argument(
        "--append",
        action="store_true",
        help="Append this fetch after existing CSV rows, then dedupe (keep='last').",
    )
    args     = ap.parse_args()
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Fetch ────────────────────────────────────────────────────────────────
    data: list     = []
    included: list = []
    max_attempts   = 1

    if args.from_file:
        print(f"[step1] Loading NFL payload from file: {args.from_file}")
        data, included = load_payload_from_file(args.from_file)
    else:
        max_attempts = 1 + max(0, args.retries)
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                print(f"\n🔄 Retry {attempt - 1}/{args.retries} — relaunching in {args.retry_delay}s...")
                time.sleep(args.retry_delay)
            print(f"[step1] NFL fetch via headless browser | league_id={NFL_LEAGUE_ID} | attempt {attempt}/{max_attempts}")
            data, included = fetch_via_playwright(timeout_s=args.timeout)
            if data:
                print(f"  ✅ Captured on attempt {attempt}")
                break
            print(f"  ⚠️  Attempt {attempt} missed intercept.")

    # ── Empty guard ──────────────────────────────────────────────────────────
    if not data:
        if args.append and out_path.is_file():
            print("❌ No projections intercepted after all attempts.")
            print("   (--append: left existing output file unchanged)")
        else:
            pd.DataFrame(columns=EMPTY_COLS).to_csv(out_path, index=False)
            print("❌ No projections intercepted after all attempts. Wrote empty CSV.")
        log_pipeline_health(
            "nfl.step1_fetch_prizepicks",
            "no_projections",
            extra={"league_id": NFL_LEAGUE_ID, "method": "playwright_headless", "attempts": max_attempts},
            start=Path(__file__),
        )
        sys.exit(1)

    # ── Parse ────────────────────────────────────────────────────────────────
    rows = parse_rows(data, included)
    df   = pd.DataFrame(rows).fillna("")
    for c in ("pp_game_id", "start_time", "opp_team"):
        if c not in df.columns:
            df[c] = ""
    df["game_id"] = df["pp_game_id"].astype(str)
    df["game_time"] = df["start_time"].astype(str)
    df["opponent"] = df["opp_team"].astype(str)
    df["line"] = pd.to_numeric(df["line"], errors="coerce")
    df["standard_line"] = pd.to_numeric(df["standard_line"], errors="coerce")
    _mstd = df["pick_type"].astype(str).str.lower().eq("standard")
    df.loc[_mstd, "standard_line"] = df.loc[_mstd, "standard_line"].fillna(df.loc[_mstd, "line"])

    # Derive opp_team from game_id groupings when game obj lacks home/away fields
    df["pp_game_id"] = df["pp_game_id"].astype(str).str.strip()
    if "opp_team" in df.columns and "pp_game_id" in df.columns and "team" in df.columns:
        teams_per_game = (
            df[df["pp_game_id"].ne("") & df["team"].astype(str).str.strip().ne("")]
            .groupby("pp_game_id")["team"]
            .apply(lambda s: sorted(s.unique()))
            .reset_index()
        )
        teams_per_game.columns = ["pp_game_id", "_teams"]
        two_team = teams_per_game[teams_per_game["_teams"].apply(len) == 2].copy()
        two_team["_team_a"] = two_team["_teams"].apply(lambda t: t[0])
        two_team["_team_b"] = two_team["_teams"].apply(lambda t: t[1])

        df = df.merge(two_team[["pp_game_id", "_team_a", "_team_b"]], on="pp_game_id", how="left")
        # fillna("") before eq("") so NaN values (str-cast to "nan") are treated as missing
        needs_opp = df["opp_team"].fillna("").astype(str).str.strip().eq("")
        df.loc[needs_opp & (df["team"] == df["_team_a"]), "opp_team"] = df["_team_b"]
        df.loc[needs_opp & (df["team"] == df["_team_b"]), "opp_team"] = df["_team_a"]
        df = df.drop(columns=["_team_a", "_team_b", "_teams"], errors="ignore")

    # Bouncer: remove junk rows
    required = ["projection_id", "player", "team", "prop_type"]
    for c in required:
        if c not in df.columns:
            print(f"⚠️  Missing required column: {c}")
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
            print("   (--append: left existing output file unchanged)")
            log_pipeline_health(
                "nfl.step1_fetch_prizepicks",
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
                f"[step1 NFL append] {n_existing} existing + {rows_n_new} new → "
                f"{len(df)} after dedup (subset={dedup_cols})"
            )
        except Exception as e:
            print(f"  [WARN] --append merge failed ({e}); writing new fetch only")

    fetched_rows = len(df)
    filtered_df, fallback_date = _apply_game_date_filter(
        df,
        target_date=str(args.date).strip(),
        tz_name=str(args.tz).strip() or DEFAULT_TZ,
        allow_nearest_future=bool(args.allow_nearest_future),
    )
    game_dates = sorted({d for d in filtered_df.get("game_date", pd.Series([], dtype=object)).astype(str).tolist() if d and d != "nan"})
    print(
        f"[INFO] NFL step1 fetched={fetched_rows} rows; date_filter={args.date} ({args.tz}); "
        f"survived={len(filtered_df)}"
    )
    if game_dates:
        print(f"[INFO] NFL step1 filtered_game_dates={game_dates}")
    if fallback_date:
        print(f"[WARNING] NFL step1 no rows for requested date; using nearest future game_date={fallback_date}")
    df = filtered_df

    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    if len(df) == 0:
        print(f"\n[INFO] Saved empty date-filtered NFL step1 CSV -> {out_path}")
        sys.exit(0)

    rows_n  = len(df)
    teams_n = df["team"].astype(str).nunique()
    print(f"\n✅ Saved → {args.output}  rows={rows_n}  teams={teams_n}")

    if rows_n > 0:
        print("\nProp type breakdown:")
        print(df["prop_type"].value_counts().to_string())

    if rows_n < args.min_rows or teams_n < args.min_teams:
        print(f"\n⛔ BOARD_TOO_SMALL (need min_rows={args.min_rows}, min_teams={args.min_teams})")
        log_pipeline_health(
            "nfl.step1_fetch_prizepicks",
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
            "nfl.step1_fetch_prizepicks",
            "run_failed",
            extra={"error": f"{type(e).__name__}: {e}"},
            start=Path(__file__),
        )
        print(f"[step1] NFL failed (logged). {type(e).__name__}: {e}")
        sys.exit(1)
