"""
Shared Tennis helpers: ESPN rankings, scoreboard parsing, name keys, prop norms.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Accept": "application/json"}

URL_ATP_RANK = "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/rankings"
URL_WTA_RANK = "https://site.api.espn.com/apis/site/v2/sports/tennis/wta/rankings"
URL_ATP_BOARD = "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard"
URL_WTA_BOARD = "https://site.api.espn.com/apis/site/v2/sports/tennis/wta/scoreboard"


def athlete_statistics_url(tour: str, athlete_id: str) -> str:
    t = "atp" if str(tour).upper() == "ATP" else "wta"
    return f"https://site.api.espn.com/apis/site/v2/sports/tennis/{t}/athletes/{athlete_id}/statistics"


def fetch_athlete_statistics(tour: str, athlete_id: str) -> dict[str, Any]:
    if not str(athlete_id).strip():
        return {}
    try:
        return fetch_json(athlete_statistics_url(tour, athlete_id))
    except Exception:
        return {}


def _flatten_espn_stat_dicts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for split in payload.get("splits") or []:
        for cat in split.get("categories") or []:
            for st in cat.get("stats") or []:
                if isinstance(st, dict):
                    out.append(st)
    return out


def parse_tennis_season_stats(payload: dict[str, Any]) -> dict[str, float | None]:
    """
    Best-effort parse of ESPN tennis athlete statistics JSON.
    Returns floats or None when missing.
    """
    stats = _flatten_espn_stat_dicts(payload)
    if not stats and isinstance(payload.get("statistics"), list):
        stats = [x for x in payload["statistics"] if isinstance(x, dict)]

    def find_val(*needles: str) -> float | None:
        for st in stats:
            raw = str(st.get("name") or st.get("displayName") or st.get("abbreviation") or "").lower()
            raw = re.sub(r"[^a-z0-9]+", "", raw)
            for nd in needles:
                n2 = re.sub(r"[^a-z0-9]+", "", nd.lower())
                if n2 and n2 in raw:
                    try:
                        return float(st.get("value"))
                    except (TypeError, ValueError):
                        return None
        return None

    return {
        "aces_per_match": find_val("aces", "acepermatch", "avgaces"),
        "double_faults_per_match": find_val("doublefault", "doublefaults", "df"),
        "first_serve_pct": find_val("firstserve", "1stsrvpct", "firstservepercent"),
        "games_won_per_match": find_val("games", "gameswon"),
        "sets_won_per_match": find_val("sets", "setswon"),
        "win_pct": find_val("wins", "winpercent", "matchwin"),
    }


def norm_key(s: str) -> str:
    if not s or (isinstance(s, float) and str(s) == "nan"):
        return ""
    t = unicodedata.normalize("NFKD", str(s))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\s+", " ", t.lower().strip())
    t = re.sub(r"[^a-z0-9 ]+", "", t)
    return t


# Alias for Sackmann / step4 history (same normalization as ESPN rankings).
_norm_key = norm_key

_TENNIS_ROOT = Path(__file__).resolve().parent.parent
_SACKMANN_DIR = _TENNIS_ROOT / "data" / "sackmann"
_SACKMANN_MAX_AGE_DAYS = 1.0
_SACKMANN_SET_RE = re.compile(r"(\d+)\s*-\s*(\d+)(?:\(\d+\))?")

_SACKMANN_PROP_MAP: dict[str, tuple[str, ...]] = {
    "aces": ("aces",),
    "double_faults": ("double_faults",),
    "games_won": ("games_won",),
    "sets_won": ("sets_won",),
    "match_total_games": ("match_total_games",),
}


def fetch_json(url: str, timeout: int = 25) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def parse_rankings_payload(data: dict[str, Any], tour: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in data.get("rankings") or []:
        for row in block.get("ranks") or []:
            ath = row.get("athlete") or {}
            aid = str(ath.get("id") or "").strip()
            name = str(ath.get("displayName") or ath.get("fullName") or "").strip()
            if not aid or not name:
                continue
            out.append(
                {
                    "espn_athlete_id": aid,
                    "player": name,
                    "tour": tour.upper(),
                    "rank": int(row.get("current") or 999),
                    "points": float(row.get("points") or 0.0),
                    "player_key": norm_key(name),
                }
            )
    return out


def load_or_refresh_rankings(cache_path: Path, *, max_age_hours: int = 8) -> list[dict[str, Any]]:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.is_file():
        try:
            age = datetime.now(timezone.utc).timestamp() - cache_path.stat().st_mtime
            if age < max_age_hours * 3600:
                return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    rows: list[dict[str, Any]] = []
    try:
        rows.extend(parse_rankings_payload(fetch_json(URL_ATP_RANK), "ATP"))
    except Exception:
        pass
    try:
        rows.extend(parse_rankings_payload(fetch_json(URL_WTA_RANK), "WTA"))
    except Exception:
        pass
    cache_path.write_text(json.dumps(rows, indent=0), encoding="utf-8")
    return rows


def _games_from_linescores(comp: dict[str, Any]) -> float:
    ls = comp.get("linescores") or []
    return float(sum(float(x.get("value") or 0) for x in ls))


def _stat_from_competitor(comp: dict[str, Any], *needles: str) -> float | None:
    for st in comp.get("statistics") or []:
        name = str(st.get("name") or st.get("displayName") or st.get("abbreviation") or "").lower()
        name = re.sub(r"[^a-z0-9]+", "", name)
        for nd in needles:
            n2 = re.sub(r"[^a-z0-9]+", "", nd.lower())
            if n2 and n2 in name:
                try:
                    return float(st.get("value"))
                except (TypeError, ValueError):
                    return None
    return None


def _sets_won_from_linescores(comp: dict[str, Any], other: dict[str, Any] | None) -> float:
    ls = comp.get("linescores") or []
    if not ls or not other:
        return 0.0
    ols = other.get("linescores") or []
    won = 0
    for i, cur in enumerate(ls):
        try:
            gv = float(cur.get("value") or 0)
            ov = float(ols[i].get("value") or 0) if i < len(ols) else 0.0
            if gv > ov:
                won += 1
        except (TypeError, ValueError, IndexError):
            continue
    return float(won)


def _comp_status_final(comp: dict[str, Any]) -> bool:
    st = (comp.get("status") or {}).get("type") or {}
    return str(st.get("name") or "").upper() == "STATUS_FINAL"


def iter_scoreboard_matches(tour: str) -> Iterator[dict[str, Any]]:
    url = URL_ATP_BOARD if tour.upper() == "ATP" else URL_WTA_BOARD
    try:
        data = fetch_json(url)
    except Exception:
        return
    for ev in data.get("events") or []:
        for grp in ev.get("groupings") or []:
            for comp in grp.get("competitions") or []:
                if not _comp_status_final(comp):
                    continue
                comps = comp.get("competitors") or []
                if len(comps) < 2:
                    continue
                dt = str(comp.get("date") or comp.get("startDate") or "")[:19]
                match_total = sum(_games_from_linescores(c) for c in comps)
                for i, c in enumerate(comps):
                    ath = c.get("athlete") or {}
                    aid = str(ath.get("id") or c.get("id") or "").strip()
                    nm = str(ath.get("displayName") or "").strip()
                    if not aid or not nm:
                        continue
                    other_c = comps[1 - i] if len(comps) == 2 else None
                    gw = _games_from_linescores(c)
                    aces = _stat_from_competitor(c, "aces", "ace")
                    dbl = _stat_from_competitor(c, "doublefault", "doublefaults", "double faults")
                    if aces is None:
                        aces = 0.0
                    if dbl is None:
                        dbl = 0.0
                    sw = _sets_won_from_linescores(c, other_c)
                    opp = ""
                    if other_c is not None:
                        a2 = other_c.get("athlete") or {}
                        opp = str(a2.get("displayName") or "").strip()
                    yield {
                        "espn_athlete_id": aid,
                        "player": nm,
                        "player_key": norm_key(nm),
                        "tour": tour.upper(),
                        "match_date_utc": dt,
                        "games_won": gw,
                        "match_total_games": float(match_total),
                        "opponent": opp,
                        "aces": float(aces),
                        "double_faults": float(dbl),
                        "sets_won": float(sw),
                    }


def refresh_match_games_cache(cache_path: Path, tours: tuple[str, ...] = ("ATP", "WTA")) -> dict[str, list[dict[str, Any]]]:
    """Map espn_athlete_id -> list of recent match dicts (newest first)."""
    by_id: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    for tour in tours:
        for m in iter_scoreboard_matches(tour):
            aid = m["espn_athlete_id"]
            key = (aid, m.get("match_date_utc") or "")
            if key in seen:
                continue
            seen.add(key)
            by_id.setdefault(aid, []).append(m)
    for aid in by_id:
        by_id[aid].sort(key=lambda x: str(x.get("match_date_utc") or ""), reverse=True)
        by_id[aid] = by_id[aid][:24]
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(by_id, indent=0), encoding="utf-8")
    return by_id


def load_match_games_cache(cache_path: Path) -> dict[str, list[dict[str, Any]]]:
    if not cache_path.is_file():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def resolve_athlete_id(player_name: str, rankings: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (espn_id, tour) or ('','')."""
    pk = norm_key(player_name)
    if not pk:
        return "", ""
    best = ""
    best_tour = ""
    best_len = -1
    for r in rankings:
        rk = r.get("player_key") or ""
        if not rk:
            continue
        if pk == rk:
            return str(r["espn_athlete_id"]), str(r.get("tour") or "")
        if pk in rk or rk in pk:
            ln = len(rk)
            if ln > best_len:
                best_len = ln
                best = str(r["espn_athlete_id"])
                best_tour = str(r.get("tour") or "")
    return best, best_tour


