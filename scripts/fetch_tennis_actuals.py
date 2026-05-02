#!/usr/bin/env python3
"""
Build actuals_tennis_YYYY-MM-DD.csv for combined_ticket_grader (ESPN ATP/WTA finals).

Columns: player, team, prop_type, actual
(team left blank so grader matches on player + prop_norm; optional future use.)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
_TENNIS_SCRIPTS = ROOT / "Sports" / "Tennis" / "scripts"
if str(_TENNIS_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_TENNIS_SCRIPTS))

from tennis_shared import iter_scoreboard_matches  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="Slate date YYYY-MM-DD (UTC date of match start from ESPN)")
    ap.add_argument("--output", required=True, help="Path to actuals_tennis_YYYY-MM-DD.csv")
    args = ap.parse_args()

    target = str(args.date).strip()[:10]
    rows: list[dict[str, object]] = []

    for tour in ("ATP", "WTA"):
        for m in iter_scoreboard_matches(tour):
            dt = str(m.get("match_date_utc") or "")[:10]
            if dt != target:
                continue
            pl = str(m.get("player") or "").strip()
            if not pl:
                continue
            gw = float(m.get("games_won") or 0)
            mt = float(m.get("match_total_games") or 0)
            ac = float(m.get("aces") or 0)
            df = float(m.get("double_faults") or 0)
            sw = float(m.get("sets_won") or 0)
            rows.append(
                {
                    "player": pl,
                    "team": "",
                    "prop_type": "games_won",
                    "actual": gw,
                }
            )
            rows.append(
                {
                    "player": pl,
                    "team": "",
                    "prop_type": "match_total_games",
                    "actual": mt,
                }
            )
            rows.append(
                {
                    "player": pl,
                    "team": "",
                    "prop_type": "aces",
                    "actual": ac,
                }
            )
            rows.append(
                {
                    "player": pl,
                    "team": "",
                    "prop_type": "double faults",
                    "actual": df,
                }
            )
            rows.append(
                {
                    "player": pl,
                    "team": "",
                    "prop_type": "sets won",
                    "actual": sw,
                }
            )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"OK [tennis-actuals] date={target} rows={len(rows)} -> {out}")


if __name__ == "__main__":
    main()
