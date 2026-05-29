#!/usr/bin/env python3
"""
Build nhl_matchup_edge.json for Slate Explorer Matchup Edge panel.

Reads: proporacle_ref.db (nhl table), nhl_defense_summary.csv, nhl_top3_vs_defense.csv,
       slate_sport_nhl.json (or step8) for tonight's team→opp map.

Matchup classification needs opponent + def_rank from step8 (or published slate JSON);
step1-only slates produce NEUTRAL edges — expected, not a bug.

Run (repo root):
  py -3 Sports/NHL/scripts/build_nhl_matchup_edge_json.py
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[3]
_NHL = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from utils.matchup_edge.classify import classify_edge  # noqa: E402
from utils.matchup_edge.slate_io import (  # noqa: E402
    build_slate_pp_lookup,
    load_slate_rows,
    lookup_pp_edge,
    tonight_matchups,
)

SLATE_TO_DEF: dict[str, str] = {
    "LA": "LAK",
    "NJ": "NJD",
    "SJ": "SJS",
    "TB": "TBL",
    "CLB": "CBJ",
    "ARZ": "UTA",
}

CATEGORIES: list[dict] = [
    {"id": "goals", "label": "Goals", "threshold": 0.4},
    {"id": "assists", "label": "Assists", "threshold": 0.4},
    {"id": "points", "label": "Points", "threshold": 0.8},
    {"id": "shots", "label": "Shots", "threshold": 2.5},
]

TOP_N = 5
BOTTOM_N = 5
MIN_GAMES = 10
MIN_TOI = 8.0
ELITE_RANK_CUT = 6


def _norm_name(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def defense_team_key(team: object) -> str:
    s = str(team or "").strip().upper()
    return SLATE_TO_DEF.get(s, s)


def _slate_team(abbr: str) -> str:
    return defense_team_key(abbr)


def _load_defense(path: Path) -> pd.DataFrame:
    d = pd.read_csv(path, encoding="utf-8-sig")
    d["def_key"] = d["team"].astype(str).str.upper().map(defense_team_key)
    d["slate_abbr"] = d["def_key"]
    d["OVERALL_DEF_RANK"] = pd.to_numeric(d["OVERALL_DEF_RANK"], errors="coerce")
    return d


def _default_db_path() -> Path:
    return _REPO / "data" / "cache" / "proporacle_ref.db"


def _load_game_logs(db_path: Path, season_year: int | None) -> pd.DataFrame:
    if not db_path.is_file():
        raise FileNotFoundError(f"Missing DB: {db_path}")
    con = sqlite3.connect(db_path)
    df = pd.read_sql(
        """
        SELECT player, team, game_date, home_team, away_team, position,
               goals, assists, points, shots_on_goal, toi
        FROM nhl
        ORDER BY game_date, event_id
        """,
        con,
    )
    con.close()
    if df.empty:
        return df

    df["PLAYER_NAME"] = df["player"].astype(str)
    df["PLAYER_NORM"] = df["PLAYER_NAME"].map(_norm_name)
    df["TEAM"] = df["team"].astype(str).str.upper()
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
    if season_year is not None:
        df = df[df["game_date"].dt.year == season_year]

    pos = df["position"].astype(str).str.upper()
    df = df[~pos.eq("G")].copy()
    df["toi"] = pd.to_numeric(df["toi"], errors="coerce")
    df = df[df["toi"].fillna(0) >= MIN_TOI * 0.5]

    def _opp(row: pd.Series) -> str:
        t, h, a = row["TEAM"], str(row["home_team"]).upper(), str(row["away_team"]).upper()
        if t == h:
            return a
        if t == a:
            return h
        return ""

    df["opp_team"] = df.apply(_opp, axis=1)
    return df[df["opp_team"].astype(str).str.len() > 0].copy()


def _derive_stat(df: pd.DataFrame, cat: str) -> pd.Series:
    if cat == "goals":
        return pd.to_numeric(df["goals"], errors="coerce")
    if cat == "assists":
        return pd.to_numeric(df["assists"], errors="coerce")
    if cat == "points":
        return pd.to_numeric(df["points"], errors="coerce")
    if cat == "shots":
        return pd.to_numeric(df["shots_on_goal"], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def _load_leader_lookups(path: Path) -> tuple[dict[tuple[str, str, str], dict], dict[tuple[str, str, str], dict]]:
    empty: dict[tuple[str, str, str], dict] = {}
    if not path.exists():
        return empty, empty
    t3 = pd.read_csv(path, encoding="utf-8-sig")
    top_out: dict[tuple[str, str, str], dict] = {}
    bottom_out: dict[tuple[str, str, str], dict] = {}
    for r in t3.itertuples(index=False):
        key = (_norm_name(r.PLAYER_NORM), str(r.team_slate).upper(), str(r.category).lower())
        hist = {
            "rank_on_team": int(r.rank_on_team) if pd.notna(getattr(r, "rank_on_team", np.nan)) else None,
            "def_boost": float(r.def_boost) if pd.notna(getattr(r, "def_boost", np.nan)) else None,
            "overperform_vs_weak": bool(getattr(r, "overperform_vs_weak", False)),
            "fades_vs_elite": bool(getattr(r, "fades_vs_elite", False)),
            "avg_delta_vs_weak": float(r.avg_delta_vs_weak) if pd.notna(getattr(r, "avg_delta_vs_weak", np.nan)) else None,
            "avg_delta_vs_elite": float(r.avg_delta_vs_elite) if pd.notna(getattr(r, "avg_delta_vs_elite", np.nan)) else None,
        }
        side = str(getattr(r, "leader_side", "top") or "top").lower()
        if side == "bottom":
            bottom_out[key] = hist
        else:
            top_out[key] = hist
    return top_out, bottom_out


def _team_rank_label(top_rank: int | None, bottom_rank: int | None) -> str:
    parts: list[str] = []
    if top_rank is not None and top_rank <= 5:
        parts.append(f"T{top_rank}")
    if bottom_rank is not None and bottom_rank <= 3:
        parts.append(f"B{bottom_rank}")
    return "/".join(parts)


def _load_slate_rows(slate_path: Path) -> list[dict]:
    if not slate_path.exists():
        return []
    suf = slate_path.suffix.lower()
    if suf == ".csv":
        df = pd.read_csv(slate_path, encoding="utf-8-sig", dtype=str).fillna("")
        return df.to_dict(orient="records")
    if suf in (".xlsx", ".xls"):
        df = pd.read_excel(slate_path, dtype=str).fillna("")
        return df.to_dict(orient="records")
    return load_slate_rows(slate_path)


def _slate_roster_maps(slate_path: Path) -> tuple[dict[str, str], dict[str, str], dict[str, set[str]]]:
    team_by_player: dict[str, str] = {}
    pos_by_player: dict[str, str] = {}
    roster_by_team: dict[str, set[str]] = {}
    for row in _load_slate_rows(slate_path):
        if not isinstance(row, dict):
            continue
        player = str(row.get("player") or row.get("player_name") or "").strip()
        if not player:
            continue
        pnorm = _norm_name(player)
        team_raw = str(row.get("team") or "").strip().upper()
        if not team_raw or team_raw in ("—", "-", "NAN"):
            continue
        team_slate = _slate_team(team_raw)
        team_by_player[pnorm] = team_slate
        roster_by_team.setdefault(team_slate, set()).add(pnorm)
        pos_raw = row.get("position") or row.get("position_group") or row.get("pos")
        if pos_raw and str(pos_raw).strip().lower() not in ("", "nan", "none"):
            pos_by_player[pnorm] = str(pos_raw).strip().upper()[:3]
    return team_by_player, pos_by_player, roster_by_team


def _assign_player_teams(
    df: pd.DataFrame,
    *,
    slate_team_by_player: dict[str, str],
    season: int | None,
) -> pd.Series:
    df = df.copy()
    df["_pnorm"] = df["PLAYER_NORM"].astype(str).map(_norm_name)
    df["_game_dt"] = pd.to_datetime(df["game_date"], errors="coerce")
    df["_team_slate"] = df["TEAM"].map(defense_team_key)

    if season is not None:
        sub = df[df["_game_dt"].dt.year == season].sort_values("_game_dt")
    else:
        sub = df.sort_values("_game_dt")
    latest: dict[str, str] = {}
    for pnorm, grp in sub.groupby("_pnorm", sort=False):
        if grp.empty:
            continue
        latest[pnorm] = str(grp.iloc[-1]["_team_slate"])

    return pd.Series(
        [slate_team_by_player.get(p) or latest.get(p) or "" for p in df["_pnorm"]],
        index=df.index,
    )


def _norm_matchups(raw: dict[str, dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for team, mu in raw.items():
        tk = defense_team_key(team)
        if tk in out:
            continue
        opp = defense_team_key(str(mu.get("opp_slate", "")))
        out[tk] = {
            "team_slate": tk,
            "opp_slate": opp,
            "opp_def_rank": mu.get("opp_def_rank") or mu.get("def_rank"),
            "opp_def_tier": mu.get("opp_def_tier") or mu.get("def_tier") or "",
        }
    return out


def _player_notes(name: str, cat: str, rank: int | None, hist: dict) -> str:
    if hist.get("overperform_vs_weak"):
        return "Historically overperforms vs weak defenses"
    if rank == 1:
        return f"Team #{cat} leader"
    return ""


def build_payload(
    *,
    db_path: Path,
    defense_path: Path,
    top3_path: Path,
    slate_path: Path,
    season: int | None = None,
) -> dict:
    defense = _load_defense(defense_path)
    n_teams = int(defense["OVERALL_DEF_RANK"].notna().sum()) or 32
    def_by_key = defense.set_index("def_key")
    top3_lookup, bottom3_lookup = _load_leader_lookups(top3_path)
    slate_rows = _load_slate_rows(slate_path)
    matchups_raw = _norm_matchups(tonight_matchups(slate_rows, team_key="team", opp_key="opp"))
    if not matchups_raw and slate_rows:
        matchups_raw = _norm_matchups(tonight_matchups(slate_rows, team_key="team", opp_key="opponent"))
    pp_by_player = build_slate_pp_lookup(slate_rows, CATEGORIES, team_normalize=_slate_team)
    slate_team_by_player, pos_by_player, roster_by_team = _slate_roster_maps(slate_path)

    df = _load_game_logs(db_path, season)
    if df.empty:
        return {
            "sport": "nhl",
            "display_name": "NHL",
            "error": "No skater game logs in DB",
            "teams": [],
            "categories": CATEGORIES,
            "matchups": {},
            "players_by_team_cat": {},
        }

    if season is None:
        season = int(df["game_date"].max().year)

    df["team_slate"] = _assign_player_teams(df, slate_team_by_player=slate_team_by_player, season=season)
    df = df[df["team_slate"].astype(str).str.len() > 0]

    teams_meta: list[dict] = []
    for r in defense.itertuples(index=False):
        gaa = getattr(r, "opp_gaa", np.nan)
        teams_meta.append(
            {
                "def_key": r.def_key,
                "slate_abbr": r.slate_abbr,
                "name": str(r.team),
                "def_rank": int(r.OVERALL_DEF_RANK) if pd.notna(r.OVERALL_DEF_RANK) else None,
                "def_tier": str(getattr(r, "def_tier", "") or ""),
                "opp_gaa": float(gaa) if pd.notna(gaa) else None,
            }
        )

    matchups_ui: dict[str, dict] = {}
    for t in teams_meta:
        ab = t["slate_abbr"]
        mu = matchups_raw.get(ab, {})
        opp_sl = _slate_team(str(mu.get("opp_slate", "")))
        opp_row = def_by_key.loc[opp_sl] if opp_sl in def_by_key.index else None
        matchups_ui[ab] = {
            "opponent_slate": opp_sl,
            "opponent_name": str(opp_row.team) if opp_row is not None else opp_sl,
            "opponent_def_rank": int(opp_row.OVERALL_DEF_RANK)
            if opp_row is not None and pd.notna(opp_row.OVERALL_DEF_RANK)
            else mu.get("opp_def_rank"),
            "opponent_def_tier": str(getattr(opp_row, "def_tier", "")) if opp_row is not None else str(mu.get("opp_def_tier") or ""),
            "opponent_opp_gaa": float(getattr(opp_row, "opp_gaa", np.nan))
            if opp_row is not None and pd.notna(getattr(opp_row, "opp_gaa", np.nan))
            else None,
            "team_def_rank": t["def_rank"],
            "team_def_tier": t["def_tier"],
        }

    players_by_key: dict[str, dict] = {}
    for cat in CATEGORIES:
        cid = cat["id"]
        df[f"_stat_{cid}"] = _derive_stat(df, cid)
        df["_gs"] = _derive_stat(df, "points")
        agg = (
            df.groupby(["PLAYER_NAME", "PLAYER_NORM", "team_slate"], as_index=False)
            .agg(
                season_avg=(f"_stat_{cid}", "mean"),
                games=(f"_stat_{cid}", "count"),
                avg_toi=("toi", "mean"),
                game_score=("_gs", "mean"),
            )
        )
        agg = agg[(agg["games"] >= MIN_GAMES) & (agg["avg_toi"].fillna(0) >= MIN_TOI)]

        for team_slate, grp in agg.groupby("team_slate", sort=False):
            roster = roster_by_team.get(str(team_slate).upper())
            if roster:
                grp = grp[grp["PLAYER_NORM"].astype(str).map(_norm_name).isin(roster)]
            if grp.empty:
                continue
            top = grp.nlargest(TOP_N, "season_avg")
            bottom = grp.nsmallest(BOTTOM_N, "season_avg")
            mu_ui = matchups_ui.get(team_slate, {})
            opp_slate = mu_ui.get("opponent_slate", "")
            opp_rank = mu_ui.get("opponent_def_rank")
            opp_tier = mu_ui.get("opponent_def_tier", "")
            opp_name = mu_ui.get("opponent_name", "")
            opp_gaa = mu_ui.get("opponent_opp_gaa")

            plist: list[dict] = []
            seen_norm: set[str] = set()

            def _append_player(r, *, top_rank: int | None, bottom_rank: int | None) -> None:
                pnorm = _norm_name(r.PLAYER_NORM)
                if pnorm in seen_norm:
                    return
                seen_norm.add(pnorm)
                key = (pnorm, str(team_slate).upper(), cid)
                hist_top = top3_lookup.get(key, {})
                hist_bot = bottom3_lookup.get(key, {})
                hist = {**hist_top, **{k: v for k, v in hist_bot.items() if v is not None}}
                pp = lookup_pp_edge(
                    pp_by_player,
                    player=r.PLAYER_NAME,
                    team=str(team_slate),
                    cat_id=cid,
                    player_norm=pnorm,
                )
                avg = float(r.season_avg)
                edge, note = classify_edge(
                    avg,
                    cat["threshold"],
                    opp_rank,
                    n_teams,
                    hist=hist,
                    elite_rank_cut=ELITE_RANK_CUT,
                    cat_id=cid,
                    pp_line=pp.get("pp_line"),
                    pp_edge=pp.get("pp_edge"),
                    rank_on_team=top_rank,
                    bottom_rank_on_team=bottom_rank,
                )
                plist.append(
                    {
                        "player": r.PLAYER_NAME,
                        "player_norm": pnorm,
                        "pos": pos_by_player.get(pnorm, ""),
                        "rank_on_team": top_rank,
                        "bottom_rank_on_team": bottom_rank,
                        "leader_slice": "bottom"
                        if bottom_rank is not None and top_rank is None
                        else "top",
                        "team_rank_label": _team_rank_label(top_rank, bottom_rank),
                        "bottom3_on_team": bottom_rank is not None and bottom_rank <= 3,
                        "season_avg": round(avg, 2),
                        "game_score": round(float(r.game_score), 2) if pd.notna(r.game_score) else round(avg, 2),
                        "pp_line": pp.get("pp_line"),
                        "pp_edge": round(float(pp["pp_edge"]), 2) if pp.get("pp_edge") is not None else None,
                        "edge": edge,
                        "notes": note or _player_notes(r.PLAYER_NAME, cid, top_rank, hist),
                        "overperform_vs_weak": hist.get("overperform_vs_weak", False),
                        "fades_vs_elite": hist.get("fades_vs_elite", False),
                        "def_boost": hist.get("def_boost"),
                        "avg_delta_vs_elite": hist.get("avg_delta_vs_elite"),
                    }
                )

            for i, r in enumerate(top.itertuples(index=False), start=1):
                bot_hist = bottom3_lookup.get((_norm_name(r.PLAYER_NORM), str(team_slate).upper(), cid), {})
                _append_player(r, top_rank=i, bottom_rank=bot_hist.get("rank_on_team"))

            for i, r in enumerate(bottom.itertuples(index=False), start=1):
                if _norm_name(r.PLAYER_NORM) in seen_norm:
                    continue
                _append_player(r, top_rank=None, bottom_rank=i)

            players_by_key[f"{team_slate}|{cid}"] = {
                "team_slate": team_slate,
                "category": cid,
                "category_label": cat["label"],
                "threshold": cat["threshold"],
                "opponent": {
                    "slate_abbr": opp_slate,
                    "name": opp_name,
                    "def_rank": opp_rank,
                    "def_tier": opp_tier,
                    "opp_gaa": opp_gaa,
                },
                "players": plist,
            }

    return {
        "sport": "nhl",
        "display_name": "NHL",
        "matchup_mode": "team",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season": season,
        "n_teams": n_teams,
        "elite_rank_cut": ELITE_RANK_CUT,
        "weak_rank_cut": max(10, int(np.ceil(n_teams * 0.65))),
        "opp_metric_label": "Opp def rank (GAA)",
        "categories": CATEGORIES,
        "teams": teams_meta,
        "matchups": matchups_ui,
        "players_by_team_cat": players_by_key,
        "edge_legend": {
            "TOP_EDGE": "Positive PP edge (+1.5+) or strong avg vs soft defense (high rank = weak GAA).",
            "OK_EDGE": "PP edge on board or team leader vs soft/average defense.",
            "TOP_UNDER": "PP edge -2+ vs elite defense, or historically fades vs elite D.",
            "OK_UNDER": "Negative PP edge vs elite defense or bottom-3 producer vs elite D — lean UNDER.",
            "NEUTRAL": "No clear edge.",
            "AVOID": "Negative PP edge without elite matchup — skip OVER.",
        },
    }


def _resolve_slate(slate: Path | None) -> Path:
    if slate is not None and slate.exists():
        return slate
    for cand in (
        _REPO / "ui_runner/templates/slate_sport_nhl.json",
        _REPO / "mobile/www/slate_sport_nhl.json",
        _NHL / "step8_nhl_direction_clean.csv",
        _NHL / "step8_nhl_direction_clean.xlsx",
    ):
        if cand.exists():
            return cand
    return _REPO / "ui_runner/templates/slate_sport_nhl.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(_default_db_path()))
    ap.add_argument("--defense", default=str(_NHL / "cache" / "nhl_defense_summary.csv"))
    ap.add_argument("--top3", default=str(_NHL / "data" / "nhl_top3_vs_defense.csv"))
    ap.add_argument("--slate", default="")
    args = ap.parse_args()

    slate = _resolve_slate(Path(args.slate) if args.slate else None)
    payload = build_payload(
        db_path=Path(args.db),
        defense_path=Path(args.defense),
        top3_path=Path(args.top3),
        slate_path=slate,
    )

    out_paths = [
        _NHL / "data" / "nhl_matchup_edge.json",
        _REPO / "ui_runner/templates/nhl_matchup_edge.json",
        _REPO / "mobile/www/data/nhl_matchup_edge.json",
    ]
    text = json.dumps(payload, indent=2)
    for p in out_paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"Wrote {p}")


if __name__ == "__main__":
    main()
