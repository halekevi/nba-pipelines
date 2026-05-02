#!/usr/bin/env python3
"""
step2_attach_picktypes.py (FINAL - PP schema + robust opp_team)

Step2 depends ONLY on Step1:
- Keeps ALL Step1 columns intact
- Adds nba_player_id:
    Singles: "<nba_id>"
    Combos : "<nba_id1>|<nba_id2>" (sorted asc)
- Adds combo helper columns:
    player_1, player_2, team_1, team_2
- Adds opp_team for singles using pp_game_id inference:
    * Primary: build mapping from singles only (ignore team strings like CHA/HOU)
    * Fallback: if only one single team exists in game, infer opponent from combo pairs
- Normalizes pick_type and prop_norm
- Adds id_status
- Adds standard_line + deviation_level
- OUTPUT ORDER:
    nba_player_id,
    then PP schema cols: projection_id, pp_projection_id, player_id, pp_game_id, start_time, pp_home_team, pp_away_team,
    then model cols, then all remaining Step1 columns, then is_combo_player at end.

Run:
  py -3.14 step2_attach_picktypes.py --input step1_fetch_prizepicks_api.csv --output step2_attach_picktypes.csv
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from nba_api.stats.static import players

COMBO_SEP = "|"

# ---------------- NORMALIZERS ---------------- #

def norm_name_strict(s: str) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def norm_name_loose(s: str) -> str:
    x = norm_name_strict(s)
    x = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", x)
    x = re.sub(r"\s+", " ", x).strip()
    return x


def norm_pick_type(s: str) -> str:
    if s is None or str(s).strip() == "":
        return "Standard"
    t = str(s).strip().lower()
    if "gob" in t:
        return "Goblin"
    if "dem" in t:
        return "Demon"
    if t in {"standard", "classic", "normal"}:
        return "Standard"
    return str(s).strip().title()


def norm_prop(s: str) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    raw = str(s).lower()
    clean = raw.replace(" ", "").replace("-", "").replace("_", "")

    exact_map = {
        "points": "pts",
        "rebounds": "reb",
        "assists": "ast",
        "blocks": "blk",
        "blockedshots": "blk",
        "steals": "stl",
        "turnovers": "tov",
        "blks+stls": "stocks",
        "fantasyscore": "fantasy",
        "pts+rebs+asts": "pra",
        "points+rebounds+assists": "pra",
        "pts+rebs": "pr",
        "points+rebounds": "pr",
        "pts+asts": "pa",
        "points+assists": "pa",
        "rebs+asts": "ra",
        "rebounds+assists": "ra",
        "fgm": "fgm",
        "fgmade": "fgm",
        "fga": "fga",
        "fgattempted": "fga",
        "3ptfgattempted": "fg3a",
        "3ptfgmade": "fg3m",
        "fg3a": "fg3a",
        "fg3m": "fg3m",
        "2ptfgattempted": "fg2a",
        "2ptfgmade": "fg2m",
        "fg2a": "fg2a",
        "fg2m": "fg2m",
        "fta": "fta",
        "ftattempted": "fta",
        "ftm": "ftm",
        "ftmade": "ftm",
    }
    return exact_map.get(clean, clean)


def detect_combo_player(player_str: str) -> int:
    if player_str is None or (isinstance(player_str, float) and pd.isna(player_str)):
        return 0
    return 1 if "+" in str(player_str) else 0


def split_combo_player(player_str: str) -> Tuple[str, str]:
    s = str(player_str or "")
    parts = [p.strip() for p in s.split("+")]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return s.strip(), ""


def split_combo_team(team_str: str) -> Tuple[str, str]:
    s = str(team_str or "")
    parts = [p.strip() for p in s.split("/")]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return s.strip(), ""


# ---------------- OPP TEAM (pp_game_id inference) ---------------- #

def build_opp_team_from_gameid(df: pd.DataFrame) -> pd.Series:
    """
    Build opponent using pp_game_id + team values.
    Robust to combo rows like CHA/HOU by:
      - building map from singles only (no '/')
      - fallback: if only 1 single team exists, infer opponent from combo pairs
      - ignore opponent assignment for combos
    """
    df2 = df.copy()
    df2["pp_game_id"] = df2["pp_game_id"].astype(str).fillna("")
    df2["team"] = df2["team"].astype(str).fillna("")
    df2["is_combo_player"] = pd.to_numeric(df2.get("is_combo_player", 0), errors="coerce").fillna(0).astype(int)

    opp_map: Dict[tuple, str] = {}

    for gid, g in df2.groupby("pp_game_id", dropna=False):
        gid = str(gid)
        if not gid or gid.lower() == "nan":
            continue

        # Primary: singles teams only (exclude combo team strings like CHA/HOU)
        singles = g[(g["is_combo_player"] == 0) & (g["team"].str.strip() != "") & (~g["team"].str.contains("/"))]
        single_teams = list(singles["team"].dropna().unique())

        # Fallback: combo pairs (CHA/HOU -> ("CHA","HOU"))
        combos = g[(g["is_combo_player"] == 1) & (g["team"].str.contains("/"))]
        combo_pairs = []
        for t in combos["team"].dropna().unique():
            parts = [p.strip() for p in str(t).split("/") if p.strip()]
            if len(parts) >= 2:
                combo_pairs.append((parts[0], parts[1]))

        # Case A: clean 2-team game from singles
        if len(single_teams) == 2:
            t1, t2 = single_teams
            opp_map[(gid, t1)] = t2
            opp_map[(gid, t2)] = t1
            continue

        # Case B: only 1 single team seen -> infer from combos containing that team
        if len(single_teams) == 1 and combo_pairs:
            t = single_teams[0]
            candidates = set()
            for a, b in combo_pairs:
                if a == t:
                    candidates.add(b)
                elif b == t:
                    candidates.add(a)
            if len(candidates) == 1:
                other = next(iter(candidates))
                opp_map[(gid, t)] = other

        # Case C: >2 single teams (rare/messy) -> take top 2 most frequent singles
        if len(single_teams) > 2:
            top2 = singles["team"].value_counts().head(2).index.tolist()
            if len(top2) == 2:
                t1, t2 = top2
                opp_map[(gid, t1)] = t2
                opp_map[(gid, t2)] = t1

    # Apply map row-by-row
    out = []
    for _, row in df2.iterrows():
        gid = str(row["pp_game_id"])
        team = str(row["team"]).strip()

        # do not assign opp_team for combo rows or combo team strings
        if int(row["is_combo_player"]) == 1 or "/" in team or team == "":
            out.append("")
            continue

        out.append(opp_map.get((gid, team), ""))

    return pd.Series(out, index=df2.index)


# ---------------- NBA DIRECTORY + RESOLUTION ---------------- #

def build_nba_directory() -> pd.DataFrame:
    nba_players = players.get_players()
    pldf = pd.DataFrame(nba_players)
    if "full_name" not in pldf.columns or "id" not in pldf.columns:
        raise RuntimeError("❌ nba_api players directory missing full_name/id")

    pldf["norm_strict"] = pldf["full_name"].apply(norm_name_strict)
    pldf["norm_loose"] = pldf["full_name"].apply(norm_name_loose)
    if "is_active" not in pldf.columns:
        pldf["is_active"] = False

    return pldf[["id", "full_name", "is_active", "norm_strict", "norm_loose"]].copy()


def resolve_nba_id_by_name(pldf: pd.DataFrame, name: str) -> Tuple[Optional[int], Optional[str], str]:
    strict = norm_name_strict(name)
    loose = norm_name_loose(name)

    if strict:
        hit = pldf.loc[pldf["norm_strict"] == strict]
        if len(hit) == 1:
            r = hit.iloc[0]
            return int(r["id"]), str(r["full_name"]), "name_strict"

    if loose:
        hit = pldf.loc[pldf["norm_loose"] == loose]
        if len(hit) == 1:
            r = hit.iloc[0]
            return int(r["id"]), str(r["full_name"]), "name_loose"

        if len(hit) > 1:
            active = hit[hit["is_active"] == True]
            if len(active) == 1:
                r = active.iloc[0]
                return int(r["id"]), str(r["full_name"]), "name_loose_active_tiebreak"

    return None, None, "unresolved"


# ---------------- MAIN ---------------- #

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="step1_fetch_prizepicks_api.csv")
    ap.add_argument("--output", default="step2_attach_picktypes.csv")
    args = ap.parse_args()

    print(f"→ Loading Step1: {args.input}")
    df = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig").fillna("")

    # Required for PP schema
    required = ["pp_projection_id", "pp_game_id", "player", "team", "prop_type", "line"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"❌ Step1 missing required columns: {missing}")

    # Optional columns we want to exist
    for c in ["pos", "opp_team", "pick_type", "start_time"]:
        if c not in df.columns:
            df[c] = ""

    # Normalize pick type + prop norm (vectorized)
    df["pick_type"] = df["pick_type"].astype(str).apply(norm_pick_type)
    df["prop_norm"] = df["prop_type"].astype(str).apply(norm_prop)

    # ── Drop prop types with no stat backing or that are ungradeable ──
    EXCLUDE_PROPS = {
        "Points - 1st 3 Minutes",   # 1st-quarter prop, drop per design
        "Dunks",                     # not tracked in ESPN API
        "3-PT Made (Combo)",         # combo prop, ungradeable per-player
    }
    before = len(df)
    df = df[~df["prop_type"].isin(EXCLUDE_PROPS)].reset_index(drop=True)
    dropped = before - len(df)
    if dropped:
        print(f"  🗑️  Dropped {dropped} rows with excluded prop types")

    # Combo marker (vectorized)
    df["is_combo_player"] = df["player"].astype(str).str.contains(r"\+", na=False).astype(int)

    # Combo helper cols
    for c in ["player_1", "player_2", "team_1", "team_2"]:
        if c not in df.columns:
            df[c] = ""

    # Fill combo helper cols (vectorized — no iterrows)
    combos = df["is_combo_player"] == 1
    if combos.any():
        player_parts = df.loc[combos, "player"].astype(str).str.split(r"\+", n=1, expand=True)
        team_parts   = df.loc[combos, "team"].astype(str).str.split(r"/",   n=1, expand=True)
        df.loc[combos, "player_1"] = player_parts[0].str.strip()
        df.loc[combos, "player_2"] = (player_parts[1].str.strip() if 1 in player_parts.columns else "")
        df.loc[combos, "team_1"]   = team_parts[0].str.strip()
        df.loc[combos, "team_2"]   = (team_parts[1].str.strip() if 1 in team_parts.columns else "")

    # Build opp_team (singles only)
    df["opp_team"] = build_opp_team_from_gameid(df)

    # tqdm progress
    try:
        from tqdm import tqdm as _tqdm
    except ImportError:
        import subprocess as _sp, sys as _sys
        _sp.check_call([_sys.executable, "-m", "pip", "install", "tqdm", "--break-system-packages", "-q"])
        from tqdm import tqdm as _tqdm

    # NBA ID resolution — build lookup dict once, map vectorially
    pldf = build_nba_directory()
    df["nba_player_id"] = ""
    df["id_status"] = "OK"

    # Singles: resolve each unique name once, then broadcast
    singles = df["is_combo_player"] == 0
    unique_singles = df.loc[singles, "player"].unique()
    single_id_map: dict = {}
    single_status_map: dict = {}
    for name in _tqdm(unique_singles, desc="Resolving NBA IDs", unit="player"):
        pid, _, _ = resolve_nba_id_by_name(pldf, name)
        single_id_map[name]    = str(int(pid)) if pid is not None else ""
        single_status_map[name] = "OK" if pid is not None else "UNRESOLVED_SINGLE"

    df.loc[singles, "nba_player_id"] = df.loc[singles, "player"].map(single_id_map)
    df.loc[singles, "id_status"]     = df.loc[singles, "player"].map(single_status_map)

    # Combos: resolve each unique (player_1, player_2) pair once, map back
    if combos.any():
        print(f"→ Processing {int(combos.sum())} combo rows (writing nba_player_id as id1|id2)...")
        unique_combo_names = set(df.loc[combos, "player_1"].tolist() + df.loc[combos, "player_2"].tolist())
        combo_name_id: dict = {}
        for name in unique_combo_names:
            pid, _, _ = resolve_nba_id_by_name(pldf, name)
            combo_name_id[name] = pid

        def _resolve_combo_row(row) -> tuple:
            id1 = combo_name_id.get(row["player_1"])
            id2 = combo_name_id.get(row["player_2"])
            if id1 is not None and id2 is not None:
                ids = sorted([int(id1), int(id2)])
                return f"{ids[0]}{COMBO_SEP}{ids[1]}", "OK"
            return "", "UNRESOLVED_COMBO"

        combo_results = df.loc[combos, ["player_1", "player_2"]].apply(_resolve_combo_row, axis=1, result_type="expand")
        combo_results.columns = ["nba_player_id", "id_status"]
        df.loc[combos, "nba_player_id"] = combo_results["nba_player_id"].values
        df.loc[combos, "id_status"]     = combo_results["id_status"].values

    # ---- STANDARD LINE + DEVIATION LEVEL (vectorized) ---- #
    df["line_num"] = pd.to_numeric(df["line"], errors="coerce")

    std_df = df[(df["pick_type"] == "Standard") & df["line_num"].notna()]
    std_lookup: dict = (
        std_df.groupby(["player", "prop_norm"])["line_num"]
        .first()
        .to_dict()
    )

    # Vectorized standard_line via tuple key map
    df["standard_line"] = [
        std_lookup.get((p, pn), None)
        for p, pn in zip(df["player"], df["prop_norm"])
    ]

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

    # Vectorized deviation_level
    dev_levels = []
    for _, row in zip(df["pick_type"], zip(df["player"], df["prop_norm"], df["pick_type"], df["line_num"])):
        pt = row[0]  # pick_type already via zip iterator above — reuse below
    # Rebuild properly
    dev_levels = []
    for pt, p, pn, ln in zip(df["pick_type"], df["player"], df["prop_norm"], df["line_num"]):
        if pt == "Standard" or (isinstance(ln, float) and np.isnan(ln)):
            dev_levels.append(0)
        else:
            dev_levels.append(rank_lookup.get((p, pn, pt, ln), 0))
    df["deviation_level"] = dev_levels
    df.drop(columns=["line_num"], inplace=True)

    # ---------------- OUTPUT COLUMN ORDER ---------------- #
    # You requested these PP schema columns to appear right after nba_player_id (when present)
    pp_schema_cols = [
        "projection_id",
        "pp_projection_id",
        "player_id",
        "pp_game_id",
        "start_time",
        "pp_home_team",
        "pp_away_team",
    ]

    core_front = ["nba_player_id"]

    model_cols = [
        "player",
        "pos",
        "team",
        "opp_team",
        "line",
        "prop_type",
        "prop_norm",
        "pick_type",
        "standard_line",
        "deviation_level",
    ]

    front = [c for c in core_front if c in df.columns]
    pp_block = [c for c in pp_schema_cols if c in df.columns]
    model_block = [c for c in model_cols if c in df.columns]

    tail = ["is_combo_player"]

    middle = [
        c for c in df.columns
        if c not in set(front + pp_block + model_block + tail)
    ]

    out = df[front + pp_block + model_block + middle + tail].copy()

    out.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"✅ Saved → {args.output} | rows={len(out)}")


if __name__ == "__main__":
    main()