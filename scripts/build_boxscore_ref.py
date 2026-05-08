#!/usr/bin/env python3
"""
build_boxscore_ref.py — PropOracle Universal Boxscore Reference DB

Pulls ESPN boxscores for one date across all 4 sports and appends them
to a SQLite database at data/cache/proporacle_ref.db.

Called by run_grader.ps1 as Step 0 before any grading runs.

Usage:
    py -3 scripts/build_boxscore_ref.py --date 2026-03-09
    py -3 scripts/build_boxscore_ref.py --date 2026-03-09 --sports nba cbb
    py -3 scripts/build_boxscore_ref.py --date 2026-03-09 --sports soccer
    py -3 scripts/build_boxscore_ref.py --backfill --days 30   # rebuild last 30 days

Schema (one table per sport, all in proporacle_ref.db):

  nba / cbb:
    game_date, event_id, league, home_team, away_team,
    player, team, position, espn_athlete_id,
    pts, reb, ast, stl, blk, tov, fgm, fga, fg3m, fg3a, fg2m, fg2a,
    ftm, fta, oreb, dreb, pf, minutes,
    pra, pr, pa, ra, bs, fantasy_score

  nhl:
    game_date, event_id, home_team, away_team,
    player, team, position,
    goals, assists, points, shots_on_goal, hits, blocked_shots,
    pim, plus_minus, pp_points, faceoffs_won, toi

  soccer:
    game_date, event_id, league, home_team, away_team,
    player, team, espn_player_id,
    sh, sog, g, a, sv, pa, kp, tk, fc, yc, minutes_played

Primary key on all tables: (event_id, player, team)
→ re-running the same date is always safe (INSERT OR REPLACE)
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import threading

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional, TypeVar

import requests

# ── Config ────────────────────────────────────────────────────────────────────
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "cache" / "proporacle_ref.db"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# requests.Session is not documented as thread-safe; use one session per worker thread.
_thread_local = threading.local()


def _http_session() -> requests.Session:
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update(HEADERS)
        _thread_local.session = s
    return s


def _summary_fetch_workers() -> int:
    raw = os.environ.get("PROPORACLE_ESPN_SUMMARY_WORKERS", "").strip()
    if raw.isdigit():
        return max(1, min(12, int(raw)))
    return 6


T = TypeVar("T")


def _parallel_flatmap(fn: Callable[[T], list], items: list[T]) -> list:
    """Run I/O-bound per-item work in a small thread pool; flatten list results."""
    if not items:
        return []
    workers = min(_summary_fetch_workers(), len(items))
    if workers <= 1:
        out: list = []
        for it in items:
            out.extend(fn(it))
        return out
    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fn, it) for it in items]
        for fut in as_completed(futs):
            out.extend(fut.result())
    return out


# ── ESPN URL templates ─────────────────────────────────────────────────────────
NBA_SCOREBOARD  = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date}&limit=100&page={page}"
NBA_SUMMARY     = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={event_id}"
CBB_SCOREBOARD  = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates={date}&limit=100&groups={group}&page={page}"
CBB_SUMMARY     = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/summary?event={event_id}"
NHL_SCOREBOARD  = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates={date}"
NHL_SUMMARY     = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/summary?event={event_id}"
SOC_SCOREBOARD  = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={date}"
SOC_SUMMARY     = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/summary?event={event_id}"

# Soccer leagues to cover
SOCCER_LEAGUES = [
    ("eng.1",          "EPL"),
    ("esp.1",          "La Liga"),
    ("ger.1",          "Bundesliga"),
    ("ita.1",          "Serie A"),
    ("fra.1",          "Ligue 1"),
    ("usa.1",          "MLS"),
    ("uefa.champions", "UCL"),
    ("uefa.nations",   "UEFA Nations League"),
    ("fifa.worldq.uefa", "FIFA World Cup Qualifying - UEFA"),
    ("arg.1",          "Argentina"),
    ("bra.1",          "Brasileirao"),
    ("mex.1",          "Liga MX"),
]

# CBB conference group IDs (same as fetch_actuals.py)
CBB_CONF_GROUPS = [
    2, 4, 8, 80, 9009, 9510, 9, 22, 10, 8570,
    24, 25, 26, 27, 28, 29, 36, 37, 40, 44,
    45, 46, 48, 49, 50, 56, 59, 60, 62, None,
]

ESPN_TO_SLATE_ABBREV = {
    "NCSU": "NCST", "TA&M": "TXAM", "MIZ": "MIZZ",
    "OLEM": "MISS", "NWST": "NW",   "OU":  "OKLA",
    "SC":   "SCAR", "BOIS": "BSU",
}

# ── DB Setup ───────────────────────────────────────────────────────────────────
CREATE_NBA_CBB = """
CREATE TABLE IF NOT EXISTS {table} (
    game_date    TEXT NOT NULL,
    event_id     TEXT NOT NULL,
    league       TEXT,
    home_team    TEXT,
    away_team    TEXT,
    player       TEXT NOT NULL,
    team         TEXT,
    position     TEXT,
    espn_athlete_id  TEXT,
    minutes      REAL,
    pts          REAL, reb   REAL, ast   REAL,
    stl          REAL, blk   REAL, tov   REAL,
    fgm          REAL, fga   REAL,
    fg3m         REAL, fg3a  REAL,
    fg2m         REAL, fg2a  REAL,
    ftm          REAL, fta   REAL,
    oreb         REAL, dreb  REAL,
    pf           REAL,
    pra          REAL, pr    REAL,
    pa           REAL, ra    REAL,
    bs           REAL, fantasy_score REAL,
    PRIMARY KEY (event_id, player, team)
);
"""

CREATE_NHL = """
CREATE TABLE IF NOT EXISTS nhl (
    game_date       TEXT NOT NULL,
    event_id        TEXT NOT NULL,
    home_team       TEXT,
    away_team       TEXT,
    player          TEXT NOT NULL,
    team            TEXT,
    position        TEXT,
    goals           REAL, assists       REAL,
    points          REAL, shots_on_goal REAL,
    hits            REAL, blocked_shots REAL,
    pim             REAL, plus_minus    REAL,
    pp_points       REAL, faceoffs_won  REAL,
    toi             REAL,
    PRIMARY KEY (event_id, player, team)
);
"""

CREATE_SOCCER = """
CREATE TABLE IF NOT EXISTS soccer (
    game_date       TEXT NOT NULL,
    event_id        TEXT NOT NULL,
    league          TEXT,
    home_team       TEXT,
    away_team       TEXT,
    player          TEXT NOT NULL,
    team            TEXT,
    espn_player_id  TEXT,
    sh              REAL, sog REAL,
    g               REAL, a   REAL,
    sv              REAL, pa  REAL,
    kp              REAL, tk  REAL,
    fc              REAL, yc  REAL,
    minutes_played  REAL,
    clearances      REAL,
    dribble_attempts REAL,
    PRIMARY KEY (event_id, player, team)
);
"""

CREATE_DEFENSE = """
CREATE TABLE IF NOT EXISTS defense (
    sport            TEXT NOT NULL,
    team             TEXT NOT NULL,
    -- NBA/CBB/WNBA columns
    TEAM_NAME        TEXT,
    OVERALL_DEF_RANK REAL,
    OPP_PPG          TEXT,
    -- NHL/shared columns
    opp_gaa          REAL,
    opp_saa          REAL,
    opp_pk_pct       REAL,
    opp_gf_per_game  REAL,
    opp_sf_per_game  REAL,
    opp_pp_pct       REAL,
    opp_wins         REAL,
    opp_gp           REAL,
    def_rank         REAL,
    def_tier         TEXT,
    -- Soccer columns
    pp_name          TEXT,
    league           TEXT,
    -- Generic overflow: any extra columns stored as JSON
    extra_json       TEXT,
    updated_at       TEXT,
    PRIMARY KEY (sport, team)
);
"""

CREATE_PLAYER_IDS = """
CREATE TABLE IF NOT EXISTS player_ids (
    sport            TEXT NOT NULL,
    player           TEXT NOT NULL,
    team             TEXT,
    espn_athlete_id  TEXT,
    nba_player_id    TEXT,
    pp_player_name   TEXT,
    updated_at       TEXT,
    PRIMARY KEY (sport, player, team)
);
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_nba_player    ON nba    (player, game_date);",
    "CREATE INDEX IF NOT EXISTS idx_nba_date       ON nba    (game_date);",
    "CREATE INDEX IF NOT EXISTS idx_cbb_player    ON cbb    (player, game_date);",
    "CREATE INDEX IF NOT EXISTS idx_cbb_date       ON cbb    (game_date);",
    "CREATE INDEX IF NOT EXISTS idx_nhl_player     ON nhl    (player, game_date);",
    "CREATE INDEX IF NOT EXISTS idx_nhl_date       ON nhl    (game_date);",
    "CREATE INDEX IF NOT EXISTS idx_soccer_player  ON soccer (player, game_date);",
    "CREATE INDEX IF NOT EXISTS idx_soccer_date    ON soccer (game_date);",
    "CREATE INDEX IF NOT EXISTS idx_soccer_espnid  ON soccer (espn_player_id, game_date);",
    "CREATE INDEX IF NOT EXISTS idx_nba_espnid     ON nba    (espn_athlete_id, game_date);",
    "CREATE INDEX IF NOT EXISTS idx_cbb_espnid     ON cbb    (espn_athlete_id, game_date);",
    "CREATE INDEX IF NOT EXISTS idx_defense_sport  ON defense (sport, team);",
    "CREATE INDEX IF NOT EXISTS idx_playerids_espn ON player_ids (espn_athlete_id);",
    "CREATE INDEX IF NOT EXISTS idx_playerids_sport ON player_ids (sport, player);",
]


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL;")  # safe concurrent reads
    con.execute("PRAGMA synchronous=NORMAL;")
    con.execute(CREATE_NBA_CBB.format(table="nba"))
    con.execute(CREATE_NBA_CBB.format(table="cbb"))
    con.execute(CREATE_NHL)
    con.execute(CREATE_SOCCER)
    con.execute(CREATE_DEFENSE)
    con.execute(CREATE_PLAYER_IDS)
    for idx in CREATE_INDEXES:
        con.execute(idx)
    con.commit()
    _migrate_columns(con)  # add any columns missing from pre-existing tables
    return con


