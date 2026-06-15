"""
Step 1 — Fetch PrizePicks Soccer Board
HTTP first (curl_cffi chrome131 via shared API module), urllib fallback.
PrizePicks soccer boards — club soccer plus FIFA World Cup (separate PP league_ids).
Club leagues (EPL/MLS/etc.) appear inside league_id=82; World Cup has its own boards.

Usage:
    py step1_fetch_prizepicks_soccer.py --output s1_soccer_props.csv
    py step1_fetch_prizepicks_soccer.py --include_halves --output s1_soccer_props.csv
    py step1_fetch_prizepicks_soccer.py --no-world-cup   # skip WC boards when inactive
    py step1_fetch_prizepicks_soccer.py --league_id 82 --output s1_soccer_props.csv
"""

import argparse
import sys
import time
import json
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

_PROPORACLE_ROOT = Path(__file__).resolve().parents[3]
if str(_PROPORACLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROPORACLE_ROOT))

from utils.prizepicks_http import fetch_pp_projections, make_pp_session, ensure_chrome131
from utils.step1_slate_date_filter import (
    apply_game_date_filter,
    no_props_log_line,
    should_preserve_append_output,
)

# PrizePicks internal soccer league IDs (from GET /leagues)
SOCCER_BOARDS = {
    "82":  "SOCCER",       # club soccer (full game)
    "242": "SOCCER1H",     # club first half
    "243": "SOCCER2H",     # club second half
    "262": "SOCCERSZN",    # club season props
}
WORLD_CUP_BOARDS = {
    "241": "WORLDCUP",     # World Cup full game
    "458": "WORLDCUP1H",   # World Cup first half
    "459": "WORLDCUP2H",   # World Cup second half
    "457": "WORLDCUPTRNY", # World Cup tournament props
}

PICKTYPE_MAP = {
    "standard": "Standard",
    "goblin":   "Goblin",
    "demon":    "Demon",
}

HEADERS = {
    "User-Agent":  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept":      "application/json",
    "Referer":     "https://app.prizepicks.com/",
    "Origin":      "https://app.prizepicks.com",
}

DEFAULT_TZ = "America/New_York"


def _default_et_date_str() -> str:
    return datetime.now(ZoneInfo(DEFAULT_TZ)).date().isoformat()


def _fetch_board_urllib(league_id: str, league_name: str, per_page: int = 250) -> tuple[list, list]:
    """Legacy urllib fetch (both in_game flags) — fallback when curl_cffi fails."""
    all_data = []
    all_included = []

    for in_game in ("false", "true"):
        url = (
            f"https://api.prizepicks.com/projections"
            f"?league_id={league_id}"
            f"&per_page={per_page}"
            f"&single_stat=true"
            f"&in_game={in_game}"
            f"&game_mode=pickem"
        )
        for attempt in range(1, 4):
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    j = json.loads(resp.read())
                data = j.get("data") or []
                incl = j.get("included") or []
                all_data.extend(data)
                all_included.extend(incl)
                print(f"    urllib in_game={in_game}: {len(data)} props")
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait = 60 * attempt
                    print(f"    429 rate limit — waiting {wait}s (attempt {attempt})")
                    time.sleep(wait)
                else:
                    print(f"    HTTP {e.code} for {league_name} in_game={in_game}: {e}")
                    break
            except Exception as e:
                print(f"    Error fetching {league_name} in_game={in_game}: {e}")
                break

    return all_data, all_included


def fetch_board(league_id: str, league_name: str, per_page: int = 250) -> tuple[list, list]:
    """Fetch props for a board — HTTP (curl_cffi) first, urllib fallback."""
    all_data: list = []
    all_included: list = []
    seen: set[str] = set()

    def _extend(data: list, included: list) -> int:
        added = 0
        for obj in data or []:
            oid = str(obj.get("id", "")).strip()
            if oid and oid not in seen:
                seen.add(oid)
                all_data.append(obj)
                added += 1
        all_included.extend(included or [])
        return added

    http_ok = False
    try:
        ensure_chrome131()
        data, included = fetch_pp_projections(
            str(league_id),
            per_page=per_page,
            max_pages=10,
        )
        if data:
            n = _extend(data, included)
            http_ok = True
            print(f"    HTTP (curl_cffi) in_game=false: {n} props")
    except Exception as e:
        print(f"    HTTP fetch failed ({type(e).__name__}: {e})")

    if not http_ok:
        print(f"    Falling back to urllib for {league_name}...")
        return _fetch_board_urllib(league_id, league_name, per_page=per_page)

    # Live in-game board supplement (not covered by fetch_pp_projections defaults)
    try:
        session = make_pp_session(HEADERS)
        url = (
            f"https://api.prizepicks.com/projections"
            f"?league_id={league_id}"
            f"&per_page={per_page}"
            f"&single_stat=true"
            f"&in_game=true"
            f"&game_mode=pickem"
        )
        r = session.get(url, timeout=30)
        if r.status_code == 200:
            j = r.json()
            n = _extend(j.get("data") or [], j.get("included") or [])
            if n:
                print(f"    HTTP in_game=true supplement: +{n} props")
        elif r.status_code == 403:
            print("    HTTP in_game=true: 403 (skipped)")
        else:
            print(f"    HTTP in_game=true: status {r.status_code}")
    except Exception as e:
        print(f"    in_game=true supplement skipped: {e}")

    return all_data, all_included


