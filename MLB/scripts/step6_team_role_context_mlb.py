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
import numpy as np
import pandas as pd
import requests

PITCHER_PROPS = {
    "strikeouts", "pitching_outs", "innings_pitched",
    "hits_allowed", "earned_runs", "walks_allowed", "batters_faced",
    "pitches_thrown",
}
GAME_FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live"

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
    ap.add_argument("--input",  default="MLB/scripts/step5_mlb_hit_rates.csv")
    ap.add_argument("--output", default="MLB/scripts/step6_mlb_team_role.csv")
    ap.add_argument("--stats-cache", default="MLB/scripts/mlb_stats_cache.csv")
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

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"✅ Saved → {args.output}  rows={len(df)}")
    print("minutes_tier:",       df["minutes_tier"].value_counts().to_dict())
    print("batting_order_tier:", df["batting_order_tier"].value_counts().to_dict())
    print("pitcher_role:",       df["pitcher_role"].value_counts().to_dict())
    print("player_type_norm:",   df["player_type_norm"].value_counts().to_dict())
    print("same_series_hit_rate (non-null):", int(pd.to_numeric(df["same_series_hit_rate"], errors="coerce").notna().sum()))


if __name__ == "__main__":
    main()
