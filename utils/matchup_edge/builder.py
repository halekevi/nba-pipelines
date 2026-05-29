from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.matchup_edge.classify import classify_edge
from utils.matchup_edge.slate_io import (
    build_slate_pp_lookup,
    leaders_from_slate,
    load_slate_rows,
    lookup_pp_edge,
    merge_slate_rows,
    norm_player_name,
    norm_prop,
    pp_leaders_from_slate,
    tonight_matchups,
)
from utils.matchup_edge.sports_config import SPORT_CONFIGS, SportMatchupConfig
from utils.matchup_edge.team_aliases import cbb_defense_alias_keys, cbb_slate_to_defense_key, mlb_display_name
from utils.matchup_edge.tennis_builder import build_tennis_matchup_payload

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _norm_name(s: object) -> str:
    return norm_player_name(s)


def _team_norm(cfg: SportMatchupConfig, abbr: str) -> str:
    if cfg.team_normalize:
        return cfg.team_normalize(abbr)
    return str(abbr or "").strip().upper()


def _load_defense(cfg: SportMatchupConfig) -> pd.DataFrame:
    if not cfg.defense_path.is_file():
        return pd.DataFrame()
    d = pd.read_csv(cfg.defense_path, encoding="utf-8-sig")
    tcol = cfg.defense_team_col
    if tcol not in d.columns:
        return pd.DataFrame()
    d["def_key"] = d[tcol].astype(str).str.strip()
    d["slate_abbr"] = d["def_key"].map(lambda x: _team_norm(cfg, x))
    if cfg.defense_rank_col in d.columns:
        d["_def_rank"] = pd.to_numeric(d[cfg.defense_rank_col], errors="coerce")
    else:
        d["_def_rank"] = np.nan
    if cfg.defense_tier_col and cfg.defense_tier_col in d.columns:
        d["_def_tier"] = d[cfg.defense_tier_col].astype(str)
    else:
        d["_def_tier"] = ""
    name_col = cfg.defense_name_col if cfg.defense_name_col in d.columns else tcol
    d["_def_name"] = d[name_col].astype(str)
    if cfg.sport == "mlb":
        d["_def_name"] = d["def_key"].map(mlb_display_name)
    return d


def _attach_opponents_games(df: pd.DataFrame, team_col: str, game_col: str) -> pd.DataFrame:
    out = df.copy()
    out["opp_team"] = ""
    if game_col not in out.columns:
        return out
    for gid, grp in out.groupby(game_col, sort=False):
        teams = grp[team_col].astype(str).str.upper().unique().tolist()
        if len(teams) != 2:
            continue
        t0, t1 = teams[0], teams[1]
        mask = out[game_col] == gid
        out.loc[mask & (out[team_col].astype(str).str.upper() == t0), "opp_team"] = t1
        out.loc[mask & (out[team_col].astype(str).str.upper() == t1), "opp_team"] = t0
    return out


def _derive_basketball_stat(df: pd.DataFrame, cat: str) -> pd.Series:
    pts = pd.to_numeric(df.get("PTS", df.get("points")), errors="coerce")
    reb = pd.to_numeric(df.get("REB", df.get("totalRebounds")), errors="coerce")
    ast = pd.to_numeric(df.get("AST", df.get("assists")), errors="coerce")
    stl = pd.to_numeric(df.get("STL", df.get("steals")), errors="coerce")
    blk = pd.to_numeric(df.get("BLK", df.get("blocks")), errors="coerce")
    fg3 = pd.to_numeric(df.get("FG3M", df.get("threePointFieldGoalsMade", df.get("3PM"))), errors="coerce")
    if cat == "pts":
        return pts
    if cat == "reb":
        return reb
    if cat == "ast":
        return ast
    if cat == "fg3m":
        return fg3
    if cat == "stl":
        return stl
    if cat == "blk":
        return blk
    if cat == "pra":
        return pts + reb + ast
    return pd.Series([np.nan] * len(df), index=df.index)


def _derive_football_stat(df: pd.DataFrame, cat: str) -> pd.Series:
    pass_yds = pd.to_numeric(df.get("PASS_YDS", df.get("pass_yds")), errors="coerce")
    rush_yds = pd.to_numeric(df.get("RUSH_YDS", df.get("rush_yds")), errors="coerce")
    rec_yds = pd.to_numeric(df.get("REC_YDS", df.get("rec_yds")), errors="coerce")
    if cat == "pass_yds":
        return pass_yds
    if cat == "rush_yds":
        return rush_yds
    if cat == "rec_yds":
        return rec_yds
    return pd.Series([np.nan] * len(df), index=df.index)


