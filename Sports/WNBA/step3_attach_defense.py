#!/usr/bin/env python3
"""
step3_attach_defense.py  (WNBA Pipeline)

Attaches opponent defensive context to each prop row.
Identical logic to NBA step3 — left-merge on opp_team vs defense CSV.

Defense file: wnba_defense_summary.csv
  Must include TEAM_ABBREVIATION (or team_abbr) + OVERALL_DEF_RANK + DEF_TIER.

Key difference from NBA: WNBA has fewer teams; ``wnba_defense_summary.csv`` uses
``utils.defense_tiers`` quintiles on the active team count (same 5 labels as NBA).

Run:
  py -3.14 step3_attach_defense.py \
      --input  step2_wnba_picktypes.csv \
      --defense wnba_defense_summary.csv \
      --output step3_wnba_defense.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from utils.defense_tiers import assert_def_tier_column, format_def_tier_counts
from utils.wnba_team_keys import canonical_team_key, defense_team_key


def _col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _safe_upper(x) -> str:
    return str(x or "").strip().upper()


def _backfill_missing_opp_team(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fill empty opp_team when the board only lists one PP game_id but the same
    start_time window has props for both franchises (mixed LVA/LAS codes, etc.).
    """
    if "opp_team" not in df.columns:
        df["opp_team"] = ""
    if "team" not in df.columns:
        return df

    out = df.copy()
    out["opp_team"] = out["opp_team"].astype(str).str.strip().str.upper()
    out["team"] = out["team"].astype(str).str.strip().str.upper()
    missing = out["opp_team"].eq("") & out["team"].ne("") & (~out["team"].str.contains("/"))

    if not bool(missing.any()):
        return out

    # Same pp_game_id: two canonical franchises
    if "pp_game_id" in out.columns:
        for gid, grp in out.groupby("pp_game_id", dropna=False):
            gid_s = str(gid).strip()
            if not gid_s or gid_s.lower() == "nan":
                continue
            sub = grp[~grp["team"].str.contains("/") & grp["team"].ne("")]
            canon_to_raw: dict[str, str] = {}
            for t in sub["team"]:
                c = canonical_team_key(t)
                if c:
                    canon_to_raw.setdefault(c, str(t))
            canon_keys = list(canon_to_raw.keys())
            if len(canon_keys) != 2:
                continue
            a, b = canon_keys
            for idx, row in sub.iterrows():
                if str(row.get("opp_team", "")).strip():
                    continue
                tc = canonical_team_key(row["team"])
                if tc == a:
                    out.at[idx, "opp_team"] = canon_to_raw[b]
                elif tc == b:
                    out.at[idx, "opp_team"] = canon_to_raw[a]

    missing = out["opp_team"].eq("") & out["team"].ne("") & (~out["team"].str.contains("/"))
    if not bool(missing.any()) or "start_time" not in out.columns:
        return out

    st = pd.to_datetime(out["start_time"], errors="coerce")
    out["_st_bucket"] = st.dt.floor("h")
    for bucket, grp in out.groupby("_st_bucket", dropna=True):
        if pd.isna(bucket):
            continue
        sub = grp[~grp["team"].str.contains("/") & grp["team"].ne("")]
        canon_to_raw: dict[str, str] = {}
        for t in sub["team"]:
            c = canonical_team_key(t)
            if c:
                canon_to_raw.setdefault(c, str(t))
        canon_keys = list(canon_to_raw.keys())
        if len(canon_keys) != 2:
            continue
        a, b = canon_keys
        for idx, row in sub.iterrows():
            if str(row.get("opp_team", "")).strip():
                continue
            tc = canonical_team_key(row["team"])
            if tc == a:
                out.at[idx, "opp_team"] = canon_to_raw[b]
            elif tc == b:
                out.at[idx, "opp_team"] = canon_to_raw[a]

    out.drop(columns=["_st_bucket"], inplace=True, errors="ignore")
    return out


