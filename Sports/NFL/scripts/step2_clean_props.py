#!/usr/bin/env python3
"""
NFL step2 — normalize PrizePicks prop types and infer position group.

Reads step1 CSV, writes NFL/data/outputs/step2_clean_props.csv

Run from repo NFL folder (or anywhere with paths adjusted):
  set NFL_PIPELINE_ACTIVE=1
  py -3.14 scripts/step2_clean_props.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _nfl_pipeline_active import require_nfl_pipeline_active_or_exit

PASSING_PROPS = frozenset(
    {
        "passing_yards",
        "passing_tds",
        "pass_attempts",
        "completions",
        "interceptions",
    }
)
RUSHING_PROPS = frozenset({"rushing_yards", "rushing_attempts", "rushing_tds", "carries"})
RECEIVING_PROPS = frozenset({"receiving_yards", "receptions", "targets", "receiving_tds"})
SCORING_PROPS = frozenset({"anytime_td", "first_touchdown", "first_td"})
DEFENSE_PROPS = frozenset({"sacks", "tackles", "solo_tackles", "assisted_tackles", "defensive_interceptions"})


def _norm_prop(raw: str) -> str:
    s = str(raw or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def _pos_bucket(pos_raw: str) -> str:
    p = str(pos_raw or "").strip().upper()
    if not p:
        return ""
    if p in ("QB",):
        return "QB"
    if p in ("RB", "FB"):
        return "RB"
    if p in ("TE",):
        return "TE"
    if p in ("WR",):
        return "WR"
    if p in ("DST", "DEF", "D", "D/ST"):
        return "DEF"
    if "LB" in p or "DE" in p or "DT" in p or "CB" in p or "S" in p or "DB" in p:
        return "DEF"
    return ""


def _position_group_from_row(norm_prop: str, pos_raw: str) -> str:
    bucket = _pos_bucket(pos_raw)
    if norm_prop == "interceptions" and bucket == "DEF":
        return "DEF"
    if norm_prop in PASSING_PROPS:
        return bucket if bucket == "QB" else ("QB" if not bucket else bucket)
    if norm_prop in RUSHING_PROPS:
        if bucket == "QB":
            return "QB"
        return "RB" if not bucket else bucket
    if norm_prop in RECEIVING_PROPS:
        if bucket in ("TE", "RB", "QB"):
            return bucket
        return "WR" if not bucket else bucket
    if norm_prop in SCORING_PROPS:
        return bucket or "UNK"
    if norm_prop in DEFENSE_PROPS:
        return "DEF"
    return bucket or "UNK"


def main() -> None:
    require_nfl_pipeline_active_or_exit()

    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/outputs/step1_pp_props_today.csv")
    ap.add_argument("--output", default="data/outputs/step2_clean_props.csv")
    args = ap.parse_args()

    in_path = Path(args.input)
    if not in_path.is_file():
        print(f"[NFL step2] Missing input: {in_path}")
        sys.exit(1)

    df = pd.read_csv(in_path, encoding="utf-8-sig")
    if df.empty:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"[NFL step2] Wrote empty {out}")
        return

    prop_col = "prop_type" if "prop_type" in df.columns else ""
    if not prop_col:
        print("[NFL step2] prop_type column missing")
        sys.exit(1)

    df = df.copy()
    df["prop_type_normalized"] = df[prop_col].map(_norm_prop)
    pos_col = "pos" if "pos" in df.columns else None
    pos_series = df[pos_col] if pos_col else pd.Series([""] * len(df))
    df["position_group"] = [
        _position_group_from_row(p, str(pos_series.iloc[i]))
        for i, p in enumerate(df["prop_type_normalized"])
    ]

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"[NFL step2] Wrote {out_path} rows={len(df)}")


if __name__ == "__main__":
    main()
