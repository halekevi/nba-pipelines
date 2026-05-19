#!/usr/bin/env python3
"""Bart Torvik team ratings — manual CSV + JSON cache (no live scraping)."""

from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd

from cbb_team_map import ABBR_TO_SR

_REPO = Path(__file__).resolve().parents[1]
REF_CSV = _REPO / "data" / "reference" / "torvik_team_ratings.csv"
CACHE_PATH = _REPO / "data" / "torvik_team_cache.json"

_COL_MAP = {
    "team": ("team", "school", "name", "team_name"),
    "adj_o": ("adj_o", "adjo", "adj_off", "adj_offense", "adjoe"),
    "adj_d": ("adj_d", "adjd", "adj_def", "adj_defense", "adjde"),
    "adj_em": ("adj_em", "adjem", "adj_eff", "barthag"),
    "tempo": ("tempo", "adj_t", "adj_tempo", "t"),
}


def _norm_key(s: object) -> str:
    t = unicodedata.normalize("NFD", str(s or ""))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^A-Z0-9& ]+", " ", t.upper()).strip()
    t = re.sub(r"\s+", " ", t)
    return t.replace("&AMP;", "&")


def _pick_col(df: pd.DataFrame, keys: tuple[str, ...]) -> str | None:
    lower = {c.lower().strip(): c for c in df.columns}
    for k in keys:
        if k in lower:
            return lower[k]
    return None


def _load_reference_csv(path: Path | None = None) -> pd.DataFrame:
    p = path or REF_CSV
    if not p.is_file():
        return pd.DataFrame()
    df = pd.read_csv(p, encoding="utf-8-sig")
    team_c = _pick_col(df, _COL_MAP["team"])
    if not team_c:
        raise ValueError(f"torvik CSV missing team column: {p}")
    out = pd.DataFrame()
    out["team"] = df[team_c].astype(str).str.strip()
    for dst, keys in _COL_MAP.items():
        if dst == "team":
            continue
        src = _pick_col(df, keys)
        if src:
            out[dst] = pd.to_numeric(df[src], errors="coerce")
    if "adj_em" not in out.columns or out["adj_em"].isna().all():
        if "adj_o" in out.columns and "adj_d" in out.columns:
            out["adj_em"] = out["adj_o"] - out["adj_d"]
    return out.dropna(subset=["team"])


def _fuzzy_match(name: str, keys: list[str], threshold: float = 0.88) -> str | None:
    nk = _norm_key(name)
    if nk in keys:
        return nk
    best_k, best_s = None, 0.0
    for k in keys:
        s = SequenceMatcher(None, nk, k).ratio()
        if s > best_s:
            best_s, best_k = s, k
    return best_k if best_s >= threshold else None


def refresh_cache(season: str, ref_path: Path | None = None) -> dict[str, Any]:
    df = _load_reference_csv(ref_path)
    if df.empty:
        cache = {"seasons": {}}
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        return cache

    tempos = df["tempo"].dropna().tolist() if "tempo" in df.columns else []
    tempos_sorted = sorted(tempos)
    t_lo = tempos_sorted[len(tempos_sorted) // 3] if len(tempos_sorted) >= 3 else None
    t_hi = tempos_sorted[(2 * len(tempos_sorted)) // 3] if len(tempos_sorted) >= 3 else None

    teams: dict[str, dict[str, Any]] = {}
    for _, row in df.iterrows():
        key = _norm_key(row["team"])
        tempo = float(row["tempo"]) if pd.notna(row.get("tempo")) else None
        pace = "medium"
        if tempo is not None and t_lo is not None and t_hi is not None:
            if tempo <= t_lo:
                pace = "slow"
            elif tempo >= t_hi:
                pace = "fast"
        teams[key] = {
            "team": row["team"],
            "adj_o": float(row["adj_o"]) if pd.notna(row.get("adj_o")) else None,
            "adj_d": float(row["adj_d"]) if pd.notna(row.get("adj_d")) else None,
            "adj_em": float(row["adj_em"]) if pd.notna(row.get("adj_em")) else None,
            "tempo": tempo,
            "pace_context": pace,
        }

    cache = {"seasons": {season: {"teams": teams, "tempo_tertiles": {"lo": t_lo, "hi": t_hi}}}}
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    return cache


def load_cache() -> dict[str, Any]:
    if not CACHE_PATH.is_file():
        return {"seasons": {}}
    return json.loads(CACHE_PATH.read_text(encoding="utf-8"))


def lookup_team(abbr_or_name: str, season: str, cache: dict[str, Any] | None = None) -> dict[str, Any] | None:
    cache = cache or load_cache()
    season_data = (cache.get("seasons") or {}).get(season) or {}
    teams: dict[str, dict] = season_data.get("teams") or {}
    if not teams:
        return None
    keys = list(teams.keys())

    raw = str(abbr_or_name or "").strip()
    abbr = raw.upper()
    sr = ABBR_TO_SR.get(abbr, raw)
    for candidate in (sr, raw):
        k = _fuzzy_match(candidate, keys)
        if k:
            return teams[k]
    return None
