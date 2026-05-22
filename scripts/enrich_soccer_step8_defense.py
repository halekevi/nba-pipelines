#!/usr/bin/env python3
"""Merge soccer defense DB fields onto an existing step8 clean xlsx (historical slates)."""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from difflib import get_close_matches
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_STRIP_TOKENS = frozenset(
    {
        "fc",
        "sc",
        "afc",
        "cf",
        "sl",
        "sd",
        "ca",
        "cd",
        "ac",
        "as",
        "rc",
        "rcd",
        "ud",
        "ce",
        "cp",
        "fk",
        "bk",
        "rb",
    }
)

_ALIASES: dict[str, str] = {
    "man city": "manchester city",
    "man utd": "manchester united",
    "manchester utd": "manchester united",
    "spurs": "tottenham hotspur",
    "tottenham": "tottenham hotspur",
    "psg": "paris saint-germain",
    "paris sg": "paris saint-germain",
    "paris saint germain": "paris saint-germain",
    "atletico": "atletico madrid",
    "atletico de madrid": "atletico madrid",
    "fc barcelona": "barcelona",
    "barca": "barcelona",
    "inter": "inter milan",
    "internazionale": "inter milan",
    "ac milan": "milan",
    "juve": "juventus",
    "dortmund": "borussia dortmund",
    "bvb": "borussia dortmund",
    "leverkusen": "bayer leverkusen",
    "bayer 04": "bayer leverkusen",
    "lyon": "olympique lyonnais",
    "marseille": "olympique de marseille",
    "monaco": "as monaco",
    "nice": "ogc nice",
    "roma": "as roma",
    "lazio": "ss lazio",
    "napoli": "ssc napoli",
    "celta": "celta vigo",
    "celta de vigo": "celta vigo",
    "betis": "real betis",
    "sociedad": "real sociedad",
}


