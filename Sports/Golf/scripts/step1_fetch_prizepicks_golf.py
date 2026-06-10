#!/usr/bin/env python3
"""
step1_fetch_prizepicks_golf.py — PrizePicks PGA / golf projections (API fetch).

Default league_id=1 (PGA). Also supports EUROGOLF (131), LPGA (256), LIVGOLF (228).

Run:
  py -3.14 Sports/Golf/scripts/step1_fetch_prizepicks_golf.py
  py -3.14 Sports/Golf/scripts/step1_fetch_prizepicks_golf.py --list-leagues
  py -3.14 Sports/Golf/scripts/step1_fetch_prizepicks_golf.py --league_id 256
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

REPO_ROOT = Path(__file__).resolve().parents[3]
LEAGUES_URL = "https://api.prizepicks.com/leagues"

# Verified via GET /leagues (2026-06): PGA=1, EUROGOLF=131, LPGA=256, LIVGOLF=228 (CFB=15).
GOLF_LEAGUE_CANDIDATES = ("1", "131", "256", "228")


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
        print(f"[Golf step1] list-leagues failed: {last_exc}")
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
    print("[Golf step1] PrizePicks leagues (golf-related highlighted)")
    print(f"{'ID':<8} | Name")
    print("-" * 56)
    for lid, name in rows:
        mark = " *" if lid in GOLF_LEAGUE_CANDIDATES or "golf" in name.lower() or name.upper() in {"PGA", "LPGA"} else ""
        print(f"{lid:<8} | {name}{mark}")
    print("-" * 56)
    print(f"Total: {len(rows)} leagues")


def _load_nba_step1():
    candidates = [
        REPO_ROOT / "Sports" / "NBA" / "scripts" / "step1_fetch_prizepicks_api.py",
        REPO_ROOT / "NBA" / "scripts" / "step1_fetch_prizepicks_api.py",
    ]
    p = next((c for c in candidates if c.exists()), candidates[0])
    spec = importlib.util.spec_from_file_location("nba_pp_fetch", p)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load NBA step1 API helper")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _pick_type_from_attrs(attrs: dict[str, Any]) -> tuple[str, str, str, str]:
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


def _golf_league_score(df: pd.DataFrame) -> float:
    if df.empty or "prop_type" not in df.columns:
        return -1e9
    props = df["prop_type"].astype(str).str.lower()
    golf_hits = props.str.contains(
        r"birdie|bogey|fairway|gir|green|putt|stroke|fantasy|eagle|hole|round|driving|approach|total|score",
        regex=True,
        na=False,
    )
    nba_hits = props.str.contains(
        r"\bpoints\b|rebounds|assists|three|steals|blocks|pra|combo",
        regex=True,
        na=False,
    )
    tennis_hits = props.str.contains(r"ace|double\s*fault|games?\s*won|sets?\s*won", regex=True, na=False)
    mma_hits = props.str.contains(r"strike|takedown|significant", regex=True, na=False)
    cfb_hits = props.str.contains(r"passing|rushing|receiving|touchdown", regex=True, na=False)
    return float(
        golf_hits.sum()
        - 5.0 * nba_hits.sum()
        - 4.0 * tennis_hits.sum()
        - 4.0 * mma_hits.sum()
        - 4.0 * cfb_hits.sum()
    )


def build_golf_rows(data: list[dict], included: list[dict], nba_mod: Any) -> list[dict]:
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

        player_name = pos = image_url = ""
        if isinstance(player_obj, dict):
            pa = player_obj.get("attributes") or {}
            player_name = str(pa.get("display_name", pa.get("name", ""))).strip()
            pos = str(pa.get("position", "")).strip()
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

        start_time = tournament = league_name = course = ""
        if isinstance(game_obj, dict):
            ga = game_obj.get("attributes") or {}
            start_time = str(ga.get("start_time", "")).strip()
            tournament = str(
                ga.get("name") or ga.get("short_name") or ga.get("slug") or ga.get("summary") or ""
            ).strip()
            course = str(ga.get("venue") or ga.get("course") or "").strip()

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
            league_name = str(la.get("name") or la.get("abbr") or la.get("slug") or "").strip()

        if not start_time:
            start_time = str(attrs.get("start_time", "")).strip()

        event = tournament or league_name or course or "GOLF"

        rows.append(
            {
                "projection_id": pid,
                "player_name": player_name,
                "player": player_name,
                "team": event,
                "event": event,
                "tournament": tournament,
                "course": course,
                "prop_type": prop_type,
                "line_score": line,
                "line": line,
                "start_time": start_time,
                "sport": "Golf",
                "league": league_name or "PGA",
                "pick_type": pick_type,
                "standard_line": standard_line_s,
                "goblin_line": goblin_line_s,
                "demon_line": demon_line_s,
                "pp_projection_id": pid,
                "player_id": str(player_id).strip(),
                "pp_game_id": str(game_id or "").strip(),
                "pos": pos,
                "opp_team": course,
                "image_url": image_url,
            }
        )

    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-leagues", action="store_true")
    ap.add_argument("--output", default="outputs/step1_golf_props.csv")
    ap.add_argument("--league_id", default="1", help="PrizePicks league_id or 'auto'")
    ap.add_argument("--per_page", type=int, default=250)
    ap.add_argument("--max_pages", type=int, default=10)
    ap.add_argument("--retries", type=int, default=5)
    ap.add_argument("--min_rows", type=int, default=3)
    ap.add_argument("--replace", action="store_true")
    args = ap.parse_args()

    if args.list_leagues:
        list_leagues_and_print(retries=args.retries)
        return

    print("[Golf step1] Starting...")
    nba = _load_nba_step1()
    root = Path(__file__).resolve().parent.parent
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = root / out_path

    def _fallback_to_existing_csv(reason: str) -> bool:
        if not out_path.is_file():
            return False
        try:
            old = pd.read_csv(out_path, low_memory=False)
        except Exception:
            return False
        if len(old) < max(1, int(args.min_rows)):
            return False
        print(f"[Golf step1] WARN: {reason}. Using existing board at {out_path} (rows={len(old)})")
        return True

    chosen = str(args.league_id).strip().lower()
    if chosen == "auto":
        best_id = ""
        best_score = -1e10
        print("[Golf step1] Auto-detecting golf league_id...", flush=True)
        for lid in GOLF_LEAGUE_CANDIDATES:
            try:
                time.sleep(2.5)
                data, inc = nba.fetch_projections(
                    league_id=lid, per_page=args.per_page, max_pages=1, retries=args.retries
                )
                df_try = pd.DataFrame(build_golf_rows(data, inc, nba))
                sc = _golf_league_score(df_try)
                print(f"  league_id={lid}  rows={len(df_try)}  golf_score={sc:.1f}", flush=True)
                if sc > best_score and len(df_try) > 0:
                    best_score = sc
                    best_id = lid
            except Exception as e:
                print(f"  league_id={lid}  ERROR: {e}", flush=True)
        if not best_id:
            if _fallback_to_existing_csv("auto-detect found no golf board"):
                print("[Golf step1] BOARD_OK_FALLBACK")
                return
            print("[Golf step1] ERROR: No golf league_id with projections.")
            sys.exit(1)
        use_id = best_id
        print(f"[Golf step1] Using league_id={use_id} (golf_score={best_score:.1f})")
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
            print("[Golf step1] BOARD_OK_FALLBACK")
            return
        print(f"[Golf step1] Fetch failed: {e}")
        sys.exit(1)

    if not data:
        if _fallback_to_existing_csv("no projections returned"):
            print("[Golf step1] BOARD_OK_FALLBACK")
            return
        print("[Golf step1] No projections returned.")
        sys.exit(1)

    rows = build_golf_rows(data, included, nba)
    df = pd.DataFrame(rows).fillna("")
    for col in ("line", "line_score", "standard_line", "goblin_line", "demon_line"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    _mstd = df["pick_type"].astype(str).str.lower().eq("standard")
    if "standard_line" in df.columns and "line_score" in df.columns:
        df.loc[_mstd, "standard_line"] = df.loc[_mstd, "standard_line"].fillna(df.loc[_mstd, "line_score"])

    before = len(df)
    df = df.drop_duplicates(subset=["projection_id"], keep="first").reset_index(drop=True)
    if before != len(df):
        print(f"  Deduped: {before} → {len(df)}")

    preferred = [
        "projection_id", "player_name", "player", "team", "event", "tournament", "course",
        "prop_type", "line_score", "line", "start_time", "sport", "league", "pick_type",
        "standard_line", "goblin_line", "demon_line", "pp_projection_id", "player_id",
        "pp_game_id", "pos", "opp_team", "image_url",
    ]
    extra = [c for c in df.columns if c not in preferred]
    df = df[[c for c in preferred if c in df.columns] + extra]

    n_rows = len(df)
    n_events = df["event"].astype(str).replace("", pd.NA).dropna().nunique()
    n_players = df["player"].astype(str).replace("", pd.NA).dropna().nunique()
    print(f"[Golf step1] Fetched {n_rows} props | events={n_events} | players={n_players}")

    if n_rows < args.min_rows:
        if _fallback_to_existing_csv(f"board too small (rows={n_rows})"):
            print("[Golf step1] BOARD_OK_FALLBACK")
            return
        print(f"[Golf step1] BOARD_TOO_SMALL (min_rows={args.min_rows})")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        sys.exit(1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"[Golf step1] Saved → {out_path}")
    print("[Golf step1] BOARD_OK")


if __name__ == "__main__":
    main()
