#!/usr/bin/env python3
"""
step3c_attach_torvik_context.py — Bart Torvik team efficiency (manual CSV cache).

Run:
  py -3.14 Sports/CBB/scripts/step3c_attach_torvik_context.py --refresh --season 2025-26
  py -3.14 Sports/CBB/scripts/step3c_attach_torvik_context.py --input step3b.csv --output step3b.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPTS = Path(__file__).resolve().parent
_REPO_ROOT = Path(__file__).resolve().parents[3]
for p in (_REPO_ROOT, _SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from torvik_ratings_api import load_cache, lookup_team, refresh_cache
from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs

OUT_COLS = (
    "team_adj_o",
    "team_adj_d",
    "team_adj_em",
    "team_tempo",
    "opp_adj_o",
    "opp_adj_d",
    "opp_adj_em",
    "adj_em_diff",
    "pace_context",
    "torvik_data_source",
)


def _team_abbr(row: pd.Series) -> str:
    for c in ("team_abbr", "team", "pp_team"):
        v = str(row.get(c, "")).strip()
        if v and v.lower() != "nan":
            return v
    return ""


def _opp_abbr(row: pd.Series) -> str:
    for c in ("opp_team_abbr", "pp_opp_team"):
        v = str(row.get(c, "")).strip()
        if v and v.lower() != "nan":
            return v
    return ""


def attach_torvik(df: pd.DataFrame, season: str, cache: dict) -> pd.DataFrame:
    out = df.copy()
    for c in OUT_COLS:
        if c in ("pace_context", "torvik_data_source"):
            out[c] = "cache_miss"
        else:
            out[c] = np.nan

    for idx, row in out.iterrows():
        team_hit = lookup_team(_team_abbr(row), season, cache)
        opp_hit = lookup_team(_opp_abbr(row), season, cache)

        if team_hit:
            out.at[idx, "team_adj_o"] = team_hit.get("adj_o")
            out.at[idx, "team_adj_d"] = team_hit.get("adj_d")
            out.at[idx, "team_adj_em"] = team_hit.get("adj_em")
            out.at[idx, "team_tempo"] = team_hit.get("tempo")
            out.at[idx, "pace_context"] = team_hit.get("pace_context") or "medium"
            out.at[idx, "torvik_data_source"] = "torvik"

        if opp_hit:
            out.at[idx, "opp_adj_o"] = opp_hit.get("adj_o")
            out.at[idx, "opp_adj_d"] = opp_hit.get("adj_d")
            out.at[idx, "opp_adj_em"] = opp_hit.get("adj_em")

        tem = out.at[idx, "team_adj_em"]
        oem = out.at[idx, "opp_adj_em"]
        if pd.notna(tem) and pd.notna(oem):
            out.at[idx, "adj_em_diff"] = float(tem) - float(oem)

    return out


def _print_fill(df: pd.DataFrame) -> None:
    print(f"CBB Torvik context attached: {len(df)} rows")
    for c in OUT_COLS:
        if c not in df.columns:
            print(f"  {c}: MISSING")
            continue
        if c == "torvik_data_source":
            vc = df[c].fillna("cache_miss").astype(str).value_counts()
            print(f"  {c}: {vc.to_dict()}")
        elif c == "pace_context":
            vc = df[c].fillna("cache_miss").astype(str).value_counts().head(5)
            print(f"  {c}: {vc.to_dict()}")
        else:
            fill = float(df[c].notna().mean())
            print(f"  {c}: {int(df[c].notna().sum())}/{len(df)} ({fill:.1%})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="step3b_with_def_rankings_cbb.csv")
    ap.add_argument("--output", default="")
    ap.add_argument("--season", default="2025-26")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    out_path = Path(args.output or args.input)
    if args.refresh and not out_path.exists():
        refresh_cache(args.season)
        print(f"Cache refreshed -> Sports/CBB/data/torvik_team_cache.json")
        if not Path(args.input).exists():
            return 0

    if not Path(args.input).is_file():
        print(f"Missing input: {args.input}")
        return 1

    if args.refresh:
        cache = refresh_cache(args.season)
    else:
        cache = load_cache()
        if not (cache.get("seasons") or {}).get(args.season):
            cache = refresh_cache(args.season)

    df = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig").fillna("")
    out = attach_torvik(df, args.season, cache)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False, encoding="utf-8-sig")
    _print_fill(out)

    copy_pipeline_output_to_dated_dirs(
        output_path=out_path,
        df=out,
        sport_dir_name="CBB",
        repo_root=_REPO_ROOT,
    )
    print(f"Saved -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
