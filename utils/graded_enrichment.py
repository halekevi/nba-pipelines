"""
Backfill opponent / top-3 context on graded prop rows for analysis and archives.

Used by:
- analyze_graded_prop_winners.load_unified (runtime enrichment)
- scripts/backfill_graded_def_tier.py (persist to JSON / Box Raw)
- scripts/backtest_stack_70_lift.py
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.defense_tiers import normalize_def_tier_label
from utils.stack_70_eligible import attach_stack_70_columns

_REPO = Path(__file__).resolve().parent.parent
_STRAT_HIT_RATES_CSV = _REPO / "data" / "reports" / "graded_stratification" / "graded_strat_hit_rates.csv"

_DEFENSE_PATHS: dict[str, Path] = {
    "NBA": _REPO / "Sports" / "NBA" / "data" / "cache" / "defense_team_summary.csv",
    "NBA1Q": _REPO / "Sports" / "NBA" / "data" / "cache" / "defense_team_summary.csv",
    "NBA1H": _REPO / "Sports" / "NBA" / "data" / "cache" / "defense_team_summary.csv",
    "WNBA": _REPO / "Sports" / "WNBA" / "wnba_defense_summary.csv",
    "NHL": _REPO / "Sports" / "NHL" / "cache" / "nhl_defense_summary.csv",
    "MLB": _REPO / "Sports" / "MLB" / "mlb_defense_summary.csv",
    "SOCCER": _REPO / "Sports" / "Soccer" / "cache" / "soccer_defense_summary.csv",
}

_OPP_ALIASES: dict[str, dict[str, str]] = {
    "MLB": {
        "AZ": "AZ", "ARI": "AZ", "CHW": "CWS", "CWS": "CWS", "KCR": "KC", "KC": "KC",
        "SDP": "SD", "SD": "SD", "SFG": "SF", "SF": "SF", "TBR": "TB", "TB": "TB",
        "WSN": "WSH", "WAS": "WSH", "WSH": "WSH", "OAK": "ATH", "ATH": "ATH",
    },
    "NBA": {"PHO": "PHX", "PHX": "PHX", "NO": "NOP", "NOP": "NOP", "NY": "NYK", "NYK": "NYK"},
    "NBA1Q": {"PHO": "PHX", "PHX": "PHX", "NO": "NOP", "NOP": "NOP", "NY": "NYK", "NYK": "NYK"},
    "NBA1H": {"PHO": "PHX", "PHX": "PHX", "NO": "NOP", "NOP": "NOP", "NY": "NYK", "NYK": "NYK"},
    "WNBA": {},
    "NHL": {},
    "SOCCER": {},
}

_EMPTY_MARKERS = frozenset({"", "nan", "none", "null", "n/a", "na", "—", "-", "unknown", "(missing)"})
_LOOKUP_CACHE: dict[str, dict[str, str]] = {}


def _repo_root() -> Path:
    return _REPO


def is_empty_value(v: object) -> bool:
    s = str(v or "").strip().lower()
    return s in _EMPTY_MARKERS


def is_empty_def_tier(v: object) -> bool:
    return is_empty_value(v)


def load_defense_lookup(defense_path: Path) -> dict[str, str]:
    key = str(defense_path.resolve())
    if key in _LOOKUP_CACHE:
        return _LOOKUP_CACHE[key]
    d = pd.read_csv(defense_path, dtype=str, encoding="utf-8-sig").fillna("")
    team_col = next(
        (c for c in ("TEAM_ABBREVIATION", "team_abbr", "TEAM_ABBR", "team", "pp_name", "team_name") if c in d.columns),
        None,
    )
    tier_col = next((c for c in ("DEF_TIER", "def_tier") if c in d.columns), None)
    if not team_col or not tier_col:
        _LOOKUP_CACHE[key] = {}
        return {}
    lookup: dict[str, str] = {}
    for _, row in d.iterrows():
        team = str(row[team_col]).strip().upper()
        if not team:
            continue
        tier = normalize_def_tier_label(row[tier_col])
        if tier:
            lookup[team] = tier
    _LOOKUP_CACHE[key] = lookup
    return lookup


def _norm_opp(sport: str, opp: object) -> str:
    raw = str(opp or "").strip().upper()
    if not raw:
        return ""
    aliases = _OPP_ALIASES.get(str(sport).upper(), {})
    parts = [p.strip() for p in raw.split("/") if p.strip()]
    if not parts:
        parts = [raw]
    out: list[str] = []
    for part in parts:
        out.append(aliases.get(part, part))
    return out[0] if out else ""


def tier_for_opp(sport: str, opp: object, lookup: dict[str, str]) -> str:
    key = _norm_opp(sport, opp)
    if not key:
        return ""
    tier = lookup.get(key, "")
    if tier:
        return tier
    aliases = _OPP_ALIASES.get(str(sport).upper(), {})
    alt = aliases.get(key, key)
    return lookup.get(alt, "")


def _sport_key(row: pd.Series) -> str:
    for c in ("_sport", "sport", "Sport"):
        if c in row.index and str(row.get(c, "")).strip():
            return str(row.get(c, "")).strip().upper()
    return ""


def _opp_key(row: pd.Series) -> str:
    for c in ("opp_team", "opp", "Opp", "opponent", "Opp Team"):
        if c in row.index and str(row.get(c, "")).strip():
            return str(row.get(c, "")).strip()
    return ""


def backfill_def_tier_dataframe(df: pd.DataFrame, *, repo: Path | None = None) -> pd.DataFrame:
    """Fill missing def_tier from sport defense summary CSVs via opp_team."""
    if df is None or df.empty:
        return df
    out = df.copy()
    if "def_tier" not in out.columns:
        out["def_tier"] = np.nan
    root = repo or _repo_root()
    sport_series = out.apply(_sport_key, axis=1)
    opp_series = out.apply(_opp_key, axis=1)
    patched = 0
    for sport in sport_series.unique():
        if sport not in _DEFENSE_PATHS:
            continue
        path = _DEFENSE_PATHS[sport]
        if not path.is_file():
            continue
        lookup = load_defense_lookup(path)
        if not lookup:
            continue
        sm = sport_series.eq(sport)
        empty = out.loc[sm, "def_tier"].map(is_empty_def_tier)
        if not empty.any():
            continue
        for idx in out.index[sm & empty]:
            tier = tier_for_opp(sport, opp_series.at[idx], lookup)
            if tier:
                out.at[idx, "def_tier"] = tier
                patched += 1
    return out


def _norm_name(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _norm_prop(s: object) -> str:
    return str(s or "").strip().lower()


def _step8_paths(sport: str, date: str, repo: Path) -> list[Path]:
    sp = sport.lower()
    d = date[:10]
    candidates = [
        repo / "outputs" / d / f"step8_{sp}_direction_clean_{d}.xlsx",
        repo / "outputs" / d / f"step8_{sp}_direction_clean.xlsx",
        repo / "outputs" / d / sp / f"step8_{sp}_direction_clean.xlsx",
        repo / "Sports" / sport / f"step8_{sp}_direction_clean.xlsx",
        repo / "Sports" / sport / "outputs" / f"step8_{sp}_direction_clean.xlsx",
    ]
    if sport == "WNBA":
        candidates.insert(0, repo / "Sports" / "WNBA" / "outputs" / "step8_wnba_direction_clean.xlsx")
    return [p for p in candidates if p.is_file()]


def backfill_def_tier_from_step8(df: pd.DataFrame, *, repo: Path | None = None) -> pd.DataFrame:
    """Second-pass def_tier fill by joining dated step8 slates on player+prop+line."""
    if df is None or df.empty:
        return df
    out = df.copy()
    if "def_tier" not in out.columns:
        out["def_tier"] = np.nan
    root = repo or _repo_root()
    date_col = "_slate_date" if "_slate_date" in out.columns else "file_date"
    if date_col not in out.columns:
        return out

    sport_keys = out.apply(_sport_key, axis=1)
    date_keys = out[date_col].astype(str).str[:10]
    for (sport, date), grp in out.groupby([sport_keys, date_keys], sort=False):
        if not sport or not date or date == "nan":
            continue
        need = grp.index[grp["def_tier"].map(is_empty_def_tier)]
        if len(need) == 0:
            continue
        paths = _step8_paths(sport, date, root)
        if not paths:
            continue
        try:
            s8 = pd.read_excel(paths[0], sheet_name=0, engine="openpyxl")
        except Exception:
            continue
        s8.columns = [str(c).strip() for c in s8.columns]
        ren = {
            "Player": "player", "Prop": "prop_type", "Line": "line",
            "Direction": "direction", "Def Tier": "def_tier", "Opp": "opp_team",
        }
        s8 = s8.rename(columns={k: v for k, v in ren.items() if k in s8.columns})
        if "def_tier" not in s8.columns:
            continue
        s8 = s8[s8["def_tier"].map(lambda x: not is_empty_def_tier(x))].copy()
        if s8.empty:
            continue
        s8["_pk"] = (
            s8.get("player", "").astype(str).map(_norm_name)
            + "|"
            + s8.get("prop_type", "").astype(str).map(_norm_prop)
            + "|"
            + pd.to_numeric(s8.get("line"), errors="coerce").round(2).astype(str)
        )
        tier_map = s8.drop_duplicates("_pk").set_index("_pk")["def_tier"].to_dict()
        for idx in need:
            pk = (
                _norm_name(out.at[idx, "player"] if "player" in out.columns else "")
                + "|"
                + _norm_prop(
                    out.at[idx, "prop"]
                    if "prop" in out.columns
                    else out.at[idx, "prop_type"] if "prop_type" in out.columns else ""
                )
                + "|"
                + str(round(float(pd.to_numeric(out.at[idx, "line"], errors="coerce") or 0), 2))
            )
            tier = tier_map.get(pk, "")
            if tier and not is_empty_def_tier(tier):
                out.at[idx, "def_tier"] = normalize_def_tier_label(tier)
    return out


def _norm_prop_display(prop: object) -> str:
    s = str(prop or "").strip().lower()
    return re.sub(r"\s+", " ", s)


def _pick_type_group(pick: object, direction: object) -> str:
    p = str(pick or "").strip().upper()
    d = str(direction or "").strip().upper()
    if "GOBLIN" in p:
        return "Goblin"
    if "DEMON" in p:
        return "Demon"
    if d in {"UNDER", "LOWER"}:
        return "Standard UNDER"
    return "Standard OVER"


def attach_strat_hit_rates(df: pd.DataFrame, *, min_n: int = 30) -> pd.DataFrame:
    """Attach strat_hit_rate / strat_n from graded stratification report CSV."""
    if df is None or df.empty or not _STRAT_HIT_RATES_CSV.is_file():
        return df
    try:
        strat = pd.read_csv(_STRAT_HIT_RATES_CSV, encoding="utf-8-sig")
    except Exception:
        return df
    if strat.empty:
        return df
    strat["n"] = pd.to_numeric(strat["n"], errors="coerce")
    strat["hit_rate"] = pd.to_numeric(strat["hit_rate"], errors="coerce")
    strat = strat[strat["n"] >= min_n].copy()
    if strat.empty:
        return df

    strat["_sport"] = strat["sport_disp"].astype(str).str.upper().str.strip()
    strat["_prop"] = strat["prop"].map(_norm_prop_display)
    strat["_dir"] = strat["direction"].astype(str).str.upper().str.strip()
    strat["_pick"] = strat["pick_type_group"].astype(str).str.strip()
    strat["_def"] = strat.get("def_tier_norm", pd.Series("", index=strat.index)).astype(str).str.strip()

    coarse = (
        strat.sort_values("n", ascending=False)
        .drop_duplicates(subset=["_sport", "_prop", "_dir", "_pick"], keep="first")
    )
    coarse_lut = coarse.set_index(["_sport", "_prop", "_dir", "_pick"])[["hit_rate", "n"]]

    fine = (
        strat[strat["_def"].ne("") & ~strat["_def"].str.lower().eq("(missing)")]
        .sort_values("n", ascending=False)
        .drop_duplicates(subset=["_sport", "_prop", "_dir", "_pick", "_def"], keep="first")
    )
    fine_lut = (
        fine.set_index(["_sport", "_prop", "_dir", "_pick", "_def"])[["hit_rate", "n"]]
        if not fine.empty
        else None
    )

    out = df.copy()
    sport_s = out.get("_sport", out.get("sport", pd.Series("", index=out.index))).astype(str).str.upper().str.strip()
    prop_col = next((c for c in ("prop", "prop_type", "prop_type_norm", "Prop") if c in out.columns), None)
    dir_col = next((c for c in ("direction", "bet_direction", "Direction") if c in out.columns), None)
    pick_col = "pick_type" if "pick_type" in out.columns else None
    if not prop_col or not dir_col:
        return out

    props = out[prop_col].map(_norm_prop_display)
    dirs = out[dir_col].astype(str).str.upper().str.strip()
    picks = pd.Series(
        [
            _pick_type_group(out.at[i, pick_col] if pick_col else "", out.at[i, dir_col])
            for i in out.index
        ],
        index=out.index,
    )
    defs = out.get("def_tier", pd.Series("", index=out.index)).map(normalize_def_tier_label).astype(str).str.strip()

    hr_vals: list[float] = []
    n_vals: list[float] = []
    for i in out.index:
        key = (sport_s.loc[i], props.loc[i], dirs.loc[i], picks.loc[i])
        row = None
        if fine_lut is not None:
            dkey = (*key, defs.loc[i])
            if dkey in fine_lut.index:
                row = fine_lut.loc[dkey]
        if row is None and key in coarse_lut.index:
            row = coarse_lut.loc[key]
        if row is None:
            hr_vals.append(np.nan)
            n_vals.append(np.nan)
        else:
            hr_vals.append(float(row["hit_rate"]))
            n_vals.append(float(row["n"]))
    out["strat_hit_rate"] = hr_vals
    out["strat_n"] = n_vals
    return out


def enrich_graded_for_analysis(
    df: pd.DataFrame,
    *,
    repo: Path | None = None,
    stack_eligible: bool = False,
) -> pd.DataFrame:
    """Backfill def_tier then attach top-3 context; optional stack_70_eligible (slow on large frames)."""
    if df is None or df.empty:
        return df
    out = backfill_def_tier_dataframe(df, repo=repo)
    out = backfill_def_tier_from_step8(out, repo=repo)
    if "sport" not in out.columns and "_sport" in out.columns:
        out["sport"] = out["_sport"]
    out = attach_strat_hit_rates(out)
    out = attach_stack_70_columns(out, repo=repo, compute_eligible=stack_eligible)
    return out


def coverage_summary(df: pd.DataFrame) -> dict[str, Any]:
    """Quick before/after stats for def_tier and top-3 flags."""
    total = len(df)
    if total == 0:
        return {"n": 0}
    def_known = int((~df.get("def_tier", pd.Series(dtype=object)).map(is_empty_def_tier)).sum()) if "def_tier" in df.columns else 0
    top3_known = 0
    if "team_top3_rank" in df.columns:
        top3_known = int(pd.to_numeric(df["team_top3_rank"], errors="coerce").notna().sum())
    stack_n = int(df["stack_70_eligible"].fillna(False).sum()) if "stack_70_eligible" in df.columns else 0
    strat_known = int(pd.to_numeric(df.get("strat_hit_rate"), errors="coerce").notna().sum()) if "strat_hit_rate" in df.columns else 0
    return {
        "n": total,
        "def_tier_known": def_known,
        "def_tier_pct": round(100.0 * def_known / total, 1),
        "top3_known": top3_known,
        "top3_pct": round(100.0 * top3_known / total, 1),
        "strat_known": strat_known,
        "strat_pct": round(100.0 * strat_known / total, 1),
        "stack_70_eligible": stack_n,
        "stack_70_pct": round(100.0 * stack_n / total, 1),
    }
