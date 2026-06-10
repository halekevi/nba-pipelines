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
from utils.stack_context_cols import GRADED_SIGNAL_COLS, STACK_CONTEXT_COLS

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
_STEP8_LOOKUP_CACHE: dict[tuple[str, str], dict[str, dict[str, object]]] = {}

_STEP8_RENAME: dict[str, str] = {
    "Player": "player",
    "Prop": "prop_type",
    "Line": "line",
    "Direction": "direction",
    "Def Tier": "def_tier",
    "Opp": "opp_team",
    "L5 Over": "l5_over",
    "L5 Under": "l5_under",
    "last5_over": "l5_over",
    "last5_under": "l5_under",
    "over_L5_raw": "l5_over",
    "under_L5_raw": "l5_under",
    "Hit Rate (5g)": "hit_rate",
    "line_hit_rate_over_ou_5": "hit_rate",
    "composite_hr": "hit_rate",
    "hit_rate": "hit_rate",
    "Consistency Grade": "consistency_grade",
    "Top3 Rank": "team_top3_rank",
    "Bottom3 Rank": "team_bottom3_rank",
    "Top3 Weak Over": "top3_weak_overperformer",
    "Top3 Elite Fade": "top3_elite_fader",
}


def _repo_root() -> Path:
    return _REPO


def is_empty_value(v: object) -> bool:
    s = str(v or "").strip().lower()
    return s in _EMPTY_MARKERS


def is_empty_def_tier(v: object) -> bool:
    return is_empty_value(v)


def is_empty_numeric(v: object) -> bool:
    return pd.isna(pd.to_numeric(v, errors="coerce"))


def _row_pk(player: object, prop: object, line: object) -> str:
    return (
        f"{_norm_name(player)}|{_norm_prop(prop)}|"
        f"{round(float(pd.to_numeric(line, errors='coerce') or 0), 2)}"
    )


def _scalar_cell(val: object) -> object:
    if isinstance(val, pd.Series):
        non_null = val.dropna()
        return non_null.iloc[0] if not non_null.empty else np.nan
    return val


def _get_step8_lookup(sport: str, date: str, repo: Path) -> dict[str, dict[str, object]]:
    cache_key = (sport, date)
    if cache_key in _STEP8_LOOKUP_CACHE:
        return _STEP8_LOOKUP_CACHE[cache_key]
    paths = _step8_paths(sport, date, repo)
    if not paths:
        _STEP8_LOOKUP_CACHE[cache_key] = {}
        return {}
    try:
        s8 = pd.read_excel(paths[0], sheet_name=0, engine="openpyxl")
    except Exception:
        _STEP8_LOOKUP_CACHE[cache_key] = {}
        return {}
    s8 = s8.loc[:, ~pd.Index(s8.columns).duplicated()].copy()
    s8.columns = [str(c).strip() for c in s8.columns]
    s8 = s8.reset_index(drop=True)
    s8 = s8.rename(columns={k: v for k, v in _STEP8_RENAME.items() if k in s8.columns})
    for col in STACK_CONTEXT_COLS:
        if col in s8.columns:
            continue
        display = col.replace("_", " ").title()
        if display in s8.columns:
            s8 = s8.rename(columns={display: col})
    if "player" not in s8.columns or "prop_type" not in s8.columns:
        _STEP8_LOOKUP_CACHE[cache_key] = {}
        return {}
    lookup: dict[str, dict[str, object]] = {}
    for _, row in s8.iterrows():
        pk = _row_pk(row.get("player"), row.get("prop_type"), row.get("line"))
        payload: dict[str, object] = {}
        for field in GRADED_SIGNAL_COLS:
            if field not in row.index:
                continue
            val = _scalar_cell(row.get(field))
            if field == "def_tier":
                if not is_empty_def_tier(val):
                    payload[field] = normalize_def_tier_label(val)
            elif field in {
                "l5_over",
                "l5_under",
                "strat_n",
                "top3_weak_overperformer",
                "top3_elite_fader",
                "top3_def_context",
                "top3_under_context",
            }:
                num = pd.to_numeric(val, errors="coerce")
                if pd.notna(num):
                    payload[field] = int(num)
            elif field in {"team_top3_rank", "team_bottom3_rank", "def_boost_hist"}:
                num = pd.to_numeric(val, errors="coerce")
                if pd.notna(num):
                    payload[field] = float(num)
            elif field in ("hit_rate", "strat_hit_rate"):
                num = pd.to_numeric(val, errors="coerce")
                if pd.notna(num):
                    payload[field] = float(num)
            elif not is_empty_value(val):
                payload[field] = val
        if payload:
            lookup[pk] = payload
    _STEP8_LOOKUP_CACHE[cache_key] = lookup
    return lookup


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


_SIGNAL_STRING_COLS = frozenset({"def_tier", "consistency_grade"})
_SIGNAL_INT_COLS = frozenset({
    "l5_over",
    "l5_under",
    "strat_n",
    "top3_weak_overperformer",
    "top3_elite_fader",
    "top3_def_context",
    "top3_under_context",
})


