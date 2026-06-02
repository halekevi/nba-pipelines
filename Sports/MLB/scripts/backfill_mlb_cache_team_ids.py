#!/usr/bin/env python3
"""
Backfill TEAM_ID / OPP_TEAM_ID on 2025 mlb_stats_cache rows where either is missing.

GAME_ID in cache is MLB Stats API gamePk (numeric), not gid_YYYY_MM_DD strings.
Primary: boxscore API per gamePk -> home/away team ids + player roster side.
Fallback: re-fetch one game-log split for (player_id, season, gamePk).

Usage (repo root):
  py -3.14 Sports/MLB/scripts/backfill_mlb_cache_team_ids.py
  py -3.14 Sports/MLB/scripts/backfill_mlb_cache_team_ids.py --dry-run
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from step4_attach_player_stats_mlb import (  # noqa: E402
    MLB_HEADERS,
    MLB_TEAM_ID_MAP,
    fetch_game_log,
)

BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
GID_RE = re.compile(
    r"^gid_(?P<year>\d{4})_(?P<mon>\d{2})_(?P<day>\d{2})_(?P<away>[a-z]+)mlb_(?P<home>[a-z]+)mlb",
    re.IGNORECASE,
)

# Abbrev from gid string -> statsapi team id (stable; mirrors step4 map).
GID_ABBREV_TO_ID: dict[str, str] = {k: str(v) for k, v in MLB_TEAM_ID_MAP.items()}


def _is_missing(val: Any) -> bool:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return True
    s = str(val).strip()
    return s == "" or s.lower() in ("nan", "none", "<na>")


def _norm_id(val: Any) -> str:
    if _is_missing(val):
        return ""
    try:
        return str(int(float(val)))
    except (TypeError, ValueError):
        return str(val).strip()


_GID_ABBREV_ALIASES: dict[str, str] = {
    "AZ": "ARI",
    "CHW": "CWS",
    "KC": "KC",
    "KCR": "KC",
    "TB": "TB",
    "TBR": "TB",
    "SF": "SF",
    "SFG": "SF",
    "SD": "SD",
    "SDP": "SD",
    "WSN": "WSH",
    "WAS": "WSH",
    "OAK": "ATH",
}


def _abbr_to_team_id(abbr: str) -> str:
    key = str(abbr or "").strip().upper()
    if not key:
        return ""
    canon = _GID_ABBREV_ALIASES.get(key, key)
    return GID_ABBREV_TO_ID.get(canon, GID_ABBREV_TO_ID.get(key, ""))


def _parse_gid_teams(game_id: str) -> tuple[str, str] | None:
    """Return (away_id, home_id) from gid_* GAME_ID, or None."""
    m = GID_RE.match(str(game_id or "").strip())
    if not m:
        return None
    away_id = _abbr_to_team_id(m.group("away"))
    home_id = _abbr_to_team_id(m.group("home"))
    if away_id and home_id:
        return away_id, home_id
    return None


def _fetch_boxscore(game_pk: str, *, sleep_s: float = 0.35) -> dict[str, Any] | None:
    try:
        pk = int(float(game_pk))
    except (TypeError, ValueError):
        return None
    try:
        resp = requests.get(
            BOXSCORE_URL.format(game_pk=pk),
            headers=MLB_HEADERS,
            timeout=25,
        )
        resp.raise_for_status()
        time.sleep(sleep_s)
        return resp.json()
    except Exception as exc:
        print(f"  [WARN] boxscore {game_pk}: {exc}")
        return None


def _boxscore_team_ids(box: dict[str, Any]) -> tuple[str, str] | None:
    try:
        away = _norm_id((box.get("teams") or {}).get("away", {}).get("team", {}).get("id"))
        home = _norm_id((box.get("teams") or {}).get("home", {}).get("team", {}).get("id"))
        if away and home:
            return away, home
    except Exception:
        pass
    return None


def _player_side_in_boxscore(box: dict[str, Any], player_id: str) -> str | None:
    """Return 'away' or 'home' if player appears on that roster."""
    pid = str(player_id).strip()
    if not pid:
        return None
    for side in ("away", "home"):
        players = (box.get("teams") or {}).get(side, {}).get("players") or {}
        for _key, entry in players.items():
            person_id = str((entry.get("person") or {}).get("id", "")).strip()
            if person_id == pid:
                return side
    return None


def _team_ids_from_game_log(player_id: str, season: str, game_pk: str, player_type: str) -> tuple[str, str] | None:
    group = "pitching" if str(player_type).lower() == "pitcher" else "hitting"
    splits = fetch_game_log(str(player_id), group, str(season))
    for split in splits:
        gpk = str((split.get("game") or {}).get("gamePk", "")).strip()
        if gpk != str(game_pk).strip():
            continue
        tid = _norm_id((split.get("team") or {}).get("id"))
        oid = _norm_id((split.get("opponent") or {}).get("id"))
        if tid and oid:
            return tid, oid
    return None


def _resolve_pair(
    player_id: str,
    game_id: str,
    season: str,
    player_type: str,
    game_box_cache: dict[str, dict[str, Any] | None],
    gid_game_cache: dict[str, tuple[str, str]],
    *,
    sleep_s: float = 0.35,
) -> tuple[str, str, str]:
    """
    Return (team_id, opp_team_id, method) or ('','', 'unresolved').
    method: gid_parse | boxscore | game_log | unresolved
    """
    gid_key = str(game_id).strip()
    if gid_key in gid_game_cache:
        away_id, home_id = gid_game_cache[gid_key]
        # Without player side, cannot assign TEAM vs OPP from gid alone
        # fall through to boxscore for side

    parsed = _parse_gid_teams(gid_key)
    if parsed:
        away_id, home_id = parsed
        gid_game_cache[gid_key] = (away_id, home_id)

    box = game_box_cache.get(gid_key)
    if box is None and gid_key not in game_box_cache:
        if gid_key.isdigit():
            game_box_cache[gid_key] = _fetch_boxscore(gid_key, sleep_s=sleep_s)
            box = game_box_cache[gid_key]

    if box:
        teams = _boxscore_team_ids(box)
        if teams:
            away_id, home_id = teams
            side = _player_side_in_boxscore(box, player_id)
            if side == "away":
                return away_id, home_id, "boxscore"
            if side == "home":
                return home_id, away_id, "boxscore"

    gl = _team_ids_from_game_log(player_id, season, gid_key, player_type)
    if gl:
        return gl[0], gl[1], "game_log"

    return "", "", "unresolved"


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill MLB cache TEAM_ID / OPP_TEAM_ID for 2025 rows.")
    ap.add_argument(
        "--cache",
        default=str(_REPO / "Sports" / "MLB" / "mlb_stats_cache.csv"),
        help="Path to mlb_stats_cache.csv",
    )
    ap.add_argument(
        "--backup",
        default="",
        help="Backup path (default: <cache>_pre_backfill.csv beside cache)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print summary only; do not write.")
    ap.add_argument("--sleep", type=float, default=0.35, help="Sleep between boxscore API calls.")
    args = ap.parse_args()

    cache_path = Path(args.cache)
    if not cache_path.is_file():
        print(f"ERROR: cache not found: {cache_path}")
        return 1

    backup_path = Path(args.backup) if args.backup else cache_path.with_name(
        f"{cache_path.stem}_pre_backfill{cache_path.suffix}"
    )

    print(f"→ Loading {cache_path}")
    if not args.dry_run and not backup_path.exists():
        print(f"→ Backup -> {backup_path}")
        shutil.copy2(cache_path, backup_path)

    df = pd.read_csv(cache_path, low_memory=False, encoding="utf-8-sig")
    for col in ("TEAM_ID", "OPP_TEAM_ID"):
        if col in df.columns:
            df[col] = df[col].apply(lambda x: "" if _is_missing(x) else _norm_id(x)).astype(str)
    if "BACKFILL_STATUS" not in df.columns:
        df["BACKFILL_STATUS"] = ""
    else:
        df["BACKFILL_STATUS"] = df["BACKFILL_STATUS"].astype(str)

    df["_game_year"] = pd.to_datetime(df["GAME_DATE"], errors="coerce").dt.year
    need_mask = (df["_game_year"] == 2025) & (
        df["TEAM_ID"].apply(_is_missing) | df["OPP_TEAM_ID"].apply(_is_missing)
    )
    n_need = int(need_mask.sum())
    print(f"  2025 rows missing TEAM_ID or OPP_TEAM_ID: {n_need:,} / {len(df):,}")

    if n_need == 0:
        print("Nothing to backfill.")
        return 0

    sub = df.loc[need_mask]
    gid_null = int(sub["GAME_ID"].apply(_is_missing).sum())
    print(f"  GAME_ID null on those rows: {gid_null:,} (numeric gamePk expected: {not gid_null})")
    sample_gid = sub["GAME_ID"].dropna().astype(str).head(3).tolist()
    print(f"  GAME_ID samples: {sample_gid}")

    keys = (
        sub[["MLB_PLAYER_ID", "GAME_ID", "GAME_DATE", "SEASON", "PLAYER_TYPE"]]
        .drop_duplicates()
        .reset_index(drop=True)
    )
    print(f"  Unique player-game keys to resolve: {len(keys):,} ({sub['GAME_ID'].nunique():,} games)")

    game_box_cache: dict[str, dict[str, Any] | None] = {}
    gid_game_cache: dict[str, tuple[str, str]] = {}
    unique_games = sorted({str(g).strip() for g in keys["GAME_ID"] if not _is_missing(g)})
    print(f"  Prefetching boxscores for {len(unique_games):,} gamePk values…")
    for gi, gid in enumerate(unique_games):
        if gid.isdigit():
            game_box_cache[gid] = _fetch_boxscore(gid, sleep_s=float(args.sleep))
        else:
            parsed = _parse_gid_teams(gid)
            if parsed:
                gid_game_cache[gid] = parsed
        if (gi + 1) % 100 == 0:
            print(f"    … {gi + 1:,} / {len(unique_games):,} games")

    resolved: dict[tuple[str, str], tuple[str, str, str]] = {}

    for i, row in keys.iterrows():
        pid = str(row["MLB_PLAYER_ID"]).strip()
        gid = str(row["GAME_ID"]).strip()
        season = str(row["SEASON"]).strip()
        ptype = str(row["PLAYER_TYPE"]).strip()
        key = (pid, gid)
        if key in resolved:
            continue
        tid, oid, method = _resolve_pair(
            pid, gid, season, ptype, game_box_cache, gid_game_cache, sleep_s=float(args.sleep)
        )
        resolved[key] = (tid, oid, method)
        if (i + 1) % 200 == 0:
            print(f"  … resolved {i + 1:,} / {len(keys):,} keys")

    rows_updated = 0
    rows_unresolved = 0
    method_counts: dict[str, int] = {}

    for idx in df.index[need_mask]:
        pid = str(df.at[idx, "MLB_PLAYER_ID"]).strip()
        gid = str(df.at[idx, "GAME_ID"]).strip()
        tid, oid, method = resolved.get((pid, gid), ("", "", "unresolved"))
        method_counts[method] = method_counts.get(method, 0) + 1
        if tid and oid:
            df.at[idx, "TEAM_ID"] = tid
            df.at[idx, "OPP_TEAM_ID"] = oid
            df.at[idx, "BACKFILL_STATUS"] = "backfilled"
            rows_updated += 1
        else:
            df.at[idx, "BACKFILL_STATUS"] = "unresolved"
            rows_unresolved += 1

    still_missing = int(
        (
            (df["_game_year"] == 2025)
            & (df["TEAM_ID"].apply(_is_missing) | df["OPP_TEAM_ID"].apply(_is_missing))
        ).sum()
    )
    pct_filled = 100.0 * (1.0 - still_missing / n_need) if n_need else 100.0

    print("\n=== Backfill summary (2025 targets only) ===")
    print(f"  rows_targeted:     {n_need:,}")
    print(f"  rows_updated:      {rows_updated:,}")
    print(f"  rows_unresolved:   {rows_unresolved:,}")
    print(f"  still_missing:     {still_missing:,}")
    print(f"  pct_filled:        {pct_filled:.1f}%")
    print(f"  boxscore_calls:    {sum(1 for v in game_box_cache.values() if v is not None):,}")
    print("  methods:", method_counts)

    df = df.drop(columns=["_game_year"], errors="ignore")

    if args.dry_run:
        print("\n[dry-run] No files written.")
        return 0

    print(f"→ Writing {cache_path}")
    df.to_csv(cache_path, index=False, encoding="utf-8-sig")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
