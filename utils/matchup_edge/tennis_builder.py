from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.matchup_edge.classify import classify_edge
from utils.matchup_edge.slate_io import load_slate_rows, norm_prop
from utils.matchup_edge.sports_config import SportMatchupConfig

_REPO = Path(__file__).resolve().parents[2]
_TENNIS_SCRIPTS = _REPO / "Sports" / "Tennis" / "scripts"


def _norm_key(s: object) -> str:
    if str(_TENNIS_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_TENNIS_SCRIPTS))
    from tennis_shared import norm_key  # noqa: WPS433

    return norm_key(str(s or ""))


def _resolve_tennis_slate(slate_path: Path | None) -> Path:
    if slate_path and slate_path.is_file():
        return slate_path
    candidates = (
        _REPO / "ui_runner/templates/slate_sport_tennis.json",
        _REPO / "mobile/www/slate_sport_tennis.json",
        _REPO / "Sports/Tennis/step8_tennis_direction_filled.csv",
        _REPO / "Sports/Tennis/step8_tennis_direction.csv",
        _REPO / "Sports/Tennis/outputs/step6_tennis_role_context.csv",
    )

    def _row_count(path: Path) -> int:
        try:
            if path.suffix.lower() == ".json":
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
                rows = raw.get("rows") or raw.get("picks") or []
                if isinstance(raw, list):
                    rows = raw
                return int(len(rows))
            if path.suffix.lower() == ".csv":
                # Subtract header row, but never return negative.
                return max(sum(1 for _ in path.open(encoding="utf-8-sig")) - 1, 0)
        except Exception:
            return 0
        return 0

    best_path: Path | None = None
    best_rows = -1
    for c in candidates:
        if not c.is_file():
            continue
        count = _row_count(c)
        if count > best_rows:
            best_rows = count
            best_path = c
    return best_path or (_REPO / "ui_runner/templates/slate_sport_tennis.json")


def _load_rankings() -> list[dict[str, Any]]:
    path = _REPO / "Sports/Tennis/cache/tennis_rankings.json"
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _load_match_cache() -> dict[str, list[dict[str, Any]]]:
    path = _REPO / "Sports/Tennis/cache/tennis_match_games.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_opp_rank(opp_name: str, rankings: list[dict[str, Any]]) -> float:
    if str(_TENNIS_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_TENNIS_SCRIPTS))
    from tennis_shared import resolve_opp_rank  # noqa: WPS433

    return float(resolve_opp_rank(opp_name, rankings))


def _enrich_opponents(rows: list[dict], match_cache: dict[str, list[dict[str, Any]]]) -> None:
    for row in rows:
        opp = str(row.get("opp_team") or row.get("opp") or "").strip()
        if opp and opp.upper() not in ("UNKNOWN_OPP", "UNK", ""):
            continue
        aid = str(row.get("espn_athlete_id") or row.get("espn_player_id") or "").strip()
        if aid and aid in match_cache and match_cache[aid]:
            row["opp_team"] = str(match_cache[aid][0].get("opponent") or "")
            row["opp"] = row["opp_team"]
            continue
        player = str(row.get("player") or row.get("player_name") or "").strip().lower()
        home = str(row.get("pp_home_team") or "").strip().lower()
        away = str(row.get("pp_away_team") or "").strip().lower()
        if player and home and away:
            if player in home:
                row["opp_team"] = str(row.get("pp_away_team") or "")
            elif player in away:
                row["opp_team"] = str(row.get("pp_home_team") or "")
            row["opp"] = row.get("opp_team", "")


