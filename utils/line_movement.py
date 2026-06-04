"""
Odds API line movement snapshot + slate enrichment (NHL pilot).

Requires ODDS_API_KEY in the environment (set in repo-root `.env` for local runs).
Never raises into pipeline callers — returns empty snapshot / default columns on failure.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.player_name_utils import normalize_player_name

_log = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_CACHE_DIR = _REPO_ROOT / "cache"

# Sport presets (Tennis omitted — no player props on Odds API)
# Player props require per-event /events/{id}/odds (bulk /odds returns 422 for NHL).
SPORT_LINE_MOVEMENT_PRESETS: dict[str, dict[str, Any]] = {
    "NHL": {
        "sport_key": "icehockey_nhl",
        "markets": [
            "player_shots_on_goal",
            "player_goals",
            "player_assists",
            "player_points",
            "player_power_play_points",
            "player_goal_scorer_anytime",
            "player_blocked_shots",
        ],
    },
    "NBA": {
        "sport_key": "basketball_nba",
        "markets": ["player_points", "player_rebounds", "player_assists", "player_threes"],
    },
    "MLB": {
        "sport_key": "baseball_mlb",
        "markets": ["batter_hits", "batter_home_runs", "pitcher_strikeouts"],
    },
    "WNBA": {
        "sport_key": "basketball_wnba",
        "markets": ["player_points", "player_rebounds", "player_assists"],
    },
    "Soccer": {
        "sport_key": "soccer_epl",
        "markets": ["player_shots_on_target", "player_to_score"],
    },
}

# Pipeline stat_norm / prop_type → Odds API market keys.
# Diagnosis (2026-05): open_line IS stored in the daily snapshot cache; enrich_with_line_movement
# joins it via prop_norm → odds market. MLB/WNBA/Soccer failed lookup because only NHL keys
# were mapped — movement defaulted to 0.0 while open_line stayed null.
PIPELINE_PROP_TO_ODDS_MARKET: dict[str, str] = {
    # NHL
    "shots_on_goal": "player_shots_on_goal",
    "shots": "player_shots_on_goal",
    "sog": "player_shots_on_goal",
    "goal_scorer": "player_goal_scorer_anytime",
    "anytime_goal": "player_goal_scorer_anytime",
    "anytime_goal_scorer": "player_goal_scorer_anytime",
    "assists": "player_assists",
    "points": "player_points",
    "power_play_points": "player_power_play_points",
    "blocked_shots": "player_blocked_shots",
    # MLB (prop_norm → Odds API batter_/pitcher_ markets)
    "hits": "batter_hits",
    "total_bases": "batter_total_bases",
    "home_runs": "batter_home_runs",
    "rbis": "batter_rbis",
    "hitter_strikeouts": "batter_strikeouts",
    "strikeouts": "pitcher_strikeouts",
    "hits_allowed": "pitcher_hits_allowed",
    "walks_allowed": "pitcher_walks",
    "walks": "pitcher_walks",
    # WNBA (PrizePicks abbreviations)
    "pts": "player_points",
    "reb": "player_rebounds",
    "ast": "player_assists",
    "3ptmade": "player_threes",
    "threes": "player_threes",
    "pra": "player_points_rebounds_assists",
    "pa": "player_points_assists",
    # Soccer
    "shots_on_target": "player_shots_on_target",
    "goal_assist": "player_assists",
    "anytime_scorer": "player_to_score",
    "to_score": "player_to_score",
}

# Sport-specific prop_norm overrides (resolve cross-sport name collisions).
SPORT_PROP_TO_ODDS_MARKET: dict[str, dict[str, str]] = {
    "icehockey_nhl": {
        "goals": "player_goals",
        "shots": "player_shots_on_goal",
    },
    "soccer_epl": {
        "goals": "player_to_score",
        "shots": "player_shots_on_target",
    },
}

BM_PRIORITY = ("draftkings", "fanduel", "betmgm", "caesars", "pointsbetus", "bovada")
_LINE_EPS = 0.05

NHL_TEAM_ABBREV: dict[str, str] = {
    "anaheim ducks": "ANA",
    "arizona coyotes": "ARI",
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
    "st louis blues": "STL",
    "st. louis blues": "STL",
    "tampa bay lightning": "TBL",
    "toronto maple leafs": "TOR",
    "utah hockey club": "UTA",
    "vancouver canucks": "VAN",
    "vegas golden knights": "VGK",
    "washington capitals": "WSH",
    "winnipeg jets": "WPG",
}


def _bootstrap_env_from_dotenv() -> None:
    """Load repo-root .env into os.environ when ODDS_API_KEY is not already set."""
    if (os.getenv("ODDS_API_KEY") or "").strip():
        return
    env_path = _REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if not key or key in os.environ:
                continue
            os.environ[key] = val.strip().strip('"').strip("'")
    except OSError as exc:
        _log.debug("line_movement: .env read skipped: %s", exc)


def _odds_api_key() -> str:
    _bootstrap_env_from_dotenv()
    return (os.getenv("ODDS_API_KEY") or "").strip()


def _cache_path(sport_key: str, day: str) -> Path:
    safe = sport_key.replace("/", "_")
    return _CACHE_DIR / f"line_movement_{safe}_{day}.json"


def _totals_cache_path(sport_key: str, day: str) -> Path:
    safe = sport_key.replace("/", "_")
    return _CACHE_DIR / f"game_totals_{safe}_{day}.json"


def _snapshot_cache_key(player: str, market: str, line: float) -> str:
    return f"{player}|{market}|{line:.4g}"


def _parse_snapshot_key(key: str) -> tuple[str, str, float] | None:
    parts = key.split("|", 2)
    if len(parts) != 3:
        return None
    try:
        return parts[0], parts[1], float(parts[2])
    except ValueError:
        return None


def _serialize_snapshot(data: dict[tuple[str, str, float], dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, val in data.items():
        if isinstance(key, tuple) and len(key) == 3:
            sk = _snapshot_cache_key(key[0], key[1], float(key[2]))
        else:
            sk = str(key)
        out[sk] = val
    return out


def _deserialize_snapshot(raw: dict[str, Any]) -> dict[tuple[str, str, float], dict[str, Any]]:
    out: dict[tuple[str, str, float], dict[str, Any]] = {}
    for key, val in (raw or {}).items():
        parsed = _parse_snapshot_key(str(key))
        if parsed and isinstance(val, dict):
            out[parsed] = val
    return out


def _load_cache(sport_key: str, today: str) -> dict[tuple[str, str, float], dict[str, Any]] | None:
    path = _cache_path(sport_key, today)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if str(payload.get("date", ""))[:10] != today:
            return None
        return _deserialize_snapshot(payload.get("snapshot", {}))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        _log.warning("line_movement: cache read failed (%s): %s", path, exc)
        return None


def _merge_line_snapshot(
    prior: dict[tuple[str, str, float], dict[str, Any]],
    fresh: dict[tuple[str, str, float], dict[str, Any]],
) -> dict[tuple[str, str, float], dict[str, Any]]:
    """Preserve open_line from the first snapshot of the day; refresh current_line/movement."""
    out = dict(prior)
    for key, new_row in fresh.items():
        old_row = out.get(key)
        old_open = old_row.get("open_line") if isinstance(old_row, dict) else None
        if old_open is not None:
            try:
                open_line = float(old_open)
                current_line = float(new_row.get("current_line", open_line))
            except (TypeError, ValueError):
                out[key] = new_row
                continue
            movement = round(current_line - open_line, 3)
            merged = _snapshot_row_defaults(new_row)
            merged["open_line"] = open_line
            merged["current_line"] = current_line
            merged["line_movement"] = movement
            merged["line_direction_shift"] = _line_direction(open_line, current_line)
            if merged.get("over_price") is None and isinstance(old_row, dict):
                merged["over_price"] = old_row.get("over_price")
            if merged.get("under_price") is None and isinstance(old_row, dict):
                merged["under_price"] = old_row.get("under_price")
            out[key] = merged
        else:
            out[key] = _snapshot_row_defaults(new_row)
    return out


def _save_cache(sport_key: str, today: str, snapshot: dict[tuple[str, str, float], dict[str, Any]]) -> None:
    path = _cache_path(sport_key, today)
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"date": today, "sport_key": sport_key, "snapshot": _serialize_snapshot(snapshot)}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        _log.warning("line_movement: cache write failed (%s): %s", path, exc)


def _http_get_json(url: str, timeout: int = 25) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "PropOracle/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        remaining = resp.headers.get("x-requests-remaining", "?")
        used = resp.headers.get("x-requests-used", "?")
        _log.info("line_movement: Odds API quota used=%s remaining=%s", used, remaining)
        return json.loads(resp.read().decode("utf-8"))


def _is_target_event(event: dict) -> bool:
    """True if commence_time is within the next 48 hours (UTC), inclusive of now."""
    commence_time = event.get("commence_time", "")
    if not commence_time:
        return True
    try:
        ts = datetime.fromisoformat(str(commence_time).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts = ts.astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        return now <= ts <= now + timedelta(hours=48)
    except (ValueError, TypeError):
        return True


def _bm_sort_key(bookmaker: dict) -> int:
    key = str(bookmaker.get("key", "")).lower()
    for i, pref in enumerate(BM_PRIORITY):
        if pref in key:
            return i
    return 99


def _line_direction(open_line: float, current_line: float) -> str:
    delta = current_line - open_line
    if delta > _LINE_EPS:
        return "moved_up"
    if delta < -_LINE_EPS:
        return "moved_down"
    return "stable"


def _norm_name(x: str) -> str:
    import unicodedata

    s = str(x or "").strip().lower()
    if not s:
        return ""
    n = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in n if not unicodedata.combining(ch))


def _team_to_abbrev(name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""
    norm = _norm_name(raw)
    if norm in NHL_TEAM_ABBREV:
        return NHL_TEAM_ABBREV[norm]
    parts = [p for p in raw.replace(".", "").split() if p]
    if not parts:
        return ""
    return parts[-1][:3].upper()


def _american_to_prob(price: Any) -> float | None:
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p == 0:
        return None
    if p > 0:
        return 100.0 / (p + 100.0)
    if p < 0:
        return abs(p) / (abs(p) + 100.0)
    return None


def _american_to_implied(american: Any) -> float | None:
    """American odds → implied probability in [0, 1]. Alias of _american_to_prob."""
    return _american_to_prob(american)


def _snapshot_row_defaults(row: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize cache/API snapshot rows; old caches may omit price fields."""
    if not isinstance(row, dict):
        return {
            "open_line": None,
            "current_line": None,
            "line_movement": 0.0,
            "line_direction_shift": "stable",
            "over_price": None,
            "under_price": None,
        }
    return {
        "open_line": row.get("open_line"),
        "current_line": row.get("current_line", row.get("open_line")),
        "line_movement": row.get("line_movement", 0.0),
        "line_direction_shift": row.get("line_direction_shift", "stable"),
        "over_price": row.get("over_price"),
        "under_price": row.get("under_price"),
    }