def _backfill_opp_from_espn_cache(df: pd.DataFrame, cache_path: Path) -> pd.DataFrame:
    """Use ESPN boxscore cache to infer opponent when PP omits home/away teams."""
    if not cache_path.is_file() or "team" not in df.columns:
        return df
    if "start_time" not in df.columns:
        return df

    out = df.copy()
    missing = out["opp_team"].astype(str).str.strip().eq("") & out["team"].astype(str).str.strip().ne("")
    if not bool(missing.any()):
        return out

    cache = pd.read_csv(cache_path, dtype=str, encoding="utf-8-sig").fillna("")
    if "TEAM" not in cache.columns or "game_date" not in cache.columns:
        return out

    cache["game_date"] = pd.to_datetime(cache["game_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    cache["TEAM"] = cache["TEAM"].astype(str).str.strip().str.upper()

    # Prefer slate abbrev seen in this file for each defense key
    slate_by_def: dict[str, str] = {}
    for raw in out["team"].astype(str).str.strip().str.upper():
        if raw:
            slate_by_def[defense_team_key(raw)] = raw

    def _opp_display(def_abbr: str) -> str:
        d = str(def_abbr or "").strip().upper()
        return slate_by_def.get(d, {"NY": "NYL", "LV": "LVA", "GS": "GSV"}.get(d, d))

    st = pd.to_datetime(out["start_time"], errors="coerce")
    for idx, row in out.loc[missing].iterrows():
        if str(row.get("opp_team", "")).strip():
            continue
        gd = st.loc[idx]
        if pd.isna(gd):
            continue
        date_s = gd.strftime("%Y-%m-%d")
        team_def = defense_team_key(row.get("team", ""))
        if not team_def:
            continue
        day = cache[cache["game_date"] == date_s]
        if day.empty:
            continue
        opp_def = ""
        for _, g in day.groupby("event_id"):
            teams = sorted(set(g["TEAM"].astype(str).str.upper().unique()) - {""})
            if team_def in teams and len(teams) == 2:
                opp_def = teams[1] if teams[0] == team_def else teams[0]
                break
        if opp_def:
            out.at[idx, "opp_team"] = _opp_display(opp_def)

    return out


def format_combo_opp_display(o1: str, o2: str) -> str:
    parts = [p.strip() for p in (o1, o2) if str(p or "").strip()]
    if len(parts) >= 2:
        if parts[0] == parts[1]:
            return parts[0]
        return f"{parts[0]} / {parts[1]}"
    return parts[0] if parts else ""


def build_player_opp_lookup(df: pd.DataFrame) -> Dict[str, str]:
    """Map player name -> opponent abbrev from populated single-player rows."""
    work = df.copy()
    work["is_combo_player"] = pd.to_numeric(
        work.get("is_combo_player", 0), errors="coerce"
    ).fillna(0).astype(int)
    singles = work[work["is_combo_player"] == 0]
    out: Dict[str, str] = {}
    for player, grp in singles.groupby("player", dropna=False):
        opps = grp["opp_team"].astype(str).str.strip().str.upper()
        opps = opps[(opps != "") & (~opps.str.lower().isin(["nan", "none"]))]
        if len(opps):
            out[str(player).strip()] = str(opps.mode().iloc[0]).strip().upper()
    return out


def _build_game_team_map(df: pd.DataFrame) -> dict[str, dict[str, str]]:
    """Map pp_game_id -> canonical_team_key -> slate team abbrev (from singles)."""
    out: dict[str, dict[str, str]] = {}
    if "pp_game_id" not in df.columns or "team" not in df.columns:
        return out
    work = df.copy()
    work["is_combo_player"] = pd.to_numeric(
        work.get("is_combo_player", 0), errors="coerce"
    ).fillna(0).astype(int)
    for gid, g in work.groupby("pp_game_id", dropna=False):
        gid_s = str(gid).strip()
        if not gid_s or gid_s.lower() == "nan":
            continue
        singles = g[
            (g["is_combo_player"] == 0)
            & (~g["team"].astype(str).str.contains("/"))
            & (g["team"].astype(str).str.strip() != "")
        ]
        canon_to_raw: dict[str, str] = {}
        for raw in singles["team"].dropna().astype(str):
            raw = raw.strip()
            if not raw:
                continue
            canon_to_raw.setdefault(canonical_team_key(raw), raw)
        if canon_to_raw:
            out[gid_s] = canon_to_raw
    return out


def _infer_opp_for_team(gid: str, team: str, game_team_map: dict[str, dict[str, str]]) -> str:
    team = str(team or "").strip().upper()
    if not team or not gid:
        return ""
    canon_to_raw = game_team_map.get(str(gid).strip(), {})
    canon_keys = list(canon_to_raw.keys())
    if len(canon_keys) != 2:
        return ""
    tc = canonical_team_key(team)
    if tc not in canon_to_raw:
        return ""
    other = [k for k in canon_keys if k != tc]
    return canon_to_raw.get(other[0], "") if other else ""


def derive_combo_opponents(row: pd.Series) -> Tuple[str, str]:
    team1 = _safe_upper(row.get("team_1", ""))
    team2 = _safe_upper(row.get("team_2", ""))
    home  = _safe_upper(row.get("pp_home_team", ""))
    away  = _safe_upper(row.get("pp_away_team", ""))

    if home and away and team1 and team2:
        t1c, t2c, hc, ac = map(canonical_team_key, (team1, team2, home, away))
        opp1 = away if t1c == hc else (home if t1c == ac else "")
        opp2 = away if t2c == hc else (home if t2c == ac else "")
        return opp1, opp2

    opp = str(row.get("opp_team", "")).strip()
    if "/" in opp:
        parts = [p.strip() for p in opp.split("/")]
        return (parts[0], parts[1]) if len(parts) >= 2 else ("", "")
    return "", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",   required=True)
    ap.add_argument("--defense", required=True)
    ap.add_argument("--output",  required=True)
    args = ap.parse_args()

    print(f"→ Loading: {args.input}")
    df = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig").fillna("")

    print(f"→ Loading defense: {args.defense}")
    d = pd.read_csv(args.defense, dtype=str, encoding="utf-8-sig").fillna("")

    key = _col(d, ["TEAM_ABBREVIATION","team_abbr","abbr","TEAM_ABBR"])
    if not key:
        raise RuntimeError(f"❌ Defense file missing TEAM_ABBREVIATION. Found: {list(d.columns)}")

    d[key] = d[key].astype(str).str.strip().str.upper()
    def_cols = [c for c in d.columns if c != key]

    if "opp_team" not in df.columns:
        df["opp_team"] = ""
    df["opp_team"] = df["opp_team"].astype(str).str.strip().str.upper()
    df = _backfill_missing_opp_team(df)
    cache_default = Path(__file__).resolve().parent / "wnba_espn_cache.csv"
    df = _backfill_opp_from_espn_cache(df, cache_default)
    df["opp_team"] = df["opp_team"].astype(str).str.strip().str.upper()

    if "is_combo_player" not in df.columns:
        df["is_combo_player"] = df.get("player","").astype(str).str.contains(r"\+").astype(int)

    singles_mask = df["is_combo_player"].astype(str).isin(["0","False","false",""])
    combos_mask  = ~singles_mask

    # Singles — map PP opp codes (LVA, NYL, LAS, …) to defense-summary keys (LV, NY, …)
    singles = df.loc[singles_mask].copy()
    singles["_opp_def_key"] = singles["opp_team"].map(defense_team_key)
    singles = singles.merge(d[[key] + def_cols], how="left", left_on="_opp_def_key", right_on=key)
    singles.drop(columns=["_opp_def_key"], inplace=True, errors="ignore")
    if key in singles.columns:
        singles.drop(columns=[key], inplace=True)

    # Combos
    combos = df.loc[combos_mask].copy()
    if len(combos):
        for c in ["team_1","team_2","pp_home_team","pp_away_team"]:
            if c not in combos.columns:
                combos[c] = ""
        game_team_map = _build_game_team_map(df)
        player_opps = build_player_opp_lookup(df)
        opps = combos.apply(derive_combo_opponents, axis=1, result_type="expand")
        opps.columns = ["opp_team_1","opp_team_2"]
        combos["opp_team_1"] = opps["opp_team_1"].astype(str).str.strip().str.upper()
        combos["opp_team_2"] = opps["opp_team_2"].astype(str).str.strip().str.upper()
        for idx, row in combos.iterrows():
            gid = str(row.get("pp_game_id", "")).strip()
            p1 = str(row.get("player_1", "")).strip()
            p2 = str(row.get("player_2", "")).strip()
            if not str(combos.at[idx, "opp_team_1"]).strip():
                combos.at[idx, "opp_team_1"] = (
                    player_opps.get(p1)
                    or _infer_opp_for_team(gid, row.get("team_1", ""), game_team_map)
                )
            if not str(combos.at[idx, "opp_team_2"]).strip():
                combos.at[idx, "opp_team_2"] = (
                    player_opps.get(p2)
                    or _infer_opp_for_team(gid, row.get("team_2", ""), game_team_map)
                )
        combos["opp_team"] = combos.apply(
            lambda r: format_combo_opp_display(r.get("opp_team_1", ""), r.get("opp_team_2", "")),
            axis=1,
        )
        combos["_opp_def_key_1"] = combos["opp_team_1"].map(defense_team_key)
        combos["_opp_def_key_2"] = combos["opp_team_2"].map(defense_team_key)

        leg1 = combos.merge(d[[key] + def_cols], how="left", left_on="_opp_def_key_1", right_on=key)
        if key in leg1.columns: leg1.drop(columns=[key], inplace=True)
        leg2 = combos.merge(d[[key] + def_cols], how="left", left_on="_opp_def_key_2", right_on=key)
        if key in leg2.columns: leg2.drop(columns=[key], inplace=True)

        leg1 = leg1.rename(columns={c: f"{c}_DEF_1" for c in def_cols})
        leg2 = leg2.rename(columns={c: f"{c}_DEF_2" for c in def_cols})

        combos.drop(columns=["_opp_def_key_1", "_opp_def_key_2"], inplace=True, errors="ignore")
        combos = pd.concat([
            combos.reset_index(drop=True),
            leg1[[c for c in leg1.columns if c.endswith("_DEF_1")]].reset_index(drop=True),
            leg2[[c for c in leg2.columns if c.endswith("_DEF_2")]].reset_index(drop=True),
        ], axis=1)

    out = pd.concat([singles, combos], axis=0, ignore_index=True)

    desired_front = ["wnba_player_id","player","pos","team","opp_team","line","prop_type","prop_norm","pick_type"]
    front  = [c for c in desired_front if c in out.columns]
    tail   = ["is_combo_player"] if "is_combo_player" in out.columns else []
    middle = [c for c in out.columns if c not in set(front+tail)]
    out    = out[front + middle + tail]

    if "def_tier" in out.columns and "DEF_TIER" not in out.columns:
        out = out.rename(columns={"def_tier": "DEF_TIER"})

    _dt_col = "DEF_TIER" if "DEF_TIER" in out.columns else ("def_tier" if "def_tier" in out.columns else None)
    if _dt_col:
        _chk = out[[_dt_col]].rename(columns={_dt_col: "def_tier"})
        _m = _chk["def_tier"].astype(str).str.strip().ne("")
        if _m.any():
            assert_def_tier_column(_chk.loc[_m], "def_tier", allow_empty=False)
        print(f"[WNBA step3] {format_def_tier_counts(_chk, 'def_tier')}")

    out.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"✅ Saved → {args.output}  rows={len(out)}")

    if "OVERALL_DEF_RANK" in out.columns:
        filled = (out["OVERALL_DEF_RANK"].astype(str).str.strip() != "").sum()
        print(f"Defense filled (OVERALL_DEF_RANK): {filled}/{len(out)}")


if __name__ == "__main__":
    main()
