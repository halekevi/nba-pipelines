#!/usr/bin/env python3
"""
step6_team_role_context.py  (WNBA)

WNBA Step 6:
- Adds minutes / shot / usage tiers for downstream ranking context
- Uses available stat columns from Step 4/5
"""

from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
WNBA_SPORT_KEY = "basketball_wnba"
ODDS_CACHE_SLUG = "wnba"

WNBA_TEAM_NAME_MAP = {
    "ATL": "Atlanta Dream",
    "CHI": "Chicago Sky",
    "CON": "Connecticut Sun",
    "DAL": "Dallas Wings",
    "IND": "Indiana Fever",
    "LAS": "Las Vegas Aces",
    "LVA": "Las Vegas Aces",
    "LAL": "Los Angeles Sparks",
    "MIN": "Minnesota Lynx",
    "NYL": "New York Liberty",
    "PHO": "Phoenix Mercury",
    "SEA": "Seattle Storm",
    "WAS": "Washington Mystics",
    "GSV": "Golden State Valkyries",
}


# ----------------------------
# Tiering
# ----------------------------

def tier_minutes(x):
    if pd.isna(x):
        return "UNKNOWN"
    if x < 24:
        return "LOW"
    if x < 32:
        return "MED"
    return "HIGH"

def tier_shots(x):
    if pd.isna(x):
        return "UNKNOWN"
    if x < 8:
        return "LOW_VOL"
    if x < 14:
        return "MID_VOL"
    return "HIGH_VOL"

def tier_usage(x):
    if pd.isna(x):
        return "UNKNOWN"
    if x < 7:
        return "SUPPORT"
    if x < 13:
        return "SECONDARY"
    return "PRIMARY"


def _norm_name(v: str) -> str:
    return "".join(ch for ch in str(v or "").strip().lower() if ch.isalnum())


def _team_for_match(v: str) -> str:
    if pd.isna(v):
        return ""
    raw = str(v or "").strip().upper()
    if not raw or raw in {"NAN", "NONE", "NULL"}:
        return ""
    return WNBA_TEAM_NAME_MAP.get(raw, raw)


def _game_key(v1: str, v2: str) -> tuple[str, str]:
    a = _norm_name(v1)
    b = _norm_name(v2)
    return (a, b) if a <= b else (b, a)


