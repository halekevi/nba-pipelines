#!/usr/bin/env python3
"""
NFL step3 — merge ESPN defense ranks (step4) onto the cleaned PrizePicks slate (step2).

Adds:
  - opp_pass_def_rank  (opponent pass defense rank; 1 = stingiest)
  - team_pass_def_rank (player's team pass defense rank)
  - points_allowed_pg_opp (optional context)

Run from NFL/ with NFL_PIPELINE_ACTIVE=1.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _nfl_pipeline_active import require_nfl_pipeline_active_or_exit


def _abbr(x: object) -> str:
    return str(x or "").strip().upper()


def main() -> None:
    require_nfl_pipeline_active_or_exit()

    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/outputs/step2_clean_props.csv")
    ap.add_argument("--defense", default="data/defense_rankings.csv")
    ap.add_argument("--output", default="data/outputs/step3_nfl_with_defense.csv")
    args = ap.parse_args()

    slate = Path(args.input)
    deff = Path(args.defense)
    if not slate.is_file():
        print(f"[NFL step3] Missing slate: {slate}")
        sys.exit(1)
    if not deff.is_file():
        print(f"[NFL step3] Missing defense CSV: {deff}")
        sys.exit(1)

    df = pd.read_csv(slate, encoding="utf-8-sig")
    if df.empty:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"[NFL step3] Wrote empty {out}")
        return

    dref = pd.read_csv(deff, encoding="utf-8-sig")
    if "team" not in dref.columns or "pass_def_rank" not in dref.columns:
        print("[NFL step3] defense CSV must include columns: team, pass_def_rank")
        sys.exit(1)

    dmap = dref.set_index(dref["team"].map(_abbr))["pass_def_rank"].to_dict()
    pts_map = {}
    for _pcol in ("points_allowed_pg", "points_allowed_pg_opp"):
        if _pcol in dref.columns:
            pts_map = dref.set_index(dref["team"].map(_abbr))[_pcol].to_dict()
            break

    team_col = "team" if "team" in df.columns else None
    opp_col = "opp_team" if "opp_team" in df.columns else ("opponent" if "opponent" in df.columns else None)
    if not team_col or not opp_col:
        print("[NFL step3] slate needs team + opp_team (or opponent) columns")
        sys.exit(1)

    t = df[team_col].map(_abbr)
    o = df[opp_col].map(_abbr)
    df["team_pass_def_rank"] = t.map(lambda x: dmap.get(x, pd.NA))
    df["opp_pass_def_rank"] = o.map(lambda x: dmap.get(x, pd.NA))
    if pts_map:
        df["points_allowed_pg_opp"] = o.map(lambda x: pts_map.get(x, pd.NA))

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"[NFL step3] Wrote {out_path} rows={len(df)}")


if __name__ == "__main__":
    main()
