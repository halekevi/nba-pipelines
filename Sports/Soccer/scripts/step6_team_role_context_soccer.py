#!/usr/bin/env python3
"""
step6_team_role_context_soccer.py  (Soccer Pipeline)

Mirrors NBA step6_team_role_context.py but uses soccer positions:
  GK  = Goalkeeper
  DEF = Defender
  MID = Midfielder
  FWD = Forward / Attacker

Adds:
  minutes_tier    LOW / MEDIUM / HIGH
  shot_role       LOW_VOL / MID_VOL / HIGH_VOL
  usage_role      SUPPORT / SECONDARY / PRIMARY
  position_group  GK / DEF / MID / FWD

Run:
  py -3.14 step6_team_role_context_soccer.py \
    --input step5_soccer_hit_rates.csv \
    --output step6_soccer_role_context.csv
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

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_CACHE_SLUG = "soccer"
SOCCER_LEAGUE_MAP = {
    "EPL": "soccer_epl",
    "MLS": "soccer_usa_mls",
}

SOCCER_TEAM_NAME_MAP = {
    "ARS": "Arsenal",
    "AVL": "Aston Villa",
    "BOU": "Bournemouth",
    "BRE": "Brentford",
    "BHA": "Brighton and Hove Albion",
    "CHE": "Chelsea",
    "CRY": "Crystal Palace",
    "EVE": "Everton",
    "FUL": "Fulham",
    "IPS": "Ipswich Town",
    "LEI": "Leicester City",
    "LIV": "Liverpool",
    "MCI": "Manchester City",
    "MUN": "Manchester United",
    "NEW": "Newcastle United",
    "NFO": "Nottingham Forest",
    "SOU": "Southampton",
    "TOT": "Tottenham Hotspur",
    "WHU": "West Ham United",
    "WOL": "Wolverhampton Wanderers",
}


# ── Soccer position normalizer ───────────────────────────────────────────────

POSITION_MAP = {
    # Goalkeeper variants
    "gk":  "GK", "goalkeeper": "GK", "g": "GK", "portero": "GK",
    # Defender variants
    "d":   "DEF", "def": "DEF", "defender": "DEF", "cb": "DEF", "lb": "DEF",
    "rb":  "DEF", "rwb": "DEF", "lwb": "DEF", "centre-back": "DEF",
    "fullback": "DEF",
    # Midfielder variants
    "m":   "MID", "mid": "MID", "midfielder": "MID", "cm": "MID", "cdm": "MID",
    "cam": "MID", "lm": "MID", "rm": "MID", "dm": "MID", "winger": "MID",
    "lw":  "MID", "rw": "MID",
    # Forward/Attacker variants
    "f":   "FWD", "fwd": "FWD", "forward": "FWD", "st": "FWD", "cf": "FWD",
    "striker": "FWD", "attacker": "FWD", "ss": "FWD",
}

def norm_position(pos: str) -> str:
    p = str(pos or "").lower().strip().replace("-", "").replace(" ", "")
    return POSITION_MAP.get(p, "MID")   # default MID if unknown


def _norm_name(v: str) -> str:
    return "".join(ch for ch in str(v or "").strip().lower() if ch.isalnum())


def _team_for_match(v: str) -> str:
    if pd.isna(v):
        return ""
    raw = str(v or "").strip()
    if not raw or raw.upper() in {"NAN", "NONE", "NULL"}:
        return ""
    upper = raw.upper()
    return SOCCER_TEAM_NAME_MAP.get(upper, raw)


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
            if str(payload.get("date", ""))[:10] == date_str and str(payload.get("sport_key", "")) == sport_key:
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
        print(f"[ODDS] SOCCER: fetch failed ({exc})")
        return pd.DataFrame(columns=["home_team", "away_team", "game_total", "home_spread", "away_spread", "game_date"]), ""
    remaining = str(resp.headers.get("x-requests-remaining", ""))
    if resp.status_code != 200:
        print(f"[ODDS] SOCCER: HTTP {resp.status_code} — using null odds")
        return pd.DataFrame(columns=["home_team", "away_team", "game_total", "home_spread", "away_spread", "game_date"]), remaining
    try:
        rem_i = int(remaining)
    except Exception:
        rem_i = 9999
    if rem_i < 50:
        print(f"[ODDS] SOCCER: requests_remaining={remaining} (<50) — skipping live fetch and using cache/null")
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


# ── Tier functions ────────────────────────────────────────────────────────────
# Soccer averages are lower than NBA — tune thresholds accordingly

def tier_minutes(x) -> str:
    """Soccer players play 90 min max. Most starters play 70+."""
    if pd.isna(x):    return "UNKNOWN"
    if x < 45:        return "LOW"       # Sub / limited role
    if x < 70:        return "MEDIUM"    # Rotational / partial
    return "HIGH"                        # Regular starter


def tier_shot_volume(x, pos_group: str = "MID") -> str:
    """Shot volume tier by position group (shots per game, last 5)."""
    if pd.isna(x):    return "UNKNOWN"
    if pos_group == "FWD":
        if x <= 1.5:  return "LOW_VOL"
        if x <= 3.0:  return "MID_VOL"
        return "HIGH_VOL"
    elif pos_group == "MID":
        if x <= 0.8:  return "LOW_VOL"
        if x <= 2.0:  return "MID_VOL"
        return "HIGH_VOL"
    else:  # DEF / GK
        if x <= 0.3:  return "LOW_VOL"
        if x <= 1.0:  return "MID_VOL"
        return "HIGH_VOL"


def tier_passes(x, pos_group: str = "MID") -> str:
    """Passes attempted per game."""
    if pd.isna(x):    return "UNKNOWN"
    if pos_group == "GK":
        thresholds = (20, 40)
    elif pos_group == "DEF":
        thresholds = (30, 55)
    elif pos_group == "MID":
        thresholds = (40, 70)
    else:  # FWD
        thresholds = (20, 40)
    if x <= thresholds[0]: return "SUPPORT"
    if x <= thresholds[1]: return "SECONDARY"
    return "PRIMARY"


def tier_field_involvement(x, pos_group: str = "MID") -> str:
    """
    Involvement tier based on passes + shots composite proxy.
    Thresholds differ by position since MF/DEF touch the ball far more than FWD.
    """
    if pd.isna(x):    return "UNKNOWN"
    if pos_group == "FWD":
        if x <= 20:   return "FRINGE"
        if x <= 40:   return "ROTATIONAL"
        return "STARTER"
    elif pos_group == "MID":
        if x <= 30:   return "FRINGE"
        if x <= 60:   return "ROTATIONAL"
        return "STARTER"
    elif pos_group == "DEF":
        if x <= 25:   return "FRINGE"
        if x <= 50:   return "ROTATIONAL"
        return "STARTER"
    else:  # GK
        if x <= 15:   return "FRINGE"
        if x <= 35:   return "ROTATIONAL"
        return "STARTER"


def _assign_starter_tier(row: pd.Series) -> str:
    """
    Priority order:
    1. avg_minutes (real DB data when available)
    2. minutes_tier from step6 heuristics (HIGH/MEDIUM/LOW)
    3. position_group + pick_type inference
    """
    # Level 1 — real minutes data
    avg_min = pd.to_numeric(pd.Series([row.get("avg_minutes")]), errors="coerce").iloc[0]
    if pd.notna(avg_min) and float(avg_min) > 0:
        m = float(avg_min)
        if m >= 60:
            return "STARTER"
        if m >= 30:
            return "ROTATION"
        return "SUB"

    # Level 2 — minutes_tier heuristic from step6
    mt = str(row.get("minutes_tier") or "").strip().upper()
    if mt == "HIGH":
        return "STARTER"
    if mt == "MEDIUM":
        return "ROTATION"
    if mt == "LOW":
        return "SUB"

    # Level 3 — position + pick_type inference
    pos = str(row.get("position_group") or "").strip().upper()
    pick = str(row.get("pick_type") or "").strip().lower()

    # Goalkeepers almost always start (only 1 per team)
    if pos == "GK":
        return "STARTER"

    # Goblin lines = low lines = player likely plays limited minutes
    if "goblin" in pick:
        return "ROTATION"

    # DEF/MID with standard lines typically start
    if pos in ("DEF", "MID"):
        return "STARTER"

    # FWD — could be starter or rotation, default rotation
    if pos == "FWD":
        return "ROTATION"

    return "UNKNOWN"


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--date", default="", help="Slate date YYYY-MM-DD (default from data/start_time)")
    ap.add_argument("--odds-api-key", default="", help="Override ODDS_API_KEY for game totals/spreads")
    ap.add_argument("--league", default="EPL", help="Soccer league key for Odds API (EPL|MLS)")
    args = ap.parse_args()

    print(f"→ Loading: {args.input}")
    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")

    if df.empty:
        print("❌ [PropOracle-Soccer-S6] Empty input from S5 — aborting.")
        sys.exit(1)

    # Normalize position to group
    if "pos" in df.columns:
        df["position_group"] = df["pos"].astype(str).apply(norm_position)
    else:
        df["position_group"] = "MID"

    # Pull numeric averages
    last5_avg   = pd.to_numeric(df.get("stat_last5_avg",   pd.Series(dtype=float)), errors="coerce")
    season_avg  = pd.to_numeric(df.get("stat_season_avg",  pd.Series(dtype=float)), errors="coerce")
    avg_minutes = pd.to_numeric(df.get("avg_minutes",      pd.Series(dtype=float)), errors="coerce")

    prop_norm = df.get("prop_norm", pd.Series([""] * len(df))).astype(str)

    new_cols = {}

    # minutes_tier — use avg_minutes from S4 stats when available,
    # fall back to position-based inference (soccer ESPN feed rarely has minutes)
    def _minutes_tier(i):
        v = avg_minutes.iloc[i]
        if not pd.isna(v):
            return tier_minutes(v)
        # Infer from position: starters in key positions → HIGH, subs/GK backups → MEDIUM
        pg = df["position_group"].iloc[i] if "position_group" in df.columns else "MID"
        pt = str(df.get("pick_type", pd.Series(["Standard"] * len(df))).iloc[i]).lower()
        # Goblins tend to be set for players who DO play; Demons for players with high usage
        if "gob" in pt:  return "HIGH"
        if "dem" in pt:  return "HIGH"
        # Position inference: GK and starting DEF/MID typically play full 90
        if pg == "GK":   return "HIGH"
        if pg == "DEF":  return "MEDIUM"
        if pg == "MID":  return "MEDIUM"
        return "LOW"  # FWD with no data — rotational

    new_cols["minutes_tier"] = pd.Series([_minutes_tier(i) for i in range(len(df))], index=df.index)
    df["starter_tier"] = df.apply(_assign_starter_tier, axis=1)

    # shot_volume — shooting output tier per position
    def _shot_volume(i):
        pg  = df["position_group"].iloc[i] if "position_group" in df.columns else "MID"
        val = last5_avg.iloc[i] if prop_norm.iloc[i] == "shots" else np.nan
        return tier_shot_volume(val, pg)
    new_cols["shot_volume"] = pd.Series([_shot_volume(i) for i in range(len(df))], index=df.index)

    # field_involvement — composite passes+shots involvement proxy
    def _field_involvement(i):
        pg  = df["position_group"].iloc[i] if "position_group" in df.columns else "MID"
        val = last5_avg.iloc[i] if prop_norm.iloc[i] == "passes" else season_avg.iloc[i]
        return tier_field_involvement(val, pg)
    new_cols["field_involvement"] = pd.Series([_field_involvement(i) for i in range(len(df))], index=df.index)

    # pass_role
    def _pass_role(i):
        pg  = df["position_group"].iloc[i] if "position_group" in df.columns else "MID"
        val = last5_avg.iloc[i] if prop_norm.iloc[i] == "passes" else np.nan
        return tier_passes(val, pg)
    new_cols["pass_role"] = pd.Series([_pass_role(i) for i in range(len(df))], index=df.index)

    # GK flag — useful downstream
    if "position_group" in df.columns:
        new_cols["is_goalkeeper"] = (df["position_group"] == "GK").astype(int)
    else:
        new_cols["is_goalkeeper"] = 0

    df = pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1).copy()

    # Add aliases so step8 column references work correctly
    # step8 looks for shot_role and usage_role; step6 computes shot_volume and field_involvement
    df["shot_role"]  = df["shot_volume"]
    df["usage_role"] = df["field_involvement"]

    # Odds API game totals/spreads
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
    league = str(args.league or "EPL").strip().upper()
    sport_key = SOCCER_LEAGUE_MAP.get(league, "soccer_usa_mls")
    odds_api_key = _load_odds_api_key(args.odds_api_key)
    odds_df, req_remaining = fetch_game_odds(sport_key, inferred_date, odds_api_key)

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
    print(f"[ODDS] SOCCER: {matched} games matched, requests_remaining={req_remaining or 'n/a'}")

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    copy_pipeline_output_to_dated_dirs(
        output_path=args.output,
        df=df,
        sport_dir_name="Soccer",
        repo_root=_REPO_ROOT,
    )
    if df.empty:
        print("❌ [PropOracle-Soccer-S6] Output is empty — aborting.")
        sys.exit(1)
    print(f"✅ Saved → {args.output}  rows={len(df)}")
    if "position_group" in df.columns:
        print("position_group breakdown:")
        print(df["position_group"].value_counts().to_string())
    if "minutes_tier" in df.columns:
        print("minutes_tier breakdown:")
        print(df["minutes_tier"].value_counts().to_string())
    if "shot_volume" in df.columns:
        print("shot_volume breakdown:")
        print(df["shot_volume"].value_counts().to_string())
    if "field_involvement" in df.columns:
        print("field_involvement breakdown:")
        print(df["field_involvement"].value_counts().to_string())


if __name__ == "__main__":
    main()
