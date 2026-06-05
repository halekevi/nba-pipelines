#!/usr/bin/env python3
"""step4d_attach_injury_context.py — MLB ESPN injury / IL context per prop row."""

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
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import requests

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MLB_DIR = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs

log = logging.getLogger("mlb.step4d")

ESPN_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/injuries"
INJURY_CACHE = _MLB_DIR / "data" / "mlb_injury_cache.json"
TIMEOUT = 12
CACHE_HOURS = 2.0

TEAM_ABBREV_ALIAS = {"AZ": "ARI", "OAK": "ATH", "WSN": "WSH", "WAS": "WSH", "SDP": "SD", "SFG": "SF"}

MLB_TEAM_DISPLAY_TO_ABBR = {
    "arizona diamondbacks": "ARI",
    "athletics": "ATH",
    "atlanta braves": "ATL",
    "baltimore orioles": "BAL",
    "boston red sox": "BOS",
    "chicago cubs": "CHC",
    "chicago white sox": "CWS",
    "cincinnati reds": "CIN",
    "cleveland guardians": "CLE",
    "colorado rockies": "COL",
    "detroit tigers": "DET",
    "houston astros": "HOU",
    "kansas city royals": "KC",
    "los angeles angels": "LAA",
    "los angeles dodgers": "LAD",
    "miami marlins": "MIA",
    "milwaukee brewers": "MIL",
    "minnesota twins": "MIN",
    "new york mets": "NYM",
    "new york yankees": "NYY",
    "philadelphia phillies": "PHI",
    "pittsburgh pirates": "PIT",
    "san diego padres": "SD",
    "san francisco giants": "SF",
    "seattle mariners": "SEA",
    "st. louis cardinals": "STL",
    "st louis cardinals": "STL",
    "tampa bay rays": "TB",
    "texas rangers": "TEX",
    "toronto blue jays": "TOR",
    "washington nationals": "WSH",
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
    return TEAM_ABBREV_ALIAS.get(s, s)


def _team_from_display(display_name: str) -> str:
    key = str(display_name or "").strip().lower()
    if not key:
        return ""
    abbr = MLB_TEAM_DISPLAY_TO_ABBR.get(key, "")
    if abbr:
        return abbr
    nick = key.split()[-1].upper() if key.split() else ""
    nick_map = {
        "DIAMONDBACKS": "ARI", "ORIOLES": "BAL", "RED SOX": "BOS", "WHITE SOX": "CWS",
        "GUARDIANS": "CLE", "INDIANS": "CLE", "TIGERS": "DET", "ROYALS": "KC",
        "TWINS": "MIN", "YANKEES": "NYY", "ATHLETICS": "ATH", "MARINERS": "SEA",
        "RAYS": "TB", "RANGERS": "TEX", "BLUE JAYS": "TOR", "BRAVES": "ATL",
        "CUBS": "CHC", "REDS": "CIN", "ROCKIES": "COL", "MARLINS": "MIA",
        "ASTROS": "HOU", "DODGERS": "LAD", "BREWERS": "MIL", "NATIONALS": "WSH",
        "METS": "NYM", "PHILLIES": "PHI", "PIRATES": "PIT", "CARDINALS": "STL",
        "PADRES": "SD", "GIANTS": "SF", "ANGELS": "LAA",
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


def _is_on_il(status: str, type_abbrev: str, type_desc: str) -> bool:
    st = str(status or "").upper()
    ab = str(type_abbrev or "").upper()
    desc = str(type_desc or "").lower()
    if ab.startswith("IL") or "IL" in st:
        return True
    if st in ("O", "OUT") or ab in ("O", "OUT"):
        return True
    if "60-day" in desc or "15-day" in desc or "10-day" in desc:
        return True
    if "injured list" in desc and "day-to-day" not in desc:
        return True
    return False


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


def _flatten_espn_injuries() -> list[dict]:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PropORACLE/1.0)"}
    try:
        time.sleep(0.4)
        r = requests.get(ESPN_INJURIES, headers=headers, timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception as exc:
        log.warning("ESPN MLB injuries fetch failed: %s", exc)
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
            rows.append(
                {
                    "team": team_abbr,
                    "player_name": pname,
                    "player_norm": _norm_name(pname),
                    "injury_status": status,
                    "injury_type": abbrev,
                    "injury_type_desc": desc,
                    "player_on_il": _is_on_il(status, abbrev, desc),
                    "injury_date": inj_dt.isoformat() if inj_dt else "",
                }
            )
    return rows


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
            {
                "team": team,
                "player_name": pname,
                "player_norm": _norm_name(pname),
                "injury_status": status,
                "injury_type": abbrev,
                "injury_type_desc": desc,
                "player_on_il": _is_on_il(status, abbrev, desc),
                "injury_date": "",
            }
        )
    return rows


def ensure_injury_rows(
    *,
    refresh: bool,
    cache_hours: float,
    sidecar_path: Path | None,
) -> list[dict]:
    cache = _load_json(INJURY_CACHE)
    key = "latest"
    block = cache.get(key, {})
    if refresh or not block or _cache_stale(block, cache_hours):
        rows = _flatten_espn_injuries()
        if not rows and sidecar_path:
            log.info("ESPN injuries empty — using sidecar %s", sidecar_path)
            rows = load_injuries_from_sidecar(sidecar_path)
        cache[key] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "rows": rows,
        }
        _save_json(INJURY_CACHE, cache)
        return rows
    rows = block.get("rows") or []
    if not rows and sidecar_path:
        rows = load_injuries_from_sidecar(sidecar_path)
    return rows


