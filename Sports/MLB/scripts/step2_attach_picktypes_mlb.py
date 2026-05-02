#!/usr/bin/env python3
"""
step2_attach_picktypes_mlb.py  (MLB Pipeline)

Normalizes props and resolves MLB Stats API player IDs via
https://statsapi.mlb.com/api/v1/people/search?names={name}

Inputs:  step1_mlb_props.csv
Outputs: step2_mlb_picktypes.csv
         mlb_id_cache.csv  (persistent — don't delete)

Run:
  py -3.14 step2_attach_picktypes_mlb.py
  py -3.14 step2_attach_picktypes_mlb.py --input step1_mlb_props.csv --output step2_mlb_picktypes.csv
"""

from __future__ import annotations

import argparse
import os
import re
import time
import random
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from zoneinfo import ZoneInfo

COMBO_SEP = "|"
DEFAULT_TZ = "America/New_York"

MLB_SEARCH_URL = "https://statsapi.mlb.com/api/v1/people/search?names={name}&sportIds=1"
MLB_HEADERS    = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# ── MLB prop normalizer ───────────────────────────────────────────────────────
PROP_NORM_MAP = {
    # Hitter
    "hits":                    "hits",
    "total bases":             "total_bases",
    "totalbases":              "total_bases",
    "total bases (combo)":     "total_bases",
    "home runs":               "home_runs",
    "homeruns":                "home_runs",
    "rbi":                     "rbi",
    "runs":                    "runs",
    "walks":                   "walks",
    "stolen bases":            "stolen_bases",
    "stolenbases":             "stolen_bases",
    "hitter strikeouts":       "hitter_strikeouts",
    "hitterstrikeouts":        "hitter_strikeouts",
    "batter strikeouts":       "hitter_strikeouts",
    "batterstrikeouts":        "hitter_strikeouts",
    "fantasy score":           "fantasy_score",
    "fantasyscore":            "fantasy_score",
    "hits+runs+rbi":           "hits_runs_rbi",
    "hitsrunsrbi":             "hits_runs_rbi",
    "hits + runs + rbi":       "hits_runs_rbi",
    "singles":                 "singles",
    "doubles":                 "doubles",
    "triples":                 "triples",
    # Pitcher
    "strikeouts":              "strikeouts",
    "pitcher strikeouts":      "strikeouts",
    "pitcherstrikeouts":       "strikeouts",
    "pitching outs":           "pitching_outs",
    "pitchingouts":            "pitching_outs",
    "innings pitched":         "innings_pitched",
    "inningspitched":          "innings_pitched",
    "hits allowed":            "hits_allowed",
    "hitsallowed":             "hits_allowed",
    "earned runs":             "earned_runs",
    "earnedrun":               "earned_runs",
    "earnedrunsr":             "earned_runs",
    "walks allowed":           "walks_allowed",
    "walksallowed":            "walks_allowed",
    "batters faced":           "batters_faced",
    "battersfaced":            "batters_faced",
    "pitches thrown":          "pitches_thrown",
    "pitchesthrown":           "pitches_thrown",
}

PITCHER_PROPS = {
    "strikeouts", "pitching_outs", "innings_pitched",
    "hits_allowed", "earned_runs", "walks_allowed", "batters_faced",
    "pitches_thrown",
}

HITTER_PROPS = {
    "hits", "total_bases", "home_runs", "rbi", "runs",
    "walks", "stolen_bases", "fantasy_score", "hits_runs_rbi",
    "singles", "doubles", "triples",
    "hitter_strikeouts",
}

PICKTYPE_MAP = {"standard": "Standard", "goblin": "Goblin", "demon": "Demon"}
MLB_PP_TO_ESPN_TEAM = {
    "AZ": "ARI",
    "WSH": "WSN",
    "SD": "SDP",
    "SF": "SFG",
    "TB": "TBR",
    "KC": "KCR",
    "CWS": "CHW",
}
MLB_ESPN_TO_PP_TEAM = {v: k for k, v in MLB_PP_TO_ESPN_TEAM.items()}