def fetch_game_totals(sport_key: str) -> dict[tuple[str, str], dict[str, float]]:
    """
    Fetch implied team totals from bulk odds endpoint (markets=totals,h2h).
    Returns {(home_abbrev, away_abbrev): {"game_total": x, "home_implied": y, "away_implied": z}}
    """
    api_key = _odds_api_key()
    today = date.today().isoformat()
    if not api_key:
        return {}

    path = _totals_cache_path(sport_key, today)
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if str(payload.get("date", ""))[:10] == today and isinstance(payload.get("totals"), dict):
                out: dict[tuple[str, str], dict[str, float]] = {}
                for k, v in payload["totals"].items():
                    if not isinstance(v, dict) or "|" not in k:
                        continue
                    h, a = k.split("|", 1)
                    out[(h, a)] = v
                return out
        except Exception:
            pass

    params = urllib.parse.urlencode(
        {
            "apiKey": api_key,
            "regions": "us",
            "markets": "totals,h2h",
            "oddsFormat": "american",
            "dateFormat": "iso",
        }
    )
    url = f"{ODDS_API_BASE}/sports/{sport_key}/odds?{params}"
    try:
        events = _http_get_json(url)
        if not isinstance(events, list):
            return {}
        out: dict[tuple[str, str], dict[str, float]] = {}

        for ev in events:
            home_name = str(ev.get("home_team", "") or "").strip()
            away_name = str(ev.get("away_team", "") or "").strip()
            home_ab = _team_to_abbrev(home_name)
            away_ab = _team_to_abbrev(away_name)
            if not (home_ab and away_ab):
                continue

            bookmakers = sorted(ev.get("bookmakers") or [], key=_bm_sort_key)
            picked = None
            for bm in bookmakers:
                markets = bm.get("markets") or []
                h2h = next((m for m in markets if str(m.get("key", "")) == "h2h"), None)
                totals = next((m for m in markets if str(m.get("key", "")) == "totals"), None)
                if h2h and totals:
                    picked = (h2h, totals)
                    break
            if not picked:
                continue
            h2h, totals = picked

            over = next(
                (o for o in (totals.get("outcomes") or []) if str(o.get("name", "")).strip().lower() == "over"),
                None,
            )
            try:
                game_total = float(over.get("point")) if over else None
            except (TypeError, ValueError):
                game_total = None
            if game_total is None:
                continue

            home_o = next(
                (
                    o
                    for o in (h2h.get("outcomes") or [])
                    if _norm_name(o.get("name", "")) == _norm_name(home_name)
                ),
                None,
            )
            away_o = next(
                (
                    o
                    for o in (h2h.get("outcomes") or [])
                    if _norm_name(o.get("name", "")) == _norm_name(away_name)
                ),
                None,
            )
            if not home_o or not away_o:
                continue

            hp = _american_to_prob(home_o.get("price"))
            ap = _american_to_prob(away_o.get("price"))
            if hp is None or ap is None or (hp + ap) <= 0:
                continue

            share = hp / (hp + ap)
            home_implied = round(game_total * share, 2)
            away_implied = round(game_total * (1.0 - share), 2)
            out[(home_ab, away_ab)] = {
                "game_total": round(game_total, 2),
                "home_implied": home_implied,
                "away_implied": away_implied,
            }

        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            serial = {"date": today, "sport_key": sport_key, "totals": {}}
            for (h, a), v in out.items():
                serial["totals"][f"{h}|{a}"] = v
            path.write_text(json.dumps(serial, indent=2), encoding="utf-8")
        except Exception:
            pass
        return out
    except Exception as exc:
        _log.warning("line_movement: game totals fetch failed for %s — %s", sport_key, exc)
        return {}


