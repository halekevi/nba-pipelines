from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

PROP_NORM_ALIASES: dict[str, str] = {
    "points": "pts",
    "rebounds": "reb",
    "assists": "ast",
    "steals": "stl",
    "blocks": "blk",
    "3-pointers made": "fg3m",
    "3-pt made": "fg3m",
    "3pt made": "fg3m",
    "3-pointers": "fg3m",
    "three pointers made": "fg3m",
    "pts+rebs+asts": "pra",
    "pts+reb+ast": "pra",
    "points (combo)": "pts",
    "goals": "goals",
    "shots on goal": "shots",
    "shots_on_goal": "shots",
    "sog": "shots",
    "shots": "shots",
    "hits": "hits",
    "strikeouts": "strikeouts",
    "pitcher strikeouts": "strikeouts",
    "total bases": "total_bases",
    "home runs": "home_runs",
    "rbi": "rbi",
    "total games": "match_total_games",
    "total games won": "games_won",
    "double faults": "double_faults",
    "aces": "aces",
    "break points won": "break_points_won",
    "stocks": "stocks",
    "stl+blk": "stocks",
    "pass yards": "pass_yds",
    "passing yards": "pass_yds",
    "rushing yards": "rush_yds",
    "receiving yards": "rec_yds",
}


def norm_name(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def pick_type_rank(pick_type: object) -> int:
    p = str(pick_type or "").lower()
    if p == "standard":
        return 3
    if p == "demon":
        return 2
    if p == "goblin":
        return 1
    return 0


def _opp_from_row(row: dict) -> str:
    for k in ("opp", "opp_team", "opp_team_abbr", "pp_opp_team", "opponent"):
        v = str(row.get(k) or "").strip().upper()
        if v and v not in ("—", "-", "NAN", "UNKNOWN_OPP", "UNK"):
            return v
    return ""


def _team_from_row(row: dict) -> str:
    team = str(row.get("team") or row.get("team_abbr") or row.get("pp_team") or "").strip().upper()
    if team and team not in ("—", "-", "NAN"):
        if "/" in team:
            team = team.split("/")[0].strip()
        return team
    return ""


def norm_prop(raw: object) -> str:
    s = re.sub(r"\(combo\)\s*$", "", str(raw or "").lower().strip())
    s = re.sub(r"\s+", " ", s)
    if s in PROP_NORM_ALIASES:
        return PROP_NORM_ALIASES[s]
    if "3-pt" in s or "3pt" in s:
        return "fg3m"
    if "point" in s and "+" in s:
        return "pra"
    if "point" in s:
        return "pts"
    if "rebound" in s:
        return "reb"
    if "assist" in s:
        return "ast"
    if "steal" in s:
        return "stl"
    if "block" in s and "shot" not in s:
        return "blk"
    if "shot" in s and "goal" in s:
        return "shots"
    if "strikeout" in s or s == "k's":
        return "strikeouts"
    if "total base" in s:
        return "total_bases"
    if "home run" in s:
        return "home_runs"
    if "total game" in s and "won" in s:
        return "games_won"
    if "total game" in s:
        return "match_total_games"
    if "double fault" in s:
        return "double_faults"
    if "ace" in s:
        return "aces"
    if "break point" in s:
        return "break_points_won"
    if "stock" in s or "stl+blk" in s:
        return "stocks"
    if "pass" in s and "yard" in s:
        return "pass_yds"
    if "rush" in s and "yard" in s:
        return "rush_yds"
    if "receiv" in s and "yard" in s:
        return "rec_yds"
    return s.replace(" ", "_")[:32]


def build_slate_pp_lookup(
    rows: list[dict],
    categories: list[dict],
    *,
    team_normalize: Callable[[str], str] | None = None,
    player_mode: bool = False,
) -> dict[tuple[str, ...], dict]:
    """Map slate PP line/edge by player (+ team) + category id."""
    cat_ids = {c["id"] for c in categories}

    def _team_key(raw: object) -> str:
        t = _team_from_row({"team": raw}) or str(raw or "").strip().upper()
        if team_normalize:
            t = team_normalize(t)
        return t

    out: dict[tuple[str, ...], dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        player = str(row.get("player") or row.get("player_name") or "").strip()
        if not player:
            continue
        pnorm = norm_name(player)
        prop_raw = row.get("prop") or row.get("prop_type") or row.get("prop_norm")
        cid = norm_prop(prop_raw)
        if cid not in cat_ids:
            continue
        if player_mode:
            team_key = pnorm
            lookup_key: tuple[str, ...] = (pnorm, cid)
        else:
            team_key = _team_key(row.get("team") or row.get("team_abbr"))
            if not team_key:
                continue
            lookup_key = (pnorm, team_key, cid)
        line = pd.to_numeric(row.get("line") or row.get("standard_line"), errors="coerce")
        edge = pd.to_numeric(row.get("edge") or row.get("abs_edge"), errors="coerce")
        avg = pd.to_numeric(row.get("season_avg") or row.get("stat_season_avg"), errors="coerce")
        pt_rank = pick_type_rank(row.get("pick_type"))
        cur = out.get(lookup_key)
        cur_rank = cur.get("_pt_rank", -1) if cur else -1
        cur_edge = float(cur.get("pp_edge") or -999) if cur else -999
        new_edge = float(edge) if pd.notna(edge) else -999
        if cur is None or new_edge > cur_edge or (new_edge == cur_edge and pt_rank > cur_rank):
            out[lookup_key] = {
                "pp_line": float(line) if pd.notna(line) else None,
                "pp_edge": float(edge) if pd.notna(edge) else None,
                "slate_avg": float(avg) if pd.notna(avg) else None,
                "pick_type": str(row.get("pick_type") or ""),
                "dir": str(row.get("dir") or row.get("direction") or ""),
                "team_key": team_key,
                "_pt_rank": pt_rank,
            }
    for rec in out.values():
        rec.pop("_pt_rank", None)
    return out


def lookup_pp_edge(
    pp_lookup: dict[tuple[str, ...], dict],
    *,
    player: str,
    team: str,
    cat_id: str,
    player_norm: str | None = None,
    player_mode: bool = False,
) -> dict:
    pnorm = player_norm or norm_name(player)
    if player_mode:
        return pp_lookup.get((pnorm, cat_id), {})
    team_u = str(team or "").upper()
    return pp_lookup.get((pnorm, team_u, cat_id), {})


def load_slate_rows(slate_path: Path) -> list[dict]:
    if not slate_path.exists():
        return []
    suf = slate_path.suffix.lower()
    if suf in (".csv", ".xlsx", ".xls"):
        if suf == ".csv":
            df = pd.read_csv(slate_path, encoding="utf-8-sig", dtype=str).fillna("")
        else:
            df = pd.read_excel(slate_path, dtype=str).fillna("")
        return df.to_dict(orient="records")
    raw = json.loads(slate_path.read_text(encoding="utf-8-sig"))
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    return [r for r in (raw.get("rows") or raw.get("picks") or []) if isinstance(r, dict)]


def tonight_matchups(rows: list[dict], *, team_key: str = "team", opp_key: str = "opp") -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        team = _team_from_row(row) or str(row.get(team_key) or "").strip().upper()
        opp = _opp_from_row(row) or str(row.get(opp_key) or "").strip().upper()
        if not team or not opp:
            continue
        if team not in out:
            out[team] = {
                "team_slate": team,
                "opp_slate": opp,
                "opp_def_rank": (
                    row.get("opponent_def_rank")
                    or row.get("opp_def_rank")
                    or row.get("OVERALL_DEF_RANK")
                    or row.get("def_rank")
                ),
                "opp_def_tier": row.get("def_tier") or row.get("DEF_TIER") or row.get("opp_def_tier") or "",
            }
    return out


def _is_combo_player(name: object) -> bool:
    s = str(name or "")
    return "+" in s or " + " in s.lower()


def pp_leaders_from_slate(
    rows: list[dict],
    categories: list[dict],
    *,
    top_n: int = 5,
    team_normalize: Callable[[str], str] | None = None,
) -> dict[str, list[dict]]:
    """Top single-player props on tonight's board keyed team|category."""
    if not rows:
        return {}

    def _team(raw: object) -> str:
        t = _team_from_row({"team": raw}) or str(raw or "").strip().upper()
        if team_normalize:
            t = team_normalize(t)
        return t

    cat_ids = {c["id"] for c in categories}
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        player = str(row.get("player") or row.get("player_name") or "").strip()
        if not player or _is_combo_player(player):
            continue
        team = _team(row.get("team") or row.get("team_abbr"))
        if not team:
            continue
        cid = norm_prop(row.get("prop") or row.get("prop_type") or row.get("prop_norm"))
        if cid not in cat_ids:
            continue
        avg = pd.to_numeric(row.get("season_avg") or row.get("stat_season_avg"), errors="coerce")
        edge = pd.to_numeric(row.get("edge") or row.get("abs_edge"), errors="coerce")
        line = pd.to_numeric(row.get("line") or row.get("standard_line"), errors="coerce")
        if pd.isna(avg) and pd.isna(edge):
            continue
        sort_val = float(edge) if pd.notna(edge) else float(avg)
        buckets.setdefault(f"{team}|{cid}", []).append(
            {
                "player": player,
                "player_norm": norm_name(player),
                "pos": str(row.get("pos") or row.get("position") or "").strip().upper()[:3],
                "season_avg": round(float(avg), 2) if pd.notna(avg) else round(sort_val, 2),
                "game_score": round(float(avg), 1) if pd.notna(avg) else round(sort_val, 1),
                "pp_line": float(line) if pd.notna(line) else None,
                "pp_edge": round(float(edge), 2) if pd.notna(edge) else None,
                "_sort": sort_val,
            }
        )

    out: dict[str, list[dict]] = {}
    for key, items in buckets.items():
        items.sort(key=lambda x: x["_sort"], reverse=True)
        players: list[dict] = []
        seen: set[str] = set()
        for item in items:
            pn = item["player_norm"]
            if pn in seen:
                continue
            seen.add(pn)
            rec = {k: v for k, v in item.items() if k != "_sort"}
            rec["rank_on_team"] = len(players) + 1
            rec["edge"] = "NEUTRAL"
            rec["notes"] = "From tonight's PP board"
            rec["overperform_vs_weak"] = False
            rec["def_boost"] = None
            players.append(rec)
            if len(players) >= top_n:
                break
        if players:
            out[key] = players
    return out


def leaders_from_slate(
    rows: list[dict],
    categories: list[dict],
    *,
    top_n: int = 5,
) -> dict[str, list[dict]]:
    """Fallback leaders keyed team|category from slate season_avg."""
    if not rows:
        return {}
    df = pd.DataFrame(rows)
    if df.empty:
        return {}
    team_src = df.get("team", df.get("team_abbr", pd.Series([""] * len(df))))
    df["team"] = team_src.astype(str).str.upper().str.split("/").str[0]
    prop_src = df.get("prop_norm", df.get("prop_type", df.get("prop", "")))
    df["prop_norm"] = prop_src.map(norm_prop)
    avg_src = df.get("season_avg")
    if avg_src is None or pd.to_numeric(avg_src, errors="coerce").notna().sum() == 0:
        for alt in ("stat_season_avg", "projection", "stat_last10_avg", "stat_last5_avg"):
            if alt in df.columns:
                avg_src = df[alt]
                break
    df["season_avg"] = pd.to_numeric(avg_src, errors="coerce")
    df["player"] = df.get("player", "").astype(str)
    df = df[df["season_avg"].notna() & df["player"].astype(bool)]
    df = df[~df["player"].map(_is_combo_player)]

    out: dict[str, list[dict]] = {}
    cat_ids = {c["id"] for c in categories}
    for cat in categories:
        cid = cat["id"]
        sub = df[df["prop_norm"] == cid]
        if sub.empty:
            sub = df[df["prop_norm"].astype(str).str.contains(cid[:3], na=False)]
        for team, grp in sub.groupby("team", sort=False):
            top = grp.nlargest(top_n, "season_avg")
            players = []
            for i, r in enumerate(top.itertuples(index=False), start=1):
                players.append(
                    {
                        "player": r.player,
                        "player_norm": str(r.player).lower(),
                        "pos": "",
                        "rank_on_team": i,
                        "season_avg": round(float(r.season_avg), 2),
                        "game_score": round(float(r.season_avg), 1),
                        "edge": "NEUTRAL",
                        "notes": "From slate season average",
                        "overperform_vs_weak": False,
                        "def_boost": None,
                    }
                )
            if players:
                out[f"{team}|{cid}"] = players
    return out