def _load_cache_leaders(cfg: SportMatchupConfig) -> dict[str, list[dict]] | None:
    path = cfg.cache_path
    if not path or not path.is_file():
        return None

    sport = cfg.sport
    if sport in ("nba", "nba1h", "nba1q"):
        df = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
        df["TEAM"] = df["team"].astype(str).str.upper()
        df = _attach_opponents_games(df, "TEAM", "game_id")
        return _build_from_game_logs(cfg, df, _derive_basketball_stat, "player", "TEAM", "date")

    if sport == "wnba":
        sys_path = _REPO_ROOT / "Sports" / "WNBA"
        import sys

        if str(sys_path) not in sys.path:
            sys.path.insert(0, str(sys_path))
        from step4_fetch_player_stats import derive_stat  # noqa: WPS433

        df = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
        df["TEAM"] = df["TEAM"].astype(str).str.upper()
        df["MIN"] = pd.to_numeric(df["MIN"], errors="coerce")
        df = df[df["MIN"] >= cfg.min_mpg * 0.4]
        df = _attach_opponents_games(df, "TEAM", "event_id")

        def derive(df_in: pd.DataFrame, cat: str) -> pd.Series:
            return derive_stat(df_in, cat)

        return _build_from_game_logs(cfg, df, derive, "PLAYER_NAME", "TEAM", "game_date", player_norm_col="PLAYER_NORM")

    if sport == "nhl":
        df = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
        df["TEAM"] = df["Team"].astype(str).str.upper().str.split(",").str[0].str.strip()
        col_map = {
            "goals": "Goals",
            "assists": "Total Assists",
            "points": "Total Points",
            "shots": "Shots",
        }

        def derive_nhl(df_in: pd.DataFrame, cat: str) -> pd.Series:
            c = col_map.get(cat)
            if not c or c not in df_in.columns:
                return pd.Series([np.nan] * len(df_in), index=df_in.index)
            gp = pd.to_numeric(df_in.get("GP"), errors="coerce").replace(0, np.nan)
            return pd.to_numeric(df_in[c], errors="coerce") / gp

        # Season-rate table (no per-game opp): one row per player-team
        out: dict[str, list[dict]] = {}
        for cat in cfg.categories:
            cid = cat["id"]
            sub = df.copy()
            sub["_stat"] = derive_nhl(sub, cid)
            sub = sub[sub["_stat"].notna()]
            for team, grp in sub.groupby("TEAM", sort=False):
                top = grp.nlargest(cfg.top_n, "_stat")
                plist = []
                for i, (_, r) in enumerate(top.iterrows(), start=1):
                    stat_v = float(r["_stat"])
                    plist.append(
                        {
                            "player": r["Player"],
                            "player_norm": _norm_name(r["Player"]),
                            "pos": str(r.get("Position", "") or ""),
                            "rank_on_team": i,
                            "season_avg": round(stat_v, 2),
                            "game_score": round(stat_v * 3, 1),
                            "edge": "NEUTRAL",
                            "notes": "NST season rate per game",
                            "overperform_vs_weak": False,
                            "def_boost": None,
                        }
                    )
                if plist:
                    out[f"{_team_norm(cfg, team)}|{cid}"] = plist
        return out

    if sport == "cfb":
        df = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
        df["TEAM"] = df.get("team_abbr", df.get("team_id", "")).astype(str).str.upper()
        df["PLAYER"] = df.get("player", df.get("player_norm", "")).astype(str)
        # Offseason fallback: use last available season when present.
        if "SEASON" in df.columns:
            seasons = pd.to_numeric(df["SEASON"], errors="coerce")
            if seasons.notna().any():
                last_season = int(seasons.max())
                df = df[seasons == last_season]
        return _build_from_game_logs(cfg, df, _derive_football_stat, "PLAYER", "TEAM", "game_date", player_norm_col="player_norm")

    return None


