#!/usr/bin/env python3
"""Query prop line history from data/line_history.db for sparkline anchors."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
LINE_HISTORY_DB = _REPO_ROOT / "data" / "line_history.db"


def get_line_series(
    player_name: str,
    prop_type: str,
    sport: str,
    n: int = 10,
    *,
    db_path: Path | None = None,
) -> list[float]:
    """
    Return up to ``n`` line values, oldest-first, from line_history snapshots.
    Empty list if DB missing or fewer than 2 snapshots.
    """
    pname = str(player_name or "").strip()
    prop = str(prop_type or "").strip().lower()
    sp = str(sport or "").strip().upper()
    if not pname or not prop or not sp:
        return []

    path = db_path or LINE_HISTORY_DB
    if not path.is_file():
        return []

    try:
        with sqlite3.connect(path) as conn:
            cur = conn.execute(
                """
                SELECT COALESCE(line_score, line) AS line_val
                FROM line_history
                WHERE sport = ?
                  AND (
                    LOWER(COALESCE(player_name, player)) = LOWER(?)
                    OR LOWER(COALESCE(player, player_name)) = LOWER(?)
                  )
                  AND (
                    LOWER(COALESCE(prop_norm, prop_type, '')) = LOWER(?)
                    OR LOWER(COALESCE(prop_type, prop_norm, '')) = LOWER(?)
                  )
                ORDER BY fetched_at DESC
                LIMIT ?
                """,
                (sp, pname, pname, prop, prop, int(max(2, n))),
            )
            rows = cur.fetchall()
    except Exception:
        return []

    vals: list[float] = []
    for (raw,) in reversed(rows):
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v == v:
            vals.append(v)
    return vals if len(vals) >= 2 else []


class LineSeriesCache:
    """Per-request memoization for /api/slate pick building."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db = db_path or LINE_HISTORY_DB
        self._cache: dict[tuple[str, str, str, int], list[float]] = {}

    def get(
        self,
        player_name: str,
        prop_type: str,
        sport: str,
        n: int = 10,
    ) -> list[float]:
        key = (
            str(player_name or "").strip().lower(),
            str(prop_type or "").strip().lower(),
            str(sport or "").strip().upper(),
            int(n),
        )
        if key not in self._cache:
            self._cache[key] = get_line_series(
                player_name, prop_type, sport, n, db_path=self._db
            )
        return list(self._cache[key])
