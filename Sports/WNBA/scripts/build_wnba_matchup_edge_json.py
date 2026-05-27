#!/usr/bin/env python3
"""
Build wnba_matchup_edge.json for Slate Explorer Matchup Edge panel.

Reads: wnba_espn_cache.csv, wnba_defense_summary.csv, wnba_top3_vs_defense.csv,
       slate_sport_wnba.json (or step8) for tonight's team→opp map.

Run (repo root):
  py -3 Sports/WNBA/scripts/build_wnba_matchup_edge_json.py
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[3]
_WNBA = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_WNBA_SCRIPTS_PARENT = Path(__file__).resolve().parents[1]
if str(_WNBA_SCRIPTS_PARENT) not in sys.path:
    sys.path.insert(0, str(_WNBA_SCRIPTS_PARENT))
from step4_fetch_player_stats import derive_stat  # noqa: E402
from utils.wnba_team_keys import canonical_team_key, defense_team_key  # noqa: E402

ESPN_TO_SLATE: dict[str, str] = {
    "LV": "LVA", "LA": "LAS", "NY": "NYL", "GS": "GSV", "PHO": "PHX", "PHX": "PHX",
    "CONN": "CON", "CON": "CON", "DAL": "DAL", "IND": "IND", "ATL": "ATL", "CHI": "CHI",
    "MIN": "MIN", "SEA": "SEA", "WSH": "WSH", "POR": "POR", "TOR": "TOR",
}

SLATE_TO_DEF: dict[str, str] = {v: k for k, v in ESPN_TO_SLATE.items()}
for k in list(ESPN_TO_SLATE):
    SLATE_TO_DEF[ESPN_TO_SLATE[k]] = k
SLATE_TO_DEF.update({"LVA": "LV", "LAS": "LA", "NYL": "NY", "GSV": "GS", "PHO": "PHX", "CONN": "CON"})

CATEGORIES: list[dict] = [
    {"id": "pts", "label": "Points", "threshold": 15.0},
    {"id": "reb", "label": "Rebounds", "threshold": 6.0},
    {"id": "ast", "label": "Assists", "threshold": 4.0},
    {"id": "fg3m", "label": "3-Pointers made", "threshold": 1.5},
    {"id": "stl", "label": "Steals", "threshold": 1.0},
    {"id": "blk", "label": "Blocks", "threshold": 1.0},
    {"id": "stocks", "label": "Stocks (STL+BLK)", "threshold": 2.0},
    {"id": "pra", "label": "Pts+Reb+Ast", "threshold": 25.0},
]

TOP_N = 5
MIN_MPG = 14.0
ELITE_RANK_CUT = 4
PROP_LABEL_TO_NORM: dict[str, str] = {
    "points": "pts", "rebounds": "reb", "assists": "ast", "steals": "stl", "blocks": "blk",
    "3-pointers made": "fg3m", "3pt made": "fg3m", "3-pointers": "fg3m",
    "pts+rebs+asts": "pra", "pts+rebs": "pr", "pts+asts": "pa", "rebs+asts": "ra",
}


def _norm_name(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _slate_team(abbr: str) -> str:
    k = defense_team_key(abbr)
    return ESPN_TO_SLATE.get(k, k or str(abbr or "").upper())


def _load_defense(path: Path) -> pd.DataFrame:
    d = pd.read_csv(path, encoding="utf-8-sig")
    d["def_key"] = d["TEAM_ABBREVIATION"].astype(str).str.upper().map(defense_team_key)
    d["slate_abbr"] = d["def_key"].map(_slate_team)
    return d


def _load_top3(path: Path) -> dict[tuple[str, str, str], dict]:
    if not path.exists():
        return {}
    t3 = pd.read_csv(path, encoding="utf-8-sig")
    out: dict[tuple[str, str, str], dict] = {}
    for r in t3.itertuples(index=False):
        key = (_norm_name(r.PLAYER_NORM), str(r.team_slate).upper(), str(r.category).lower())
        out[key] = {
            "rank_on_team": int(r.rank_on_team) if pd.notna(getattr(r, "rank_on_team", np.nan)) else None,
            "def_boost": float(r.def_boost) if pd.notna(getattr(r, "def_boost", np.nan)) else None,
            "overperform_vs_weak": bool(getattr(r, "overperform_vs_weak", False)),
            "avg_delta_vs_weak": float(r.avg_delta_vs_weak) if pd.notna(getattr(r, "avg_delta_vs_weak", np.nan)) else None,
        }
    return out


def _load_slate_rows(slate_path: Path) -> list[dict]:
    if not slate_path.exists():
        return []
    suf = slate_path.suffix.lower()
    if suf == ".csv":
        df = pd.read_csv(slate_path, encoding="utf-8-sig", dtype=str).fillna("")
        return df.to_dict(orient="records")
    raw = json.loads(slate_path.read_text(encoding="utf-8-sig"))
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    return [r for r in (raw.get("rows") or raw.get("picks") or []) if isinstance(r, dict)]


def _tonight_matchups(slate_path: Path) -> dict[str, dict]:
    """team slate abbr -> {opp_slate, opp_name, opp_def_rank, opp_def_tier, opp_ppg}."""
    rows = _load_slate_rows(slate_path)
    matchups: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        team = str(row.get("team") or "").strip().upper()
        opp = str(row.get("opp") or "").strip().upper()
        if not team or not opp or team == "—" or opp == "—":
            continue
        team_slate = _slate_team(team)
        if team_slate not in matchups:
            matchups[team_slate] = {
                "team_slate": team_slate,
                "opp_slate": _slate_team(opp),
                "opp_def_rank": row.get("opponent_def_rank") or row.get("OVERALL_DEF_RANK"),
                "opp_def_tier": row.get("def_tier") or row.get("DEF_TIER") or "",
            }
    return matchups


def _classify_edge(
    season_avg: float,
    threshold: float,
    opp_rank: float | None,
    n_teams: int,
    hist: dict,
) -> tuple[str, str]:
    rank = float(opp_rank) if opp_rank is not None and not pd.isna(opp_rank) else np.nan
    weak_cut = max(10, int(np.ceil(n_teams * 0.65)))
    over_weak = hist.get("overperform_vs_weak", False)
    boost = hist.get("def_boost")

    if not np.isnan(rank) and rank <= ELITE_RANK_CUT and season_avg < threshold * 0.9:
        return "AVOID", "Elite defense (#1–4); production below threshold — lean UNDER or skip OVER."
    if not np.isnan(rank) and rank >= weak_cut and season_avg >= threshold:
        note = "Strong avg vs soft defense tier."
        if over_weak:
            note += " Historical weak-D booster."
        return "TOP_EDGE", note
    if over_weak and not np.isnan(rank) and rank >= weak_cut - 2:
        return "TOP_EDGE", "Top team producer; historically spikes vs weak defenses."
    if not np.isnan(rank) and rank >= int(n_teams / 2) and season_avg >= threshold * 0.85:
        return "OK_EDGE", "Solid vs average-or-softer opponent defense."
    if not np.isnan(rank) and rank <= ELITE_RANK_CUT:
        return "NEUTRAL", "Elite opponent defense — no clear OVER edge on volume."
    return "NEUTRAL", "No strong matchup edge either way."


def _player_notes(name: str, cat: str, rank: int | None, hist: dict) -> str:
    if hist.get("overperform_vs_weak"):
        return "Historically overperforms vs weak defenses"
    if rank == 1:
        return f"Team #{cat} leader"
    return ""


def build_payload(
    *,
    cache_path: Path,
    defense_path: Path,
    top3_path: Path,
    slate_path: Path,
    season: int | None = None,
) -> dict:
    defense = _load_defense(defense_path)
    n_teams = int(defense["OVERALL_DEF_RANK"].notna().sum()) or 15
    def_by_key = defense.set_index("def_key")
    top3_lookup = _load_top3(top3_path)
    matchups_raw = _tonight_matchups(slate_path)

    df = pd.read_csv(cache_path, low_memory=False, encoding="utf-8-sig")
    if season is None and "SEASON" in df.columns:
        season = int(pd.to_numeric(df["SEASON"], errors="coerce").max())
    if season is not None:
        df = df[pd.to_numeric(df["SEASON"], errors="coerce") == season]

    df["TEAM"] = df["TEAM"].astype(str).str.upper()
    df["MIN"] = pd.to_numeric(df["MIN"], errors="coerce")
    df = df[df["MIN"] >= MIN_MPG * 0.4]
    valid = set(def_by_key.index)
    df = df[df["TEAM"].map(defense_team_key).isin(valid)]

    pos_map: dict[str, str] = {}
    for row in _load_slate_rows(slate_path):
            if isinstance(row, dict) and row.get("player") and row.get("pos"):
                pos_map[_norm_name(row["player"])] = str(row["pos"]).upper()[:2]

    teams_meta: list[dict] = []
    for r in defense.itertuples(index=False):
        teams_meta.append(
            {
                "def_key": r.def_key,
                "slate_abbr": r.slate_abbr,
                "name": str(r.TEAM_NAME),
                "def_rank": int(r.OVERALL_DEF_RANK) if pd.notna(r.OVERALL_DEF_RANK) else None,
                "def_tier": str(r.DEF_TIER or ""),
                "opp_ppg": float(getattr(r, "OPP_PPG", np.nan))
                if hasattr(r, "OPP_PPG") and pd.notna(getattr(r, "OPP_PPG", np.nan))
                else None,
            }
        )

    players_by_key: dict[str, list[dict]] = {}
    matchups_ui: dict[str, dict] = {}
    for t in teams_meta:
        ab = t["slate_abbr"]
        mu = matchups_raw.get(ab, {})
        opp_sl = mu.get("opp_slate", "")
        opp_def_key = SLATE_TO_DEF.get(opp_sl, defense_team_key(opp_sl))
        opp_row = def_by_key.loc[opp_def_key] if opp_def_key in def_by_key.index else None
        matchups_ui[ab] = {
            "opponent_slate": opp_sl,
            "opponent_name": str(opp_row.TEAM_NAME) if opp_row is not None else opp_sl,
            "opponent_def_rank": int(opp_row.OVERALL_DEF_RANK)
            if opp_row is not None and pd.notna(opp_row.OVERALL_DEF_RANK)
            else mu.get("opp_def_rank"),
            "opponent_def_tier": str(opp_row.DEF_TIER) if opp_row is not None else str(mu.get("opp_def_tier") or ""),
            "opponent_opp_ppg": None,
            "team_def_rank": t["def_rank"],
            "team_def_tier": t["def_tier"],
        }

    for cat in CATEGORIES:
        cid = cat["id"]
        df[f"_stat_{cid}"] = derive_stat(df, cid)
        df["_gs"] = derive_stat(df, "pra")  # display "game score" = PRA
        agg = (
            df.groupby(["PLAYER_NAME", "PLAYER_NORM", "TEAM"], as_index=False)
            .agg(
                season_avg=(f"_stat_{cid}", "mean"),
                avg_min=("MIN", "mean"),
                game_score=("_gs", "mean"),
            )
        )

        agg = agg[agg["avg_min"] >= MIN_MPG]
        agg["team_slate"] = agg["TEAM"].map(_slate_team)

        for team_slate, grp in agg.groupby("team_slate", sort=False):
            top = grp.nlargest(TOP_N, "season_avg")
            mu_ui = matchups_ui.get(team_slate, {})
            opp_slate = mu_ui.get("opponent_slate", "")
            opp_rank = mu_ui.get("opponent_def_rank")
            opp_tier = mu_ui.get("opponent_def_tier", "")
            opp_name = mu_ui.get("opponent_name", "")
            opp_ppg = mu_ui.get("opponent_opp_ppg")

            plist: list[dict] = []
            for i, r in enumerate(top.itertuples(index=False), start=1):
                pnorm = _norm_name(r.PLAYER_NORM)
                hist = top3_lookup.get((pnorm, str(team_slate).upper(), cid), {})
                avg = float(r.season_avg)
                edge, note = _classify_edge(avg, cat["threshold"], opp_rank, n_teams, hist)
                plist.append(
                    {
                        "player": r.PLAYER_NAME,
                        "player_norm": pnorm,
                        "pos": pos_map.get(pnorm, ""),
                        "rank_on_team": i,
                        "season_avg": round(avg, 2),
                        "game_score": round(float(r.game_score), 1) if pd.notna(r.game_score) else round(avg, 1),
                        "edge": edge,
                        "notes": _player_notes(r.PLAYER_NAME, cid, i, hist) or note,
                        "overperform_vs_weak": hist.get("overperform_vs_weak", False),
                        "def_boost": hist.get("def_boost"),
                    }
                )

            key = f"{team_slate}|{cid}"
            players_by_key[key] = {
                "team_slate": team_slate,
                "category": cid,
                "category_label": cat["label"],
                "threshold": cat["threshold"],
                "opponent": {
                    "slate_abbr": opp_slate,
                    "name": opp_name,
                    "def_rank": opp_rank,
                    "def_tier": opp_tier,
                    "opp_ppg": opp_ppg,
                },
                "players": plist,
            }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season": season,
        "n_teams": n_teams,
        "elite_rank_cut": ELITE_RANK_CUT,
        "weak_rank_cut": max(10, int(np.ceil(n_teams * 0.65))),
        "categories": CATEGORIES,
        "teams": teams_meta,
        "matchups": matchups_ui,
        "players_by_team_cat": players_by_key,
        "edge_legend": {
            "TOP_EDGE": "Avg at/above threshold vs soft defense (rank 10+). Historical weak-D boosters qualify.",
            "OK_EDGE": "Solid production vs average-or-softer defense.",
            "NEUTRAL": "No clear edge.",
            "AVOID": "Elite defense (#1–4) with below-threshold production — skip OVER.",
        },
    }


def main() -> None:
    ap = __import__("argparse").ArgumentParser()
    ap.add_argument("--cache", default=str(_WNBA / "wnba_espn_cache.csv"))
    ap.add_argument("--defense", default=str(_WNBA / "wnba_defense_summary.csv"))
    ap.add_argument("--top3", default=str(_WNBA / "data" / "wnba_top3_vs_defense.csv"))
    ap.add_argument("--slate", default="")
    args = ap.parse_args()

    slate = Path(args.slate) if args.slate else None
    if slate is None or not slate.exists():
        for cand in (
            _REPO / "ui_runner/templates/slate_sport_wnba.json",
            _REPO / "mobile/www/slate_sport_wnba.json",
            _WNBA / "step8_wnba_direction.csv",
        ):
            if cand.exists():
                slate = cand
                break
        else:
            slate = _REPO / "ui_runner/templates/slate_sport_wnba.json"

    payload = build_payload(
        cache_path=Path(args.cache),
        defense_path=Path(args.defense),
        top3_path=Path(args.top3),
        slate_path=slate,
    )

    out_paths = [
        _WNBA / "data" / "wnba_matchup_edge.json",
        _REPO / "ui_runner/templates/wnba_matchup_edge.json",
        _REPO / "mobile/www/data/wnba_matchup_edge.json",
    ]
    text = json.dumps(payload, indent=2)
    for p in out_paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"Wrote {p}")


if __name__ == "__main__":
    main()
