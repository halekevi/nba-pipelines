#!/usr/bin/env python3
"""
step1_fetch_prizepicks_tennis.py — PrizePicks Tennis projections (API, NBA-style fetch).

Default: auto-detect league_id among candidates (14, 20, 7, 9, 12, 15) using
tennis-like prop names vs NBA stat noise.

Run:
  py -3.14 Tennis/scripts/step1_fetch_prizepicks_tennis.py
  py -3.14 Tennis/scripts/step1_fetch_prizepicks_tennis.py --list-leagues
"""

from __future__ import annotations

import argparse
import importlib.util
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Tennis/scripts -> repo root is parents[3].
REPO_ROOT = Path(__file__).resolve().parents[3]

LEAGUES_URL = "https://api.prizepicks.com/leagues"


def _leagues_request_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://app.prizepicks.com/",
        "Origin": "https://app.prizepicks.com",
    }


def list_leagues_and_print(*, retries: int = 5) -> None:
    """GET /leagues and print id + name; exit process on failure."""
    last_exc: Exception | None = None
    payload: dict[str, Any] | None = None
    for attempt in range(1, retries + 1):
        try:
            if attempt > 1:
                time.sleep(random.uniform(2.0, 5.0))
            r = requests.get(LEAGUES_URL, headers=_leagues_request_headers(), timeout=(10.0, 45.0))
            if r.status_code == 429:
                wait = random.uniform(45.0, 95.0)
                print(f"  [429] Rate limited — waiting {wait:.0f}s (attempt {attempt}/{retries})")
                time.sleep(wait)
                continue
            r.raise_for_status()
            payload = r.json()
            break
        except Exception as e:
            last_exc = e
            time.sleep(min(30.0, (2 ** (attempt - 1)) * 2.0) + random.uniform(0.5, 2.0))
    if not payload:
        print(f"[Tennis step1] list-leagues failed: {last_exc}")
        sys.exit(1)

    rows: list[tuple[str, str]] = []
    for o in payload.get("data") or []:
        if not isinstance(o, dict):
            continue
        lid = str(o.get("id", "")).strip()
        attr = o.get("attributes") or {}
        name = str(attr.get("name") or attr.get("abbr") or "").strip() or "(no name)"
        if lid:
            rows.append((lid, name))

    def _sort_key(t: tuple[str, str]) -> tuple[int, int, str, str]:
        lid, name = t
        try:
            return (0, int(lid), name.lower(), lid)
        except ValueError:
            return (1, 0, name.lower(), lid)

    rows.sort(key=_sort_key)

    print("[Tennis step1] PrizePicks leagues (from GET /leagues)")
    print(f"{'ID':<8} | Name")
    print("-" * 56)
    for lid, name in rows:
        print(f"{lid:<8} | {name}")
    print("-" * 56)
    print(f"Total: {len(rows)} leagues")


def _load_nba_step1():
    p = REPO_ROOT / "NBA" / "scripts" / "step1_fetch_prizepicks_api.py"
    spec = importlib.util.spec_from_file_location("nba_pp_fetch", p)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load NBA step1 from {p}")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _pick_type_from_attrs(attrs: dict[str, Any]) -> tuple[str, str, str, str]:
    """Returns (pick_type, standard_line, goblin_line, demon_line) as strings for CSV."""
    desc = str(attrs.get("description", "") or "")
    odds_type = str(attrs.get("odds_type", "")).strip().lower()
    std_api = attrs.get("standard_line") or attrs.get("standard_score") or attrs.get("baseline")
    gob_api = attrs.get("goblin_line") or attrs.get("goblin_score") or ""
    dem_api = attrs.get("demon_line") or attrs.get("demon_score") or ""

    if "🐱" in desc or "goblin" in desc.lower():
        pick = "Goblin"
    elif "😈" in desc or "demon" in desc.lower():
        pick = "Demon"
    elif odds_type == "goblin":
        pick = "Goblin"
    elif odds_type == "demon":
        pick = "Demon"
    else:
        pick = {"standard": "Standard", "goblin": "Goblin", "demon": "Demon"}.get(odds_type, "Standard")

    line = attrs.get("line_score", attrs.get("line"))
    if pick == "Standard":
        standard = std_api if std_api is not None and str(std_api).strip() != "" else line
    else:
        standard = std_api if std_api is not None else ""
    return (
        pick,
        str(standard) if standard is not None else "",
        str(gob_api) if gob_api is not None else "",
        str(dem_api) if dem_api is not None else "",
    )


