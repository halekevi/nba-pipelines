#!/usr/bin/env python3
"""
FBref expected-goals cache for Soccer step4b.

Reads manually saved HTML from data/cache/fbref_html/ (no live scraping).
When Expected xG/npxG columns are missing (paywall), falls back to Shooting Sh/90
and SoT/90 with proxy_xg_per90 = SoT/90 * 0.33.
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

# League-average shots-on-target to goal conversion (proxy when xG block unavailable).
PROXY_SOT_TO_XG = 0.33

FBREF_LEAGUES = {
    "ENG-Premier League": {
        "local_files": {
            "summary": "epl_summary.html",
            # Optional dedicated save from /comps/9/shooting/Premier-League-Stats
            "shooting": "epl_shooting.html",
            "keeper": "epl_keeper.html",
        },
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
    new_cols: list[str] = []
    for i, col in enumerate(df.columns):
        parts = [str(c).strip() for c in col if "Unnamed" not in str(c)]
        name = "_".join(parts).strip("_") if parts else ""
        if not name:
            # pandas sometimes keeps "Unnamed: 0_level_0_Player" — take trailing label.
            raw = "_".join(str(c) for c in col)
            m = re.search(r"_([A-Za-z][A-Za-z0-9 /%]*)$", raw)
            name = (m.group(1).strip() if m else "") or f"col_{i}"
        new_cols.append(name)
    df.columns = new_cols
    return df


def _player_series(df: pd.DataFrame) -> Optional[pd.Series]:
    if "Player" in df.columns:
        return df["Player"]
    for c in df.columns:
        if str(c).endswith("_Player") or str(c) == "Player":
            return df[c]
    return None


def _extract_player_ids(html: str) -> list[str]:
    ids, seen = [], set()
    for m in re.finditer(r"/en/players/([a-f0-9]{8})/", html):
        pid = m.group(1)
        if pid not in seen:
            seen.add(pid)
            ids.append(pid)
    return ids


def _read_local(filename: str) -> Optional[str]:
    if not filename:
        return None
    path = CACHE_DIR / filename
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    if "Just a moment" in text and "challenges.cloudflare" in text:
        return None
    return text


def _per90(total: Optional[float], minutes: Optional[float]) -> Optional[float]:
    if total is None or minutes is None or minutes <= 0:
        return None
    return round(total * 90.0 / minutes, 4)


def _cols_blob(df: pd.DataFrame) -> str:
    return " ".join(str(c) for c in df.columns).lower()


def _table_score(df: pd.DataFrame) -> int:
    if _player_series(df) is None:
        return 0
    cols = _cols_blob(df)
    min_rows = 2 if "sot/90" in cols else 10
    if len(df) < min_rows:
        return 0
    score = len(df)
    if "expected_npxg" in cols or "npxg" in cols:
        score += 500
    if "sot/90" in cols or "sot_90" in cols:
        score += 800
    if "sh/90" in cols and "sot" in cols:
        score += 400
    if "performance_gls" in cols or "_gls" in cols:
        score += 100
    return score


def _pick_player_table(tables: list[pd.DataFrame]) -> Optional[pd.DataFrame]:
    best: Optional[pd.DataFrame] = None
    best_score = 0
    for t in tables:
        flat = _flatten_cols(t.copy())
        score = _table_score(flat)
        if score > best_score:
            best_score = score
            best = flat
    return best


def _read_tables(html: str) -> list[pd.DataFrame]:
    html_clean = re.sub(r"<!--(.*?)-->", r"\1", html, flags=re.DOTALL)
    try:
        return pd.read_html(StringIO(html_clean), header=[0, 1])
    except Exception:
        try:
            return pd.read_html(StringIO(html_clean))
        except Exception:
            return []


def _shooting_proxy_from_row(row: pd.Series) -> tuple[Optional[float], Optional[float], str]:
    """Return (proxy_xg_per90, shots_per90, source)."""
    sh90 = _get(
        row,
        "Standard_Sh/90",
        "Sh/90",
        "Shooting_Sh/90",
        "Per_90_Minutes_Sh/90",
        "Per_90_Sh/90",
    )
    sot90 = _get(
        row,
        "Standard_SoT/90",
        "SoT/90",
        "Shooting_SoT/90",
        "Per_90_Minutes_SoT/90",
        "Per_90_SoT/90",
    )
    minutes = _get(row, "Playing Time_Min", "Time_Min", "_Min", "Playing_Time_Min")
    shots = _get(row, "Standard_Sh", "Shots_Sh", "_Sh", "Shooting_Sh")
    sot = _get(row, "Standard_SoT", "SoT", "Shooting_SoT")

    if sh90 is None:
        sh90 = _per90(shots, minutes)
    if sot90 is None:
        sot90 = _per90(sot, minutes)

    if sot90 is not None:
        return round(sot90 * PROXY_SOT_TO_XG, 4), sh90, "proxy_shots"
    if sh90 is not None:
        # Weaker fallback: approximate xG from volume when SoT/90 missing.
        return round(sh90 * 0.10, 4), sh90, "proxy_shots"
    return None, None, ""


def _map_xg_row(row: pd.Series) -> dict[str, Any]:
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

    source = "fbref" if player_xg is not None else ""
    if player_xg is None:
        proxy_xg, proxy_sh, proxy_src = _shooting_proxy_from_row(row)
        if proxy_xg is not None:
            player_xg = proxy_xg
            if player_shots is None:
                player_shots = proxy_sh
            source = proxy_src

    return {
        "player_xg_per90": player_xg,
        "player_xag_per90": player_xag,
        "player_shots_per90": player_shots,
        "player_goals_minus_xg": g_xg,
        "minutes": minutes,
        "xg_data_source": source or None,
    }


def _map_shooting_row(row: pd.Series) -> dict[str, Any]:
    minutes = _get(row, "Playing Time_Min", "Time_Min", "_Min")
    proxy_xg, sh90, source = _shooting_proxy_from_row(row)
    if proxy_xg is None and sh90 is None:
        return {}
    return {
        "player_xg_per90": proxy_xg,
        "player_xag_per90": None,
        "player_shots_per90": sh90,
        "player_goals_minus_xg": None,
        "minutes": minutes,
        "xg_data_source": source,
    }


def _rows_from_table(
    df: pd.DataFrame,
    html: str,
    league_key: str,
    *,
    shooting_only: bool = False,
) -> list[dict]:
    players = _player_series(df)
    if players is None:
        return []
    df = df[players.astype(str).str.strip().ne("Player")].copy().reset_index(drop=True)
    players = _player_series(df)
    fbref_ids = _extract_player_ids(html)
    df["fbref_player_id"] = [fbref_ids[i] if i < len(fbref_ids) else "" for i in df.index]

    rows: list[dict] = []
    for _, row in df.iterrows():
        player = str(players.at[row.name] if row.name in players.index else row.get("Player", "") or "").strip()
        if not player or player in ("Player", "Squad Total", "Opponent Total"):
            continue
        stats = _map_shooting_row(row) if shooting_only else _map_xg_row(row)
        if not stats or stats.get("player_xg_per90") is None:
            continue
        rows.append(
            {
                "player": player,
                "team": str(row.get("Squad", "") or "").strip(),
                "league": league_key,
                "fbref_id": str(row.get("fbref_player_id", "") or "").strip(),
                "norm_name": _norm_name(player),
                "norm_team": _norm_team(str(row.get("Squad", "") or "")),
                **stats,
            }
        )
    return rows


def _parse_html(html: str, league_key: str, *, shooting_only: bool = False) -> list[dict]:
    tables = _read_tables(html)
    if not tables:
        return []
    df = _pick_player_table(tables)
    if df is None:
        return []
    cols = _cols_blob(df)
    if shooting_only or ("sot/90" in cols and "expected_npxg" not in cols):
        return _rows_from_table(df, html, league_key, shooting_only=True)
    return _rows_from_table(df, html, league_key, shooting_only=False)


def _parse_summary_html(html: str, league_key: str) -> list[dict]:
    return _parse_html(html, league_key, shooting_only=False)


def _parse_shooting_html(html: str, league_key: str) -> list[dict]:
    return _parse_html(html, league_key, shooting_only=True)


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


def _merge_player_rows(existing: Optional[dict], incoming: dict) -> dict:
    """Prefer real FBref xG over shooting proxy; fill gaps from proxy."""
    if not existing:
        return incoming
    ex_src = str(existing.get("xg_data_source") or "")
    in_src = str(incoming.get("xg_data_source") or "")
    if ex_src == "fbref" and in_src == "proxy_shots":
        return existing
    if in_src == "fbref" and ex_src == "proxy_shots":
        return incoming
    if existing.get("player_xg_per90") is not None:
        return existing
    return incoming


def refresh_cache(season: str) -> dict[str, Any]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    merged: dict[str, dict] = {}

    for league_key, cfg in FBREF_LEAGUES.items():
        files = cfg.get("local_files", {})
        html_paths: list[tuple[str, bool]] = []
        summary = _read_local(files.get("summary", ""))
        if summary:
            html_paths.append((summary, False))
        shooting_name = files.get("shooting", "")
        shooting = _read_local(shooting_name) if shooting_name else None
        if shooting and shooting != summary:
            html_paths.append((shooting, True))
        elif summary and shooting_name:
            # Same file saved as summary may be the shooting page.
            cols_probe = _pick_player_table(_read_tables(summary))
            if cols_probe is not None and "sot/90" in _cols_blob(cols_probe):
                html_paths = [(summary, True)]

        for html, shoot_only in html_paths:
            parsed = (
                _parse_shooting_html(html, league_key)
                if shoot_only
                else _parse_summary_html(html, league_key)
            )
            for r in parsed:
                key = r.get("fbref_id") or f"{r['norm_name']}|{r['norm_team']}"
                merged[key] = _merge_player_rows(merged.get(key), r)

    all_rows = list(merged.values())
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
            src = hit.get("xg_data_source") or "fbref"
            return {**hit, "xg_data_source": src}

    nn, nt = _norm_name(name), _norm_team(team)
    direct = by_name.get(f"{season}|{nn}|{nt}")
    if direct:
        src = direct.get("xg_data_source") or "fbref"
        return {**direct, "xg_data_source": src}

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
        src = best.get("xg_data_source") or "fbref"
        return {**best, "xg_data_source": src}
    if best and best_score >= 0.92 and not nt:
        src = best.get("xg_data_source") or "fbref"
        return {**best, "xg_data_source": src}

    return None