def norm_name(s: str) -> str:
    if not s or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower().strip())


def norm_pick_type(s: str) -> str:
    t = str(s or "").strip().lower()
    if "gob" in t: return "Goblin"
    if "dem" in t: return "Demon"
    return "Standard"


def norm_prop(s: str) -> str:
    raw = str(s or "").lower().strip()
    flat = raw.replace("-", "").replace("_", "").replace(" ", "")
    for k, v in PROP_NORM_MAP.items():
        if flat == k.replace("-", "").replace("_", "").replace(" ", ""):
            return v
    return raw


def player_type(prop_norm: str) -> str:
    if prop_norm in PITCHER_PROPS:
        return "pitcher"
    if prop_norm in HITTER_PROPS:
        return "hitter"
    return "unknown"


def _backfill_opp_team(df: pd.DataFrame) -> pd.DataFrame:
    """
    Backfill opp_team from pp_game_id/team for rows where step1 couldn't map home/away.
    Supports combo teams (TEAM1/TEAM2 -> OPP1/OPP2).
    """
    if df is None or len(df) == 0:
        return df
    if "pp_game_id" not in df.columns or "team" not in df.columns:
        return df
    if "opp_team" not in df.columns:
        df["opp_team"] = ""

    out = df.copy()
    out["pp_game_id"] = out["pp_game_id"].astype(str).str.strip()
    out["team"] = out["team"].astype(str).str.strip().str.upper()
    out["opp_team"] = out["opp_team"].astype(str).str.strip().str.upper()

    out["team_single"] = out["team"].str.split("/").str[0].str.strip()
    valid = out[out["pp_game_id"].ne("") & out["team_single"].ne("")]
    teams_per_game = (
        valid.groupby("pp_game_id")["team_single"]
        .apply(lambda s: sorted({str(v).strip().upper() for v in s if str(v).strip()}))
        .reset_index()
    )
    teams_per_game.columns = ["pp_game_id", "_teams"]
    two_team = teams_per_game[teams_per_game["_teams"].apply(len) == 2].copy()
    two_team["_team_a"] = two_team["_teams"].apply(lambda t: t[0])
    two_team["_team_b"] = two_team["_teams"].apply(lambda t: t[1])
    out = out.merge(two_team[["pp_game_id", "_team_a", "_team_b"]], on="pp_game_id", how="left")

    needs = out["opp_team"].eq("")
    team_first = out["team"].str.split("/").str[0].str.strip()
    team_second = out["team"].str.split("/").str[1].fillna("").str.strip()
    is_combo = out["team"].str.contains("/", regex=False, na=False)

    out.loc[needs & ~is_combo & team_first.eq(out["_team_a"]), "opp_team"] = out["_team_b"]
    out.loc[needs & ~is_combo & team_first.eq(out["_team_b"]), "opp_team"] = out["_team_a"]

    combo_ok = needs & is_combo & team_first.ne("") & team_second.ne("")
    opp1 = out["_team_b"].where(team_first.eq(out["_team_a"]), out["_team_a"])
    opp2 = out["_team_b"].where(team_second.eq(out["_team_a"]), out["_team_a"])
    out.loc[combo_ok, "opp_team"] = (
        opp1.fillna("").astype(str).str.strip().str.upper()
        + "/"
        + opp2.fillna("").astype(str).str.strip().str.upper()
    ).str.strip("/")
    out.loc[combo_ok, "opp_team"] = out.loc[combo_ok, "opp_team"].replace("/", "")

    out = out.drop(columns=["team_single", "_team_a", "_team_b", "_teams"], errors="ignore")
    return out


def _pp_to_espn_team(team: str) -> str:
    t = str(team or "").strip().upper()
    return MLB_PP_TO_ESPN_TEAM.get(t, t)


def _espn_to_pp_team(team: str) -> str:
    t = str(team or "").strip().upper()
    return MLB_ESPN_TO_PP_TEAM.get(t, t)


