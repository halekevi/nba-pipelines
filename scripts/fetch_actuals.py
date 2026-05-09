#!/usr/bin/env python3
"""
fetch_actuals.py — pulls NBA/CBB box scores from ESPN and outputs
actuals CSV for the slate grader.

Usage:
  py -3 fetch_actuals.py --sport NBA --date 2026-02-20
  py -3 fetch_actuals.py --sport CBB --date 2026-02-20
  py -3 fetch_actuals.py --sport NBA   # defaults to yesterday
  py -3 fetch_actuals.py --sport NHL --date 2026-03-06
  py -3 fetch_actuals.py --sport Soccer --date 2026-03-06
  py -3 fetch_actuals.py --sport Soccer --date 2026-04-13 --soccer-window 1  # default; +1 day for next-day kickoffs
  py -3 fetch_actuals.py --sport WCBB --date 2026-04-02

Fixes vs previous version:
  - CBB scoreboard now paginates through ALL pages (was capped at 200 events)
  - Each actuals row now includes raw stat columns (PTS, REB, AST, 3PM, etc.)
    so the grader's stat_from_row() can look them up directly
  - 3-PT Made no longer voids as UNSUPPORTED_PROP
  - Double Double / Triple Double actuals (1.0/0.0) from PTS/REB/AST/STL/BLK >= 10 rules

Quarter / half splits: scripts/fetch_nba_period_actuals.py (--segment 1Q|2Q|3Q|4Q|1H|2H; --sport CBB for college).

"Quarters with 3+ / 5+ Points" are derived from ESPN regulation (Q1–Q4) play-by-play scoring
on the same summary/core endpoints as the box score (not available as a flat box aggregate).
"""

import argparse
import re
import sys
from collections import defaultdict
import requests
import pandas as pd
import time
from datetime import date, timedelta
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
for _p in (_SCRIPTS_DIR, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from player_name_norm import fold_player_name  # noqa: E402
from utils.player_name_utils import normalize_player_name  # noqa: E402

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# ── Build all prop rows from a stat_map ──────────────────────────────────────
def _double_triple_from_five(pts, reb, ast, stl, blk):
    """
    PrizePicks-style: 10+ in two of PTS/REB/AST/STL/BLK => double-double (1.0 else 0.0);
    10+ in three => triple-double. Missing stat treated as not qualifying (skip in count).
    """
    n = 0
    for x in (pts, reb, ast, stl, blk):
        if x is None:
            continue
        try:
            if float(x) >= 10.0:
                n += 1
        except (TypeError, ValueError):
            continue
    return (1.0 if n >= 2 else 0.0), (1.0 if n >= 3 else 0.0)


def parse_stats(player_name, t_abbr, stat_map):
    pts   = stat_map.get('PTS')
    reb   = stat_map.get('REB')
    ast   = stat_map.get('AST')
    blk   = stat_map.get('BLK')
    stl   = stat_map.get('STL')
    tov   = stat_map.get('TO')
    fgm   = stat_map.get('FGM')
    fga   = stat_map.get('FGA')
    fg3m  = stat_map.get('3PM')
    fg3a  = stat_map.get('3PA')
    fg2m  = stat_map.get('2PM')
    fg2a  = stat_map.get('2PA')
    ftm   = stat_map.get('FTM')
    fta   = stat_map.get('FTA')
    oreb  = stat_map.get('OREB')
    dreb  = stat_map.get('DREB')
    pf    = stat_map.get('PF')
    mins  = stat_map.get('MIN')

    if fg2m is None and fgm is not None and fg3m is not None:
        fg2m = fgm - fg3m
    if fg2a is None and fga is not None and fg3a is not None:
        fg2a = fga - fg3a

    # Combos
    pra = pts + reb + ast if all(x is not None for x in [pts, reb, ast]) else None
    pr  = pts + reb       if all(x is not None for x in [pts, reb])       else None
    pa  = pts + ast       if all(x is not None for x in [pts, ast])       else None
    ra  = reb + ast       if all(x is not None for x in [reb, ast])       else None
    bs  = blk + stl       if all(x is not None for x in [blk, stl])       else None

    dd_actual, td_actual = _double_triple_from_five(pts, reb, ast, stl, blk)

    # PrizePicks NBA Fantasy Score:
    # PTS*1.0 + REB*1.2 + AST*1.5 + STL*3.0 + BLK*3.0 - TOV*1.0
    # (No 3PM bonus, no double-double bonuses.)
    fs = None
    if all(x is not None for x in [pts, reb, ast, stl, blk, tov]):
        fs = (
            pts * 1.0
            + reb * 1.2
            + ast * 1.5
            + stl * 3.0
            + blk * 3.0
            - tov * 1.0
        )

    prop_map = {
        'Points':                 pts,
        'Rebounds':               reb,
        'Assists':                ast,
        'Blocked Shots':          blk,
        'Steals':                 stl,
        'Turnovers':              tov,
        'FG Made':                fgm,
        'FG Attempted':           fga,
        '3-PT Made':              fg3m,
        '3-PT Attempted':         fg3a,
        'Two Pointers Made':      fg2m,
        'Two Pointers Attempted': fg2a,
        'Free Throws Made':       ftm,
        'Free Throws Attempted':  fta,
        'Offensive Rebounds':     oreb,
        'Defensive Rebounds':     dreb,
        'Personal Fouls':         pf,
        'Fantasy Score':          fs,
        'Pts+Rebs+Asts':          pra,
        'PRA':                    pra,
        'Pts+Rebs':               pr,
        'Pts+Asts':               pa,
        'Rebs+Asts':              ra,
        'Blks+Stls':              bs,
        # Yes/no markets from same box score (O/U vs 0.5)
        'Double Double':          dd_actual,
        'Triple Double':          td_actual,
    }

    # ── Raw stat columns on every row so grader's stat_from_row() can look
    #    them up directly (fixes UNSUPPORTED_PROP on 3-PT Made / 3pm)
    raw_stats = {
        'PTS':  pts,  'REB':  reb,  'AST':  ast,
        'BLK':  blk,  'STL':  stl,  'TO':   tov,
        'FGM':  fgm,  'FGA':  fga,
        '3PM':  fg3m, '3PA':  fg3a,
        '3PT':  fg3m,               # alias so grader finds it either way
        'FTM':  ftm,  'FTA':  fta,
        '2PM':  fg2m, '2PA':  fg2a,
        'OREB': oreb, 'DREB': dreb,
        'PF':   pf,   'MIN':  mins,
    }

    rows = []
    for prop_type, actual in prop_map.items():
        if actual is not None:
            row = {
                'player':    player_name,
                'team':      t_abbr,
                'prop_type': prop_type,
                'actual':    round(float(actual), 1),
            }
            # attach raw stats — grader uses these for stat_from_row()
            for col, val in raw_stats.items():
                row[col] = round(float(val), 1) if val is not None else None
            rows.append(row)
    return rows


# ── Parse ESPN box score JSON ─────────────────────────────────────────────────
def _nba_box_dnp_status(athlete: dict, stats: list) -> str | None:
    """
    Return a human-readable DNP status for the injuries sidecar when the athlete
    was on the game roster but did not record box stats (coach's decision, injury DNP, etc.).
    """
    stats = stats or []
    reason = str(athlete.get("notPlayedReason") or athlete.get("reason") or "").strip()
    did = bool(athlete.get("didNotPlay"))

    for s in stats:
        if isinstance(s, str) and "DNP" in s.upper():
            t = " ".join(s.split())
            if t:
                return t
    if reason and "DNP" in reason.upper():
        return reason
    if did:
        return reason if reason else "DNP"
    return None


def parse_boxscore(
    box: dict,
    *,
    date_str: str = "",
    event_id: str = "",
    collect_dnp: bool = False,
) -> tuple[list, list[dict]]:
    """
    Returns (stat_rows, dnp_sidecar_rows). dnp_sidecar_rows are only populated when
    collect_dnp is True (NBA path) — merged into injuries_nba_<date>.csv after ESPN injury fetch.
    """
    rows: list = []
    dnp_rows: list[dict] = []
    seen_dnp: set[tuple[str, str, str]] = set()

    for bteam in box.get("boxscore", {}).get("players", []):
        t_abbr_raw = bteam.get("team", {}).get("abbreviation", "")
        t_abbr = ESPN_TO_SLATE_ABBREV.get(t_abbr_raw, t_abbr_raw)
        for stat_group in bteam.get("statistics", []):
            labels = stat_group.get("labels", [])
            for athlete in stat_group.get("athletes", []):
                ainfo = athlete.get("athlete") or {}
                player_raw = str(ainfo.get("displayName", "") or "").strip()
                stats = athlete.get("stats") or []

                emitted_stats = False
                if stats and not all(s in ("--", "", None) for s in stats):
                    stat_map = {}
                    raw_map = {}
                    for label, val in zip(labels, stats):
                        raw_map[label] = val
                        try:
                            stat_map[label] = float(val)
                        except (ValueError, TypeError):
                            pass

                    def _parse_made_att(x):
                        try:
                            s = str(x).strip()
                        except Exception:
                            return None, None
                        m2 = re.match(r"^(\d+)\s*[-/]\s*(\d+)$", s)
                        if not m2:
                            return None, None
                        return float(m2.group(1)), float(m2.group(2))

                    fg_m, fg_a = _parse_made_att(
                        raw_map.get("FG") or raw_map.get("FGM-A") or raw_map.get("FGMA"))
                    if fg_m is not None:
                        stat_map["FGM"] = fg_m
                        stat_map["FGA"] = fg_a

                    t3_m, t3_a = _parse_made_att(
                        raw_map.get("3PT") or raw_map.get("3FG") or raw_map.get("3PTM-A"))
                    if t3_m is not None:
                        stat_map["3PM"] = t3_m
                        stat_map["3PA"] = t3_a

                    ft_m, ft_a = _parse_made_att(raw_map.get("FT") or raw_map.get("FTM-A"))
                    if ft_m is not None:
                        stat_map["FTM"] = ft_m
                        stat_map["FTA"] = ft_a

                    tw_m, tw_a = _parse_made_att(
                        raw_map.get("2PT") or raw_map.get("2FG") or raw_map.get("2PTM-A"))
                    if tw_m is not None:
                        stat_map["2PM"] = tw_m
                        stat_map["2PA"] = tw_a

                    label_aliases = {
                        "3PM": ["3PM", "FG3M", "3FGM"],
                        "3PA": ["3PA", "FG3A", "3FGA"],
                        "FGM": ["FGM"],
                        "FGA": ["FGA"],
                        "FTM": ["FTM"],
                        "FTA": ["FTA"],
                        "PTS": ["PTS"],
                        "REB": ["REB", "TREB"],
                        "AST": ["AST"],
                        "BLK": ["BLK"],
                        "STL": ["STL"],
                        "TO": ["TO", "TOV"],
                        "PF": ["PF", "FOULS"],
                        "OREB": ["OREB"],
                        "DREB": ["DREB"],
                        "MIN": ["MIN"],
                    }
                    normalized = {}
                    for canon, aliases in label_aliases.items():
                        for alias in aliases:
                            if alias in stat_map:
                                normalized[canon] = stat_map[alias]
                                break

                    if normalized:
                        rows.extend(parse_stats(player_raw, t_abbr, normalized))
                        emitted_stats = True

                if emitted_stats:
                    continue

                dnp_status = _nba_box_dnp_status(athlete, stats)
                if collect_dnp and dnp_status and player_raw:
                    pl_norm = normalize_player_name(player_raw)
                    dk = (str(event_id or ""), str(t_abbr or "").upper(), fold_player_name(pl_norm))
                    if dk not in seen_dnp:
                        seen_dnp.add(dk)
                        st_out = (
                            dnp_status
                            if "DNP" in dnp_status.upper()
                            else f"DNP-{dnp_status.strip()}"
                        )
                        dnp_rows.append(
                            {
                                "date": date_str,
                                "sport": "NBA",
                                "event_id": str(event_id or ""),
                                "team": str(t_abbr or "").strip().upper(),
                                "player": pl_norm,
                                "injury_status": st_out,
                                "injury_type": "DNP",
                                "injury_type_desc": "From box score",
                                "injury_detail": st_out,
                                "injury_side": "",
                                "rank_penalty": -0.45,
                            }
                        )
    return rows, dnp_rows


def _nba_athlete_id_to_player_team(box: dict) -> dict[str, tuple[str, str]]:
    """Map ESPN athlete id -> (displayName, slate team abbr) from summary boxscore."""
    out: dict[str, tuple[str, str]] = {}
    for bteam in box.get("boxscore", {}).get("players", []):
        t_abbr_raw = bteam.get("team", {}).get("abbreviation", "")
        t_abbr = ESPN_TO_SLATE_ABBREV.get(t_abbr_raw, t_abbr_raw)
        for stat_group in bteam.get("statistics", []):
            for athlete in stat_group.get("athletes", []):
                a = athlete.get("athlete", {}) or {}
                aid = str(a.get("id", "")).strip()
                name = str(a.get("displayName", "")).strip()
                if aid and name:
                    out[aid] = (name, t_abbr)
    return out


def _nba_plays_from_summary(box: dict, event_id: str) -> list:
    """Plays array from game summary JSON, or ESPN core play-by-play XHR if missing."""
    gp = box.get("gamepackageJSON")
    if isinstance(gp, dict) and isinstance(gp.get("plays"), list):
        return gp["plays"]
    if isinstance(box.get("plays"), list):
        return box["plays"]
    eid = str(event_id or "").strip()
    if not eid:
        return []
    try:
        url = f"https://cdn.espn.com/core/nba/playbyplay?gameId={eid}&xhr=1"
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
        payload = r.json() or {}
        gp2 = payload.get("gamepackageJSON", {}) or {}
        pl = gp2.get("plays") or []
        return pl if isinstance(pl, list) else []
    except Exception:
        return []


def _nba_regulation_period_points_from_plays(plays: list) -> dict[str, dict[int, float]]:
    """
    Per athlete ESPN id: points scored in each regulation quarter (1–4) from PBP.
    Mirrors scoring attribution in fetch_nba_period_actuals._parse_game_period_stats.
    """
    acc: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))

    def add_pts(aid: str, pnum: int, pts: float) -> None:
        if not aid or pnum < 1 or pnum > 4 or pts <= 0:
            return
        acc[aid][pnum] += float(pts)

    for play in plays:
        if not isinstance(play, dict):
            continue
        pnum = int((play.get("period") or {}).get("number") or 0)
        if pnum < 1 or pnum > 4:
            continue

        text = str(play.get("text", "") or "")
        ltxt = text.lower()
        participants = [
            str((p.get("athlete") or {}).get("id", "")).strip()
            for p in (play.get("participants") or [])
            if isinstance(p, dict)
        ]
        participants = [p for p in participants if p]
        primary = participants[0] if participants else ""

        if "free throw" in ltxt:
            if not primary:
                continue
            ft_miss = "misses free throw" in ltxt or (
                " misses " in f" {ltxt} " and "free throw" in ltxt and "makes" not in ltxt
            )
            if ft_miss:
                continue
            ft_make = "makes free throw" in ltxt or (
                " makes " in f" {ltxt} " and "free throw" in ltxt
            )
            if ft_make:
                try:
                    pts_ft = float(play.get("scoreValue"))
                except (TypeError, ValueError):
                    pts_ft = 1.0
                if pts_ft <= 0:
                    pts_ft = 1.0
                add_pts(primary, pnum, pts_ft)
            continue

        made = " makes " in f" {ltxt} "
        missed = " misses " in f" {ltxt} "
        if not (made or missed):
            continue
        if "free throw" in ltxt:
            continue

        is_shot = bool(play.get("shootingPlay")) or any(
            k in ltxt for k in ("jumper", "jumpshot", "layup", "dunk", "hook shot", "tip shot", "shot")
        )
        if not is_shot or not primary:
            continue

        if not made:
            continue

        is_three = ("three point" in ltxt) or ("3-point" in ltxt)
        score_val = play.get("scoreValue")
        try:
            pts = float(score_val)
        except (TypeError, ValueError):
            pts = 3.0 if is_three else 2.0
        add_pts(primary, pnum, pts)

    return acc


