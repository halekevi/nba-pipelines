#!/usr/bin/env python3
"""Apply income DB schema (if needed) and insert demo settled bets for /dashboard/income."""

from __future__ import annotations

import sys
from pathlib import Path

# Repo root (scripts/ is one level below)
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from proporacle.monitoring.dashboard_queries import (  # noqa: E402
    load_income_db,
    seed_demo_income_data,
)


def main() -> None:
    conn = load_income_db()
    try:
        inserted = seed_demo_income_data(conn)
        if inserted:
            print("Inserted demo income rows (slate_id prefix demo_slate_).")
        else:
            print("Skipped: demo slates already present in bet_result.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
