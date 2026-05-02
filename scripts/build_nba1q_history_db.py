#!/usr/bin/env python3
"""
build_nba1q_history_db.py

Consolidates outputs/**/period NBA actuals CSVs into the ``nba1q`` table in:
  NBA/data/cache/proporacle_ref.db

Ingests: 1Q, 2Q, 3Q, 4Q, 1H, 2H (segment column distinguishes them).

Safe to run repeatedly (INSERT OR IGNORE + unique key).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "Sports" / "NBA" / "data" / "cache" / "proporacle_ref.db"
ACTUALS_GLOBS = (
    "actuals_nba1q_*.csv",
    "actuals_nba2q_*.csv",
    "actuals_nba3q_*.csv",
    "actuals_nba4q_*.csv",
    "actuals_nba1h_*.csv",
    "actuals_nba2h_*.csv",
)
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _norm_prop_type(v: str) -> str:
    p = str(v or "").strip().lower()
    mapping = {
        "points": "points",
        "rebounds": "rebounds",
        "assists": "assists",
        "turnovers": "turnovers",
        "blocked shots": "blocked shots",
        "steals": "steals",
        "fantasy score": "fantasy score",
        "blks+stls": "blks+stls",
        "pts+asts": "pts+asts",
        "pts+rebs": "pts+rebs",
        "pts+rebs+asts": "pts+rebs+asts",
        "rebs+asts": "rebs+asts",
        "3-pt made": "3-pt made",
        "3-pt attempted": "3-pt attempted",
        "fg made": "fg made",
        "fg attempted": "fg attempted",
        "free throws made": "free throws made",
        "free throws attempted": "free throws attempted",
        "two pointers made": "two pointers made",
        "two pointers attempted": "two pointers attempted",
        "offensive rebounds": "offensive rebounds",
        "defensive rebounds": "defensive rebounds",
        "personal fouls": "personal fouls",
    }
    return mapping.get(p, p)


def _guess_espn_player_id(df: pd.DataFrame) -> pd.Series:
    for c in ("espn_player_id", "espn_athlete_id", "player_id"):
        if c in df.columns:
            s = df[c].astype(str).str.strip()
            return s.where(s.ne(""), "")
    return pd.Series([""] * len(df), index=df.index, dtype="object")


def _date_from_path(path: Path) -> str:
    m = DATE_RE.search(path.name)
    return m.group(1) if m else ""


def _segment_from_path(path: Path) -> str:
    n = path.name.lower()
    # Longer tokens first (e.g. nba2h before nba2q).
    if "nba2h" in n:
        return "2H"
    if "nba1h" in n:
        return "1H"
    if "nba4q" in n:
        return "4Q"
    if "nba3q" in n:
        return "3Q"
    if "nba2q" in n:
        return "2Q"
    return "1Q"


def _ensure_nba1q_schema(conn: sqlite3.Connection) -> None:
    """
    Ensure nba1q supports multiple segments per game/player/prop by using
    UNIQUE(game_date, espn_player_id, prop_type, segment).
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nba1q (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_date TEXT NOT NULL,
            espn_player_id TEXT,
            player TEXT NOT NULL,
            prop_type TEXT NOT NULL,
            actual REAL,
            segment TEXT DEFAULT '1Q',
            UNIQUE(game_date, espn_player_id, prop_type, segment)
        );
        """
    )
    conn.commit()

    # Detect legacy schema that enforced uniqueness without segment.
    idx_info = conn.execute("PRAGMA index_list('nba1q')").fetchall()
    legacy_unique = False
    for idx in idx_info:
        # row format: (seq, name, unique, origin, partial)
        idx_name = idx[1]
        is_unique = int(idx[2]) == 1
        if not is_unique:
            continue
        cols = [r[2] for r in conn.execute(f"PRAGMA index_info('{idx_name}')").fetchall()]
        if cols == ["game_date", "espn_player_id", "prop_type"]:
            legacy_unique = True
            break
    if not legacy_unique:
        return

    # Migrate to segment-aware unique key.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nba1q_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_date TEXT NOT NULL,
            espn_player_id TEXT,
            player TEXT NOT NULL,
            prop_type TEXT NOT NULL,
            actual REAL,
            segment TEXT DEFAULT '1Q',
            UNIQUE(game_date, espn_player_id, prop_type, segment)
        );
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO nba1q_new
        (game_date, espn_player_id, player, prop_type, actual, segment)
        SELECT game_date, espn_player_id, player, prop_type, actual, COALESCE(NULLIF(segment, ''), '1Q')
        FROM nba1q
        """
    )
    conn.execute("DROP TABLE nba1q")
    conn.execute("ALTER TABLE nba1q_new RENAME TO nba1q")
    conn.commit()


def main() -> None:
    files = []
    out_root = REPO_ROOT / "outputs"
    for pat in ACTUALS_GLOBS:
        files.extend(out_root.rglob(pat))
    files = sorted(files)
    if not files:
        print("[nba1q-db] No actuals_nba{1q,2q,3q,4q,1h,2h}_*.csv files found under outputs/.")
        return

    rows: list[dict] = []
    for f in files:
        try:
            df = pd.read_csv(f, dtype=str).fillna("")
        except Exception as e:
            print(f"[nba1q-db] Skip unreadable file: {f} ({e})")
            continue

        if "player" not in df.columns or "prop_type" not in df.columns or "actual" not in df.columns:
            print(f"[nba1q-db] Skip malformed file (missing required cols): {f}")
            continue

        game_date = _date_from_path(f)
        segment = _segment_from_path(f)
        espn_id = _guess_espn_player_id(df)
        actual = pd.to_numeric(df["actual"], errors="coerce")

        for i, r in df.iterrows():
            player = str(r.get("player", "")).strip()
            prop = _norm_prop_type(r.get("prop_type", ""))
            act = actual.iloc[i]
            if not player or not prop or pd.isna(act):
                continue
            pid = str(espn_id.iloc[i]).strip()
            # Keep uniqueness stable when ESPN id is absent.
            if not pid:
                pid = player.lower()
            rows.append(
                {
                    "game_date": game_date,
                    "espn_player_id": pid,
                    "player": player,
                    "prop_type": prop,
                    "actual": float(act),
                    "segment": segment,
                }
            )

    if not rows:
        print("[nba1q-db] No valid rows parsed.")
        return

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        _ensure_nba1q_schema(conn)
        before = conn.execute("SELECT COUNT(*) FROM nba1q").fetchone()[0]
        ins = 0
        skp = 0
        for r in rows:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO nba1q
                (game_date, espn_player_id, player, prop_type, actual, segment)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    r["game_date"],
                    r["espn_player_id"],
                    r["player"],
                    r["prop_type"],
                    r["actual"],
                    r["segment"],
                ),
            )
            if cur.rowcount == 1:
                ins += 1
            else:
                skp += 1
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM nba1q").fetchone()[0]
    finally:
        conn.close()

    print(f"[nba1q-db] Source files: {len(files)}")
    print(f"[nba1q-db] Parsed rows:  {len(rows)}")
    print(f"[nba1q-db] Inserted:     {ins}")
    print(f"[nba1q-db] Skipped:      {skp}")
    print(f"[nba1q-db] Total rows:   {total} (was {before})")
    print(f"[nba1q-db] DB:           {DB_PATH}")


if __name__ == "__main__":
    main()