def resolve_opp_rank(opp_name: str, rankings: list[dict[str, Any]]) -> float:
    if not str(opp_name or "").strip() or str(opp_name).upper() in ("UNKNOWN_OPP", "UNK"):
        return 75.0
    pk = norm_key(opp_name)
    for r in rankings:
        if r.get("player_key") == pk:
            return float(r.get("rank") or 75)
    best = 75.0
    for r in rankings:
        rk = r.get("player_key") or ""
        if pk and rk and (pk in rk or rk in pk):
            best = min(best, float(r.get("rank") or 75))
    return best


PROP_NORM_MAP = {
    "aces": "aces",
    "ace": "aces",
    "doublefaults": "double_faults",
    "double faults": "double_faults",
    "double fault": "double_faults",
    "break point": "break_points_won",
    "break points won": "break_points_won",
    "games won": "games_won",
    "total games": "match_total_games",
    "match total games": "match_total_games",
    "match games": "match_total_games",
    "sets won": "sets_won",
    "set won": "sets_won",
}


def norm_tennis_prop(raw: str) -> str:
    if not raw or (isinstance(raw, float) and str(raw) == "nan"):
        return ""
    s = str(raw).lower().strip()
    s2 = re.sub(r"[^a-z0-9 ]+", "", s.replace("-", " "))
    s2 = re.sub(r"\s+", " ", s2).strip()
    if s2 in PROP_NORM_MAP:
        return PROP_NORM_MAP[s2]
    for k, v in PROP_NORM_MAP.items():
        if k in s2:
            return v
    if "game" in s2 and "won" in s2:
        return "games_won"
    if "total" in s2 and "game" in s2:
        return "match_total_games"
    if "set" in s2 and "won" in s2:
        return "sets_won"
    return s2.replace(" ", "_")[:48]