def normalize_opp(raw: str) -> str:
    """Normalize opponent team text for defense DB lookup."""
    s = unicodedata.normalize("NFKD", str(raw or ""))
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("`", "'")
    s = re.sub(r"[-–—/]+", " ", s)
    s = re.sub(r"[^\w\s']", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    if s in _ALIASES:
        return _ALIASES[s]

    parts = s.split()
    changed = True
    while parts and changed:
        changed = False
        if parts and parts[0] in _STRIP_TOKENS:
            parts = parts[1:]
            changed = True
        if parts and parts[-1] in _STRIP_TOKENS:
            parts = parts[:-1]
            changed = True
    s = " ".join(parts).strip()
    if s in _ALIASES:
        return _ALIASES[s]

    parts = s.split()
    while parts:
        trial = " ".join(parts).strip()
        if trial in _ALIASES:
            return _ALIASES[trial]
        if not parts:
            break
        parts = parts[:-1]
    return s


def _load_defense() -> pd.DataFrame:
    soc_scripts = _REPO / "Sports" / "Soccer" / "scripts"
    if str(soc_scripts) not in sys.path:
        sys.path.insert(0, str(soc_scripts))
    from defense_db import load_defense_from_db  # type: ignore

    d = load_defense_from_db("soccer")
    if not isinstance(d, pd.DataFrame) or d.empty:
        cache = _REPO / "Sports" / "Soccer" / "cache" / "soccer_defense_summary.csv"
        if cache.is_file():
            d = pd.read_csv(cache, encoding="utf-8-sig", low_memory=False)
        else:
            raise SystemExit("No soccer defense DB or cache available")
    key = "pp_name" if "pp_name" in d.columns else ("team_name" if "team_name" in d.columns else None)
    if not key:
        raise SystemExit("Defense table missing pp_name / team_name")
    keep = [key]
    for c in (
        "DEF_TIER",
        "def_tier",
        "OVERALL_DEF_RANK",
        "opp_gf_per_game",
        "OPP_PPG",
        "opp_gaa",
        "goals_conceded_pg",
        "league",
    ):
        if c in d.columns:
            keep.append(c)
    return d[keep].copy()


def _build_defense_lookup(def_df: pd.DataFrame) -> tuple[dict[str, dict], list[str]]:
    key_col = "pp_name" if "pp_name" in def_df.columns else "team_name"
    lookup: dict[str, dict] = {}
    for _, row in def_df.iterrows():
        raw_key = row.get(key_col, "")
        norm = normalize_opp(raw_key)
        if not norm or norm in lookup:
            continue
        tier = row.get("DEF_TIER", row.get("def_tier", ""))
        pace = None
        for pc in ("opp_gf_per_game", "OPP_PPG", "opp_gaa", "goals_conceded_pg"):
            if pc in row.index and pd.notna(row.get(pc)):
                pace = row.get(pc)
                break
        lookup[norm] = {
            "DEF_TIER": tier,
            "def_tier": tier,
            "OVERALL_DEF_RANK": row.get("OVERALL_DEF_RANK"),
            "opp_pace": pace,
        }
    return lookup, sorted(lookup.keys())


def _resolve_opp_key(raw_opp: str, lookup: dict[str, dict], db_keys: list[str]) -> tuple[str | None, dict | None]:
    norm = normalize_opp(raw_opp)
    if norm in lookup:
        return norm, lookup[norm]
    hits = get_close_matches(norm, db_keys, n=1, cutoff=0.72)
    if hits:
        return hits[0], lookup[hits[0]]
    return None, None


def _attach_defense_columns(df: pd.DataFrame, opp_col: str, lookup: dict[str, dict], db_keys: list[str]) -> pd.DataFrame:
    out = df.copy()
    if "Def Tier" not in out.columns:
        out["Def Tier"] = pd.NA
    if "Opp Pace" not in out.columns:
        out["Opp Pace"] = pd.NA
    if "opp_pace_zscore" not in out.columns:
        out["opp_pace_zscore"] = pd.NA
    if "Def Rank" not in out.columns:
        out["Def Rank"] = pd.NA

    for idx, raw in out[opp_col].items():
        raw_s = str(raw or "").strip()
        if not raw_s or raw_s.lower() == "nan":
            continue
        _key, rec = _resolve_opp_key(raw_s, lookup, db_keys)
        if rec is None:
            print(f"  [Soccer enrich] unmatched: {raw_s!r}")
            continue
        tier = rec.get("DEF_TIER", "")
        if pd.isna(out.at[idx, "Def Tier"]) or str(out.at[idx, "Def Tier"]).strip() in ("", "nan"):
            out.at[idx, "Def Tier"] = tier
        if pd.isna(out.at[idx, "Def Rank"]) and rec.get("OVERALL_DEF_RANK") is not None:
            out.at[idx, "Def Rank"] = rec["OVERALL_DEF_RANK"]
        pace = rec.get("opp_pace")
        if pace is not None and not (isinstance(pace, float) and pd.isna(pace)):
            if pd.isna(out.at[idx, "Opp Pace"]) or str(out.at[idx, "Opp Pace"]).strip() in ("", "nan"):
                out.at[idx, "Opp Pace"] = pace
            if "opp_pace_zscore" in out.columns and (
                pd.isna(out.at[idx, "opp_pace_zscore"])
                or str(out.at[idx, "opp_pace_zscore"]).strip() in ("", "nan")
            ):
                out.at[idx, "opp_pace_zscore"] = pace

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="step8_soccer_direction_clean*.xlsx")
    ap.add_argument("--output", default="", help="default: overwrite input")
    args = ap.parse_args()

    inp = Path(args.input).resolve()
    out = Path(args.output or inp).resolve()
    df = pd.read_excel(inp, engine="openpyxl")
    if df.empty:
        print(f"[enrich] empty input: {inp}")
        return 1

    opp_col = None
    for c in ("Opp", "opp_team", "OPP", "Opponent"):
        if c in df.columns:
            opp_col = c
            break
    if not opp_col:
        print(f"[enrich] no Opp column in {inp.name}; cols={list(df.columns)[:20]}")
        return 1

    def_df = _load_defense()
    lookup, db_keys = _build_defense_lookup(def_df)
    merged = _attach_defense_columns(df, opp_col, lookup, db_keys)

    tier_fill = 0
    if "Def Tier" in merged.columns:
        tier_fill = int(
            (
                merged["Def Tier"].notna()
                & (merged["Def Tier"].astype(str).str.strip() != "")
                & (merged["Def Tier"].astype(str).str.lower() != "nan")
            ).sum()
        )
    pace_fill = 0
    if "Opp Pace" in merged.columns:
        pace_fill = int(pd.to_numeric(merged["Opp Pace"], errors="coerce").notna().sum())

    out.parent.mkdir(parents=True, exist_ok=True)
    merged.to_excel(out, index=False, engine="openpyxl")
    print(f"[enrich] {inp.name} -> {out.name}  rows={len(merged):,}  Def Tier={tier_fill:,}  Opp Pace={pace_fill:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
