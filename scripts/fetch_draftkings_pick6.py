#!/usr/bin/env python3
"""
DraftKings Pick6 — fetch status for PropORACLE

As of 2026-04, Pick6 loads markets through bundled SPA code; there is no
documented, stable unauthenticated JSON feed comparable to PrizePicks or
Underdog's /v1/over_under_lines. Network calls typically require an authenticated
session and change with deployments.

This script:
  - Writes an empty PP-shaped CSV (with source_book=draftkings) so downstream
    merge tooling can standardize on column names.
  - Exits with code 2 and prints practical next steps (DevTools export, future
    Playwright capture mirroring NHL step1_fetch_prizepicks_nhl.py).

When you have a captured JSON array of Pick6 selections, add a loader here or
pipe through a small normalizer; until then, use Underdog + PrizePicks for
automated boards.

Usage:
  py -3 scripts/fetch_draftkings_pick6.py --output dk_pick6_placeholder.csv
"""

from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from pickem_step1_schema import OUTPUT_COLUMNS


def main() -> None:
    ap = argparse.ArgumentParser(description="DraftKings Pick6 fetch (placeholder)")
    ap.add_argument("--output", default="step1_draftkings_pick6_placeholder.csv")
    args = ap.parse_args()

    print(
        "DraftKings Pick6: no built-in public fetch yet.\n"
        "Options:\n"
        "  • Capture XHR/JSON from https://pick6.draftkings.com while logged in,\n"
        "    then normalize into the same columns as pickem_step1_schema.OUTPUT_COLUMNS.\n"
        "  • Or add Playwright route interception (see NHL/scripts/step1_fetch_prizepicks_nhl.py).\n"
        "Use scripts/fetch_underdog_pickem.py for an automated alternate book today."
    )

    df = pd.DataFrame(columns=OUTPUT_COLUMNS)
    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"Wrote empty schema template → {args.output}")
    sys.exit(2)


if __name__ == "__main__":
    main()