def history_value_key(prop_norm: str) -> str | None:
    if prop_norm == "games_won":
        return "games_won"
    if prop_norm == "match_total_games":
        return "match_total_games"
    if prop_norm == "aces":
        return "aces"
    if prop_norm == "double_faults":
        return "double_faults"
    if prop_norm == "sets_won":
        return "sets_won"
    return None


def _sackmann_file_stale(path: Path) -> bool:
    if not path.is_file():
        return True
    age_days = (time.time() - path.stat().st_mtime) / 86400.0
    return age_days > _SACKMANN_MAX_AGE_DAYS


def ensure_sackmann_matches(*, force_fetch: bool = False) -> pd.DataFrame:
    """
    Load combined ATP+WTA Sackmann matches; refresh via fetch_sackmann_data.py if stale.
    """
    combined_atp = _SACKMANN_DIR / "atp_matches_combined.csv"
    if force_fetch or _sackmann_file_stale(combined_atp):
        fetch_script = Path(__file__).resolve().parent / "fetch_sackmann_data.py"
        if fetch_script.is_file():
            cmd = [sys.executable, str(fetch_script)]
            if force_fetch:
                cmd.append("--force")
            subprocess.run(cmd, cwd=str(_TENNIS_ROOT.parents[1]), check=False)
    frames: list[pd.DataFrame] = []
    for name in ("atp_matches_combined.csv", "wta_matches_combined.csv"):
        p = _SACKMANN_DIR / name
        if not p.is_file():
            continue
        try:
            frames.append(pd.read_csv(p, low_memory=False))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _parse_score(score: str, is_winner: bool) -> dict[str, float | None]:
    """Parse Sackmann score string -> games_won, sets_won, match_total_games."""
    both = _parse_score_both_sides(score)
    if not both:
        return {"games_won": None, "sets_won": None, "match_total_games": None}
    side = both["winner"] if is_winner else both["loser"]
    return {
        "games_won": side["games_won"],
        "sets_won": side["sets_won"],
        "match_total_games": both["match_total_games"],
    }