def _build_from_game_logs(
    cfg: SportMatchupConfig,
    df: pd.DataFrame,
    derive_fn,
    player_col: str,
    team_col: str,
    date_col: str,
    *,
    player_norm_col: str | None = None,
) -> dict[str, list[dict]]:
    if df.empty:
        return {}
    pnorm = player_norm_col or player_col
    if pnorm not in df.columns:
        df[pnorm] = df[player_col].map(_norm_name)

    top3: dict[tuple[str, str, str], dict] = {}
    if cfg.top3_path and cfg.top3_path.is_file():
        t3 = pd.read_csv(cfg.top3_path, encoding="utf-8-sig")
        for r in t3.itertuples(index=False):
            top3[(_norm_name(getattr(r, "PLAYER_NORM", "")), str(r.team_slate).upper(), str(r.category).lower())] = {
                "overperform_vs_weak": bool(getattr(r, "overperform_vs_weak", False)),
                "def_boost": float(r.def_boost) if pd.notna(getattr(r, "def_boost", np.nan)) else None,
            }

    defense = _load_defense(cfg)
    def_by = defense.set_index("slate_abbr") if not defense.empty else pd.DataFrame()

    out_blocks: dict[str, list[dict]] = {}
    base = df.copy()
    for cat in cfg.categories:
        cid = cat["id"]
        df = base.copy()
        df[f"_s_{cid}"] = derive_fn(df, cid)
        agg = (
            df.groupby([player_col, pnorm, team_col], as_index=False)
            .agg(season_avg=(f"_s_{cid}", "mean"), games=(f"_s_{cid}", "count"))
        )
        if "MIN" in df.columns and cfg.sport not in ("cfb", "nfl"):
            mins = df.groupby([pnorm, team_col])["MIN"].mean().reset_index()
            agg = agg.merge(mins, on=[pnorm, team_col], how="left")
            agg = agg[pd.to_numeric(agg.get("MIN"), errors="coerce").fillna(0) >= cfg.min_mpg]
        agg["team_slate"] = agg[team_col].map(lambda t: _team_norm(cfg, t))

        for team_slate, grp in agg.groupby("team_slate", sort=False):
            top = grp.nlargest(cfg.top_n, "season_avg")
            plist: list[dict] = []
            for i, r in enumerate(top.itertuples(index=False), start=1):
                pnorm_val = _norm_name(getattr(r, pnorm, ""))
                hist = top3.get((pnorm_val, str(team_slate).upper(), cid), {})
                avg = float(r.season_avg)
                plist.append(
                    {
                        "player": getattr(r, player_col),
                        "player_norm": pnorm_val,
                        "pos": "",
                        "rank_on_team": i,
                        "season_avg": round(avg, 2),
                        "game_score": round(avg * 1.2, 1),
                        "edge": "NEUTRAL",
                        "notes": "",
                        "overperform_vs_weak": hist.get("overperform_vs_weak", False),
                        "def_boost": hist.get("def_boost"),
                    }
                )
            if plist:
                out_blocks[f"{team_slate}|{cid}"] = plist
    return out_blocks


_PIPELINE_SLATES: dict[str, list[str]] = {
    "nhl": [
        "Sports/NHL/step8_nhl_direction_clean.csv",
        "Sports/NHL/step1_nhl_props.csv",
    ],
    "mlb": ["Sports/MLB/step8_mlb_direction.csv"],
    "cbb": [
        "Sports/CBB/step5b_cbb.csv",
        "Sports/CBB/step3b_with_def_rankings_cbb.csv",
    ],
    "tennis": [
        "ui_runner/templates/slate_sport_tennis.json",
        "Sports/Tennis/step8_tennis_direction.csv",
    ],
    "soccer": [
        "ui_runner/templates/slate_sport_soccer.json",
        "mobile/www/slate_sport_soccer.json",
        "Sports/Soccer/step8_soccer_direction.csv",
    ],
}


def _slate_row_count(path: Path) -> int:
    try:
        if path.suffix.lower() == ".json":
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            rows = raw.get("rows") or raw.get("picks") or []
            if isinstance(raw, list):
                rows = raw
            return int(len(rows))
        if path.suffix.lower() == ".csv":
            return max(sum(1 for _ in path.open(encoding="utf-8-sig")) - 1, 0)
    except Exception:
        return 0
    return 0