def parse_nba_quarter_milestone_rows(box: dict, event_id: str = "") -> list[dict]:
    """
    Build actuals rows for PrizePicks-style regulation quarter scoring props using ESPN PBP.
    Emits 0.0–4.0 for every player listed in the boxscore roster.
    """
    athlete_meta = _nba_athlete_id_to_player_team(box)
    if not athlete_meta:
        return []

    plays = _nba_plays_from_summary(box, event_id)
    acc = _nba_regulation_period_points_from_plays(plays)

    rows: list[dict] = []
    for aid, (player_name, team_abbr) in athlete_meta.items():
        per = acc.get(aid, {})
        n_ge_3 = sum(1 for q in (1, 2, 3, 4) if float(per.get(q, 0.0)) >= 3.0)
        n_ge_5 = sum(1 for q in (1, 2, 3, 4) if float(per.get(q, 0.0)) >= 5.0)
        pl_norm = normalize_player_name(player_name)
        for prop_type, val in (
            ("Quarters with 3+ Points", float(n_ge_3)),
            ("Quarters with 5+ Points", float(n_ge_5)),
        ):
            rows.append(
                {
                    "player": pl_norm,
                    "team": team_abbr,
                    "prop_type": prop_type,
                    "actual": round(val, 1),
                }
            )
    return rows