def build_rows(data: list, included: list, league_name: str) -> list:
    players_map = {}
    games_map   = {}
    for obj in included:
        obj_id   = obj.get("id")
        obj_type = obj.get("type", "")
        attrs    = obj.get("attributes", {})
        if obj_type in ("new_player", "player"):
            players_map[obj_id] = attrs
        elif obj_type in ("game", "new_game"):
            games_map[obj_id] = attrs

    rows     = []
    seen_ids = set()
    for proj in data:
        proj_id = str(proj.get("id", ""))
        if not proj_id or proj_id in seen_ids:
            continue
        seen_ids.add(proj_id)

        attrs = proj.get("attributes", {})
        rels  = proj.get("relationships", {})

        player_id = (rels.get("new_player") or rels.get("player") or {}).get("data", {}).get("id", "")
        game_id   = (rels.get("new_game")   or rels.get("game")   or {}).get("data", {}).get("id", "")
        p = players_map.get(str(player_id), {})
        g = games_map.get(str(game_id), {})

        player_name = str(p.get("display_name", p.get("name", ""))).strip()
        team        = str(p.get("team", "")).strip().upper()
        pos         = str(p.get("position", "")).strip()
        image_url   = str(p.get("image_url") or p.get("image_url_small") or "").strip()

        home = str(
            g.get("home_team")
            or g.get("home_team_name")
            or g.get("home")
            or ""
        ).strip().upper()
        away = str(
            g.get("away_team")
            or g.get("away_team_name")
            or g.get("away")
            or ""
        ).strip().upper()
        start_time = str(g.get("start_time", attrs.get("start_time", ""))).strip()

        opp_team = ""
        if team and home and away:
            opp_team = away if team == home else (home if team == away else "")

        # Derive sub-league from game description or player league attr
        sub_league = str(attrs.get("league", p.get("league", league_name))).strip()
        if not sub_league:
            sub_league = league_name
        # PrizePicks API returns "WORLD CUP" text; keep board tag (WORLDCUP, WORLDCUP2H, …) for pipeline keys
        if str(league_name).upper().startswith("WORLDCUP"):
            sub_league = league_name

        odds_type = str(attrs.get("odds_type", "")).strip().lower()
        pick_type = PICKTYPE_MAP.get(odds_type, "Standard")
        prop_type = str(attrs.get("stat_type", attrs.get("name", ""))).strip()
        line      = attrs.get("line_score", attrs.get("line", ""))
        std_api = attrs.get("standard_line") or attrs.get("standard_score") or attrs.get("baseline")
        if pick_type == "Standard":
            standard_line = std_api if std_api is not None and str(std_api).strip() != "" else line
        else:
            standard_line = std_api if std_api is not None else ""

        rows.append({
            "projection_id":    proj_id,
            "pp_projection_id": proj_id,
            "player_id":        str(player_id),
            "pp_game_id":       str(game_id),
            "league":           sub_league,
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
            "standard_line":    standard_line,
            "pick_type":        pick_type,
        })

    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output",          default="s1_soccer_props.csv")
    ap.add_argument("--include_halves",  action="store_true",
                    help="Also fetch SOCCER1H and SOCCER2H boards")
    ap.add_argument("--include_season",  action="store_true",
                    help="Also fetch SOCCERSZN board")
    ap.add_argument(
        "--no-world-cup",
        action="store_true",
        help="Skip World Cup boards (241/458/459/457). Default: include when PP has WC slate.",
    )
    ap.add_argument("--league_id", default=None, metavar="ID",
                    help="Primary board PrizePicks league_id (default 82). "
                         "Half/season extras still come from --include_halves / --include_season.")
    ap.add_argument(
        "--append",
        action="store_true",
        help="Append this fetch after existing CSV rows, then dedupe (keep='last').",
    )
    ap.add_argument(
        "--date",
        default=_default_et_date_str(),
        help=f"Target game date in {DEFAULT_TZ} (YYYY-MM-DD). Defaults to today {DEFAULT_TZ}.",
    )
    ap.add_argument("--tz", default=DEFAULT_TZ, help="Timezone used to derive game_date from start_time.")
    ap.add_argument(
        "--allow-nearest-future",
        action="store_true",
        help="Skip same-day date filter (keep full API board; explicit opt-in only).",
    )
    args = ap.parse_args()
    out_path = Path(args.output)

    primary_id = str(args.league_id).strip() if args.league_id is not None else "82"
    if not primary_id.isdigit():
        print(f"❌ --league_id must be numeric, got {args.league_id!r}")
        sys.exit(2)
    boards_to_fetch = {primary_id: SOCCER_BOARDS.get(primary_id, "SOCCER")}
    if not args.no_world_cup:
        boards_to_fetch.update(WORLD_CUP_BOARDS)
    if args.include_halves:
        boards_to_fetch["242"] = "SOCCER1H"
        boards_to_fetch["243"] = "SOCCER2H"
        # WC halves already in WORLD_CUP_BOARDS when --no-world-cup is off
    if args.include_season:
        boards_to_fetch["262"] = "SOCCERSZN"

    print(f"📡 Fetching PrizePicks Soccer | boards: {list(boards_to_fetch.values())}")

    all_rows = []

    for lid, lname in boards_to_fetch.items():
        print(f"\n  → {lname} (league_id={lid})")
        data, included = fetch_board(lid, lname)
        if data:
            rows = build_rows(data, included, lname)
            all_rows.extend(rows)
            print(f"    ✓ {len(rows)} rows parsed")
        else:
            print(f"    ⚠️ No data for {lname} — may not be on the board today")

    if not all_rows:
        print("\n❌ No soccer props fetched — nothing on the board right now.")
        if args.append and out_path.is_file():
            print("   (--append: left existing output file unchanged)")
            sys.exit(0)
        pd.DataFrame().to_csv(args.output, index=False, encoding="utf-8-sig")
        sys.exit(1)

    df = pd.DataFrame(all_rows)
    df["line"] = pd.to_numeric(df["line"], errors="coerce")
    df["standard_line"] = pd.to_numeric(df["standard_line"], errors="coerce")
    _mstd = df["pick_type"].astype(str).str.lower().eq("standard")
    df.loc[_mstd, "standard_line"] = df.loc[_mstd, "standard_line"].fillna(df.loc[_mstd, "line"])
    df = df.drop_duplicates(subset=["projection_id"], keep="first").reset_index(drop=True)

    if args.append and out_path.is_file():
        try:
            existing = pd.read_csv(out_path, encoding="utf-8-sig")
            n_existing = len(existing)
            all_cols = list(dict.fromkeys(list(existing.columns) + list(df.columns)))
            for c in all_cols:
                if c not in existing.columns:
                    existing[c] = ""
                if c not in df.columns:
                    df[c] = ""
            existing = existing[all_cols].copy()
            df = df[all_cols].copy()
            n_new_chunk = len(df)
            combined = pd.concat([existing, df], ignore_index=True)
            for col in ("player", "prop_type", "pick_type", "pp_game_id", "league"):
                if col in combined.columns:
                    combined[col] = combined[col].astype(str).str.strip()
            combined["line"] = pd.to_numeric(combined["line"], errors="coerce")
            dedup_cols = [
                c
                for c in ("player", "prop_type", "line", "pp_game_id", "pick_type", "league")
                if c in combined.columns
            ]
            if dedup_cols:
                combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
            df = combined
            print(
                f"[step1 SOCCER append] {n_existing} existing + {n_new_chunk} new → "
                f"{len(df)} after dedup (subset={dedup_cols})"
            )
        except Exception as e:
            print(f"  [WARN] --append merge failed ({e}); writing this fetch only")

    fetched_rows = len(df)
    pre_filter_columns = list(df.columns)
    _raw_ts = pd.to_datetime(df.get("start_time", pd.Series([], dtype=object)), errors="coerce", utc=True)
    distinct_dates = sorted(
        {
            d
            for d in _raw_ts.dt.tz_convert(ZoneInfo(str(args.tz).strip() or DEFAULT_TZ)).dt.date.astype("string").tolist()
            if d and d != "nan"
        }
    )
    filtered, fallback_date = apply_game_date_filter(
        df,
        target_date=str(args.date).strip(),
        tz_name=str(args.tz).strip() or DEFAULT_TZ,
        allow_nearest_future=bool(args.allow_nearest_future),
    )
    print(
        f"[INFO] Soccer step1 fetched={fetched_rows} rows; "
        f"date_filter={args.date} ({args.tz}); survived={len(filtered)}"
    )
    if distinct_dates:
        print(f"[INFO] Soccer step1 game_dates_on_board={distinct_dates}")
    if fallback_date:
        print("[WARNING] Soccer step1 allow-nearest-future: skipping date filter")
    df = filtered

    if len(df) == 0:
        print(no_props_log_line("Soccer", str(args.date).strip()))
        if should_preserve_append_output(out_path, args.append):
            print("   (--append: left existing output file unchanged)")
            sys.exit(0)
        pd.DataFrame(
            columns=pre_filter_columns
            or ["player", "prop_type", "line", "start_time", "team", "opp_team", "pick_type", "league"]
        ).to_csv(
            args.output, index=False, encoding="utf-8-sig"
        )
        print(f"\n[INFO] Saved empty date-filtered Soccer step1 CSV -> {args.output}")
        sys.exit(0)

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    try:
        from scripts.line_history_archive import archive_lines

        archive_lines(df, sport="SOCCER")
    except Exception as _arch_exc:
        print(f"  [WARN] line_history archive skipped: {_arch_exc}")
    print(f"\n✅ Saved {len(df)} rows -> {args.output}")
    league_counts = df["league"].value_counts().to_dict()
    print(f"   Leagues: {league_counts}")
    prop_counts = df["prop_type"].value_counts().head(10).to_dict()
    print(f"   Top props: {prop_counts}")


if __name__ == "__main__":
    main()
