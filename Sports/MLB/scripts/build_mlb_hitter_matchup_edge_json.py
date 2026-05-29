#!/usr/bin/env python3
"""
Build mlb_matchup_edge.json for Slate Explorer Matchup Edge panel (hitter props).

Reads: mlb_stats_cache.csv, mlb_defense_summary.csv, mlb_hitter_top3_vs_defense.csv,
       slate_sport_mlb.json (or step8) for tonight's team→opp map.

Pitcher K/outs/ER use a separate starter-centric model (deferred).

Matchup classification needs opponent + def_rank from step8 (or published slate JSON);
step1-only slates produce NEUTRAL edges — expected, not a bug.

Run (repo root):
  py -3 Sports/MLB/scripts/build_mlb_hitter_matchup_edge_json.py
  py -3 Sports/MLB/scripts/build_mlb_hitter_matchup_edge_json.py --slate Sports/MLB/step8_mlb_direction.csv
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[3]
_MLB = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from utils.matchup_edge.classify import classify_edge  # noqa: E402
from utils.matchup_edge.slate_io import (  # noqa: E402
    build_slate_pp_lookup,
    load_slate_rows,
    lookup_pp_edge,
    tonight_matchups,
)

_ANALYZE_PATH = Path(__file__).resolve().parent / "analyze_top_hitters_vs_defense.py"
_spec = importlib.util.spec_from_file_location("analyze_top_hitters_vs_defense", _ANALYZE_PATH)
_analyze = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_analyze)

CATEGORIES: list[dict] = [
    {"id": "hits", "label": "Hits", "threshold": 1.0},
    {"id": "total_bases", "label": "Total bases", "threshold": 1.5},
    {"id": "home_runs", "label": "Home runs", "threshold": 0.5},
    {"id": "rbi", "label": "RBI", "threshold": 0.8},
]

TOP_N = 5
BOTTOM_N = 3
MIN_GAMES = 10
ELITE_RANK_CUT = 8


def _norm_name(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def defense_team_key(team: object) -> str:
    return _analyze.defense_team_key(team)


def _slate_team(abbr: str) -> str:
    return _analyze._slate_team(abbr)


def _load_defense(path: Path) -> pd.DataFrame:
    d = _analyze._load_defense(path)
    d["slate_abbr"] = d["def_key"]
    return d


def _derive_stat(df: pd.DataFrame, cat: str) -> pd.Series:
    return _analyze._derive_stat(df, cat)


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
        pos_raw = row.get("pos") or row.get("position")
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
        sub = df[df["SEASON"].astype(str) == str(season)].sort_values("_game_dt")
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
        return "Historically overperforms vs weak pitching"
    if rank == 1:
        return f"Team #{cat} leader"
    return ""


def build_payload(
    *,
    cache_path: Path,
    defense_path: Path,
    top3_path: Path,
    slate_path: Path,
    id_cache_path: Path,
    season: int | None = None,
) -> dict:
    defense = _load_defense(defense_path)
    n_teams = int(defense["OVERALL_DEF_RANK"].notna().sum()) or 30
    def_by_key = defense.set_index("def_key")
    top3_lookup, bottom3_lookup = _load_leader_lookups(top3_path)
    slate_rows = _load_slate_rows(slate_path)
    matchups_raw = _norm_matchups(tonight_matchups(slate_rows, team_key="team", opp_key="opp"))
    if not matchups_raw and slate_rows:
        matchups_raw = _norm_matchups(tonight_matchups(slate_rows, team_key="team", opp_key="opp_team"))
    pp_by_player = build_slate_pp_lookup(slate_rows, CATEGORIES, team_normalize=_slate_team)
    slate_team_by_player, pos_by_player, roster_by_team = _slate_roster_maps(slate_path)

    names = _analyze._load_player_names(
        id_cache_path,
        [slate_path, _MLB / "step8_mlb_direction.csv"],
    )
    logs, _stats = _analyze._load_game_logs(cache_path, str(season) if season else None)
    if logs.empty:
        return {
            "sport": "mlb",
            "display_name": "MLB",
            "error": "No hitter game logs in cache",
            "teams": [],
            "categories": CATEGORIES,
            "matchups": {},
            "players_by_team_cat": {},
        }

    if season is None:
        season = int(_analyze._pick_default_season(logs, MIN_GAMES))
    logs = logs[logs["SEASON"].astype(str) == str(season)].copy()
    logs = _analyze._attach_player_names(logs, names)
    logs["PLAYER_NORM"] = logs["PLAYER_NAME"].map(_norm_name)

    valid = set(def_by_key.index)
    logs = logs[logs["TEAM"].map(defense_team_key).isin(valid)]
    logs["team_slate"] = _assign_player_teams(logs, slate_team_by_player=slate_team_by_player, season=season)
    logs = logs[logs["team_slate"].astype(str).str.len() > 0]

    teams_meta: list[dict] = []
    for r in defense.itertuples(index=False):
        sp_era = getattr(r, "SP_ERA", np.nan)
        teams_meta.append(
            {
                "def_key": r.def_key,
                "slate_abbr": r.slate_abbr,
                "name": str(r.TEAM_ABBREVIATION),
                "def_rank": int(r.OVERALL_DEF_RANK) if pd.notna(r.OVERALL_DEF_RANK) else None,
                "def_tier": str(getattr(r, "DEF_TIER", "") or ""),
                "sp_era": float(sp_era) if pd.notna(sp_era) else None,
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
            "opponent_name": str(opp_row.TEAM_ABBREVIATION) if opp_row is not None else opp_sl,
            "opponent_def_rank": int(opp_row.OVERALL_DEF_RANK)
            if opp_row is not None and pd.notna(opp_row.OVERALL_DEF_RANK)
            else mu.get("opp_def_rank"),
            "opponent_def_tier": str(getattr(opp_row, "DEF_TIER", "")) if opp_row is not None else str(mu.get("opp_def_tier") or ""),
            "opponent_sp_era": float(getattr(opp_row, "SP_ERA", np.nan))
            if opp_row is not None and pd.notna(getattr(opp_row, "SP_ERA", np.nan))
            else None,
            "team_def_rank": t["def_rank"],
            "team_def_tier": t["def_tier"],
        }

    players_by_key: dict[str, dict] = {}
    for cat in CATEGORIES:
        cid = cat["id"]
        logs[f"_stat_{cid}"] = _derive_stat(logs, cid)
        logs["_gs"] = _derive_stat(logs, "total_bases")
        agg = (
            logs.groupby(["PLAYER_NAME", "PLAYER_NORM", "team_slate"], as_index=False)
            .agg(
                season_avg=(f"_stat_{cid}", "mean"),
                games=(f"_stat_{cid}", "count"),
                game_score=("_gs", "mean"),
            )
        )
        agg = agg[agg["games"] >= MIN_GAMES]

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
            opp_era = mu_ui.get("opponent_sp_era")

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
                    "sp_era": opp_era,
                },
                "players": plist,
            }

    return {
        "sport": "mlb",
        "display_name": "MLB",
        "matchup_mode": "team",
        "prop_scope": "hitter",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season": season,
        "n_teams": n_teams,
        "elite_rank_cut": ELITE_RANK_CUT,
        "weak_rank_cut": max(10, int(np.ceil(n_teams * 0.65))),
        "opp_metric_label": "Opp pitching rank",
        "categories": CATEGORIES,
        "teams": teams_meta,
        "matchups": matchups_ui,
        "players_by_team_cat": players_by_key,
        "edge_legend": {
            "TOP_EDGE": "Positive PP edge (+1.5+) or strong avg vs weak pitching (high rank).",
            "OK_EDGE": "PP edge on board or team leader vs soft/average pitching.",
            "TOP_UNDER": "PP edge -2+ vs elite pitching, or historically fades vs elite D.",
            "OK_UNDER": "Negative PP edge vs elite pitching or bottom-3 producer vs elite D — lean UNDER.",
            "NEUTRAL": "No clear edge.",
            "AVOID": "Negative PP edge without elite matchup — skip OVER.",
        },
    }


def _resolve_slate(slate: Path | None) -> Path:
    if slate is not None and slate.exists():
        return slate
    for cand in (
        _REPO / "ui_runner/templates/slate_sport_mlb.json",
        _REPO / "mobile/www/slate_sport_mlb.json",
        _MLB / "step8_mlb_direction.csv",
        _MLB / "outputs/step8_mlb_direction.csv",
    ):
        if cand.exists():
            return cand
    return _REPO / "ui_runner/templates/slate_sport_mlb.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(_MLB / "mlb_stats_cache.csv"))
    ap.add_argument("--defense", default=str(_MLB / "mlb_defense_summary.csv"))
    ap.add_argument("--top3", default=str(_MLB / "data/mlb_hitter_top3_vs_defense.csv"))
    ap.add_argument("--id-cache", default=str(_MLB / "mlb_id_cache.csv"))
    ap.add_argument("--slate", default="")
    ap.add_argument("--season", type=int, default=None)
    args = ap.parse_args()

    slate = _resolve_slate(Path(args.slate) if args.slate else None)
    payload = build_payload(
        cache_path=Path(args.cache),
        defense_path=Path(args.defense),
        top3_path=Path(args.top3),
        slate_path=slate,
        id_cache_path=Path(args.id_cache),
        season=args.season,
    )

    out_paths = [
        _MLB / "data/mlb_matchup_edge.json",
        _REPO / "ui_runner/templates/mlb_matchup_edge.json",
        _REPO / "mobile/www/data/mlb_matchup_edge.json",
    ]
    text = json.dumps(payload, indent=2)
    blocks = len(payload.get("players_by_team_cat") or {})
    for p in out_paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"Wrote {p}")
    print(f"[mlb] blocks={blocks} -> mlb_matchup_edge.json")


if __name__ == "__main__":
    main()
