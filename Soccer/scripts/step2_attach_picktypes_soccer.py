#!/usr/bin/env python3
"""
step2_attach_picktypes_soccer.py  (Soccer Pipeline)

Changes:
- LEAGUE_SLUGS_FOR_ROSTER includes eng.3, esp.2, fra.2, usa.nwsl, aus.1, etc.
  (Saudi sau.1 removed: ESPN boxscore/scoreboard unreliable for grading.)
- Roster cache auto-rebuilds after ROSTER_CACHE_MAX_AGE_DAYS (default 7).
  Pass --refresh-roster to force an immediate rebuild.
- opp_team derived from pp_home_team/pp_away_team first; falls back to
  pp_game_id pairing for blank rows (common in soccer Step1).
- Works for singles and combos (combos get opp_team like "OPP1/OPP2").
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import random
import threading
import unicodedata
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Optional, Tuple, List

import pandas as pd
import requests
from pathlib import Path

COMBO_SEP = "|"
MANUAL_PP_MAP_PATH = "outputs/pp_to_espn_id_map_soccer.csv"

ESPN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Roster-based ID resolution — pulls all athletes from each league's teams
LEAGUE_SLUGS_FOR_ROSTER = [
    # Top 5 + UCL
    "eng.1", "ita.1", "esp.1", "ger.1", "fra.1", "uefa.champions",
    # Secondary European
    "eng.2", "eng.3", "esp.2", "fra.2", "por.1", "ned.1", "tur.1", "sco.1",
    # Americas
    "usa.1", "usa.nwsl", "arg.1", "arg.2", "bra.1", "mex.1",
    # Other
    "aus.1",
]

ESPN_TEAMS_URL  = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/teams?limit=100"
ESPN_ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/teams/{team_id}/roster"

PROP_NORM_MAP = {
    "shots": "shots",
    "shots on target": "shots_on_target",
    "shotsontarget": "shots_on_target",
    "goalie saves": "saves",
    "goaliesaves": "saves",
    "saves": "saves",
    "passes attempted": "passes",
    "passesattempted": "passes",
    "passes": "passes",
    "assists": "assists",
    "goals": "goals",
    "clearances": "clearances",
    "tackles": "tackles",
    "fouls": "fouls",
    "shots (combo)": "shots",
    "shots on target (combo)": "shots_on_target",
    "goalie saves (combo)": "saves",
    "goals allowed": "goals_allowed",
    "goals allowed (combo)": "goals_allowed",
    "goal + assist": "goal_assist",
    "shots assisted": "shots_assisted",
    "attempted dribbles": "attempted_dribbles",
    "attempteddribbles": "attempted_dribbles",
    "crosses": "crosses",
    "goals allowed in first 30 minutes": "goals_allowed_first30",
    "goalsallowedinfirst30minutes": "goals_allowed_first30",
}

# ── normalizers ──────────────────────────────────────────────────────────────

def norm_name(s: str) -> str:
    if not s or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower().strip())

def norm_pick_type(s: str) -> str:
    if not s or str(s).strip() == "":
        return "Standard"
    t = str(s).strip().lower()
    if "gob" in t: return "Goblin"
    if "dem" in t: return "Demon"
    return "Standard"

def norm_prop(s: str) -> str:
    if not s or (isinstance(s, float) and pd.isna(s)):
        return ""
    raw = str(s).lower().strip().replace("-", "").replace("_", "").replace(" ", "")
    for k, v in PROP_NORM_MAP.items():
        if raw == k.lower().replace("-", "").replace("_", "").replace(" ", ""):
            return v
    return str(s).lower().strip()

def detect_combo(player_str: str) -> int:
    return 1 if "+" in str(player_str or "") else 0

def split_combo_player(player_str: str) -> Tuple[str, str]:
    parts = [p.strip() for p in str(player_str or "").split("+")]
    return (parts[0], parts[1]) if len(parts) >= 2 else (parts[0], "")

def split_combo_team(team_str: str) -> Tuple[str, str]:
    parts = [p.strip() for p in str(team_str or "").split("/")]
    return (parts[0], parts[1]) if len(parts) >= 2 else (parts[0].strip(), "")

# ── Roster-based ESPN ID resolution ─────────────────────────────────────────

_local = threading.local()

def _get_session() -> requests.Session:
    if not hasattr(_local, "session"):
        s = requests.Session()
        s.headers.update(ESPN_HEADERS)
        _local.session = s
    return _local.session

def _safe_get_json(url: str, retries: int = 3, sleep_base: float = 0.5) -> Optional[dict]:
    session = _get_session()
    for attempt in range(1, retries + 1):
        try:
            time.sleep(sleep_base + random.uniform(0, 0.3))
            r = session.get(url, timeout=20)
            if r.status_code == 429:
                time.sleep(10 * attempt)
                continue
            if r.status_code in (403, 404):
                return None
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt < retries:
                time.sleep(2.0 * attempt)
    return None

def _fetch_team_ids(league: str) -> List[Tuple[str, str]]:
    url  = ESPN_TEAMS_URL.format(league=league)
    data = _safe_get_json(url)
    if not data:
        return []
    teams = []
    for t in (data.get("sports") or [{}])[0].get("leagues", [{}])[0].get("teams", []):
        team = t.get("team", t)
        tid  = str(team.get("id", "")).strip()
        name = str(team.get("displayName", team.get("name", ""))).strip()
        if tid:
            teams.append((tid, name))
    if not teams:
        for team in (data.get("teams") or []):
            t    = team.get("team", team)
            tid  = str(t.get("id", "")).strip()
            name = str(t.get("displayName", t.get("name", ""))).strip()
            if tid:
                teams.append((tid, name))
    return teams

def norm_team(s: str) -> str:
    if not s or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "", s.lower().strip())
    return s

def _fetch_roster(league: str, team_id: str, team_name: str = "") -> Dict[str, Tuple[str, str]]:
    url  = ESPN_ROSTER_URL.format(league=league, team_id=team_id)
    data = _safe_get_json(url)
    if not data:
        return {}
    result: Dict[str, Tuple[str, str]] = {}
    team_norm = norm_team(team_name)
    athletes = data.get("athletes") or []
    if athletes and isinstance(athletes[0], dict) and "items" in athletes[0]:
        flat = []
        for grp in athletes:
            flat.extend(grp.get("items", []))
        athletes = flat
    for a in athletes:
        if not isinstance(a, dict):
            continue
        aid  = str(a.get("id", "")).strip()
        name = str(a.get("displayName", a.get("fullName", a.get("name", "")))).strip()
        if aid.isdigit() and int(aid) > 100 and name:
            result[norm_name(name)] = (aid, team_norm)
    return result

ROSTER_CACHE_MAX_AGE_DAYS = 7  # Auto-rebuild roster cache if older than this

def build_roster_id_map(
    leagues: List[str],
    workers: int = 20,
    roster_cache_path: str = "",
    force_refresh: bool = False,
) -> Tuple[Dict[str, str], Dict[Tuple[str, str], str]]:
    if roster_cache_path and os.path.exists(roster_cache_path) and not force_refresh:
        try:
            cache_age_days = (time.time() - os.path.getmtime(roster_cache_path)) / 86400
            if cache_age_days > ROSTER_CACHE_MAX_AGE_DAYS:
                print(f"  ⚠️  Roster cache is {cache_age_days:.1f}d old (>{ROSTER_CACHE_MAX_AGE_DAYS}d) — rebuilding for freshness...")
            else:
                cdf = pd.read_csv(roster_cache_path, dtype=str).fillna("")
                if not cdf.empty and "player_norm" in cdf.columns and "espn_athlete_id" in cdf.columns:
                    cached = dict(zip(cdf["player_norm"], cdf["espn_athlete_id"]))
                    cached = {k: v for k, v in cached.items() if v.strip()}
                    print(f"  ✅ Loaded roster cache: {len(cached)} players from {roster_cache_path} (age: {cache_age_days:.1f}d)")
                    by_last_team: Dict[Tuple[str, str], str] = {}
                    if "team_norm" in cdf.columns:
                        for _, rr in cdf.iterrows():
                            pnorm = str(rr.get("player_norm", "")).strip()
                            aid = str(rr.get("espn_athlete_id", "")).strip()
                            tnorm = str(rr.get("team_norm", "")).strip()
                            if not pnorm or not aid:
                                continue
                            parts = pnorm.split()
                            if not parts:
                                continue
                            last = parts[-1]
                            if tnorm:
                                by_last_team[(last, tnorm)] = aid
                    return cached, by_last_team
        except Exception as e:
            print(f"  ⚠️ Could not load roster cache: {e}")

    print(f"  Building ESPN ID map via team rosters ({len(leagues)} leagues)...")
    all_teams: List[Tuple[str, str, str]] = []
    for league in leagues:
        teams = _fetch_team_ids(league)
        print(f"    {league}: {len(teams)} teams")
        for tid, tname in teams:
            all_teams.append((league, tid, tname))
        time.sleep(0.3)

    print(f"  Fetching rosters for {len(all_teams)} teams (workers={workers})...")
    combined: Dict[str, str] = {}
    by_last_team: Dict[Tuple[str, str], str] = {}

    def _fetch_one(args):
        league, team_id, team_name = args
        return _fetch_roster(league, team_id, team_name)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one, t): t for t in all_teams}
        done = 0
        for fut in as_completed(futures):
            done += 1
            try:
                roster = fut.result()
                for pnorm, (aid, tnorm) in roster.items():
                    combined[pnorm] = aid
                    parts = pnorm.split()
                    if parts and tnorm:
                        by_last_team[(parts[-1], tnorm)] = aid
            except Exception:
                pass
            if done % 25 == 0 or done == len(all_teams):
                print(f"    roster progress: {done}/{len(all_teams)} teams, players={len(combined)}")

    if roster_cache_path and combined:
        rows = []
        for k, v in combined.items():
            parts = k.split()
            tnorm = ""
            if parts:
                for (last, team_norm), aid in by_last_team.items():
                    if aid == v and last == parts[-1]:
                        tnorm = team_norm
                        break
            rows.append({"player_norm": k, "espn_athlete_id": v, "team_norm": tnorm})
        pd.DataFrame(rows) \
          .to_csv(roster_cache_path, index=False, encoding="utf-8-sig")
        print(f"  Saved roster cache → {roster_cache_path}")

    return combined, by_last_team

def build_espn_id_cache_concurrent(
    names: List[str],
    existing_cache: Dict[str, str],
    player_team_hints: Dict[str, str],
    manual_map: Dict[Tuple[str, str], str],
    workers: int = 20,
    roster_cache_path: str = "",
    force_refresh: bool = False,
) -> Dict[str, str]:
    cache = dict(existing_cache)
    need  = [n for n in dict.fromkeys(n for n in names if n and n.strip())
             if norm_name(n) not in cache]

    if not need and not force_refresh:
        print(f"  All {len(existing_cache)} players already in cache — skipping lookups.")
        return cache

    print(f"  Need to resolve {len(need)} players — using roster-based lookup...")
    roster_map, roster_last_team = build_roster_id_map(
        LEAGUE_SLUGS_FOR_ROSTER,
        workers=workers,
        roster_cache_path=roster_cache_path,
        force_refresh=force_refresh,
    )

    resolved = failed = 0
    for name in need:
        key = norm_name(name)
        if not key:
            continue
        team_hint = norm_team(player_team_hints.get(key, ""))
        aid = roster_map.get(key, "")

        # Fallback 1: manual persistent map (normalized name + team).
        if not aid and team_hint:
            aid = manual_map.get((key, team_hint), "")

        # Fallback 2: last-name-only match within same team.
        if not aid and team_hint:
            parts = key.split()
            if parts:
                aid = roster_last_team.get((parts[-1], team_hint), "")

        # Fallback 3: loose last-name + first-initial across all rosters.
        if not aid:
            parts = key.split()
            if len(parts) >= 2:
                last = parts[-1]
                first_init = parts[0][0] if parts[0] else ""
                for rname, rid in roster_map.items():
                    rparts = rname.split()
                    if rparts and rparts[-1] == last and rparts[0].startswith(first_init):
                        aid = rid
                        break

        cache[key] = aid
        if aid: resolved += 1
        else:   failed += 1

    print(f"  Resolved {resolved}/{len(need)} players ({failed} not found in rosters)")
    return cache

# ── Opponent derivation ──────────────────────────────────────────────────────

def build_opp_team_from_home_away(df: pd.DataFrame) -> pd.Series:
    team = df.get("team", pd.Series("", index=df.index)).astype(str).str.strip().str.upper()
    home = df.get("pp_home_team", pd.Series("", index=df.index)).astype(str).str.strip().str.upper()
    away = df.get("pp_away_team", pd.Series("", index=df.index)).astype(str).str.strip().str.upper()

    opp = pd.Series("", index=df.index)
    has_both = (home != "") & (away != "") & (team != "")
    opp = opp.where(~(has_both & (team == home)), away)
    opp = opp.where(~(has_both & (team == away)), home)
    return opp

def _game_team_pair_map(df: pd.DataFrame) -> Dict[str, Tuple[str, str]]:
    """
    Map pp_game_id -> (TEAM_A, TEAM_B) using the two unique teams appearing in that game.
    If a game doesn't have exactly 2 teams, it won't be mapped.
    """
    game_col = "pp_game_id"
    if game_col not in df.columns:
        return {}
    tmp = df[[game_col, "team"]].copy()
    tmp[game_col] = tmp[game_col].astype(str).str.strip()
    tmp["team"] = tmp["team"].astype(str).str.strip().str.upper()

    pairs: Dict[str, Tuple[str, str]] = {}
    for gid, g in tmp.groupby(game_col, dropna=False):
        gid = str(gid).strip()
        if not gid:
            continue
        teams = [t for t in g["team"].unique().tolist() if t]
        if len(teams) == 2:
            pairs[gid] = (teams[0], teams[1])
    return pairs

def build_opp_team_from_game_pairing(df: pd.DataFrame) -> pd.Series:
    """
    Fallback when pp_home_team/pp_away_team are blank.
    Uses pp_game_id to infer the opponent from the other team in the game.
    Handles combos by producing "OPP1/OPP2" when team_1/team_2 exist.
    """
    opp = pd.Series("", index=df.index)
    if "pp_game_id" not in df.columns or "team" not in df.columns:
        return opp

    pair_map = _game_team_pair_map(df)
    if not pair_map:
        return opp

    gid = df["pp_game_id"].astype(str).str.strip()
    team = df["team"].astype(str).str.strip().str.upper()

    def _opp_single(g, t):
        p = pair_map.get(g)
        if not p:
            return ""
        a, b = p
        if t == a: return b
        if t == b: return a
        return ""

    # singles first
    opp = gid.combine(team, _opp_single)

    # combos: if we have team_1/team_2, prefer "OPP1/OPP2"
    if "team_1" in df.columns and "team_2" in df.columns:
        t1 = df["team_1"].astype(str).str.strip().str.upper()
        t2 = df["team_2"].astype(str).str.strip().str.upper()

        o1 = gid.combine(t1, _opp_single)
        o2 = gid.combine(t2, _opp_single)

        combo_mask = df.get("is_combo_player", "0").astype(str).isin(["1", "True", "true"])
        opp_combo = (o1.fillna("") + "/" + o2.fillna("")).str.strip("/")
        opp = opp.where(~combo_mask, opp_combo)

    return opp

# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",          default="s1_soccer_props.csv")
    ap.add_argument("--output",         default="s2_soccer_picktypes.csv")
    ap.add_argument("--idcache",         default="soccer_espn_id_cache.csv")
    ap.add_argument("--rostercache",     default="soccer_roster_cache.csv")
    ap.add_argument("--skip_id_lookup",  action="store_true")
    ap.add_argument("--refresh-roster",  dest="refresh_roster", action="store_true",
                    help="Force rebuild roster cache even if within max-age window")
    ap.add_argument("--workers",         type=int, default=20)
    args = ap.parse_args()

    print(f"→ Loading Step1: {args.input}")
    df = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig").fillna("")

    if df.empty:
        print("❌ [PropOracle-Soccer-S2] Empty input from S1 — aborting.")
        sys.exit(1)

    required = ["player", "team", "prop_type", "line"]
    missing  = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"❌ Missing columns: {missing}")

    for c in ["pos", "opp_team", "pick_type", "start_time", "league",
              "pp_home_team", "pp_away_team", "pp_game_id"]:
        if c not in df.columns:
            df[c] = ""

    df["pick_type"]       = df["pick_type"].apply(norm_pick_type)
    df["prop_norm"]       = df["prop_type"].apply(norm_prop)
    df["is_combo_player"] = df["player"].apply(detect_combo).astype(int)

    for c in ["player_1", "player_2", "team_1", "team_2"]:
        if c not in df.columns:
            df[c] = ""

    combos_mask = df["is_combo_player"] == 1
    if combos_mask.any():
        p_splits = df.loc[combos_mask, "player"].apply(split_combo_player)
        t_splits = df.loc[combos_mask, "team"].apply(split_combo_team)
        df.loc[combos_mask, "player_1"] = [x[0] for x in p_splits]
        df.loc[combos_mask, "player_2"] = [x[1] for x in p_splits]
        df.loc[combos_mask, "team_1"]   = [x[0] for x in t_splits]
        df.loc[combos_mask, "team_2"]   = [x[1] for x in t_splits]

    # --- opp_team: try home/away first; fallback to game pairing ---
    opp1 = build_opp_team_from_home_away(df)
    df["opp_team"] = opp1

    need_fallback = df["opp_team"].astype(str).str.strip().eq("")
    if need_fallback.any():
        opp2 = build_opp_team_from_game_pairing(df)
        df.loc[need_fallback, "opp_team"] = opp2.loc[need_fallback]

    # sanity print
    filled = (df["opp_team"].astype(str).str.strip() != "").sum()
    print(f"  opp_team filled: {filled}/{len(df)}")

    # ── ESPN ID resolution ──
    df["espn_player_id"] = ""
    df["id_status"]      = "OK"

    if not args.skip_id_lookup:
        id_cache: Dict[str, str] = {}
        if os.path.exists(args.idcache):
            try:
                cdf = pd.read_csv(args.idcache, dtype=str).fillna("")
                id_cache = dict(zip(cdf["player_norm"].tolist(), cdf["espn_athlete_id"].tolist()))
                print(f"  Loaded ID cache: {len(id_cache)} entries from {args.idcache}")
            except Exception as e:
                print(f"  ⚠️ Could not load ID cache: {e}")

        singles_mask = df["is_combo_player"] == 0
        old_exact_rate = 0.0
        if singles_mask.any() and id_cache:
            _old_keys = df.loc[singles_mask, "player"].apply(norm_name)
            _old_ids = _old_keys.map(id_cache).fillna("")
            old_exact_rate = float((_old_ids.astype(str).str.strip() != "").mean())
        print(f"[SOCCER ID] Old exact/cache-only match rate: {old_exact_rate*100:.1f}%")

        # Team hints for player-level fallback matching.
        player_team_hints: Dict[str, str] = {}
        for _, rr in df.loc[singles_mask, ["player", "team"]].iterrows():
            pk = norm_name(rr.get("player", ""))
            if pk and pk not in player_team_hints:
                player_team_hints[pk] = str(rr.get("team", "") or "")
        if combos_mask.any():
            for _, rr in df.loc[combos_mask, ["player_1", "team_1", "player_2", "team_2"]].iterrows():
                pk1 = norm_name(rr.get("player_1", ""))
                pk2 = norm_name(rr.get("player_2", ""))
                if pk1 and pk1 not in player_team_hints:
                    player_team_hints[pk1] = str(rr.get("team_1", "") or "")
                if pk2 and pk2 not in player_team_hints:
                    player_team_hints[pk2] = str(rr.get("team_2", "") or "")

        # Persistent manual map for hand-curated PP->ESPN IDs.
        manual_map: Dict[Tuple[str, str], str] = {}
        manual_path = Path(args.output).resolve().parent / "pp_to_espn_id_map_soccer.csv"
        if not manual_path.exists():
            manual_path = Path(MANUAL_PP_MAP_PATH)
        if manual_path.exists():
            try:
                mdf = pd.read_csv(manual_path, dtype=str).fillna("")
                for _, rr in mdf.iterrows():
                    pname = norm_name(rr.get("player_name", rr.get("player", "")))
                    pteam = norm_team(rr.get("team", ""))
                    aid = str(rr.get("espn_athlete_id", rr.get("espn_player_id", ""))).strip()
                    if pname and pteam and aid:
                        manual_map[(pname, pteam)] = aid
                print(f"  Loaded manual PP->ESPN map: {len(manual_map)} rows from {manual_path}")
            except Exception as e:
                print(f"  ⚠️ Could not load manual map {manual_path}: {e}")

        all_names = (
            df.loc[singles_mask, "player"].tolist()
            + df.loc[combos_mask, "player_1"].tolist()
            + df.loc[combos_mask, "player_2"].tolist()
        )

        id_cache = build_espn_id_cache_concurrent(
            all_names, id_cache,
            player_team_hints=player_team_hints,
            manual_map=manual_map,
            workers=args.workers,
            roster_cache_path=args.rostercache,
            force_refresh=args.refresh_roster,
        )

        resolved_entries = [
            {"player_norm": k, "espn_athlete_id": v}
            for k, v in id_cache.items()
            if v and str(v).strip()
        ]
        pd.DataFrame(resolved_entries).to_csv(args.idcache, index=False, encoding="utf-8-sig")
        print(f"  Saved ID cache → {args.idcache}  ({len(resolved_entries)} resolved entries)")

        single_keys = df.loc[singles_mask, "player"].apply(norm_name)
        df.loc[singles_mask, "espn_player_id"] = single_keys.map(id_cache).fillna("")
        df.loc[singles_mask & (df["espn_player_id"] == ""), "id_status"] = "UNRESOLVED_SINGLE"

        if combos_mask.any():
            k1 = df.loc[combos_mask, "player_1"].apply(norm_name).map(id_cache).fillna("")
            k2 = df.loc[combos_mask, "player_2"].apply(norm_name).map(id_cache).fillna("")
            both_ok = (k1 != "") & (k2 != "")
            for idx in df.index[combos_mask][both_ok.values]:
                id1 = k1.loc[idx]; id2 = k2.loc[idx]
                ids = sorted([int(id1), int(id2)])
                df.at[idx, "espn_player_id"] = f"{ids[0]}{COMBO_SEP}{ids[1]}"
            df.loc[df.index[combos_mask][~both_ok.values], "id_status"] = "UNRESOLVED_COMBO"

        new_match_rate = float((df["espn_player_id"].astype(str).str.strip() != "").mean())
        print(f"[SOCCER ID] New match rate (after fallbacks): {new_match_rate*100:.1f}%")

        # Persist unresolved list for manual map review.
        unresolved = df[df["espn_player_id"].astype(str).str.strip() == ""].copy()
        if not unresolved.empty:
            if "start_time" in unresolved.columns and unresolved["start_time"].astype(str).str.strip().ne("").any():
                ds = pd.to_datetime(unresolved["start_time"], errors="coerce").min()
                date_tag = ds.strftime("%Y-%m-%d") if pd.notna(ds) else datetime.now().strftime("%Y-%m-%d")
            else:
                date_tag = datetime.now().strftime("%Y-%m-%d")
            unmatched_path = Path(args.output).resolve().parent / f"unmatched_soccer_players_{date_tag}.csv"
            keep_cols = [c for c in ["player", "team", "prop_type", "pp_game_id"] if c in unresolved.columns]
            unresolved[keep_cols].rename(columns={"player": "player_name"}).drop_duplicates().to_csv(
                unmatched_path, index=False, encoding="utf-8-sig"
            )
            print(f"[SOCCER ID] Wrote unresolved players: {unmatched_path}")
        else:
            print("[SOCCER ID] All players resolved after fallback matching.")
    else:
        print("  ⚠️ Skipping ESPN ID lookup (--skip_id_lookup)")
        df["id_status"] = "SKIPPED"

    # ── standard line + deviation level ──
    df["line_num"]    = pd.to_numeric(df["line"], errors="coerce")
    std_df            = df[(df["pick_type"] == "Standard") & df["line_num"].notna()]
    std_lookup        = std_df.groupby(["player", "prop_norm"])["line_num"].first().to_dict()
    df["standard_line"] = df.apply(
        lambda r: std_lookup.get((r["player"], r["prop_norm"]), None), axis=1
    )

    df["deviation_level"] = 0
    gob_dem = df[df["pick_type"].isin(["Goblin", "Demon"])]
    if len(gob_dem):
        def _rank_group(grp):
            pt    = grp.name[2] if isinstance(grp.name, tuple) else grp["pick_type"].iloc[0]
            asc   = (pt != "Goblin")
            lines = grp["line_num"].dropna().sort_values(ascending=asc)
            rmap  = {v: i + 1 for i, v in enumerate(lines.unique())}
            return grp["line_num"].map(rmap).fillna(0).astype(int)

        dev = gob_dem.groupby(["player", "prop_norm", "pick_type"], group_keys=False).apply(_rank_group)
        df.loc[dev.index, "deviation_level"] = dev.values

    df.drop(columns=["line_num"], inplace=True)

    # ── output ──
    pp_schema   = ["projection_id", "pp_projection_id", "player_id", "pp_game_id",
                   "start_time", "pp_home_team", "pp_away_team"]
    front       = ["espn_player_id"]
    model       = ["player", "pos", "team", "opp_team", "league", "line", "prop_type",
                   "prop_norm", "pick_type", "standard_line", "deviation_level"]
    tail        = ["is_combo_player"]
    pp_block    = [c for c in pp_schema if c in df.columns]
    model_block = [c for c in model     if c in df.columns]
    front_block = [c for c in front     if c in df.columns]
    tail_block  = [c for c in tail      if c in df.columns]
    middle      = [c for c in df.columns
                   if c not in set(front_block + pp_block + model_block + tail_block)]

    out = df[front_block + pp_block + model_block + middle + tail_block].copy()
    out.to_csv(args.output, index=False, encoding="utf-8-sig")

    if out.empty:
        print("❌ [PropOracle-Soccer-S2] Output is empty — aborting.")
        sys.exit(1)

    print(f"✅ Saved → {args.output}  rows={len(out)}")
    print(f"  id_status breakdown: {df['id_status'].value_counts().to_dict()}")
    print(f"  prop_norm breakdown: {df['prop_norm'].value_counts().head(10).to_dict()}")


if __name__ == "__main__":
    main()
