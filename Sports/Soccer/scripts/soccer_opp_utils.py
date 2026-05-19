#!/usr/bin/env python3
"""Shared opponent resolution for Soccer pipeline steps."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, Set, Tuple

import pandas as pd

_REPO = Path(__file__).resolve().parents[3]
ALIASES_CSV = Path(__file__).resolve().parent.parent / "data" / "soccer_team_aliases.csv"

_UNKNOWN_OPP = frozenset({"", "NAN", "NONE", "NULL", "UNKNOWN", "UNKNOWN_OPP"})


def atomic_teams(label: object) -> Set[str]:
    """Split combo labels (A/B) into atomic team tokens for pairing."""
    s = str(label or "").strip().upper()
    if not s or s in _UNKNOWN_OPP:
        return set()
    if "/" in s:
        return {p.strip() for p in s.split("/") if p.strip() and p.strip() not in _UNKNOWN_OPP}
    return {s}


def game_pair_map(df: pd.DataFrame) -> Dict[str, Tuple[str, str]]:
    """
    Map pp_game_id -> (TEAM_A, TEAM_B) using atomic teams across all rows in the game.
    Combo rows like MAN CITY/BOURNEMOUTH still contribute both atomic sides.
    """
    if "pp_game_id" not in df.columns or "team" not in df.columns:
        return {}
    tmp = df[["pp_game_id", "team"]].copy()
    tmp["pp_game_id"] = tmp["pp_game_id"].astype(str).str.strip()
    pairs: Dict[str, Tuple[str, str]] = {}
    for gid, g in tmp.groupby("pp_game_id", dropna=False):
        gid = str(gid).strip()
        if not gid:
            continue
        atoms: Set[str] = set()
        for raw in g["team"]:
            atoms.update(atomic_teams(raw))
        if len(atoms) == 2:
            a, b = sorted(atoms)
            pairs[gid] = (a, b)
    return pairs


def opp_from_pair(gid: str, team: str, pair_map: Dict[str, Tuple[str, str]]) -> str:
    """Return the other team in a two-team fixture, or '' if unknown."""
    tset = atomic_teams(team)
    if len(tset) != 1:
        return ""
    t = next(iter(tset))
    p = pair_map.get(str(gid or "").strip())
    if not p:
        return ""
    a, b = p
    if t == a:
        return b
    if t == b:
        return a
    return ""


def fill_opp_team_column(df: pd.DataFrame) -> pd.DataFrame:
    """Fill blank opp_team using home/away, then atomic game pairing."""
    if df is None or df.empty:
        return df
    out = df.copy()
    if "opp_team" not in out.columns:
        out["opp_team"] = ""

    team = out.get("team", pd.Series("", index=out.index)).astype(str).str.strip().str.upper()
    home = out.get("pp_home_team", pd.Series("", index=out.index)).astype(str).str.strip().str.upper()
    away = out.get("pp_away_team", pd.Series("", index=out.index)).astype(str).str.strip().str.upper()
    opp = out["opp_team"].astype(str).str.strip().str.upper()
    opp = opp.mask(opp.isin(_UNKNOWN_OPP), "")

    has_both = (home != "") & (away != "") & (team != "")
    opp = opp.where(~(has_both & (team == home)), away)
    opp = opp.where(~(has_both & (team == away)), home)

    pair_map = game_pair_map(out)
    if pair_map and "pp_game_id" in out.columns:
        gid = out["pp_game_id"].astype(str).str.strip()
        need = opp.eq("")
        if need.any():
            inferred = [
                opp_from_pair(g, t, pair_map)
                for g, t in zip(gid[need], team[need])
            ]
            opp.loc[need] = inferred

    # Combo rows: team_1 / team_2 when present
    if "team_1" in out.columns and "team_2" in out.columns and pair_map:
        combo_mask = out.get("is_combo_player", "0").astype(str).isin(["1", "True", "true"])
        if combo_mask.any():
            gid = out["pp_game_id"].astype(str).str.strip()
            t1 = out["team_1"].astype(str).str.strip().str.upper()
            t2 = out["team_2"].astype(str).str.strip().str.upper()
            o1 = [opp_from_pair(g, t, pair_map) for g, t in zip(gid[combo_mask], t1[combo_mask])]
            o2 = [opp_from_pair(g, t, pair_map) for g, t in zip(gid[combo_mask], t2[combo_mask])]
            combo_opp = (pd.Series(o1, index=out.index[combo_mask]).astype(str) + "/" +
                         pd.Series(o2, index=out.index[combo_mask]).astype(str)).str.strip("/")
            opp.loc[combo_mask] = combo_opp.values

    out["opp_team"] = opp.mask(opp.isin(_UNKNOWN_OPP), "")
    return out


def load_team_aliases() -> Dict[str, str]:
    """Load pp_name -> canonical_name map from CSV (append-only file)."""
    aliases: Dict[str, str] = {}
    if not ALIASES_CSV.is_file():
        return aliases
    try:
        tbl = pd.read_csv(ALIASES_CSV, dtype=str, encoding="utf-8-sig").fillna("")
        for _, r in tbl.iterrows():
            src = str(r.get("pp_name", "")).strip().upper()
            dst = str(r.get("canonical_name", "")).strip().upper()
            if src and dst:
                aliases[src] = dst
    except Exception:
        pass
    return aliases


def apply_team_aliases(series: pd.Series, aliases: Dict[str, str] | None = None) -> pd.Series:
    amap = aliases if aliases is not None else load_team_aliases()
    if not amap:
        return series.astype(str).str.strip().str.upper()
    return series.astype(str).str.strip().str.upper().map(lambda x: amap.get(x, x))


def sanitize_unknown_opp(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip().str.upper()
    return s.mask(s.isin(_UNKNOWN_OPP), "")