def _migrate_columns(con: sqlite3.Connection) -> None:
    """
    Add columns that may be missing from tables created before the current schema.
    Safe to run on every startup — ALTER TABLE is a no-op if the column already exists.
    """
    migrations = {
        "nba": [
            ("fg2m", "REAL"), ("fg2a", "REAL"), ("pf", "REAL"),
            ("oreb", "REAL"), ("dreb", "REAL"), ("pra", "REAL"),
            ("pr",   "REAL"), ("pa",   "REAL"), ("ra",  "REAL"),
            ("bs",   "REAL"), ("fantasy_score", "REAL"),
            ("espn_athlete_id", "TEXT"),
        ],
        "cbb": [
            ("fg2m", "REAL"), ("fg2a", "REAL"), ("pf", "REAL"),
            ("oreb", "REAL"), ("dreb", "REAL"), ("pra", "REAL"),
            ("pr",   "REAL"), ("pa",   "REAL"), ("ra",  "REAL"),
            ("bs",   "REAL"), ("fantasy_score", "REAL"),
            ("espn_athlete_id", "TEXT"),
        ],
        "soccer": [
            ("clearances", "REAL"),
            ("dribble_attempts", "REAL"),
        ],
    }
    for table, cols in migrations.items():
        for col, col_type in cols:
            try:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                print(f"  migrated: {table}.{col}")
            except Exception:
                pass
    con.commit()


# ── Defense table helpers ──────────────────────────────────────────────────────