def _safe_get(d: Any, path: list[str], default: Any = "") -> Any:
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur if cur is not None else default


def _norm_team(s: Any) -> str:
    return str(s or "").strip().upper()


def _tennis_league_score(df: pd.DataFrame) -> float:
    """Higher = more likely tennis board."""
    if df.empty or "prop_type" not in df.columns:
        return -1e9
    props = df["prop_type"].astype(str).str.lower()
    tennis_hits = props.str.contains(
        r"ace|double\s*fault|doublefault|games?\s*won|sets?\s*won|break\s*point|match\s*total|tennis",
        regex=True,
        na=False,
    )
    nba_hits = props.str.contains(
        r"\bpoints\b|rebounds|assists|three|steals|blocks|pra|combo|fantasy",
        regex=True,
        na=False,
    )
    return float(tennis_hits.sum() - 6.0 * nba_hits.sum())


def build_tennis_rows(data: list[dict], included: list[dict], nba_mod: Any) -> list[dict]:
    inc = nba_mod._included_index(included)
    rows: list[dict] = []

    for d in data:
        if not isinstance(d, dict):
            continue
        pid = str(d.get("id", "")).strip()
        attrs = d.get("attributes") or {}
        rel = d.get("relationships") or {}
        pick_type, standard_line_s, goblin_line_s, demon_line_s = _pick_type_from_attrs(attrs)

        line = attrs.get("line_score", attrs.get("line"))
        prop_type = str(
            attrs.get("stat_type", attrs.get("projection_type", attrs.get("name", "")))
        ).strip()

        player_id = _safe_get(rel, ["new_player", "data", "id"], "") or ""
        player_type = _safe_get(rel, ["new_player", "data", "type"], "new_player")
        player_obj = inc.get((str(player_type), str(player_id))) if player_id else None

        player_name = pos = team_pp = image_url = ""
        if isinstance(player_obj, dict):
            pa = player_obj.get("attributes") or {}
            player_name = str(pa.get("display_name", pa.get("name", ""))).strip()
            pos = str(pa.get("position", "")).strip()
            team_pp = _norm_team(pa.get("team", ""))
            image_url = str(
                pa.get("image_url")
                or pa.get("image_url_small")
                or pa.get("photo_url")
                or pa.get("headshot")
                or pa.get("avatar")
                or ""
            ).strip()

        game_id = _safe_get(rel, ["new_game", "data", "id"], "") or _safe_get(rel, ["game", "data", "id"], "")
        game_type = _safe_get(rel, ["new_game", "data", "type"], "") or _safe_get(rel, ["game", "data", "type"], "")
        game_obj = inc.get((str(game_type), str(game_id))) if game_id and game_type else None

        home = away = start_time = tournament = league_name = ""
        if isinstance(game_obj, dict):
            ga = game_obj.get("attributes") or {}
            home = _norm_team(ga.get("home_team", ""))
            away = _norm_team(ga.get("away_team", ""))
            start_time = str(ga.get("start_time", "")).strip()
            tournament = str(
                ga.get("name") or ga.get("short_name") or ga.get("slug") or ga.get("summary") or ""
            ).strip()

        league_id_rel = _safe_get(rel, ["new_league", "data", "id"], "") or _safe_get(
            rel, ["league", "data", "id"], ""
        )
        league_type = _safe_get(rel, ["new_league", "data", "type"], "") or _safe_get(
            rel, ["league", "data", "type"], "league"
        )
        league_obj = (
            inc.get((str(league_type), str(league_id_rel))) if league_id_rel and league_type else None
        )
        if isinstance(league_obj, dict):
            la = league_obj.get("attributes") or {}
            league_name = str(la.get("name") or la.get("slug") or "").strip()

        if not start_time:
            start_time = str(attrs.get("start_time", "")).strip()

        team_slot = tournament or team_pp or league_name or ""
        opp_team = ""
        if team_slot and home and away:
            opp_team = away if team_slot == home else (home if team_slot == away else "")
        if not opp_team:
            desc = str(attrs.get("description", "") or "")
            m = re.search(r"vs\.?\s+([A-Za-z0-9 .'-]{2,64})", desc, re.I)
            if m:
                opp_team = m.group(1).strip()
            elif home and away and player_name:
                for cand in (home, away):
                    if cand and cand not in player_name.upper() and len(cand) > 1:
                        opp_team = cand
                        break

        rows.append(
            {
                "projection_id": pid,
                "player_name": player_name,
                "player": player_name,
                "team": team_slot,
                "prop_type": prop_type,
                "line_score": line,
                "line": line,
                "start_time": start_time,
                "sport": "Tennis",
                "league": league_name or "Tennis",
                "pick_type": pick_type,
                "standard_line": standard_line_s,
                "goblin_line": goblin_line_s,
                "demon_line": demon_line_s,
                "pp_projection_id": pid,
                "player_id": str(player_id).strip(),
                "pp_game_id": str(game_id or "").strip(),
                "pos": pos,
                "opp_team": opp_team,
                "pp_home_team": home,
                "pp_away_team": away,
                "image_url": image_url,
                "tournament": tournament,
            }
        )

    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--list-leagues",
        action="store_true",
        help="Fetch api.prizepicks.com/leagues, print ID | Name, then exit (no projections fetch).",
    )
    ap.add_argument("--output", default="outputs/step1_tennis_props.csv")
    ap.add_argument(
        "--league_id",
        default="auto",
        help="PrizePicks league_id, or 'auto' to scan candidates",
    )
    ap.add_argument("--per_page", type=int, default=250)
    ap.add_argument("--max_pages", type=int, default=10)
    ap.add_argument("--retries", type=int, default=5)
    ap.add_argument("--min_rows", type=int, default=5)
    ap.add_argument("--min_teams", type=int, default=1)
    ap.add_argument("--replace", action="store_true", help="Do not merge with existing output")
    args = ap.parse_args()

    if args.list_leagues:
        print("[Tennis step1] Starting...")
        list_leagues_and_print(retries=args.retries)
        return

    print("[Tennis step1] Starting...")
    nba = _load_nba_step1()

    root = Path(__file__).resolve().parent.parent
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = root / out_path

    def _fallback_to_existing_csv(reason: str) -> bool:
        """Use prior step1 CSV when live fetch is blocked and prior board is usable."""
        if not out_path.is_file():
            return False
        try:
            old = pd.read_csv(out_path, low_memory=False)
        except Exception:
            return False
        old_rows = len(old)
        if old_rows < max(1, int(args.min_rows)):
            return False
        print(
            f"[Tennis step1] WARN: {reason}. Using existing board at {out_path} "
            f"(rows={old_rows})"
        )
        return True

    candidates = ["14", "20", "7", "9", "12", "15"]
    chosen = str(args.league_id).strip().lower()
    use_id: str

    if chosen == "auto":
        best_id = ""
        best_score = -1e10
        print("[Tennis step1] Auto-detecting tennis league_id (first page each)...")
        for lid in candidates:
            try:
                time.sleep(2.75)
                data, inc = nba.fetch_projections(
                    league_id=lid,
                    per_page=args.per_page,
                    max_pages=1,
                    retries=args.retries,
                )
                rows = build_tennis_rows(data, inc, nba)
                df_try = pd.DataFrame(rows)
                sc = _tennis_league_score(df_try)
                print(f"  league_id={lid}  rows={len(df_try)}  tennis_score={sc:.1f}")
                if sc > best_score and len(df_try) > 0:
                    best_score = sc
                    best_id = lid
            except Exception as e:
                print(f"  league_id={lid}  ERROR: {e}")
        if not best_id:
            print("[Tennis step1] ERROR: Could not find a league_id with projections.")
            pd.DataFrame().to_csv(out_path, index=False)
            sys.exit(1)
        use_id = best_id
        print(f"[Tennis step1] Using league_id={use_id} (best tennis_score={best_score:.1f})")
    else:
        use_id = str(args.league_id).strip()

    try:
        data, included = nba.fetch_projections(
            league_id=use_id,
            per_page=args.per_page,
            max_pages=args.max_pages,
            retries=args.retries,
        )
    except Exception as e:
        if _fallback_to_existing_csv(f"fetch failed ({e})"):
            print("[Tennis step1] BOARD_OK_FALLBACK")
            return
        print(f"[Tennis step1] Fetch failed: {e}")
        sys.exit(1)

    if not data:
        if _fallback_to_existing_csv("no projections returned"):
            print("[Tennis step1] BOARD_OK_FALLBACK")
            return
        print("[Tennis step1] No projections returned.")
        sys.exit(1)

    rows = build_tennis_rows(data, included, nba)
    df = pd.DataFrame(rows).fillna("")
    for col in ("line", "line_score", "standard_line", "goblin_line", "demon_line"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    _mstd = df["pick_type"].astype(str).str.lower().eq("standard")
    df.loc[_mstd, "standard_line"] = df.loc[_mstd, "standard_line"].fillna(df.loc[_mstd, "line_score"])

    before = len(df)
    df = df.drop_duplicates(subset=["projection_id"], keep="first").reset_index(drop=True)
    if before != len(df):
        print(f"  Deduped: {before} → {len(df)}")

    preferred = [
        "projection_id",
        "player_name",
        "team",
        "prop_type",
        "line_score",
        "start_time",
        "sport",
        "league",
        "pick_type",
        "standard_line",
        "goblin_line",
        "demon_line",
        "pp_projection_id",
        "player_id",
        "pp_game_id",
        "player",
        "line",
        "pos",
        "opp_team",
        "pp_home_team",
        "pp_away_team",
        "image_url",
        "tournament",
    ]
    extra = [c for c in df.columns if c not in preferred]
    df = df[preferred + extra]

    n_rows = len(df)
    n_teams = df["team"].astype(str).replace("", pd.NA).dropna().nunique()
    n_players = df["player"].astype(str).replace("", pd.NA).dropna().nunique()
    print(f"[Tennis step1] Fetched {n_rows} props | tournaments/teams={n_teams} | players={n_players}")

    if n_rows < args.min_rows or n_teams < args.min_teams:
        if _fallback_to_existing_csv(
            f"board too small (rows={n_rows}, teams={n_teams}; "
            f"min_rows={args.min_rows}, min_teams={args.min_teams})"
        ):
            print("[Tennis step1] BOARD_OK_FALLBACK")
            return
        print(
            f"[Tennis step1] BOARD_TOO_SMALL (min_rows={args.min_rows}, min_teams={args.min_teams}) — writing CSV and exiting 1"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        sys.exit(1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.is_file() and not args.replace:
        try:
            old = pd.read_csv(out_path, encoding="utf-8-sig")
            for c in df.columns:
                if c not in old.columns:
                    old[c] = ""
            for c in old.columns:
                if c not in df.columns:
                    df[c] = ""
            old = old[df.columns]
            new_ids = set(df["projection_id"].astype(str).str.strip())
            kept = old[~old["projection_id"].astype(str).str.strip().isin(new_ids)]
            df = pd.concat([df, kept], ignore_index=True)
            print(f"[Tennis step1] Merged with prior file → {len(df)} total rows")
        except Exception as e:
            print(f"  [WARN] merge skipped: {e}")

    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"[Tennis step1] Saved → {out_path}")
    print("[Tennis step1] BOARD_OK")


if __name__ == "__main__":
    main()
