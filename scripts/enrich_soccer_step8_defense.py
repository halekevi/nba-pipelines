#!/usr/bin/env python3
"""Merge soccer defense DB fields onto an existing step8 clean xlsx (historical slates)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


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
        "league",
    ):
        if c in d.columns:
            keep.append(c)
    out = d[keep].copy()
    out["_opp_key"] = out[key].astype(str).str.strip().str.upper()
    out = out.drop_duplicates(subset=["_opp_key"], keep="first")
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
    df["_opp_key"] = df[opp_col].astype(str).str.strip().str.upper()
    merged = df.merge(def_df, on="_opp_key", how="left", suffixes=("", "_def"))

    if "DEF_TIER" in merged.columns:
        tier = merged["DEF_TIER"].astype(str).str.strip()
        merged["Def Tier"] = tier.where(tier.ne("") & tier.ne("nan"), merged.get("Def Tier", pd.NA))
    if "def_tier" in merged.columns and "Def Tier" not in merged.columns:
        merged["Def Tier"] = merged["def_tier"]

    pace_src = None
    for src in ("opp_gf_per_game", "OPP_PPG", "opp_gaa", "goals_conceded_pg"):
        if src in merged.columns:
            pace_src = src
            break
    if pace_src:
        merged["Opp Pace"] = pd.to_numeric(merged[pace_src], errors="coerce")

    if "OVERALL_DEF_RANK" in merged.columns and "Def Rank" not in merged.columns:
        merged["Def Rank"] = merged["OVERALL_DEF_RANK"]

    drop = [c for c in merged.columns if c.endswith("_def") or c == "_opp_key"]
    merged = merged.drop(columns=drop, errors="ignore")

    tier_fill = merged["Def Tier"].notna().sum() if "Def Tier" in merged.columns else 0
    pace_fill = merged["Opp Pace"].notna().sum() if "Opp Pace" in merged.columns else 0
    out.parent.mkdir(parents=True, exist_ok=True)
    merged.to_excel(out, index=False, engine="openpyxl")
    print(f"[enrich] {inp.name} -> {out.name}  rows={len(merged):,}  Def Tier={tier_fill:,}  Opp Pace={pace_fill:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
