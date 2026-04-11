#!/usr/bin/env python3
"""
Tennis step3 — no team defense; stub columns for pipeline parity.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent


def _infer_opponent(row: pd.Series) -> str:
    o = str(row.get("opp_team", "") or "").strip()
    if o and o.upper() not in ("UNKNOWN_OPP", "UNK", "NAN"):
        return o
    desc = str(row.get("description", "") or row.get("prop_type", "") or "")
    m = re.search(r"vs\.?\s+([A-Za-z0-9 .'-]{2,64})", desc, re.I)
    if m:
        return m.group(1).strip()
    home = str(row.get("pp_home_team", "") or "").strip()
    away = str(row.get("pp_away_team", "") or "").strip()
    pl = str(row.get("player", "") or row.get("player_name", "") or "").strip()
    for cand in (home, away):
        if cand and pl and cand.upper() not in pl.upper():
            return cand
    return o


def main() -> None:
    print("[Tennis step3] Starting...")
    print("[Tennis step3] No defense rankings for tennis — stub")
    root = _SCRIPT_DIR.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="outputs/step2_tennis_picktypes.csv")
    ap.add_argument("--output", default="outputs/step3_tennis_with_defense.csv")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_absolute():
        inp = root / inp
    out = Path(args.output)
    if not out.is_absolute():
        out = root / out

    df = pd.read_csv(inp, dtype=str, encoding="utf-8-sig").fillna("")
    if df.empty:
        print("ERROR [Tennis step3] empty input")
        sys.exit(1)

    df["opp_def_tier"] = "N/A"
    df["DEF_TIER"] = "N/A"
    df["OVERALL_DEF_RANK"] = ""
    if "opp_team" not in df.columns:
        df["opp_team"] = ""
    df["opp_team"] = [_infer_opponent(df.iloc[i]) for i in range(len(df))]

    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"OK [Tennis step3] -> {out}  rows={len(df)}")


if __name__ == "__main__":
    main()