def _player_matchups(rows: list[dict], rankings: list[dict[str, Any]]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        player = str(row.get("player") or row.get("player_name") or "").strip()
        if not player:
            continue
        opp = str(row.get("opp_team") or row.get("opp") or "").strip()
        if not opp or opp.upper() in ("UNKNOWN_OPP", "UNK"):
            continue
        pk = _norm_key(player) or player.upper()
        if pk in out:
            continue
        opp_rank = row.get("opponent_rank")
        if opp_rank is None or (isinstance(opp_rank, float) and np.isnan(opp_rank)):
            opp_rank = _resolve_opp_rank(opp, rankings)
        player_rank = row.get("player_atp_rank")
        out[pk] = {
            "player_name": player,
            "player_key": pk,
            "opponent_slate": _norm_key(opp) or opp.upper(),
            "opponent_name": opp,
            "opponent_def_rank": int(float(opp_rank)) if opp_rank is not None else None,
            "player_rank": int(float(player_rank)) if player_rank is not None else None,
        }
    return out


def _leaders_by_player(
    rows: list[dict],
    categories: tuple[dict, ...],
    *,
    top_n: int = 5,
) -> dict[str, list[dict]]:
    if not rows:
        return {}
    df = pd.DataFrame(rows)
    df["player"] = df.get("player", df.get("player_name", "")).astype(str)
    df["player_key"] = df["player"].map(_norm_key)
    prop_src = df.get("prop_norm", df.get("prop_type", df.get("prop", "")))
    df["prop_norm"] = prop_src.map(norm_prop)
    avg_src = df.get("season_avg")
    if avg_src is None or pd.to_numeric(avg_src, errors="coerce").notna().sum() == 0:
        for alt in ("stat_season_avg", "projection", "line", "stat_last10_avg", "stat_last5_avg"):
            if alt in df.columns:
                avg_src = df[alt]
                if pd.to_numeric(avg_src, errors="coerce").notna().sum() > 0:
                    break
    df["season_avg"] = pd.to_numeric(avg_src, errors="coerce")
    df = df[df["season_avg"].notna() & df["player"].astype(bool)]

    out: dict[str, list[dict]] = {}
    for cat in categories:
        cid = cat["id"]
        sub = df[df["prop_norm"] == cid]
        if sub.empty:
            sub = df[df["prop_norm"].astype(str).str.contains(cid[:4], na=False)]
        for pk, grp in sub.groupby("player_key", sort=False):
            if not pk:
                continue
            top = grp.nlargest(top_n, "season_avg")
            players = []
            for i, r in enumerate(top.itertuples(index=False), start=1):
                players.append(
                    {
                        "player": r.player,
                        "player_norm": pk,
                        "pos": str(getattr(r, "tour", "") or ""),
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
                out[f"{pk}|{cid}"] = players
    return out


def build_tennis_matchup_payload(
    cfg: SportMatchupConfig,
    *,
    slate_path: Path | None = None,
) -> dict[str, Any]:
    slate_file = _resolve_tennis_slate(slate_path)
    rows = load_slate_rows(slate_file)
    rankings = _load_rankings()
    match_cache = _load_match_cache()
    _enrich_opponents(rows, match_cache)

    matchups_raw = _player_matchups(rows, rankings)
    n_field = max(128, len(rankings) or 128)

    teams_meta = [
        {
            "def_key": pk,
            "slate_abbr": pk,
            "name": mu["player_name"],
            "def_rank": mu.get("player_rank"),
            "def_tier": "",
        }
        for pk, mu in sorted(matchups_raw.items(), key=lambda x: x[1]["player_name"])
    ]

    slate_blocks = _leaders_by_player(rows, cfg.categories, top_n=cfg.top_n)
    players_by_key: dict[str, Any] = {}

    for key, players in slate_blocks.items():
        if "|" not in key:
            continue
        pk, cid = key.split("|", 1)
        mu = matchups_raw.get(pk, {})
        opp_rank = mu.get("opponent_def_rank")
        cat = next((c for c in cfg.categories if c["id"] == cid), {"threshold": 1.0, "label": cid})
        threshold = float(cat.get("threshold", 1.0))

        enriched = []
        for p in players:
            edge, note = classify_edge(
                float(p["season_avg"]),
                threshold,
                opp_rank,
                n_field,
                elite_rank_cut=cfg.elite_rank_cut,
            )
            enriched.append({**p, "edge": edge, "notes": note})

        players_by_key[key] = {
            "team_slate": pk,
            "category": cid,
            "category_label": cat.get("label", cid),
            "threshold": threshold,
            "opponent": {
                "slate_abbr": mu.get("opponent_slate", ""),
                "name": mu.get("opponent_name", ""),
                "def_rank": opp_rank,
                "def_tier": "",
            },
            "players": enriched,
        }

    matchups_ui = {
        pk: {
            "opponent_slate": mu.get("opponent_slate", ""),
            "opponent_name": mu.get("opponent_name", ""),
            "opponent_def_rank": mu.get("opponent_def_rank"),
            "opponent_def_tier": "",
            "team_def_rank": mu.get("player_rank"),
            "team_def_tier": "",
        }
        for pk, mu in matchups_raw.items()
    }

    return {
        "sport": cfg.sport,
        "display_name": cfg.display_name,
        "matchup_mode": "player",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_teams": n_field,
        "elite_rank_cut": cfg.elite_rank_cut,
        "weak_rank_cut": max(80, int(np.ceil(n_field * 0.65))),
        "opp_metric_label": cfg.opp_metric_label,
        "categories": list(cfg.categories),
        "teams": teams_meta,
        "matchups": matchups_ui,
        "players_by_team_cat": players_by_key,
        "edge_legend": {
            "TOP_EDGE": "Avg at/above threshold vs weaker opponent (high ATP rank #).",
            "OK_EDGE": "Solid vs average-or-weaker opponent.",
            "NEUTRAL": "No clear edge.",
            "AVOID": "Top-ranked opponent with below-threshold production.",
        },
    }
