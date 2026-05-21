#!/usr/bin/env python3
"""Backfill line_history snapshots for a slate date when step1 fetch wrote 0 rows."""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.line_history_archive import archive_lines  # noqa: E402


def _synthetic_id(*parts: object) -> str:
    raw = "|".join(str(p or "").strip().lower() for p in parts)
    return "rec-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _frame_from_step1(path: Path, date: str) -> pd.DataFrame | None:
    if not path.is_file():
        return None
    df = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    if df.empty:
        return None
    if "game_date" in df.columns:
        match = df["game_date"].astype(str).str.strip() == date
        if match.any():
            df = df.loc[match].copy()
    if df.empty:
        return None
    return df


def _frame_from_combined_slate(path: Path, date: str) -> pd.DataFrame | None:
    if not path.is_file():
        return None
    try:
        raw = pd.read_excel(path, sheet_name="NBA Slate", engine="openpyxl")
    except Exception:
        return None
    if raw.empty:
        return None
    rows: list[dict] = []
    for _, r in raw.iterrows():
        player = str(r.get("Player", "")).strip()
        team = str(r.get("Team", "")).strip()
        prop = str(r.get("Prop", "")).strip()
        if not player or not prop:
            continue
        line = r.get("Line")
        pick_type = str(r.get("Pick Type", "Standard")).strip() or "Standard"
        opp = str(r.get("Opp", "")).strip()
        gid = _synthetic_id(player, team, opp, prop, line, pick_type)
        rows.append(
            {
                "projection_id": gid,
                "pp_projection_id": gid,
                "player_id": "",
                "pp_game_id": _synthetic_id(team, opp, date),
                "start_time": f"{date}T19:00:00-04:00",
                "player": player,
                "pos": str(r.get("Pos", "")).strip(),
                "team": team,
                "opp_team": opp,
                "prop_type": prop,
                "line": line,
                "standard_line": r.get("Standard Line", line),
                "pick_type": pick_type,
                "pp_home_team": "",
                "pp_away_team": "",
                "image_url": "",
                "game_date": date,
            }
        )
    return pd.DataFrame(rows) if rows else None


def _frame_from_step8(path: Path, date: str) -> pd.DataFrame | None:
    if not path.is_file():
        return None
    try:
        raw = pd.read_excel(path, engine="openpyxl")
    except Exception:
        return None
    if raw.empty:
        return None
    rows: list[dict] = []
    for _, r in raw.iterrows():
        player = str(r.get("Player", "")).strip()
        team = str(r.get("Team", "")).strip()
        prop = str(r.get("Prop", "")).strip()
        if not player or not prop:
            continue
        line = r.get("Line")
        pick_type = str(r.get("Pick Type", "Standard")).strip() or "Standard"
        opp = str(r.get("Opp", "")).strip()
        game_date = str(r.get("Game Date", date)).strip() or date
        gid = _synthetic_id(player, team, opp, prop, line, pick_type)
        rows.append(
            {
                "projection_id": gid,
                "pp_projection_id": gid,
                "player_id": "",
                "pp_game_id": _synthetic_id(team, opp, game_date),
                "start_time": f"{game_date}T19:00:00-04:00",
                "player": player,
                "pos": str(r.get("Pos", "")).strip(),
                "team": team,
                "opp_team": opp,
                "prop_type": prop,
                "line": line,
                "standard_line": r.get("Standard Line", line),
                "pick_type": pick_type,
                "pp_home_team": "",
                "pp_away_team": "",
                "image_url": "",
                "game_date": game_date,
            }
        )
    return pd.DataFrame(rows) if rows else None


def load_recovery_frame(date: str) -> tuple[pd.DataFrame, str]:
    day = REPO / "outputs" / date
    step1 = day / "nba" / "step1_pp_props_today.csv"
    for label, loader, path in (
        ("step1", _frame_from_step1, step1),
        (
            "combined_slate",
            _frame_from_combined_slate,
            day / f"combined_slate_tickets_{date}.xlsx",
        ),
        (
            "step8",
            _frame_from_step8,
            day / f"step8_nba_direction_clean_{date}.xlsx",
        ),
    ):
        df = loader(path, date)
        if df is not None and not df.empty:
            return df, label
    raise SystemExit(f"No NBA recovery source found for {date}")


def _existing_count(date: str) -> int:
    db = REPO / "data" / "line_history.db"
    if not db.is_file():
        return 0
    with sqlite3.connect(db) as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM line_history WHERE sport='NBA' AND fetched_at LIKE ?",
                (f"{date}%",),
            ).fetchone()[0]
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="Slate date YYYY-MM-DD")
    ap.add_argument(
        "--fetched-at",
        default="",
        help="ISO timestamp for line_history.fetched_at (default: <date>T17:00:00)",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="Archive even if rows exist for date")
    args = ap.parse_args()
    date = str(args.date).strip()
    fetched_at = str(args.fetched_at).strip() or f"{date}T17:00:00"

    existing = _existing_count(date)
    if existing and not args.force:
        print(f"line_history already has {existing:,} NBA rows for {date}; use --force to add another snapshot")
        return 0

    df, source = load_recovery_frame(date)
    print(f"Recovery source: {source} ({len(df):,} rows)")
    if args.dry_run:
        print(f"Would archive to line_history at fetched_at={fetched_at}")
        return 0

    archive_lines(df, sport="NBA", fetched_at=fetched_at)
    print(f"Archived {len(df):,} NBA rows -> line_history (fetched_at={fetched_at})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
