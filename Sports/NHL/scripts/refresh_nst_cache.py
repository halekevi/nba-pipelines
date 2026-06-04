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
from nst_client import (
    LINE_CACHE,
    import_line_csv,
    nst_key,
    refresh_line_cache,
    refresh_player_pp_cache,
)


def _resolve_season_id(raw: str) -> int:
    raw = str(raw).strip()
    if raw.isdigit() and len(raw) == 8:
        return int(raw)
    if raw.isdigit() and len(raw) == 4:
        return season_id_from_year(int(raw))
    return current_season_id()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="", help="SeasonId (20252026) or start year (2025)")
    ap.add_argument("--refresh-pp", action="store_true", help="Refresh NHL API PP skater cache")
    ap.add_argument("--refresh-nst", action="store_true", help="Refresh NST line + player PP tables")
    ap.add_argument(
        "--team",
        action="append",
        default=None,
        help="NST team filter (repeat for multiple: --team CAR --team VGK; default all)",
    )
    ap.add_argument(
        "--import-csv",
        metavar="PATH",
        default="",
        help="Import manually downloaded NST line stats CSV into line combos cache",
    )
    ap.add_argument("--sit", default="5v5", help="Situation filter for --import-csv (default: 5v5)")
    ap.add_argument("--skip-pp", action="store_true", help="Skip NHL API PP cache refresh")
    ap.add_argument(
        "--cdp",
        metavar="URL",
        nargs="?",
        const="http://127.0.0.1:9222",
        default="",
        help=(
            "CDP endpoint (default: http://127.0.0.1:9222). "
            "When passed, uses browser_fetch_html for line combos fetch."
        ),
    )
    ap.add_argument(
        "--cdp-only",
        action="store_true",
        help="Skip requests path entirely, use CDP only",
    )
    ap.add_argument(
        "--pairs-only",
        action="store_true",
        help="Skip linestats.php; refresh defensive pairs via pairings.php only",
    )
    args = ap.parse_args()

    season_id = _resolve_season_id(args.season)

    if args.import_csv:
        import_path = str(args.import_csv).strip()
        n = import_line_csv(import_path, season_id, sit=args.sit, team_filter=args.team)
        print(f"[NST] Imported {n} rows from {import_path} → {LINE_CACHE}")
        if not args.skip_pp:
            df = refresh_pp_cache(season_id)
            print(f"✅ NHL PP cache: {len(df)} rows (season_id={season_id})")
        sys.exit(0)

    do_pp = args.refresh_pp or not args.refresh_nst
    do_nst = args.refresh_nst or not args.refresh_pp

    if do_pp and not args.skip_pp:
        df = refresh_pp_cache(season_id)
        print(f"✅ NHL PP cache: {len(df)} rows (season_id={season_id})")

    if do_nst:
        use_cdp = bool(str(args.cdp).strip()) or args.cdp_only
        cdp_url = str(args.cdp).strip() or "http://127.0.0.1:9222"
        if not use_cdp and not nst_key():
            print("❌ NST_ACCESS_KEY not set. Request a key at naturalstattrick.com → profile.")
            sys.exit(1)
        teams = args.team if args.team else ["all"]
        lines = refresh_line_cache(
            season_id,
            teams=teams,
            prefer_browser=use_cdp,
            cdp_url=cdp_url,
            cdp_only=args.cdp_only,
            pairs_only=args.pairs_only,
        )
        print(f"✅ NST line combos: {len(lines)} rows cached")
        pp = refresh_player_pp_cache(season_id, teams=teams)
        print(f"✅ NST player PP table: {len(pp)} rows cached")


if __name__ == "__main__":
    main()
