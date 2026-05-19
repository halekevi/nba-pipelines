#!/usr/bin/env python3
"""
FBref expected-goals cache for Soccer step4b.

Reads manually saved HTML from data/cache/fbref_html/ (no live scraping).
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from io import StringIO
from pathlib import Path
from typing import Any, Optional

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = _REPO_ROOT / "data" / "cache" / "fbref_html"
CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "fbref_xg_cache.json"

FBREF_LEAGUES = {
    "ENG-Premier League": {
        "local_files": {"summary": "epl_summary.html", "keeper": "epl_keeper.html"},
    },
    "ENG-Championship": {
        "local_files": {"summary": "champ_summary.html", "keeper": "champ_keeper.html"},
    },
    "UCL": {
        "local_files": {"summary": "ucl_summary.html", "keeper": "ucl_keeper.html"},
    },
    "NOR-Eliteserien": {
        "local_files": {"summary": "nor_summary.html", "keeper": "nor_keeper.html"},
    },
    "MLS": {
        "local_files": {"summary": "mls_summary.html", "keeper": "mls_keeper.html"},
    },
}


def fbref_season(d: date) -> str:
    year = d.year if d.month >= 8 else d.year - 1
    return f"{year}-{year + 1}"


def _norm_name(name: str) -> str:
    name = unicodedata.normalize("NFD", str(name or ""))
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = re.sub(r"[^a-z0-9 ]", "", name.lower())
    return re.sub(r"\s+", " ", name).strip()


def _norm_team(team: str) -> str:
    t = _norm_name(team)
    for suf in (" fc", " sc", " afc", " cf"):
        if t.endswith(suf):
            t = t[: -len(suf)].strip()
    return t


def _safe_float(v: object) -> Optional[float]:
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _get(row: pd.Series, *patterns: str) -> Optional[float]:
    for pat in patterns:
        for col in row.index:
            if pat.lower() in str(col).lower():
                v = _safe_float(row[col])
                if v is not None:
                    return v
    return None


def _flatten_cols(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [
        "_".join(str(c).strip() for c in col if "Unnamed" not in str(c)).strip("_")
        or f"col_{i}"
        for i, col in enumerate(df.columns)
    ]
    return df


def _extract_player_ids(html: str) -> list[str]:
    ids, seen = [], set()
    for m in re.finditer(r"/en/players/([a-f0-9]{8})/", html):
        pid = m.group(1)
        if pid not in seen:
            seen.add(pid)
            ids.append(pid)
    return ids


def _read_local(filename: str) -> Optional[str]:
    path = CACHE_DIR / filename
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _per90(total: Optional[float], minutes: Optional[float]) -> Optional[float]:
    if total is None or minutes is None or minutes <= 0:
        return None
    return round(total * 90.0 / minutes, 4)


def _map_xg_row(row: pd.Series) -> dict[str, Optional[float]]:
    minutes = _get(row, "Playing Time_Min", "Time_Min", "_Min")
    npxg = _get(row, "Expected_npxG", "npxG_")
    xg = _get(row, "Expected_xG", "Per_90_xG", "_xG")
    xag = _get(row, "Expected_xAG", "xAG_")
    goals = _get(row, "Performance_Gls", "Goals_Gls", "_Gls")
    shots = _get(row, "Standard_Sh", "Shots_Sh", "_Sh")

    npxg90 = _get(row, "Expected_npxG/90", "npxG/90")
    xg90 = _get(row, "Expected_xG/90", "xG/90")
    xag90 = _get(row, "Expected_xAG/90", "xAG/90")

    player_xg = npxg90 if npxg90 is not None else xg90
    if player_xg is None:
        player_xg = _per90(npxg if npxg is not None else xg, minutes)

    player_xag = xag90 if xag90 is not None else _per90(xag, minutes)
    player_shots = _get(row, "Standard_Sh/90", "Sh/90")
    if player_shots is None:
        player_shots = _per90(shots, minutes)

    g_xg = None
    if goals is not None and (npxg is not None or xg is not None):
        g_xg = round(goals - (npxg if npxg is not None else xg), 4)

    return {
        "player_xg_per90": player_xg,
        "player_xag_per90": player_xag,
        "player_shots_per90": player_shots,
        "player_goals_minus_xg": g_xg,
        "minutes": minutes,
    }


def _parse_summary_html(html: str, league_key: str) -> list[dict]:
    html_clean = re.sub(r"<!--(.*?)-->", r"\1", html, flags=re.DOTALL)
    try:
        tables = pd.read_html(StringIO(html_clean), header=[0, 1])
    except Exception:
        return []

    df = None
    for t in sorted(tables, key=len, reverse=True):
        flat = _flatten_cols(t.copy())
        if "Player" in flat.columns and len(flat) > 10:
            df = flat
            break
    if df is None:
        return []

    df = df[df["Player"] != "Player"].copy().reset_index(drop=True)
    fbref_ids = _extract_player_ids(html)
    df["fbref_player_id"] = [fbref_ids[i] if i < len(fbref_ids) else "" for i in df.index]

    rows: list[dict] = []
    for _, row in df.iterrows():
        player = str(row.get("Player", "") or "").strip()
        if not player or player == "Player":
            continue
        stats = _map_xg_row(row)
        if stats.get("player_xg_per90") is None and stats.get("player_xag_per90") is None:
            continue
        rows.append({
            "player": player,
            "team": str(row.get("Squad", "") or "").strip(),
            "league": league_key,
            "fbref_id": str(row.get("fbref_player_id", "") or "").strip(),
            "norm_name": _norm_name(player),
            "norm_team": _norm_team(str(row.get("Squad", "") or "")),
            **stats,
        })
    return rows


def _assign_xg_tiers(players: list[dict]) -> None:
    vals = [p["player_xg_per90"] for p in players if p.get("player_xg_per90") is not None]
    if len(vals) < 3:
        for p in players:
            p["xg_tier"] = "mid" if p.get("player_xg_per90") is not None else "cache_miss"
        return
    s = pd.Series(vals)
    lo, hi = float(s.quantile(0.33)), float(s.quantile(0.66))
    for p in players:
        v = p.get("player_xg_per90")
        if v is None:
            p["xg_tier"] = "cache_miss"
        elif v <= lo:
            p["xg_tier"] = "low"
        elif v <= hi:
            p["xg_tier"] = "mid"
        else:
            p["xg_tier"] = "high"


def refresh_cache(season: str) -> dict[str, Any]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []
    for league_key, cfg in FBREF_LEAGUES.items():
        html = _read_local(cfg["local_files"].get("summary", ""))
        if not html:
            continue
        all_rows.extend(_parse_summary_html(html, league_key))

    _assign_xg_tiers(all_rows)

    by_id: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for r in all_rows:
        rec = {k: r[k] for k in r if k not in ("norm_name", "norm_team")}
        if r.get("fbref_id"):
            by_id[f"{season}|{r['fbref_id']}"] = rec
        name_key = f"{season}|{r['norm_name']}|{r['norm_team']}"
        by_name[name_key] = rec

    payload = {
        "seasons": {
            season: {
                "by_id": by_id,
                "by_name": by_name,
                "player_count": len(all_rows),
                "leagues_parsed": sorted({r["league"] for r in all_rows}),
            }
        }
    }
    existing: dict[str, Any] = {"seasons": {}}
    if CACHE_PATH.exists():
        try:
            existing = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.setdefault("seasons", {})[season] = payload["seasons"][season]
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return existing


def load_cache() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {"seasons": {}}
    return json.loads(CACHE_PATH.read_text(encoding="utf-8"))


def lookup_player(
    cache: dict[str, Any],
    season: str,
    name: str,
    team: str = "",
    fbref_id: str = "",
    league_hint: str = "",
) -> Optional[dict]:
    season_data = cache.get("seasons", {}).get(season, {})
    by_id = season_data.get("by_id", {})
    by_name = season_data.get("by_name", {})

    if fbref_id:
        hit = by_id.get(f"{season}|{fbref_id}")
        if hit:
            return {**hit, "xg_data_source": "fbref"}

    nn, nt = _norm_name(name), _norm_team(team)
    direct = by_name.get(f"{season}|{nn}|{nt}")
    if direct:
        return {**direct, "xg_data_source": "fbref"}

    best, best_score = None, 0.0
    for key, rec in by_name.items():
        if not key.startswith(f"{season}|"):
            continue
        parts = key.split("|", 2)
        if len(parts) < 3:
            continue
        rname, rteam = parts[1], parts[2]
        score = SequenceMatcher(None, nn, rname).ratio()
        if nt and rteam:
            score = 0.6 * score + 0.4 * SequenceMatcher(None, nt, rteam).ratio()
        if score > best_score:
            best_score, best = score, rec

    if best and best_score >= 0.88:
        return {**best, "xg_data_source": "fbref"}
    if best and best_score >= 0.92 and not nt:
        return {**best, "xg_data_source": "fbref"}

    return None
