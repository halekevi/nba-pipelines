from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

PROP_NORM_ALIASES: dict[str, str] = {
    "points": "pts",
    "rebounds": "reb",
    "assists": "ast",
    "steals": "stl",
    "blocks": "blk",
    "3-pointers made": "fg3m",
    "3pt made": "fg3m",
    "3-pointers": "fg3m",
    "three pointers made": "fg3m",
    "pts+rebs+asts": "pra",
    "pts+reb+ast": "pra",
    "points (combo)": "pts",
    "goals": "goals",
    "shots on goal": "sog",
    "shots": "shots",
    "hits": "hits",
    "strikeouts": "strikeouts",
    "total bases": "total_bases",
    "home runs": "home_runs",
    "rbi": "rbi",
}


def norm_prop(raw: object) -> str:
    s = re.sub(r"\(combo\)\s*$", "", str(raw or "").lower().strip())
    s = re.sub(r"\s+", " ", s)
    return PROP_NORM_ALIASES.get(s, s.replace(" ", "_")[:32])


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
        team = str(row.get(team_key) or row.get("team") or "").strip().upper()
        opp = str(row.get(opp_key) or row.get("opp_team") or row.get("opponent") or "").strip().upper()
        if not team or not opp or team in ("—", "-", "NAN") or opp in ("—", "-", "NAN"):
            continue
        if "/" in team:
            team = team.split("/")[0].strip()
        if "/" in opp:
            opp = opp.split("/")[0].strip()
        if team not in out:
            out[team] = {
                "team_slate": team,
                "opp_slate": opp,
                "opp_def_rank": row.get("opponent_def_rank") or row.get("OVERALL_DEF_RANK") or row.get("def_rank"),
                "opp_def_tier": row.get("def_tier") or row.get("DEF_TIER") or "",
            }
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
    df["team"] = df.get("team", pd.Series([""] * len(df))).astype(str).str.upper().str.split("/").str[0]
    df["prop_norm"] = df.get("prop_norm", df.get("prop", "")).map(norm_prop)
    df["season_avg"] = pd.to_numeric(df.get("season_avg"), errors="coerce")
    df["player"] = df.get("player", "").astype(str)
    df = df[df["season_avg"].notna() & df["player"].astype(bool)]

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
