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

Fixes vs previous version:
  - CBB scoreboard now paginates through ALL pages (was capped at 200 events)
  - Each actuals row now includes raw stat columns (PTS, REB, AST, 3PM, etc.)
    so the grader's stat_from_row() can look them up directly
  - 3-PT Made no longer voids as UNSUPPORTED_PROP
"""

import argparse
import re
import requests
import pandas as pd
import time
from datetime import date, timedelta

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# ── Build all prop rows from a stat_map ──────────────────────────────────────
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

    # Fantasy score (PrizePicks DK-style)
    fs = None
    if all(x is not None for x in [pts, reb, ast, stl, blk, tov, fg3m]):
        fs = (pts * 1.0 + reb * 1.25 + ast * 1.5 + stl * 2.0 + blk * 2.0
              - tov * 0.5 + fg3m * 0.5
              + (4.5 if pts >= 10 else 0)
              + (6.0 if reb >= 10 else 0)
              + (6.0 if ast >= 10 else 0))

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
def parse_boxscore(box):
    rows = []
    for bteam in box.get('boxscore', {}).get('players', []):
        t_abbr_raw = bteam.get('team', {}).get('abbreviation', '')
        # Normalize ESPN abbrev to match slate/PrizePicks abbreviations
        t_abbr = ESPN_TO_SLATE_ABBREV.get(t_abbr_raw, t_abbr_raw)
        for stat_group in bteam.get('statistics', []):
            labels = stat_group.get('labels', [])
            for athlete in stat_group.get('athletes', []):
                player_name = athlete.get('athlete', {}).get('displayName', '')
                stats = athlete.get('stats', [])
                if not stats or all(s in ('--', '', None) for s in stats):
                    continue

                stat_map = {}
                raw_map  = {}
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
                    raw_map.get('FG') or raw_map.get('FGM-A') or raw_map.get('FGMA'))
                if fg_m is not None:
                    stat_map['FGM'] = fg_m
                    stat_map['FGA'] = fg_a

                t3_m, t3_a = _parse_made_att(
                    raw_map.get('3PT') or raw_map.get('3FG') or raw_map.get('3PTM-A'))
                if t3_m is not None:
                    stat_map['3PM'] = t3_m
                    stat_map['3PA'] = t3_a

                ft_m, ft_a = _parse_made_att(
                    raw_map.get('FT') or raw_map.get('FTM-A'))
                if ft_m is not None:
                    stat_map['FTM'] = ft_m
                    stat_map['FTA'] = ft_a

                tw_m, tw_a = _parse_made_att(
                    raw_map.get('2PT') or raw_map.get('2FG') or raw_map.get('2PTM-A'))
                if tw_m is not None:
                    stat_map['2PM'] = tw_m
                    stat_map['2PA'] = tw_a

                label_aliases = {
                    '3PM':  ['3PM', 'FG3M', '3FGM'],
                    '3PA':  ['3PA', 'FG3A', '3FGA'],
                    'FGM':  ['FGM'],
                    'FGA':  ['FGA'],
                    'FTM':  ['FTM'],
                    'FTA':  ['FTA'],
                    'PTS':  ['PTS'],
                    'REB':  ['REB', 'TREB'],
                    'AST':  ['AST'],
                    'BLK':  ['BLK'],
                    'STL':  ['STL'],
                    'TO':   ['TO', 'TOV'],
                    'PF':   ['PF', 'FOULS'],
                    'OREB': ['OREB'],
                    'DREB': ['DREB'],
                    'MIN':  ['MIN'],
                }
                normalized = {}
                for canon, aliases in label_aliases.items():
                    for alias in aliases:
                        if alias in stat_map:
                            normalized[canon] = stat_map[alias]
                            break

                if not normalized:
                    continue
                rows.extend(parse_stats(player_name, t_abbr, normalized))
    return rows


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
    ("sau.1",  "Saudi Pro League"),
    ("aus.1",  "A-League"),
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


# ── Main sport fetch ──────────────────────────────────────────────────────────
def fetch_sport(sport_path, date_str, window=2):
    from datetime import datetime as _dt, timedelta as _td

    is_cbb    = 'college' in sport_path
    target_dt = _dt.strptime(date_str, "%Y-%m-%d")

    # CBB: optionally fetch a multi-day window (-window to +window) to catch games
    # ESPN indexes under adjacent dates. Pass window=0 for single-date fetch (faster,
    # recommended for same-day grading runs where the slate date is already known).
    if is_cbb and window > 0:
        fetch_dates = [
            (target_dt + _td(days=d)).strftime("%Y-%m-%d")
            for d in range(-window, window + 1)
        ]
        print(f"CBB mode: conference-by-conference fetch across {window*2+1}-day window "
              f"({fetch_dates[0]} → {fetch_dates[-1]})")
    else:
        fetch_dates = [date_str]
        if is_cbb:
            print(f"CBB mode: single-date fetch for {date_str} (--window 0)")

    seen_ids = set()
    events   = []
    for d in fetch_dates:
        print(f"\nFetching scoreboard for {d} ...")
        day_events = fetch_events_for_date(sport_path, d, is_cbb=is_cbb)
        new = 0
        for e in day_events:
            eid = str(e.get('id', '')).strip()
            if eid and eid not in seen_ids:
                seen_ids.add(eid)
                events.append(e)
                new += 1
        print(f"  Day total: {len(day_events)} events ({new} new unique)")

    print(f"\n  Grand total unique events to process: {len(events)}")

    all_rows         = []
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

        rows = parse_boxscore(box)
        all_rows.extend(rows)
        print(f"    -> {len(rows)} stat rows")

    if not all_rows:
        # Distinguish "no games on slate" vs "games not final yet" for downstream stubs.
        reason = "no_games" if len(events) == 0 else "pending"
        return pd.DataFrame(), reason

    df = pd.DataFrame(all_rows)

    # Deduplicate per player+team+prop_type — keep highest actual value
    # (guards against a player appearing on multiple date pages)
    df['actual'] = pd.to_numeric(df['actual'], errors='coerce')
    df = (df.sort_values('actual', ascending=False)
            .drop_duplicates(subset=['player', 'team', 'prop_type'], keep='first'))

    print(f"\n  Total: {len(df)} player-prop actuals across {len(graded_event_ids)} games")
    return df, "ok"



# ── Parse NHL ESPN box score ──────────────────────────────────────────────────
NHL_STAT_MAP = {
    "shots_on_goal": ["SOG", "S", "SHOTS"],
    "goals":         ["G", "GOALS"],
    "assists":       ["A", "ASSISTS"],
    "points":        ["PTS", "P"],
    "hits":          ["HIT", "HITS"],
    "blocked_shots": ["BS", "BKS", "BLOCKED"],
    "pim":           ["PIM"],
    "plus_minus":    ["PLUSMINUS", "+/-"],
    "power_play_points": ["PPP"],
    "faceoffs_won":  ["FOW"],
    "time_on_ice":   ["TOI"],
}

def _parse_nhl_stat(label_map, key):
    """Look up a stat from NHL box score label map, return float or None."""
    aliases = NHL_STAT_MAP.get(key, [key.upper()])
    for alias in aliases:
        norm = re.sub(r"[^A-Z0-9]", "", alias.upper())
        if norm in label_map:
            try:
                return float(label_map[norm])
            except (ValueError, TypeError):
                pass
    return None


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
                toi  = _parse_nhl_stat(label_map, "time_on_ice")

                # Only emit rows for players with any real stats
                if all(x is None for x in [sog, g, ast, hits]):
                    continue

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
                    if actual is not None:
                        row = {
                            "player":    name,
                            "team":      t_abbr,
                            "prop_type": prop_type,
                            "actual":    round(float(actual), 1),
                        }
                        for col, val in raw.items():
                            row[col] = round(float(val), 1) if val is not None else None
                        rows.append(row)
    return rows


# ── Fetch NHL actuals ─────────────────────────────────────────────────────────
def fetch_nhl(date_str):
    """Fetch all completed NHL games for date_str and return actuals DataFrame."""
    date_espn = date_str.replace("-", "")
    print(f"\nFetching NHL scoreboard for {date_str} ...")
    try:
        r = requests.get(NHL_SCOREBOARD_URL.format(date_espn=date_espn),
                         headers=HEADERS, timeout=20)
        r.raise_for_status()
        events = r.json().get("events", [])
    except Exception as e:
        print(f"  ERROR fetching NHL scoreboard: {e}")
        return pd.DataFrame()

    print(f"  Found {len(events)} events")
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
    df = (df.sort_values("actual", ascending=False)
            .drop_duplicates(subset=["player", "team", "prop_type"], keep="first"))

    # Enrich with official NHL API for stats ESPN frequently misses/inconsistently
    # exposes (notably Hits and Faceoffs Won).
    nhl_api_df = fetch_nhl_api_enrichment(date_str)
    if not nhl_api_df.empty:
        df = pd.concat([df, nhl_api_df], ignore_index=True)
        df["actual"] = pd.to_numeric(df["actual"], errors="coerce")
        df = (df.sort_values("actual", ascending=False)
                .drop_duplicates(subset=["player", "team", "prop_type"], keep="first"))
        print(f"  NHL API enrichment rows merged: {len(nhl_api_df)}")

    print(f"\n  Total: {len(df)} NHL player-prop actuals")
    return df


def _nhl_player_rows_from_team_block(team_block, team_abbr):
    """Flatten NHL API team block into prop rows for skaters."""
    rows = []
    if not isinstance(team_block, dict):
        return rows

    # NHL API typically separates skaters into forwards/defensemen and goalies.
    skater_groups = []
    for key in ("forwards", "defense", "defencemen", "defensemen", "skaters"):
        group = team_block.get(key, [])
        if isinstance(group, list) and group:
            skater_groups.extend(group)

    for p in skater_groups:
        if not isinstance(p, dict):
            continue

        name = str(p.get("name", {}).get("default", "") or p.get("name", "")).strip()
        if not name:
            name = str(p.get("firstName", {}).get("default", "") or "").strip()
            last = str(p.get("lastName", {}).get("default", "") or "").strip()
            name = f"{name} {last}".strip()
        if not name:
            continue

        def _num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        g = _num(p.get("goals"))
        a = _num(p.get("assists"))
        pts = _num(p.get("points"))
        sog = _num(p.get("sog") if p.get("sog") is not None else p.get("shots"))
        hits = _num(p.get("hits"))
        blocks = _num(p.get("blockedShots") if p.get("blockedShots") is not None else p.get("blocks"))
        pim = _num(p.get("pim"))
        plus_minus = _num(p.get("plusMinus"))
        fow = _num(p.get("faceoffWins") if p.get("faceoffWins") is not None else p.get("faceoffsWon"))

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
        }
        for prop_type, actual in prop_map.items():
            if actual is None:
                continue
            rows.append({
                "player": name,
                "team": team_abbr,
                "prop_type": prop_type,
                "actual": round(float(actual), 1),
            })
    return rows


def fetch_nhl_api_enrichment(date_str):
    """
    Pull NHL player box stats from api-web.nhle.com and return long-format rows.
    This is especially important for Hits and Faceoffs Won coverage.
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
def fetch_soccer(date_str):
    """Fetch completed soccer games across all tracked leagues for date_str."""
    date_espn = date_str.replace("-", "")
    all_rows = []
    seen_event_ids = set()

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
    if args.sport not in ("NBA", "CBB", "NHL"):
        return
    try:
        from espn_injuries import injuries_csv_path_for_actuals, write_injuries_for_date
    except ImportError:
        return
    try:
        outp = injuries_csv_path_for_actuals(args.output, args.sport)
        n = write_injuries_for_date(args.sport, args.date, outp)
        print(f"  Injury report saved -> {outp}  ({n} rows)")
    except Exception as e:
        print(f"  WARNING: injury export failed: {e}")


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sport',  default='NBA', choices=['NBA', 'CBB', 'NHL', 'Soccer'])
    ap.add_argument('--date',   default='', help='YYYY-MM-DD (default: yesterday)')
    ap.add_argument('--output', default='')
    ap.add_argument('--window', default=2, type=int,
                    help='CBB only: days either side of target date to fetch (default: 2, use 0 for single-date)')
    args = ap.parse_args()

    if not args.date:
        args.date = (date.today() - timedelta(days=1)).strftime('%Y-%m-%d')
    if not args.output:
        args.output = f'actuals_{args.sport.lower()}_{args.date}.csv'

    print(f"\n=== {args.sport} actuals for {args.date} ===\n")

    empty_reason = "pending"
    if args.sport == 'NHL':
        df = fetch_nhl(args.date)
    elif args.sport == 'Soccer':
        df = fetch_soccer(args.date)
    else:
        sport_path = 'nba' if args.sport == 'NBA' else 'mens-college-basketball'
        df, empty_reason = fetch_sport(sport_path, args.date, window=args.window)

    if df.empty:
        # Zero NBA/CBB games on the calendar: write header-only CSV so combined_ticket_grader
        # and run_grader.ps1 can still run (CBB off-days are common in March).
        if empty_reason == "no_games" and args.sport in ("NBA", "CBB"):
            stub = pd.DataFrame(columns=["player", "team", "prop_type", "actual"])
            stub.to_csv(args.output, index=False)
            print(f"\nNo games scheduled — wrote empty actuals stub -> {args.output}")
            _export_injuries_sidecar(args)
            return
        _export_injuries_sidecar(args)
        print("\nNo actuals fetched — games may not be final yet.")
        print("Try again after all games have finished (usually safe by 1am ET).")
        return

    df.to_csv(args.output, index=False)
    _export_injuries_sidecar(args)
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
              f"--slate NHL\\step8_nhl_direction_clean.xlsx "
              f"--actuals {args.output} --output nhl_graded_{args.date}.xlsx")
    elif args.sport == 'Soccer':
        print(f"  py -3 slate_grader.py --sport Soccer "
              f"--slate Soccer\\step8_soccer_direction_clean.xlsx "
              f"--actuals {args.output} --output soccer_graded_{args.date}.xlsx")
    else:
        print(f"  py -3 grade_cbb_full_slate.py "
              f"--slate CBB\\step6_ranked_cbb.xlsx "
              f"--actuals {args.output} --output cbb_graded_{args.date}.xlsx")


if __name__ == '__main__':
    main()

