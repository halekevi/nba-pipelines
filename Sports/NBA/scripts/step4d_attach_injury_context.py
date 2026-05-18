#!/usr/bin/env python3
"""step4d_attach_injury_context.py — ESPN injury trickle-up context."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs
from nba_stats_api import USAGE_CACHE, _load_json, norm_team

log = logging.getLogger("nba.step4d")

ESPN_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
INJURY_CACHE = Path(__file__).resolve().parents[1] / "data" / "nba_injury_cache.json"
TIMEOUT = 10
CACHE_HOURS = 2.0

TEAM_DISPLAY_TO_ABBR = {
    "atlanta hawks": "ATL",
    "boston celtics": "BOS",
    "brooklyn nets": "BKN",
    "charlotte hornets": "CHA",
    "chicago bulls": "CHI",
    "cleveland cavaliers": "CLE",
    "dallas mavericks": "DAL",
    "denver nuggets": "DEN",
    "detroit pistons": "DET",
    "golden state warriors": "GSW",
    "houston rockets": "HOU",
    "indiana pacers": "IND",
    "la clippers": "LAC",
    "los angeles clippers": "LAC",
    "los angeles lakers": "LAL",
    "memphis grizzlies": "MEM",
    "miami heat": "MIA",
    "milwaukee bucks": "MIL",
    "minnesota timberwolves": "MIN",
    "new orleans pelicans": "NOP",
    "new york knicks": "NYK",
    "oklahoma city thunder": "OKC",
    "orlando magic": "ORL",
    "philadelphia 76ers": "PHI",
    "phoenix suns": "PHX",
    "portland trail blazers": "POR",
    "sacramento kings": "SAC",
    "san antonio spurs": "SAS",
    "toronto raptors": "TOR",
    "utah jazz": "UTA",
    "washington wizards": "WSH",
}


def _norm_name(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _cache_stale(entry: dict, hours: float) -> bool:
    ts = entry.get("fetched_at")
    if not ts:
        return True
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt > timedelta(hours=hours)
    except Exception:
        return True


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(path)


def _scale_pct(v: object) -> float:
    if v is None:
        return 0.0
    try:
        x = float(v)
        return x / 100.0 if x > 1.0 else x
    except (TypeError, ValueError):
        return 0.0


def fetch_espn_injuries() -> dict[str, list[dict]]:
    """Return team_abbr -> list of {player_name, status, usage_pct, ast_pct}."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PropORACLE/1.0)"}
    try:
        time.sleep(0.5)
        r = requests.get(ESPN_INJURIES, headers=headers, timeout=TIMEOUT)
        if r.status_code != 200:
            return {}
        data = r.json()
    except Exception as exc:
        log.warning("ESPN injuries fetch failed: %s", exc)
        return {}

    usage_cache = _load_json(USAGE_CACHE)
    players_by_name: dict[str, dict] = {}
    for block in usage_cache.values():
        if not isinstance(block, dict):
            continue
        for rec in (block.get("players") or {}).values():
            nm = _norm_name(rec.get("player_name", ""))
            if nm:
                players_by_name[nm] = rec

    team_out: dict[str, list[dict]] = {}
    for team_block in data.get("injuries", []) or []:
        team_name = str(team_block.get("displayName", team_block.get("team", ""))).strip()
        team_abbr = norm_team(team_block.get("abbreviation", ""))
        if not team_abbr:
            team_abbr = TEAM_DISPLAY_TO_ABBR.get(team_name.lower(), "")
        if not team_abbr and team_name:
            team_abbr = norm_team(team_name[:3])
        entries = []
        for inj in team_block.get("injuries", []) or []:
            athlete = inj.get("athlete") or {}
            pname = str(athlete.get("displayName", inj.get("name", ""))).strip()
            status = str(inj.get("status", inj.get("type", ""))).strip().upper()
            if not pname:
                continue
            if "OUT" not in status and status not in ("O", "OUT"):
                continue
            prec = players_by_name.get(_norm_name(pname), {})
            usg = _scale_pct(prec.get("usage_pct"))
            ast = _scale_pct(prec.get("ast_pct"))
            entries.append(
                {
                    "player_name": pname,
                    "status": status,
                    "usage_pct": usg,
                    "ast_pct": ast,
                }
            )
        if team_abbr:
            team_out[team_abbr] = entries
    return team_out


def _team_injury_flags(out_players: list[dict]) -> dict:
    vacuum = sum(p.get("usage_pct", 0.0) for p in out_players)
    star_out = any(p.get("usage_pct", 0.0) >= 0.28 for p in out_players)
    facilitator_out = any(
        p.get("ast_pct", 0.0) >= 0.25 or p.get("usage_pct", 0.0) >= 0.28 for p in out_players
    )
    return {
        "usage_vacuum": round(vacuum, 4),
        "team_star_out": bool(star_out),
        "key_facilitator_out": bool(facilitator_out),
    }


def ensure_injury_cache(refresh: bool = False) -> dict[str, dict]:
    cache = _load_json(INJURY_CACHE)
    key = "latest"
    block = cache.get(key, {})
    if refresh or not block or _cache_stale(block, CACHE_HOURS):
        teams = fetch_espn_injuries()
        cache[key] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "teams": teams,
        }
        _save_json(INJURY_CACHE, cache)
    return (cache.get(key) or {}).get("teams") or {}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="step4_with_stats.csv")
    ap.add_argument("--output", default="step4_with_stats.csv")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")
    team_injuries = ensure_injury_cache(refresh=args.refresh)

    for c in (
        "team_star_out",
        "key_facilitator_out",
        "injury_boost_candidate",
    ):
        if c not in df.columns:
            df[c] = False
    if "usage_vacuum" not in df.columns:
        df["usage_vacuum"] = np.nan

    hit = 0
    for idx, row in df.iterrows():
        team = norm_team(row.get("team", ""))
        flags = {"usage_vacuum": 0.0, "team_star_out": False, "key_facilitator_out": False}
        if team and team in team_injuries:
            flags = _team_injury_flags(team_injuries[team])
            hit += 1
        df.at[idx, "usage_vacuum"] = flags["usage_vacuum"]
        df.at[idx, "team_star_out"] = flags["team_star_out"]
        df.at[idx, "key_facilitator_out"] = flags["key_facilitator_out"]
        ut = str(row.get("usage_tier", "")).strip().lower()
        df.at[idx, "injury_boost_candidate"] = bool(
            flags["usage_vacuum"] >= 0.15 and ut in ("high", "star")
        )

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    copy_pipeline_output_to_dated_dirs(
        output_path=args.output,
        df=df,
        sport_dir_name="NBA",
        repo_root=_REPO_ROOT,
    )
    print(f"NBA injury context: {hit}/{len(df)} rows with team injury data")
    print(f"✅ Saved → {args.output}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ NBA step4d failed. {type(e).__name__}: {e}")
        sys.exit(1)
