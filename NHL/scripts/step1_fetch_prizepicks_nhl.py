"""
Step 1 — Fetch PrizePicks NHL Board
Tries prizepools mode first (no browser needed, same as CBB).
Falls back to Playwright interception if blocked.

First-time setup (only needed if prizepools fails):
    pip install playwright --break-system-packages
    playwright install chromium

Usage:
    py step1_fetch_prizepicks_nhl.py --output outputs/step1_nhl_props.csv
"""

import argparse
import csv
import sys
import time
import random
import requests
from datetime import datetime, timezone
try:
    from tqdm import tqdm as _tqdm
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "tqdm", "--break-system-packages", "-q"])
    from tqdm import tqdm as _tqdm

NHL_LEAGUE_ID = 8
PER_PAGE = 250

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://app.prizepicks.com",
    "Referer": "https://app.prizepicks.com/board",
}

NHL_STAT_KEYWORDS = {
    "goals", "assists", "shots", "saves", "hits", "blocks",
    "time on ice", "points", "goals allowed", "fantasy"
}


def is_nhl_data(rows: list) -> bool:
    """Sanity check — make sure we got hockey props not NBA."""
    if not rows:
        return False
    stats = {r.get("stat_type", "").lower() for r in rows[:20]}
    nba_tells = {"rebounds", "pts+rebs", "3-pt made", "turnovers"}
    return not any(t in " ".join(stats) for t in nba_tells)


