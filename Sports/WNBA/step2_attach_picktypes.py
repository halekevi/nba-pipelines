#!/usr/bin/env python3
"""
step2_attach_picktypes.py  (WNBA Pipeline)

Normalizes pick types, prop names, builds opp_team from pp_game_id,
and adds standard_line / deviation_level.

Key difference from NBA version: no nba_api dependency.
WNBA player IDs come from the PP API player_id field directly.

Run:
  py -3.14 step2_attach_picktypes.py --input step1_wnba_props.csv --output step2_wnba_picktypes.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from utils.wnba_team_keys import canonical_team_key


# ── normalizers ──────────────────────────────────────────────────────────────

def norm_pick_type(s: str) -> str:
    if s is None or str(s).strip() == "":
        return "Standard"
    t = str(s).strip().lower()
    if "gob" in t: return "Goblin"
    if "dem" in t: return "Demon"
    if t in {"standard", "classic", "normal"}: return "Standard"
    return str(s).strip().title()


def norm_prop(s: str) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    raw   = str(s).lower()
    clean = raw.replace(" ", "").replace("-", "").replace("_", "")
    exact_map = {
        "points":"pts","rebounds":"reb","assists":"ast","blocks":"blk",
        "blockedshots":"blk","steals":"stl","turnovers":"tov",
        "blks+stls":"stocks","fantasyscore":"fantasy",
        "pts+rebs+asts":"pra","points+rebounds+assists":"pra",
        "pts+rebs":"pr","points+rebounds":"pr",
        "pts+asts":"pa","points+assists":"pa",
        "rebs+asts":"ra","rebounds+assists":"ra",
        "fgm":"fgm","fgmade":"fgm","fga":"fga","fgattempted":"fga",
        "fieldgoalsmade":"fgm","fieldgoalsattempted":"fga",
        "3ptfgmade":"fg3m","3ptfgattempted":"fg3a","fg3m":"fg3m","fg3a":"fg3a",
        "3ptmade":"fg3m","3ptattempted":"fg3a",
        "threepointersmade":"fg3m","threepointersattempted":"fg3a",
        "3pointersmade":"fg3m","3pointersattempted":"fg3a",
        "2ptfgmade":"fg2m","2ptfgattempted":"fg2a","fg2m":"fg2m","fg2a":"fg2a",
        "2ptmade":"fg2m","2ptattempted":"fg2a",
        "twopointersmade":"fg2m","twopointersattempted":"fg2a",
        "ftm":"ftm","ftmade":"ftm","fta":"fta","ftattempted":"fta",
        "freethrowsmade":"ftm","freethrowsattempted":"fta",
    }
    return exact_map.get(clean, clean)


def detect_combo(player_str: str) -> int:
    return 1 if "+" in str(player_str or "") else 0


def split_combo_player(s: str) -> Tuple[str, str]:
    parts = [p.strip() for p in str(s or "").split("+")]
    return (parts[0], parts[1]) if len(parts) >= 2 else (str(s).strip(), "")


def split_combo_team(s: str) -> Tuple[str, str]:
    parts = [p.strip() for p in str(s or "").split("/")]
    return (parts[0], parts[1]) if len(parts) >= 2 else (str(s).strip(), "")


def format_combo_team_display(t1: str, t2: str) -> str:
    parts = [p.strip() for p in (t1, t2) if str(p or "").strip()]
    if len(parts) >= 2:
        if parts[0] == parts[1]:
            return parts[0]
        return f"{parts[0]} / {parts[1]}"
    return parts[0] if parts else ""


def build_player_team_lookup(df: pd.DataFrame) -> Dict[str, str]:
    """Map player name -> team abbrev from single-player rows on today's board."""
    work = df.copy()
    work["is_combo_player"] = pd.to_numeric(
        work.get("is_combo_player", 0), errors="coerce"
    ).fillna(0).astype(int)
    singles = work[work["is_combo_player"] == 0]
    out: Dict[str, str] = {}
    for player, grp in singles.groupby("player", dropna=False):
        teams = grp["team"].astype(str).str.strip()
        teams = teams[(teams != "") & (~teams.str.contains("/", regex=False))]
        if len(teams):
            out[str(player).strip()] = str(teams.mode().iloc[0]).strip()
    return out