def _load_soccer_slate_rows(slate_path: Path | None) -> list[dict]:
    """Merge UI JSON (outfield shots/assists) with step8 CSV (GK saves + season stats)."""
    if slate_path and slate_path.is_file():
        return load_slate_rows(slate_path)
    cat_ids = {c["id"] for c in SPORT_CONFIGS["soccer"].categories}
    paths = [
        _REPO_ROOT / "ui_runner/templates/slate_sport_soccer.json",
        _REPO_ROOT / "mobile/www/slate_sport_soccer.json",
        _REPO_ROOT / "Sports/Soccer/step8_soccer_direction.csv",
    ]
    return merge_slate_rows(paths, cat_ids=cat_ids)


def _resolve_slate_path(cfg: SportMatchupConfig, slate_path: Path | None) -> Path:
    if slate_path and slate_path.is_file():
        return slate_path
    candidates: list[Path] = [
        _REPO_ROOT / "ui_runner/templates" / f"slate_sport_{cfg.sport}.json",
        _REPO_ROOT / "mobile/www" / f"slate_sport_{cfg.sport}.json",
        _REPO_ROOT / f"Sports/{cfg.sport.upper()}/step8_{cfg.sport}_direction.csv",
    ]
    if cfg.sport == "wnba":
        candidates.insert(0, _REPO_ROOT / "Sports/WNBA/step8_wnba_direction.csv")
    for rel in _PIPELINE_SLATES.get(cfg.sport, []):
        candidates.append(_REPO_ROOT / rel)
    seen: set[Path] = set()
    unique: list[Path] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    best_path: Path | None = None
    best_rows = -1
    for c in unique:
        if not c.is_file():
            continue
        count = _slate_row_count(c)
        if count > best_rows:
            best_rows = count
            best_path = c
    if best_path:
        return best_path
    return unique[0] if unique else (_REPO_ROOT / "ui_runner/templates" / f"slate_sport_{cfg.sport}.json")


def _extend_def_lookup(cfg: SportMatchupConfig, def_lookup: dict[str, dict]) -> None:
    if cfg.sport != "cbb":
        return
    for abbr, sr_name in cbb_defense_alias_keys().items():
        sr_row = def_lookup.get(sr_name.upper()) or def_lookup.get(sr_name)
        if sr_row:
            def_lookup[abbr] = sr_row
            def_lookup[abbr.upper()] = sr_row


def _def_row_for_team(cfg: SportMatchupConfig, def_lookup: dict[str, dict], team: str) -> dict | None:
    t = str(team or "").upper()
    row = def_lookup.get(t) or def_lookup.get(_team_norm(cfg, t))
    if row:
        return row
    if cfg.sport == "cbb":
        sr = cbb_slate_to_defense_key(t)
        return def_lookup.get(sr.upper()) or def_lookup.get(sr)
    return None


def _resolve_wnba_slate(slate_path: Path | None) -> Path:
    if slate_path and slate_path.is_file():
        return slate_path
    for c in (
        _REPO_ROOT / "ui_runner/templates/slate_sport_wnba.json",
        _REPO_ROOT / "mobile/www/slate_sport_wnba.json",
        _REPO_ROOT / "Sports/WNBA/step8_wnba_direction.csv",
    ):
        if c.is_file():
            return c
    return _REPO_ROOT / "ui_runner/templates/slate_sport_wnba.json"


def _merge_player_blocks(
    cache_blocks: dict[str, list[dict]],
    slate_blocks: dict[str, list[dict]],
    *,
    top_n: int,
) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    keys = set(cache_blocks) | set(slate_blocks)
    for key in keys:
        merged: list[dict] = []
        seen: set[str] = set()
        for src in (cache_blocks.get(key, []), slate_blocks.get(key, [])):
            for p in src:
                pn = str(p.get("player_norm") or _norm_name(p.get("player", ""))).lower()
                if not pn or pn in seen:
                    continue
                seen.add(pn)
                merged.append(p)
                if len(merged) >= top_n:
                    break
            if len(merged) >= top_n:
                break
        if merged:
            for i, p in enumerate(merged, start=1):
                p["rank_on_team"] = i
            out[key] = merged
    return out


