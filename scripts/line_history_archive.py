#!/usr/bin/env python3
"""Append PrizePicks slate rows to data/line_history.db (cross-sport)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DB = _REPO_ROOT / "data" / "line_history.db"


def archive_lines(
    df: pd.DataFrame,
    sport: str,
    *,
    fetched_at: str | None = None,
) -> None:
    """Append fetch snapshot to line_history; create DB/index on first use."""
    if df is None or df.empty:
        return
    out = df.copy()
    out["fetched_at"] = (
        str(fetched_at).strip()
        if fetched_at
        else datetime.now().isoformat(timespec="seconds")
    )
    out["sport"] = str(sport).strip().upper()
    ARCHIVE_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(ARCHIVE_DB) as conn:
        table_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(line_history)").fetchall()
        }
        if table_cols:
            keep = [c for c in out.columns if c in table_cols]
            extra = table_cols - set(keep)
            insert_df = out[keep].copy() if keep else out.copy()
        else:
            insert_df = out.copy()
        insert_df.to_sql("line_history", conn, if_exists="append", index=False)
        for ddl in (
            "CREATE INDEX IF NOT EXISTS idx_line_history_player_sport "
            "ON line_history (player, sport, fetched_at)",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