def upsert_defense(con: sqlite3.Connection, sport: str, df_defense: "pd.DataFrame") -> int:
    """
    Write a defense report DataFrame into the defense table.
    sport: 'nba' | 'cbb' | 'nhl' | 'soccer' | 'wnba'
    The DataFrame should come straight from the defense_report output CSV.
    Returns number of rows upserted.
    """
    import json, pandas as pd
    from datetime import datetime

    known_cols = {
        "TEAM_NAME", "OVERALL_DEF_RANK", "OPP_PPG",
        "opp_gaa", "opp_saa", "opp_pk_pct", "opp_gf_per_game",
        "opp_sf_per_game", "opp_pp_pct", "opp_wins", "opp_gp",
        "def_rank", "def_tier", "DEF_TIER", "pp_name", "league",
    }

    # Find team key column
    team_key = next(
        (c for c in ["pp_name", "team", "TEAM_ABBREVIATION", "team_abbr", "abbr", "sr_name", "school", "team_name"]
         if c in df_defense.columns), None
    )
    if not team_key:
        print(f"  ⚠️  upsert_defense: no team key column found in {list(df_defense.columns)}")
        return 0

    ts = datetime.utcnow().isoformat()
    count = 0
    for _, row in df_defense.iterrows():
        team = str(row[team_key]).strip().upper()
        if not team:
            continue
        extra = {k: v for k, v in row.items()
                 if k not in known_cols and k != team_key and pd.notna(v)}
        con.execute("""
            INSERT OR REPLACE INTO defense
              (sport, team, TEAM_NAME, OVERALL_DEF_RANK, OPP_PPG,
               opp_gaa, opp_saa, opp_pk_pct, opp_gf_per_game,
               opp_sf_per_game, opp_pp_pct, opp_wins, opp_gp,
               def_rank, def_tier, pp_name, league,
               extra_json, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            sport, team,
            _str_or_none(row.get("TEAM_NAME")),
            _float_or_none(row.get("OVERALL_DEF_RANK")),
            _str_or_none(row.get("OPP_PPG")),
            _float_or_none(row.get("opp_gaa")),
            _float_or_none(row.get("opp_saa")),
            _float_or_none(row.get("opp_pk_pct")),
            _float_or_none(row.get("opp_gf_per_game")),
            _float_or_none(row.get("opp_sf_per_game")),
            _float_or_none(row.get("opp_pp_pct")),
            _float_or_none(row.get("opp_wins")),
            _float_or_none(row.get("opp_gp")),
            _float_or_none(row.get("def_rank")),
            _str_or_none(row.get("def_tier") or row.get("DEF_TIER")),
            _str_or_none(row.get("pp_name")),
            _str_or_none(row.get("league")),
            json.dumps(extra) if extra else None,
            ts,
        ))
        count += 1
    con.commit()
    return count


def read_defense(con: sqlite3.Connection, sport: str) -> "pd.DataFrame":
    """
    Read defense table for a given sport into a DataFrame ready for step3 merges.
    Expands extra_json back into columns.
    """
    import json, pandas as pd
    rows = con.execute(
        "SELECT * FROM defense WHERE sport = ?", (sport,)
    ).fetchall()
    cols = [d[0] for d in con.execute("SELECT * FROM defense LIMIT 0").description]
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df
    # Expand extra_json
    def expand_extra(j):
        try:
            return json.loads(j) if j else {}
        except Exception:
            return {}
    extras = df["extra_json"].apply(expand_extra)
    extra_df = pd.DataFrame(list(extras), index=df.index)
    df = pd.concat([df.drop(columns=["extra_json"]), extra_df], axis=1)
    # Normalize team column for merge
    df["team"] = df["team"].astype(str).str.strip().str.upper()
    return df


def upsert_player_ids(con: sqlite3.Connection, sport: str, csv_path: "Path") -> int:
    """
    Seed player_ids table from an existing id map CSV.
    Expects columns: player, team, espn_athlete_id (+ optionally nba_player_id, pp_player_name).
    """
    import pandas as pd
    from datetime import datetime
    df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig").fillna("")
    ts = datetime.utcnow().isoformat()
    count = 0
    for _, row in df.iterrows():
        player = str(row.get("player", "")).strip()
        if not player:
            continue
        con.execute("""
            INSERT OR REPLACE INTO player_ids
              (sport, player, team, espn_athlete_id, nba_player_id, pp_player_name, updated_at)
            VALUES (?,?,?,?,?,?,?)
        """, (
            sport,
            player,
            str(row.get("team", "")).strip().upper(),
            str(row.get("espn_athlete_id", "")).strip() or None,
            str(row.get("nba_player_id", "")).strip() or None,
            str(row.get("pp_player_name", row.get("player", ""))).strip() or None,
            ts,
        ))
        count += 1
    con.commit()
    return count


def _float_or_none(v):
    try:
        f = float(v)
        return None if (f != f) else f  # NaN check
    except (TypeError, ValueError):
        return None


def _str_or_none(v):
    if v is None:
        return None
    s = str(v).strip()
    return s if s and s.lower() not in ("nan", "none", "") else None


# ── HTTP helpers ───────────────────────────────────────────────────────────────
def _get(url: str, retries: int = 3) -> Optional[dict]:
    for attempt in range(1, retries + 1):
        try:
            r = _http_session().get(url, timeout=20)
            if r.status_code == 429:
                wait = 30 * attempt
                print(f"    ⚠️  429 rate-limit — sleeping {wait}s")
                time.sleep(wait)
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries:
                time.sleep(1.5 * attempt)
            else:
                print(f"    ⚠️  fetch failed ({url[:80]}): {e}")
    return None


def _is_final(event: dict) -> bool:
    st = event.get("status", {}).get("type", {})
    return st.get("completed", False) or st.get("state", "") == "post"


def _team_names(event: dict) -> tuple[str, str]:
    """Return (home_abbr, away_abbr) from an ESPN event dict."""
    comps = event.get("competitions", [{}])
    competitors = comps[0].get("competitors", []) if comps else []
    home = away = ""
    for c in competitors:
        abbr = c.get("team", {}).get("abbreviation", "")
        if c.get("homeAway") == "home":
            home = abbr
        else:
            away = abbr
    return home, away


# ── NBA / CBB ─────────────────────────────────────────────────────────────────
def _parse_made_att(raw):
    try:
        s = str(raw).strip()
        m = re.match(r"^(\d+)\s*[-/]\s*(\d+)$", s)
        if m:
            return float(m.group(1)), float(m.group(2))
    except Exception:
        pass
    return None, None


def _parse_bball_boxscore(box: dict, event_id: str, game_date: str,
                           home: str, away: str, league: str) -> list[dict]:
    rows = []
    for bteam in box.get("boxscore", {}).get("players", []):
        t_raw = bteam.get("team", {}).get("abbreviation", "")
        t_abbr = ESPN_TO_SLATE_ABBREV.get(t_raw, t_raw)
        for sg in bteam.get("statistics", []):
            labels = sg.get("labels", [])
            for ath in sg.get("athletes", []):
                name = ath.get("athlete", {}).get("displayName", "")
                pos  = ath.get("athlete", {}).get("position", {})
                pos  = pos.get("abbreviation", "") if isinstance(pos, dict) else ""
                espn_athlete_id = str(ath.get("athlete", {}).get("id", "") or "").strip() or None
                stats_raw = ath.get("stats", [])
                if not stats_raw or all(s in ("--", "", None) for s in stats_raw):
                    continue

                raw_map = {}
                sm = {}
                for label, val in zip(labels, stats_raw):
                    raw_map[label] = val
                    try:
                        sm[label] = float(val)
                    except (ValueError, TypeError):
                        pass

                # Parse made-attempt fields
                for src_keys, made_key, att_key in [
                    (["FG", "FGM-A", "FGMA"],     "FGM", "FGA"),
                    (["3PT", "3FG", "3PTM-A"],     "3PM", "3PA"),
                    (["FT", "FTM-A"],              "FTM", "FTA"),
                    (["2PT", "2FG", "2PTM-A"],     "2PM", "2PA"),
                ]:
                    for k in src_keys:
                        m, a = _parse_made_att(raw_map.get(k))
                        if m is not None:
                            sm[made_key] = m
                            sm[att_key]  = a
                            break

                # Canonical aliases
                alias_map = {
                    "PTS": ["PTS"], "REB": ["REB", "TREB"], "AST": ["AST"],
                    "BLK": ["BLK"], "STL": ["STL"], "TO":  ["TO", "TOV"],
                    "FGM": ["FGM"], "FGA": ["FGA"], "3PM": ["3PM", "FG3M"],
                    "3PA": ["3PA", "FG3A"], "FTM": ["FTM"], "FTA": ["FTA"],
                    "2PM": ["2PM"], "2PA": ["2PA"],
                    "OREB": ["OREB"], "DREB": ["DREB"],
                    "PF":  ["PF", "FOULS"], "MIN": ["MIN"],
                }
                n = {}
                for canon, aliases in alias_map.items():
                    for alias in aliases:
                        if alias in sm:
                            n[canon] = sm[alias]
                            break

                if not n:
                    continue

                def v(k):
                    return n.get(k)

                pts = v("PTS"); reb = v("REB"); ast = v("AST")
                stl = v("STL"); blk = v("BLK"); tov = v("TO")
                fgm = v("FGM"); fga = v("FGA")
                fg3m = v("3PM"); fg3a = v("3PA")
                fg2m = v("2PM"); fg2a = v("2PA")
                ftm = v("FTM"); fta = v("FTA")
                oreb = v("OREB"); dreb = v("DREB")
                pf = v("PF"); mins = v("MIN")

                if fg2m is None and fgm is not None and fg3m is not None:
                    fg2m = fgm - fg3m
                if fg2a is None and fga is not None and fg3a is not None:
                    fg2a = fga - fg3a

                def combo(*vals):
                    return sum(vals) if all(x is not None for x in vals) else None

                pra = combo(pts, reb, ast)
                pr  = combo(pts, reb)
                pa  = combo(pts, ast)
                ra  = combo(reb, ast)
                bs  = combo(blk, stl)

                fs = None
                # PrizePicks NBA Fantasy Score formula:
                # PTS×1.0 + REB×1.2 + AST×1.5 + STL×3.0 + BLK×3.0 - TOV×1.0
                # No fg3m bonus, no double-double bonus (those are DraftKings, not PP)
                if all(x is not None for x in [pts, reb, ast, stl, blk, tov]):
                    fs = (pts * 1.0 + reb * 1.2 + ast * 1.5
                          + stl * 3.0 + blk * 3.0 - tov * 1.0)

                rows.append({
                    "game_date": game_date, "event_id": event_id,
                    "league": league, "home_team": home, "away_team": away,
                    "player": name, "team": t_abbr, "position": pos,
                    "espn_athlete_id": espn_athlete_id,
                    "minutes": mins,
                    "pts": pts, "reb": reb, "ast": ast, "stl": stl,
                    "blk": blk, "tov": tov, "fgm": fgm, "fga": fga,
                    "fg3m": fg3m, "fg3a": fg3a, "fg2m": fg2m, "fg2a": fg2a,
                    "ftm": ftm, "fta": fta, "oreb": oreb, "dreb": dreb,
                    "pf": pf, "pra": pra, "pr": pr, "pa": pa, "ra": ra,
                    "bs": bs, "fantasy_score": fs,
                })
    return rows


def fetch_nba(date_str: str, con: sqlite3.Connection) -> int:
    date_espn = date_str.replace("-", "")
    all_rows = []
    seen = set()
    page = 1
    while True:
        data = _get(NBA_SCOREBOARD.format(date=date_espn, page=page))
        if not data:
            break
        events = data.get("events", [])
        page_count = data.get("pageCount", 1)
        work: list[tuple[str, str, str]] = []
        for ev in events:
            eid = str(ev.get("id", ""))
            if eid in seen or not _is_final(ev):
                continue
            seen.add(eid)
            work.append((eid, *_team_names(ev)))

        def _nba_one(item: tuple[str, str, str]) -> list[dict]:
            eid, home, away = item
            box = _get(NBA_SUMMARY.format(event_id=eid))
            if not box:
                return []
            return _parse_bball_boxscore(box, eid, date_str, home, away, "NBA")

        page_rows = _parallel_flatmap(_nba_one, work)
        all_rows.extend(page_rows)
        new = len(page_rows)
        print(f"  NBA page {page}/{page_count}: {len(events)} events, {new} rows")
        if page >= page_count or not events:
            break
        page += 1

    return _upsert(con, "nba", all_rows)


def fetch_cbb(date_str: str, con: sqlite3.Connection) -> int:
    date_espn = date_str.replace("-", "")
    seen = set()
    all_rows = []

    for group_id in CBB_CONF_GROUPS:
        page = 1
        while True:
            url = CBB_SCOREBOARD.format(
                date=date_espn,
                group=group_id if group_id else "",
                page=page
            )
            if group_id is None:
                url = url.replace("&groups=", "")
            data = _get(url)
            if not data:
                break
            events = data.get("events", [])
            page_count = data.get("pageCount", 1)
            work: list[tuple[str, str, str]] = []
            for ev in events:
                eid = str(ev.get("id", ""))
                if eid in seen or not _is_final(ev):
                    continue
                seen.add(eid)
                work.append((eid, *_team_names(ev)))

            def _cbb_one(item: tuple[str, str, str]) -> list[dict]:
                eid, home, away = item
                box = _get(CBB_SUMMARY.format(event_id=eid))
                if not box:
                    return []
                return _parse_bball_boxscore(box, eid, date_str, home, away, "CBB")

            all_rows.extend(_parallel_flatmap(_cbb_one, work))
            if page >= page_count or not events:
                break
            page += 1
        time.sleep(0.2)

    print(f"  CBB: {len(seen)} games, {len(all_rows)} rows")
    return _upsert(con, "cbb", all_rows)


# ── NHL ───────────────────────────────────────────────────────────────────────
NHL_STAT_MAP = {
    "goals":         ["G", "GOALS"],
    "assists":       ["A", "ASSISTS"],
    "points":        ["PTS", "P"],
    "shots_on_goal": ["SOG", "S", "SHOTS"],
    "hits":          ["HIT", "HITS", "HT"],       # ESPN returns "HT"
    "blocked_shots": ["BS", "BKS", "BLOCKED"],
    "pim":           ["PIM"],
    "plus_minus":    ["PLUSMINUS", "+/-"],
    # Power-play points often appear as PPP, or split into PPG/PPA.
    "pp_points":     ["PPP", "PPPTS", "POWERPLAYPOINTS"],
    "pp_goals":      ["PPG", "POWERPLAYGOALS"],
    "pp_assists":    ["PPA", "POWERPLAYASSISTS"],
    "faceoffs_won":  ["FOW", "FW"],               # ESPN returns "FW"
    "toi":           ["TOI"],
}


def _parse_toi(v) -> float | None:
    """Convert ESPN TOI string 'MM:SS' to decimal minutes, or float passthrough."""
    if v is None:
        return None
    s = str(v).strip()
    if ":" in s:
        parts = s.split(":", 1)
        try:
            return int(parts[0]) + int(parts[1]) / 60.0
        except (ValueError, TypeError):
            return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _nhl_stat(lmap, key):
    for alias in NHL_STAT_MAP.get(key, [key.upper()]):
        norm = re.sub(r"[^A-Z0-9]", "", alias.upper())
        if norm in lmap:
            if key == "toi":
                return _parse_toi(lmap[norm])
            try:
                return float(lmap[norm])
            except (ValueError, TypeError):
                pass
    return None


def _parse_nhl_boxscore(box: dict, event_id: str, game_date: str,
                         home: str, away: str) -> list[dict]:
    rows = []
    for tb in box.get("boxscore", {}).get("players", []):
        t_abbr = tb.get("team", {}).get("abbreviation", "")
        for sg in tb.get("statistics", []):
            labels = sg.get("labels") or sg.get("keys") or []
            norm_labels = [re.sub(r"[^A-Z0-9]", "", str(l).upper()) for l in labels]
            for a in sg.get("athletes") or []:
                ath = a.get("athlete", {}) if isinstance(a, dict) else {}
                name = str(ath.get("displayName", "")).strip()
                pos  = ath.get("position", {})
                pos  = pos.get("abbreviation", "") if isinstance(pos, dict) else ""
                stats = a.get("stats") or []
                if not stats or all(s in ("--", "", None) for s in stats):
                    continue
                lmap = {lbl: stats[i] for i, lbl in enumerate(norm_labels) if i < len(stats)}

                g   = _nhl_stat(lmap, "goals")
                ast = _nhl_stat(lmap, "assists")
                pts = (g + ast) if g is not None and ast is not None else _nhl_stat(lmap, "points")
                sog = _nhl_stat(lmap, "shots_on_goal")
                hits = _nhl_stat(lmap, "hits")
                bs  = _nhl_stat(lmap, "blocked_shots")
                pim = _nhl_stat(lmap, "pim")
                pm  = _nhl_stat(lmap, "plus_minus")
                ppp = _nhl_stat(lmap, "pp_points")
                if ppp is None:
                    ppg = _nhl_stat(lmap, "pp_goals")
                    ppa = _nhl_stat(lmap, "pp_assists")
                    if ppg is not None or ppa is not None:
                        ppp = float(ppg or 0.0) + float(ppa or 0.0)
                fow = _nhl_stat(lmap, "faceoffs_won")
                toi = _nhl_stat(lmap, "toi")

                if all(x is None for x in [g, ast, sog, hits]):
                    continue

                rows.append({
                    "game_date": game_date, "event_id": event_id,
                    "home_team": home, "away_team": away,
                    "player": name, "team": t_abbr, "position": pos,
                    "goals": g, "assists": ast, "points": pts,
                    "shots_on_goal": sog, "hits": hits, "blocked_shots": bs,
                    "pim": pim, "plus_minus": pm, "pp_points": ppp,
                    "faceoffs_won": fow, "toi": toi,
                })
    return rows


def fetch_nhl(date_str: str, con: sqlite3.Connection) -> int:
    date_espn = date_str.replace("-", "")
    data = _get(NHL_SCOREBOARD.format(date=date_espn))
    if not data:
        return 0
    events = data.get("events", [])
    work: list[tuple[str, str, str]] = []
    for ev in events:
        eid = str(ev.get("id", ""))
        if not _is_final(ev):
            continue
        work.append((eid, *_team_names(ev)))

    def _nhl_one(item: tuple[str, str, str]) -> list[dict]:
        eid, home, away = item
        box = _get(NHL_SUMMARY.format(event_id=eid))
        if not box:
            return []
        return _parse_nhl_boxscore(box, eid, date_str, home, away)

    all_rows = _parallel_flatmap(_nhl_one, work)
    print(f"  NHL: {len(events)} events, {len(all_rows)} rows")
    return _upsert(con, "nhl", all_rows)


# ── Soccer ────────────────────────────────────────────────────────────────────
SOCCER_STAT_MAP = {
    "sog": ["SOG", "SOT", "SHOTSONTARGET", "ONTARGETSCORINGATT"],
    "sh":  ["SH", "TOTALSHOTS", "SHOTS", "SHT", "ATTSHOT"],
    "g":   ["G", "GOALS", "GL", "GLS"],
    "a":   ["A", "GOALASSISTS", "ASSISTS", "AST"],
    # Goalkeeper saves — ESPN uses SV; also index by name "saves" for robustness
    "sv":  ["SV", "SAVES", "SVS", "GOALSAVE", "GOALSAVED"],
    "pa":  ["PA", "TOTALPASS", "PASSES", "PS"],
    "kp":  ["KP", "KEYPASS", "KEYPASSES"],
    "tk":  ["TK", "TOTALTACKLE", "TACKLES", "TCKS", "TOTALTACKLES", "EFFECTIVETACKLES"],
    "fc":  ["FC", "FOULSCOMMITTED", "FL", "FOULS"],
    "yc":  ["YC", "YELLOWCARDS", "YELLOW"],
    "min": ["MIN", "MINSPLAYED", "MINUTESPLAYED", "TIMEPLAYED"],
    "app": ["APP", "APPEARANCES"],
    "sub": ["SUB", "SUBSTITUTIONS", "SUBS"],
    # Outfield defending / dribbling — only present on some ESPN payloads / stat shapes.
    "clr": [
        "CLR", "CL", "CLEARANCES", "CLEARANCE", "TOTALCLEARANCE", "EFFECTIVECLEARANCE",
        "DEFENDINGCLEARANCES", "DEFCLEARANCES",
    ],
    "drib": [
        "DRI", "DR", "DRB", "ATTEMPTEDDRIBBLES", "DRIBBLEATTEMPTS", "DRIBBLESATTEMPTED",
        "TAKEONS", "TAKEON", "TOTALDRIBBLES", "ONBALLCARRIES", "UNSUCCESSFULTAKEONS",
    ],
}


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def _build_soccer_lmap(stats_list: list) -> dict:
    """Build {NORM_ABBREV: float} — indexes by BOTH abbreviation AND name."""
    lmap = {}
    for stat in stats_list:
        if not isinstance(stat, dict):
            return {}  # flat-array format, signal caller
        abbr = stat.get("abbreviation") or ""
        name = stat.get("name") or ""
        val = stat.get("value")
        if val is None:
            val = stat.get("displayValue")
        if val is None:
            continue
        try:
            fval = float(val)
        except (TypeError, ValueError):
            sval = str(val).strip()
            # Handle soccer minute formats such as "90:00" or "90+4".
            mmss = re.match(r"^(\d{1,3}):\d{2}$", sval)
            plus = re.match(r"^(\d{1,3})\+\d{1,2}$", sval)
            if mmss:
                fval = float(mmss.group(1))
            elif plus:
                fval = float(plus.group(1))
            else:
                continue
        # Index by abbreviation (e.g. "SV") AND camelCase name (e.g. "saves")
        for key in (_norm(abbr), _norm(name)):
            if key:
                lmap[key] = fval
    return lmap


def _soc_stat(lmap: dict, key: str):
    for alias in SOCCER_STAT_MAP.get(key, [key.upper()]):
        k = _norm(alias)
        if k in lmap:
            return lmap[k]
    return None


def _soc_minutes_from_roster_entry(entry: dict, lmap: dict, default_full_minutes: float = 90.0) -> Optional[float]:
    """
    Derive minutes from roster-level context when explicit MIN is absent.
    This uses substitution metadata when provided, otherwise starter/appearance heuristics.
    """
    mins = _soc_stat(lmap, "min")
    if mins is not None:
        return float(mins)

    def _to_min(v) -> Optional[float]:
        if isinstance(v, bool) or v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        mmss = re.match(r"^(\d{1,3}):\d{2}$", s)
        plus = re.match(r"^(\d{1,3})\+\d{1,2}$", s)
        if mmss:
            return float(mmss.group(1))
        if plus:
            return float(plus.group(1))
        try:
            return float(s)
        except (TypeError, ValueError):
            return None

    starter = bool(entry.get("starter", False))
    sub_in_raw = entry.get("subbedIn")
    sub_out_raw = entry.get("subbedOut")
    sub_in_min = _to_min(sub_in_raw)
    sub_out_min = _to_min(sub_out_raw)

    if starter:
        if sub_out_min is not None:
            return max(0.0, sub_out_min)
        if sub_out_raw is False:
            return float(default_full_minutes)
    else:
        if sub_in_min is not None:
            end_min = sub_out_min if sub_out_min is not None else float(default_full_minutes)
            return max(0.0, end_min - sub_in_min)
        if sub_in_raw is False:
            return 0.0

    app = _soc_stat(lmap, "app")
    if app is not None:
        try:
            if float(app) <= 0:
                return 0.0
        except (TypeError, ValueError):
            pass
        # Common case: started and no sub-out event details in summary.
        if starter:
            return float(default_full_minutes)

    return None


def _parse_soccer_boxscore(box: dict, event_id: str, game_date: str,
                            home: str, away: str, league_id: str) -> list[dict]:
    rows = []

    def _emit(name, t_abbr, espn_id, lmap, minutes_override: Optional[float] = None):
        sh  = _soc_stat(lmap, "sh")
        sog = _soc_stat(lmap, "sog")
        g   = _soc_stat(lmap, "g")
        a   = _soc_stat(lmap, "a")
        sv  = _soc_stat(lmap, "sv")
        pa  = _soc_stat(lmap, "pa")
        kp  = _soc_stat(lmap, "kp")
        tk  = _soc_stat(lmap, "tk")
        fc  = _soc_stat(lmap, "fc")
        yc  = _soc_stat(lmap, "yc")
        mins = minutes_override if minutes_override is not None else _soc_stat(lmap, "min")
        clr = _soc_stat(lmap, "clr")
        drb = _soc_stat(lmap, "drib")
        if all(x is None for x in [sh, sog, g, a, sv, pa, kp, tk, fc, yc, mins, clr, drb]):
            return
        rows.append({
            "game_date": game_date, "event_id": event_id,
            "league": league_id, "home_team": home, "away_team": away,
            "player": name, "team": t_abbr, "espn_player_id": espn_id,
            "sh": sh, "sog": sog, "g": g, "a": a,
            "sv": sv, "pa": pa, "kp": kp, "tk": tk,
            "fc": fc, "yc": yc, "minutes_played": mins,
            "clearances": clr,
            "dribble_attempts": drb,
        })

    # PATH 1: box['rosters'] with stat objects
    rosters = box.get("rosters")
    if isinstance(rosters, list) and rosters:
        for tb in rosters:
            if not isinstance(tb, dict):
                continue
            t_abbr = tb.get("team", {}).get("abbreviation", "")
            for entry in (tb.get("roster") or []):
                if not isinstance(entry, dict):
                    continue
                ath    = entry.get("athlete", {})
                name   = str(ath.get("displayName", "")).strip()
                espn_id = str(ath.get("id", ""))
                if not name:
                    continue
                stats_list = entry.get("stats") or []
                if not stats_list:
                    continue
                lmap = _build_soccer_lmap(stats_list)
                if lmap:
                    mins_guess = _soc_minutes_from_roster_entry(entry, lmap, default_full_minutes=90.0)
                    _emit(name, t_abbr, espn_id, lmap, mins_guess)
        if rows:
            return rows

    # PATH 2: box['boxscore']['players'] flat arrays
    for tb in box.get("boxscore", {}).get("players", []) or []:
        if not isinstance(tb, dict):
            continue
        t_abbr = tb.get("team", {}).get("abbreviation", "")
        for sg in tb.get("statistics", []):
            plabels = sg.get("labels") or sg.get("keys") or []
            norm_labels = [_norm(l) for l in plabels]
            for a in sg.get("athletes") or []:
                ath = a.get("athlete", {}) if isinstance(a, dict) else {}
                name = str(ath.get("displayName", "")).strip()
                espn_id = str(ath.get("id", ""))
                flat = a.get("stats") or []
                if not flat or all(s in ("--", "", None) for s in flat):
                    continue
                lmap = {}
                for i, lbl in enumerate(norm_labels):
                    if i < len(flat):
                        try:
                            lmap[lbl] = float(flat[i])
                        except (TypeError, ValueError):
                            pass
                _emit(name, t_abbr, espn_id, lmap)

    return rows


def fetch_soccer(date_str: str, con: sqlite3.Connection) -> int:
    date_espn = date_str.replace("-", "")
    seen = set()
    all_rows = []
    for league_id, league_name in SOCCER_LEAGUES:
        data = _get(SOC_SCOREBOARD.format(league=league_id, date=date_espn))
        if not data:
            continue
        events = data.get("events", [])
        league_rows = 0
        work: list[tuple[str, str, str, str]] = []
        ev_by_eid: dict[str, dict] = {}
        for ev in events:
            eid = str(ev.get("id", ""))
            if eid in seen or not _is_final(ev):
                continue
            seen.add(eid)
            ev_by_eid[eid] = ev
            home, away = _team_names(ev)
            work.append((eid, home, away, league_id))

        def _soc_one(item: tuple[str, str, str, str]) -> list[dict]:
            eid, home, away, lid = item
            box = _get(SOC_SUMMARY.format(league=lid, event_id=eid))
            if not box:
                return []
            rows = _parse_soccer_boxscore(box, eid, date_str, home, away, lid)
            if not rows:
                ev = ev_by_eid.get(eid, {})
                rosters = box.get("rosters", [])
                if rosters:
                    first_entry = (rosters[0].get("roster") or [{}])[0]
                    abbrevs = [s.get("abbreviation", "?")
                               for s in (first_entry.get("stats") or [])
                               if isinstance(s, dict)]
                    print(f"    ⚠️  {ev.get('shortName','?')} — 0 rows. ESPN abbrevs: {abbrevs}")
            return rows

        batch = _parallel_flatmap(_soc_one, work)
        all_rows.extend(batch)
        league_rows = len(batch)
        if league_rows:
            print(f"  {league_name}: {len(events)} events, {league_rows} rows")
        time.sleep(0.3)

    print(f"  Soccer total: {len(seen)} games, {len(all_rows)} rows")
    return _upsert(con, "soccer", all_rows)


# ── Upsert ────────────────────────────────────────────────────────────────────
def _upsert(con: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ", ".join("?" * len(cols))
    col_names    = ", ".join(cols)
    sql = f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})"
    data = [[r.get(c) for c in cols] for r in rows]
    with con:
        con.executemany(sql, data)
    return len(rows)


# ── Query helpers (used by step4 and graders) ──────────────────────────────────
def query_player_soccer(con: sqlite3.Connection, espn_player_id: str,
                         stat_col: str, n: int = 10) -> list[float]:
    """
    Return up to n most-recent values of stat_col for a soccer player.
    Indexed by espn_player_id. Used by step4 to replace live ESPN calls.
    """
    cur = con.execute(f"""
        SELECT {stat_col}
        FROM soccer
        WHERE espn_player_id = ?
          AND {stat_col} IS NOT NULL
        ORDER BY game_date DESC
        LIMIT ?
    """, (str(espn_player_id), n))
    return [row[0] for row in cur.fetchall()]


def query_player_nba(con: sqlite3.Connection, player: str, team: str,
                      stat_col: str, n: int = 10) -> list[float]:
    cur = con.execute(f"""
        SELECT {stat_col}
        FROM nba
        WHERE player = ? AND team = ? AND {stat_col} IS NOT NULL
        ORDER BY game_date DESC
        LIMIT ?
    """, (player, team, n))
    return [row[0] for row in cur.fetchall()]


def query_player_nhl(con: sqlite3.Connection, player: str,
                      stat_col: str, n: int = 10) -> list[float]:
    cur = con.execute(f"""
        SELECT {stat_col}
        FROM nhl
        WHERE player = ? AND {stat_col} IS NOT NULL
        ORDER BY game_date DESC
        LIMIT ?
    """, (player, n))
    return [row[0] for row in cur.fetchall()]


def db_summary(con: sqlite3.Connection) -> None:
    print("\n── DB Summary ──────────────────────────────")
    for table in ("nba", "cbb", "nhl", "soccer"):
        try:
            total = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            dates = con.execute(
                f"SELECT MIN(game_date), MAX(game_date) FROM {table}"
            ).fetchone()
            players = con.execute(
                f"SELECT COUNT(DISTINCT player) FROM {table}"
            ).fetchone()[0]
            print(f"  {table:8s}  {total:>7,} rows  {players:>5,} players  "
                  f"{dates[0] or '—'} → {dates[1] or '—'}")
        except Exception:
            print(f"  {table:8s}  (table not found)")
    # Defense table
    try:
        rows = con.execute("SELECT sport, COUNT(*) FROM defense GROUP BY sport").fetchall()
        if rows:
            summary = "  ".join(f"{s}={n}" for s, n in sorted(rows))
            print(f"  defense   {summary}")
    except Exception:
        pass
    # Player IDs table
    try:
        rows = con.execute("SELECT sport, COUNT(*) FROM player_ids GROUP BY sport").fetchall()
        if rows:
            summary = "  ".join(f"{s}={n}" for s, n in sorted(rows))
            print(f"  player_ids  {summary}")
    except Exception:
        pass
    print()


# ── Main ───────────────────────────────────────────────────────────────────────
SPORT_FETCHERS = {
    "nba":    fetch_nba,
    "cbb":    fetch_cbb,
    "nhl":    fetch_nhl,
    "soccer": fetch_soccer,
}


def main():
    ap = argparse.ArgumentParser(
        description="PropOracle universal boxscore reference DB builder"
    )
    ap.add_argument("--date",     default="",
                    help="YYYY-MM-DD to fetch (default: yesterday)")
    ap.add_argument("--sports",   nargs="+",
                    default=["nba", "cbb", "nhl", "soccer"],
                    choices=list(SPORT_FETCHERS.keys()),
                    help="Sports to fetch (default: all 4)")
    ap.add_argument("--db",       default="",
                    help="Override DB path (default: data/cache/proporacle_ref.db)")
    ap.add_argument("--backfill", action="store_true",
                    help="Backfill mode: fetch --days days ending at --date")
    ap.add_argument("--days",     type=int, default=30,
                    help="Days to backfill (default: 30)")
    ap.add_argument("--summary",  action="store_true",
                    help="Print DB summary and exit")
    ap.add_argument("--seed-ids", default="",
                    help="Seed player_ids table from a CSV. Format: --seed-ids nba=path/to/nba_to_espn_id_map.csv")
    ap.add_argument("--upsert-defense", default="",
                    help="Write defense CSV into DB. Format: --upsert-defense nhl=path/to/nhl_defense_summary.csv")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else DB_PATH
    con = init_db(db_path)
    print(f"📦 DB: {db_path}")

    if args.summary:
        db_summary(con)
        return

    # ── Seed player IDs ──────────────────────────────────────────────────────
    if args.seed_ids:
        import pandas as pd
        for pair in args.seed_ids.split(","):
            pair = pair.strip()
            if "=" not in pair:
                print(f"  ⚠️  --seed-ids: expected sport=path, got '{pair}'")
                continue
            sport_key, csv_path = pair.split("=", 1)
            sport_key = sport_key.strip()
            csv_path  = csv_path.strip()
            if not Path(csv_path).exists():
                print(f"  ⚠️  --seed-ids: file not found: {csv_path}")
                continue
            n = upsert_player_ids(con, sport_key, Path(csv_path))
            print(f"  ✅ player_ids ({sport_key}): {n} rows upserted from {csv_path}")
        return

    # ── Upsert defense report ────────────────────────────────────────────────
    if args.upsert_defense:
        import pandas as pd
        for pair in args.upsert_defense.split(","):
            pair = pair.strip()
            if "=" not in pair:
                print(f"  ⚠️  --upsert-defense: expected sport=path, got '{pair}'")
                continue
            sport_key, csv_path = pair.split("=", 1)
            sport_key = sport_key.strip()
            csv_path  = csv_path.strip()
            if not Path(csv_path).exists():
                print(f"  ⚠️  --upsert-defense: file not found: {csv_path}")
                continue
            df_def = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig").fillna("")
            n = upsert_defense(con, sport_key, df_def)
            print(f"  ✅ defense ({sport_key}): {n} teams upserted from {csv_path}")
        return

    if not args.date:
        args.date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    if args.backfill:
        end_date   = date.fromisoformat(args.date)
        start_date = end_date - timedelta(days=args.days - 1)
        dates = [
            (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(args.days)
        ]
        print(f"🔄 Backfill mode: {args.days} days ({dates[0]} → {dates[-1]})")
    else:
        dates = [args.date]

    total_by_sport = {s: 0 for s in args.sports}

    for d in dates:
        print(f"\n{'='*55}")
        print(f"  Date: {d}")
        print(f"{'='*55}")
        for sport in args.sports:
            print(f"\n→ {sport.upper()}")
            n = SPORT_FETCHERS[sport](d, con)
            total_by_sport[sport] += n
            print(f"  ✅ {n} rows upserted")

    print(f"\n{'='*55}")
    print("  DONE")
    for sport, n in total_by_sport.items():
        print(f"  {sport.upper():8s}  {n:,} rows total")
    db_summary(con)


if __name__ == "__main__":
    main()
