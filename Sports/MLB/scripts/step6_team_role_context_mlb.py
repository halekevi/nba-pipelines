#!/usr/bin/env python3
"""
step6_team_role_context_mlb.py  (MLB Pipeline)

MLB-specific role context:
  - Hitters: batting_order_tier (LEADOFF / MID / BOTTOM)
  - Pitchers: pitcher_role (SP / RP / CLOSER)
  - minutes_tier proxy based on at-bats or innings pitched averages

Adds:
  minutes_tier      (LOW / MEDIUM / HIGH  — proxy for playing time)
  batting_order_tier (LEADOFF / MID / BOTTOM / UNKNOWN)
  pitcher_role       (SP / RP / CLOSER / UNKNOWN)
  player_type_norm   (hitter / pitcher)

Run:
  py -3.14 step6_team_role_context_mlb.py \
    --input step5_mlb_hit_rates.csv \
    --output step6_mlb_role_context.csv
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

PITCHER_PROPS = {
    "strikeouts", "pitching_outs", "innings_pitched",
    "hits_allowed", "earned_runs", "walks_allowed", "batters_faced",
    "pitches_thrown",
}
GAME_FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
MLB_SPORT_KEY = "baseball_mlb"
ODDS_CACHE_SLUG = "mlb"

MLB_TEAM_NAME_MAP = {
    "ARI": "Arizona Diamondbacks",
    "ATL": "Atlanta Braves",
    "BAL": "Baltimore Orioles",
    "BOS": "Boston Red Sox",
    "CHC": "Chicago Cubs",
    "CIN": "Cincinnati Reds",
    "CLE": "Cleveland Guardians",
    "COL": "Colorado Rockies",
    "CWS": "Chicago White Sox",
    "DET": "Detroit Tigers",
    "HOU": "Houston Astros",
    "KC": "Kansas City Royals",
    "KCR": "Kansas City Royals",
    "LAA": "Los Angeles Angels",
    "LAD": "Los Angeles Dodgers",
    "MIA": "Miami Marlins",
    "MIL": "Milwaukee Brewers",
    "MIN": "Minnesota Twins",
    "NYM": "New York Mets",
    "NYY": "New York Yankees",
    "OAK": "Athletics",
    "ATH": "Athletics",
    "PHI": "Philadelphia Phillies",
    "PIT": "Pittsburgh Pirates",
    "SD": "San Diego Padres",
    "SDP": "San Diego Padres",
    "SEA": "Seattle Mariners",
    "SF": "San Francisco Giants",
    "SFG": "San Francisco Giants",
    "STL": "St. Louis Cardinals",
    "TB": "Tampa Bay Rays",
    "TBR": "Tampa Bay Rays",
    "TEX": "Texas Rangers",
    "TOR": "Toronto Blue Jays",
    "WSH": "Washington Nationals",
}

# ── Position normalizer ───────────────────────────────────────────────────────

PITCHER_POS = {"p", "sp", "rp", "cp", "lhp", "rhp", "pitcher", "starter", "reliever", "closer"}
CATCHER_POS = {"c"}
INFIELD_POS = {"1b", "2b", "3b", "ss", "if", "infielder"}
OUTFIELD_POS= {"lf", "cf", "rf", "of", "outfielder"}
DH_POS      = {"dh"}


def norm_pos(pos: str) -> str:
    p = str(pos or "").lower().strip().replace("-", "")
    if p in PITCHER_POS:  return "P"
    if p in CATCHER_POS:  return "C"
    if p in INFIELD_POS:  return "IF"
    if p in OUTFIELD_POS: return "OF"
    if p in DH_POS:       return "DH"
    return "UNK"


def batting_order_tier(pos_norm: str, batting_order=None) -> str:
    """
    Simple tier:
      - If batting order number available: 1-2=LEADOFF, 3-5=POWER, 6-9=BOTTOM
      - Else use position as proxy: C/IF/OF/DH all get MID, P gets UNKNOWN
    """
    if batting_order is not None and not (isinstance(batting_order, float) and np.isnan(batting_order)):
        try:
            o = int(batting_order)
            if o <= 2:   return "LEADOFF"
            if o <= 5:   return "POWER"
            return "BOTTOM"
        except Exception:
            pass
    if pos_norm == "P":   return "UNKNOWN"
    if pos_norm == "DH":  return "MID"
    return "MID"


def pitcher_role(pos: str, last5_ip_avg: float) -> str:
    """Classify pitcher role from position + avg innings."""
    p = str(pos or "").lower().strip()
    if "sp" in p or "starter" in p:
        return "SP"
    if "cp" in p or "closer" in p:
        return "CLOSER"
    if "rp" in p or "reliever" in p:
        return "RP"
    if not np.isnan(last5_ip_avg):
        if last5_ip_avg >= 4.0:  return "SP"
        if last5_ip_avg >= 1.0:  return "RP"
        return "CLOSER"
    return "UNKNOWN"


def minutes_tier_hitter(last5_ab_avg: float) -> str:
    """At-bats as proxy for playing time."""
    if np.isnan(last5_ab_avg): return "UNKNOWN"
    if last5_ab_avg >= 3.5:    return "HIGH"
    if last5_ab_avg >= 2.0:    return "MEDIUM"
    return "LOW"


def minutes_tier_pitcher(last5_ip_avg: float) -> str:
    """Innings pitched as proxy for workload."""
    if np.isnan(last5_ip_avg): return "UNKNOWN"
    if last5_ip_avg >= 5.0:    return "HIGH"
    if last5_ip_avg >= 1.5:    return "MEDIUM"
    return "LOW"


def _to_float(v) -> float:
    x = pd.to_numeric(pd.Series([v]), errors="coerce").iloc[0]
    return float(x) if not pd.isna(x) else np.nan


def _norm_team(v) -> str:
    return str(v or "").strip().upper()


def _norm_name(v: str) -> str:
    return "".join(ch for ch in str(v or "").strip().lower() if ch.isalnum())


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
    """
    Fetch totals/spreads from Odds API with daily cache fallback.
    Returns (game_odds_df, requests_remaining).
    """
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
        print(f"[ODDS] MLB: fetch failed ({exc})")
        return pd.DataFrame(columns=["home_team", "away_team", "game_total", "home_spread", "away_spread", "game_date"]), ""

    remaining = str(resp.headers.get("x-requests-remaining", ""))
    if resp.status_code != 200:
        print(f"[ODDS] MLB: HTTP {resp.status_code} — using null odds")
        return pd.DataFrame(columns=["home_team", "away_team", "game_total", "home_spread", "away_spread", "game_date"]), remaining

    try:
        rem_i = int(remaining)
    except Exception:
        rem_i = 9999
    if rem_i < 50:
        print(f"[ODDS] MLB: requests_remaining={remaining} (<50) — skipping live fetch and using cache/null")
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


def _game_key(v1: str, v2: str) -> tuple[str, str]:
    a = _norm_name(v1)
    b = _norm_name(v2)
    return (a, b) if a <= b else (b, a)


def _team_for_match(v: str) -> str:
    if pd.isna(v):
        return ""
    raw = _norm_team(v)
    if not raw or raw in {"NAN", "NONE", "NULL"}:
        return ""
    return MLB_TEAM_NAME_MAP.get(raw, raw)


def _parse_player_id(v: str) -> str:
    s = str(v or "").strip()
    if not s or "|" in s:
        return ""
    try:
        return str(int(float(s)))
    except Exception:
        return s if s.isdigit() else ""


def _load_stats_cache(path: str) -> pd.DataFrame:
    try:
        cache = pd.read_csv(path, dtype=str, low_memory=False).fillna("")
    except Exception:
        return pd.DataFrame(columns=["MLB_PLAYER_ID", "SEASON", "GAME_DATE", "GAME_ID", "PROP_NORM", "STAT_VALUE"])
    return cache


def _fetch_game_teams(game_id: str, team_cache: dict[str, tuple[str, str] | None]) -> tuple[str, str] | None:
    gid = str(game_id or "").strip()
    if not gid:
        return None
    if gid in team_cache:
        return team_cache[gid]
    try:
        r = requests.get(GAME_FEED_URL.format(game_id=gid), timeout=12)
        r.raise_for_status()
        j = r.json() or {}
        t = (j.get("gameData", {}) or {}).get("teams", {}) or {}
        home = _norm_team((t.get("home", {}) or {}).get("abbreviation", ""))
        away = _norm_team((t.get("away", {}) or {}).get("abbreviation", ""))
        val = (home, away) if home and away else None
    except Exception:
        val = None
    team_cache[gid] = val
    return val


def _derive_cache_opp_team(
    rec: dict,
    player_team: str,
    team_cache: dict[str, tuple[str, str] | None],
) -> str:
    for col in ("OPP_TEAM", "opp_team", "OPPONENT", "opponent", "OPP"):
        if col in rec and str(rec.get(col, "")).strip():
            return _norm_team(rec.get(col, ""))

    game_id = str(rec.get("GAME_ID", "")).strip()
    game_teams = _fetch_game_teams(game_id, team_cache)
    if not game_teams:
        return ""
    home, away = game_teams

    rec_team = ""
    for col in ("TEAM", "team"):
        if col in rec and str(rec.get(col, "")).strip():
            rec_team = _norm_team(rec.get(col, ""))
            break
    team = rec_team or _norm_team(player_team)
    if not team:
        return ""
    if team == home:
        return away
    if team == away:
        return home
    return ""


def _compute_same_series_hit_rate(
    row: pd.Series,
    cache: pd.DataFrame,
    team_cache: dict[str, tuple[str, str] | None],
) -> float:
    pid = _parse_player_id(row.get("mlb_player_id", ""))
    prop = str(row.get("prop_norm", "")).strip().lower()
    opp_team = _norm_team(row.get("opp_team", ""))
    player_team = _norm_team(row.get("team", ""))
    line = _to_float(row.get("line", np.nan))
    if (not pid) or (not prop) or (not opp_team) or np.isnan(line):
        return np.nan

    season = str(row.get("season", "")).strip()
    if not season:
        st = pd.to_datetime(pd.Series([row.get("start_time", "")]), errors="coerce").iloc[0]
        gd = pd.to_datetime(pd.Series([row.get("game_date", "")]), errors="coerce").iloc[0]
        dt = gd if pd.notna(gd) else st
        if pd.notna(dt):
            season = str(dt.year)

    player_cache = cache.loc[
        (cache.get("MLB_PLAYER_ID", pd.Series("", index=cache.index)).astype(str) == pid)
        & (cache.get("PROP_NORM", pd.Series("", index=cache.index)).astype(str).str.lower().str.strip() == prop)
        & (cache.get("STAT_VALUE", pd.Series("", index=cache.index)).astype(str).str.strip() != "")
    ].copy()
    if season:
        player_cache = player_cache.loc[
            player_cache.get("SEASON", pd.Series("", index=player_cache.index)).astype(str).str.strip() == season
        ]
    if player_cache.empty:
        return np.nan

    player_cache["GAME_DATE_TS"] = pd.to_datetime(player_cache.get("GAME_DATE", ""), errors="coerce")
    player_cache = player_cache.sort_values("GAME_DATE_TS", ascending=False).head(5)
    if player_cache.empty:
        return np.nan

    stat_vals: list[float] = []
    for rec in player_cache.to_dict("records"):
        rec_opp = _derive_cache_opp_team(rec, player_team, team_cache)
        if rec_opp != opp_team:
            continue
        stat = _to_float(rec.get("STAT_VALUE", np.nan))
        if not np.isnan(stat):
            stat_vals.append(stat)

    if len(stat_vals) < 2:
        return np.nan
    return float(sum(1 for v in stat_vals if v > line) / len(stat_vals))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  default="step5_mlb_hit_rates.csv")
    ap.add_argument("--output", default="step6_mlb_role_context.csv")
    ap.add_argument("--stats-cache", default="mlb_stats_cache.csv")
    ap.add_argument("--date", default="", help="Slate date YYYY-MM-DD (default from data/start_time)")
    ap.add_argument("--odds-api-key", default="", help="Override ODDS_API_KEY for game totals/spreads")
    args = ap.parse_args()

    print(f"→ Loading: {args.input}")
    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")

    prop_norm   = df.get("prop_norm", pd.Series([""] * len(df))).astype(str).str.lower().str.strip()
    pos_col     = df.get("pos",         pd.Series([""] * len(df))).astype(str)
    player_type = df.get("player_type", pd.Series([""] * len(df))).astype(str).str.lower().str.strip()

    # Normalize player_type from prop_norm if missing
    def infer_ptype(i):
        pt = player_type.iloc[i]
        if pt in ("pitcher", "hitter"):
            return pt
        return "pitcher" if prop_norm.iloc[i] in PITCHER_PROPS else "hitter"

    df["player_type_norm"] = pd.Series([infer_ptype(i) for i in range(len(df))], index=df.index)

    pos_norm = pos_col.apply(norm_pos)
    df["pos_norm"] = pos_norm

    # Stat averages
    last5_avg  = pd.to_numeric(df.get("stat_last5_avg",  pd.Series(dtype=float)), errors="coerce")
    season_avg = pd.to_numeric(df.get("stat_season_avg", pd.Series(dtype=float)), errors="coerce")

    # --- minutes_tier ---
    def _mt(i):
        pt = df["player_type_norm"].iloc[i]
        if pt == "pitcher":
            # use innings pitched avg (prop_norm = innings_pitched → last5_avg in innings)
            ip_avg = last5_avg.iloc[i] if prop_norm.iloc[i] == "innings_pitched" else season_avg.iloc[i]
            return minutes_tier_pitcher(ip_avg if not np.isnan(ip_avg) else np.nan)
        else:
            return "HIGH"   # Most MLB hitters who are in the lineup play full games

    df["minutes_tier"] = pd.Series([_mt(i) for i in range(len(df))], index=df.index)

    # --- batting_order_tier ---
    bat_ord_col = df.get("batting_order", pd.Series([np.nan] * len(df)))
    def _bot(i):
        pt = df["player_type_norm"].iloc[i]
        if pt == "pitcher":
            return "UNKNOWN"
        bo = pd.to_numeric(pd.Series([bat_ord_col.iloc[i]]), errors="coerce").iloc[0]
        return batting_order_tier(pos_norm.iloc[i], bo if not np.isnan(bo) else None)

    df["batting_order_tier"] = pd.Series([_bot(i) for i in range(len(df))], index=df.index)

    # --- pitcher_role ---
    def _pr(i):
        pt = df["player_type_norm"].iloc[i]
        if pt != "pitcher":
            return "N/A"
        ip_avg = last5_avg.iloc[i] if prop_norm.iloc[i] == "innings_pitched" else season_avg.iloc[i]
        return pitcher_role(pos_col.iloc[i], ip_avg if not np.isnan(ip_avg) else np.nan)

    df["pitcher_role"] = pd.Series([_pr(i) for i in range(len(df))], index=df.index)

    # --- same_series_hit_rate ---
    stats_cache = _load_stats_cache(args.stats_cache)
    team_lookup_cache: dict[str, tuple[str, str] | None] = {}
    df["same_series_hit_rate"] = pd.Series(
        [_compute_same_series_hit_rate(df.iloc[i], stats_cache, team_lookup_cache) for i in range(len(df))],
        index=df.index,
    )

    # --- game_total + spread from Odds API ---
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
    odds_df, req_remaining = fetch_game_odds(MLB_SPORT_KEY, inferred_date, odds_api_key)
    game_lookup: dict[tuple[str, str], dict] = {}
    if not odds_df.empty:
        for _, r in odds_df.iterrows():
            game_lookup[_game_key(str(r.get("home_team", "")), str(r.get("away_team", "")))] = {
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
        rec = game_lookup.get(_game_key(home, away)) if home and away else None
        if rec is None:
            game_total_vals.append(np.nan)
            spread_vals.append(np.nan)
            continue
        gt = rec.get("game_total")
        hs = rec.get("home_spread")
        a_s = rec.get("away_spread")
        spread = hs if _norm_name(team) == _norm_name(home) else a_s if _norm_name(team) == _norm_name(away) else np.nan
        game_total_vals.append(gt if pd.notna(gt) else np.nan)
        spread_vals.append(spread if pd.notna(spread) else np.nan)
        matched += 1
    df["game_total"] = game_total_vals
    df["spread"] = spread_vals
    print(f"[ODDS] MLB: {matched} games matched, requests_remaining={req_remaining or 'n/a'}")

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    copy_pipeline_output_to_dated_dirs(
        output_path=args.output,
        df=df,
        sport_dir_name="MLB",
        repo_root=_REPO_ROOT,
    )
    print(f"✅ Saved → {args.output}  rows={len(df)}")
    print("minutes_tier:",       df["minutes_tier"].value_counts().to_dict())
    print("batting_order_tier:", df["batting_order_tier"].value_counts().to_dict())
    print("pitcher_role:",       df["pitcher_role"].value_counts().to_dict())
    print("player_type_norm:",   df["player_type_norm"].value_counts().to_dict())
    print("same_series_hit_rate (non-null):", int(pd.to_numeric(df["same_series_hit_rate"], errors="coerce").notna().sum()))


if __name__ == "__main__":
    main()
