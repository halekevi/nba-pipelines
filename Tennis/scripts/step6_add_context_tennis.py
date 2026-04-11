#!/usr/bin/env python3
"""Tennis step6 — surface, round, ranking_diff, is_grand_slam, roles for step7/8."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from tennis_shared import load_or_refresh_rankings, norm_key, resolve_opp_rank


ROUND_RE = re.compile(r"\b(R128|R64|R32|R16|QF|SF|F)\b", re.I)
GRAND_SLAMS = (
    "australian open",
    "roland garros",
    "french open",
    "wimbledon",
    "us open",
)


def infer_surface(team_or_tourn: str) -> str:
    t = str(team_or_tourn or "").lower()
    if any(x in t for x in ("roland", "monte carlo", "barcelona", "madrid", "rome", "clay")):
        return "clay"
    if any(x in t for x in ("wimbledon", "queen", "halle", "grass")):
        return "grass"
    return "hard"


def infer_round(row: pd.Series) -> str:
    blob = f"{row.get('prop_type','')} {row.get('team','')} {row.get('tournament','')}"
    m = ROUND_RE.search(blob)
    return m.group(1).upper() if m else ""


def main() -> None:
    print("[Tennis step6] Starting...")
    root = _SCRIPT_DIR.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="outputs/step5_tennis_hit_rates.csv")
    ap.add_argument("--output", default="outputs/step6_tennis_role_context.csv")
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
        print("ERROR [Tennis step6] empty input")
        sys.exit(1)

    team_src = df["team"].astype(str) if "team" in df.columns else pd.Series([""] * len(df))
    tourn = df["tournament"].astype(str) if "tournament" in df.columns else pd.Series([""] * len(df))
    surf_src = (tourn.where(tourn.str.len() > 2, team_src)).fillna("")
    df["surface"] = [infer_surface(surf_src.iat[i]) for i in range(len(df))]
    df["round"] = [infer_round(df.iloc[i]) for i in range(len(df))]

    blob_gs = (surf_src + " " + team_src).str.lower()
    df["is_grand_slam"] = blob_gs.apply(lambda s: int(any(g in s for g in GRAND_SLAMS)))

    rpath = Path(args.rankings_cache)
    if not rpath.is_absolute():
        rpath = root / rpath
    rankings = load_or_refresh_rankings(rpath)
    by_id = {str(r["espn_athlete_id"]): float(r.get("rank") or 999) for r in rankings}
    by_pk = {str(r.get("player_key") or ""): float(r.get("rank") or 999) for r in rankings if r.get("player_key")}

    pranks = []
    for _, r in df.iterrows():
        aid = str(r.get("espn_athlete_id", "")).strip()
        if aid and aid in by_id:
            pranks.append(by_id[aid])
        else:
            pk = norm_key(str(r.get("player", "")))
            pranks.append(by_pk.get(pk, 999.0))
    df["player_atp_rank"] = pranks

    opp_col = "opp_team" if "opp_team" in df.columns else "opp"
    oranks = [resolve_opp_rank(str(df.iloc[i].get(opp_col, "")), rankings) for i in range(len(df))]
    df["opponent_rank"] = oranks
    df["ranking_diff"] = pd.to_numeric(df["player_atp_rank"], errors="coerce") - pd.to_numeric(
        df["opponent_rank"], errors="coerce"
    )

    if "best_surface" not in df.columns or df["best_surface"].astype(str).str.len().max() < 2:
        df["best_surface"] = ""

    df["position_group"] = df["tour"].astype(str).str.upper()
    df["minutes_tier"] = 2
    df["shot_role"] = np.where(df["prop_norm"].astype(str).str.contains("ace"), "SERVE_HEAVY", "NEUTRAL")
    df["usage_role"] = np.where(pd.to_numeric(df["player_atp_rank"], errors="coerce") <= 32, "ELITE", "FIELD")

    o5 = pd.to_numeric(df.get("line_hits_over_5", np.nan), errors="coerce")
    u5 = pd.to_numeric(df.get("line_hits_under_5", np.nan), errors="coerce")
    df["last5_over"] = o5
    df["last5_under"] = u5

    df["game_script_mult"] = 1.0
    df["game_script_note"] = ""
    df["avg_minutes"] = np.nan

    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"OK [Tennis step6] -> {out}  rows={len(df)}")


if __name__ == "__main__":
    main()
