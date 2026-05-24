#!/usr/bin/env python3
"""
Build UI cache for Hot Players + Player Evaluator tabs.
Reads graded history (retrain_dataset.csv or graded_export_*.csv) and writes:
  data/cache/player_consistency.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
RETRAIN_CSV = REPO_ROOT / "data" / "retrain_dataset.csv"
TRAINING_DIR = REPO_ROOT / "data" / "training"
CACHE_DIR = REPO_ROOT / "data" / "cache"
OUTPUT_PATH = CACHE_DIR / "player_consistency.json"
UI_DEPLOY_PATH = REPO_ROOT / "ui_runner" / "data" / "player_consistency.json"

SPORT_ALIASES = {
    "nba": "NBA",
    "mlb": "MLB",
    "nhl": "NHL",
    "nfl": "NFL",
    "wnba": "WNBA",
    "soccer": "Soccer",
    "tennis": "Tennis",
    "cbb": "CBB",
    "cfb": "CFB",
}

SLATE_PATHS = (
    REPO_ROOT / "ui_runner" / "templates" / "slate_latest.json",
    REPO_ROOT / "mobile" / "www" / "slate_latest.json",
)


def find_latest_graded_csv() -> Path | None:
    pattern = sorted(TRAINING_DIR.glob("graded_export_*.csv"), reverse=True)
    return pattern[0] if pattern else None


def _norm_name(name: str) -> str:
    return (name or "").strip().lower()


def load_graded_dataframe() -> tuple[pd.DataFrame, str]:
    if RETRAIN_CSV.is_file():
        return pd.read_csv(RETRAIN_CSV, low_memory=False), RETRAIN_CSV.name
    fallback = find_latest_graded_csv()
    if fallback is None:
        raise FileNotFoundError("No retrain_dataset.csv or graded_export_*.csv found")
    return pd.read_csv(fallback, low_memory=False), fallback.name


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.lower().strip().replace(" ", "_") for c in df.columns]
    col_map = {
        "player_name": "player",
        "name": "player",
        "pick_direction": "direction",
        "side": "direction",
        "result": "outcome",
        "grade": "outcome",
        "date": "game_date",
        "file_date": "game_date",
    }
    for src, dst in col_map.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})
    if "outcome" not in df.columns and "result" in df.columns:
        df["outcome"] = df["result"]
    if "outcome" in df.columns:
        df["outcome"] = (
            df["outcome"]
            .astype(str)
            .str.upper()
            .str.strip()
            .replace({"WIN": "HIT", "LOSS": "MISS", "LOSE": "MISS"})
        )
    elif "result_binary" in df.columns:
        df["outcome"] = df["result_binary"].map({1: "HIT", 0: "MISS", True: "HIT", False: "MISS"})
    elif "hit" in df.columns:
        df["outcome"] = df["hit"].map({1: "HIT", 0: "MISS", True: "HIT", False: "MISS"})
    return df


def compute_consistency(df: pd.DataFrame, min_props: int = 10) -> list[dict]:
    df = df.copy()
    df["sport"] = df["sport"].astype(str).str.lower().map(SPORT_ALIASES).fillna(df["sport"].astype(str).str.upper())
    df["direction"] = df["direction"].astype(str).str.upper().str.strip()
    df["outcome"] = df["outcome"].astype(str).str.upper().str.strip()
    df = df[df["outcome"].isin(["HIT", "MISS"])]

    results: list[dict] = []
    for (player, sport), grp in df.groupby(["player", "sport"], sort=False):
        total = len(grp)
        if total < min_props:
            continue

        hits = int((grp["outcome"] == "HIT").sum())
        hit_rate = round(hits / total, 4)

        over_grp = grp[grp["direction"] == "OVER"]
        under_grp = grp[grp["direction"] == "UNDER"]

        over_total = len(over_grp)
        over_hits = int((over_grp["outcome"] == "HIT").sum()) if over_total else 0
        over_rate = round(over_hits / over_total, 4) if over_total else None

        under_total = len(under_grp)
        under_hits = int((under_grp["outcome"] == "HIT").sum()) if under_total else 0
        under_rate = round(under_hits / under_total, 4) if under_total else None

        direction = "BOTH"
        if over_rate is not None and under_rate is not None and over_total >= 10 and under_total >= 10:
            gap = over_rate - under_rate
            if gap > 0.10:
                direction = "OVER"
            elif gap < -0.10:
                direction = "UNDER"
        elif over_rate is not None and over_total >= 10 and (under_total < 10 or under_rate is None):
            direction = "OVER"
        elif under_rate is not None and under_total >= 10 and (over_total < 10 or over_rate is None):
            direction = "UNDER"

        if over_rate is not None and under_rate is not None and over_total >= 5 and under_total >= 5:
            balance_score = round(1 - abs(over_rate - under_rate), 4)
        else:
            balance_score = None

        tier = "high" if total >= 50 else "medium" if total >= 25 else "low"

        results.append(
            {
                "player": str(player),
                "sport": str(sport),
                "total": int(total),
                "hits": hits,
                "hit_rate": hit_rate,
                "over_hits": over_hits,
                "over_total": int(over_total),
                "over_rate": over_rate,
                "under_hits": under_hits,
                "under_total": int(under_total),
                "under_rate": under_rate,
                "direction": direction,
                "tier": tier,
                "balance_score": balance_score,
                "last_updated": str(date.today()),
            }
        )

    tier_order = {"high": 0, "medium": 1, "low": 2}
    results.sort(key=lambda r: (r["sport"], tier_order[r["tier"]], -r["hit_rate"]))
    return results


def _players_from_slate_json(data: dict) -> set[str]:
    players: set[str] = set()
    sports = data.get("sports") or {}
    if not isinstance(sports, dict):
        return players
    for rows in sports.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("player") or row.get("player_name") or ""
            if name:
                players.add(str(name).strip())
    return players


def load_today_slate() -> set[str]:
    players: set[str] = set()
    today_str = str(date.today())
    fallback: set[str] = set()

    for path in SLATE_PATHS:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        file_date = str(data.get("date", ""))[:10]
        names = _players_from_slate_json(data)
        if not names:
            continue
        if file_date == today_str:
            players.update(names)
        elif not fallback:
            fallback = names

    if not players and fallback:
        players = fallback
        print(f"[consistency-ui] Using slate_latest ({len(players)} players; date not today)")

    step8_dir = CACHE_DIR
    for sport_file in step8_dir.glob("step8_*.json"):
        try:
            data = json.loads(sport_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        picks = data if isinstance(data, list) else data.get("picks", [])
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            gd = pick.get("game_date", "") or pick.get("date", "")
            if gd and str(gd)[:10] != today_str:
                continue
            name = pick.get("player") or pick.get("player_name") or ""
            if name:
                players.add(str(name).strip())

    return players


def tag_today_slate(records: list[dict], today_players: set[str]) -> list[dict]:
    today_norm = {_norm_name(p) for p in today_players}
    for r in records:
        r["on_today_slate"] = _norm_name(r["player"]) in today_norm
    return records


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build player_consistency.json for UI")
    p.add_argument("--sport", default=None)
    p.add_argument("--min-props", type=int, default=10)
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--today-only", action="store_true")
    p.add_argument("--top-n", type=int, default=50)
    p.add_argument("--output", default=str(OUTPUT_PATH))
    return p.parse_args()


def main() -> int:
    args = parse_args()

    try:
        df, source_name = load_graded_dataframe()
    except FileNotFoundError as exc:
        print(f"[consistency-ui] ERROR: {exc}", file=sys.stderr)
        return 1

    df = normalize_columns(df)
    required = ["player", "sport", "direction", "outcome"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"[consistency-ui] ERROR: missing columns {missing}", file=sys.stderr)
        return 1

    if args.days and "game_date" in df.columns:
        cutoff = date.today() - timedelta(days=args.days)
        df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
        df = df[df["game_date"].dt.date >= cutoff]

    if args.sport:
        sport_norm = SPORT_ALIASES.get(args.sport.lower(), args.sport.upper())
        df = df[df["sport"].astype(str).str.upper() == sport_norm.upper()]

    print(f"[consistency-ui] Computing from {source_name} ({len(df):,} rows) ...")
    records = compute_consistency(df, min_props=args.min_props)

    today_players = load_today_slate()
    if today_players:
        print(f"[consistency-ui] Today's slate: {len(today_players)} players")
    records = tag_today_slate(records, today_players)

    top_records: list[dict] = []
    for _sport, group in groupby(sorted(records, key=lambda r: r["sport"]), key=lambda r: r["sport"]):
        top_records.extend(list(group)[: args.top_n])

    if args.today_only:
        top_records = [r for r in top_records if r.get("on_today_slate")]

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_csv": source_name,
        "total_players": len(top_records),
        "players": top_records,
    }
    text = json.dumps(payload, indent=2)
    out_path.write_text(text, encoding="utf-8")
    print(f"[consistency-ui] Wrote {len(top_records)} players -> {out_path}")
    UI_DEPLOY_PATH.parent.mkdir(parents=True, exist_ok=True)
    UI_DEPLOY_PATH.write_text(text, encoding="utf-8")
    print(f"[consistency-ui] Mirrored deploy copy -> {UI_DEPLOY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