def build_opp_team(df: pd.DataFrame) -> pd.Series:
    df2 = df.copy()
    df2["pp_game_id"]      = df2["pp_game_id"].astype(str).fillna("")
    df2["team"]            = df2["team"].astype(str).fillna("")
    df2["is_combo_player"] = pd.to_numeric(df2.get("is_combo_player", 0), errors="coerce").fillna(0).astype(int)
    opp_map: Dict[tuple, str] = {}
    for gid, g in df2.groupby("pp_game_id", dropna=False):
        gid = str(gid)
        if not gid or gid.lower() == "nan":
            continue
        singles = g[(g["is_combo_player"] == 0) & (~g["team"].str.contains("/")) & (g["team"].str.strip() != "")]
        canon_to_raw: dict[str, str] = {}
        for raw in singles["team"].dropna().astype(str):
            raw = raw.strip()
            if not raw:
                continue
            canon_to_raw.setdefault(canonical_team_key(raw), raw)
        canon_keys = list(canon_to_raw.keys())
        if len(canon_keys) == 2:
            a, b = canon_keys
            opp_map[(gid, canon_to_raw[a])] = canon_to_raw[b]
            opp_map[(gid, canon_to_raw[b])] = canon_to_raw[a]
    out = []
    for _, row in df2.iterrows():
        gid  = str(row["pp_game_id"])
        team = str(row["team"]).strip()
        if int(row["is_combo_player"]) == 1 or "/" in team or not team:
            out.append("")
        else:
            out.append(opp_map.get((gid, team), ""))
    return pd.Series(out, index=df2.index)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  default="step1_wnba_props.csv")
    ap.add_argument("--output", default="step2_wnba_picktypes.csv")
    args = ap.parse_args()

    print(f"→ Loading: {args.input}")
    df = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig").fillna("")

    required = ["pp_projection_id","pp_game_id","player","team","prop_type","line"]
    missing  = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"❌ Missing required columns: {missing}")

    for c in ["pos","opp_team","pick_type","start_time"]:
        if c not in df.columns:
            df[c] = ""

    df["pick_type"]       = df["pick_type"].apply(norm_pick_type)
    df["prop_norm"]       = df["prop_type"].apply(norm_prop)
    df["is_combo_player"] = df["player"].apply(detect_combo).astype(int)

    # Use PP player_id directly — no nba_api needed for WNBA
    df["wnba_player_id"] = df.get("player_id", pd.Series([""] * len(df), index=df.index)).astype(str).str.strip()
    df["id_status"] = df["wnba_player_id"].apply(
        lambda x: "OK" if x and x not in ("", "nan") else "NO_PP_ID"
    )

    for c in ["player_1","player_2","team_1","team_2"]:
        if c not in df.columns:
            df[c] = ""

    combos = df["is_combo_player"] == 1
    player_teams = build_player_team_lookup(df) if combos.any() else {}
    if combos.any():
        for idx, row in df.loc[combos, ["player","team"]].iterrows():
            p1, p2 = split_combo_player(row["player"])
            t1, t2 = split_combo_team(row["team"])
            if not t1 or ("/" not in str(row["team"]) and t1 == str(row["team"]).strip()):
                t1 = player_teams.get(p1, t1)
            if not t2:
                t2 = player_teams.get(p2, t2)
            df.at[idx, "player_1"] = p1
            df.at[idx, "player_2"] = p2
            df.at[idx, "team_1"]   = t1
            df.at[idx, "team_2"]   = t2
            display_team = format_combo_team_display(t1, t2)
            if display_team:
                df.at[idx, "team"] = display_team

    df["opp_team"] = build_opp_team(df)

    df["line_num"]    = pd.to_numeric(df["line"], errors="coerce")
    std_df            = df[(df["pick_type"] == "Standard") & df["line_num"].notna()]
    std_lookup        = std_df.groupby(["player","prop_norm"])["line_num"].first().to_dict()
    df["standard_line"] = df.apply(lambda r: std_lookup.get((r["player"], r["prop_norm"])), axis=1)

    rank_lookup: dict = {}
    for (player, prop_norm, pick_type), grp in df[df["pick_type"].isin(["Goblin","Demon"])].groupby(["player","prop_norm","pick_type"]):
        lines = sorted(grp["line_num"].dropna().unique(), reverse=(pick_type == "Goblin"))
        for rank, val in enumerate(lines, start=1):
            rank_lookup[(player, prop_norm, pick_type, val)] = rank

    df["deviation_level"] = df.apply(
        lambda r: 0 if r["pick_type"] == "Standard" or pd.isna(r["line_num"])
        else rank_lookup.get((r["player"], r["prop_norm"], r["pick_type"], r["line_num"]), 0),
        axis=1
    )
    df.drop(columns=["line_num"], inplace=True)

    front  = ["wnba_player_id"]
    pp_blk = [c for c in ["projection_id","pp_projection_id","player_id","pp_game_id",
                           "start_time","pp_home_team","pp_away_team"] if c in df.columns]
    model  = [c for c in ["player","pos","team","opp_team","line","prop_type","prop_norm",
                           "pick_type","standard_line","deviation_level"] if c in df.columns]
    tail   = ["is_combo_player"]
    middle = [c for c in df.columns if c not in set(front+pp_blk+model+tail)]

    out = df[front + pp_blk + model + middle + tail].copy()
    out.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"✅ Saved → {args.output}  rows={len(out)}")
    print(f"id_status: {df['id_status'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()
