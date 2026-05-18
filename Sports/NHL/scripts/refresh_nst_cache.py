#!/usr/bin/env python3
"""Refresh Natural Stat Trick caches for the NHL pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from nhl_pp_api import current_season_id, refresh_pp_cache, season_id_from_year
from nst_client import nst_key, refresh_line_cache, refresh_player_pp_cache


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="", help="SeasonId (20252026) or start year (2025)")
    ap.add_argument("--refresh-pp", action="store_true", help="Refresh NHL API PP skater cache")
    ap.add_argument("--refresh-nst", action="store_true", help="Refresh NST line + player PP tables")
    ap.add_argument("--team", default="all", help="NST team filter (default all)")
    args = ap.parse_args()

    raw = str(args.season).strip()
    if raw.isdigit() and len(raw) == 8:
        season_id = int(raw)
    elif raw.isdigit() and len(raw) == 4:
        season_id = season_id_from_year(int(raw))
    else:
        season_id = current_season_id()

    do_pp = args.refresh_pp or not args.refresh_nst
    do_nst = args.refresh_nst or not args.refresh_pp

    if do_pp:
        df = refresh_pp_cache(season_id)
        print(f"✅ NHL PP cache: {len(df)} rows (season_id={season_id})")

    if do_nst:
        if not nst_key():
            print("❌ NST_ACCESS_KEY not set. Request a key at naturalstattrick.com → profile.")
            sys.exit(1)
        teams = [args.team] if args.team else ["all"]
        lines = refresh_line_cache(season_id, teams=teams)
        print(f"✅ NST line combos: {len(lines)} rows cached")
        pp = refresh_player_pp_cache(season_id, teams=teams)
        print(f"✅ NST player PP table: {len(pp)} rows cached")


if __name__ == "__main__":
    main()