def _build_player_lookup(rows: list[dict]) -> dict[str, dict]:
    """Best (most severe) injury per normalized player name."""
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
    return out


def _build_team_il_pitchers(rows: list[dict]) -> dict[str, set[str]]:
    team_il: dict[str, set[str]] = {}
    for row in rows:
        if not row.get("player_on_il"):
            continue
        team = _norm_team(row.get("team", ""))
        key = row.get("player_norm", "")
        if team and key:
            team_il.setdefault(team, set()).add(key)
    return team_il


def _player_col(df: pd.DataFrame) -> str:
    for c in ("player", "player_name", "Player"):
        if c in df.columns:
            return c
    return "player"


def _is_pitcher_row(row: pd.Series) -> bool:
    ptn = str(row.get("player_type_norm", "")).strip().lower()
    if ptn == "pitcher":
        return True
    role = str(row.get("pitcher_role", "")).strip().upper()
    if role in ("SP", "RP", "CLOSER"):
        return True
    pos = str(row.get("pos", "")).strip().lower()
    return pos in {"p", "sp", "rp", "cp", "lhp", "rhp", "pitcher"}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="step4_mlb_with_stats.csv")
    ap.add_argument("--output", default="step4_mlb_with_stats.csv")
    ap.add_argument("--date", default="", help="YYYY-MM-DD for injuries_mlb sidecar fallback")
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
        sidecar = _REPO_ROOT / "outputs" / slate_date / f"injuries_mlb_{slate_date}.csv"

    injury_rows = ensure_injury_rows(
        refresh=args.refresh,
        cache_hours=float(args.cache_hours),
        sidecar_path=sidecar,
    )
    player_lookup = _build_player_lookup(injury_rows)
    team_il = _build_team_il_pitchers(injury_rows)

    for c in (
        "player_on_il",
        "pitcher_scratched",
        "opp_starter_on_il",
    ):
        if c not in df.columns:
            df[c] = False
    for c in ("injury_status", "injury_type"):
        if c not in df.columns:
            df[c] = ""
    if "days_since_injury_report" not in df.columns:
        df["days_since_injury_report"] = np.nan

    pcol = _player_col(df)
    players_hit = 0
    opp_hit = 0
    for idx, row in df.iterrows():
        pname = _norm_name(row.get(pcol, ""))
        gdate = str(row.get("game_date", slate_date))[:10]
        team = _norm_team(row.get("team", ""))
        opp = _norm_team(row.get("opp_team", row.get("opp", "")))

        inj = player_lookup.get(pname)
        if inj:
            players_hit += 1
            df.at[idx, "injury_status"] = inj.get("injury_status", "")
            df.at[idx, "injury_type"] = inj.get("injury_type", "")
            df.at[idx, "player_on_il"] = bool(inj.get("player_on_il"))
            if _is_pitcher_row(row) and bool(inj.get("player_on_il")):
                df.at[idx, "pitcher_scratched"] = True
            if not bool(inj.get("player_on_il")):
                inj_dt = _parse_injury_date(inj.get("injury_date"))
                days = _days_since_report(inj_dt, gdate)
                if days is not None:
                    df.at[idx, "days_since_injury_report"] = days

        starter = _norm_name(row.get("opp_starter_name", ""))
        if starter and opp and starter in team_il.get(opp, set()):
            df.at[idx, "opp_starter_on_il"] = True
            opp_hit += 1

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    try:
        copy_pipeline_output_to_dated_dirs(
            output_path=args.output,
            df=df,
            sport_dir_name="MLB",
            repo_root=_REPO_ROOT,
        )
    except Exception as exc:
        log.warning("dated output copy skipped: %s", exc)

    il_rows = int(df["player_on_il"].astype(bool).sum()) if "player_on_il" in df.columns else 0
    print(
        f"MLB injury context: {len(injury_rows)} ESPN rows, "
        f"{players_hit} player matches, {il_rows} on IL, {opp_hit} opp_starter_on_il"
    )
    print(f"✅ Saved → {args.output}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ MLB step4d failed. {type(e).__name__}: {e}")
        sys.exit(1)
