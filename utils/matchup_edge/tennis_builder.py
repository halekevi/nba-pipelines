from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.matchup_edge.slate_io import load_slate_rows, norm_prop, build_slate_pp_lookup, lookup_pp_edge
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


def _resolve_player_rank(player_name: str, rankings: list[dict[str, Any]]) -> int | None:
    """Player's own ATP/WTA rank from tennis_rankings.json (player_key match)."""
    if not str(player_name or "").strip():
        return None
    pk = _norm_key(player_name)
    if not pk:
        return None
    for r in rankings:
        if r.get("player_key") == pk:
            rank = r.get("rank")
            if rank is not None:
                return int(rank)
    best: int | None = None
    for r in rankings:
        rk = r.get("player_key") or ""
        if pk and rk and (pk in rk or rk in pk):
            rank = int(r.get("rank") or 999)
            if best is None or rank < best:
                best = rank
    return best


# ATP rank → display tier (lower rank # = stronger player)
_ATP_TIER_ELITE_MAX = 10
_ATP_TIER_ABOVE_AVG_MAX = 25
_ATP_TIER_AVG_MAX = 50
_ATP_TIER_BELOW_AVG_MAX = 100


def _atp_tier_from_rank(rank: int | float | None) -> str:
    """Map ATP/WTA rank to five tiers for YOUR RANK / OPP ATP RANK cards."""
    if rank is None:
        return ""
    try:
        r = int(float(rank))
    except (TypeError, ValueError):
        return ""
    if r <= _ATP_TIER_ELITE_MAX:
        return "Elite"
    if r <= _ATP_TIER_ABOVE_AVG_MAX:
        return "Above Avg"
    if r <= _ATP_TIER_AVG_MAX:
        return "Avg"
    if r <= _ATP_TIER_BELOW_AVG_MAX:
        return "Below Avg"
    return "Weak"


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
        if player_rank is None or (isinstance(player_rank, float) and np.isnan(player_rank)):
            player_rank = _resolve_player_rank(player, rankings)
        opp_rank_i = int(float(opp_rank)) if opp_rank is not None else None
        player_rank_i = int(float(player_rank)) if player_rank is not None else None
        out[pk] = {
            "player_name": player,
            "player_key": pk,
            "opponent_slate": _norm_key(opp) or opp.upper(),
            "opponent_name": opp,
            "opponent_def_rank": opp_rank_i,
            "opponent_def_tier": _atp_tier_from_rank(opp_rank_i),
            "player_rank": player_rank_i,
            "player_tier": _atp_tier_from_rank(player_rank_i),
        }
    return out


