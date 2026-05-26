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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

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

# Pipeline stat_norm / prop_type → Odds API market keys (NHL pilot mappings)
PIPELINE_PROP_TO_ODDS_MARKET: dict[str, str] = {
    "shots_on_goal": "player_shots_on_goal",
    "shots": "player_shots_on_goal",
    "sog": "player_shots_on_goal",
    "goals": "player_goals",
    "goal_scorer": "player_goal_scorer_anytime",
    "anytime_goal": "player_goal_scorer_anytime",
    "anytime_goal_scorer": "player_goal_scorer_anytime",
    "assists": "player_assists",
    "points": "player_points",
    "power_play_points": "player_power_play_points",
    "blocked_shots": "player_blocked_shots",
}

BM_PRIORITY = ("draftkings", "fanduel", "betmgm", "caesars", "pointsbetus", "bovada")
_LINE_EPS = 0.05


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


def _is_commence_today(commence_time: str, today: str) -> bool:
    if not commence_time:
        return True
    try:
        ts = datetime.fromisoformat(str(commence_time).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc).date().isoformat() == today
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
    open_line = first priority bookmaker; current_line = last priority book with data.
    """
    wanted = {m.strip() for m in markets if m.strip()}
    # (player, market) -> list of (bm_rank, point) in encounter order
    acc: dict[tuple[str, str], list[tuple[int, float]]] = {}

    for event in events:
        for bm in sorted(event.get("bookmakers") or [], key=_bm_sort_key):
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
            snapshot[(player, mkey, line)] = {
                "open_line": open_line,
                "current_line": current_line,
                "line_movement": movement,
                "line_direction_shift": _line_direction(open_line, current_line),
            }
    return snapshot


def fetch_line_snapshot(sport_key: str, markets: list[str]) -> dict[tuple[str, str, float], dict[str, Any]]:
    """
    Fetch (or load cached) line movement snapshot for today's slate.

    Returns dict keyed by (player_name_lower, prop_type_odds_market, line).
    Empty dict if API key missing, quota/error, or no data.
    """
    api_key = _odds_api_key()
    today = date.today().isoformat()
    if not api_key:
        _log.info("line_movement: ODDS_API_KEY missing — skipping fetch")
        return {}

    cached = _load_cache(sport_key, today)
    if cached is not None:
        _log.info("line_movement: using cache %s", _cache_path(sport_key, today))
        return cached

    try:
        events = _fetch_events(sport_key, api_key)
        today_events = [e for e in events if _is_commence_today(e.get("commence_time", ""), today)]
        _log.info(
            "line_movement: %s events=%d (today=%d)",
            sport_key,
            len(events),
            len(today_events),
        )
        event_payloads: list[dict] = []
        for idx, ev in enumerate(today_events):
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


def _pipeline_prop_to_odds_market(prop_type: str) -> str:
    raw = str(prop_type or "").strip().lower()
    if not raw:
        return ""
    if raw in PIPELINE_PROP_TO_ODDS_MARKET:
        return PIPELINE_PROP_TO_ODDS_MARKET[raw]
    if raw.startswith("player_"):
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
    return best


def enrich_with_line_movement(
    df: pd.DataFrame,
    sport_key: str,
    markets: list[str],
) -> pd.DataFrame:
    """
    Left-join open_line, line_movement, line_direction_shift onto df.
    Unmatched rows: open_line=None, line_movement=0.0, line_direction_shift='stable'.
    """
    out = df.copy()
    snapshot = fetch_line_snapshot(sport_key, markets)

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

    for _, row in out.iterrows():
        if not player_col or not prop_col or not line_col:
            open_lines.append(None)
            movements.append(0.0)
            directions.append("stable")
            continue

        odds_market = _pipeline_prop_to_odds_market(str(row.get(prop_col, "")))
        hit = _lookup_snapshot_row(
            snapshot,
            str(row.get(player_col, "")),
            odds_market,
            row.get(line_col),
        )
        if hit:
            open_lines.append(hit.get("open_line"))
            movements.append(float(hit.get("line_movement", 0.0) or 0.0))
            directions.append(str(hit.get("line_direction_shift") or "stable"))
        else:
            open_lines.append(None)
            movements.append(0.0)
            directions.append("stable")

    out["open_line"] = open_lines
    out["line_movement"] = movements
    out["line_direction_shift"] = directions
    return out