def parse_rows(data: list, included: list) -> list:
    players_map = {}
    games_map = {}
    for obj in included:
        obj_id = obj.get("id")
        obj_type = obj.get("type", "")
        attrs = obj.get("attributes", {})
        if obj_type == "new_player":
            players_map[obj_id] = {
                "player_name": attrs.get("display_name", attrs.get("name", "")),
                "team": attrs.get("team", ""),
                "position": attrs.get("position", ""),
            }
        elif obj_type in ("game", "new_game"):
            games_map[obj_id] = {
                "away_team": attrs.get("away_team_name", attrs.get("away_team", "")),
                "home_team": attrs.get("home_team_name", attrs.get("home_team", "")),
                "game_start": attrs.get("start_time", ""),
            }

    rows = []
    seen_ids = set()
    with _tqdm(data, desc="  Parsing props", unit="prop", leave=False) as pbar:
        for proj in pbar:
            proj_id = proj.get("id")
            if proj_id in seen_ids:
                continue
            seen_ids.add(proj_id)

            attrs = proj.get("attributes", {})
            rels = proj.get("relationships", {})
            player_id = rels.get("new_player", {}).get("data", {}).get("id", "")
            game_id = (rels.get("game") or rels.get("new_game") or {}).get("data", {}).get("id", "")
            player_info = players_map.get(player_id, {})
            game_info = games_map.get(game_id, {})

            rows.append({
                "projection_id": proj_id,
                "player_id": player_id,
                "player_name": player_info.get("player_name", ""),
                "team": player_info.get("team", ""),
                "position": player_info.get("position", ""),
                "stat_type": attrs.get("stat_type", ""),
                "line_score": attrs.get("line_score", ""),
                "pick_type": attrs.get("odds_type") or attrs.get("pick_type") or "",
                "is_promo": attrs.get("is_promo", False),
                "description": attrs.get("description", ""),
                "away_team": game_info.get("away_team", ""),
                "home_team": game_info.get("home_team", ""),
                "game_start": game_info.get("game_start", ""),
                "game_id": game_id,
                "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
    return rows


def fetch_via_requests(game_mode: str) -> list:
    """Try plain requests — works if the endpoint isn't bot-protected."""
    print(f"  Trying requests ({game_mode} mode)...")
    session = requests.Session()
    session.headers.update(HEADERS)

    all_data, all_included = [], []
    seen_ids = set()

    page_bar = _tqdm(range(1, 20), desc=f"  Fetching pages ({game_mode})", unit="page", leave=True)
    for page in page_bar:
        params = {
            "league_id": NHL_LEAGUE_ID,
            "game_mode": game_mode,
            "per_page": PER_PAGE,
            "page": page,
        }
        try:
            r = session.get("https://api.prizepicks.com/projections", params=params, timeout=20)
            if r.status_code == 403:
                print(f"  ✗ 403 on {game_mode}")
                return []
            if r.status_code != 200:
                print(f"  ✗ HTTP {r.status_code}")
                return []
            j = r.json()
            data = j.get("data") or []
            if not data:
                break
            new = [d for d in data if d.get("id") not in seen_ids]
            if not new:
                break
            for d in new:
                seen_ids.add(d.get("id"))
            all_data.extend(new)
            all_included.extend(j.get("included") or [])
            page_bar.set_postfix(total=len(all_data))
            print(f"  ✓ Page {page}: +{len(new)} props (total {len(all_data)})")
            time.sleep(random.uniform(0.5, 1.2))
        except Exception as e:
            print(f"  ✗ Error: {e}")
            return []

    rows = parse_rows(all_data, all_included)
    if rows and not is_nhl_data(rows):
        print(f"  ✗ Got non-NHL data on {game_mode} — skipping")
        return []
    return rows


def fetch_via_playwright() -> list:
    """Playwright interception — filters strictly by league_id=8 in the API response URL."""
    from playwright.sync_api import sync_playwright

    print("  Trying Playwright (browser interception)...")
    captured_data, captured_included = [], []
    intercept_done = False

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--start-maximized"]
        )
        context = browser.new_context(
            viewport=None,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
            geolocation={"latitude": 40.7128, "longitude": -74.0060},
            permissions=["geolocation", "notifications"],
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en'] });
            window.chrome = { runtime: {} };
        """)
        page = context.new_page()

        def handle_response(response):
            nonlocal intercept_done
            if intercept_done:
                return
            url = response.url
            # Must contain league_id=8 to avoid capturing NBA (league_id=7)
            if "api.prizepicks.com/projections" in url and "league_id=8" in url:
                try:
                    j = response.json()
                    data = j.get("data") or []
                    if data:
                        captured_data.extend(data)
                        captured_included.extend(j.get("included") or [])
                        print(f"  ✓ Intercepted {len(data)} projections (league_id=8)")
                        intercept_done = True
                except Exception:
                    pass

        page.on("response", handle_response)

        # Load board then click NHL tab
        print("  Loading board and clicking NHL tab...")
        try:
            page.goto("https://app.prizepicks.com/board", wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print(f"  ⚠️ {e}")

        time.sleep(4)

        # Click NHL tab
        clicked = False
        try:
            for tab in page.query_selector_all("li, button, a"):
                text = (tab.inner_text() or "").strip().lower()
                if "nhl" in text or "hockey" in text:
                    tab.click()
                    print(f"  → Clicked: '{tab.inner_text().strip()}'")
                    clicked = True
                    break
        except Exception:
            pass

        if not clicked:
            print("  ⚠️ NHL tab not found — trying URL navigation")
            try:
                page.goto(f"https://app.prizepicks.com/board?league_id={NHL_LEAGUE_ID}",
                          wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass

        deadline = time.time() + 30
        while time.time() < deadline:
            if intercept_done:
                break
            time.sleep(0.5)

        if not intercept_done:
            try:
                page.evaluate("window.scrollTo(0, 200)")
                time.sleep(6)
            except Exception:
                pass

        time.sleep(1.5)
        browser.close()

    if not captured_data:
        return []
    rows = parse_rows(captured_data, captured_included)
    if rows and not is_nhl_data(rows):
        print("  ✗ Playwright also captured NBA data — NHL may not be on the board today")
        return []
    return rows


def write_csv(rows, path):
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"✅ Saved {len(rows)} rows -> {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/step1_nhl_props.csv")
    args = parser.parse_args()

    print(f"📡 Fetching PrizePicks NHL | league_id={NHL_LEAGUE_ID}")

    # Try prizepools first (no browser, fast)
    rows = fetch_via_requests("prizepools")

    # Then pickem via requests
    if not rows:
        rows = fetch_via_requests("pickem")

    # Finally Playwright
    if not rows:
        try:
            rows = fetch_via_playwright()
        except ImportError:
            print("\n❌ Playwright not installed:")
            print("   pip install playwright --break-system-packages")
            print("   playwright install chromium")
            sys.exit(1)

    if not rows:
        print("⚠️  No NHL props found. NHL may not be active on PrizePicks today.")
        sys.exit(0)

    stat_counts = {}
    for r in rows:
        stat_counts[r["stat_type"]] = stat_counts.get(r["stat_type"], 0) + 1
    print(f"\nFound {len(rows)} props across {len(stat_counts)} stat types:")
    for st, cnt in sorted(stat_counts.items(), key=lambda x: -x[1]):
        print(f"  {st}: {cnt}")

    write_csv(rows, args.output)


if __name__ == "__main__":
    main()
