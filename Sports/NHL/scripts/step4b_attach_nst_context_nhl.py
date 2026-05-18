#!/usr/bin/env python3
"""
step4b_attach_nst_context_nhl.py — PP TOI (NHL API) + line combos (NST cache).

Attaches after step4_attach_player_stats_nhl.py:
  pp_toi_per_game, pp_toi_pct, pp_unit_tier (NHL Stats API)
  line_combo, line_combo_toi_pct, line_combo_cf_pct, on_pp1_line (NST cache when available)

Refresh NST caches (requires NST_ACCESS_KEY):
  py -3.14 refresh_nst_cache.py --season 20252026

Run:
  py -3.14 step4b_attach_nst_context_nhl.py \\
    --input outputs/step4_nhl_with_stats.csv \\
    --output outputs/step4_nhl_with_stats.csv
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import unicodedata
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs
from nhl_pp_api import (
    current_season_id,
    load_pp_cache,
    pp_unit_tier,
    refresh_pp_cache,
    season_id_from_year,
)
from nst_client import LINE_CACHE, load_cache, nst_key, refresh_line_cache

log = logging.getLogger("nhl.step4b")


def _norm_name(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _player_name(row: pd.Series) -> str:
    for c in ("player_name", "player", "Player"):
        v = str(row.get(c, "")).strip()
        if v and v.lower() != "nan":
            return v
    return ""


def _norm_team(v: object) -> str:
    return str(v or "").strip().upper()


def _to_float(v: object) -> Optional[float]:
    try:
        x = float(v)
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _find_col(df: pd.DataFrame, *needles: str) -> Optional[str]:
    for c in df.columns:
        cl = str(c).lower().replace("%", "pct").replace(" ", "")
        for n in needles:
            if n in cl:
                return c
    return None


def build_pp_lookup(pp_df: pd.DataFrame) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    if pp_df.empty:
        return out
    for _, r in pp_df.iterrows():
        pid = str(r.get("playerId", "")).strip()
        if not pid or pid == "nan":
            continue
        ppg = _to_float(r.get("ppTimeOnIcePerGame"))
        out[pid] = {
            "pp_toi_per_game": ppg,
            "pp_toi_pct": _to_float(r.get("ppTimeOnIcePctPerGame")),
            "pp_toi_total_sec": _to_float(r.get("ppTimeOnIce")),
            "pp_goals": _to_float(r.get("ppGoals")),
            "pp_points": _to_float(r.get("ppPoints")),
            "pp_unit_tier": pp_unit_tier(ppg) if ppg is not None else "",
            "team": str(r.get("teamAbbrevs", "")).strip().upper(),
        }
    return out


def build_line_player_index(line_df: pd.DataFrame, team: str) -> Dict[str, dict]:
    """player norm name -> best line row (5v5 preferred, highest TOI%)."""
    if line_df.empty:
        return {}
    sub = line_df.copy()
    if "team_filter" in sub.columns:
        tf = sub["team_filter"].astype(str).str.upper()
        sub = sub[(tf == team.upper()) | (tf == "ALL")]
    if "sit" in sub.columns:
        sub = sub.sort_values(
            by="sit",
            key=lambda s: s.map({"5v5": 0, "pp": 1}).fillna(2),
        )

    line_col = _find_col(sub, "line") or "Line"
    toi_col = _find_col(sub, "toi", "toi%")
    cf_col = _find_col(sub, "cf", "cf%")
    xgf_col = _find_col(sub, "xgf", "xgf%")

    index: Dict[str, dict] = {}
    for _, r in sub.iterrows():
        line_txt = str(r.get(line_col, "")).strip()
        if not line_txt or line_txt.lower() == "nan":
            continue
        parts = re.split(r"\s*[-–]\s*", line_txt)
        toi_v = _to_float(r.get(toi_col)) if toi_col else None
        for part in parts:
            nm = _norm_name(part)
            if len(nm) < 3:
                continue
            prev = index.get(nm)
            if prev is None or (toi_v or 0) > (prev.get("_toi_sort") or 0):
                index[nm] = {
                    "line_combo": line_txt,
                    "line_combo_toi_pct": toi_v,
                    "line_combo_cf_pct": _to_float(r.get(cf_col)) if cf_col else None,
                    "line_combo_xgf_pct": _to_float(r.get(xgf_col)) if xgf_col else None,
                    "line_sit": str(r.get("sit", "")),
                    "_toi_sort": toi_v or 0,
                }
    return index


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="outputs/step4_nhl_with_stats.csv")
    ap.add_argument("--output", default="outputs/step4_nhl_with_stats.csv")
    ap.add_argument("--season", default="", help="Season start year or seasonId (default: current)")
    ap.add_argument("--refresh-pp", action="store_true", help="Refresh NHL API PP cache")
    ap.add_argument("--refresh-nst", action="store_true", help="Refresh NST line cache (needs key)")
    args = ap.parse_args()

    season_raw = str(args.season).strip()
    if season_raw.isdigit() and len(season_raw) == 8:
        season_id = int(season_raw)
    elif season_raw.isdigit() and len(season_raw) == 4:
        season_id = season_id_from_year(int(season_raw))
    else:
        season_id = current_season_id()

    if args.refresh_pp:
        print(f"→ Refreshing NHL PP cache (season_id={season_id})...")
        refresh_pp_cache(season_id)

    if args.refresh_nst:
        if not nst_key():
            print("⚠️  NST_ACCESS_KEY not set — skip NST refresh")
        else:
            teams = ["all"]
            print(f"→ Refreshing NST line cache (season_id={season_id})...")
            refresh_line_cache(season_id, teams=teams)

    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")
    pp_df = load_pp_cache()
    if not pp_df.empty and "season_id" in pp_df.columns:
        pp_df = pp_df[pp_df["season_id"].astype(int) == season_id]
    elif args.refresh_pp or pp_df.empty:
        pp_df = refresh_pp_cache(season_id)

    line_df = load_cache(LINE_CACHE)
    if not line_df.empty and "season_id" in line_df.columns:
        line_df = line_df[line_df["season_id"].astype(int) == season_id]

    pp_lookup = build_pp_lookup(pp_df)

    new_cols = [
        "pp_toi_per_game", "pp_toi_pct", "pp_toi_total_sec",
        "pp_goals_season", "pp_points_season", "pp_unit_tier",
        "line_combo", "line_combo_toi_pct", "line_combo_cf_pct", "line_combo_xgf_pct",
        "line_sit", "on_pp1_line", "nst_data_source",
    ]
    for c in new_cols:
        if c not in df.columns:
            df[c] = np.nan
    for c in ("pp_unit_tier", "line_combo", "line_sit", "nst_data_source", "on_pp1_line"):
        df[c] = df[c].astype(object)

    team_line_cache: Dict[str, Dict[str, dict]] = {}
    pp_joined = 0
    line_joined = 0

    for idx, row in df.iterrows():
        role = str(row.get("player_role", "SKATER")).upper()
        if role == "GOALIE":
            df.at[idx, "nst_data_source"] = "goalie_skip"
            continue

        pid = str(row.get("nhl_player_id", "")).strip().split("|")[0]
        team = _norm_team(row.get("team", ""))
        pname = _player_name(row)
        pnorm = _norm_name(pname)

        if pid in pp_lookup:
            pp = pp_lookup[pid]
            df.at[idx, "pp_toi_per_game"] = pp.get("pp_toi_per_game")
            df.at[idx, "pp_toi_pct"] = pp.get("pp_toi_pct")
            df.at[idx, "pp_toi_total_sec"] = pp.get("pp_toi_total_sec")
            df.at[idx, "pp_goals_season"] = pp.get("pp_goals")
            df.at[idx, "pp_points_season"] = pp.get("pp_points")
            df.at[idx, "pp_unit_tier"] = pp.get("pp_unit_tier")
            pp_joined += 1

        if team and team not in team_line_cache:
            team_line_cache[team] = build_line_player_index(line_df, team)
        lidx = team_line_cache.get(team, {})
        if pnorm and pnorm in lidx:
            hit = lidx[pnorm]
            df.at[idx, "line_combo"] = hit.get("line_combo")
            df.at[idx, "line_combo_toi_pct"] = hit.get("line_combo_toi_pct")
            df.at[idx, "line_combo_cf_pct"] = hit.get("line_combo_cf_pct")
            df.at[idx, "line_combo_xgf_pct"] = hit.get("line_combo_xgf_pct")
            df.at[idx, "line_sit"] = hit.get("line_sit")
            line_joined += 1
            df.at[idx, "nst_data_source"] = "nst_line_cache"
        elif pid in pp_lookup:
            df.at[idx, "nst_data_source"] = "nhl_api_pp"
        else:
            df.at[idx, "nst_data_source"] = ""

        tier = str(df.at[idx, "pp_unit_tier"] or "")
        line_s = str(df.at[idx, "line_sit"] or "")
        df.at[idx, "on_pp1_line"] = tier == "PP1" or (line_s == "pp" and _to_float(df.at[idx, "line_combo_toi_pct"]) or 0 >= 40)

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    copy_pipeline_output_to_dated_dirs(
        output_path=args.output,
        df=df,
        sport_dir_name="NHL",
        repo_root=_REPO_ROOT,
    )

    n = len(df)
    print(f"PP TOI attached: {pp_joined}/{n} rows")
    print(f"Line combo attached: {line_joined}/{n} rows")
    if line_joined == 0 and not nst_key():
        print("  [HINT] Set NST_ACCESS_KEY and run: py refresh_nst_cache.py --refresh-nst")
    print(f"✅ Saved → {args.output}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ NHL step4b failed. {type(e).__name__}: {e}")
        sys.exit(1)
