#!/usr/bin/env python3
"""
Tennis step3 — opponent strength from ATP/WTA ranking table (ESPN).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from tennis_shared import load_or_refresh_rankings, resolve_opp_rank


def opp_to_def_tier(rank: float) -> str:
    if rank <= 10:
        return "ELITE"
    if rank <= 35:
        return "ABOVE AVG"
    if rank <= 75:
        return "AVERAGE"
    return "BELOW AVG"


def main() -> None:
    root = _SCRIPT_DIR.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="outputs/step2_tennis_picktypes.csv")
    ap.add_argument("--output", default="outputs/step3_tennis_with_context.csv")
    ap.add_argument("--rankings-cache", default="cache/tennis_rankings.json")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_absolute():
        inp = root / inp
    out = Path(args.output)
    if not out.is_absolute():
        out = root / out

    df = pd.read_csv(inp, dtype=str, encoding="utf-8-sig").fillna("")
    if df.empty:
        print("ERROR [Tennis-S3] empty input")
        sys.exit(1)

    rpath = Path(args.rankings_cache)
    if not rpath.is_absolute():
        rpath = root / rpath
    rankings = load_or_refresh_rankings(rpath)

    opp_col = "opp_team" if "opp_team" in df.columns else "opp"
    ranks = []
    for _, r in df.iterrows():
        ranks.append(resolve_opp_rank(str(r.get(opp_col, "")), rankings))
    df["OVERALL_DEF_RANK"] = ranks
    df["DEF_TIER"] = [opp_to_def_tier(x) for x in ranks]

    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"OK [Tennis-S3] -> {out}  rows={len(df)}")


if __name__ == "__main__":
    main()