def enrich_with_game_totals(df: pd.DataFrame, sport_key: str) -> pd.DataFrame:
    """
    Add implied_team_total (player's team) and game_total by matching home/away teams.
    """
    out = df.copy()
    totals = fetch_game_totals(sport_key)
    out["implied_team_total"] = None
    out["game_total"] = None
    if not len(out):
        return out

    for idx, row in out.iterrows():
        team = str(row.get("team", "") or "").strip().upper()
        opp = str(row.get("opponent", "") or "").strip().upper()
        home_team = str(row.get("home_team", "") or "").strip().upper()
        away_team = str(row.get("away_team", "") or "").strip().upper()
        is_home = str(row.get("is_home", "") or "").strip().lower()

        # Resolve orientation for lookup.
        if home_team and away_team:
            home_ab, away_ab = home_team, away_team
        elif team and opp:
            if is_home in ("1", "true", "yes"):
                home_ab, away_ab = team, opp
            else:
                home_ab, away_ab = opp, team
        else:
            continue

        game = totals.get((home_ab, away_ab))
        if not game:
            continue

        player_team_is_home = (
            team == home_ab if team else (is_home in ("1", "true", "yes"))
        )
        implied = game.get("home_implied") if player_team_is_home else game.get("away_implied")
        out.at[idx, "implied_team_total"] = implied
        out.at[idx, "game_total"] = game.get("game_total")

    return out


