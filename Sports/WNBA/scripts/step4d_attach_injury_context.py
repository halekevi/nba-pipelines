#!/usr/bin/env python3
"""step4d_attach_injury_context.py — WNBA ESPN injury trickle-up context."""

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
_WNBA_DIR = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs

log = logging.getLogger("wnba.step4d")

ESPN_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/injuries"
INJURY_CACHE = _WNBA_DIR / "data" / "wnba_injury_cache.json"
STAR_TIERS_CSV = _WNBA_DIR / "data" / "wnba_star_tiers.csv"
TIMEOUT = 10
CACHE_HOURS = 2.0

TEAM_ALIAS = {
    "CONN": "CON",
    "CONNECTICUT": "CON",
    "SUN": "CON",
    "NY": "NYL",
    "LIBERTY": "NYL",
    "LV": "LVA",
    "LAS VEGAS": "LVA",
    "ACES": "LVA",
    "LA": "LAS",
    "SPARKS": "LAS",
    "PHX": "PHO",
    "MERCURY": "PHO",
    "PHOENIX": "PHO",
    "WASH": "WSH",
    "WASHINGTON": "WSH",
    "MYS": "WSH",
    "MINN": "MIN",
    "LYNX": "MIN",
    "CHI": "CHI",
    "SKY": "CHI",
    "DAL": "DAL",
    "WINGS": "DAL",
    "ATL": "ATL",
    "DREAM": "ATL",
    "SEA": "SEA",
    "STORM": "SEA",
    "IND": "IND",
    "FEVER": "IND",
    "GSV": "GSV",
    "VALKYRIES": "GSV",
    "GOLDEN STATE": "GSV",
}

TEAM_DISPLAY_TO_ABBR = {
    "atlanta dream": "ATL",
    "chicago sky": "CHI",
    "connecticut sun": "CON",
    "dallas wings": "DAL",
    "golden state valkyries": "GSV",
    "indiana fever": "IND",
    "las vegas aces": "LVA",
    "los angeles sparks": "LAS",
    "minnesota lynx": "MIN",
    "new york liberty": "NYL",
    "phoenix mercury": "PHO",
    "seattle storm": "SEA",
    "washington mystics": "WSH",
}


