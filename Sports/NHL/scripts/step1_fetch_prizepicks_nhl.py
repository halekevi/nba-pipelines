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
from pathlib import Path

import pandas as pd
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.step1_slate_date_filter import apply_game_date_filter, no_props_log_line

try:
    from tqdm import tqdm as _tqdm
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "tqdm", "--break-system-packages", "-q"])
    from tqdm import tqdm as _tqdm

NHL_LEAGUE_ID = 8
PER_PAGE = 250
# PrizePicks occasionally paginates deep on busy slates; stop only after an empty page.
MAX_PAGES = 40

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

DEFAULT_TZ = "America/New_York"

NHL_CSV_FIELDNAMES = [
    "projection_id",
    "player_id",
    "player_name",
    "team",
    "position",
    "stat_type",
    "line_score",
    "standard_line",
    "pick_type",
    "is_promo",
    "description",
    "away_team",
    "home_team",
    "game_start",
    "game_id",
    "fetched_at",
]


def _default_et_date_str() -> str:
    return datetime.now(ZoneInfo(DEFAULT_TZ)).date().isoformat()


def _to_float(x) -> float | None:
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _pick_type_lower(pick_type) -> str:
    return str(pick_type or "").strip().lower()


def enrich_standard_lines(rows: list) -> tuple[list, int]:
    """
    Populate standard_line and delta_pct for each row.

    - Standard: standard_line from API if present, else line_score.
    - Goblin/Demon: standard_line from API if present, else the line_score of the
      matching Standard prop for the same player_id + stat_type (first match wins).
    - delta_pct = line_score / standard_line (same ratio as combined_slate / grader),
      blank when standard_line is unknown or zero.

    Returns (rows, n_goblin_demon_missing_std) for logging.
    """
    std_lookup: dict[tuple[str, str], float] = {}
    for r in rows:
        if _pick_type_lower(r.get("pick_type")) != "standard":
            continue
        pid = str(r.get("player_id", "")).strip()
        st = str(r.get("stat_type", "")).strip().lower()
        if not pid or not st:
            continue
        ls = _to_float(r.get("line_score"))
        if ls is None:
            continue
        key = (pid, st)
        if key not in std_lookup:
            std_lookup[key] = ls

    missing_std = 0
    for r in rows:
        pt = _pick_type_lower(r.get("pick_type"))
        pid = str(r.get("player_id", "")).strip()
        st = str(r.get("stat_type", "")).strip().lower()
        line_val = _to_float(r.get("line_score"))
        std_val = _to_float(r.get("standard_line"))

        if pt == "standard":
            if std_val is None and line_val is not None:
                std_val = line_val
        elif pt in ("goblin", "demon"):
            if std_val is None and pid and st:
                std_val = std_lookup.get((pid, st))
            if std_val is None:
                missing_std += 1
        else:
            # Unknown odds label — treat like Standard for baseline purposes
            if std_val is None and line_val is not None:
                std_val = line_val

        if std_val is not None:
            r["standard_line"] = std_val
        else:
            r["standard_line"] = ""

        if line_val is not None and std_val is not None and std_val != 0:
            r["delta_pct"] = round(line_val / std_val, 6)
        else:
            r["delta_pct"] = ""

    return rows, missing_std


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
            std_api = attrs.get("standard_line") or attrs.get("standard_score") or attrs.get("baseline")

            rows.append({
                "projection_id": proj_id,
                "player_id": player_id,
                "player_name": player_info.get("player_name", ""),
                "team": player_info.get("team", ""),
                "position": player_info.get("position", ""),
                "stat_type": attrs.get("stat_type", ""),
                "line_score": attrs.get("line_score", ""),
                "standard_line": std_api if std_api is not None else "",
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


def _merge_projection_boards(primary: list, secondary: list) -> list:
    """
    Union NHL projections from two API game_mode pulls (prizepools vs pickem).

    Historically we only used the first successful mode; boards can differ slightly,
    so pickem-only rows were silently missing from the slate.
    Later rows overwrite earlier for the same projection_id (pickem wins over prizepools).
    """
    merged: dict[str, dict] = {}
    for r in primary or []:
        pid = str((r or {}).get("projection_id") or "").strip()
        if pid:
            merged[pid] = r
    n_add = 0
    for r in secondary or []:
        pid = str((r or {}).get("projection_id") or "").strip()
        if not pid:
            continue
        if pid not in merged:
            n_add += 1
        merged[pid] = r
    out = list(merged.values())
    if out:
        parts = []
        if primary:
            parts.append(f"prizepools={len(primary)}")
        if secondary:
            parts.append(f"pickem={len(secondary)}")
        extra = f" (pickem-only ids: {n_add})" if primary and secondary and n_add else ""
        print(f"  [merge] {' + '.join(parts)} → unique projections={len(out)}{extra}")
    return out


def fetch_via_requests(game_mode: str) -> list:
    """Try plain requests — works if the endpoint isn't bot-protected."""
    print(f"  Trying requests ({game_mode} mode)...")
    session = requests.Session()
    session.headers.update(HEADERS)

    all_data, all_included = [], []
    seen_ids = set()

    page_bar = _tqdm(range(1, MAX_PAGES + 1), desc=f"  Fetching pages ({game_mode})", unit="page", leave=True)
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


def write_csv(rows, path, *, fieldnames: list[str] | None = None):
    names = fieldnames or (list(rows[0].keys()) if rows else NHL_CSV_FIELDNAMES)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=names)
        writer.writeheader()
        if rows:
            writer.writerows(rows)
    print(f"✅ Saved {len(rows)} rows -> {path}")


def _write_empty_nhl(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_csv([], str(path), fieldnames=NHL_CSV_FIELDNAMES)


def _archive_nhl_lines(df: pd.DataFrame) -> None:
    try:
        root = Path(__file__).resolve().parents[3]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from scripts.line_history_archive import archive_lines
        archive_lines(df, sport="NHL")
    except Exception as exc:
        print(f"  [WARN] line_history archive skipped: {exc}")


def _write_nhl_output(rows: list, out_path: Path, append: bool) -> None:
    """Write NHL props; with --append, merge with existing CSV and semantic-dedupe (keep='last')."""
    if not rows:
        return
    new_df = pd.DataFrame(rows)
    if append and out_path.is_file():
        try:
            existing = pd.read_csv(out_path, encoding="utf-8-sig")
            n_existing = len(existing)
            all_cols = list(dict.fromkeys(list(existing.columns) + list(new_df.columns)))
            for c in all_cols:
                if c not in existing.columns:
                    existing[c] = ""
                if c not in new_df.columns:
                    new_df[c] = ""
            existing = existing[all_cols].copy()
            new_df = new_df[all_cols].copy()
            n_new = len(new_df)
            combined = pd.concat([existing, new_df], ignore_index=True)
            for col in ("player_name", "stat_type", "pick_type", "game_id"):
                if col in combined.columns:
                    combined[col] = combined[col].astype(str).str.strip()
            if "line_score" in combined.columns:
                combined["line_score"] = pd.to_numeric(combined["line_score"], errors="coerce")
            dedup_cols = [
                c
                for c in ("player_name", "stat_type", "line_score", "game_id", "pick_type")
                if c in combined.columns
            ]
            if dedup_cols:
                combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
            combined.to_csv(out_path, index=False, encoding="utf-8-sig")
            print(
                f"[step1 NHL append] {n_existing} existing + {n_new} new → "
                f"{len(combined)} after dedup (subset={dedup_cols})"
            )
            print(f"✅ Saved {len(combined)} rows -> {out_path}")
            _archive_nhl_lines(combined)
        except Exception as e:
            print(f"  [WARN] --append merge failed ({e}); writing this fetch only")
            write_csv(rows, str(out_path))
            _archive_nhl_lines(new_df)
    else:
        write_csv(rows, str(out_path))
        _archive_nhl_lines(new_df)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/step1_nhl_props.csv")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append this fetch after existing CSV rows, then dedupe (keep='last').",
    )
    parser.add_argument(
        "--date",
        default=_default_et_date_str(),
        help=f"Target slate date in {DEFAULT_TZ} (YYYY-MM-DD).",
    )
    parser.add_argument("--tz", default=DEFAULT_TZ)
    parser.add_argument(
        "--allow-nearest-future",
        action="store_true",
        help="Skip same-day date filter (keep full API board; explicit opt-in only).",
    )
    args = parser.parse_args()
    out_path = Path(args.output)

    print(f"📡 Fetching PrizePicks NHL | league_id={NHL_LEAGUE_ID}")

    # Pull both game modes and merge — boards can differ; using only the first hit
    # used to drop pickem-only projections (common symptom: "missing players/props").
    rows_pp = fetch_via_requests("prizepools")
    rows_pk = fetch_via_requests("pickem")
    rows = _merge_projection_boards(rows_pp or [], rows_pk or [])

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
        if args.append and out_path.is_file():
            print("   (--append: left existing output file unchanged)")
            sys.exit(1)
        _write_empty_nhl(out_path)
        sys.exit(0)

    fetch_date = str(args.date).strip()[:10]
    n_before_date = len(rows)
    df_nhl = pd.DataFrame(rows)
    filtered, _ = apply_game_date_filter(
        df_nhl,
        target_date=fetch_date,
        tz_name=str(args.tz).strip() or DEFAULT_TZ,
        allow_nearest_future=bool(args.allow_nearest_future),
        start_time_col="game_start",
    )
    print(
        f"[NHL step1] Date filter {fetch_date}: fetched={n_before_date} survived={len(filtered)}"
    )
    if args.allow_nearest_future:
        print("[NHL step1] allow-nearest-future: skipping date filter")
    if len(filtered) == 0:
        print(no_props_log_line("NHL", fetch_date))
        _write_empty_nhl(out_path)
        sys.exit(0)
    rows = filtered.to_dict("records")

    rows, n_missing_std = enrich_standard_lines(rows)
    if n_missing_std:
        print(
            f"\n⚠️  Goblin/Demon rows with no standard_line (no API field + no matching Standard prop): "
            f"{n_missing_std} — delta_pct blank; payout curve uses factor 1.0 for those legs."
        )

    stat_counts = {}
    for r in rows:
        stat_counts[r["stat_type"]] = stat_counts.get(r["stat_type"], 0) + 1
    print(f"\nFound {len(rows)} props across {len(stat_counts)} stat types:")
    for st, cnt in sorted(stat_counts.items(), key=lambda x: -x[1]):
        print(f"  {st}: {cnt}")

    _write_nhl_output(rows, out_path, args.append)


if __name__ == "__main__":
    main()