def _derive_game_date_et(start_time: str, fallback_date: str) -> str:
    raw = str(start_time or "").strip()
    if not raw:
        return str(fallback_date or "").strip()
    try:
        ts = pd.to_datetime(raw, errors="coerce", utc=True)
        if pd.isna(ts):
            return str(fallback_date or "").strip()
        return ts.tz_convert(ZoneInfo(DEFAULT_TZ)).date().isoformat()
    except Exception:
        return str(fallback_date or "").strip()


def _fetch_espn_mlb_opponents_for_date(game_date: str, timeout_s: float = 10.0) -> Dict[Tuple[str, str], str]:
    """
    Returns mapping: (date, team_abbr_pp) -> opponent_abbr_pp
    """
    date_tag = str(game_date or "").replace("-", "")
    if not date_tag:
        return {}
    url = f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates={date_tag}"
    try:
        r = requests.get(url, timeout=timeout_s, headers=MLB_HEADERS)
        r.raise_for_status()
        payload = r.json() if r.content else {}
    except Exception:
        return {}

    out: Dict[Tuple[str, str], str] = {}
    events = payload.get("events") or []
    for ev in events:
        comps = (ev.get("competitions") or [])
        if not comps:
            continue
        comp = comps[0] if isinstance(comps[0], dict) else {}
        teams = comp.get("competitors") or []
        if len(teams) < 2:
            continue
        rows: list[str] = []
        for t in teams:
            team_obj = (t or {}).get("team") or {}
            abbr = _espn_to_pp_team(str(team_obj.get("abbreviation", "")).strip().upper())
            if abbr:
                rows.append(abbr)
        if len(rows) == 2:
            a, b = rows[0], rows[1]
            out[(game_date, a)] = b
            out[(game_date, b)] = a
    return out


def _backfill_opp_from_espn(df: pd.DataFrame, *, fallback_date: str = "") -> pd.DataFrame:
    """
    Fill remaining blank opp_team values using ESPN scoreboard by date/team.
    """
    if df is None or len(df) == 0:
        return df
    if "team" not in df.columns or "opp_team" not in df.columns:
        return df

    out = df.copy()
    out["team"] = out["team"].astype(str).str.strip().str.upper()
    out["opp_team"] = out["opp_team"].astype(str).str.strip().str.upper()
    if "start_time" not in out.columns:
        out["start_time"] = ""

    out["_game_date"] = out["start_time"].apply(lambda s: _derive_game_date_et(s, fallback_date))
    miss = out["opp_team"].eq("") & out["team"].ne("") & out["_game_date"].ne("")
    if not miss.any():
        out.drop(columns=["_game_date"], inplace=True, errors="ignore")
        return out

    date_maps: Dict[str, Dict[Tuple[str, str], str]] = {}
    for d in sorted(out.loc[miss, "_game_date"].dropna().astype(str).unique().tolist()):
        date_maps[d] = _fetch_espn_mlb_opponents_for_date(d)

    def _lookup(row) -> str:
        if str(row.get("opp_team", "")).strip():
            return str(row.get("opp_team", "")).strip().upper()
        d = str(row.get("_game_date", "")).strip()
        t = _pp_to_espn_team(str(row.get("team", "")).strip().upper())
        opp = (date_maps.get(d) or {}).get((d, t), "")
        return _espn_to_pp_team(opp)

    out.loc[miss, "opp_team"] = out.loc[miss].apply(_lookup, axis=1)
    out.drop(columns=["_game_date"], inplace=True, errors="ignore")
    return out


# ── MLB Stats API ID resolution ───────────────────────────────────────────────