def _init_signal_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in GRADED_SIGNAL_COLS:
        if col not in out.columns:
            out[col] = "" if col in _SIGNAL_STRING_COLS else np.nan
        elif col in _SIGNAL_STRING_COLS and pd.api.types.is_numeric_dtype(out[col]):
            out[col] = out[col].astype(object)
    return out


def _assign_signal_value(df: pd.DataFrame, idx: object, field: str, val: object) -> None:
    if field in _SIGNAL_STRING_COLS:
        if pd.api.types.is_numeric_dtype(df[field]):
            df[field] = df[field].astype(object)
        df.at[idx, field] = str(val) if val is not None and not (isinstance(val, float) and np.isnan(val)) else ""
        return
    if field in _SIGNAL_INT_COLS:
        num = pd.to_numeric(val, errors="coerce")
        if pd.notna(num):
            df.at[idx, field] = int(num)
        return
    num = pd.to_numeric(val, errors="coerce")
    if pd.notna(num):
        df.at[idx, field] = float(num)


def _field_empty(col: str, val: object) -> bool:
    if col == "def_tier":
        return is_empty_def_tier(val)
    if col in {"l5_over", "l5_under", "hit_rate", "strat_hit_rate", "strat_n"}:
        return is_empty_numeric(val)
    if col in {"top3_weak_overperformer", "top3_elite_fader", "top3_def_context", "top3_under_context"}:
        return int(np.nan_to_num(pd.to_numeric(val, errors="coerce"), nan=0.0)) == 0
    if col in {"team_top3_rank", "team_bottom3_rank", "def_boost_hist"}:
        return is_empty_numeric(val)
    return is_empty_value(val)


def backfill_from_step8(df: pd.DataFrame, *, repo: Path | None = None) -> pd.DataFrame:
    """Fill def_tier, L5 counts, hit_rate, and stack context from dated step8 slates."""
    if df is None or df.empty:
        return df
    out = _init_signal_columns(df)
    root = repo or _repo_root()
    date_col = "_slate_date" if "_slate_date" in out.columns else "file_date"
    if date_col not in out.columns:
        return out

    sport_keys = out.apply(_sport_key, axis=1)
    date_keys = out[date_col].astype(str).str[:10]
    prop_col = next((c for c in ("prop", "prop_type", "prop_type_norm") if c in out.columns), None)
    for (sport, date), grp in out.groupby([sport_keys, date_keys], sort=False):
        if not sport or not date or date == "nan":
            continue
        lookup = _get_step8_lookup(sport, date, root)
        if not lookup:
            continue
        for idx in grp.index:
            prop_val = out.at[idx, prop_col] if prop_col else ""
            pk = _row_pk(out.at[idx, "player"] if "player" in out.columns else "", prop_val, out.at[idx, "line"] if "line" in out.columns else "")
            payload = lookup.get(pk)
            if not payload:
                continue
            for field, val in payload.items():
                if field not in out.columns:
                    continue
                if not _field_empty(field, out.at[idx, field]):
                    continue
                _assign_signal_value(out, idx, field, val)
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


def _coalesce_l5_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    pairs = (
        ("l5_over", ("l5_over", "last5_over", "L5 Over")),
        ("l5_under", ("l5_under", "last5_under", "L5 Under")),
        ("hit_rate", ("hit_rate", "last5_hit_rate", "Hit Rate (5g)", "line_hit_rate_over_ou_5", "composite_hr")),
    )
    for target, alts in pairs:
        if target not in out.columns:
            out[target] = np.nan
        series = pd.to_numeric(out[target], errors="coerce")
        for alt in alts:
            if alt in out.columns and alt != target:
                series = series.combine_first(pd.to_numeric(out[alt], errors="coerce"))
        out[target] = series
    return out


def enrich_graded_for_analysis(
    df: pd.DataFrame,
    *,
    repo: Path | None = None,
    stack_eligible: bool = False,
    attach_context: bool = True,
) -> pd.DataFrame:
    """Backfill def_tier then attach top-3 context; optional stack_70_eligible (slow on large frames)."""
    if df is None or df.empty:
        return df
    out = _coalesce_l5_columns(df)
    out = backfill_def_tier_dataframe(out, repo=repo)
    out = backfill_from_step8(out, repo=repo)
    if "sport" not in out.columns and "_sport" in out.columns:
        out["sport"] = out["_sport"]
    out = attach_strat_hit_rates(out)
    if attach_context:
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
    l5_known = 0
    if "l5_over" in df.columns:
        l5_known = int(pd.to_numeric(df["l5_over"], errors="coerce").notna().sum())
    return {
        "n": total,
        "def_tier_known": def_known,
        "def_tier_pct": round(100.0 * def_known / total, 1),
        "top3_known": top3_known,
        "top3_pct": round(100.0 * top3_known / total, 1),
        "strat_known": strat_known,
        "strat_pct": round(100.0 * strat_known / total, 1),
        "l5_over_known": l5_known,
        "l5_over_pct": round(100.0 * l5_known / total, 1),
        "stack_70_eligible": stack_n,
        "stack_70_pct": round(100.0 * stack_n / total, 1),
    }
