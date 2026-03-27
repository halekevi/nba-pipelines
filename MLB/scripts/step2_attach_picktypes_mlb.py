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
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

COMBO_SEP = "|"

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
}

PITCHER_PROPS = {
    "strikeouts", "pitching_outs", "innings_pitched",
    "hits_allowed", "earned_runs", "walks_allowed", "batters_faced",
}

HITTER_PROPS = {
    "hits", "total_bases", "home_runs", "rbi", "runs",
    "walks", "stolen_bases", "fantasy_score", "hits_runs_rbi",
    "singles", "doubles", "triples",
}

PICKTYPE_MAP = {"standard": "Standard", "goblin": "Goblin", "demon": "Demon"}


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


# ── MLB Stats API ID resolution ───────────────────────────────────────────────

def search_mlb_player(name: str, retries: int = 3) -> Optional[str]:
    """Search MLB Stats API for player by name. Returns mlb_player_id string."""
    encoded = name.strip().replace(" ", "%20")
    url     = MLB_SEARCH_URL.format(name=encoded)
    for attempt in range(1, retries + 1):
        try:
            time.sleep(0.3 + random.uniform(0, 0.2))
            r = requests.get(url, headers=MLB_HEADERS, timeout=15)
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
                time.sleep(2.0 * attempt)
    return None


def build_id_cache(names: List[str]) -> Dict[str, str]:
    cache: Dict[str, str] = {}
    unique = list(dict.fromkeys(n for n in names if n and n.strip()))
    print(f"  Resolving {len(unique)} unique players via MLB Stats API...")
    for i, name in enumerate(unique, 1):
        key = norm_name(name)
        if not key:
            continue
        mlb_id = search_mlb_player(name)
        if mlb_id:
            cache[key] = mlb_id
        if i % 20 == 0:
            print(f"    {i}/{len(unique)} resolved...")
    resolved = sum(1 for v in cache.values() if v)
    print(f"  Resolved {resolved}/{len(unique)} players")
    return cache


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",           default="step1_mlb_props.csv")
    ap.add_argument("--output",          default="step2_mlb_picktypes.csv")
    ap.add_argument("--idcache",         default="mlb_id_cache.csv")
    ap.add_argument("--skip_id_lookup",  action="store_true")
    args = ap.parse_args()

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
            new_ids  = build_id_cache(need_lookup)
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


if __name__ == "__main__":
    main()