def _parse_score_both_sides(score: str) -> dict[str, Any] | None:
    s = str(score or "").strip()
    if not s:
        return None
    low = s.lower()
    if low in {"w/o", "wo", "ret", "retired", "def", "default", "walkover"}:
        return None
    sets = _SACKMANN_SET_RE.findall(s)
    if not sets:
        return None
    w_games = l_games = w_sets = l_sets = 0
    total = 0
    for a, b in sets:
        try:
            wi, li = int(a), int(b)
        except ValueError:
            continue
        total += wi + li
        w_games += wi
        l_games += li
        if wi > li:
            w_sets += 1
        elif li > wi:
            l_sets += 1
    if total <= 0:
        return None
    return {
        "match_total_games": float(total),
        "winner": {"games_won": float(w_games), "sets_won": float(w_sets)},
        "loser": {"games_won": float(l_games), "sets_won": float(l_sets)},
    }


def build_sackmann_player_index(matches: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    """
    Pre-index Sackmann matches by norm_key(player), newest tourney_date first.
    Each entry holds per-match stats used for stat_g1..g10.
    """
    if matches is None or matches.empty:
        return {}
    need = {"winner_name", "loser_name", "tourney_date", "score", "w_ace", "l_ace", "w_df", "l_df"}
    if not need.issubset(set(matches.columns)):
        return {}

    index: dict[str, list[dict[str, Any]]] = {}

    def _append(pk: str, rec: dict[str, Any]) -> None:
        if pk:
            index.setdefault(pk, []).append(rec)

    for _, rd in matches.iterrows():
        w_name = str(rd.get("winner_name") or "")
        l_name = str(rd.get("loser_name") or "")
        date = str(rd.get("tourney_date") or "")
        score = str(rd.get("score") or "")
        parsed = _parse_score_both_sides(score)
        mtg = parsed["match_total_games"] if parsed else None
        w_side = parsed["winner"] if parsed else {}
        l_side = parsed["loser"] if parsed else {}
        try:
            w_ace = float(rd.get("w_ace"))
        except (TypeError, ValueError):
            w_ace = float("nan")
        try:
            l_ace = float(rd.get("l_ace"))
        except (TypeError, ValueError):
            l_ace = float("nan")
        try:
            w_df = float(rd.get("w_df"))
        except (TypeError, ValueError):
            w_df = float("nan")
        try:
            l_df = float(rd.get("l_df"))
        except (TypeError, ValueError):
            l_df = float("nan")

        _append(
            norm_key(w_name),
            {
                "date": date,
                "aces": w_ace,
                "double_faults": w_df,
                "games_won": w_side.get("games_won"),
                "sets_won": w_side.get("sets_won"),
                "match_total_games": mtg,
            },
        )
        _append(
            norm_key(l_name),
            {
                "date": date,
                "aces": l_ace,
                "double_faults": l_df,
                "games_won": l_side.get("games_won"),
                "sets_won": l_side.get("sets_won"),
                "match_total_games": mtg,
            },
        )

    for pk in index:
        index[pk].sort(key=lambda x: str(x.get("date") or ""), reverse=True)
    return index


def build_sackmann_player_log(
    matches: pd.DataFrame,
    player_norm: str,
    prop_norm: str,
    last_n: int = 20,
    *,
    player_index: dict[str, list[dict[str, Any]]] | None = None,
) -> list[float]:
    """
    Return up to last_n float values for prop_norm from Sackmann matches, newest first.
    """
    if prop_norm not in _SACKMANN_PROP_MAP:
        return []
    pk = (player_norm or "").strip()
    if not pk:
        return []
    if player_index is None:
        player_index = build_sackmann_player_index(matches)
    rows = player_index.get(pk) or []
    vals: list[float] = []
    for rec in rows[: max(1, int(last_n))]:
        raw = rec.get(prop_norm)
        if raw is None:
            continue
        try:
            fv = float(raw)
        except (TypeError, ValueError):
            continue
        if fv != fv:  # NaN
            continue
        vals.append(fv)
    return vals
