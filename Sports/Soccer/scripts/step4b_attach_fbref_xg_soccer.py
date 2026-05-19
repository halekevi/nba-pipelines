#!/usr/bin/env python3
"""
step4b_attach_fbref_xg_soccer.py — FBref expected-goals context (manual HTML cache).

Attaches per-90 xG/xAG and finishing-luck columns after step4_attach_player_stats_soccer.py.
Stats are per-90 from FBref "Expected" block (npxG/90 preferred).

Run:
  py -3.14 step4b_attach_fbref_xg_soccer.py --refresh --season 2025-2026
  py -3.14 step4b_attach_fbref_xg_soccer.py --input step4_soccer_with_stats.csv --season 2025-2026
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = Path(__file__).resolve().parent
for p in (_REPO_ROOT, _SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from fbref_xg_api import fbref_season, load_cache, lookup_player, refresh_cache
from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs

OUT_COLS = (
    "player_xg_per90",
    "player_xag_per90",
    "player_goals_minus_xg",
    "player_shots_per90",
    "xg_tier",
    "xg_data_source",
)


def _player_name(row: pd.Series) -> str:
    for c in ("player", "player_1"):
        v = str(row.get(c, "")).strip()
        if v and v.lower() != "nan":
            return v
    return ""


def _team_name(row: pd.Series) -> str:
    for c in ("team", "team_1", "pp_team"):
        v = str(row.get(c, "")).strip()
        if v and v.lower() != "nan":
            return v
    return ""


def _fbref_id_from_row(row: pd.Series) -> str:
    eid = str(row.get("espn_player_id", "") or "").strip()
    if eid.startswith("fbref_"):
        return eid.replace("fbref_", "", 1)
    return ""


def _season_from_slate(df: pd.DataFrame, season_arg: str) -> str:
    if season_arg:
        return season_arg
    for c in ("game_date", "start_time"):
        if c in df.columns:
            parsed = pd.to_datetime(df[c], errors="coerce")
            if parsed.notna().any():
                return fbref_season(parsed.dropna().iloc[0].date())
    return fbref_season(date.today())


def attach_xg(df: pd.DataFrame, season: str, cache: dict) -> pd.DataFrame:
    out = df.copy()
    for c in OUT_COLS:
        if c == "xg_tier":
            out[c] = "cache_miss"
        elif c == "xg_data_source":
            out[c] = "cache_miss"
        else:
            out[c] = np.nan

    for idx, row in out.iterrows():
        name = _player_name(row)
        if not name:
            continue
        hit = lookup_player(
            cache,
            season,
            name=name,
            team=_team_name(row),
            fbref_id=_fbref_id_from_row(row),
            league_hint=str(row.get("league", "") or ""),
        )
        if not hit:
            continue
        has_xg = hit.get("player_xg_per90") is not None
        source = str(hit.get("xg_data_source") or ("fbref" if has_xg else "cache_miss"))
        for c in ("player_xg_per90", "player_xag_per90", "player_goals_minus_xg", "player_shots_per90"):
            if hit.get(c) is not None:
                out.at[idx, c] = hit[c]
        if has_xg:
            out.at[idx, "xg_tier"] = hit.get("xg_tier") or "mid"
            out.at[idx, "xg_data_source"] = source
        else:
            out.at[idx, "xg_tier"] = "cache_miss"
            out.at[idx, "xg_data_source"] = "cache_miss"

    return out


def _print_fill(df: pd.DataFrame) -> None:
    print(f"Soccer xG context attached: {len(df)} rows")
    for c in OUT_COLS:
        if c not in df.columns:
            print(f"  {c}: MISSING")
            continue
        if c in ("xg_tier", "xg_data_source"):
            vc = df[c].fillna("cache_miss").astype(str).value_counts().head(5)
            print(f"  {c}: {vc.to_dict()}")
        else:
            fill = float(df[c].notna().mean())
            print(f"  {c}: {int(df[c].notna().sum())}/{len(df)} ({fill:.1%})")
    if float(df.get("player_xg_per90", pd.Series(dtype=float)).notna().mean()) < 0.05:
        print("  [HINT] Low xG fill — save EPL summary HTML to data/cache/fbref_html/epl_summary.html and --refresh")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="step4_soccer_with_stats.csv")
    ap.add_argument("--output", default="")
    ap.add_argument("--season", default="")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    out_path = Path(args.output or args.input)
    if args.refresh and not out_path.exists():
        season = args.season or fbref_season(date.today())
        refresh_cache(season)
        print(f"Cache refreshed for season {season} -> Sports/Soccer/data/fbref_xg_cache.json")
        if not Path(args.input).exists():
            return 0

    df = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig").fillna("")
    season = _season_from_slate(df, args.season)

    if args.refresh:
        cache = refresh_cache(season)
    else:
        cache = load_cache()

    out = attach_xg(df, season, cache)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False, encoding="utf-8-sig")
    _print_fill(out)

    copy_pipeline_output_to_dated_dirs(
        output_path=out_path,
        df=out,
        sport_dir_name="Soccer",
        repo_root=_REPO_ROOT,
    )

    print(f"Saved -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
