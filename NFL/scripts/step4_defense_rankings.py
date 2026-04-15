#!/usr/bin/env python3
"""
NFL step4 — team defensive summary for pass / rush matchup context.

Priority:
  1. Pro Football Reference (skipped here: Cloudflare blocks simple HTTP clients)
  2. ESPN: NFL standings (points against) + team statistics byteam (opponent pass/rush)

Output: NFL/data/defense_rankings.csv

  set NFL_PIPELINE_ACTIVE=1
  py -3.14 scripts/step4_defense_rankings.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import requests

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _nfl_pipeline_active import require_nfl_pipeline_active_or_exit

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

# Fallback if ESPN is unreachable (32 teams; ranks are placeholders).
_FALLBACK_ABBR = [
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB",
    "HOU", "IND", "JAX", "KC", "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG",
    "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WSH",
]


def _standings_points_against(season: int) -> dict[str, dict[str, float]]:
    url = f"https://site.api.espn.com/apis/v2/sports/football/nfl/standings?season={season}&type=0"
    r = requests.get(url, headers=HEADERS, timeout=45)
    r.raise_for_status()
    j = r.json()
    out: dict[str, dict[str, float]] = {}
    for child in j.get("children") or []:
        for entry in (child.get("standings") or {}).get("entries") or []:
            team = entry.get("team") or {}
            abbr = str(team.get("abbreviation") or "").strip().upper()
            if not abbr:
                continue
            games = 0.0
            pa = 0.0
            for st in entry.get("stats") or []:
                name = str(st.get("name") or "")
                if name == "wins":
                    games += float(st.get("value") or 0)
                elif name == "losses":
                    games += float(st.get("value") or 0)
                elif name == "ties":
                    games += float(st.get("value") or 0)
                elif name == "pointsAgainst":
                    pa = float(st.get("value") or 0)
            out[abbr] = {"points_against": pa, "games": max(games, 1.0)}
    return out


def _byteam_opponent_yards(season: int) -> dict[str, dict[str, float]]:
    url = (
        "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/statistics/byteam"
        f"?season={season}&seasontype=2&contentorigin=espn"
    )
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    j = r.json()
    root_labels: dict[str, list[str]] = {}
    for cat in j.get("categories") or []:
        name = str(cat.get("name") or "")
        if name in ("passing", "rushing") and cat.get("labels"):
            root_labels[name] = list(cat["labels"])

    out: dict[str, dict[str, float]] = {}
    for t in j.get("teams") or []:
        team = t.get("team") or {}
        abbr = str(team.get("abbreviation") or "").strip().upper()
        if not abbr:
            continue
        pass_pg = rush_pg = pass_td = float("nan")
        for cat in t.get("categories") or []:
            disp = str(cat.get("displayName") or "")
            vals = cat.get("values") or []
            if disp == "Opponent Passing" and "passing" in root_labels:
                labs = root_labels["passing"]
                yds_g_idx = [i for i, lab in enumerate(labs) if lab == "YDS/G"]
                # ESPN lists two YDS/G blocks; the last one pairs with opponent passing YDS.
                if yds_g_idx and yds_g_idx[-1] < len(vals):
                    pass_pg = float(vals[yds_g_idx[-1]])
                if "TD" in labs:
                    ti = labs.index("TD")
                    if ti < len(vals):
                        pass_td = float(vals[ti])
            if disp == "Opponent Rushing" and "rushing" in root_labels:
                labs = root_labels["rushing"]
                yds_g_idx = [i for i, lab in enumerate(labs) if lab == "YDS/G"]
                if yds_g_idx and yds_g_idx[0] < len(vals):
                    rush_pg = float(vals[yds_g_idx[0]])
        out[abbr] = {
            "pass_yards_allowed_pg": pass_pg,
            "rush_yards_allowed_pg": rush_pg,
            "pass_tds_allowed": pass_td,
        }
    return out


def _rank_series(values: pd.Series, *, ascending: bool = True) -> pd.Series:
    """1 = best (lowest yards allowed when ascending=True)."""
    return values.rank(method="min", ascending=ascending).astype(int)


def _fallback_df() -> pd.DataFrame:
    rows = []
    for i, abbr in enumerate(_FALLBACK_ABBR):
        rows.append(
            {
                "team": abbr,
                "pass_yards_allowed_pg": 230.0,
                "rush_yards_allowed_pg": 115.0,
                "pass_tds_allowed": 24.0,
                "points_allowed_pg": 22.0,
                "pass_def_rank": i + 1,
                "rush_def_rank": i + 1,
            }
        )
    return pd.DataFrame(rows)


def fetch_defense_table(season: int) -> pd.DataFrame:
    try:
        pa = _standings_points_against(season)
        yd = _byteam_opponent_yards(season)
        rows: list[dict[str, Any]] = []
        for abbr in sorted(set(pa.keys()) | set(yd.keys())):
            g = pa.get(abbr, {}).get("games", 17.0)
            pts = pa.get(abbr, {}).get("points_against", float("nan"))
            y = yd.get(abbr, {})
            rows.append(
                {
                    "team": abbr,
                    "pass_yards_allowed_pg": y.get("pass_yards_allowed_pg", float("nan")),
                    "rush_yards_allowed_pg": y.get("rush_yards_allowed_pg", float("nan")),
                    "pass_tds_allowed": y.get("pass_tds_allowed", float("nan")),
                    "points_allowed_pg": float(pts) / float(g) if g else float("nan"),
                }
            )
        df = pd.DataFrame(rows)
        df = df.dropna(subset=["team"])
        df["pass_def_rank"] = _rank_series(df["pass_yards_allowed_pg"], ascending=True)
        df["rush_def_rank"] = _rank_series(df["rush_yards_allowed_pg"], ascending=True)
        return df.sort_values("team").reset_index(drop=True)
    except Exception as exc:
        print(f"[NFL step4] ESPN fetch failed ({type(exc).__name__}: {exc}); using fallback table.")
        return _fallback_df()


def main() -> None:
    require_nfl_pipeline_active_or_exit()

    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2024, help="NFL league year (e.g. 2024 for last full season)")
    ap.add_argument("--output", default="data/defense_rankings.csv")
    args = ap.parse_args()

    df = fetch_defense_table(int(args.season))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"[NFL step4] Wrote {out} rows={len(df)} (season={args.season})")


if __name__ == "__main__":
    main()