def _load_odds_api_key(explicit: str = "") -> str:
    key = str(explicit or "").strip() or str(os.getenv("ODDS_API_KEY", "")).strip()
    if key:
        return key
    env_path = _REPO_ROOT / ".env"
    if not env_path.is_file():
        return ""
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, val = line.partition("=")
            if k.strip() == "ODDS_API_KEY":
                return val.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def fetch_game_odds(
    sport_key: str,
    date_str: str,
    api_key: str,
) -> tuple[pd.DataFrame, str]:
    cache_path = _REPO_ROOT / "data" / "cache" / f"odds_{ODDS_CACHE_SLUG}_{date_str}.json"
    if cache_path.is_file():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if str(payload.get("date", ""))[:10] == date_str:
                rows = payload.get("rows", [])
                rem = str(payload.get("requests_remaining", "cache"))
                return pd.DataFrame(rows), rem
        except Exception:
            pass

    if not api_key:
        return pd.DataFrame(columns=["home_team", "away_team", "game_total", "home_spread", "away_spread", "game_date"]), ""

    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": "totals,spreads",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    url = f"{ODDS_API_BASE}/sports/{sport_key}/odds"
    try:
        resp = requests.get(url, params=params, timeout=25)
    except Exception as exc:
        print(f"[ODDS] WNBA: fetch failed ({exc})")
        return pd.DataFrame(columns=["home_team", "away_team", "game_total", "home_spread", "away_spread", "game_date"]), ""
    remaining = str(resp.headers.get("x-requests-remaining", ""))
    if resp.status_code != 200:
        print(f"[ODDS] WNBA: HTTP {resp.status_code} — using null odds")
        return pd.DataFrame(columns=["home_team", "away_team", "game_total", "home_spread", "away_spread", "game_date"]), remaining
    try:
        rem_i = int(remaining)
    except Exception:
        rem_i = 9999
    if rem_i < 50:
        print(f"[ODDS] WNBA: requests_remaining={remaining} (<50) — skipping live fetch and using cache/null")
        return pd.DataFrame(columns=["home_team", "away_team", "game_total", "home_spread", "away_spread", "game_date"]), remaining
    try:
        games = resp.json()
    except Exception:
        return pd.DataFrame(columns=["home_team", "away_team", "game_total", "home_spread", "away_spread", "game_date"]), remaining
    if not isinstance(games, list):
        return pd.DataFrame(columns=["home_team", "away_team", "game_total", "home_spread", "away_spread", "game_date"]), remaining

    rows = []
    for g in games:
        home = str(g.get("home_team", "")).strip()
        away = str(g.get("away_team", "")).strip()
        game_date = str(g.get("commence_time", ""))[:10]
        total = None
        home_spread = None
        away_spread = None
        for bm in g.get("bookmakers", []) or []:
            for m in bm.get("markets", []) or []:
                key = str(m.get("key", ""))
                outcomes = m.get("outcomes", []) or []
                if key == "totals" and total is None:
                    over = next((o for o in outcomes if str(o.get("name", "")).lower() == "over"), None)
                    if over is not None:
                        total = pd.to_numeric(over.get("point"), errors="coerce")
                        total = None if pd.isna(total) else float(total)
                elif key == "spreads":
                    for o in outcomes:
                        nm = str(o.get("name", "")).strip().lower()
                        pt = pd.to_numeric(o.get("point"), errors="coerce")
                        if pd.isna(pt):
                            continue
                        if nm == home.lower() and home_spread is None:
                            home_spread = float(pt)
                        elif nm == away.lower() and away_spread is None:
                            away_spread = float(pt)
            if total is not None and home_spread is not None and away_spread is not None:
                break
        rows.append(
            {
                "home_team": home,
                "away_team": away,
                "game_total": total,
                "home_spread": home_spread,
                "away_spread": away_spread,
                "game_date": game_date,
            }
        )
    out = pd.DataFrame(rows)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "date": date_str,
                    "sport_key": sport_key,
                    "requests_remaining": remaining,
                    "saved_at": datetime.utcnow().isoformat(),
                    "rows": out.to_dict(orient="records"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    return out, remaining


# ----------------------------
# Main
# ----------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--roles-csv", default=None)
    ap.add_argument("--defense-csv", default=None)
    ap.add_argument("--date", default="", help="Slate date YYYY-MM-DD (default from data/start_time)")
    ap.add_argument("--odds-api-key", default="", help="Override ODDS_API_KEY for game totals/spreads")
    args = ap.parse_args()

    print(f"→ Loading: {args.input}")
    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")

    # -----------------------------------
    # Basic tiers (already in Step5 stats)
    # -----------------------------------

    # WNBA boards mainly include pts/ast/reb/3ptmade/stl/blk. Use last5 + season
    # values from step4 as role signals (minutes metric falls back to season stat
    # signal when explicit minutes are unavailable).
    def _num_series(name: str) -> pd.Series:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce")
        return pd.Series(np.nan, index=df.index, dtype=float)

    last5 = _num_series("stat_last5_avg")
    season = _num_series("stat_season_avg")
    line = _num_series("line")
    min_signal = _num_series("min_player_avg")
    min_signal = min_signal.where(min_signal.notna(), season.where(season.notna(), last5))
    shot_signal = last5.where(last5.notna(), season.where(season.notna(), line))
    usage_signal = season.where(season.notna(), last5.where(last5.notna(), line))
    new_cols = {
        "min_player_avg": min_signal,
        "fga_player_avg": shot_signal,
        "pts_player_avg": usage_signal,
        "minutes_tier":   min_signal.apply(tier_minutes),
        "shot_role":      shot_signal.apply(tier_shots),
        "usage_role":     usage_signal.apply(tier_usage),
    }
    for c, s in new_cols.items():
        df[c] = s

    # -----------------------------------
    # Odds API game totals/spreads
    # -----------------------------------
    inferred_date = str(args.date or "").strip()[:10]
    if len(inferred_date) != 10:
        if "game_date" in df.columns:
            gd = pd.to_datetime(df["game_date"], errors="coerce").dropna()
            if len(gd):
                inferred_date = gd.dt.strftime("%Y-%m-%d").mode().iloc[0]
        if not inferred_date and "start_time" in df.columns:
            st = pd.to_datetime(df["start_time"], errors="coerce").dropna()
            if len(st):
                inferred_date = st.dt.strftime("%Y-%m-%d").mode().iloc[0]
    if len(inferred_date) != 10:
        inferred_date = datetime.utcnow().strftime("%Y-%m-%d")

    odds_api_key = _load_odds_api_key(args.odds_api_key)
    odds_df, req_remaining = fetch_game_odds(WNBA_SPORT_KEY, inferred_date, odds_api_key)
    lookup: dict[tuple[str, str], dict] = {}
    if not odds_df.empty:
        for _, r in odds_df.iterrows():
            lookup[_game_key(str(r.get("home_team", "")), str(r.get("away_team", "")))] = {
                "game_total": pd.to_numeric(r.get("game_total"), errors="coerce"),
                "home_spread": pd.to_numeric(r.get("home_spread"), errors="coerce"),
                "away_spread": pd.to_numeric(r.get("away_spread"), errors="coerce"),
            }

    matched = 0
    game_total_vals: list[float] = []
    spread_vals: list[float] = []
    for _, row in df.iterrows():
        home = _team_for_match(row.get("pp_home_team", ""))
        away = _team_for_match(row.get("pp_away_team", ""))
        team = _team_for_match(row.get("team", ""))
        opp = _team_for_match(row.get("opp_team", ""))
        if not home or not away:
            home = _team_for_match(row.get("team_1", ""))
            away = _team_for_match(row.get("team_2", ""))
        if (not home or not away) and team and opp:
            home, away = team, opp
        rec = lookup.get(_game_key(home, away)) if home and away else None
        if rec is None:
            game_total_vals.append(np.nan)
            spread_vals.append(np.nan)
            continue
        hs = rec.get("home_spread")
        a_s = rec.get("away_spread")
        spread = hs if _norm_name(team) == _norm_name(home) else a_s if _norm_name(team) == _norm_name(away) else np.nan
        game_total_vals.append(rec.get("game_total"))
        spread_vals.append(spread)
        matched += 1
    df["game_total"] = game_total_vals
    df["spread"] = spread_vals
    print(f"[ODDS] WNBA: {matched} games matched, requests_remaining={req_remaining or 'n/a'}")

    # -----------------------------------
    # Merge Team Roles (Step10)
    # -----------------------------------

    if args.roles_csv:
        print(f"→ Merging roles from: {args.roles_csv}")
        roles = pd.read_csv(args.roles_csv, encoding="utf-8-sig")

        roles["PLAYER_ID"] = roles["PLAYER_ID"].astype(str)
        df["cbb_player_id"] = df["cbb_player_id"].astype(str)

        role_cols = [c for c in roles.columns if c.startswith("role_")]

        df = df.merge(
            roles[["PLAYER_ID"] + role_cols],
            left_on="cbb_player_id",
            right_on="PLAYER_ID",
            how="left"
        )

        df.drop(columns=["PLAYER_ID"], inplace=True, errors="ignore")

    # -----------------------------------
    # Merge Defense (Step11)
    # -----------------------------------

    if args.defense_csv:
        print(f"→ Merging defense from: {args.defense_csv}")
        defense = pd.read_csv(args.defense_csv, encoding="utf-8-sig")

        defense["espn_team_id"] = defense["espn_team_id"].astype(str)
        df["espn_opp_team_id"] = df["espn_opp_team_id"].astype(str)

        defense_cols = [
            "espn_team_id",
            "OVERALL_DEF_RANK",
            "DEF_TIER"
        ]

        df = df.merge(
            defense[defense_cols],
            left_on="espn_opp_team_id",
            right_on="espn_team_id",
            how="left"
        )

        df.rename(columns={
            "OVERALL_DEF_RANK": "OPP_OVERALL_DEF_RANK",
            "DEF_TIER": "OPP_DEF_TIER"
        }, inplace=True)

        df.drop(columns=["espn_team_id"], inplace=True, errors="ignore")

    # -----------------------------------
    # Save
    # -----------------------------------

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    copy_pipeline_output_to_dated_dirs(
        output_path=args.output,
        df=df,
        sport_dir_name="WNBA",
        repo_root=_REPO_ROOT,
    )
    print(f"✅ Saved → {args.output}")
    print(f"Rows: {len(df)}")


if __name__ == "__main__":
    main()