def _fetch_events(sport_key: str, api_key: str) -> list[dict]:
    qs = urllib.parse.urlencode({"apiKey": api_key, "dateFormat": "iso"})
    url = f"{ODDS_API_BASE}/sports/{sport_key}/events?{qs}"
    data = _http_get_json(url)
    return data if isinstance(data, list) else []


def _fetch_event_odds(
    sport_key: str,
    event_id: str,
    markets: list[str],
    api_key: str,
) -> dict:
    """Per-event player props: GET /sports/{sport}/events/{event_id}/odds."""
    if not markets or not (event_id or "").strip():
        return {}
    params: dict[str, str] = {
        "apiKey": api_key,
        "regions": "us",
        "markets": ",".join(m.strip() for m in markets if m.strip()),
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    qs = urllib.parse.urlencode(params)
    url = f"{ODDS_API_BASE}/sports/{sport_key}/events/{event_id.strip()}/odds?{qs}"
    data = _http_get_json(url)
    return data if isinstance(data, dict) else {}


def _parse_odds_events(events: list[dict], markets: list[str]) -> dict[tuple[str, str, float], dict[str, Any]]:
    """
    Build snapshot keyed by (player_lower, odds_market_key, line).
    Outcomes use description=player, point=line (per-event odds shape).
    open_line = first bookmaker seen; current_line = last (cross-book line movement).
    over_price / under_price = American odds from the highest-priority bookmaker per event.
    """
    wanted = {m.strip() for m in markets if m.strip()}
    acc: dict[tuple[str, str], list[tuple[int, float]]] = {}
    prices: dict[tuple[str, str, float], dict[str, Any]] = {}

    for event in events:
        bookmakers = sorted(event.get("bookmakers") or [], key=_bm_sort_key)
        best_bm = None
        for bm in bookmakers:
            if any(
                str(m.get("key", "")).strip() in wanted for m in (bm.get("markets") or [])
            ):
                best_bm = bm
                break

        if best_bm is not None:
            for market in best_bm.get("markets") or []:
                mkey = str(market.get("key", "")).strip()
                if mkey not in wanted:
                    continue
                over_at: dict[tuple[str, float], Any] = {}
                under_at: dict[tuple[str, float], Any] = {}
                for outcome in market.get("outcomes") or []:
                    player_raw = outcome.get("description")
                    if player_raw is None or not str(player_raw).strip():
                        continue
                    player = normalize_player_name(str(player_raw)).lower()
                    if not player:
                        continue
                    try:
                        point = round(float(outcome["point"]), 2)
                    except (KeyError, TypeError, ValueError):
                        continue
                    side = str(outcome.get("name", "")).strip().lower()
                    price = outcome.get("price")
                    if side == "over":
                        over_at[(player, point)] = price
                    elif side == "under":
                        under_at[(player, point)] = price
                for (player, point), over_price in over_at.items():
                    pk = (player, mkey, point)
                    prices[pk] = {
                        "over_price": over_price,
                        "under_price": under_at.get((player, point)),
                    }

        for bm in bookmakers:
            rank = _bm_sort_key(bm)
            for market in bm.get("markets") or []:
                mkey = str(market.get("key", "")).strip()
                if mkey not in wanted:
                    continue
                for outcome in market.get("outcomes") or []:
                    if str(outcome.get("name", "")).strip().lower() != "over":
                        continue
                    player_raw = outcome.get("description")
                    if player_raw is None or not str(player_raw).strip():
                        continue
                    player = normalize_player_name(str(player_raw)).lower()
                    if not player:
                        continue
                    try:
                        point = float(outcome["point"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    line = round(point, 2)
                    pk = (player, mkey)
                    acc.setdefault(pk, []).append((rank, line))

    snapshot: dict[tuple[str, str, float], dict[str, Any]] = {}
    for (player, mkey), entries in acc.items():
        if not entries:
            continue
        entries.sort(key=lambda x: x[0])
        points = [p for _, p in entries]
        open_line = float(points[0])
        current_line = float(points[-1])
        for line in sorted(set(points)):
            movement = round(current_line - open_line, 3)
            pk = (player, mkey, line)
            row = _snapshot_row_defaults(
                {
                    "open_line": open_line,
                    "current_line": current_line,
                    "line_movement": movement,
                    "line_direction_shift": _line_direction(open_line, current_line),
                }
            )
            pr = prices.get(pk)
            if pr:
                row["over_price"] = pr.get("over_price")
                row["under_price"] = pr.get("under_price")
            snapshot[pk] = row
    return snapshot


def fetch_line_snapshot(
    sport_key: str,
    markets: list[str],
    *,
    force_refresh: bool = False,
    date: str | None = None,
) -> dict[tuple[str, str, float], dict[str, Any]]:
    """
    Fetch (or load cached) line movement snapshot for events in the next 48h (UTC).

    Returns dict keyed by (player_name_lower, prop_type_odds_market, line).
    Empty dict if API key missing, quota/error, or no data.
    """
    api_key = _odds_api_key()
    today = (str(date or "").strip()[:10] or date.today().isoformat())
    if not api_key:
        _log.info("line_movement: ODDS_API_KEY missing — skipping fetch")
        return {}

    if not force_refresh:
        cached = _load_cache(sport_key, today)
        if cached is not None:
            _log.info("line_movement: using cache %s", _cache_path(sport_key, today))
            return {
                k: _snapshot_row_defaults(v) for k, v in cached.items()
            }

    try:
        events = _fetch_events(sport_key, api_key)
        target_events = [e for e in events if _is_target_event(e)]
        _log.info(
            "line_movement: %s events=%d (target_48h=%d)",
            sport_key,
            len(events),
            len(target_events),
        )
        event_payloads: list[dict] = []
        for idx, ev in enumerate(target_events):
            event_id = str(ev.get("id", "")).strip()
            if not event_id:
                continue
            if idx > 0:
                time.sleep(0.3)
            try:
                payload = _fetch_event_odds(sport_key, event_id, markets, api_key)
                if payload.get("bookmakers"):
                    event_payloads.append(payload)
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")[:200]
                except Exception:
                    pass
                _log.warning(
                    "line_movement: HTTP %s event %s — %s",
                    exc.code,
                    event_id[:12],
                    body,
                )
            except Exception as exc:
                _log.warning("line_movement: event %s failed — %s", event_id[:12], exc)

        snapshot = _parse_odds_events(event_payloads, markets)
        prior = _load_cache(sport_key, today) or {}
        snapshot = _merge_line_snapshot(prior, snapshot)
        _save_cache(sport_key, today, snapshot)
        _log.info(
            "line_movement: parsed %d prop lines for %s (%d events)",
            len(snapshot),
            sport_key,
            len(event_payloads),
        )
        return snapshot
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        _log.warning("line_movement: HTTP %s for %s — %s", exc.code, sport_key, body)
        return {}
    except Exception as exc:
        _log.warning("line_movement: fetch failed for %s — %s", sport_key, exc)
        return {}


def _pipeline_prop_to_odds_market(prop_type: str, sport_key: str = "") -> str:
    raw = str(prop_type or "").strip().lower()
    if not raw:
        return ""
    sport_map = SPORT_PROP_TO_ODDS_MARKET.get(str(sport_key or "").strip(), {})
    if raw in sport_map:
        return sport_map[raw]
    if raw in PIPELINE_PROP_TO_ODDS_MARKET:
        return PIPELINE_PROP_TO_ODDS_MARKET[raw]
    if raw.startswith("player_") or raw.startswith("batter_") or raw.startswith("pitcher_"):
        return raw
    return PIPELINE_PROP_TO_ODDS_MARKET.get(raw.replace(" ", "_"), raw)


def _lookup_snapshot_row(
    snapshot: dict[tuple[str, str, float], dict[str, Any]],
    player: str,
    odds_market: str,
    line: float,
) -> dict[str, Any] | None:
    if not odds_market or line is None:
        return None
    player_k = normalize_player_name(player).lower()
    if not player_k:
        return None
    try:
        line_f = round(float(line), 2)
    except (TypeError, ValueError):
        return None

    direct = snapshot.get((player_k, odds_market, line_f))
    if direct:
        return direct

    # Nearest-line match same player + market (PP line vs book rounding)
    best = None
    best_dist = 0.51
    for (p, m, ln), row in snapshot.items():
        if p != player_k or m != odds_market:
            continue
        dist = abs(ln - line_f)
        if dist < best_dist:
            best_dist = dist
            best = row
    if best:
        return best

    # Movement is identical for all line keys under the same player + market.
    for (p, m, _ln), row in snapshot.items():
        if p == player_k and m == odds_market:
            return row
    return None


def _current_line_value(row: pd.Series, line_col: str | None) -> float | None:
    if not line_col:
        return None
    try:
        return round(float(row.get(line_col)), 2)
    except (TypeError, ValueError):
        return None


def print_line_movement_wire_stats(df: pd.DataFrame, sport: str) -> None:
    """Log open_line / line_movement / implied_prob fill rates for pipeline validation."""
    total = len(df)
    if total == 0:
        print(
            f"[LM-WIRE] {sport}: open_line=0/0, line_movement=0/0, "
            f"line_direction_shift=0/0, implied_prob=0/0"
        )
        return
    ol = int(pd.to_numeric(df.get("open_line"), errors="coerce").notna().sum()) if "open_line" in df.columns else 0
    lm = int(pd.to_numeric(df.get("line_movement"), errors="coerce").notna().sum()) if "line_movement" in df.columns else 0
    lds = int(df.get("line_direction_shift", pd.Series(dtype=object)).notna().sum()) if "line_direction_shift" in df.columns else 0
    ip = int(pd.to_numeric(df.get("implied_prob"), errors="coerce").notna().sum()) if "implied_prob" in df.columns else 0
    print(
        f"[LM-WIRE] {sport}: open_line={ol}/{total}, "
        f"line_movement={lm}/{total}, line_direction_shift={lds}/{total}, "
        f"implied_prob={ip}/{total}"
    )


def _row_bet_direction(row: pd.Series) -> str:
    for col in ("final_bet_direction", "bet_direction", "direction", "Direction"):
        if col not in row.index:
            continue
        d = str(row.get(col, "") or "").strip().upper()
        if d in ("UNDER", "LOWER"):
            return "UNDER"
        if d in ("OVER", "HIGHER"):
            return "OVER"
    return "OVER"


def enrich_with_line_movement(
    df: pd.DataFrame,
    sport_key: str,
    markets: list[str],
    *,
    force_refresh: bool = False,
    date: str | None = None,
) -> pd.DataFrame:
    """
    Left-join open_line, line_movement, line_direction_shift, and market implied_prob onto df.
    Unmatched rows: open_line falls back to current line; movement=0.0; direction='stable';
    implied_prob columns are NaN when no book match.
    """
    out = df.copy()
    snapshot = fetch_line_snapshot(
        sport_key, markets, force_refresh=force_refresh, date=date
    )

    player_col = next(
        (c for c in ("player_name", "player", "Player") if c in out.columns),
        None,
    )
    prop_col = next(
        (c for c in ("prop_type", "stat_norm", "prop_norm") if c in out.columns),
        None,
    )
    line_col = next(
        (c for c in ("line_score", "line", "Line") if c in out.columns),
        None,
    )

    open_lines: list[Any] = []
    movements: list[float] = []
    directions: list[str] = []
    implied_over: list[float | None] = []
    implied_under: list[float | None] = []
    implied_sel: list[float | None] = []

    for _, row in out.iterrows():
        if not player_col or not prop_col or not line_col:
            open_lines.append(None)
            movements.append(0.0)
            directions.append("stable")
            implied_over.append(np.nan)
            implied_under.append(np.nan)
            implied_sel.append(np.nan)
            continue

        odds_market = _pipeline_prop_to_odds_market(str(row.get(prop_col, "")), sport_key)
        hit = _lookup_snapshot_row(
            snapshot,
            str(row.get(player_col, "")),
            odds_market,
            row.get(line_col),
        )
        cur_line = _current_line_value(row, line_col)
        if hit:
            hit = _snapshot_row_defaults(hit)
            ol = hit.get("open_line")
            open_lines.append(ol if ol is not None else cur_line)
            movements.append(float(hit.get("line_movement", 0.0) or 0.0))
            directions.append(str(hit.get("line_direction_shift") or "stable"))
            ip_o = _american_to_implied(hit.get("over_price"))
            ip_u = _american_to_implied(hit.get("under_price"))
        else:
            open_lines.append(cur_line)
            movements.append(0.0)
            directions.append("stable")
            ip_o = None
            ip_u = None

        implied_over.append(ip_o if ip_o is not None else np.nan)
        implied_under.append(ip_u if ip_u is not None else np.nan)
        side = _row_bet_direction(row)
        if side == "UNDER" and ip_u is not None:
            implied_sel.append(ip_u)
        elif ip_o is not None:
            implied_sel.append(ip_o)
        else:
            implied_sel.append(ip_u if ip_u is not None else np.nan)

    out["open_line"] = open_lines
    out["line_movement"] = movements
    out["line_direction_shift"] = directions
    out["implied_prob_over"] = implied_over
    out["implied_prob_under"] = implied_under
    out["implied_prob"] = implied_sel
    return out
