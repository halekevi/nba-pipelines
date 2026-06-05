#!/usr/bin/env python3
"""step4d_attach_injury_context.py — NHL ESPN injury context per prop row."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests

_REPO_ROOT = Path(__file__).resolve().parents[3]
_NHL_DIR = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs

log = logging.getLogger("nhl.step4d")

ESPN_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries"
INJURY_CACHE = _NHL_DIR / "data" / "nhl_injury_cache.json"
TIMEOUT = 12
CACHE_HOURS = 2.0
KEY_OUT_PENALTY = -0.20

NHL_TEAM_ALIAS = {
    "TB": "TBL",
    "TBL": "TBL",
    "SJ": "SJS",
    "SJS": "SJS",
    "LA": "LAK",
    "LAK": "LAK",
    "NJ": "NJD",
    "NJD": "NJD",
    "NAS": "NSH",
    "NSH": "NSH",
    "MON": "MTL",
    "MTL": "MTL",
    "VEG": "VGK",
    "VGK": "VGK",
    "WAS": "WSH",
    "WSH": "WSH",
    "WSN": "WSH",
    "CAL": "CGY",
    "CGY": "CGY",
    "ARI": "UTA",
    "UTA": "UTA",
}

NHL_TEAM_DISPLAY_TO_ABBR = {
    "anaheim ducks": "ANA",
    "boston bruins": "BOS",
    "buffalo sabres": "BUF",
    "calgary flames": "CGY",
    "carolina hurricanes": "CAR",
    "chicago blackhawks": "CHI",
    "colorado avalanche": "COL",
    "columbus blue jackets": "CBJ",
    "dallas stars": "DAL",
    "detroit red wings": "DET",
    "edmonton oilers": "EDM",
    "florida panthers": "FLA",
    "los angeles kings": "LAK",
    "minnesota wild": "MIN",
    "montreal canadiens": "MTL",
    "montréal canadiens": "MTL",
    "nashville predators": "NSH",
    "new jersey devils": "NJD",
    "new york islanders": "NYI",
    "new york rangers": "NYR",
    "ottawa senators": "OTT",
    "philadelphia flyers": "PHI",
    "pittsburgh penguins": "PIT",
    "san jose sharks": "SJS",
    "seattle kraken": "SEA",
    "st. louis blues": "STL",
    "st louis blues": "STL",
    "tampa bay lightning": "TBL",
    "toronto maple leafs": "TOR",
    "utah hockey club": "UTA",
    "utah mammoth": "UTA",
    "vancouver canucks": "VAN",
    "vegas golden knights": "VGK",
    "washington capitals": "WSH",
    "winnipeg jets": "WPG",
}


def _norm_name(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _norm_team(v: object) -> str:
    s = str(v or "").strip().upper()
    if not s or s == "NAN":
        return ""
    return NHL_TEAM_ALIAS.get(s, s)


def _team_from_display(display_name: str) -> str:
    key = str(display_name or "").strip().lower()
    if not key:
        return ""
    abbr = NHL_TEAM_DISPLAY_TO_ABBR.get(key, "")
    if abbr:
        return abbr
    nick = key.split()[-1].upper() if key.split() else ""
    nick_map = {
        "DUCKS": "ANA", "BRUINS": "BOS", "SABRES": "BUF", "FLAMES": "CGY",
        "HURRICANES": "CAR", "BLACKHAWKS": "CHI", "AVALANCHE": "COL",
        "JACKETS": "CBJ", "STARS": "DAL", "WINGS": "DET", "OILERS": "EDM",
        "PANTHERS": "FLA", "KINGS": "LAK", "WILD": "MIN", "CANADIENS": "MTL",
        "PREDATORS": "NSH", "DEVILS": "NJD", "ISLANDERS": "NYI", "RANGERS": "NYR",
        "SENATORS": "OTT", "FLYERS": "PHI", "PENGUINS": "PIT", "SHARKS": "SJS",
        "KRAKEN": "SEA", "BLUES": "STL", "LIGHTNING": "TBL", "LEAFS": "TOR",
        "CANUCKS": "VAN", "KNIGHTS": "VGK", "CAPITALS": "WSH", "JETS": "WPG",
    }
    return nick_map.get(nick, "")


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(path)


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


def _parse_penalty(raw: object) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _is_on_il(status: str, type_abbrev: str) -> bool:
    st = str(status or "").strip()
    st_u = st.upper()
    ab = str(type_abbrev or "").strip().upper()
    if st_u in ("OUT", "O") or ab in ("OUT", "O"):
        return True
    if st_u in ("INJURED RESERVE", "IR") or ab == "IR":
        return True
    if "INJURED RESERVE" in st_u:
        return True
    return False


def _is_dtd(status: str, type_abbrev: str) -> bool:
    st = str(status or "").strip()
    st_u = st.upper()
    ab = str(type_abbrev or "").strip().upper()
    if st_u in ("DAY-TO-DAY", "DD") or ab == "DD":
        return True
    if "DAY-TO-DAY" in st_u or "DAY TO DAY" in st_u:
        return True
    return False


def _is_suspended(status: str) -> bool:
    return str(status or "").strip().upper() == "SUSPENSION"


def _parse_injury_date(raw: object) -> Optional[datetime]:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _days_since_report(injury_dt: Optional[datetime], game_date: str) -> Optional[int]:
    if injury_dt is None:
        return None
    gd = str(game_date or "")[:10]
    if len(gd) < 10:
        return None
    try:
        slate = datetime.strptime(gd, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    delta = slate.date() - injury_dt.date()
    return max(int(delta.days), 0)


def _row_from_parts(
    *,
    team: str,
    pname: str,
    status: str,
    abbrev: str,
    desc: str,
    rank_penalty: float,
    injury_date: str = "",
) -> dict:
    return {
        "team": team,
        "player_name": pname,
        "player_norm": _norm_name(pname),
        "injury_status": status,
        "injury_type": abbrev,
        "injury_type_desc": desc,
        "rank_penalty": rank_penalty,
        "player_on_il": _is_on_il(status, abbrev),
        "player_dtd": _is_dtd(status, abbrev),
        "player_suspended": _is_suspended(status),
        "injury_date": injury_date,
    }


def load_injuries_from_sidecar(csv_path: Path) -> list[dict]:
    if not csv_path.is_file():
        return []
    try:
        inj = pd.read_csv(csv_path, dtype=str).fillna("")
    except Exception as exc:
        log.warning("injuries sidecar read failed: %s", exc)
        return []
    if inj.empty:
        return []
    rows: list[dict] = []
    for _, row in inj.iterrows():
        team = _norm_team(row.get("team", ""))
        pname = str(row.get("player", "")).strip()
        if not team or not pname:
            continue
        status = str(row.get("injury_status", row.get("status", ""))).strip()
        abbrev = str(row.get("injury_type", "")).strip()
        desc = str(row.get("injury_type_desc", "")).strip()
        rows.append(
            _row_from_parts(
                team=team,
                pname=pname,
                status=status,
                abbrev=abbrev,
                desc=desc,
                rank_penalty=_parse_penalty(row.get("rank_penalty", 0)),
            )
        )
    return rows


def _flatten_espn_injuries() -> list[dict]:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PropORACLE/1.0)"}
    try:
        time.sleep(0.4)
        r = requests.get(ESPN_INJURIES, headers=headers, timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception as exc:
        log.warning("ESPN NHL injuries fetch failed: %s", exc)
        return []

    rows: list[dict] = []
    for block in data.get("injuries") or []:
        if not isinstance(block, dict):
            continue
        team_abbr = _team_from_display(block.get("displayName", ""))
        if not team_abbr:
            team_obj = block.get("team") or {}
            team_abbr = _norm_team(team_obj.get("abbreviation", ""))
        for inj in block.get("injuries") or []:
            if not isinstance(inj, dict):
                continue
            ath = inj.get("athlete") or {}
            pname = str(ath.get("displayName") or inj.get("name") or "").strip()
            if not pname or not team_abbr:
                continue
            typ = inj.get("type") or {}
            abbrev = str(typ.get("abbreviation") or "").strip()
            desc = str(typ.get("description") or "").strip()
            status = str(inj.get("status") or "").strip()
            inj_dt = _parse_injury_date(inj.get("date"))
            pen = _parse_penalty((inj.get("rank_penalty") if "rank_penalty" in inj else None))
            if pen == 0.0:
                # Approximate ESPN injury penalties when not in payload.
                if _is_on_il(status, abbrev):
                    pen = -0.45
                elif _is_dtd(status, abbrev):
                    pen = -0.15
                elif _is_suspended(status):
                    pen = -0.30
            rows.append(
                _row_from_parts(
                    team=team_abbr,
                    pname=pname,
                    status=status,
                    abbrev=abbrev,
                    desc=desc,
                    rank_penalty=pen,
                    injury_date=inj_dt.isoformat() if inj_dt else "",
                )
            )
    return rows


def ensure_injury_rows(
    *,
    refresh: bool,
    cache_hours: float,
    sidecar_path: Path | None,
) -> list[dict]:
    if sidecar_path and sidecar_path.is_file():
        rows = load_injuries_from_sidecar(sidecar_path)
        if rows:
            return rows
        log.warning("injuries sidecar empty or missing rows: %s", sidecar_path)

    cache = _load_json(INJURY_CACHE)
    key = "latest"
    block = cache.get(key, {})
    if refresh or not block or _cache_stale(block, cache_hours):
        rows = _flatten_espn_injuries()
        cache[key] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "rows": rows,
        }
        _save_json(INJURY_CACHE, cache)
        return rows
    return block.get("rows") or []


def _build_player_lookup(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        key = row.get("player_norm", "")
        if not key:
            continue
        prev = out.get(key)
        if prev is None:
            out[key] = row
            continue
        if bool(row.get("player_on_il")) and not bool(prev.get("player_on_il")):
            out[key] = row
        elif bool(row.get("player_dtd")) and not bool(prev.get("player_dtd")):
            out[key] = row
    return out


def _team_injury_flags(rows: list[dict]) -> dict[str, dict]:
    teams: dict[str, dict] = {}
    for row in rows:
        team = _norm_team(row.get("team", ""))
        if not team:
            continue
        flags = teams.setdefault(
            team,
            {"team_key_out": False, "team_dtd_count": 0},
        )
        if bool(row.get("player_dtd")):
            flags["team_dtd_count"] += 1
        if bool(row.get("player_on_il")) and float(row.get("rank_penalty", 0.0) or 0.0) <= KEY_OUT_PENALTY:
            flags["team_key_out"] = True
    return teams


def _player_col(df: pd.DataFrame) -> str:
    for c in ("player_name", "player", "Player"):
        if c in df.columns:
            return c
    return "player_name"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="step4_nhl_with_stats.csv")
    ap.add_argument("--output", default="step4_nhl_with_stats.csv")
    ap.add_argument("--date", default="", help="YYYY-MM-DD for injuries_nhl sidecar")
    ap.add_argument("--cache-hours", type=float, default=CACHE_HOURS)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")
    slate_date = str(args.date or "").strip()[:10]
    if len(slate_date) < 10 and "game_date" in df.columns:
        gd = df["game_date"].astype(str).str[:10]
        gd = gd[gd.str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)]
        if not gd.empty:
            slate_date = str(gd.mode().iloc[0])

    sidecar: Path | None = None
    if len(slate_date) >= 10:
        sidecar = _REPO_ROOT / "outputs" / slate_date / f"injuries_nhl_{slate_date}.csv"

    injury_rows = ensure_injury_rows(
        refresh=args.refresh,
        cache_hours=float(args.cache_hours),
        sidecar_path=sidecar,
    )
    if not injury_rows:
        log.warning("No NHL injury rows — flags left at defaults")

    player_lookup = _build_player_lookup(injury_rows)
    team_flags = _team_injury_flags(injury_rows)

    for c in ("player_on_il", "player_dtd", "player_suspended", "team_key_out"):
        if c not in df.columns:
            df[c] = False
    if "player_injury_status" not in df.columns:
        df["player_injury_status"] = ""
    if "team_dtd_count" not in df.columns:
        df["team_dtd_count"] = 0
    if "days_since_injury_report" not in df.columns:
        df["days_since_injury_report"] = np.nan

    pcol = _player_col(df)
    players_hit = 0
    teams_hit: set[str] = set()

    for idx, row in df.iterrows():
        team = _norm_team(row.get("team", ""))
        gdate = str(row.get("game_date", slate_date))[:10]
        if team and team in team_flags:
            teams_hit.add(team)
            tf = team_flags[team]
            df.at[idx, "team_key_out"] = bool(tf.get("team_key_out"))
            df.at[idx, "team_dtd_count"] = int(tf.get("team_dtd_count", 0))

        pname = _norm_name(row.get(pcol, ""))
        inj = player_lookup.get(pname)
        if inj:
            players_hit += 1
            df.at[idx, "player_injury_status"] = inj.get("injury_status", "")
            df.at[idx, "player_on_il"] = bool(inj.get("player_on_il"))
            df.at[idx, "player_dtd"] = bool(inj.get("player_dtd"))
            df.at[idx, "player_suspended"] = bool(inj.get("player_suspended"))
            if bool(inj.get("player_dtd")):
                inj_dt = _parse_injury_date(inj.get("injury_date"))
                days = _days_since_report(inj_dt, gdate)
                if days is not None:
                    df.at[idx, "days_since_injury_report"] = days

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    try:
        copy_pipeline_output_to_dated_dirs(
            output_path=args.output,
            df=df,
            sport_dir_name="NHL",
            repo_root=_REPO_ROOT,
        )
    except Exception as exc:
        log.warning("dated output copy skipped: %s", exc)

    il_rows = int(df["player_on_il"].astype(bool).sum()) if "player_on_il" in df.columns else 0
    dtd_rows = int(df["player_dtd"].astype(bool).sum()) if "player_dtd" in df.columns else 0
    print(
        f"NHL injury context: {len(injury_rows)} injury rows, "
        f"{len(teams_hit)} teams, {players_hit} player matches, "
        f"{il_rows} on IL, {dtd_rows} DTD"
    )
    print(f"✅ Saved → {args.output}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ NHL step4d failed. {type(e).__name__}: {e}")
        sys.exit(1)
