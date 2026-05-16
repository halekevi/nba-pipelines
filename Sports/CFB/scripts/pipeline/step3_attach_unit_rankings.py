#!/usr/bin/env python3
"""
Attach conference-scoped pass/rush offense & defense ranks + tiers to the CFB slate.

Reads: data/reference/cfb_team_unit_rankings.csv (from build_cfb_unit_rankings.py)

Adds per row:
  Team offense:  pass_off_rank/tier, rush_off_rank/tier
  Opponent defense (matchup): opp_pass_def_rank/tier, opp_rush_def_rank/tier
  Opponent offense: opp_pass_off_rank/tier, opp_rush_off_rank/tier
  Team defense: pass_def_rank/tier, rush_def_rank/tier
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[4]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from utils.cfb_playoff_metadata import CFB_TEAM_ALIASES, norm_cfb_team_abbr
from utils.defense_tiers import normalize_def_tier_label

RANK_FIELDS = (
    ("pass_off_rank", "pass_off_tier"),
    ("rush_off_rank", "rush_off_tier"),
    ("pass_def_rank", "pass_def_tier"),
    ("rush_def_rank", "rush_def_tier"),
)


def _norm_key(x: object) -> str:
    return norm_cfb_team_abbr(x)


def _load_lookup(path: Path) -> dict[str, dict]:
    df = pd.read_csv(path, dtype=str).fillna("")
    by_abbr: dict[str, dict] = {}
    for _, r in df.iterrows():
        abbr = _norm_key(r.get("team_abbr", ""))
        if not abbr:
            continue
        payload = {c: r[c] for c in df.columns if c != "team_abbr"}
        by_abbr[abbr] = payload
        # PrizePicks alias → same row
        for alias, canon in CFB_TEAM_ALIASES.items():
            if _norm_key(canon) == abbr:
                by_abbr[_norm_key(alias)] = payload
    return by_abbr


def _attach_side(df: pd.DataFrame, abbr_col: str, prefix: str, lookup: dict[str, dict]) -> None:
    for rank_col, tier_col in RANK_FIELDS:
        out_rank = f"{prefix}_{rank_col}"
        out_tier = f"{prefix}_{tier_col}"
        ranks, tiers = [], []
        for _, row in df.iterrows():
            abbr = _norm_key(row.get(abbr_col, ""))
            payload = lookup.get(abbr)
            if not payload:
                ranks.append("")
                tiers.append("")
                continue
            ranks.append(payload.get(rank_col, ""))
            t = payload.get(tier_col, "")
            tiers.append(normalize_def_tier_label(t) or str(t).strip())
        df[out_rank] = ranks
        df[out_tier] = tiers


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument(
        "--rankings",
        default="data/reference/cfb_team_unit_rankings.csv",
        help="Unit rankings CSV from build_cfb_unit_rankings.py",
    )
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    cfb_root = Path(__file__).resolve().parents[2]
    rank_path = Path(args.rankings)
    if not rank_path.is_absolute():
        rank_path = cfb_root / rank_path
    if not rank_path.exists():
        raise SystemExit(
            f"Missing rankings file: {rank_path}\n"
            "Run: py -3.14 scripts/build_cfb_unit_rankings.py --season <year>"
        )

    df = pd.read_csv(args.input, dtype=str).fillna("")
    lookup = _load_lookup(rank_path)
    print(f"→ Rankings: {rank_path} | teams={len(lookup)}")

    team_col = next((c for c in ("team_abbr", "pp_team", "team") if c in df.columns), None)
    opp_col = next((c for c in ("opp_team_abbr", "pp_opp_team", "opp") if c in df.columns), None)
    if not team_col:
        raise SystemExit(f"No team column in slate. Columns: {list(df.columns)}")

    _attach_side(df, team_col, "team", lookup)
    if opp_col:
        _attach_side(df, opp_col, "opp", lookup)
        # Matchup columns: offense vs opponent's defense unit
        if "team_pass_off_rank" in df.columns and "opp_pass_def_rank" in df.columns:
            df["matchup_pass_off_vs_def_rank"] = df["team_pass_off_rank"]
            df["matchup_pass_off_vs_def_tier"] = df["opp_pass_def_tier"]
            df["matchup_rush_off_vs_def_rank"] = df["team_rush_off_rank"]
            df["matchup_rush_off_vs_def_tier"] = df["opp_rush_def_tier"]

    misses = 0
    if team_col:
        for _, row in df.iterrows():
            if _norm_key(row.get(team_col, "")) not in lookup:
                misses += 1
    print(f"→ Rows={len(df)} | team rank misses={misses}")

    df.to_csv(args.output, index=False)
    print(f"✅ Saved → {args.output}")


if __name__ == "__main__":
    main()
