#!/usr/bin/env python3
"""
step4b_attach_lineup_context.py — MLB lineup, pitcher splits, park factors, line movement.

Attaches after step4_attach_player_stats_mlb.py:
  batting order, opposing starter, pitcher splits vs batter hand, park factors,
  optional line-movement columns from data/line_history.db.

Run:
  py -3.14 step4b_attach_lineup_context.py \\
    --input step4_mlb_with_stats.csv \\
    --output step4b_mlb_with_context.csv
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

_PROPORACLE_ROOT = Path(__file__).resolve().parents[3]
if str(_PROPORACLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROPORACLE_ROOT))

from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_ID_CACHE = _DATA_DIR / "mlb_player_id_cache.json"
_SPLITS_CACHE = _DATA_DIR / "mlb_pitcher_splits_cache.json"
_PARK_CSV = _DATA_DIR / "park_factors.csv"
_LINE_DB = _PROPORACLE_ROOT / "data" / "line_history.db"

MLB_API = "https://statsapi.mlb.com/api/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}
TIMEOUT = 10
SLEEP_S = 0.3

log = logging.getLogger("mlb.step4b")

TEAM_ABBREV_ALIAS = {"AZ": "ARI", "OAK": "ATH", "WSN": "WSH", "WAS": "WSH", "SDP": "SD", "SFG": "SF"}

TEAM_ABBREV_FROM_NAME = {
    "ANGELS": "LAA", "ORIOLES": "BAL", "RED SOX": "BOS", "WHITE SOX": "CWS",
    "GUARDIANS": "CLE", "INDIANS": "CLE", "TIGERS": "DET", "ROYALS": "KC",
    "TWINS": "MIN", "YANKEES": "NYY", "ATHLETICS": "ATH", "A'S": "ATH",
    "MARINERS": "SEA", "RAYS": "TB", "RANGERS": "TEX", "BLUE JAYS": "TOR",
    "DIAMONDBACKS": "ARI", "BRAVES": "ATL", "CUBS": "CHC", "REDS": "CIN",
    "ROCKIES": "COL", "MARLINS": "MIA", "ASTROS": "HOU", "DODGERS": "LAD",
    "BREWERS": "MIL", "NATIONALS": "WSH", "METS": "NYM", "PHILLIES": "PHI",
    "PIRATES": "PIT", "CARDINALS": "STL", "PADRES": "SD", "GIANTS": "SF",
}


def _get(url: str) -> Optional[dict]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.warning("MLB API request failed: %s | %s", url, exc)
        return None


def _load_json_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_json_cache(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(path)


def _norm_team(v: object) -> str:
    s = str(v or "").strip().upper()
    if not s or s == "NAN":
        return ""
    if s in TEAM_ABBREV_ALIAS:
        return TEAM_ABBREV_ALIAS[s]
    if len(s) <= 4 and s.isalpha():
        return s
    for key, abbr in TEAM_ABBREV_FROM_NAME.items():
        if key in s:
            return abbr
    return s


def _player_name(row: pd.Series) -> str:
    for col in ("player_name", "player"):
        v = str(row.get(col, "")).strip()
        if v and v.lower() != "nan":
            return v
    return ""


def resolve_player_id(name: str, id_cache: dict) -> Optional[int]:
    key = str(name).strip().lower()
    if not key:
        return None
    if key in id_cache:
        try:
            return int(id_cache[key])
        except (TypeError, ValueError):
            pass
    url = f"{MLB_API}/people/search?names={requests.utils.quote(name)}&sportIds=1"
    time.sleep(SLEEP_S)
    data = _get(url)
    if not data:
        return None
    people = data.get("people") or []
    if not people:
        return None
    pid = people[0].get("id")
    if pid is not None:
        id_cache[key] = int(pid)
    return int(pid) if pid is not None else None


def resolve_batter_hand(player_id: int, id_cache: dict) -> str:
    hand_key = f"hand_{player_id}"
    if hand_key in id_cache:
        return str(id_cache[hand_key]).upper()[:1]
    time.sleep(SLEEP_S)
    data = _get(f"{MLB_API}/people/{player_id}")
    if not data:
        return ""
    bat = ((data.get("people") or [{}])[0]).get("batSide") or {}
    code = str(bat.get("code", "")).upper()[:1]
    if code in ("L", "R", "S"):
        id_cache[hand_key] = code
    return code


def _pitcher_from_team_block(team_block: dict) -> Tuple[str, str, Optional[int]]:
    pp = team_block.get("probablePitcher") or {}
    if not pp:
        return "", "", None
    name = str(pp.get("fullName") or "").strip()
    hand = str((pp.get("pitchHand") or {}).get("code") or "").upper()[:1]
    pid = pp.get("id")
    return name, hand, int(pid) if pid is not None else None


def _batting_order_from_lineup(team_block: dict) -> List[int]:
    order: List[int] = []
    for slot in team_block.get("battingOrder") or []:
        if isinstance(slot, int):
            order.append(slot)
        elif isinstance(slot, dict) and slot.get("id"):
            order.append(int(slot["id"]))
    if order:
        return order
    for entry in team_block.get("lineup") or []:
        if isinstance(entry, dict) and entry.get("id"):
            order.append(int(entry["id"]))
    return order


def _parse_boxscore_orders(box: dict) -> Tuple[List[int], List[int], bool]:
    teams = (box.get("teams") or {})
    home_bo: List[int] = []
    away_bo: List[int] = []
    for side in ("home", "away"):
        t = teams.get(side) or {}
        bo = t.get("battingOrder") or []
        ids = [int(x) for x in bo if str(x).isdigit()]
        if side == "home":
            home_bo = ids
        else:
            away_bo = ids
    confirmed = bool(home_bo and away_bo)
    return home_bo, away_bo, confirmed


def _team_abbrev(team_obj: dict) -> str:
    t = team_obj or {}
    ab = str(t.get("abbreviation") or "").strip().upper()
    if ab:
        return ab
    fc = str(t.get("fileCode") or "").strip().upper()
    if fc:
        return fc
    tc = str(t.get("teamCode") or "").strip().upper()
    return tc[:3].upper() if tc else ""


def _pitcher_hand(pitcher_id: Optional[int], cached_hand: str, id_cache: dict) -> str:
    hand = str(cached_hand or "").upper()[:1]
    if hand in ("L", "R", "S") or not pitcher_id:
        return hand
    key = f"pitch_hand_{pitcher_id}"
    if key in id_cache:
        return str(id_cache[key]).upper()[:1]
    time.sleep(SLEEP_S)
    data = _get(f"{MLB_API}/people/{pitcher_id}")
    if data:
        people = data.get("people") or []
        if people:
            hand = str((people[0].get("pitchHand") or {}).get("code", "")).upper()[:1]
            if hand:
                id_cache[key] = hand
    return hand


def fetch_schedule_day(game_date: str) -> List[dict]:
    url = (
        f"{MLB_API}/schedule?sportId=1&date={game_date}"
        f"&hydrate=lineups,probablePitcher,team"
    )
    time.sleep(SLEEP_S)
    data = _get(url)
    if not data:
        return []
    games: List[dict] = []
    for day in data.get("dates") or []:
        games.extend(day.get("games") or [])
    return games


def fetch_boxscore(game_pk: int) -> Optional[dict]:
    time.sleep(SLEEP_S)
    return _get(f"{MLB_API}/game/{game_pk}/boxscore")


def build_day_context(game_date: str, id_cache: dict) -> Dict[str, dict]:
    """game_key (HOME|AWAY) -> context dict."""
    out: Dict[str, dict] = {}
    for g in fetch_schedule_day(game_date):
        gpk = g.get("gamePk")
        home = g.get("teams", {}).get("home") or {}
        away = g.get("teams", {}).get("away") or {}
        home_abbr = _team_abbrev(home.get("team") or {})
        away_abbr = _team_abbrev(away.get("team") or {})
        if not home_abbr or not away_abbr:
            continue

        home_bo = _batting_order_from_lineup(home)
        away_bo = _batting_order_from_lineup(away)
        lineup_confirmed = bool(home_bo and away_bo)

        if not lineup_confirmed and gpk:
            box = fetch_boxscore(int(gpk))
            if box:
                h2, a2, lineup_confirmed = _parse_boxscore_orders(box)
                if h2:
                    home_bo = h2
                if a2:
                    away_bo = a2

        h_name, h_hand, h_pid = _pitcher_from_team_block(home)
        a_name, a_hand, a_pid = _pitcher_from_team_block(away)
        h_hand = _pitcher_hand(h_pid, h_hand, id_cache)
        a_hand = _pitcher_hand(a_pid, a_hand, id_cache)

        def _order_map(order: List[int]) -> Dict[int, int]:
            return {pid: pos for pos, pid in enumerate(order, start=1)}

        ctx = {
            "home_abbr": home_abbr,
            "away_abbr": away_abbr,
            "home_bo": _order_map(home_bo),
            "away_bo": _order_map(away_bo),
            "home_starter": {"name": h_name, "hand": h_hand, "id": h_pid},
            "away_starter": {"name": a_name, "hand": a_hand, "id": a_pid},
            "lineup_confirmed": lineup_confirmed,
        }
        out[f"{home_abbr}|{away_abbr}"] = ctx
        out[f"{away_abbr}|{home_abbr}"] = ctx
    return out


def _splits_stale(entry: dict) -> bool:
    ts = entry.get("fetched_at")
    if not ts:
        return True
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt > timedelta(hours=24)
    except Exception:
        return True


def fetch_pitcher_splits(pitcher_id: int, season: str, cache: dict) -> dict:
    key = f"{pitcher_id}_{season}"
    if key in cache and not _splits_stale(cache[key]):
        return cache[key]

    url = (
        f"{MLB_API}/people/{pitcher_id}/stats"
        f"?stats=statSplits&group=pitching&season={season}&sitCodes=vl,vr"
    )
    time.sleep(SLEEP_S)
    data = _get(url)
    result = {
        "era_vs_left": None, "era_vs_right": None,
        "whip_vs_left": None, "whip_vs_right": None,
        "k9_vs_left": None, "k9_vs_right": None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    if data:
        for block in data.get("stats") or []:
            for split in block.get("splits") or []:
                sit = str(split.get("situation", {}).get("code", "")).lower()
                stat = split.get("stat") or {}
                era = stat.get("era")
                whip = stat.get("whip")
                ip = float(stat.get("inningsPitched") or 0)
                k = float(stat.get("strikeOuts") or 0)
                k9 = (k * 9.0 / ip) if ip > 0 else None
                if sit == "vl":
                    result["era_vs_left"] = era
                    result["whip_vs_left"] = whip
                    result["k9_vs_left"] = k9
                elif sit == "vr":
                    result["era_vs_right"] = era
                    result["whip_vs_right"] = whip
                    result["k9_vs_right"] = k9
    cache[key] = result
    return result


def _split_for_hand(splits: dict, batter_hand: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    hand = str(batter_hand or "R").upper()[:1]
    if hand == "L":
        return splits.get("era_vs_left"), splits.get("k9_vs_left"), splits.get("whip_vs_left")
    return splits.get("era_vs_right"), splits.get("k9_vs_right"), splits.get("whip_vs_right")


def _pitcher_advantage(era: Optional[float]) -> str:
    if era is None or (isinstance(era, float) and np.isnan(era)):
        return ""
    e = float(era)
    if e < 3.5:
        return "favor_pitcher"
    if e < 4.5:
        return "neutral"
    return "favor_batter"


def load_park_factors() -> pd.DataFrame:
    if not _PARK_CSV.exists():
        log.warning("park_factors.csv missing at %s", _PARK_CSV)
        return pd.DataFrame()
    return pd.read_csv(_PARK_CSV, encoding="utf-8-sig")


def _home_team_abbrev(row: pd.Series) -> str:
    team = _norm_team(row.get("team", ""))
    home = _norm_team(row.get("pp_home_team", ""))
    away = _norm_team(row.get("pp_away_team", ""))
    if home and team == home:
        return team
    if away and team != away:
        return away
    if home:
        return home
    return team


def attach_line_movement(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["line_moved_up"] = np.nan
    out["line_moved_down"] = np.nan
    out["line_move_delta"] = np.nan
    if not _LINE_DB.exists():
        return out
    try:
        with sqlite3.connect(_LINE_DB) as conn:
            hist = pd.read_sql_query(
                """
                SELECT COALESCE(player_name, player) AS player_name,
                       sport, fetched_at,
                       COALESCE(line_score, line) AS line_val,
                       prop_norm, game_date
                FROM line_history
                WHERE sport = 'MLB'
                """,
                conn,
            )
    except Exception as exc:
        log.warning("line_history read failed: %s", exc)
        return out
    if hist.empty:
        return out

    hist["fetched_at"] = pd.to_datetime(hist["fetched_at"], errors="coerce")
    hist["line_val"] = pd.to_numeric(hist["line_val"], errors="coerce")
    hist["game_date"] = hist["game_date"].astype(str).str[:10]
    hist["day"] = hist["fetched_at"].dt.date.astype(str)

    for idx, row in out.iterrows():
        pname = _player_name(row)
        gdate = str(row.get("game_date", ""))[:10]
        prop = str(row.get("prop_norm", "")).lower().strip()
        if not pname or not gdate:
            continue
        sub = hist[
            (hist["player_name"].str.lower() == pname.lower())
            & (hist["game_date"] == gdate)
            & (hist["prop_norm"].astype(str).str.lower() == prop)
        ]
        if sub.empty:
            continue
        days = sub["day"].nunique()
        if days < 30:
            continue
        today = sub[sub["day"] == sub["day"].max()]
        morning = sub.groupby("day").first().reset_index()
        if len(morning) < 2:
            continue
        line_now = pd.to_numeric(row.get("line_score", row.get("line")), errors="coerce")
        line_morn = morning.iloc[0]["line_val"]
        if pd.isna(line_now) or pd.isna(line_morn):
            continue
        delta = float(line_now) - float(line_morn)
        out.at[idx, "line_move_delta"] = delta
        out.at[idx, "line_moved_up"] = delta > 0
        out.at[idx, "line_moved_down"] = delta < 0
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="step4_mlb_with_stats.csv")
    ap.add_argument("--output", default="step4_mlb_with_stats.csv")
    ap.add_argument("--season", default="", help="Season year (default: from game_date)")
    args = ap.parse_args()

    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")
    id_cache = _load_json_cache(_ID_CACHE)
    splits_cache = _load_json_cache(_SPLITS_CACHE)

    parks = load_park_factors()
    park_by_abbrev = {}
    if not parks.empty and "team_abbrev" in parks.columns:
        park_by_abbrev = {
            str(r["team_abbrev"]).upper(): r
            for _, r in parks.iterrows()
        }

    new_cols = [
        "batting_order_pos", "opp_starter_name", "opp_starter_hand", "lineup_confirmed",
        "top_of_order", "bottom_of_order",
        "opp_pitcher_era_vs_batter_hand", "opp_pitcher_k9_vs_batter_hand", "opp_pitcher_whip_vs_batter_hand",
        "pitcher_advantage",
        "park_factor_overall", "park_factor_hr", "park_factor_so", "park_tier",
        "line_moved_up", "line_moved_down", "line_move_delta", "game_home_team",
    ]
    for c in new_cols:
        if c not in df.columns:
            df[c] = np.nan
    _str_cols = (
        "game_home_team", "opp_starter_name", "opp_starter_hand",
        "pitcher_advantage", "park_tier",
    )
    for c in _str_cols:
        if c in df.columns:
            df[c] = df[c].astype(object)
    for c in ("lineup_confirmed", "top_of_order", "bottom_of_order", "line_moved_up", "line_moved_down"):
        if c in df.columns:
            df[c] = df[c].astype(object)

    day_cache: Dict[str, Dict[str, dict]] = {}
    for idx, row in df.iterrows():
        gdate = str(row.get("game_date", ""))[:10]
        if not gdate or gdate == "nan":
            continue
        if gdate not in day_cache:
            day_cache[gdate] = build_day_context(gdate, id_cache)

        team = _norm_team(row.get("team", ""))
        opp = _norm_team(row.get("opp_team", ""))
        if not opp:
            opp = _norm_team(row.get("pp_away_team" if team == _norm_team(row.get("pp_home_team", "")) else "pp_home_team", ""))

        ctx = day_cache[gdate].get(f"{team}|{opp}")
        if not ctx:
            continue

        is_home = team == ctx["home_abbr"]
        bo_map = ctx["home_bo"] if is_home else ctx["away_bo"]
        opp_starter = ctx["away_starter"] if is_home else ctx["home_starter"]

        pname = _player_name(row)
        pid_raw = str(row.get("mlb_player_id", "")).strip()
        pid: Optional[int] = None
        if pid_raw and pid_raw.split("|")[0].isdigit():
            pid = int(pid_raw.split("|")[0])
        elif pname:
            pid = resolve_player_id(pname, id_cache)

        if pid and pid in bo_map:
            pos = bo_map[pid]
            df.at[idx, "batting_order_pos"] = pos
            df.at[idx, "top_of_order"] = pos <= 3
            df.at[idx, "bottom_of_order"] = pos >= 7

        df.at[idx, "game_home_team"] = ctx["home_abbr"]
        df.at[idx, "opp_starter_name"] = opp_starter.get("name", "")
        df.at[idx, "opp_starter_hand"] = opp_starter.get("hand", "")
        df.at[idx, "lineup_confirmed"] = ctx.get("lineup_confirmed", False)

        season = args.season or (gdate[:4] if len(gdate) >= 4 else "2026")
        ptype = str(row.get("player_type", "")).lower()
        if ptype != "pitcher" and opp_starter.get("id"):
            splits = fetch_pitcher_splits(int(opp_starter["id"]), season, splits_cache)
            bhand = ""
            if pid:
                bhand = resolve_batter_hand(pid, id_cache)
                if bhand == "S":
                    opp_hand = str(opp_starter.get("hand", "R")).upper()[:1]
                    bhand = "L" if opp_hand == "R" else "R"
            era, k9, whip = _split_for_hand(splits, bhand or "R")
            df.at[idx, "opp_pitcher_era_vs_batter_hand"] = era
            df.at[idx, "opp_pitcher_k9_vs_batter_hand"] = k9
            df.at[idx, "opp_pitcher_whip_vs_batter_hand"] = whip
            df.at[idx, "pitcher_advantage"] = _pitcher_advantage(era)

        home_abbr = str(ctx.get("home_abbr") or _home_team_abbrev(row))
        pr = park_by_abbrev.get(home_abbr)
        if pr is not None:
            df.at[idx, "park_factor_overall"] = pr.get("pf_overall")
            df.at[idx, "park_factor_hr"] = pr.get("pf_hr")
            df.at[idx, "park_factor_so"] = pr.get("pf_so")
            pf = float(pr.get("pf_overall") or 100)
            if pf >= 105:
                df.at[idx, "park_tier"] = "hitter"
            elif pf >= 96:
                df.at[idx, "park_tier"] = "neutral"
            else:
                df.at[idx, "park_tier"] = "pitcher"

    _save_json_cache(_ID_CACHE, id_cache)
    _save_json_cache(_SPLITS_CACHE, splits_cache)

    df = attach_line_movement(df)

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    copy_pipeline_output_to_dated_dirs(
        output_path=args.output,
        df=df,
        sport_dir_name="MLB",
        repo_root=_PROPORACLE_ROOT,
    )

    n_total = len(df)
    has_order = pd.to_numeric(df["batting_order_pos"], errors="coerce").notna().sum()
    has_starter = df["opp_starter_name"].astype(str).str.strip().ne("").sum()
    n_joined = int(max(has_order, has_starter))
    n_missing = n_total - int(
        (pd.to_numeric(df["batting_order_pos"], errors="coerce").notna()
         | df["opp_starter_name"].astype(str).str.strip().ne("")).sum()
    )
    print(f"Lineup attached: {n_joined}/{n_total} rows, {n_missing} missing")
    print(f"✅ Saved → {args.output}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ MLB step4b failed. {type(e).__name__}: {e}")
        sys.exit(1)
