"""
Flask blueprint: /api/hot-players and /api/player-consistency
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from flask import Blueprint, jsonify, request

consistency_bp = Blueprint("consistency", __name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_PATH = REPO_ROOT / "data" / "cache" / "player_consistency.json"

_cache: dict | None = None
_cache_mtime: float = 0.0


def load_consistency_cache() -> dict:
    global _cache, _cache_mtime
    try:
        mtime = CACHE_PATH.stat().st_mtime
        if _cache is None or mtime != _cache_mtime:
            with open(CACHE_PATH, encoding="utf-8") as f:
                _cache = json.load(f)
            _cache_mtime = mtime
    except FileNotFoundError:
        _cache = {"players": [], "generated_at": None}
    return _cache


def _filter_players(
    players: list[dict],
    sport: str | None,
    tier: str | None,
    today_only: bool,
    limit: int,
    direction: str | None,
) -> list[dict]:
    out = players
    if sport:
        out = [p for p in out if str(p.get("sport", "")).upper() == sport.upper()]
    if tier:
        out = [p for p in out if p.get("tier") == tier.lower()]
    if today_only:
        out = [p for p in out if p.get("on_today_slate")]
    if direction:
        d = direction.upper()
        out = [p for p in out if p.get("direction") == d or p.get("direction") == "BOTH"]
    return out[:limit]


@consistency_bp.route("/api/player-consistency")
def player_consistency():
    sport = request.args.get("sport")
    tier = request.args.get("tier")
    today_only = request.args.get("today_only", "0") == "1"
    direction = request.args.get("direction")
    limit = min(int(request.args.get("limit", 50)), 100)
    sort_key = request.args.get("sort", "hit_rate")

    data = load_consistency_cache()
    players = list(data.get("players", []))
    players = _filter_players(players, sport, tier, today_only, 9999, direction)

    if sort_key == "balance_score":
        players = sorted(
            [p for p in players if p.get("balance_score") is not None],
            key=lambda p: (-float(p["balance_score"]), -float(p.get("hit_rate", 0))),
        )
    elif sort_key == "total":
        players = sorted(players, key=lambda p: -int(p.get("total", 0)))
    else:
        players = sorted(players, key=lambda p: (-float(p.get("hit_rate", 0)), -int(p.get("total", 0))))

    players = players[:limit]
    for i, p in enumerate(players, 1):
        p["rank"] = i

    return jsonify(
        {
            "generated_at": data.get("generated_at"),
            "filters": {
                "sport": sport,
                "tier": tier,
                "today_only": today_only,
                "direction": direction,
                "sort": sort_key,
                "limit": limit,
            },
            "count": len(players),
            "players": players,
        }
    )


@consistency_bp.route("/api/hot-players")
def hot_players():
    sport = request.args.get("sport")
    limit = min(int(request.args.get("limit", 5)), 20)

    data = load_consistency_cache()
    players = data.get("players", [])

    today = [p for p in players if p.get("on_today_slate") and p.get("tier") in ("high", "medium")]
    if not today:
        today = [p for p in players if p.get("tier") == "high"]

    if sport:
        today = [p for p in today if str(p.get("sport", "")).upper() == sport.upper()]

    by_sport: dict[str, list] = {}
    for p in sorted(today, key=lambda x: -float(x.get("hit_rate", 0))):
        s = str(p.get("sport", "?"))
        if s not in by_sport:
            by_sport[s] = []
        if len(by_sport[s]) < limit:
            by_sport[s].append(p)

    return jsonify(
        {
            "date": str(date.today()),
            "generated_at": data.get("generated_at"),
            "sports": by_sport,
            "total_featured": sum(len(v) for v in by_sport.values()),
        }
    )