def _classify_tennis_edge(
    season_avg: float,
    threshold: float,
    opp_rank: float | None,
    *,
    cat_id: str = "",
    pp_line: float | None = None,
    pp_edge: float | None = None,
    elite_rank_cut: int = 25,
    weak_rank_cut: int = 100,
) -> tuple[str, str]:
    """Tennis ATP rank: lower # = stronger opponent (inverse of team def rank)."""
    rank = float(opp_rank) if opp_rank is not None and not (isinstance(opp_rank, float) and np.isnan(opp_rank)) else np.nan
    eff = threshold * 0.55 if cat_id in ("aces", "double_faults", "break_points_won") else threshold
    rank_lbl = f"#{int(rank)}" if not np.isnan(rank) else "?"

    if pp_edge is not None and not (isinstance(pp_edge, float) and np.isnan(pp_edge)):
        pe = float(pp_edge)
        if not np.isnan(rank) and rank <= elite_rank_cut:
            if pe >= 2.0:
                return "OK_EDGE", f"PP edge +{pe:.1f} but elite opponent ({rank_lbl}) — tough matchup."
            if pe >= 1.0:
                return "NEUTRAL", f"PP edge +{pe:.1f} vs elite opponent ({rank_lbl}) — proceed with caution."
            if pe <= -2.0:
                return "AVOID", f"PP edge {pe:.1f} vs elite opponent ({rank_lbl})."
            if pe < 0:
                return "AVOID", f"Negative PP edge vs elite opponent ({rank_lbl})."
        if not np.isnan(rank) and rank >= weak_rank_cut:
            if pe >= 1.0:
                return "TOP_EDGE", f"PP edge +{pe:.1f} vs weak opponent ({rank_lbl})."
            if pe >= 0.5:
                return "OK_EDGE", f"PP edge +{pe:.1f} vs weak opponent ({rank_lbl})."
        if pe >= 2.0:
            return "TOP_EDGE", f"PP edge +{pe:.1f} on board tonight."
        if pe >= 1.0:
            return "OK_EDGE", f"PP edge +{pe:.1f} on board tonight."
        if pe <= -2.0:
            return "AVOID", f"PP edge {pe:.1f} on board — lean UNDER or skip OVER."

    if not np.isnan(rank) and rank <= elite_rank_cut and season_avg < eff * 0.9:
        return "AVOID", f"Elite opponent ({rank_lbl}); production below threshold."
    if not np.isnan(rank) and rank >= weak_rank_cut and season_avg >= eff:
        return "TOP_EDGE", f"Strong avg vs weak opponent ({rank_lbl})."
    if not np.isnan(rank) and elite_rank_cut < rank < weak_rank_cut and season_avg >= eff * 0.85:
        return "OK_EDGE", f"Solid vs average opponent ({rank_lbl})."
    if not np.isnan(rank) and rank <= elite_rank_cut:
        return "NEUTRAL", f"Elite opponent ({rank_lbl}) — no clear OVER edge."
    return "NEUTRAL", "No strong matchup edge either way."


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
            best = grp.sort_values("season_avg", ascending=False).iloc[0]
            players = [
                {
                    "player": best.player,
                    "player_norm": pk,
                    "pos": str(getattr(best, "tour", "") or ""),
                    "rank_on_team": 1,
                    "season_avg": round(float(best.season_avg), 2),
                    "game_score": round(float(best.season_avg), 1),
                    "edge": "NEUTRAL",
                    "notes": "From slate season average",
                    "overperform_vs_weak": False,
                    "def_boost": None,
                }
            ]
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
    weak_rank_cut = 100

    teams_meta = [
        {
            "def_key": pk,
            "slate_abbr": pk,
            "name": mu["player_name"],
            "def_rank": mu.get("player_rank"),
            "def_tier": mu.get("player_tier") or "",
        }
        for pk, mu in sorted(matchups_raw.items(), key=lambda x: x[1]["player_name"])
    ]

    slate_blocks = _leaders_by_player(rows, cfg.categories, top_n=cfg.top_n)
    pp_lookup = build_slate_pp_lookup(rows, list(cfg.categories), player_mode=True)
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
        for i, p in enumerate(players, start=1):
            pp = lookup_pp_edge(
                pp_lookup,
                player=str(p.get("player") or ""),
                team=pk,
                cat_id=cid,
                player_norm=p.get("player_norm"),
                player_mode=True,
            )
            edge, note = _classify_tennis_edge(
                float(p["season_avg"]),
                threshold,
                opp_rank,
                cat_id=cid,
                pp_line=pp.get("pp_line"),
                pp_edge=pp.get("pp_edge"),
                elite_rank_cut=cfg.elite_rank_cut,
                weak_rank_cut=weak_rank_cut,
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
            "team_slate": pk,
            "category": cid,
            "category_label": cat.get("label", cid),
            "threshold": threshold,
            "opponent": {
                "slate_abbr": mu.get("opponent_slate", ""),
                "name": mu.get("opponent_name", ""),
                "def_rank": opp_rank,
                "def_tier": mu.get("opponent_def_tier") or "",
            },
            "players": enriched,
        }

    matchups_ui = {
        pk: {
            "opponent_slate": mu.get("opponent_slate", ""),
            "opponent_name": mu.get("opponent_name", ""),
            "opponent_def_rank": mu.get("opponent_def_rank"),
            "opponent_def_tier": mu.get("opponent_def_tier") or "",
            "team_def_rank": mu.get("player_rank"),
            "team_def_tier": mu.get("player_tier") or "",
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
        "weak_rank_cut": weak_rank_cut,
        "opp_metric_label": cfg.opp_metric_label,
        "categories": list(cfg.categories),
        "teams": teams_meta,
        "matchups": matchups_ui,
        "players_by_team_cat": players_by_key,
        "edge_legend": {
            "TOP_EDGE": "PP edge +1.0+ or strong avg vs weak opponent (ATP rank 100+).",
            "OK_EDGE": "Positive PP edge or solid vs average opponent (rank 26–99).",
            "NEUTRAL": "No clear edge, or elite opponent with only modest PP edge.",
            "AVOID": "Negative PP edge or elite opponent (rank ≤25) with weak production.",
        },
    }
