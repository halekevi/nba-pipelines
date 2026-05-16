#!/usr/bin/env python3
"""
Build FBS team unit rankings (regular season): pass/rush offense and pass/rush defense.

Ranks and quintile tiers (Elite → Weak) are computed **within each conference**
using ESPN regular-season byteam stats.

Output: Sports/CFB/data/reference/cfb_team_unit_rankings.csv

  py -3.14 scripts/build_cfb_unit_rankings.py --season 2025
  py -3.14 scripts/build_cfb_unit_rankings.py --season 2025 --out data/reference/cfb_team_unit_rankings.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import requests

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from utils.defense_tiers import def_tier_from_overall_rank

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

BYTEAM_URL = (
    "https://site.web.api.espn.com/apis/common/v3/sports/football/college-football"
    "/statistics/byteam?season={season}&seasontype={seasontype}"
)
STANDINGS_URL = (
    "https://site.api.espn.com/apis/v2/sports/football/college-football/standings"
    "?season={season}&type=0"
)


def _get_json(url: str, timeout: int = 60) -> dict[str, Any]:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _standings_conference_map(season: int) -> dict[str, str]:
    """team_abbr -> conference abbrev (e.g. SEC, ACC)."""
    j = _get_json(STANDINGS_URL.format(season=season))
    out: dict[str, str] = {}
    for child in j.get("children") or []:
        conf = str(child.get("abbreviation") or child.get("name") or "").strip()
        if not conf:
            continue
        entries = (child.get("standings") or {}).get("entries") or []
        for entry in entries:
            team = entry.get("team") or {}
            abbr = str(team.get("abbreviation") or "").strip().upper()
            if abbr:
                out[abbr] = conf
    return out


def _label_index(labels: list[str], name: str) -> int | None:
    try:
        return labels.index(name)
    except ValueError:
        return None


def _val_at(vals: list[Any], idx: int | None) -> float:
    if idx is None or idx >= len(vals):
        return float("nan")
    v = vals[idx]
    if v is None:
        return float("nan")
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def fetch_byteam_units(season: int, seasontype: int = 2) -> pd.DataFrame:
    """
    seasontype 2 = regular season on ESPN college football.
    Returns one row per team with yds/g offense and defense (pass + rush).
    """
    j = _get_json(BYTEAM_URL.format(season=season, seasontype=seasontype))
    root_labels: dict[str, list[str]] = {}
    for cat in j.get("categories") or []:
        name = str(cat.get("name") or "")
        if name in ("passing", "rushing") and cat.get("labels"):
            root_labels[name] = [str(x) for x in cat["labels"]]

    pass_labs = root_labels.get("passing", [])
    rush_labs = root_labels.get("rushing", [])
    pass_yds_g_i = _label_index(pass_labs, "YDS/G")
    rush_yds_g_i = _label_index(rush_labs, "YDS/G")

    rows: list[dict[str, Any]] = []
    for t in j.get("teams") or []:
        team = t.get("team") or {}
        abbr = str(team.get("abbreviation") or "").strip().upper()
        if not abbr:
            continue
        pass_off = pass_def = rush_off = rush_def = float("nan")
        for cat in t.get("categories") or []:
            disp = str(cat.get("displayName") or "")
            vals = cat.get("values") or []
            if disp == "Own Passing":
                pass_off = _val_at(vals, pass_yds_g_i)
            elif disp == "Opponent Passing":
                pass_def = _val_at(vals, pass_yds_g_i)
            elif disp == "Own Rushing":
                rush_off = _val_at(vals, rush_yds_g_i)
            elif disp == "Opponent Rushing":
                rush_def = _val_at(vals, rush_yds_g_i)
        rows.append(
            {
                "team_abbr": abbr,
                "pass_off_yds_pg": pass_off,
                "rush_off_yds_pg": rush_off,
                "pass_def_yds_pg": pass_def,
                "rush_def_yds_pg": rush_def,
            }
        )
    return pd.DataFrame(rows)


def _rank_and_tier(
    df: pd.DataFrame,
    value_col: str,
    rank_col: str,
    tier_col: str,
    *,
    ascending: bool,
) -> None:
    """Rank within conference; rank 1 = best unit in that conference."""
    df[rank_col] = pd.NA
    df[tier_col] = ""
    for conf, grp in df.groupby("conference", dropna=False):
        if not str(conf).strip():
            continue
        sub = grp.copy()
        vals = pd.to_numeric(sub[value_col], errors="coerce")
        n = int(vals.notna().sum())
        if n < 2:
            continue
        ranks = vals.rank(method="min", ascending=ascending)
        df.loc[sub.index, rank_col] = ranks
        for idx, r in ranks.items():
            if pd.notna(r):
                df.at[idx, tier_col] = def_tier_from_overall_rank(int(r), n)


def build_rankings_table(season: int, seasontype: int = 2) -> pd.DataFrame:
    conf_map = _standings_conference_map(season)
    stats = fetch_byteam_units(season, seasontype=seasontype)
    if stats.empty:
        return stats

    stats["conference"] = stats["team_abbr"].map(conf_map)
    stats = stats[stats["conference"].notna() & (stats["conference"].astype(str).str.strip() != "")].copy()
    stats = stats.reset_index(drop=True)

    # Offense: higher yds/g = better (rank 1 = highest)
    _rank_and_tier(
        stats, "pass_off_yds_pg", "pass_off_rank", "pass_off_tier", ascending=False
    )
    _rank_and_tier(
        stats, "rush_off_yds_pg", "rush_off_rank", "rush_off_tier", ascending=False
    )
    # Defense: lower yds allowed/g = better (rank 1 = lowest)
    _rank_and_tier(
        stats, "pass_def_yds_pg", "pass_def_rank", "pass_def_tier", ascending=True
    )
    _rank_and_tier(
        stats, "rush_def_yds_pg", "rush_def_rank", "rush_def_tier", ascending=True
    )

    col_order = [
        "team_abbr",
        "conference",
        "pass_off_yds_pg",
        "pass_off_rank",
        "pass_off_tier",
        "rush_off_yds_pg",
        "rush_off_rank",
        "rush_off_tier",
        "pass_def_yds_pg",
        "pass_def_rank",
        "pass_def_tier",
        "rush_def_yds_pg",
        "rush_def_rank",
        "rush_def_tier",
    ]
    return stats[col_order].sort_values(["conference", "team_abbr"]).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2025, help="ESPN season year (e.g. 2025)")
    ap.add_argument("--seasontype", type=int, default=2, help="2=regular season")
    ap.add_argument(
        "--out",
        default="",
        help="Output CSV (default: data/reference/cfb_team_unit_rankings.csv)",
    )
    args = ap.parse_args()

    cfb_root = Path(__file__).resolve().parents[1]
    out_path = Path(args.out) if args.out else cfb_root / "data" / "reference" / "cfb_team_unit_rankings.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"→ ESPN CFB byteam season={args.season} seasontype={args.seasontype}")
    df = build_rankings_table(args.season, seasontype=args.seasontype)
    if df.empty:
        print("❌ No FBS conference teams ranked — check season / network.")
        raise SystemExit(1)

    df.to_csv(out_path, index=False)
    print(f"✅ Wrote {len(df)} teams → {out_path}")
    for conf in sorted(df["conference"].unique()):
        n = len(df[df["conference"] == conf])
        print(f"   {conf}: {n} teams")


if __name__ == "__main__":
    main()
