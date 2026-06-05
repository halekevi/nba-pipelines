#!/usr/bin/env python3
"""
ESPN game-day injury reports for NBA, WNBA, CBB (men's college), NHL, and MLB.

Used by:
  - fetch_actuals.py — writes injuries_<league>_<date>.csv next to actuals
  - combined_ticket_grader.py — VOID legs when no boxscore stat but player on report
  - step7 rank scripts — rank_score penalty for questionable / out

Injuries are read from each event's summary JSON (same source as box scores).
"""

from __future__ import annotations

import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = Path(__file__).resolve().parent
for _p in (_REPO_ROOT, _SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pandas as pd
import requests

from player_name_norm import fold_player_name

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def strip_norm(s: str) -> str:
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


# Ticket / actuals abbreviations -> ESPN-style 2–3 letter codes (upper)
NBA_TEAM_CANON: Dict[str, str] = {
    "utah": "UTA",
    "utah jazz": "UTA",
    "phx": "PHX",
    "pho": "PHX",
    "phoenix": "PHX",
    "ny": "NYK",
    "nyk": "NYK",
    "knicks": "NYK",
    "bkn": "BKN",
    "brk": "BKN",
    "nets": "BKN",
    "nop": "NOP",
    "no": "NOP",
    "gs": "GSW",
    "gsw": "GSW",
    "sa": "SAS",
    "sas": "SAS",
    "cha": "CHA",
    "cho": "CHA",
    "was": "WAS",
    "wsh": "WAS",
}

NHL_TEAM_CANON: Dict[str, str] = {
    "tb": "TBL",
    "tbl": "TBL",
    "sj": "SJS",
    "sjs": "SJS",
    "la": "LAK",
    "lak": "LAK",
    "cal": "CGY",
    "cgy": "CGY",
}

MLB_TEAM_CANON: Dict[str, str] = {
    "az": "ARI",
    "ari": "ARI",
    "oak": "ATH",
    "ath": "ATH",
    "was": "WSH",
    "wsh": "WSH",
    "wsn": "WSH",
    "sdp": "SD",
    "sd": "SD",
    "sfg": "SF",
    "sf": "SF",
    "chw": "CWS",
    "cws": "CWS",
    "tb": "TB",
    "tbl": "TB",
    "kc": "KC",
    "kcr": "KC",
}


def canon_team_abbr(sport: str, abbr: str) -> str:
    """Normalize team token for cross-source joins (tickets, actuals, ESPN)."""
    if not abbr or str(abbr).lower() in ("nan", "none"):
        return ""
    key = strip_norm(abbr)
    st = (sport or "").upper()
    if st == "SOCCER":
        return str(abbr).strip().upper()[:12]
    if st == "TENNIS":
        return str(abbr).strip().upper()[:12]
    if st == "NHL":
        u = str(abbr).strip().upper()
        return NHL_TEAM_CANON.get(key, u)[:3]
    if st == "MLB":
        u = str(abbr).strip().upper()
        return MLB_TEAM_CANON.get(key, u)[:3]
    if st in ("CBB", "WCBB"):
        u = str(abbr).strip().upper()
        return u[:8] if len(u) > 3 else NBA_TEAM_CANON.get(key, u)
    u = str(abbr).strip().upper()
    if len(u) <= 3:
        return NBA_TEAM_CANON.get(key, u)
    return NBA_TEAM_CANON.get(key, u)[:3]


def _penalty_for_injury_type(abbrev: str, desc: str) -> float:
    a = (abbrev or "").upper().strip()
    d = strip_norm(desc or "")
    table = {
        "O": -0.45,
        "OUT": -0.45,
        "D": -0.30,
        "DOUBTFUL": -0.30,
        "Q": -0.12,
        "QUESTIONABLE": -0.12,
        "DD": -0.15,
        "GTD": -0.15,
        "DAYTODAY": -0.15,
        "GAMESIMDECISION": -0.15,
    }
    if a in table:
        return float(table[a])
    if "out" in d and "day" not in d:
        return -0.45
    if "doubtful" in d:
        return -0.30
    if "questionable" in d:
        return -0.12
    if "day-to-day" in d or "day to day" in d:
        return -0.15
    if "gtd" in d:
        return -0.15
    return -0.10


def flatten_injuries_from_summary(
    summary: dict,
    sport_label: str,
    event_id: str,
) -> List[dict]:
    rows: List[dict] = []
    for block in summary.get("injuries") or []:
        if not isinstance(block, dict):
            continue
        team_abbr = str((block.get("team") or {}).get("abbreviation", "") or "").strip()
        for inj in block.get("injuries") or []:
            if not isinstance(inj, dict):
                continue
            ath = inj.get("athlete") or {}
            name = str(ath.get("displayName") or ath.get("fullName") or "").strip()
            if not name:
                continue
            typ = inj.get("type") or {}
            abbrev = str(typ.get("abbreviation") or "").strip()
            desc = str(typ.get("description") or "").strip()
            status = str(inj.get("status") or "").strip()
            details = inj.get("details") or {}
            detail_txt = str((details.get("detail") or "")).strip()
            side = str((details.get("side") or "")).strip()
            pen = _penalty_for_injury_type(abbrev, desc)
            rows.append(
                {
                    "sport": sport_label,
                    "event_id": str(event_id),
                    "team": team_abbr,
                    "player": name,
                    "injury_status": status,
                    "injury_type": abbrev,
                    "injury_type_desc": desc,
                    "injury_detail": detail_txt,
                    "injury_side": side,
                    "rank_penalty": round(pen, 4),
                }
            )
    return rows


MLB_TEAM_DISPLAY_TO_ABBR: Dict[str, str] = {
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


def _mlb_team_abbr(display_name: str, team_obj: dict) -> str:
    key = strip_norm(display_name or "")
    if key in MLB_TEAM_DISPLAY_TO_ABBR:
        return MLB_TEAM_DISPLAY_TO_ABBR[key]
    abbr = str((team_obj or {}).get("abbreviation", "") or "").strip().upper()
    if abbr:
        return MLB_TEAM_CANON.get(strip_norm(abbr), abbr)[:3]
    return ""


def collect_mlb_injuries_league(date_str: str) -> pd.DataFrame:
    """League-wide MLB injury feed (not per-event scoreboard)."""
    url = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/injuries"
    r = requests.get(url, headers=HEADERS, timeout=28)
    r.raise_for_status()
    all_rows: List[dict] = []
    for block in r.json().get("injuries") or []:
        if not isinstance(block, dict):
            continue
        team_abbr = _mlb_team_abbr(str(block.get("displayName", "")), block.get("team") or {})
        for inj in block.get("injuries") or []:
            if not isinstance(inj, dict):
                continue
            ath = inj.get("athlete") or {}
            name = str(ath.get("displayName") or ath.get("fullName") or "").strip()
            if not name or not team_abbr:
                continue
            typ = inj.get("type") or {}
            abbrev = str(typ.get("abbreviation") or "").strip()
            desc = str(typ.get("description") or "").strip()
            status = str(inj.get("status") or "").strip()
            pen = _penalty_for_injury_type(abbrev, desc)
            all_rows.append(
                {
                    "sport": "MLB",
                    "event_id": "",
                    "team": team_abbr,
                    "player": name,
                    "injury_status": status,
                    "injury_type": abbrev,
                    "injury_type_desc": desc,
                    "injury_detail": str((inj.get("shortComment") or "")).strip(),
                    "injury_side": "",
                    "rank_penalty": round(pen, 4),
                }
            )
    if not all_rows:
        return pd.DataFrame(
            columns=[
                "date",
                "sport",
                "event_id",
                "team",
                "player",
                "injury_status",
                "injury_type",
                "injury_type_desc",
                "injury_detail",
                "injury_side",
                "rank_penalty",
            ]
        )
    df = pd.DataFrame(all_rows)
    df.insert(0, "date", date_str)
    df = df.drop_duplicates(subset=["sport", "team", "player", "injury_type"], keep="first")
    return df


def _scoreboard_url(sport_key: str, date_espn: str) -> str:
    if sport_key == "nhl":
        return f"https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates={date_espn}"
    if sport_key == "mlb":
        return f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates={date_espn}"
    return f"https://site.api.espn.com/apis/site/v2/sports/basketball/{sport_key}/scoreboard?dates={date_espn}"


def _summary_url(sport_key: str, event_id: str) -> str:
    if sport_key == "nhl":
        return f"https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/summary?event={event_id}"
    if sport_key == "mlb":
        return f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/summary?event={event_id}"
    return f"https://site.api.espn.com/apis/site/v2/sports/basketball/{sport_key}/summary?event={event_id}"


def collect_injuries_raw(sport_literal: str, date_str: str) -> pd.DataFrame:
    """
    sport_literal: NBA | WNBA | CBB | NHL
    date_str: YYYY-MM-DD
    """
    lit = sport_literal.upper().strip()
    if lit == "NBA":
        sk = "nba"
        label = "NBA"
    elif lit == "WNBA":
        sk = "wnba"
        label = "WNBA"
    elif lit == "CBB":
        sk = "mens-college-basketball"
        label = "CBB"
    elif lit == "WCBB":
        sk = "womens-college-basketball"
        label = "WCBB"
    elif lit == "NHL":
        sk = "nhl"
        label = "NHL"
    elif lit == "MLB":
        return collect_mlb_injuries_league(date_str)
    else:
        raise ValueError(f"Unsupported sport for injuries: {sport_literal}")

    date_espn = date_str.replace("-", "")
    url = _scoreboard_url(sk, date_espn)
    r = requests.get(url, headers=HEADERS, timeout=28)
    r.raise_for_status()
    events = r.json().get("events") or []
    all_rows: List[dict] = []
    for ev in events:
        eid = str(ev.get("id", "")).strip()
        if not eid:
            continue
        try:
            sr = requests.get(_summary_url(sk, eid), headers=HEADERS, timeout=28)
            sr.raise_for_status()
            summ = sr.json()
        except Exception:
            continue
        all_rows.extend(flatten_injuries_from_summary(summ, label, eid))
        time.sleep(0.12)
    if not all_rows:
        return pd.DataFrame(
            columns=[
                "date",
                "sport",
                "event_id",
                "team",
                "player",
                "injury_status",
                "injury_type",
                "injury_type_desc",
                "injury_detail",
                "injury_side",
                "rank_penalty",
            ]
        )
    df = pd.DataFrame(all_rows)
    df.insert(0, "date", date_str)
    df = df.drop_duplicates(subset=["sport", "event_id", "team", "player", "injury_type"], keep="first")
    return df


def write_injuries_for_date(sport_literal: str, date_str: str, output_path: str | Path) -> int:
    df = collect_injuries_raw(sport_literal, date_str)
    outp = Path(output_path)
    outp.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(outp, index=False)
    return len(df)


def injuries_csv_path_for_actuals(
    actuals_path: str | Path,
    sport_literal: str,
    *,
    date_hint: str | None = None,
) -> Path:
    """Derive injuries path from actuals_nba_YYYY-MM-DD.csv -> injuries_nba_*.csv.

    When ``actuals_path`` does not follow the usual ``actuals_<sport>_<date>.csv`` name,
    pass ``date_hint='YYYY-MM-DD'`` (e.g. fetch_actuals ``--date``) so the sidecar is still
    ``injuries_nba_<date>.csv`` next to the actuals file.
    """
    p = Path(actuals_path)
    stem = p.name
    m = {
        "NBA": ("actuals_nba_", "injuries_nba_"),
        "CBB": ("actuals_cbb_", "injuries_cbb_"),
        "WCBB": ("actuals_wcbb_", "injuries_wcbb_"),
        "NHL": ("actuals_nhl_", "injuries_nhl_"),
        "MLB": ("actuals_mlb_", "injuries_mlb_"),
        "SOCCER": ("actuals_soccer_", "injuries_soccer_"),
    }[sport_literal.upper()]
    if stem.startswith(m[0]):
        return p.parent / (m[1] + stem[len(m[0]) :])
    ds = (date_hint or "").strip()[:10]
    if re.match(r"^\d{4}-\d{2}-\d{2}$", ds):
        return p.parent / f"{m[1]}{ds}.csv"
    dm = re.search(r"(\d{4}-\d{2}-\d{2})", stem)
    if dm:
        return p.parent / f"{m[1]}{dm.group(1)}.csv"
    return p.parent / f"injuries_{sport_literal.lower()}_{stem}"


def load_injury_void_keys(csv_path: str | Path, sport_for_team: str) -> Set[Tuple[str, str]]:
    """(player_norm, team_canon) for players listed on ESPN injury report that date."""
    p = Path(csv_path)
    if not p.is_file():
        return set()
    df = pd.read_csv(p, dtype=str).fillna("")
    out: Set[Tuple[str, str]] = set()
    for _, r in df.iterrows():
        pl = fold_player_name(r.get("player", ""))
        tm = canon_team_abbr(sport_for_team, r.get("team", ""))
        if pl and tm:
            out.add((pl, tm))
    return out


def injury_rank_penalty_map(csv_path: str | Path, sport_for_team: str) -> Dict[Tuple[str, str], float]:
    """Best penalty per (player_norm, team_canon) — most negative wins."""
    p = Path(csv_path)
    if not p.is_file():
        return {}
    df = pd.read_csv(p, dtype=str).fillna("")
    agg: Dict[Tuple[str, str], float] = {}
    for _, r in df.iterrows():
        pl = fold_player_name(r.get("player", ""))
        tm = canon_team_abbr(sport_for_team, r.get("team", ""))
        if not pl or not tm:
            continue
        try:
            pen = float(r.get("rank_penalty", -0.1) or -0.1)
        except ValueError:
            pen = -0.10
        key = (pl, tm)
        agg[key] = min(agg.get(key, 0.0), pen)
    return agg


def penalty_series_for_slate(
    df: pd.DataFrame,
    player_col: str,
    team_col: str,
    sport: str,
    csv_path: str | Path,
) -> pd.Series:
    """Aligned rank_score penalties (negative floats) per slate row."""
    pmap = injury_rank_penalty_map(csv_path, sport)
    if not pmap:
        return pd.Series(0.0, index=df.index, dtype=float)
    penalties = []
    for _, r in df.iterrows():
        pl = fold_player_name(r.get(player_col, ""))
        tm = canon_team_abbr(sport, r.get(team_col, ""))
        penalties.append(float(pmap.get((pl, tm), 0.0)))
    return pd.Series(penalties, index=df.index, dtype=float)


def auto_injuries_csv_from_outputs(repo_root: Path, slate_date: str, sport_literal: str) -> Optional[Path]:
    """outputs/<date>/injuries_<league>_<date>.csv"""
    lit = sport_literal.upper()
    fname = {
        "NBA": "injuries_nba",
        "WNBA": "injuries_wnba",
        "CBB": "injuries_cbb",
        "WCBB": "injuries_wcbb",
        "NHL": "injuries_nhl",
        "MLB": "injuries_mlb",
        "SOCCER": "injuries_soccer",
    }.get(lit)
    if not fname:
        return None
    cand = repo_root / "outputs" / slate_date / f"{fname}_{slate_date}.csv"
    return cand if cand.is_file() else None


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Fetch ESPN injury report CSV for a slate date.")
    ap.add_argument("--sport", required=True, help="NBA | WNBA | CBB | WCBB | NHL | MLB")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument(
        "--output",
        default="",
        help="Output CSV path (default: outputs/<date>/injuries_<sport>_<date>.csv)",
    )
    args = ap.parse_args()
    lit = args.sport.upper().strip()
    slug = {
        "NBA": "nba",
        "WNBA": "wnba",
        "CBB": "cbb",
        "WCBB": "wcbb",
        "NHL": "nhl",
        "MLB": "mlb",
    }.get(lit)
    if not slug:
        raise SystemExit(f"Unsupported sport: {args.sport}")
    ds = str(args.date).strip()[:10]
    outp = Path(args.output) if str(args.output).strip() else (
        Path(__file__).resolve().parents[1] / "outputs" / ds / f"injuries_{slug}_{ds}.csv"
    )
    n = write_injuries_for_date(lit, ds, outp)
    print(f"Injury report saved -> {outp}  ({n} rows)")
