#!/usr/bin/env python3
"""
Snapshot Hot Players each slate day and grade featured legs after games (next-day grader).

Artifacts:
  data/hot_players/snapshots/hot_players_{date}.json
  data/hot_players/graded/hot_players_graded_{date}.json
  data/hot_players/track_record.json  (+ ui_runner/data mirror for deploy)

CLI:
  py scripts/hot_players_tracker.py snapshot [--date YYYY-MM-DD] [--limit 8]
  py scripts/hot_players_tracker.py grade [--date YYYY-MM-DD]
  py scripts/hot_players_tracker.py rebuild-summary
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.build_player_consistency_ui import (  # noqa: E402
    SPORT_ALIASES,
    _norm_name,
    _normalize_slate_sport,
    _prop_match_key,
    load_today_slate,
)

SNAPSHOT_DIR = REPO_ROOT / "data" / "hot_players" / "snapshots"
GRADED_DIR = REPO_ROOT / "data" / "hot_players" / "graded"
TRACK_RECORD_PATH = REPO_ROOT / "data" / "hot_players" / "track_record.json"
UI_TRACK_RECORD_PATH = REPO_ROOT / "ui_runner" / "data" / "hot_players_track_record.json"
MOBILE_TRACK_RECORD_PATH = REPO_ROOT / "mobile" / "www" / "hot_players_track_record.json"

SLATE_JSON_PATHS = (
    REPO_ROOT / "ui_runner" / "templates" / "slate_latest.json",
    REPO_ROOT / "mobile" / "www" / "slate_latest.json",
)

GRADED_PROPS_PATHS = (
    REPO_ROOT / "ui_runner" / "templates",
    REPO_ROOT / "mobile" / "www",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_consistency_players() -> list[dict]:
    cache_path = load_consistency_cache_path()
    if not cache_path.is_file():
        return []
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return list(data.get("players") or [])


def load_consistency_cache_path() -> Path:
    candidates = (
        REPO_ROOT / "data" / "cache" / "player_consistency.json",
        REPO_ROOT / "ui_runner" / "data" / "player_consistency.json",
    )
    existing = [p for p in candidates if p.is_file()]
    return max(existing, key=lambda p: p.stat().st_mtime) if existing else candidates[0]


def _player_on_slate(p: dict, slate_pairs: set[tuple[str, str]]) -> bool:
    if not slate_pairs:
        return False
    name = str(p.get("player") or "").strip().lower()
    sport = str(p.get("sport") or "").upper().strip()
    return bool(name and sport and (name, sport) in slate_pairs)


def _resolve_display_prop(player: dict) -> dict | None:
    dp = player.get("display_prop")
    if isinstance(dp, dict) and dp.get("prop_type"):
        return dp
    bp = player.get("best_prop")
    if isinstance(bp, dict) and bp.get("prop_type"):
        return bp
    for alt in player.get("best_props") or []:
        if isinstance(alt, dict) and alt.get("prop_type"):
            return alt
    return None


def featured_hot_players(slate_date: str, limit: int = 8) -> list[dict]:
    """Same pool as /api/hot-players for a given ET slate date."""
    _slate_names, slate_pairs = load_today_slate() if slate_date == str(date.today()) else _load_slate_pairs_for_date(slate_date)
    players = _load_consistency_players()
    pool = [
        p
        for p in players
        if p.get("tier") in ("high", "medium") and _player_on_slate(p, slate_pairs)
    ]
    by_sport: dict[str, list[dict]] = {}
    featured: list[dict] = []
    for p in sorted(pool, key=lambda x: -float(x.get("hit_rate", 0))):
        sport = str(p.get("sport", "?"))
        if sport not in by_sport:
            by_sport[sport] = []
        if len(by_sport[sport]) >= limit:
            continue
        by_sport[sport].append(p)
        dp = _resolve_display_prop(p)
        featured.append(
            {
                "player": p.get("player"),
                "sport": sport,
                "tier": p.get("tier"),
                "hit_rate": p.get("hit_rate"),
                "display_prop": dp,
            }
        )
    return featured


def _load_slate_pairs_for_date(slate_date: str) -> tuple[set[str], set[tuple[str, str]]]:
    """Rebuild (name, sport) pairs from archived snapshot or slate JSON for a past date."""
    snap = snapshot_path(slate_date)
    if snap.is_file():
        try:
            data = json.loads(snap.read_text(encoding="utf-8"))
            pairs = set()
            names = set()
            for row in data.get("featured") or []:
                n = str(row.get("player") or "").strip()
                s = _normalize_slate_sport(str(row.get("sport") or ""))
                if n and s:
                    names.add(n)
                    pairs.add((_norm_name(n), s))
            if pairs:
                return names, pairs
        except (OSError, json.JSONDecodeError):
            pass
    return _slate_pairs_from_json_files(slate_date)


def _slate_pairs_from_json_files(slate_date: str) -> tuple[set[str], set[tuple[str, str]]]:
    from scripts.build_player_consistency_ui import _players_from_slate_json

    players: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for path in SLATE_JSON_PATHS:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        n, p = _players_from_slate_json(data, slate_date)
        players.update(n)
        pairs.update(p)
    return players, pairs


def _iter_slate_rows(slate_date: str) -> list[dict]:
    rows: list[dict] = []
    td = str(slate_date)[:10]
    for path in SLATE_JSON_PATHS:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sports = data.get("sports") or {}
        if isinstance(sports, dict):
            for _sk, sport_rows in sports.items():
                if isinstance(sport_rows, list):
                    rows.extend(sport_rows)
        elif isinstance(data.get("rows"), list):
            rows.extend(data["rows"])
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        gd = str(row.get("game_date") or "").strip()[:10]
        if gd and gd != td:
            continue
        if not gd:
            gt = str(row.get("game_time") or "").strip()
            m = re.match(r"^(\d{4}-\d{2}-\d{2})", gt)
            if m and m.group(1) != td:
                continue
        out.append(row)
    return out


def _normalize_sport_label(raw: str) -> str:
    key = str(raw or "").strip().lower()
    return SPORT_ALIASES.get(key, str(raw or "").strip().upper())


def _find_slate_leg(
    player: str,
    sport: str,
    prop_type: str,
    direction: str,
    slate_date: str,
) -> dict | None:
    want_dir = str(direction or "").upper().strip()
    want_prop = _prop_match_key(prop_type)
    want_name = _norm_name(player)
    want_sport = _normalize_sport_label(sport)
    matches: list[dict] = []
    for row in _iter_slate_rows(slate_date):
        if _norm_name(str(row.get("player") or "")) != want_name:
            continue
        row_sport = _normalize_sport_label(str(row.get("sport") or sport))
        if row_sport != want_sport:
            continue
        row_prop = _prop_match_key(str(row.get("prop") or row.get("prop_type") or ""))
        if row_prop != want_prop:
            continue
        row_dir = str(row.get("dir") or row.get("direction") or "").upper().strip()
        if want_dir and row_dir and row_dir != want_dir:
            continue
        matches.append(row)
    if not matches:
        return None
    # Prefer Standard over Goblin when multiple lines exist
    matches.sort(
        key=lambda r: (
            0 if str(r.get("pick_type") or "").lower() == "standard" else 1,
            -float(r.get("rank_score") or 0),
        )
    )
    pick = matches[0]
    line = pick.get("line")
    try:
        line_f = float(line) if line is not None and str(line).strip() != "" else None
    except (TypeError, ValueError):
        line_f = None
    return {
        "line": line_f,
        "pick_type": pick.get("pick_type"),
        "team": pick.get("team"),
        "opp": pick.get("opp"),
    }


def snapshot_path(slate_date: str) -> Path:
    return SNAPSHOT_DIR / f"hot_players_{slate_date[:10]}.json"


def graded_path(slate_date: str) -> Path:
    return GRADED_DIR / f"hot_players_graded_{slate_date[:10]}.json"


def cmd_snapshot(slate_date: str, limit: int) -> int:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    featured = featured_hot_players(slate_date, limit=limit)
    enriched: list[dict] = []
    for row in featured:
        dp = row.get("display_prop") or {}
        leg = _find_slate_leg(
            str(row.get("player") or ""),
            str(row.get("sport") or ""),
            str(dp.get("prop_type") or ""),
            str(dp.get("direction") or ""),
            slate_date,
        )
        item = dict(row)
        if leg:
            item["slate_line"] = leg.get("line")
            item["slate_pick_type"] = leg.get("pick_type")
            item["team"] = leg.get("team")
            item["opp"] = leg.get("opp")
        enriched.append(item)

    payload = {
        "slate_date": slate_date[:10],
        "snapshotted_at": _utc_now_iso(),
        "limit_per_sport": limit,
        "featured_count": len(enriched),
        "featured": enriched,
    }
    out = snapshot_path(slate_date)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[hot-players] snapshot {len(enriched)} featured -> {out}")
    return 0


def _load_graded_props(slate_date: str) -> list[dict]:
    td = slate_date[:10]
    fname = f"graded_props_{td}.json"
    for base in GRADED_PROPS_PATHS:
        path = base / fname
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return list(data.get("props") or data.get("rows") or [])
            except (OSError, json.JSONDecodeError):
                continue
    return []


def _match_graded_row(
    player: str,
    sport: str,
    prop_type: str,
    direction: str,
    graded_rows: list[dict],
) -> dict | None:
    want_name = _norm_name(player)
    want_sport = _normalize_sport_label(sport)
    want_prop = _prop_match_key(prop_type)
    want_dir = str(direction or "").upper().strip()
    candidates: list[dict] = []
    for row in graded_rows:
        if _norm_name(str(row.get("player") or "")) != want_name:
            continue
        if _normalize_sport_label(str(row.get("sport") or "")) != want_sport:
            continue
        if _prop_match_key(str(row.get("prop") or row.get("prop_type") or "")) != want_prop:
            continue
        row_dir = str(row.get("direction") or row.get("over_under") or row.get("dir") or "").upper().strip()
        if want_dir and row_dir and row_dir != want_dir:
            continue
        candidates.append(row)
    if not candidates:
        return None
    # Prefer decided HIT/MISS over VOID when duplicates exist
    def _sort_key(r: dict) -> tuple:
        res = str(r.get("result") or "").upper()
        decided = 0 if res in ("HIT", "MISS") else 1
        tier = str(r.get("tier") or "Z")
        return (decided, tier)

    candidates.sort(key=_sort_key)
    return candidates[0]


def _parse_float(v: Any) -> float | None:
    if v is None or (isinstance(v, str) and not str(v).strip()):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def cmd_grade(slate_date: str) -> int:
    snap_p = snapshot_path(slate_date)
    if not snap_p.is_file():
        print(f"[hot-players] grade skip: no snapshot for {slate_date} ({snap_p})", file=sys.stderr)
        return 0

    try:
        snap = json.loads(snap_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[hot-players] grade error reading snapshot: {exc}", file=sys.stderr)
        return 1

    graded_rows = _load_graded_props(slate_date)
    if not graded_rows:
        print(f"[hot-players] grade skip: no graded_props for {slate_date}", file=sys.stderr)
        return 0

    results: list[dict] = []
    summary = {"hit": 0, "miss": 0, "void": 0, "push": 0, "pending": 0, "no_match": 0, "total": 0}

    for item in snap.get("featured") or []:
        dp = item.get("display_prop") or {}
        prop_type = str(dp.get("prop_type") or "")
        direction = str(dp.get("direction") or "")
        player = str(item.get("player") or "")
        sport = str(item.get("sport") or "")

        row = dict(item)
        match = _match_graded_row(player, sport, prop_type, direction, graded_rows)
        if not match:
            row["result"] = "NO_MATCH"
            row["grade_source"] = None
            summary["no_match"] += 1
        else:
            result = str(match.get("result") or "").upper().strip() or "PENDING"
            row["result"] = result
            row["actual"] = _parse_float(match.get("actual_value") or match.get("actual"))
            row["line"] = _parse_float(match.get("line")) or item.get("slate_line")
            row["margin"] = _parse_float(match.get("margin"))
            row["void_reason"] = match.get("void_reason") or ""
            row["grade_source"] = "graded_props"
            bucket = result.lower()
            if bucket in summary:
                summary[bucket] += 1
            elif result == "PENDING":
                summary["pending"] += 1
            else:
                summary["no_match"] += 1

        results.append(row)
        summary["total"] += 1

    decided = summary["hit"] + summary["miss"]
    payload = {
        "slate_date": slate_date[:10],
        "graded_at": _utc_now_iso(),
        "summary": summary,
        "hit_rate": round(summary["hit"] / decided, 4) if decided else None,
        "results": results,
    }

    GRADED_DIR.mkdir(parents=True, exist_ok=True)
    out = graded_path(slate_date)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        f"[hot-players] graded {slate_date}: "
        f"{summary['hit']}H/{summary['miss']}M/{summary['void']}V "
        f"({summary['no_match']} unmatched) -> {out}"
    )
    rebuild_track_record()
    return 0


def rebuild_track_record(last_n: int = 30) -> dict:
    GRADED_DIR.mkdir(parents=True, exist_ok=True)
    days: list[dict] = []
    for path in sorted(GRADED_DIR.glob("hot_players_graded_*.json"), reverse=True):
        if len(days) >= last_n:
            break
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        s = data.get("summary") or {}
        decided = int(s.get("hit", 0)) + int(s.get("miss", 0))
        days.append(
            {
                "slate_date": data.get("slate_date"),
                "graded_at": data.get("graded_at"),
                "summary": s,
                "hit_rate": data.get("hit_rate"),
                "decided": decided,
                "results": data.get("results") or [],
            }
        )

    agg = {"hit": 0, "miss": 0, "void": 0, "push": 0, "pending": 0, "no_match": 0, "total": 0}
    for d in days:
        for k in agg:
            agg[k] += int((d.get("summary") or {}).get(k, 0))
    decided = agg["hit"] + agg["miss"]

    payload = {
        "updated_at": _utc_now_iso(),
        "days_tracked": len(days),
        "aggregate": agg,
        "aggregate_hit_rate": round(agg["hit"] / decided, 4) if decided else None,
        "recent": days,
        "latest_graded_date": days[0]["slate_date"] if days else None,
    }

    TRACK_RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2)
    TRACK_RECORD_PATH.write_text(text, encoding="utf-8")
    UI_TRACK_RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
    UI_TRACK_RECORD_PATH.write_text(text, encoding="utf-8")
    MOBILE_TRACK_RECORD_PATH.write_text(text, encoding="utf-8")
    print(f"[hot-players] track record: {len(days)} days -> {TRACK_RECORD_PATH}")
    return payload


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Snapshot and grade Hot Players featured legs")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("snapshot", help="Save today's featured Hot Players")
    ps.add_argument("--date", default=str(date.today()), help="Slate date (ET calendar day)")
    ps.add_argument("--limit", type=int, default=8)

    pg = sub.add_parser("grade", help="Grade a prior snapshot using graded_props")
    pg.add_argument("--date", required=True, help="Slate date to grade")

    pr = sub.add_parser("rebuild-summary", help="Rebuild track_record.json from graded files")
    pr.add_argument("--last-n", type=int, default=30)

    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.cmd == "snapshot":
        return cmd_snapshot(str(args.date)[:10], limit=int(args.limit))
    if args.cmd == "grade":
        return cmd_grade(str(args.date)[:10])
    if args.cmd == "rebuild-summary":
        rebuild_track_record(last_n=int(args.last_n))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