def search_mlb_player(
    name: str,
    *,
    retries: int = 2,
    request_timeout_s: float = 6.0,
) -> Optional[str]:
    """Search MLB Stats API for player by name. Returns mlb_player_id string."""
    encoded = name.strip().replace(" ", "%20")
    url     = MLB_SEARCH_URL.format(name=encoded)
    for attempt in range(1, retries + 1):
        try:
            time.sleep(0.3 + random.uniform(0, 0.2))
            r = requests.get(url, headers=MLB_HEADERS, timeout=request_timeout_s)
            r.raise_for_status()
            j = r.json()
            people = j.get("people") or []
            if people:
                # Return the first active player match
                for p in people:
                    if p.get("active", True):
                        return str(p.get("id", "")).strip()
                # fallback: first result regardless
                return str(people[0].get("id", "")).strip()
        except Exception:
            if attempt < retries:
                # Keep retries short so one flaky lookup doesn't stall the full pipeline.
                time.sleep(0.75 * attempt)
    return None


def load_mlb_name_aliases(path: Path) -> Dict[str, str]:
    """Map pp_name_norm -> mlb_canonical_name for MLB Stats API search."""
    if not path.is_file():
        return {}
    try:
        adf = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception:
        return {}
    c0 = "pp_name_norm" if "pp_name_norm" in adf.columns else ""
    c1 = "mlb_canonical_name" if "mlb_canonical_name" in adf.columns else ""
    if not c0 or not c1:
        return {}
    out: Dict[str, str] = {}
    for _, r in adf.iterrows():
        k = norm_name(str(r.get(c0, "")))
        v = str(r.get(c1, "")).strip()
        if k and v:
            out[k] = v
    return out