def _build_wnba_matchup_payload(slate_path: Path | None) -> dict[str, Any]:
    script = _REPO_ROOT / "Sports" / "WNBA" / "scripts" / "build_wnba_matchup_edge_json.py"
    spec = importlib.util.spec_from_file_location("build_wnba_matchup_edge_json", script)
    if spec is None or spec.loader is None:
        return {"sport": "wnba", "error": f"Missing WNBA builder: {script}", "teams": [], "categories": [], "matchups": {}, "players_by_team_cat": {}}
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    wnba = _REPO_ROOT / "Sports" / "WNBA"
    slate_file = _resolve_wnba_slate(slate_path)
    return mod.build_payload(
        cache_path=wnba / "wnba_espn_cache.csv",
        defense_path=wnba / "wnba_defense_summary.csv",
        top3_path=wnba / "data" / "wnba_top3_vs_defense.csv",
        slate_path=slate_file,
    )


def build_matchup_payload(
    sport: str,
    *,
    slate_path: Path | None = None,
) -> dict[str, Any]:
    cfg = SPORT_CONFIGS.get(sport.lower())
    if not cfg or not cfg.enabled:
        return {
            "sport": sport,
            "error": f"Matchup edge not available for {sport}",
            "teams": [],
            "categories": [],
            "matchups": {},
            "players_by_team_cat": {},
        }

    if cfg.matchup_mode == "player":
        return build_tennis_matchup_payload(cfg, slate_path=slate_path)

    if cfg.sport == "wnba":
        return _build_wnba_matchup_payload(slate_path)

    defense = _load_defense(cfg)
    if defense.empty:
        return {
            "sport": cfg.sport,
            "display_name": cfg.display_name,
            "error": f"Defense file missing: {cfg.defense_path}",
            "teams": [],
            "categories": list(cfg.categories),
            "matchups": {},
            "players_by_team_cat": {},
        }

    n_teams = int(defense["_def_rank"].notna().sum()) or max(len(defense), 10)
    if cfg.sport == "soccer":
        slate_rows = _load_soccer_slate_rows(slate_path)
        slate_file = _REPO_ROOT / "ui_runner/templates/slate_sport_soccer.json"
    else:
        slate_file = _resolve_slate_path(cfg, slate_path)
        slate_rows = load_slate_rows(slate_file)
    matchups_raw = tonight_matchups(slate_rows)

    # Map defense keys (soccer pp_name etc.)
    def_lookup: dict[str, dict] = {}
    for _, r in defense.iterrows():
        rec = {
            "_def_name": r.get("_def_name", ""),
            "_def_rank": r.get("_def_rank"),
            "_def_tier": r.get("_def_tier", ""),
            "slate_abbr": r.get("slate_abbr", ""),
            "def_key": r.get("def_key", ""),
        }
        def_lookup[str(rec["slate_abbr"]).upper()] = rec
        def_lookup[str(rec["def_key"]).upper()] = rec
    _extend_def_lookup(cfg, def_lookup)

    matchups_ui: dict[str, dict] = {}
    teams_on_slate = set(matchups_raw.keys())
    for t in teams_on_slate:
        mu = matchups_raw[t]
        opp = str(mu.get("opp_slate", "")).upper()
        opp_row = _def_row_for_team(cfg, def_lookup, opp)
        team_row = _def_row_for_team(cfg, def_lookup, t)
        matchups_ui[t] = {
            "opponent_slate": opp,
            "opponent_name": str(opp_row["_def_name"]) if opp_row else opp,
            "opponent_def_rank": int(opp_row["_def_rank"])
            if opp_row and pd.notna(opp_row.get("_def_rank"))
            else mu.get("opp_def_rank"),
            "opponent_def_tier": str(opp_row["_def_tier"]) if opp_row else str(mu.get("opp_def_tier") or ""),
            "team_def_rank": int(team_row["_def_rank"])
            if team_row and pd.notna(team_row.get("_def_rank"))
            else None,
            "team_def_tier": str(team_row["_def_tier"]) if team_row else "",
        }

    teams_meta = []
    for _, r in defense.iterrows():
        ab = str(r["slate_abbr"]).upper()
        if teams_on_slate and ab not in teams_on_slate and str(r["def_key"]).upper() not in teams_on_slate:
            continue
        teams_meta.append(
            {
                "def_key": str(r["def_key"]),
                "slate_abbr": ab,
                "name": str(r["_def_name"]),
                "def_rank": int(r["_def_rank"]) if pd.notna(r["_def_rank"]) else None,
                "def_tier": str(r["_def_tier"]),
            }
        )
    if not teams_meta:
        teams_meta = [
            {
                "def_key": t,
                "slate_abbr": t,
                "name": t,
                "def_rank": None,
                "def_tier": "",
            }
            for t in sorted(teams_on_slate)
        ]

    slate_blocks = pp_leaders_from_slate(
        slate_rows,
        list(cfg.categories),
        top_n=cfg.top_n,
        team_normalize=cfg.team_normalize,
    )
    cache_blocks = _load_cache_leaders(cfg) or {}
    player_blocks = _merge_player_blocks(cache_blocks, slate_blocks, top_n=cfg.top_n)
    pp_lookup = build_slate_pp_lookup(
        slate_rows,
        list(cfg.categories),
        team_normalize=cfg.team_normalize,
    )

    players_by_key: dict[str, Any] = {}
    for key, players in player_blocks.items():
        if "|" not in key:
            continue
        team_slate, cid = key.split("|", 1)
        if teams_on_slate and team_slate not in teams_on_slate:
            continue
        mu = matchups_ui.get(team_slate, {})
        opp_rank = mu.get("opponent_def_rank")
        cat = next((c for c in cfg.categories if c["id"] == cid), {"threshold": 1.0})
        threshold = float(cat.get("threshold", 1.0))

        enriched = []
        for i, p in enumerate(players, start=1):
            hist = {"overperform_vs_weak": p.get("overperform_vs_weak"), "def_boost": p.get("def_boost")}
            pp = lookup_pp_edge(
                pp_lookup,
                player=str(p.get("player") or ""),
                team=team_slate,
                cat_id=cid,
                player_norm=p.get("player_norm"),
                slate_teams=teams_on_slate or None,
            )
            rank_on_team = int(p.get("rank_on_team") or i)
            edge, note = classify_edge(
                float(p["season_avg"]),
                threshold,
                opp_rank,
                n_teams,
                elite_rank_cut=cfg.elite_rank_cut,
                hist=hist,
                cat_id=cid,
                pp_line=pp.get("pp_line"),
                pp_edge=pp.get("pp_edge"),
                rank_on_team=rank_on_team,
            )
            pp_edge_val = pp.get("pp_edge")
            enriched.append(
                {
                    **p,
                    "edge": edge,
                    "notes": note,
                    "pp_line": pp.get("pp_line"),
                    "pp_edge": round(float(pp_edge_val), 2) if pp_edge_val is not None else None,
                }
            )

        players_by_key[key] = {
            "team_slate": team_slate,
            "category": cid,
            "category_label": cat.get("label", cid),
            "threshold": threshold,
            "opponent": {
                "slate_abbr": mu.get("opponent_slate", ""),
                "name": mu.get("opponent_name", ""),
                "def_rank": opp_rank,
                "def_tier": mu.get("opponent_def_tier", ""),
            },
            "players": enriched,
        }

    slate_note = ""
    if not slate_rows and not players_by_key:
        slate_note = "No slate rows — run sport pipeline or publish slate_sport JSON."

    return {
        "sport": cfg.sport,
        "display_name": cfg.display_name,
        "matchup_mode": cfg.matchup_mode,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "slate_note": slate_note,
        "n_teams": n_teams,
        "elite_rank_cut": cfg.elite_rank_cut,
        "weak_rank_cut": max(10, int(np.ceil(n_teams * 0.65))),
        "opp_metric_label": cfg.opp_metric_label,
        "categories": list(cfg.categories),
        "teams": teams_meta,
        "matchups": matchups_ui,
        "players_by_team_cat": players_by_key,
        "edge_legend": {
            "TOP_EDGE": "Positive PP edge (+1.5+) or strong avg vs soft defense (high rank = weak).",
            "OK_EDGE": "PP edge on board or solid production vs average-or-softer defense.",
            "NEUTRAL": "No clear edge.",
            "AVOID": "Negative PP edge or elite defense with weak production.",
        },
    }


def write_payload(payload: dict[str, Any], sport: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{sport}_matchup_edge.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def publish_payload(payload: dict[str, Any], sport: str, repo_root: Path | None = None) -> list[Path]:
    root = repo_root or _REPO_ROOT
    paths: list[Path] = []
    targets = [
        root / "ui_runner/templates" / f"{sport}_matchup_edge.json",
        root / "mobile/www/data" / f"{sport}_matchup_edge.json",
    ]
    if sport == "wnba":
        targets.append(root / "Sports/WNBA/data/wnba_matchup_edge.json")
    text = json.dumps(payload, indent=2)
    for p in targets:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        paths.append(p)
    return paths
