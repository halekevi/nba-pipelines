#!/usr/bin/env python3
"""
Build Matchup Edge JSON for Slate Explorer (all supported sports).

Each sport emits top-5 + bottom-5 leaders per team/category (leader_slice in JSON).
WNBA/NBA/NHL/MLB use dedicated builders; nba1h/nba1q/soccer/cbb/cfb/nfl use the generic path.

  py -3 scripts/build_matchup_edge_json.py
  py -3 scripts/build_matchup_edge_json.py --sport nba
  py -3 scripts/build_matchup_edge_json.py --sport all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from utils.matchup_edge.builder import build_matchup_payload, publish_payload  # noqa: E402
from utils.matchup_edge.sports_config import ENABLED_SPORTS  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="all", help=f"Sport key or 'all'. Enabled: {', '.join(ENABLED_SPORTS)}")
    ap.add_argument("--slate", default="", help="Optional slate CSV/JSON path")
    args = ap.parse_args()

    sports = list(ENABLED_SPORTS) if args.sport.lower() == "all" else [args.sport.lower().strip()]
    slate = Path(args.slate) if args.slate else None

    for sport in sports:
        payload = build_matchup_payload(sport, slate_path=slate)
        paths = publish_payload(payload, sport, _REPO)
        n_blocks = len(payload.get("players_by_team_cat") or {})
        err = payload.get("error")
        if err:
            print(f"[{sport}] WARN: {err}")
        print(f"[{sport}] blocks={n_blocks} -> {paths[0].name}")


if __name__ == "__main__":
    main()