def build_id_cache(
    names: List[str],
    *,
    name_aliases: Optional[Dict[str, str]] = None,
    retries: int = 2,
    request_timeout_s: float = 6.0,
    lookup_budget_seconds: float = 180.0,
) -> Dict[str, str]:
    cache: Dict[str, str] = {}
    aliases = name_aliases or {}
    unique = list(dict.fromkeys(n for n in names if n and n.strip()))
    print(f"  Resolving {len(unique)} unique players via MLB Stats API...")
    t0 = time.time()
    skipped_budget = 0
    for i, name in enumerate(unique, 1):
        if lookup_budget_seconds > 0 and (time.time() - t0) > lookup_budget_seconds:
            skipped_budget += 1
            continue
        key = norm_name(name)
        if not key:
            continue
        canonical = aliases.get(key)
        api_name = canonical if canonical else name
        if canonical:
            print(f"[MLB step2] alias hit: {name} → {canonical}")
        mlb_id = search_mlb_player(
            api_name,
            retries=retries,
            request_timeout_s=request_timeout_s,
        )
        if mlb_id:
            cache[key] = mlb_id
        if i % 20 == 0:
            print(f"    {i}/{len(unique)} resolved...")
    resolved = sum(1 for v in cache.values() if v)
    print(f"  Resolved {resolved}/{len(unique)} players")
    if skipped_budget:
        print(f"  [MLB step2] lookup budget reached; skipped {skipped_budget} remaining player lookup(s)")
    return cache


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",           default="step1_mlb_props.csv")
    ap.add_argument("--output",          default="step2_mlb_picktypes.csv")
    ap.add_argument("--idcache",         default="mlb_id_cache.csv")
    ap.add_argument("--skip_id_lookup",  action="store_true")
    ap.add_argument("--id_lookup_retries", type=int, default=2)
    ap.add_argument("--id_lookup_timeout_s", type=float, default=6.0)
    ap.add_argument(
        "--id_lookup_budget_s",
        type=float,
        default=180.0,
        help="Max total seconds to spend resolving missing MLB IDs (prevents hangs).",
    )
    ap.add_argument(
        "--name-aliases",
        default="",
        help="CSV with pp_name_norm,mlb_canonical_name (default: MLB/data/mlb_name_aliases.csv if present)",
    )
    ap.add_argument(
        "--no-espn-opp-fallback",
        action="store_true",
        help="Disable ESPN scoreboard fallback for missing opp_team values.",
    )
    args = ap.parse_args()

    _script_dir = Path(__file__).resolve().parent
    _default_alias = _script_dir.parent / "data" / "mlb_name_aliases.csv"
    alias_path = Path(args.name_aliases).expanduser() if str(args.name_aliases).strip() else _default_alias
    name_aliases = load_mlb_name_aliases(alias_path)
    if name_aliases:
        print(f"  Loaded {len(name_aliases)} name alias(es) from {alias_path}")

    print(f"→ Loading Step1: {args.input}")
    df = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig").fillna("")

    required = ["player", "team", "prop_type", "line"]
    missing  = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"❌ Missing columns: {missing}")

    for c in ["pos", "opp_team", "pick_type", "start_time",
              "pp_home_team", "pp_away_team", "pp_game_id"]:
        if c not in df.columns:
            df[c] = ""

    # Repair opponent mapping from game/team context before downstream joins.
    df = _backfill_opp_team(df)
    if not args.no_espn_opp_fallback:
        before_missing = int((df["opp_team"].astype(str).str.strip() == "").sum())
        if before_missing > 0:
            fallback_date = _derive_game_date_et(df["start_time"].iloc[0] if len(df) else "", "")
            df = _backfill_opp_from_espn(df, fallback_date=fallback_date)
            after_missing = int((df["opp_team"].astype(str).str.strip() == "").sum())
            if after_missing < before_missing:
                print(f"[MLB step2] ESPN opp fallback filled {before_missing - after_missing} row(s)")

    df["pick_type"] = df["pick_type"].apply(norm_pick_type)
    df["prop_norm"] = df["prop_type"].apply(norm_prop)
    df["player_type"] = df["prop_norm"].apply(player_type)

    # Detect combos (player names joined with +)
    df["is_combo_player"] = df["player"].apply(
        lambda x: 1 if "+" in str(x or "") else 0
    ).astype(int)

    for c in ["player_1", "player_2", "team_1", "team_2"]:
        if c not in df.columns:
            df[c] = ""

    combos_mask = df["is_combo_player"] == 1
    for idx, row in df.loc[combos_mask, ["player", "team"]].iterrows():
        parts  = [p.strip() for p in str(row["player"]).split("+")]
        tparts = [t.strip() for t in str(row["team"]).split("/")]
        df.at[idx, "player_1"] = parts[0]  if len(parts) > 0 else ""
        df.at[idx, "player_2"] = parts[1]  if len(parts) > 1 else ""
        df.at[idx, "team_1"]   = tparts[0] if len(tparts) > 0 else ""
        df.at[idx, "team_2"]   = tparts[1] if len(tparts) > 1 else ""

    # ── MLB ID resolution ──
    df["mlb_player_id"] = ""
    df["id_status"]     = "OK"

    if not args.skip_id_lookup:
        id_cache: Dict[str, str] = {}
        if os.path.exists(args.idcache):
            try:
                cdf      = pd.read_csv(args.idcache, dtype=str).fillna("")
                id_cache = dict(zip(cdf["player_norm"].tolist(), cdf["mlb_player_id"].tolist()))
                print(f"  Loaded ID cache: {len(id_cache)} entries from {args.idcache}")
            except Exception as e:
                print(f"  ⚠️ Could not load ID cache: {e}")

        singles_mask = df["is_combo_player"] == 0
        all_names    = df.loc[singles_mask, "player"].tolist()
        all_names   += df.loc[combos_mask,  "player_1"].tolist()
        all_names   += df.loc[combos_mask,  "player_2"].tolist()
        need_lookup  = [n for n in set(all_names) if n and norm_name(n) not in id_cache]

        if need_lookup:
            new_ids  = build_id_cache(
                need_lookup,
                name_aliases=name_aliases,
                retries=max(1, int(args.id_lookup_retries)),
                request_timeout_s=max(1.0, float(args.id_lookup_timeout_s)),
                lookup_budget_seconds=max(0.0, float(args.id_lookup_budget_s)),
            )
            id_cache.update(new_ids)
            cache_df = pd.DataFrame([
                {"player_norm": k, "mlb_player_id": v}
                for k, v in id_cache.items() if v
            ])
            cache_df.to_csv(args.idcache, index=False, encoding="utf-8-sig")
            print(f"  Saved ID cache → {args.idcache}")

        # Singles
        for idx, row in df.loc[singles_mask, ["player"]].iterrows():
            key = norm_name(row["player"])
            aid = id_cache.get(key, "")
            if aid:
                df.at[idx, "mlb_player_id"] = aid
            else:
                df.at[idx, "id_status"] = "UNRESOLVED"

        # Combos
        for idx, row in df.loc[combos_mask, ["player_1", "player_2"]].iterrows():
            id1 = id_cache.get(norm_name(row["player_1"]), "")
            id2 = id_cache.get(norm_name(row["player_2"]), "")
            if id1 and id2:
                ids = sorted([int(id1), int(id2)])
                df.at[idx, "mlb_player_id"] = f"{ids[0]}{COMBO_SEP}{ids[1]}"
            else:
                df.at[idx, "id_status"] = "UNRESOLVED_COMBO"
    else:
        print("  ⚠️ Skipping MLB ID lookup (--skip_id_lookup)")
        df["id_status"] = "SKIPPED"

    # ── Deviation level ──
    df["line_num"]      = pd.to_numeric(df["line"], errors="coerce")
    std_df              = df[(df["pick_type"] == "Standard") & df["line_num"].notna()]
    std_lookup          = std_df.groupby(["player", "prop_norm"])["line_num"].first().to_dict()
    df["standard_line"] = df.apply(
        lambda r: std_lookup.get((r["player"], r["prop_norm"]), None), axis=1
    )

    rank_lookup: dict = {}
    for (player, prop_norm, pick_type), grp in df[
        df["pick_type"].isin(["Goblin", "Demon"])
    ].groupby(["player", "prop_norm", "pick_type"]):
        lines_sorted = sorted(
            grp["line_num"].dropna().unique(),
            reverse=(pick_type == "Goblin"),
        )
        for rank, line_val in enumerate(lines_sorted, start=1):
            rank_lookup[(player, prop_norm, pick_type, line_val)] = rank

    def get_deviation_level(row):
        if row["pick_type"] == "Standard":
            return 0
        if pd.isna(row["line_num"]):
            return 0
        return rank_lookup.get(
            (row["player"], row["prop_norm"], row["pick_type"], row["line_num"]), 0
        )

    df["deviation_level"] = df.apply(get_deviation_level, axis=1)
    df.drop(columns=["line_num"], inplace=True)

    # ── Output ──
    front   = ["mlb_player_id"]
    pp_cols = ["projection_id", "pp_projection_id", "player_id", "pp_game_id",
               "start_time", "pp_home_team", "pp_away_team"]
    model   = ["player", "pos", "player_type", "team", "opp_team", "line",
               "prop_type", "prop_norm", "pick_type", "standard_line", "deviation_level"]
    tail    = ["is_combo_player"]

    front  = [c for c in front   if c in df.columns]
    pp     = [c for c in pp_cols if c in df.columns]
    model  = [c for c in model   if c in df.columns]
    tail   = [c for c in tail    if c in df.columns]
    rest   = [c for c in df.columns if c not in set(front + pp + model + tail)]
    out    = df[front + pp + model + rest + tail].copy()

    out.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"\n✅ Saved → {args.output}  rows={len(out)}")
    print(f"  id_status:   {df['id_status'].value_counts().to_dict()}")
    print(f"  player_type: {df['player_type'].value_counts().to_dict()}")
    print(f"  prop_norm:   {df['prop_norm'].value_counts().head(15).to_dict()}")

    ok_ct = int((df["id_status"].astype(str) == "OK").sum())
    unr_ct = int(df["id_status"].astype(str).isin(["UNRESOLVED", "UNRESOLVED_COMBO"]).sum())
    print(f"[MLB step2] id_attach: OK={ok_ct} | UNRESOLVED={unr_ct} | total={len(df)}")


if __name__ == "__main__":
    main()