def merge_nba_box_dnp_into_injuries_csv(
    dnp_rows: list[dict],
    date_str: str,
    actuals_output: str | Path,
) -> int:
    """
    Append box-score DNP rows to injuries_nba_<date>.csv. Does not remove or overwrite
    existing ESPN injury-report rows; skips when (fold_player_name(player), team) already present.
    """
    if not dnp_rows:
        return 0
    try:
        from espn_injuries import injuries_csv_path_for_actuals
    except ImportError:
        return 0

    inj_path = injuries_csv_path_for_actuals(actuals_output, "NBA", date_hint=date_str)
    inj_path.parent.mkdir(parents=True, exist_ok=True)

    if inj_path.is_file():
        try:
            existing = pd.read_csv(inj_path, dtype=str).fillna("")
        except Exception:
            existing = pd.DataFrame()
    else:
        existing = pd.DataFrame()

    cols = [
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
    for c in cols:
        if c not in existing.columns:
            existing[c] = ""

    occupied: set[tuple[str, str]] = set()
    for _, r in existing.iterrows():
        if str(r.get("sport", "")).strip().upper() != "NBA":
            continue
        pl = fold_player_name(str(r.get("player", "") or ""))
        tm = str(r.get("team", "") or "").strip().upper()
        if pl and tm:
            occupied.add((pl, tm))

    appended = 0
    new_frames = []
    for rec in dnp_rows:
        pl = fold_player_name(str(rec.get("player", "") or ""))
        tm = str(rec.get("team", "") or "").strip().upper()
        if not pl or not tm or (pl, tm) in occupied:
            continue
        occupied.add((pl, tm))
        rec = dict(rec)
        rec["date"] = date_str
        rec["sport"] = "NBA"
        new_frames.append(pd.DataFrame([rec]))
        appended += 1

    if not new_frames:
        return 0
    merged = pd.concat([existing, *new_frames], ignore_index=True)
    merged.to_csv(inj_path, index=False)
    return appended


# ── Conference group IDs for ESPN CBB scoreboard ─────────────────────────────
# ESPN's main scoreboard returns only ~15 featured games.
# Fetching by conference group returns all games per conference.
# Some conferences have multiple IDs (primary + alternate) — include both.
CBB_CONF_GROUPS = [
    # Power conferences (primary + alternate IDs for full coverage)
    (2,    "ACC"),
    (4,    "Big East"),
    (8,    "SEC"),
    (80,   "SEC-alt"),       # catches remaining SEC games ESPN omits from group 8
    (9009, "SEC-full"),      # full SEC scoreboard (all 14 games)
    (9510, "SEC-expanded"),  # another SEC variant ESPN uses
    (9,    "Big Ten"),
    (22,   "Big Ten-alt"),   # UCLA/Oregon/Washington now here after realignment
    (10,   "Pac-12"),
    (8570, "Big 12"),
    # Mid-majors
    (24,   "Atlantic 10"),
    (25,   "American"),
    (26,   "WCC"),
    (27,   "WCC-alt"),
    (28,   "Mountain West-alt"),
    (29,   "Mountain West"),
    (36,   "Conference USA"),
    (37,   "Sun Belt"),
    (40,   "Horizon League"),
    (44,   "Missouri Valley"),
    (45,   "Summit League"),
    (46,   "Big West"),
    (48,   "Patriot League"),
    (49,   "CAA"),
    (50,   "Metro Atlantic"),
    (56,   "Northeast"),
    (59,   "SWAC"),
    (60,   "MEAC"),
    (62,   "Southern"),
    # Catch-all — featured/top games, catches any stragglers
    (None, "Featured"),
]

# ── ESPN team abbreviation → slate abbreviation normalization ─────────────────
# ESPN sometimes uses different abbreviations than PrizePicks/slate pipelines.
# ── NHL ESPN URL paths ────────────────────────────────────────────────────────
NHL_SCOREBOARD_URL  = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates={date_espn}"
NHL_SUMMARY_URL     = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/summary?event={event_id}"
NHL_API_SCHEDULE_URL = "https://api-web.nhle.com/v1/schedule/{date_iso}"
NHL_API_BOX_URL      = "https://api-web.nhle.com/v1/gamecenter/{game_id}/boxscore"

# ── Soccer ESPN URL paths ─────────────────────────────────────────────────────
SOCCER_LEAGUES = [
    ("eng.1",  "EPL"),
    ("eng.2",  "EFL Championship"),
    ("esp.1",  "La Liga"),
    ("ger.1",  "Bundesliga"),
    ("ita.1",  "Serie A"),
    ("fra.1",  "Ligue 1"),
    ("usa.1",  "MLS"),
    ("usa.nwsl", "NWSL"),
    ("arg.1",  "Argentina"),
    ("mex.1",  "Liga MX"),
    # sau.1 (Saudi Pro League): ESPN scoreboard/summary often 400 — skip; no reliable boxscore.
    ("aus.1",  "A-League"),
    # CONMEBOL club competitions — needed for South American clubs appearing on PP boards.
    ("conmebol.libertadores", "CONMEBOL Libertadores"),
    ("conmebol.sudamericana", "CONMEBOL Sudamericana"),
    ("conmebol.recopa", "CONMEBOL Recopa"),
    # Major domestic cups with frequent overlaps vs PP soccer boards.
    ("arg.copa", "Copa Argentina"),
    ("bra.copa_do_brazil", "Copa do Brasil"),
    ("uefa.champions", "UCL"),
    ("uefa.europa",    "UEL"),
]
SOCCER_SCOREBOARD_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={date_espn}"
SOCCER_SUMMARY_BASE    = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/summary?event={event_id}"

ESPN_TO_SLATE_ABBREV = {
    "NCSU": "NCST",   # NC State (ESPN=NCSU, slate=NCST)
    "TA&M": "TXAM",   # Texas A&M
    "MIZ":  "MIZZ",   # Missouri
    "OLEM": "MISS",   # Ole Miss alternate
    "NWST": "NW",     # Northwestern
    "OU":   "OKLA",   # Oklahoma
    "SC":   "SCAR",   # South Carolina (ESPN=SC, slate=SCAR)
    "BOIS": "BSU",    # Boise State (ESPN=BOIS, slate=BSU)
}


def _fetch_scoreboard_page(sport_path, date_espn, group_id=None, page=1):
    """Single scoreboard page fetch. Returns (events, page_count)."""
    base = (f"https://site.api.espn.com/apis/site/v2/sports/basketball"
            f"/{sport_path}/scoreboard?dates={date_espn}&limit=100&page={page}")
    if group_id:
        base += f"&groups={group_id}"
    try:
        r = requests.get(base, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        return data.get('events', []), data.get('pageCount', 1)
    except Exception as e:
        print(f"    WARNING: fetch failed (group={group_id}, page={page}): {e}")
        return [], 1


def fetch_events_for_date(sport_path, date_str, is_cbb=False):
    """
    Fetch ALL completed events for a date.
    - NBA: single scoreboard call (ESPN indexes all NBA games reliably).
    - CBB: fetch conference-by-conference so we get all 80+ games,
      not just the ~15 ESPN features on the main scoreboard.
    """
    date_espn  = date_str.replace('-', '')
    all_events = []
    seen_ids   = set()

    def _add_events(events):
        new = 0
        for e in events:
            eid = str(e.get('id', '')).strip()
            if eid and eid not in seen_ids:
                seen_ids.add(eid)
                all_events.append(e)
                new += 1
        return new

    if not is_cbb:
        # NBA — single paginated fetch
        page = 1
        while True:
            events, page_count = _fetch_scoreboard_page(sport_path, date_espn, page=page)
            new = _add_events(events)
            print(f"    Page {page}/{page_count}: {len(events)} events ({new} new)")
            if page >= page_count or not events or new == 0:
                break
            page += 1
            time.sleep(0.15)
    else:
        # CBB — fetch each conference group separately
        for group_id, conf_name in CBB_CONF_GROUPS:
            page = 1
            conf_new = 0
            while True:
                events, page_count = _fetch_scoreboard_page(
                    sport_path, date_espn, group_id=group_id, page=page)
                new = _add_events(events)
                conf_new += new
                if page >= page_count or not events or new == 0:
                    break
                page += 1
                time.sleep(0.15)
            if conf_new > 0:
                print(f"    {conf_name} (group={group_id}): +{conf_new} games "
                      f"— running total: {len(all_events)}")
            time.sleep(0.2)

    return all_events


# ── NBA duo combo rows (PlayerA + PlayerB) ───────────────────────────────────
_NBA_COMBO_SUM_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Pts+Rebs+Asts", ("Points", "Rebounds", "Assists")),
    ("PRA", ("Points", "Rebounds", "Assists")),
    ("Pts+Rebs", ("Points", "Rebounds")),
    ("Pts+Asts", ("Points", "Assists")),
    ("Rebs+Asts", ("Rebounds", "Assists")),
    ("Blks+Stls", ("Blocked Shots", "Steals")),
)


def _nba_combo_component_sum(team: str, player: str, comps: tuple[str, ...], base: pd.DataFrame) -> float | None:
    total = 0.0
    for cp in comps:
        sel = base.loc[
            (base["team"] == team) & (base["player"] == player) & (base["prop_type"] == cp),
            "actual",
        ]
        if sel.empty:
            return None
        v = pd.to_numeric(sel.iloc[0], errors="coerce")
        if pd.isna(v):
            return None
        total += float(v)
    return total


def append_nba_duo_combo_actual_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add synthetic actuals rows with player = 'A + B' / 'B + A' so slate combo strings
    resolve in the grader lookup. If either player lacks a component stat, no row is added.
    """
    if df.empty or not {"player", "team", "prop_type", "actual"}.issubset(df.columns):
        return df
    extra: list[dict] = []
    for team in df["team"].dropna().astype(str).unique():
        players = sorted(df.loc[df["team"] == team, "player"].dropna().astype(str).unique().tolist())
        for i, pa in enumerate(players):
            for pb in players[i + 1 :]:
                for combo_label, comps in _NBA_COMBO_SUM_SPECS:
                    sa = _nba_combo_component_sum(team, pa, comps, df)
                    sb = _nba_combo_component_sum(team, pb, comps, df)
                    if sa is None or sb is None:
                        continue
                    val = round(float(sa + sb), 1)
                    extra.append({"player": f"{pa} + {pb}", "team": team, "prop_type": combo_label, "actual": val})
                    extra.append({"player": f"{pb} + {pa}", "team": team, "prop_type": combo_label, "actual": val})
    if not extra:
        return df
    tail = pd.DataFrame(extra)
    out = pd.concat([df, tail], ignore_index=True)
    out["actual"] = pd.to_numeric(out["actual"], errors="coerce")
    return out


# ── Main sport fetch ──────────────────────────────────────────────────────────
def fetch_sport(sport_path, date_str, window=2, nba_extra_days: int = 0):
    from datetime import datetime as _dt, timedelta as _td

    is_college = "college" in sport_path
    target_dt = _dt.strptime(date_str, "%Y-%m-%d")

    # Men's CBB: optionally fetch a multi-day window (-window to +window) to catch games
    # ESPN indexes under adjacent dates. Pass window=0 for single-date fetch (faster,
    # recommended for same-day grading runs where the slate date is already known).
    if is_college and sport_path == "mens-college-basketball" and window > 0:
        fetch_dates = [
            (target_dt + _td(days=d)).strftime("%Y-%m-%d")
            for d in range(-window, window + 1)
        ]
        print(f"CBB mode: conference-by-conference fetch across {window*2+1}-day window "
              f"({fetch_dates[0]} → {fetch_dates[-1]})")
    elif sport_path in ("nba", "wnba") and nba_extra_days > 0:
        # ESPN often splits the same US "slate night" across two calendar dates on the
        # scoreboard API (e.g. 2026-04-24 returns 3 games while 2026-04-25 holds the
        # rest). Merge completed games from --date through +nba_extra_days.
        fetch_dates = [
            (target_dt + _td(days=d)).strftime("%Y-%m-%d")
            for d in range(0, nba_extra_days + 1)
        ]
        lab = "NBA" if sport_path == "nba" else "WNBA"
        print(
            f"{lab} mode: fetching scoreboards for {', '.join(fetch_dates)} "
            f"(primary {date_str} + {nba_extra_days} following day(s))"
        )
    else:
        fetch_dates = [date_str]
        if is_college:
            lab = "CBB" if sport_path == "mens-college-basketball" else "WCBB"
            print(f"{lab} mode: single-date fetch for {date_str} (--window 0)")

    seen_ids = set()
    events   = []
    for d in fetch_dates:
        print(f"\nFetching scoreboard for {d} ...")
        # Men's CBB needs per-conference scoreboard pages; women's uses main paginated board.
        cbb_split = sport_path == "mens-college-basketball"
        day_events = fetch_events_for_date(sport_path, d, is_cbb=cbb_split)
        new = 0
        for e in day_events:
            eid = str(e.get('id', '')).strip()
            if eid and eid not in seen_ids:
                seen_ids.add(eid)
                events.append(e)
                new += 1
        print(f"  Day total: {len(day_events)} events ({new} new unique)")

    print(f"\n  Grand total unique events to process: {len(events)}")

    all_rows = []
    all_box_dnp: list[dict] = []
    graded_event_ids = set()

    for event in events:
        event_id  = event.get('id', '')
        game_name = event.get('shortName', event.get('name', ''))

        status_type = event.get('status', {}).get('type', {})
        state       = status_type.get('state', '')
        completed   = status_type.get('completed', False)

        if state != 'post' and not completed:
            print(f"  Skipping {game_name} — not final (state={state})")
            continue

        if event_id in graded_event_ids:
            continue
        graded_event_ids.add(event_id)

        print(f"  Grading: {game_name}")
        box_url = (
            f"https://site.api.espn.com/apis/site/v2/sports/basketball"
            f"/{sport_path}/summary?event={event_id}"
        )
        try:
            br = requests.get(box_url, headers=HEADERS, timeout=20)
            br.raise_for_status()
            box = br.json()
            time.sleep(0.25)
        except Exception as e:
            print(f"    ERROR fetching box score: {e}")
            continue

        stat_rows, dnp_part = parse_boxscore(
            box,
            date_str=date_str,
            event_id=str(event_id),
            collect_dnp=(sport_path in ("nba", "wnba")),
        )
        all_rows.extend(stat_rows)
        if sport_path == "nba":
            qmile = parse_nba_quarter_milestone_rows(box, event_id=str(event_id))
            all_rows.extend(qmile)
            print(f"    -> {len(stat_rows)} stat rows (+{len(qmile)} quarter-milestone)")
        else:
            print(f"    -> {len(stat_rows)} stat rows")
        all_box_dnp.extend(dnp_part)

    if not all_rows:
        # Distinguish "no games on slate" vs "games not final yet" for downstream stubs.
        reason = "no_games" if len(events) == 0 else "pending"
        return pd.DataFrame(), reason, all_box_dnp

    df = pd.DataFrame(all_rows)
    if "player" in df.columns:
        df["player"] = df["player"].astype(str).map(normalize_player_name)

    # Deduplicate per player+team+prop_type — keep highest actual value
    # (guards against a player appearing on multiple date pages)
    df["actual"] = pd.to_numeric(df["actual"], errors="coerce")
    df = (
        df.sort_values("actual", ascending=False)
        .drop_duplicates(subset=["player", "team", "prop_type"], keep="first")
    )

    if sport_path == "nba" and len(df):
        df = append_nba_duo_combo_actual_rows(df)
        df["actual"] = pd.to_numeric(df["actual"], errors="coerce")
        df = (
            df.sort_values("actual", ascending=False)
            .drop_duplicates(subset=["player", "team", "prop_type"], keep="first")
        )

    print(f"\n  Total: {len(df)} player-prop actuals across {len(graded_event_ids)} games")
    return df, "ok", all_box_dnp



# ── Parse NHL ESPN box score ──────────────────────────────────────────────────
NHL_STAT_MAP = {
    "shots_on_goal": ["SOG", "SHOTSONGOAL", "S", "SHOTS", "SHOT"],
    "goals":         ["G", "GOALS"],
    "assists":       ["A", "ASSISTS"],
    "points":        ["PTS", "P", "POINTS"],
    "hits":          ["HIT", "HITS"],
    "blocked_shots": ["BS", "BKS", "BLOCKED", "BLK", "BLOCKS"],
    "pim":           ["PIM", "PENALTYMINUTES"],
    "plus_minus":    ["PLUSMINUS", "+/-", "PM"],
    "power_play_points": ["PPP", "PPPTS", "PP", "POWERPLAYPOINTS", "PPA", "PPG"],
    "faceoffs_won":  ["FOW", "FO", "FACEOFFS", "FW", "FOFACEOFFSWON"],
    "time_on_ice":   ["TOI", "MIN", "TIMEONICE", "ICETIME"],
    "saves":         ["SV", "SAVES"],
    "goals_allowed": ["GA", "GOALSAGAINST", "GOALSALLOWED"],
}


def _nhl_toi_to_minutes(val):
    """Convert ESPN/NHL TOI display (e.g. '19:42', '1:23:01') to decimal minutes."""
    if val is None or val in ("--", "-", ""):
        return None
    if isinstance(val, (int, float)) and not (isinstance(val, float) and pd.isna(val)):
        try:
            return round(float(val), 2)
        except (TypeError, ValueError):
            return None
    s = str(val).strip()
    if not s or s in ("--", "-"):
        return None
    if ":" not in s:
        try:
            return round(float(s), 2)
        except ValueError:
            return None
    parts = s.split(":")
    try:
        parts = [int(float(p)) for p in parts]
    except (ValueError, TypeError):
        return None
    if len(parts) == 2:
        m, sec = parts[0], parts[1]
        return round(m + sec / 60.0, 2)
    if len(parts) == 3:
        h, m, sec = parts[0], parts[1], parts[2]
        return round(h * 60 + m + sec / 60.0, 2)
    return None


def _nhl_stat_from_label_map(label_map: dict, key: str):
    """Like _parse_nhl_stat but returns raw cell (string or number) for TOI etc."""
    aliases = NHL_STAT_MAP.get(key, [key.upper()])
    for alias in aliases:
        norm = re.sub(r"[^A-Z0-9]", "", str(alias).upper())
        if norm in label_map:
            return label_map[norm]
    return None

def _parse_nhl_stat(label_map, key):
    """Look up a stat from NHL box score label map, return float or None."""
    aliases = NHL_STAT_MAP.get(key, [key.upper()])
    for alias in aliases:
        norm = re.sub(r"[^A-Z0-9]", "", str(alias).upper())
        if norm in label_map:
            try:
                return float(label_map[norm])
            except (ValueError, TypeError):
                pass
    return None


def _has_nhl_stat_label(norm_labels: list[str], key: str) -> bool:
    aliases = NHL_STAT_MAP.get(key, [key.upper()])
    alias_set = {re.sub(r"[^A-Z0-9]", "", str(a).upper()) for a in aliases}
    return any(lbl in alias_set for lbl in (norm_labels or []))


def parse_nhl_boxscore(box):
    """Parse NHL ESPN summary JSON into long-format actuals rows."""
    rows = []
    players_blocks = box.get("boxscore", {}).get("players", [])
    if not isinstance(players_blocks, list):
        return rows

    for team_block in players_blocks:
        if not isinstance(team_block, dict):
            continue
        t_abbr = team_block.get("team", {}).get("abbreviation", "")

        for stat_group in team_block.get("statistics", []):
            labels = stat_group.get("labels") or stat_group.get("keys") or []
            norm_labels = [re.sub(r"[^A-Z0-9]", "", str(l).upper()) for l in labels]
            athletes = stat_group.get("athletes") or []

            for a in athletes:
                athlete = a.get("athlete", {}) if isinstance(a, dict) else {}
                name = str(athlete.get("displayName", "")).strip()
                stats = a.get("stats") or []
                if not stats or all(s in ("--", "", None) for s in stats):
                    continue

                label_map = {}
                for i, lbl in enumerate(norm_labels):
                    if i < len(stats):
                        label_map[lbl] = stats[i]

                sog  = _parse_nhl_stat(label_map, "shots_on_goal")
                g    = _parse_nhl_stat(label_map, "goals")
                ast  = _parse_nhl_stat(label_map, "assists")
                pts  = (g + ast) if g is not None and ast is not None else (
                       _parse_nhl_stat(label_map, "points"))
                hits = _parse_nhl_stat(label_map, "hits")
                bs   = _parse_nhl_stat(label_map, "blocked_shots")
                pim  = _parse_nhl_stat(label_map, "pim")
                pm   = _parse_nhl_stat(label_map, "plus_minus")
                ppp  = _parse_nhl_stat(label_map, "power_play_points")
                fow  = _parse_nhl_stat(label_map, "faceoffs_won")
                toi_raw = _nhl_stat_from_label_map(label_map, "time_on_ice")
                toi  = _nhl_toi_to_minutes(toi_raw)
                sv   = _parse_nhl_stat(label_map, "saves")
                ga   = _parse_nhl_stat(label_map, "goals_allowed")

                is_goalie_row = (sv is not None or ga is not None) and all(
                    x is None for x in (sog, g, ast, hits)
                )

                # Goalie-only lines (separate ESPN stat group)
                if is_goalie_row:
                    if sv is not None:
                        rows.append({
                            "player": name,
                            "team": t_abbr,
                            "prop_type": "Goalie Saves",
                            "actual": round(float(sv), 1),
                            "source": "espn",
                        })
                    if ga is not None:
                        rows.append({
                            "player": name,
                            "team": t_abbr,
                            "prop_type": "Goals Allowed",
                            "actual": round(float(ga), 1),
                            "source": "espn",
                        })
                    continue

                has_skater_stat = any(
                    x is not None for x in (sog, g, ast, hits, bs, pim, pm, ppp, fow, toi)
                )
                if not has_skater_stat:
                    continue

                # Only impute missing skater stats to 0 when that stat label exists in this stat group.
                # This prevents mass-false 0.0 rows when ESPN changes/omits a label (notably SOG).
                if sog is None and _has_nhl_stat_label(norm_labels, "shots_on_goal"):
                    sog = 0.0
                if g is None and _has_nhl_stat_label(norm_labels, "goals"):
                    g = 0.0
                if ast is None and _has_nhl_stat_label(norm_labels, "assists"):
                    ast = 0.0
                if hits is None and _has_nhl_stat_label(norm_labels, "hits"):
                    hits = 0.0
                if bs is None and _has_nhl_stat_label(norm_labels, "blocked_shots"):
                    bs = 0.0
                if pim is None and _has_nhl_stat_label(norm_labels, "pim"):
                    pim = 0.0
                if pm is None and _has_nhl_stat_label(norm_labels, "plus_minus"):
                    pm = 0.0
                if pts is None and g is not None and ast is not None:
                    pts = float(g) + float(ast)

                prop_map = {
                    "Shots On Goal":      sog,
                    "Goals":              g,
                    "Assists":            ast,
                    "Points":             pts,
                    "Hits":               hits,
                    "Blocked Shots":      bs,
                    "PIM":                pim,
                    "Plus/Minus":         pm,
                    "Power Play Points":  ppp,
                    "Faceoffs Won":       fow,
                    "Time On Ice":        toi,
                }
                raw = {
                    "SOG": sog, "G": g, "A": ast, "PTS": pts,
                    "HIT": hits, "BS": bs, "PIM": pim, "PM": pm,
                    "PPP": ppp, "FOW": fow, "TOI": toi,
                }
                for prop_type, actual in prop_map.items():
                    if actual is None:
                        continue
                    row = {
                        "player":    name,
                        "team":      t_abbr,
                        "prop_type": prop_type,
                        "actual":    round(float(actual), 1),
                        "source":    "espn",
                    }
                    for col, val in raw.items():
                        row[col] = round(float(val), 1) if val is not None else None
                    rows.append(row)
    return rows


# ── Fetch NHL actuals ─────────────────────────────────────────────────────────
def _nhl_scoreboard_url(date_str: str) -> str:
    date_espn = date_str.replace("-", "")
    return NHL_SCOREBOARD_URL.format(date_espn=date_espn)


def _fetch_nhl_scoreboard_payload(date_str: str) -> tuple[list, str, str]:
    """
    Return (events list, url, response_text) for one calendar date.
    ESPN uses YYYYMMDD; some dates have zero scheduled games (true off-days or sparse slates).
    """
    url = _nhl_scoreboard_url(date_str)
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    text = r.text or ""
    try:
        data = r.json()
    except Exception:
        data = {}
    events = data.get("events", []) if isinstance(data, dict) else []
    if not isinstance(events, list):
        events = []
    return events, url, text


def fetch_nhl(date_str: str, adjacent_days: int = 1):
    """
    Fetch all completed NHL games for date_str and return actuals DataFrame.

    If the primary scoreboard date has **no events** (common when the slate calendar day
    has no NHL games in ESPN's schedule), also query ``adjacent_days`` before/after so
    nearby games still produce actuals (e.g. 2026-04-10 often has 0 events while 04-09/04-11 do).
    """
    print(f"\nFetching NHL scoreboard for {date_str} ...")

    try:
        primary_events, primary_url, primary_text = _fetch_nhl_scoreboard_payload(date_str)
    except Exception as e:
        print(f"  ERROR fetching NHL scoreboard: {e}")
        return pd.DataFrame()

    print(f"  Primary URL: {primary_url}")
    print(f"  Found {len(primary_events)} events on {date_str}")

    dates_to_fetch: list[str] = [date_str]
    if not primary_events:
        preview = (primary_text or "")[:800].replace("\n", " ")
        print(f"  Response preview (first 800 chars): {preview!r}")
        base = date.fromisoformat(date_str)
        for delta in range(-adjacent_days, adjacent_days + 1):
            if delta == 0:
                continue
            adj = (base + timedelta(days=delta)).strftime("%Y-%m-%d")
            if adj not in dates_to_fetch:
                dates_to_fetch.append(adj)
        print(
            "  No games on primary date — expanding scoreboard to: "
            f"{', '.join(dates_to_fetch)}"
        )

    seen_event_ids: set[str] = set()
    events: list = []

    for d in dates_to_fetch:
        try:
            if d == date_str:
                evs = primary_events
            else:
                evs, u, txt = _fetch_nhl_scoreboard_payload(d)
                print(f"  [{d}] URL: {u}")
                print(f"  [{d}] events: {len(evs)}")
                if not evs:
                    p2 = (txt or "")[:500].replace("\n", " ")
                    print(f"  [{d}] response preview: {p2!r}")
        except Exception as e:
            print(f"  WARN: scoreboard {d} failed: {e}")
            continue

        for e in evs:
            eid = str(e.get("id", "") or "")
            if eid:
                if eid in seen_event_ids:
                    continue
                seen_event_ids.add(eid)
            events.append(e)

    print(f"  Unique events to consider (after merge): {len(events)}")

    all_rows = []
    for event in events:
        state = event.get("status", {}).get("type", {}).get("state", "")
        completed = event.get("status", {}).get("type", {}).get("completed", False)
        if state != "post" and not completed:
            print(f"  Skipping {event.get('shortName','')} — not final")
            continue
        event_id = event.get("id", "")
        game_name = event.get("shortName", "")
        print(f"  Grading: {game_name}")
        try:
            br = requests.get(NHL_SUMMARY_URL.format(event_id=event_id),
                              headers=HEADERS, timeout=20)
            br.raise_for_status()
            rows = parse_nhl_boxscore(br.json())
            all_rows.extend(rows)
            print(f"    -> {len(rows)} stat rows")
            time.sleep(0.25)
        except Exception as e:
            print(f"    ERROR: {e}")

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["actual"] = pd.to_numeric(df["actual"], errors="coerce")
    if "source" not in df.columns:
        df["source"] = "espn"
    df = (df.sort_values("actual", ascending=False)
            .drop_duplicates(subset=["player", "team", "prop_type"], keep="first"))

    # Enrich with official NHL API for stats ESPN frequently misses/inconsistently
    # exposes (notably Hits and Faceoffs Won).
    nhl_api_df = fetch_nhl_api_enrichment(date_str)
    if not nhl_api_df.empty:
        merged_raw = pd.concat([df, nhl_api_df], ignore_index=True)
        merged_raw["actual"] = pd.to_numeric(merged_raw["actual"], errors="coerce")
        # Source conflict audit: same player/team/prop present in both sources with
        # materially different actuals. This catches feed/mapping divergences.
        conflict_keys: set[tuple[str, str, str]] = set()
        if {"player", "team", "prop_type", "source", "actual"}.issubset(merged_raw.columns):
            grouped = merged_raw.groupby(["player", "team", "prop_type"], dropna=False)
            for (pl, tm, pt), g in grouped:
                srcs = {str(x).strip().lower() for x in g["source"].dropna().tolist()}
                if not {"espn", "nhl_api"}.issubset(srcs):
                    continue
                lo = pd.to_numeric(g["actual"], errors="coerce").min()
                hi = pd.to_numeric(g["actual"], errors="coerce").max()
                if pd.notna(lo) and pd.notna(hi) and float(abs(hi - lo)) >= 0.5:
                    conflict_keys.add((str(pl), str(tm), str(pt)))
        if conflict_keys:
            print(f"  [NHL source-audit] conflicts detected: {len(conflict_keys)} keys (|espn-nhl_api| >= 0.5)")

        df = merged_raw.copy()
        df["source_conflict"] = df.apply(
            lambda r: 1 if (str(r.get("player")), str(r.get("team")), str(r.get("prop_type"))) in conflict_keys else 0,
            axis=1,
        )
        if "source" not in df.columns:
            df["source"] = "espn"
        prop_norm = df["prop_type"].astype(str).str.strip().str.lower()
        src = df["source"].astype(str).str.strip().str.lower()
        prefer_api = prop_norm.isin({"shots on goal", "goalie saves", "time on ice"})
        src_rank = pd.Series(0, index=df.index)
        src_rank = src_rank + (src.eq("nhl_api") & prefer_api).astype(int) * 10
        src_rank = src_rank + src.eq("nhl_api").astype(int)
        df = (
            df.assign(_src_rank=src_rank)
            .sort_values(["_src_rank", "actual"], ascending=[False, False])
            .drop_duplicates(subset=["player", "team", "prop_type"], keep="first")
            .drop(columns=["_src_rank"], errors="ignore")
        )
        print(f"  NHL API enrichment rows merged: {len(nhl_api_df)}")

    print(f"\n  Total: {len(df)} NHL player-prop actuals")
    return df


def _nhl_api_player_name(p):
    """Display name from NHL API player object (nested name.default or first/last)."""
    if not isinstance(p, dict):
        return ""
    name = str(p.get("name", {}).get("default", "") or p.get("name", "")).strip()
    if not name:
        first = str(p.get("firstName", {}).get("default", "") or "").strip()
        last = str(p.get("lastName", {}).get("default", "") or "").strip()
        name = f"{first} {last}".strip()
    return name


def _nhl_player_rows_from_team_block(team_block, team_abbr):
    """Flatten NHL API team block into prop rows for skaters and goalies."""
    rows = []
    if not isinstance(team_block, dict):
        return rows

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # NHL API typically separates skaters into forwards/defensemen and goalies.
    skater_groups = []
    for key in ("forwards", "defense", "defencemen", "defensemen", "skaters"):
        group = team_block.get(key, [])
        if isinstance(group, list) and group:
            skater_groups.extend(group)

    for p in skater_groups:
        if not isinstance(p, dict):
            continue

        name = _nhl_api_player_name(p)
        if not name:
            continue

        g = _num(p.get("goals"))
        a = _num(p.get("assists"))
        pts = _num(p.get("points"))
        sog = _num(p.get("sog") if p.get("sog") is not None else p.get("shots"))
        hits = _num(p.get("hits"))
        blocks = _num(p.get("blockedShots") if p.get("blockedShots") is not None else p.get("blocks"))
        pim = _num(p.get("pim"))
        plus_minus = _num(p.get("plusMinus"))
        fow = _num(p.get("faceoffWins") if p.get("faceoffWins") is not None else p.get("faceoffsWon"))
        ppp = _num(p.get("powerPlayPoints"))
        if ppp is None:
            ppp = _num(p.get("ppPoints"))
        if ppp is None:
            # NHL API often provides split PP stats instead of a direct PPP field.
            ppg = _num(p.get("powerPlayGoals"))
            ppa = _num(p.get("powerPlayAssists"))
            if ppg is not None or ppa is not None:
                ppp = float(ppg or 0.0) + float(ppa or 0.0)
        toi_raw = p.get("toi") or p.get("iceTime") or p.get("timeOnIce")
        toi_min = _nhl_toi_to_minutes(toi_raw)

        if pts is None and g is not None and a is not None:
            pts = g + a

        prop_map = {
            "Shots On Goal": sog,
            "Goals": g,
            "Assists": a,
            "Points": pts,
            "Hits": hits,
            "Blocked Shots": blocks,
            "PIM": pim,
            "Plus/Minus": plus_minus,
            "Faceoffs Won": fow,
            "Power Play Points": ppp,
            "Time On Ice": toi_min,
        }
        for prop_type, actual in prop_map.items():
            if actual is None:
                continue
            rows.append({
                "player": name,
                "team": team_abbr,
                "prop_type": prop_type,
                "actual": round(float(actual), 1),
                "source": "nhl_api",
            })

    for p in team_block.get("goalies", []) or []:
        if not isinstance(p, dict):
            continue
        name = _nhl_api_player_name(p)
        if not name:
            continue
        saves = _num(p.get("saves"))
        ga = _num(
            p.get("goalsAgainst")
            if p.get("goalsAgainst") is not None
            else p.get("goalsAllowed")
        )
        if saves is not None:
            rows.append({
                "player": name,
                "team": team_abbr,
                "prop_type": "Goalie Saves",
                "actual": round(float(saves), 1),
                "source": "nhl_api",
            })
        if ga is not None:
            rows.append({
                "player": name,
                "team": team_abbr,
                "prop_type": "Goals Allowed",
                "actual": round(float(ga), 1),
                "source": "nhl_api",
            })

    return rows


def fetch_nhl_api_enrichment(date_str):
    """
    Pull NHL player box stats from api-web.nhle.com and return long-format rows.
    Covers Hits, Faceoffs Won, Power Play Points, Time On Ice, and goalie saves / goals allowed
    when ESPN omits or mislabels them.
    """
    out_rows = []
    try:
        sr = requests.get(NHL_API_SCHEDULE_URL.format(date_iso=date_str), headers=HEADERS, timeout=20)
        sr.raise_for_status()
        sched = sr.json()
    except Exception as e:
        print(f"  WARNING: NHL API schedule fetch failed: {e}")
        return pd.DataFrame()

    games = []
    if isinstance(sched, dict):
        if isinstance(sched.get("gameWeek"), list):
            for day in sched.get("gameWeek", []):
                if not isinstance(day, dict):
                    continue
                if str(day.get("date", ""))[:10] != date_str:
                    continue
                games.extend(day.get("games", []) or [])
        elif isinstance(sched.get("games"), list):
            games = sched.get("games", [])

    if not games:
        return pd.DataFrame()
    print(f"  NHL API schedule games on {date_str}: {len(games)}")
    for g in games:
        if not isinstance(g, dict):
            continue
        game_id = g.get("id") or g.get("gameId")
        if not game_id:
            continue

        # Respect final-state only.
        game_state = str(g.get("gameState", "")).upper()
        if game_state and game_state not in ("FINAL", "OFF", "GAMEOVER"):
            continue

        away_abbr = str((g.get("awayTeam") or {}).get("abbrev", "")).upper()
        home_abbr = str((g.get("homeTeam") or {}).get("abbrev", "")).upper()

        try:
            br = requests.get(NHL_API_BOX_URL.format(game_id=game_id), headers=HEADERS, timeout=20)
            br.raise_for_status()
            box = br.json()
        except Exception as e:
            print(f"    NHL API boxscore failed ({game_id}): {e}")
            continue

        pstats = box.get("playerByGameStats", {}) if isinstance(box, dict) else {}
        away_block = pstats.get("awayTeam", {}) if isinstance(pstats, dict) else {}
        home_block = pstats.get("homeTeam", {}) if isinstance(pstats, dict) else {}

        out_rows.extend(_nhl_player_rows_from_team_block(away_block, away_abbr))
        out_rows.extend(_nhl_player_rows_from_team_block(home_block, home_abbr))
        time.sleep(0.2)

    if not out_rows:
        return pd.DataFrame()
    return pd.DataFrame(out_rows)


# ── Parse Soccer ESPN box score ───────────────────────────────────────────────
#
# ESPN soccer roster stats are NOT flat arrays — each entry["stats"] is a
# list of stat OBJECTS with this structure:
#   {"name": "foulsCommitted", "abbreviation": "FC", "value": 0.0, ...}
#
# We index by abbreviation.upper() -> value.
#
# Known ESPN soccer abbreviations:
#   G   = goals             A   = goalAssists (assists)
#   SH  = totalShots        SOG = shotsOnTarget
#   SV  = saves (GK)        PA  = totalPass
#   KP  = keyPass           TK  = totalTackle
#   FC  = foulsCommitted    YC  = yellowCards
#   MIN = minsPlayed        RC  = redCards
#   FA  = foulsSuffered     OG  = ownGoals
#
SOCCER_STAT_MAP = {
    # Shots on target — ESPN uses SOG (shotsOnTarget)
    "shots_on_target": ["SOG", "SOT", "SHOTSONTARGET", "ONTARGETSCORINGATT",
                        "SHT_ON_TARGET", "SHOTS_ON_TARGET"],
    # Total shots
    "shots":           ["SH", "TOTALSHOTS", "SHOTS", "SHT", "ATTSHOT"],
    # Goals
    "goals":           ["G", "GOALS", "GL", "GLS"],
    # Assists — ESPN uses "A" (goalAssists)
    "assists":         ["A", "GOALASSISTS", "ASSISTS", "AST"],
    # Goalkeeper saves
    "saves":           ["SV", "SAVES", "SVS", "GOALSAVE"],
    # Passes — ESPN uses "PA" (totalPass)
    "passes":          ["PA", "TOTALPASS", "PASSES", "PS"],
    # Key passes
    "key_passes":      ["KP", "KEYPASS", "KEY_PASSES", "KEYPASSES"],
    # Tackles — ESPN uses "TK" (totalTackle)
    "tackles":         ["TK", "TOTALTACKLE", "TACKLES", "TCKS"],
    # Fouls committed — ESPN uses "FC" (foulsCommitted)
    "fouls":           ["FC", "FOULSCOMMITTED", "FL", "FOULS", "FOULSC"],
    # Yellow cards — ESPN uses "YC" (yellowCards)
    "yellow_cards":    ["YC", "YELLOWCARDS", "YELLOW", "YELLOWS"],
}


def _build_soccer_label_map(stats_list: list) -> dict:
    """
    Build {NORM_ABBREV: float_value} from an ESPN soccer stats list.

    ESPN soccer uses TWO formats depending on the endpoint:
      Format A (rosters path — confirmed by diagnostic):
        stats_list = [
          {"name": "foulsCommitted", "abbreviation": "FC", "value": 0.0, ...},
          {"name": "goals",          "abbreviation": "G",  "value": 1.0, ...},
          ...
        ]
      Format B (older / boxscore.players path):
        stats_list = ["0", "1", "--", ...]   (flat strings, labels from parent)

    This function handles Format A.  Format B is handled separately in the
    boxscore.players fallback path.
    """
    norm = lambda s: re.sub(r"[^A-Z0-9]", "", str(s).upper())
    label_map = {}
    for stat in stats_list:
        if not isinstance(stat, dict):
            return {}  # not Format A — signal caller to use flat-array path
        abbr = stat.get("abbreviation") or stat.get("name") or ""
        val  = stat.get("value")
        if abbr and val is not None:
            try:
                label_map[norm(abbr)] = float(val)
            except (TypeError, ValueError):
                pass
    return label_map


def _get_soccer_stat(label_map: dict, key: str):
    """Look up a soccer stat from a label_map using SOCCER_STAT_MAP aliases."""
    norm = lambda s: re.sub(r"[^A-Z0-9]", "", str(s).upper())
    for alias in SOCCER_STAT_MAP.get(key, [key.upper()]):
        k = norm(alias)
        if k in label_map:
            return label_map[k]
    return None


def _emit_soccer_rows(name: str, t_abbr: str, label_map: dict, league_id: str,
                      espn_id: str = "") -> list:
    """Given a player's label_map, extract all soccer props and return row list."""
    sot = _get_soccer_stat(label_map, "shots_on_target")
    sh  = _get_soccer_stat(label_map, "shots")
    g   = _get_soccer_stat(label_map, "goals")
    ast = _get_soccer_stat(label_map, "assists")
    sv  = _get_soccer_stat(label_map, "saves")
    pa  = _get_soccer_stat(label_map, "passes")
    kp  = _get_soccer_stat(label_map, "key_passes")
    tk  = _get_soccer_stat(label_map, "tackles")
    fl  = _get_soccer_stat(label_map, "fouls")
    yc  = _get_soccer_stat(label_map, "yellow_cards")

    if all(x is None for x in [sot, sh, g, ast, sv, pa, kp, tk, fl, yc]):
        return []

    prop_map = {
        "Shots On Target":  sot,
        "Shots":            sh,
        "Goals":            g,
        "Assists":          ast,
        "Goalkeeper Saves": sv,
        "Passes":           pa,
        "Key Passes":       kp,
        "Tackles":          tk,
        "Fouls":            fl,
        "Yellow Cards":     yc,
    }
    raw = {"SOT": sot, "SH": sh, "G": g, "A": ast,
           "SV": sv,  "PA": pa, "KP": kp, "TK": tk}

    out = []
    for prop_type, actual in prop_map.items():
        if actual is not None:
            row = {
                "player":          name,
                "team":            t_abbr,
                "prop_type":       prop_type,
                "actual":          round(float(actual), 1),
                "league":          league_id,
                "espn_player_id":  espn_id,
            }
            for col, val in raw.items():
                row[col] = round(float(val), 1) if val is not None else None
            out.append(row)
    return out


def parse_soccer_boxscore(box, league_id):
    """
    Parse ESPN soccer summary JSON into long-format actuals rows.

    ESPN soccer uses box['rosters'] where each athlete entry has:
      entry['athlete']['displayName']
      entry['stats'] = list of stat objects:
        [{"abbreviation": "G", "value": 1.0}, {"abbreviation": "SH", "value": 3.0}, ...]

    Fallback: some older endpoints use box['boxscore']['players'] with
    flat stats arrays and parent-level labels (Format B).
    """
    rows = []
    norm = lambda s: re.sub(r"[^A-Z0-9]", "", str(s).upper())

    # ── PATH 1 (primary): box['rosters'] with stat objects ────────────────────
    rosters = box.get("rosters")
    if isinstance(rosters, list) and len(rosters) > 0:
        for team_block in rosters:
            if not isinstance(team_block, dict):
                continue
            t_abbr = team_block.get("team", {}).get("abbreviation", "")

            for entry in (team_block.get("roster") or []):
                if not isinstance(entry, dict):
                    continue
                athlete = entry.get("athlete", {})
                name    = str(athlete.get("displayName", "")).strip()
                espn_id = str(athlete.get("id", "")).strip()
                if not name:
                    continue

                stats_list = entry.get("stats") or []
                if not stats_list:
                    continue

                label_map = _build_soccer_label_map(stats_list)
                if not label_map:
                    continue  # not stat-object format, skip

                rows.extend(_emit_soccer_rows(name, t_abbr, label_map, league_id,
                                              espn_id=espn_id))

        if rows:
            return rows  # rosters path worked

    # ── PATH 2 (fallback): box['boxscore']['players'] with flat arrays ────────
    players_blocks = box.get("boxscore", {}).get("players", [])
    if not isinstance(players_blocks, list):
        return rows

    for team_block in players_blocks:
        if not isinstance(team_block, dict):
            continue
        t_abbr = team_block.get("team", {}).get("abbreviation", "")

        for stat_group in team_block.get("statistics", []):
            parent_labels = stat_group.get("labels") or stat_group.get("keys") or []
            norm_labels   = [norm(l) for l in parent_labels]

            for a in (stat_group.get("athletes") or []):
                athlete = a.get("athlete", {}) if isinstance(a, dict) else {}
                name    = str(athlete.get("displayName", "")).strip()
                espn_id = str(athlete.get("id", "")).strip()
                if not name:
                    continue
                flat_stats = a.get("stats") or []
                if not flat_stats or all(s in ("--", "", None) for s in flat_stats):
                    continue
                label_map = {}
                for i, lbl in enumerate(norm_labels):
                    if i < len(flat_stats):
                        try:
                            label_map[lbl] = float(flat_stats[i])
                        except (TypeError, ValueError):
                            pass
                rows.extend(_emit_soccer_rows(name, t_abbr, label_map, league_id,
                                              espn_id=espn_id))

    return rows


# ── Fetch Soccer actuals ──────────────────────────────────────────────────────
def fetch_soccer(date_str, adjacent_days: int = 1):
    """
    Fetch completed soccer games across all tracked leagues.

    adjacent_days: also scan the following N calendar days (default 1). PrizePicks
    slate files are keyed by board date while many soccer kickoffs (e.g. UCL) are
    the next local day; without +1 day, grade-date actuals miss the slate and every
    soccer row VOIDs.
    """
    base = date.fromisoformat(date_str)
    all_rows = []
    seen_event_ids = set()
    day_span = max(0, int(adjacent_days)) + 1

    for day_off in range(day_span):
        day_iso = (base + timedelta(days=day_off)).strftime("%Y-%m-%d")
        date_espn = day_iso.replace("-", "")
        if day_off:
            print(f"\n  Soccer: also fetching calendar day +{day_off} -> {day_iso}")

        for league_id, league_name in SOCCER_LEAGUES:
            try:
                url = SOCCER_SCOREBOARD_BASE.format(league=league_id, date_espn=date_espn)
                r = requests.get(url, headers=HEADERS, timeout=20)
                r.raise_for_status()
                events = r.json().get("events", [])
                if not events:
                    continue
                print(f"  {league_name}: {len(events)} events")

                for event in events:
                    state     = event.get("status", {}).get("type", {}).get("state", "")
                    completed = event.get("status", {}).get("type", {}).get("completed", False)
                    if state != "post" and not completed:
                        continue
                    event_id = event.get("id", "")
                    if event_id in seen_event_ids:
                        continue
                    seen_event_ids.add(event_id)

                    game_name = event.get("shortName", "")
                    print(f"    Grading: {game_name}")
                    try:
                        sum_url = SOCCER_SUMMARY_BASE.format(league=league_id, event_id=event_id)
                        br = requests.get(sum_url, headers=HEADERS, timeout=20)
                        br.raise_for_status()
                        box_json = br.json()
                        game_rows = parse_soccer_boxscore(box_json, league_id)
                        all_rows.extend(game_rows)
                        if len(game_rows) == 0:
                            # Diagnostic: dump first athlete's actual stats structure
                            rosters = box_json.get("rosters", [])
                            if isinstance(rosters, list) and rosters:
                                first_team  = rosters[0] if isinstance(rosters[0], dict) else {}
                                roster_list = first_team.get("roster") or []
                                first_entry = roster_list[0] if roster_list else {}
                                entry_stats = first_entry.get("stats", [])
                                # Show abbreviations found so we can add missing aliases
                                abbrevs = [s.get("abbreviation","?") for s in entry_stats
                                           if isinstance(s, dict)]
                                print(f"      WARNING: 0 rows — stat abbrevs in roster: {abbrevs}")
                            else:
                                print(f"      WARNING: 0 rows — no rosters block found")
                                print(f"      Top-level keys: {list(box_json.keys())}")
                        else:
                            print(f"      -> {len(game_rows)} stat rows")
                        time.sleep(0.2)
                    except Exception as e:
                        print(f"      ERROR: {e}")

                time.sleep(0.3)
            except Exception as e:
                print(f"  WARNING: {league_name} fetch failed: {e}")

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["actual"] = pd.to_numeric(df["actual"], errors="coerce")
    # Sort: rows with a real espn_player_id first, then by actual descending
    # so drop_duplicates keeps the ID-bearing row when dupes exist
    df["_has_id"] = df["espn_player_id"].astype(str).str.strip().ne("").astype(int)
    df = (df.sort_values(["_has_id", "actual"], ascending=[False, False])
            .drop_duplicates(subset=["player", "team", "prop_type"], keep="first")
            .drop(columns=["_has_id"]))

    # Build combo prop from existing component stats.
    combo = (df[df["prop_type"].isin(["Goals", "Assists"])]
               .pivot_table(index=["player", "team"], columns="prop_type", values="actual",
                            aggfunc="max", fill_value=0)
               .reset_index())
    if not combo.empty:
        combo["actual"] = combo.get("Goals", 0) + combo.get("Assists", 0)
        combo = combo[["player", "team", "actual"]].copy()
        combo["prop_type"] = "Goal + Assist"
        # Carry ID/league when available from existing rows.
        meta = (df.sort_values("actual", ascending=False)
                  .drop_duplicates(subset=["player", "team"], keep="first")
                  [["player", "team", "league", "espn_player_id"]])
        combo = combo.merge(meta, on=["player", "team"], how="left")
        df = pd.concat([df, combo], ignore_index=True)
        df = (df.sort_values(["player", "team", "prop_type", "actual"], ascending=[True, True, True, False])
                .drop_duplicates(subset=["player", "team", "prop_type"], keep="first"))

    print(f"\n  Total: {len(df)} Soccer player-prop actuals across {len(seen_event_ids)} games")
    return df


def _export_injuries_sidecar(args) -> None:
    """Write ESPN injury report CSV beside actuals (NBA/CBB/NHL only)."""
    if args.sport not in ("NBA", "CBB", "WCBB", "NHL"):
        return
    try:
        from espn_injuries import injuries_csv_path_for_actuals, write_injuries_for_date
    except ImportError:
        return
    try:
        outp = injuries_csv_path_for_actuals(args.output, args.sport, date_hint=args.date)
        n = write_injuries_for_date(args.sport, args.date, outp)
        print(f"  Injury report saved -> {outp}  ({n} rows)")
    except Exception as e:
        print(f"  WARNING: injury export failed: {e}")


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        '--sport',
        default='NBA',
        choices=['NBA', 'CBB', 'WCBB', 'NHL', 'Soccer', 'WNBA'],
    )
    ap.add_argument('--date',   default='', help='YYYY-MM-DD (default: yesterday)')
    ap.add_argument('--output', default='')
    ap.add_argument('--window', default=2, type=int,
                    help='CBB only: days either side of target date to fetch (default: 2, use 0 for single-date)')
    ap.add_argument('--nhl-window', default=1, type=int,
                    help='NHL only: when the primary scoreboard date has zero games, also fetch +/- this many '
                         'calendar days (default: 1). Use 0 to disable expansion.')
    ap.add_argument('--soccer-window', default=1, type=int,
                    help='Soccer only: also fetch completed games for the following N calendar days after '
                         '--date (default: 1). Use 0 for single calendar day only.')
    ap.add_argument('--nba-window', default=1, type=int,
                    help='NBA / WNBA: also fetch scoreboards for the following N calendar days after --date '
                         '(default: 1) so games ESPN indexes on the next day are included. Use 0 for '
                         'single calendar day only.')
    args = ap.parse_args()

    if not args.date:
        args.date = (date.today() - timedelta(days=1)).strftime('%Y-%m-%d')
    if not args.output:
        slug = "wcbb" if args.sport == "WCBB" else args.sport.lower()
        # Default to dated pipeline layout (outputs/YYYY-MM-DD/actuals_<sport>_YYYY-MM-DD.csv)
        # so manual runs don't drop files at repo root.
        args.output = str(_REPO_ROOT / "outputs" / args.date / f"actuals_{slug}_{args.date}.csv")
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    args.output = str(out_path)

    print(f"\n=== {args.sport} actuals for {args.date} ===\n")

    empty_reason = "pending"
    nba_box_dnp: list[dict] = []
    if args.sport == 'NHL':
        w = max(0, int(args.nhl_window))
        df = fetch_nhl(args.date, adjacent_days=w)
    elif args.sport == 'Soccer':
        df = fetch_soccer(args.date, adjacent_days=max(0, int(args.soccer_window)))
    elif args.sport == "WNBA":
        w = max(0, int(args.nba_window))
        df, empty_reason, nba_box_dnp = fetch_sport(
            "wnba", args.date, window=args.window, nba_extra_days=w
        )
    else:
        if args.sport == "NBA":
            sport_path = "nba"
        elif args.sport == "CBB":
            sport_path = "mens-college-basketball"
        else:
            sport_path = "womens-college-basketball" if args.sport == "WCBB" else "mens-college-basketball"
        nba_x = max(0, int(args.nba_window)) if args.sport == "NBA" else 0
        df, empty_reason, nba_box_dnp = fetch_sport(
            sport_path, args.date, window=args.window, nba_extra_days=nba_x
        )

    if df.empty:
        # Always write a header-only stub so downstream grading never skips due to
        # missing files on pre-final slates or true off-days.
        stub = pd.DataFrame(columns=["player", "team", "prop_type", "actual"])
        stub.to_csv(args.output, index=False)
        _export_injuries_sidecar(args)
        if args.sport in ("NBA", "WNBA") and nba_box_dnp:
            n_merged = merge_nba_box_dnp_into_injuries_csv(nba_box_dnp, args.date, args.output)
            if n_merged:
                print(f"  Box-score DNP merged into injuries sidecar (+{n_merged} row(s))")
        if empty_reason == "no_games":
            print(f"\nNo games scheduled — wrote empty actuals stub -> {args.output}")
        else:
            print(f"\nNo actuals fetched yet — wrote empty actuals stub -> {args.output}")
            print("Games may not be final yet; rerun after completion for decided grades.")
        return

    df.to_csv(args.output, index=False)
    _export_injuries_sidecar(args)
    if args.sport in ("NBA", "WNBA") and nba_box_dnp:
        n_merged = merge_nba_box_dnp_into_injuries_csv(nba_box_dnp, args.date, args.output)
        if n_merged:
            print(f"  Box-score DNP merged into injuries sidecar (+{n_merged} row(s))")
    print(f"\nSaved -> {args.output}  ({len(df)} rows)")
    print(f"\nProp types extracted: {sorted(df['prop_type'].unique().tolist())}")

    # Coverage report
    teams = sorted(df['team'].unique().tolist())
    print(f"\nTeams covered ({len(teams)}): {', '.join(teams)}")

    print(f"\nNext step:")
    if args.sport == 'NBA':
        print(f"  py -3 slate_grader.py --sport NBA "
              f"--slate NBA\\step8_all_direction_clean.xlsx "
              f"--actuals {args.output} --output nba_graded_{args.date}.xlsx")
    elif args.sport == 'NHL':
        print(f"  py -3 slate_grader.py --sport NHL "
              f"--slate NHL\\outputs\\step8_nhl_direction_clean.xlsx "
              f"--actuals {args.output} --output nhl_graded_{args.date}.xlsx")
    elif args.sport == 'Soccer':
        print(f"  py -3 slate_grader.py --sport Soccer "
              f"--slate Soccer\\step8_soccer_direction_clean.xlsx "
              f"--actuals {args.output} --output soccer_graded_{args.date}.xlsx")
    elif args.sport == "WCBB":
        print(f"  py -3 slate_grader.py --sport CBB "
              f"--slate CBB\\step6_ranked_wcbb.xlsx "
              f"--actuals {args.output} --output graded_wcbb_{args.date}.xlsx")
    else:
        print(f"  py -3 grade_cbb_full_slate.py "
              f"--slate CBB\\step6_ranked_cbb.xlsx "
              f"--actuals {args.output} --output cbb_graded_{args.date}.xlsx")


if __name__ == '__main__':
    main()