def _norm_name(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _norm_team(v: object) -> str:
    s = str(v or "").strip().upper()
    if not s or s == "NAN":
        return ""
    return TEAM_ALIAS.get(s, s)


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


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


def _player_col(df: pd.DataFrame) -> str:
    for c in ("player", "player_name", "Player"):
        if c in df.columns:
            return c
    return "player"


def _build_players_by_name(df: pd.DataFrame) -> dict[str, dict]:
    """Max usage_pct per normalized player from step4b slate rows."""
    out: dict[str, dict] = {}
    pcol = _player_col(df)
    if pcol not in df.columns:
        return out
    for _, row in df.iterrows():
        nm = _norm_name(row.get(pcol, ""))
        if not nm:
            continue
        usg = _scale_pct(row.get("usage_pct"))
        ast = _scale_pct(row.get("ast_pct")) if "ast_pct" in df.columns else 0.0
        prev = out.get(nm, {})
        if usg >= float(prev.get("usage_pct", 0.0) or 0.0):
            out[nm] = {"usage_pct": usg, "ast_pct": ast}
    return out


def _load_star_tier_usage() -> dict[str, float]:
    """Fallback usage for injured players not on the prop slate."""
    if not STAR_TIERS_CSV.is_file():
        return {}
    try:
        st = pd.read_csv(STAR_TIERS_CSV, dtype=str).fillna("")
    except Exception:
        return {}
    out: dict[str, float] = {}
    for _, row in st.iterrows():
        nm = _norm_name(row.get("player_name", ""))
        if not nm:
            continue
        try:
            tier = int(float(row.get("star_tier", 3) or 3))
        except (TypeError, ValueError):
            tier = 3
        if tier == 1:
            out[nm] = 0.30
        elif tier == 2:
            out[nm] = 0.22
    return out


def _lookup_usage(pname: str, players_by_name: dict[str, dict], star_usage: dict[str, float]) -> tuple[float, float]:
    key = _norm_name(pname)
    prec = players_by_name.get(key, {})
    usg = float(prec.get("usage_pct", 0.0) or 0.0)
    ast = float(prec.get("ast_pct", 0.0) or 0.0)
    if usg <= 0.0 and key in star_usage:
        usg = float(star_usage[key])
    return usg, ast


def fetch_espn_injuries(
    players_by_name: dict[str, dict],
    star_usage: dict[str, float],
) -> dict[str, list[dict]]:
    """Return team_abbr -> list of {player_name, status, usage_pct, ast_pct}."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PropORACLE/1.0)"}
    try:
        time.sleep(0.5)
        r = requests.get(ESPN_INJURIES, headers=headers, timeout=TIMEOUT)
        if r.status_code != 200:
            return {}
        data = r.json()
    except Exception as exc:
        log.warning("ESPN WNBA injuries fetch failed: %s", exc)
        return {}

    team_out: dict[str, list[dict]] = {}
    for team_block in data.get("injuries", []) or []:
        team_obj = team_block.get("team") or {}
        team_name = str(team_block.get("displayName", team_obj.get("displayName", ""))).strip()
        team_abbr = _norm_team(team_obj.get("abbreviation", ""))
        if not team_abbr:
            team_abbr = TEAM_DISPLAY_TO_ABBR.get(team_name.lower(), "")
        if not team_abbr and team_name:
            team_abbr = _norm_team(team_name[:3])
        entries = []
        for inj in team_block.get("injuries", []) or []:
            athlete = inj.get("athlete") or {}
            pname = str(athlete.get("displayName", inj.get("name", ""))).strip()
            status = str(inj.get("status", inj.get("type", ""))).strip().upper()
            if not pname:
                continue
            if "OUT" not in status and status not in ("O", "OUT"):
                continue
            usg, ast = _lookup_usage(pname, players_by_name, star_usage)
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


def load_injuries_from_sidecar(
    csv_path: Path,
    players_by_name: dict[str, dict],
    star_usage: dict[str, float],
) -> dict[str, list[dict]]:
    if not csv_path.is_file():
        return {}
    try:
        inj = pd.read_csv(csv_path, dtype=str).fillna("")
    except Exception as exc:
        log.warning("injuries sidecar read failed: %s", exc)
        return {}
    if inj.empty:
        return {}
    team_col = "team" if "team" in inj.columns else ""
    player_col = "player" if "player" in inj.columns else ""
    status_col = next(
        (c for c in ("injury_status", "status", "injury_type") if c in inj.columns),
        "",
    )
    if not team_col or not player_col:
        return {}

    team_out: dict[str, list[dict]] = {}
    for _, row in inj.iterrows():
        team = _norm_team(row.get(team_col, ""))
        pname = str(row.get(player_col, "")).strip()
        status = str(row.get(status_col, "")).strip().upper() if status_col else "OUT"
        if not team or not pname:
            continue
        if "OUT" not in status and status not in ("O", "OUT", "INJURED RESERVE", "IR"):
            continue
        usg, ast = _lookup_usage(pname, players_by_name, star_usage)
        team_out.setdefault(team, []).append(
            {
                "player_name": pname,
                "status": status,
                "usage_pct": usg,
                "ast_pct": ast,
            }
        )
    return team_out


def _team_injury_flags(out_players: list[dict]) -> dict:
    vacuum = sum(float(p.get("usage_pct", 0.0) or 0.0) for p in out_players)
    star_out = any(float(p.get("usage_pct", 0.0) or 0.0) >= 0.25 for p in out_players)
    facilitator_out = any(
        float(p.get("ast_pct", 0.0) or 0.0) >= 0.20
        or float(p.get("usage_pct", 0.0) or 0.0) >= 0.25
        for p in out_players
    )
    return {
        "usage_vacuum": round(vacuum, 4),
        "team_star_out": bool(star_out),
        "key_facilitator_out": bool(facilitator_out),
    }


def ensure_injury_teams(
    *,
    refresh: bool,
    cache_hours: float,
    players_by_name: dict[str, dict],
    star_usage: dict[str, float],
    sidecar_path: Path | None,
) -> dict[str, list[dict]]:
    cache = _load_json(INJURY_CACHE)
    key = "latest"
    block = cache.get(key, {})
    if refresh or not block or _cache_stale(block, cache_hours):
        teams = fetch_espn_injuries(players_by_name, star_usage)
        if not teams and sidecar_path:
            log.info("ESPN injuries empty — using sidecar %s", sidecar_path)
            teams = load_injuries_from_sidecar(sidecar_path, players_by_name, star_usage)
        cache[key] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "teams": teams,
        }
        _save_json(INJURY_CACHE, cache)
        return teams
    teams = (block.get("teams") or {}) if isinstance(block, dict) else {}
    if not teams and sidecar_path:
        teams = load_injuries_from_sidecar(sidecar_path, players_by_name, star_usage)
    return teams


def _injury_boost_candidate(usage_vacuum: float, usage_tier: object) -> bool:
    ut = str(usage_tier or "").strip().lower()
    return float(usage_vacuum) >= 0.12 and ut in ("high", "star")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="step4_wnba_stats.csv")
    ap.add_argument("--output", default="step4_wnba_stats.csv")
    ap.add_argument("--date", default="", help="YYYY-MM-DD for injuries_wnba sidecar fallback")
    ap.add_argument("--cache-hours", type=float, default=CACHE_HOURS)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")
    players_by_name = _build_players_by_name(df)
    star_usage = _load_star_tier_usage()

    slate_date = str(args.date or "").strip()[:10]
    sidecar: Path | None = None
    if len(slate_date) >= 10:
        sidecar = _REPO_ROOT / "outputs" / slate_date / f"injuries_wnba_{slate_date}.csv"

    team_injuries = ensure_injury_teams(
        refresh=args.refresh,
        cache_hours=float(args.cache_hours),
        players_by_name=players_by_name,
        star_usage=star_usage,
        sidecar_path=sidecar,
    )

    for c in (
        "team_star_out",
        "key_facilitator_out",
        "injury_boost_candidate",
    ):
        if c not in df.columns:
            df[c] = False
    if "usage_vacuum" not in df.columns:
        df["usage_vacuum"] = np.nan

    teams_hit: set[str] = set()
    for idx, row in df.iterrows():
        team = _norm_team(row.get("team", ""))
        flags = {"usage_vacuum": 0.0, "team_star_out": False, "key_facilitator_out": False}
        if team and team in team_injuries:
            flags = _team_injury_flags(team_injuries[team])
            teams_hit.add(team)
        df.at[idx, "usage_vacuum"] = flags["usage_vacuum"]
        df.at[idx, "team_star_out"] = flags["team_star_out"]
        df.at[idx, "key_facilitator_out"] = flags["key_facilitator_out"]
        df.at[idx, "injury_boost_candidate"] = _injury_boost_candidate(
            flags["usage_vacuum"], row.get("usage_tier", "")
        )

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    try:
        copy_pipeline_output_to_dated_dirs(
            output_path=args.output,
            df=df,
            sport_dir_name="WNBA",
            repo_root=_REPO_ROOT,
        )
    except Exception as exc:
        log.warning("dated output copy skipped: %s", exc)

    print(f"WNBA injury context: {len(teams_hit)} teams, {len(df)} rows updated")
    if teams_hit:
        print(f"  teams with outs: {sorted(teams_hit)}")
    print(f"✅ Saved → {args.output}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ WNBA step4d failed. {type(e).__name__}: {e}")
        sys.exit(1)
