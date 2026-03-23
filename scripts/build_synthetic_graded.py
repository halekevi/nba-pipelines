#!/usr/bin/env python3
"""
Build synthetic graded prop rows from historical_actuals.db into SQLite
(data/cache/synthetic_graded.db) for player consistency and training.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
from ensure_local_cache import ensure_local_cache

ensure_local_cache(str(Path(__file__).resolve().parents[1]))

import build_player_consistency as bpc

REPO_ROOT = Path(__file__).resolve().parents[1]
HIST_DB = REPO_ROOT / "data" / "cache" / "historical_actuals.db"
SYNTHETIC_GRADED_DB = REPO_ROOT / "data" / "cache" / "synthetic_graded.db"
OUT_SYNTHETIC_DIR = REPO_ROOT / "outputs" / "synthetic"

SYNTHETIC_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS synthetic_graded_props (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  player_name TEXT NOT NULL,
  sport TEXT NOT NULL,
  prop_type TEXT NOT NULL,
  direction TEXT NOT NULL,
  line REAL NOT NULL,
  line_bucket TEXT,
  actual_value REAL,
  result TEXT NOT NULL,
  game_date TEXT NOT NULL,
  season TEXT,
  tier TEXT,
  opponent TEXT,
  home_away TEXT,
  minutes REAL,
  weight REAL DEFAULT 1.0,
  source TEXT DEFAULT 'synthetic',
  created_at TEXT,
  UNIQUE(player_name, sport, prop_type, direction, line, game_date)
);
CREATE INDEX IF NOT EXISTS idx_sport_date
  ON synthetic_graded_props(sport, game_date);
CREATE INDEX IF NOT EXISTS idx_player_sport_prop
  ON synthetic_graded_props(player_name, sport, prop_type);
"""

# SQLite max host parameters (~999 typical); method="multi" uses one ? per cell per row.
_SQLITE_MULTI_CHUNKSIZE = 50

DB_INSERT_COLS = [
    "player_name",
    "sport",
    "prop_type",
    "direction",
    "line",
    "line_bucket",
    "actual_value",
    "result",
    "game_date",
    "season",
    "tier",
    "opponent",
    "home_away",
    "minutes",
    "weight",
    "source",
    "created_at",
]

PROP_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "NBA": [
        ("Points", "points"),
        ("Rebounds", "rebounds"),
        ("Assists", "assists"),
        ("PRA", "pra"),
        ("Pts+Asts", "pts_asts"),
        ("Pts+Rebs", "pts_rebs"),
        ("Rebs+Asts", "rebs_asts"),
        ("Threes", "threes_made"),
        ("Steals", "steals"),
        ("Blocks", "blocks"),
        ("Turnovers", "turnovers"),
        ("Fantasy Score", "fantasy_score"),
        ("FTA", "fta"),
        ("FG Attempted", "fg_attempted"),
        ("Blks+Stls", "blks_stls"),
    ],
    "CBB": [
        ("Points", "points"),
        ("Rebounds", "rebounds"),
        ("Assists", "assists"),
        ("PRA", "pra"),
        ("Pts+Asts", "pts_asts"),
        ("Pts+Rebs", "pts_rebs"),
        ("Rebs+Asts", "rebs_asts"),
        ("Threes", "threes_made"),
        ("Steals", "steals"),
        ("Blocks", "blocks"),
        ("Turnovers", "turnovers"),
        ("Fantasy Score", "fantasy_score"),
        ("FTA", "fta"),
        ("FG Attempted", "fg_attempted"),
        ("Blks+Stls", "blks_stls"),
    ],
    "NHL": [
        ("Goals", "goals"),
        ("Assists", "assists"),
        ("Points", "points"),
        ("Shots", "shots"),
        ("Saves", "saves"),
    ],
    "Soccer": [
        ("Goals", "goals"),
        ("Assists", "assists"),
        ("Shots", "shots"),
        ("Shots on Target", "shots_on_target"),
        ("Passes", "passes_attempted"),
        ("Tackles", "tackles"),
    ],
}


def ensure_synthetic_graded_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SYNTHETIC_SCHEMA_SQL)
    conn.commit()


