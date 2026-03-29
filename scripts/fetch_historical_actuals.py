#!/usr/bin/env python3
"""
Fetch player game logs from ESPN and NHL public APIs into historical_actuals.db.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
from ensure_local_cache import ensure_local_cache

_cache_dir = Path(ensure_local_cache(str(Path(__file__).resolve().parents[1])))

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = _cache_dir / "historical_actuals.db"
GAME_LINES_DB = _cache_dir / "game_lines.db"
GRADED_DATE_RE = re.compile(r"graded_(nba|cbb|nhl|soccer)_(\d{4}-\d{2}-\d{2})\.xlsx$", re.I)
LOGS_DIR = REPO_ROOT / "logs"
ERROR_LOG = LOGS_DIR / "fetch_errors.log"

SEASON_CODES = (20232024, 20242025, 20252026)

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (compatible; PropOracleHistorical/1.0; +https://github.com/)",
        "Accept": "application/json",
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_error(msg: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{_now_iso()} {msg}\n"
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalize_player_key(name: str) -> str:
    s = _strip_accents(str(name or "").lower().strip())
    s = re.sub(r"\b(jr\.?|sr\.?|ii|iii|iv|v)\b\.?", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def season_code_to_label(code: int) -> str:
    y1, y2 = code // 10000, code % 10000
    return f"{y1}-{str(y2)[2:]}"


def current_season_code() -> int:
    now = datetime.now(timezone.utc)
    y, m = now.year, now.month
    start = y if m >= 10 else y - 1
    return start * 10000 + (start + 1)


def espn_season_year_param(season_code: int) -> int:
    """ESPN basketball/soccer gamelog ?season= uses end calendar year (e.g. 2025 for 2024-25)."""
    return season_code % 10000


def http_get(url: str, params: dict[str, Any] | None = None, timeout: float = 45.0) -> requests.Response | None:
    try:
        r = SESSION.get(url, params=params or {}, timeout=timeout)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(5.0)
            r = SESSION.get(url, params=params or {}, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                _log_error(f"HTTP {r.status_code} after retry: {url} params={params}")
                return None
        if not r.ok:
            _log_error(f"HTTP {r.status_code}: {url} params={params}")
            return None
        return r
    except requests.RequestException as ex:
        _log_error(f"Request error {url}: {ex}")
        return None


def find_myticket_db() -> Path | None:
    for p in (
        REPO_ROOT / "MyTicketPerformance.db",
        REPO_ROOT / "data" / "MyTicketPerformance.db",
        REPO_ROOT / "data" / "cache" / "MyTicketPerformance.db",
    ):
        if p.is_file():
            return p
    return None


def load_players_from_entry_legs() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    dbp = find_myticket_db()
    if not dbp:
        return out
    try:
        with sqlite3.connect(str(dbp)) as conn:
            cur = conn.execute(
                "SELECT DISTINCT player_name, league FROM entry_legs WHERE player_name IS NOT NULL AND TRIM(player_name) != ''"
            )
            for name, league in cur.fetchall():
                sp = league_to_sport(str(league or ""))
                if sp:
                    out.append((str(name).strip(), sp))
    except sqlite3.Error as ex:
        _log_error(f"entry_legs read: {ex}")
    return out


def expand_combo_player_names(name: str) -> list[str]:
    """Split slate strings like 'Player A + Player B' into individual names."""
    s = str(name or "").strip()
    if not s:
        return []
    return [p.strip() for p in s.split("+") if p.strip()]


def league_to_sport(league: str) -> str | None:
    u = league.upper().strip()
    if not u:
        return None
    if "NBA" in u or u == "BASKETBALL":
        return "NBA"
    if "CBB" in u or "NCAAB" in u or "COLLEGE" in u or "NCAA" in u:
        return "CBB"
    if "NHL" in u or "HOCKEY" in u:
        return "NHL"
    if any(x in u for x in ("EPL", "MLS", "UCL", "LALIGA", "BUNDESLIGA", "SOCCER", "SERIE A")):
        return "Soccer"
    if u in ("SOC", "SOCcer"):
        return "Soccer"
    return None


def load_players_from_graded_workbooks() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    root = REPO_ROOT / "outputs"
    if not root.is_dir():
        return out
    for path in root.rglob("graded_*.xlsx"):
        if not path.is_file():
            continue
        sport = None
        low = path.name.lower()
        if "nba" in low:
            sport = "NBA"
        elif "cbb" in low:
            sport = "CBB"
        elif "nhl" in low:
            sport = "NHL"
        elif "soccer" in low:
            sport = "Soccer"
        if not sport:
            continue
        try:
            df = pd.read_excel(path, sheet_name=0, engine="openpyxl")
        except Exception:
            continue
        cols = {str(c).strip().lower().replace(" ", "_"): c for c in df.columns}
        pc = cols.get("player_name") or cols.get("player")
        if not pc:
            continue
        for val in df[pc].dropna().astype(str).unique():
            v = val.strip()
            if v:
                out.append((v, sport))
    return out


def load_players_from_step8_slates() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    patterns: list[tuple[Path, str]] = [
        (REPO_ROOT / "NBA" / "data" / "outputs", "NBA"),
        (REPO_ROOT / "CBB", "CBB"),
        (REPO_ROOT / "Soccer" / "outputs", "Soccer"),
        (REPO_ROOT / "NHL", "NHL"),
    ]
    globs = ("step8_*direction_clean*.xlsx", "step8_*direction*.xlsx", "*step8*clean*.xlsx")
    for base, sport in patterns:
        if not base.is_dir():
            continue
        for g in globs:
            for path in base.rglob(g):
                if not path.is_file():
                    continue
                _append_players_from_slate(path, sport, out)
    out_dir = REPO_ROOT / "outputs"
    if out_dir.is_dir():
        for path in out_dir.rglob("step8_*direction_clean*.xlsx"):
            if path.is_file():
                low = str(path).lower()
                sp = "NBA" if "nba" in low else "Soccer" if "soccer" in low else "NHL" if "nhl" in low else "CBB" if "cbb" in low else "NBA"
                _append_players_from_slate(path, sp, out)
    return out


def _append_players_from_slate(path: Path, default_sport: str, out: list[tuple[str, str]]) -> None:
    try:
        xl = pd.ExcelFile(path, engine="openpyxl")
    except Exception:
        return
    for sheet in xl.sheet_names:
        if str(sheet).upper() not in ("ALL", "SHEET1", "SLATE"):
            continue
        try:
            df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
        except Exception:
            continue
        if df.empty:
            continue
        cols = {str(c).strip().lower().replace(" ", "_"): c for c in df.columns}
        pc = (
            cols.get("player_name")
            or cols.get("player")
            or cols.get("pp_player")
            or cols.get("player_norm")
        )
        if not pc:
            continue
        sp_col = cols.get("sport")
        for _, r in df.iterrows():
            name = str(r.get(pc) or "").strip()
            if not name:
                continue
            sp = default_sport
            if sp_col:
                s = str(r.get(sp_col) or "").strip().upper()
                if s in ("NBA", "CBB", "NHL", "SOCCER"):
                    sp = "Soccer" if s == "SOCCER" else s
            out.append((name, sp))


def merge_player_list(
    a: list[tuple[str, str]], b: list[tuple[str, str]], c: list[tuple[str, str]]
) -> dict[str, set[str]]:
    by_sport: dict[str, set[str]] = {"NBA": set(), "CBB": set(), "NHL": set(), "Soccer": set()}
    for name, sp in a + b + c:
        if sp not in by_sport or not name:
            continue
        for token in expand_combo_player_names(name):
            by_sport[sp].add(token)
    return by_sport


def _exit_sqlite_disk_help(detail: str = "") -> None:
    print()
    print("ERROR: SQLite could not access historical_actuals.db (disk I/O or lock failure).")
    if detail:
        print(f"Detail: {detail}")
    print()
    print("This often happens when the repo is inside a OneDrive-synced folder: OneDrive can")
    print("lock files during sync, which breaks SQLite — especially with WAL mode (-wal / -shm files).")
    print()
    print("Try one of the following:")
    print("  1. Pause OneDrive syncing temporarily, then run this script again.")
    print("  2. Exclude the folder PropORACLE\\data\\cache from OneDrive sync (Choose folders).")
    print("  3. Move the repository to a path that is not synced by OneDrive.")
    print()
    sys.exit(1)


def _guard_historical_actuals_db(db_path: Path) -> None:
    """Probe DB locks/I-O; remove stale -wal/-shm when no other SQLite user; exit cleanly on failure."""
    s = str(db_path)
    wal_path = Path(f"{s}-wal")
    shm_path = Path(f"{s}-shm")

    try:
        conn = sqlite3.connect(s, timeout=2.5)
    except sqlite3.OperationalError as e:
        _exit_sqlite_disk_help(str(e))
    except sqlite3.Error as e:
        _exit_sqlite_disk_help(str(e))

    try:
        try:
            conn.execute("BEGIN EXCLUSIVE")
            conn.execute("COMMIT")
        except sqlite3.OperationalError as e:
            _exit_sqlite_disk_help(str(e))
    finally:
        conn.close()

    for sidecar in (wal_path, shm_path):
        if sidecar.is_file():
            try:
                sidecar.unlink()
            except OSError:
                pass

    try:
        conn2 = sqlite3.connect(s, timeout=2.5)
    except sqlite3.OperationalError as e:
        _exit_sqlite_disk_help(str(e))
    except sqlite3.Error as e:
        _exit_sqlite_disk_help(str(e))

    try:
        try:
            conn2.execute("PRAGMA journal_mode=DELETE")
        except sqlite3.OperationalError as e:
            _exit_sqlite_disk_help(str(e))
    finally:
        conn2.close()


def create_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS player_game_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_name TEXT NOT NULL,
            player_id TEXT,
            sport TEXT NOT NULL,
            game_date TEXT NOT NULL,
            season TEXT,
            opponent TEXT,
            home_away TEXT,
            minutes REAL,
            points REAL,
            rebounds REAL,
            assists REAL,
            steals REAL,
            blocks REAL,
            turnovers REAL,
            threes_made REAL,
            fta REAL,
            fg_attempted REAL,
            fantasy_score REAL,
            pra REAL,
            pts_asts REAL,
            pts_rebs REAL,
            rebs_asts REAL,
            blks_stls REAL,
            shots REAL,
            saves REAL,
            goals REAL,
            passes_attempted REAL,
            tackles REAL,
            shots_on_target REAL,
            raw_json TEXT,
            source TEXT,
            created_at TEXT,
            UNIQUE(player_name, sport, game_date)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pgl_player_sport ON player_game_logs (player_name, sport)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pgl_season ON player_game_logs (sport, season)"
    )


def _parse_made_attempted(cell: str | None) -> tuple[float | None, float | None]:
    if cell is None:
        return None, None
    s = str(cell).strip()
    if "-" in s:
        parts = s.split("-", 1)
        try:
            a, b = float(parts[0]), float(parts[1])
            return a, b
        except ValueError:
            return None, None
    try:
        v = float(s)
        return v, None
    except ValueError:
        return None, None


def _parse_minutes(cell: str | None) -> float | None:
    if cell is None:
        return None
    s = str(cell).strip()
    if ":" in s:
        try:
            mm, ss = s.split(":", 1)
            return float(mm) + float(ss) / 60.0
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _safe_float(x: Any) -> float | None:
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _bb_compute_row(
    player_name: str,
    sport: str,
    season_label: str,
    game_date: str,
    opponent: str | None,
    home_away: str | None,
    stats: list[str],
    names: list[str],
    raw: dict[str, Any],
    player_id: str,
) -> dict[str, Any]:
    idx = {n: i for i, n in enumerate(names)}
    def g(name: str) -> str | None:
        i = idx.get(name)
        if i is None or i >= len(stats):
            return None
        return stats[i]

    minutes = _parse_minutes(g("minutes"))
    pts = _safe_float(g("points"))
    reb = _safe_float(g("totalRebounds"))
    ast = _safe_float(g("assists"))
    stl = _safe_float(g("steals"))
    blk = _safe_float(g("blocks"))
    tov = _safe_float(g("turnovers"))
    _3pm, _3pa = _parse_made_attempted(g("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
    ftm, fta = _parse_made_attempted(g("freeThrowsMade-freeThrowsAttempted"))
    fgm, fga = _parse_made_attempted(g("fieldGoalsMade-fieldGoalsAttempted"))
    threes_made = _3pm
    fantasy = None
    if pts is not None and reb is not None and ast is not None and stl is not None and blk is not None and tov is not None:
        fantasy = pts * 1.0 + reb * 1.2 + ast * 1.5 + stl * 3.0 + blk * 3.0 + tov * -1.0
    pra = pts + reb + ast if all(v is not None for v in (pts, reb, ast)) else None
    pa = pts + ast if all(v is not None for v in (pts, ast)) else None
    pr = pts + reb if all(v is not None for v in (pts, reb)) else None
    ra = reb + ast if all(v is not None for v in (reb, ast)) else None
    bs = None
    if blk is not None and stl is not None:
        bs = blk + stl
    return {
        "player_name": player_name,
        "player_id": player_id,
        "sport": sport,
        "game_date": game_date,
        "season": season_label,
        "opponent": opponent,
        "home_away": home_away,
        "minutes": minutes,
        "points": pts,
        "rebounds": reb,
        "assists": ast,
        "steals": stl,
        "blocks": blk,
        "turnovers": tov,
        "threes_made": threes_made,
        "fta": fta,
        "fg_attempted": fga,
        "fantasy_score": fantasy,
        "pra": pra,
        "pts_asts": pa,
        "pts_rebs": pr,
        "rebs_asts": ra,
        "blks_stls": bs,
        "shots": None,
        "saves": None,
        "goals": None,
        "passes_attempted": None,
        "tackles": None,
        "shots_on_target": None,
        "raw_json": json.dumps(raw, default=str)[:65000],
        "source": "espn",
        "created_at": _now_iso(),
    }


def parse_espn_basketball_gamelog(
    j: dict[str, Any], player_name: str, sport: str, season_label: str, player_id: str
) -> list[dict[str, Any]]:
    names = j.get("names") or []
    events_meta = j.get("events") or {}
    if isinstance(events_meta, list):
        events_meta = {str(e.get("id")): e for e in events_meta if isinstance(e, dict) and e.get("id")}
    rows: list[dict[str, Any]] = []
    for st in j.get("seasonTypes") or []:
        for cat in st.get("categories") or []:
            for ev_row in cat.get("events") or []:
                eid = str(ev_row.get("eventId") or "")
                stats = ev_row.get("stats") or []
                if not eid or eid not in events_meta or not stats:
                    continue
                meta = events_meta[eid]
                gd = meta.get("gameDate") or ""
                if not gd:
                    continue
                try:
                    game_date = str(pd.Timestamp(gd).date())
                except Exception:
                    continue
                opp = (meta.get("opponent") or {}).get("displayName")
                av = meta.get("atVs")
                home_away = "HOME" if av == "vs" else "AWAY" if av == "@" else None
                raw = {"eventId": eid, "meta": meta, "stats": stats, "seasonType": st.get("displayName")}
                row = _bb_compute_row(
                    player_name, sport, season_label, game_date, opp, home_away, stats, names, raw, player_id
                )
                rows.append(row)
    return rows


def _score_name_match(target_norm: str, candidate_norm: str) -> float:
    if target_norm == candidate_norm:
        return 1.0
    if target_norm in candidate_norm or candidate_norm in target_norm:
        return 0.92
    ta, tb = set(target_norm.split()), set(candidate_norm.split())
    if ta and tb:
        return len(ta & tb) / max(len(ta), len(tb))
    return 0.0


def search_site_espn_basketball_id(player_name: str, sport: str) -> tuple[str | None, str | None]:
    """
    Resolve ESPN athlete id via site.api.espn.com search.
    The core API .../athletes?search= is not relevance-ranked (returns an alphabetical slice),
    so almost all NBA lookups failed.
    """
    target = normalize_player_key(player_name)
    r = http_get(
        "https://site.api.espn.com/apis/common/v3/search",
        params={"query": player_name, "limit": 20, "type": "player"},
    )
    if not r:
        return None, None
    try:
        data = r.json()
    except json.JSONDecodeError:
        return None, None

    def best_in_leagues(leagues: tuple[str, ...]) -> tuple[str | None, str | None, float]:
        best_id: str | None = None
        best_name: str | None = None
        best_sc = 0.0
        leagues_l = tuple(x.lower() for x in leagues)
        for it in data.get("items") or []:
            if not isinstance(it, dict):
                continue
            if str(it.get("type") or "") != "player":
                continue
            lg = str(it.get("league") or "").lower()
            if lg not in leagues_l:
                continue
            full = str(it.get("displayName") or "").strip()
            pid = str(it.get("id") or "")
            if not full or not pid:
                continue
            sc = _score_name_match(target, normalize_player_key(full))
            if sc > best_sc:
                best_sc = sc
                best_id, best_name = pid, full
        return best_id, best_name, best_sc

    if sport == "NBA":
        pid, full, sc = best_in_leagues(("nba",))
        if sc >= 0.5:
            return pid, full
        return None, None

    pid, full, sc = best_in_leagues(("mens-college-basketball",))
    if sc >= 0.5:
        return pid, full
    pid, full, sc = best_in_leagues(("nba",))
    if sc >= 0.5:
        return pid, full
    return None, None


def fetch_basketball_season(
    espn_id: str, sport: str, season_code: int, player_name: str
) -> list[dict[str, Any]]:
    league = "nba" if sport == "NBA" else "mens-college-basketball"
    year = espn_season_year_param(season_code)
    url = f"https://site.web.api.espn.com/apis/common/v3/sports/basketball/{league}/athletes/{espn_id}/gamelog"
    r = http_get(url, params={"season": year})
    if not r:
        return []
    try:
        j = r.json()
    except json.JSONDecodeError:
        return []
    label = season_code_to_label(season_code)
    return parse_espn_basketball_gamelog(j, player_name, sport, label, espn_id)


def search_soccer_espn_id(player_name: str) -> tuple[str | None, str | None]:
    r = http_get(
        "https://site.api.espn.com/apis/common/v3/search",
        params={"query": player_name, "limit": 8, "type": "player"},
    )
    if not r:
        return None, None
    try:
        data = r.json()
    except json.JSONDecodeError:
        return None, None
    target = normalize_player_key(player_name)
    best_id, best_name = None, None
    best = 0.0
    for it in data.get("items") or []:
        if not isinstance(it, dict):
            continue
        if str(it.get("type") or "") != "player":
            continue
        full = str(it.get("displayName") or "").strip()
        pid = str(it.get("id") or "")
        if not full or not pid:
            continue
        nk = normalize_player_key(full)
        sc = 1.0 if nk == target else 0.8 if target in nk or nk in target else 0.0
        if sc > best:
            best, best_id, best_name = sc, pid, full
    if best < 0.5:
        return None, None
    return best_id, best_name


def parse_espn_soccer_gamelog(
    j: dict[str, Any], player_name: str, season_label: str, player_id: str
) -> list[dict[str, Any]]:
    names = j.get("names") or []
    events_meta = j.get("events") or {}
    if not isinstance(events_meta, dict):
        events_meta = {}
    rows: list[dict[str, Any]] = []
    for st in j.get("seasonTypes") or []:
        for cat in st.get("categories") or []:
            for ev_row in cat.get("events") or []:
                eid = str(ev_row.get("eventId") or "")
                stats = ev_row.get("stats") or []
                if not eid or eid not in events_meta or not stats:
                    continue
                meta = events_meta[eid]
                gd = meta.get("gameDate") or ""
                if not gd:
                    continue
                try:
                    game_date = str(pd.Timestamp(gd).date())
                except Exception:
                    continue
                opp = (meta.get("opponent") or {}).get("displayName")
                av = meta.get("atVs")
                home_away = "HOME" if av == "vs" else "AWAY" if av == "@" else None
                idx = {n: i for i, n in enumerate(names)}

                def gv(key: str) -> float | None:
                    i = idx.get(key)
                    if i is None or i >= len(stats):
                        return None
                    return _safe_float(stats[i])

                goals = gv("totalGoals")
                ast = gv("goalAssists")
                shots = gv("totalShots")
                sot = gv("shotsOnTarget")
                raw = {"eventId": eid, "stats": stats}
                rows.append(
                    {
                        "player_name": player_name,
                        "player_id": player_id,
                        "sport": "Soccer",
                        "game_date": game_date,
                        "season": season_label,
                        "opponent": opp,
                        "home_away": home_away,
                        "minutes": None,
                        "points": None,
                        "rebounds": None,
                        "assists": ast,
                        "steals": None,
                        "blocks": None,
                        "turnovers": None,
                        "threes_made": None,
                        "fta": None,
                        "fg_attempted": None,
                        "fantasy_score": None,
                        "pra": None,
                        "pts_asts": None,
                        "pts_rebs": None,
                        "rebs_asts": None,
                        "blks_stls": None,
                        "shots": shots,
                        "saves": None,
                        "goals": goals,
                        "passes_attempted": None,
                        "tackles": None,
                        "shots_on_target": sot,
                        "raw_json": json.dumps(raw, default=str)[:65000],
                        "source": "espn",
                        "created_at": _now_iso(),
                    }
                )
    return rows


def fetch_soccer_season(espn_id: str, season_code: int, player_name: str) -> list[dict[str, Any]]:
    year = espn_season_year_param(season_code)
    url = f"https://site.web.api.espn.com/apis/common/v3/sports/soccer/athletes/{espn_id}/gamelog"
    r = http_get(url, params={"season": year})
    if not r:
        return []
    try:
        j = r.json()
    except json.JSONDecodeError:
        return []
    return parse_espn_soccer_gamelog(j, player_name, season_code_to_label(season_code), espn_id)


def search_nhl_player_id(player_name: str) -> tuple[str | None, str | None]:
    q = quote(player_name)
    r = http_get(f"https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=8&q={q}")
    if not r:
        return None, None
    try:
        data = r.json()
    except json.JSONDecodeError:
        return None, None
    if not isinstance(data, list) or not data:
        return None, None
    target = normalize_player_key(player_name)
    best = None
    best_sc = -1.0
    for row in data:
        if not isinstance(row, dict):
            continue
        nm = str(row.get("name") or "")
        pid = str(row.get("playerId") or "")
        active = row.get("active")
        nk = normalize_player_key(nm)
        sc = 2.0 if nk == target else 1.0 if (target in nk or nk in target) else 0.0
        if active:
            sc += 0.5
        if sc > best_sc:
            best_sc, best = sc, (pid, nm)
    if not best or best_sc < 1.0:
        return None, None
    return best[0], best[1]


def _parse_toi_minutes(toi: str | None) -> float | None:
    if not toi:
        return None
    s = str(toi).strip()
    if ":" in s:
        try:
            mm, ss = s.split(":", 1)
            return float(mm) + float(ss) / 60.0
        except ValueError:
            return None
    return _safe_float(s)


def fetch_nhl_season(player_id: str, season_code: int, player_name: str) -> list[dict[str, Any]]:
    url = f"https://api-web.nhle.com/v1/player/{player_id}/game-log/{season_code}/2"
    r = http_get(url)
    if not r:
        return []
    try:
        j = r.json()
    except json.JSONDecodeError:
        return []
    label = season_code_to_label(season_code)
    rows: list[dict[str, Any]] = []
    for g in j.get("gameLog") or []:
        if not isinstance(g, dict):
            continue
        gd = str(g.get("gameDate") or "")[:10]
        if not gd:
            continue
        opp = None
        oc = g.get("opponentCommonName")
        if isinstance(oc, dict):
            opp = oc.get("default")
        hr = g.get("homeRoadFlag")
        home_away = "HOME" if hr == "H" else "AWAY" if hr == "R" else None
        goals = _safe_float(g.get("goals"))
        ast = _safe_float(g.get("assists"))
        pts = _safe_float(g.get("points"))
        shots = _safe_float(g.get("shots"))
        mins = _parse_toi_minutes(g.get("toi"))
        raw = dict(g)
        rows.append(
            {
                "player_name": player_name,
                "player_id": str(player_id),
                "sport": "NHL",
                "game_date": gd,
                "season": label,
                "opponent": opp,
                "home_away": home_away,
                "minutes": mins,
                "points": pts,
                "rebounds": None,
                "assists": ast,
                "steals": None,
                "blocks": None,
                "turnovers": None,
                "threes_made": None,
                "fta": None,
                "fg_attempted": None,
                "fantasy_score": None,
                "pra": None,
                "pts_asts": (pts + ast) if pts is not None and ast is not None else None,
                "pts_rebs": None,
                "rebs_asts": None,
                "blks_stls": None,
                "shots": shots,
                "saves": _safe_float(g.get("saves")) if g.get("saves") is not None else None,
                "goals": goals,
                "passes_attempted": None,
                "tackles": None,
                "shots_on_target": None,
                "raw_json": json.dumps(raw, default=str)[:65000],
                "source": "nhle",
                "created_at": _now_iso(),
            }
        )
    return rows


def season_rows_exist(conn: sqlite3.Connection, player: str, sport: str, season_label: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM player_game_logs WHERE player_name = ? AND sport = ? AND season = ? LIMIT 1",
        (player, sport, season_label),
    )
    return cur.fetchone() is not None


def upsert_game_log(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    cols = [
        "player_name",
        "player_id",
        "sport",
        "game_date",
        "season",
        "opponent",
        "home_away",
        "minutes",
        "points",
        "rebounds",
        "assists",
        "steals",
        "blocks",
        "turnovers",
        "threes_made",
        "fta",
        "fg_attempted",
        "fantasy_score",
        "pra",
        "pts_asts",
        "pts_rebs",
        "rebs_asts",
        "blks_stls",
        "shots",
        "saves",
        "goals",
        "passes_attempted",
        "tackles",
        "shots_on_target",
        "raw_json",
        "source",
        "created_at",
    ]
    placeholders = ",".join("?" * len(cols))
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("player_name", "sport", "game_date"))
    sql = f"""
        INSERT INTO player_game_logs ({",".join(cols)})
        VALUES ({placeholders})
        ON CONFLICT(player_name, sport, game_date) DO UPDATE SET
        {updates}
    """
    conn.execute(sql, tuple(row.get(c) for c in cols))


def fetch_player_sport(
    conn: sqlite3.Connection,
    player: str,
    sport: str,
    seasons: tuple[int, ...],
    refresh_current: bool,
    since_date: str | None,
) -> tuple[int, str]:
    """Returns (games_stored, status_line)."""
    current = current_season_code()
    total = 0
    parts: list[str] = []
    if sport in ("NBA", "CBB"):
        eid, matched = search_site_espn_basketball_id(player, sport)
        if not eid:
            _log_error(f"No ESPN id for {sport} player={player!r}")
            return 0, f"WARNING: Could not find ESPN ID for {player}"
        time.sleep(0.5)
        for sc in seasons:
            label = season_code_to_label(sc)
            if not refresh_current and sc != current and season_rows_exist(conn, player, sport, label):
                time.sleep(0.2)
                continue
            time.sleep(0.2)
            rows = fetch_basketball_season(eid, sport, sc, matched or player)
            if since_date:
                rows = [r for r in rows if r["game_date"] >= since_date]
            for r in rows:
                upsert_game_log(conn, r)
                total += 1
            parts.append(f"{label} ({len(rows)} games)")
        conn.commit()
        return total, f"{matched or player}: " + ", ".join(parts)
    if sport == "NHL":
        pid, matched = search_nhl_player_id(player)
        if not pid:
            _log_error(f"No NHL id for player={player!r}")
            return 0, f"WARNING: Could not find NHL player ID for {player}"
        time.sleep(0.5)
        for sc in seasons:
            label = season_code_to_label(sc)
            if not refresh_current and sc != current and season_rows_exist(conn, player, sport, label):
                time.sleep(0.2)
                continue
            time.sleep(0.2)
            rows = fetch_nhl_season(pid, sc, matched or player)
            if since_date:
                rows = [r for r in rows if r["game_date"] >= since_date]
            for r in rows:
                upsert_game_log(conn, r)
                total += 1
            parts.append(f"{label} ({len(rows)} games)")
        conn.commit()
        return total, f"{matched or player}: " + ", ".join(parts)
    if sport == "Soccer":
        eid, matched = search_soccer_espn_id(player)
        if not eid:
            _log_error(f"No ESPN soccer id for player={player!r}")
            return 0, f"WARNING: Could not find ESPN soccer ID for {player}"
        time.sleep(0.5)
        for sc in seasons:
            label = season_code_to_label(sc)
            if not refresh_current and sc != current and season_rows_exist(conn, player, sport, label):
                time.sleep(0.2)
                continue
            time.sleep(0.2)
            rows = fetch_soccer_season(eid, sc, matched or player)
            if since_date:
                rows = [r for r in rows if r["game_date"] >= since_date]
            for r in rows:
                upsert_game_log(conn, r)
                total += 1
            parts.append(f"{label} ({len(rows)} games)")
        conn.commit()
        return total, f"{matched or player}: " + ", ".join(parts)
    return 0, f"{player}: unknown sport"


def collect_graded_dates_by_sport() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {k: set() for k in ("NBA", "CBB", "NHL", "Soccer")}
    od = REPO_ROOT / "outputs"
    if not od.is_dir():
        return out
    for p in od.rglob("graded_*.xlsx"):
        m = GRADED_DATE_RE.search(p.name)
        if not m:
            continue
        raw_league = m.group(1).upper()
        sp = "Soccer" if raw_league == "SOCCER" else raw_league
        if sp in out:
            out[sp].add(m.group(2))
    return out


def fetch_historical_spreads(sport: str | None = None, refresh: bool = False) -> None:
    """Backfill game_lines.db for each date seen in graded workbooks under outputs/."""
    import fetch_game_lines as fgl

    by_sport = collect_graded_dates_by_sport()
    if sport:
        su = sport.strip().upper()
        sports = ["Soccer"] if su == "SOCCER" else [su]
    else:
        sports = ["NBA", "CBB", "NHL", "Soccer"]
    total_dates = 0
    for sp in sports:
        dates = sorted(by_sport.get(sp, set()))
        if not dates:
            print(f"  [spreads] {sp}: no graded file dates found under outputs/")
            continue
        print(f"  [spreads] {sp}: fetching {len(dates)} dates …")
        for d in dates:
            fgl.run_fetch(d, [sp], refresh)
            total_dates += 1
            time.sleep(0.35)
    print(f"  [spreads] Done. game_lines.db → {GAME_LINES_DB} (touched ~{total_dates} sport-date fetches)")


def summarize_db(conn: sqlite3.Connection) -> dict[str, tuple[int, int, str | None]]:
    """sport -> (players, games, date_range)."""
    out: dict[str, tuple[int, int, str | None]] = {}
    for sp in ("NBA", "CBB", "NHL", "Soccer"):
        n_games = conn.execute(
            "SELECT COUNT(*) FROM player_game_logs WHERE sport = ?", (sp,)
        ).fetchone()[0]
        n_players = conn.execute(
            "SELECT COUNT(DISTINCT player_name) FROM player_game_logs WHERE sport = ?", (sp,)
        ).fetchone()[0]
        rng = conn.execute(
            "SELECT MIN(game_date), MAX(game_date) FROM player_game_logs WHERE sport = ?", (sp,)
        ).fetchone()
        dr = f"{rng[0]} to {rng[1]}" if rng[0] else None
        out[sp] = (int(n_players), int(n_games), dr)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch historical player game logs into SQLite.")
    ap.add_argument("--sport", choices=("NBA", "CBB", "NHL", "Soccer"), default=None)
    ap.add_argument("--players", default=None, help="Comma-separated player names (optional filter)")
    ap.add_argument("--since", default=None, help="Only store games on/after YYYY-MM-DD")
    ap.add_argument("--refresh-current", action="store_true", help="Re-fetch current season even if cached")
    ap.add_argument("--spreads", action="store_true", help="Backfill game_lines.db from ESPN for graded slate dates")
    ap.add_argument(
        "--refresh-spreads",
        action="store_true",
        help="With --spreads, re-fetch even when a sport-date row already exists in game_lines.db",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=12,
        help="Parallel fetch workers (default: 12; max: 20). Higher = faster but more ESPN load.",
    )
    args = ap.parse_args()

    if args.spreads:
        fetch_historical_spreads(args.sport, refresh=args.refresh_spreads)
        return

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _guard_historical_actuals_db(DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        create_db(conn)
        src1 = load_players_from_entry_legs()
        src2 = load_players_from_graded_workbooks()
        src3 = load_players_from_step8_slates()
        by_sport = merge_player_list(src1, src2, src3)
        if args.players:
            raw_names = {n.strip() for n in args.players.split(",") if n.strip()}
            names: set[str] = set()
            for n in raw_names:
                names.update(expand_combo_player_names(n))
            by_sport = {sp: {p for p in ps if p in names} for sp, ps in by_sport.items()}
        if args.sport:
            by_sport = {k: v for k, v in by_sport.items() if k == args.sport}

        err_before = ERROR_LOG.read_text(encoding="utf-8").count("\n") if ERROR_LOG.is_file() else 0

        print("Unique players by sport (sources merged):")
        for sp in ("NBA", "CBB", "NHL", "Soccer"):
            print(f"  {sp}: {len(by_sport.get(sp, set()))}")

        seasons = SEASON_CODES
        current = current_season_code()
        players_fetched = 0
        games_stored = 0
        sport_order = ("NBA", "CBB", "NHL", "Soccer")
        if args.sport:
            sport_order = (args.sport,)

        # ── Parallel fetch with per-thread SQLite connections ──────────────────
        # Each worker opens its own connection so there's no shared-state risk.
        # A print lock keeps console output readable.
        _print_lock = threading.Lock()
        workers = max(1, min(getattr(args, "workers", 12), 20))

        def _fetch_one(sp: str, player: str, i: int, total: int) -> tuple[int, str]:
            """Worker: open own DB connection, fetch, commit, close."""
            try:
                wconn = sqlite3.connect(str(DB_PATH), timeout=30)
                try:
                    n, line = fetch_player_sport(
                        wconn, player, sp, seasons,
                        args.refresh_current, args.since
                    )
                    return n, line
                finally:
                    wconn.close()
            except Exception as ex:
                _log_error(f"fetch_player_sport {sp} {player!r}: {ex}")
                return 0, f"WARNING: Fetch failed for {player} (details in {ERROR_LOG.name})"

        for sp in sport_order:
            plist = sorted(by_sport.get(sp, set()))
            if not plist:
                continue
            print(f"\nFetching {sp}: {len(plist)} players across {len(seasons)} seasons "
                  f"(workers={workers})...")
            total = len(plist)
            completed = 0

            with ThreadPoolExecutor(max_workers=workers) as exe:
                futures = {
                    exe.submit(_fetch_one, sp, player, i + 1, total): (i + 1, player)
                    for i, player in enumerate(plist)
                }
                for fut in as_completed(futures):
                    i, player = futures[fut]
                    completed += 1
                    try:
                        n, line = fut.result()
                    except Exception as ex:
                        n, line = 0, f"WARNING: Fetch failed for {player}: {ex}"
                    if n > 0:
                        players_fetched += 1
                    games_stored += n
                    with _print_lock:
                        print(f"  [{completed}/{total}] {line}")

        summ = summarize_db(conn)
        err_after = ERROR_LOG.read_text(encoding="utf-8").count("\n") if ERROR_LOG.is_file() else 0
        skipped = max(0, err_after - err_before)

        print("\n--- Summary ---")
        print(f"Total players fetched (with >=1 new row this run approx): see per-player lines")
        print(f"Total game log rows touched this run: {games_stored}")
        for sp in ("NBA", "CBB", "NHL", "Soccer"):
            np_, ng, dr = summ[sp]
            print(f"  {sp}: {np_} players, {ng} games ({dr or 'n/a'})")
        print(f"  Errors/skipped lines in log: see {ERROR_LOG} (approx +{skipped} new lines)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