def write_to_db(df: pd.DataFrame, sport: str, season: str, db_path: str) -> None:
    if df.empty:
        return
    SYNTHETIC_GRADED_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        ensure_synthetic_graded_schema(conn)
        conn.execute(
            "DELETE FROM synthetic_graded_props WHERE sport = ? AND season = ?",
            (sport, season),
        )
        out = df.copy()
        out["source"] = "synthetic"
        out["weight"] = 0.7
        out["created_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out["minutes"] = pd.to_numeric(out.get("minutes"), errors="coerce")
        out["actual_value"] = pd.to_numeric(out.get("actual_value"), errors="coerce")
        out["line"] = pd.to_numeric(out.get("line"), errors="coerce")
        for col in DB_INSERT_COLS:
            if col not in out.columns:
                out[col] = None
        out = out[DB_INSERT_COLS]
        # Larger frames: slice to bound peak memory; each to_sql uses small multi batches for SQLite limits.
        for start in range(0, len(out), 10000):
            part = out.iloc[start : start + 10000]
            part.to_sql(
                "synthetic_graded_props",
                conn,
                if_exists="append",
                index=False,
                method="multi",
                chunksize=_SQLITE_MULTI_CHUNKSIZE,
            )
        conn.commit()
    finally:
        conn.close()
    print(f"  {sport} {season}: {len(df)} rows written to DB")


def tier_for_line(line: float, avg: float, std: float) -> str:
    if std <= 1e-9:
        return "Standard"
    if line <= avg - 0.3 * std:
        return "Goblin"
    if line >= avg + 0.3 * std:
        return "Demon"
    return "Standard"


def grade_side(actual: float, line: float, direction: str) -> str:
    if direction == "OVER":
        return "HIT" if actual > line else "MISS"
    if direction == "UNDER":
        return "HIT" if actual < line else "MISS"
    return "MISS"


def season_filter_label(label: str, want: set[str] | None) -> bool:
    if not want:
        return True
    return label in want


def build_for_sport_season(
    df: pd.DataFrame, sport: str, season: str, want_seasons: set[str] | None
) -> pd.DataFrame:
    if not season_filter_label(season, want_seasons):
        return pd.DataFrame()
    sub = df[(df["sport"] == sport) & (df["season"] == season)].copy()
    if sub.empty:
        return pd.DataFrame()
    sub["game_date"] = pd.to_datetime(sub["game_date"], errors="coerce")
    sub = sub.dropna(subset=["game_date"])
    sub = sub.sort_values("game_date")

    rows_out: list[dict[str, Any]] = []
    props = PROP_COLUMNS.get(sport, [])
    for player_name, gdf in sub.groupby("player_name"):
        gdf = gdf.sort_values("game_date")
        for prop_label, col in props:
            if col not in gdf.columns:
                continue
            series = pd.to_numeric(gdf[col], errors="coerce").dropna()
            if len(series) < 3:
                continue
            avg = float(series.mean())
            std = float(series.std(ddof=1)) if len(series) > 1 else 0.0
            std = max(std, 1e-6)
            line_low = avg - 0.5 * std
            line_mid = avg
            line_high = avg + 0.5 * std
            for _, r in gdf.iterrows():
                actual = r.get(col)
                if actual is None or pd.isna(actual):
                    continue
                try:
                    v = float(actual)
                except (TypeError, ValueError):
                    continue
                gd = r["game_date"]
                ds = gd.strftime("%Y-%m-%d") if hasattr(gd, "strftime") else str(gd)[:10]
                opp = r.get("opponent")
                ha = r.get("home_away")
                mins = r.get("minutes")
                for line in (line_low, line_mid, line_high):
                    if abs(v - line) < 1e-9:
                        continue
                    tier = tier_for_line(line, avg, std)
                    for direction in ("OVER", "UNDER"):
                        res = grade_side(v, line, direction)
                        bucket = bpc.get_line_bucket(prop_label, float(line), sport)
                        rows_out.append(
                            {
                                "player_name": player_name,
                                "sport": sport,
                                "prop_type": prop_label,
                                "direction": direction,
                                "line": float(line),
                                "line_bucket": bucket,
                                "actual_value": v,
                                "result": res,
                                "game_date": ds,
                                "season": season,
                                "tier": tier,
                                "opponent": opp if pd.notna(opp) else "",
                                "home_away": ha if pd.notna(ha) else "",
                                "minutes": mins if pd.notna(mins) else None,
                            }
                        )
    if not rows_out:
        return pd.DataFrame()
    return pd.DataFrame(rows_out)


def _warn_old_synthetic_excels() -> None:
    if not OUT_SYNTHETIC_DIR.is_dir():
        return
    xs = sorted(OUT_SYNTHETIC_DIR.glob("*.xlsx"))
    if xs:
        print("Old Excel synthetic files found - safe to delete:")
        for f in xs:
            print(f"  {f}")


def _write_preview_csv(db_path: Path, sport: str) -> None:
    sport_u = sport.strip().upper()
    if not db_path.is_file():
        print(f"  (preview) No DB at {db_path}")
        return
    conn = sqlite3.connect(str(db_path))
    try:
        df = pd.read_sql_query(
            """
            SELECT * FROM synthetic_graded_props
            WHERE sport = ?
            ORDER BY game_date DESC, id DESC
            LIMIT 1000
            """,
            conn,
            params=[sport_u],
        )
    finally:
        conn.close()
    if df.empty:
        print(f"  (preview) No rows for sport={sport_u}")
        return
    df = df.iloc[::-1].reset_index(drop=True)
    OUT_SYNTHETIC_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_SYNTHETIC_DIR / f"preview_{sport_u.lower()}.csv"
    df.to_csv(out_path, index=False)
    print(f"  Preview: {out_path} ({len(df)} rows)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build synthetic graded rows into SQLite.")
    ap.add_argument("--sport", choices=("NBA", "CBB", "NHL", "Soccer"), default=None)
    ap.add_argument("--seasons", default=None, help="Comma-separated labels e.g. 2023-24,2024-25")
    ap.add_argument(
        "--preview",
        metavar="SPORT",
        default=None,
        help="After build, export last 1000 rows for this sport to outputs/synthetic/preview_<sport>.csv",
    )
    ap.add_argument(
        "--preview-only",
        metavar="SPORT",
        default=None,
        dest="preview_only",
        help="Only write preview CSV from synthetic_graded.db (no historical_actuals read or rebuild).",
    )
    args = ap.parse_args()

    _warn_old_synthetic_excels()

    if args.preview_only:
        _write_preview_csv(SYNTHETIC_GRADED_DB, str(args.preview_only).strip().upper())
        return

    if not HIST_DB.is_file():
        print(f"Missing {HIST_DB} - run fetch_historical_actuals.py first.")
        if args.preview:
            _write_preview_csv(SYNTHETIC_GRADED_DB, args.preview.strip().upper())
        return

    want: set[str] | None = None
    if args.seasons:
        want = {s.strip() for s in args.seasons.split(",") if s.strip()}

    conn = sqlite3.connect(str(HIST_DB))
    try:
        df = pd.read_sql_query("SELECT * FROM player_game_logs", conn)
    finally:
        conn.close()
    if df.empty:
        print("No rows in player_game_logs.")
        if args.preview:
            _write_preview_csv(SYNTHETIC_GRADED_DB, args.preview.strip().upper())
        return

    sports = [args.sport] if args.sport else ["NBA", "CBB", "NHL", "Soccer"]
    seasons = sorted(df["season"].dropna().unique())

    summary: list[tuple[str, str, int]] = []
    total = 0
    dbp = str(SYNTHETIC_GRADED_DB)

    for sp in sports:
        for season in seasons:
            out = build_for_sport_season(df, sp, str(season), want)
            if out.empty:
                continue
            write_to_db(out, sp, str(season), dbp)
            n = len(out)
            summary.append((sp, str(season), n))
            total += n

    print()
    print("Synthetic graded data written to:")
    print(f"  {SYNTHETIC_GRADED_DB}")
    print("  " + "-" * 33)
    for sp, se, n in summary:
        print(f"  {sp:<6} {se}: {n:>10,} rows")
    if summary:
        print(f"  Total: {total:,} rows across all sports and seasons")
    else:
        print("  (no rows written for selected filters)")

    if args.preview:
        _write_preview_csv(SYNTHETIC_GRADED_DB, args.preview.strip().upper())


if __name__ == "__main__":
    main()
