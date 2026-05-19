#!/usr/bin/env python3
"""
Copy EPL shooting HTML into data/cache/fbref_html/ without launching a browser.

FBref blocks curl, Playwright, and most headless fetchers (Cloudflare). Use one of:

  1. Chrome: File → Save As → Webpage, Complete → epl_summary.html
  2. Existing soccerdata cache (if you already fetched once via soccerdata)

This script only does (2) — file copy, no network, no Playwright.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
CACHE_DIR = _REPO / "data" / "cache" / "fbref_html"
DEST = CACHE_DIR / "epl_summary.html"

# soccerdata default cache layout (created after one manual soccerdata fetch).
SOCCERDATA_CANDIDATES = [
    Path.home() / "soccerdata" / "data" / "FBref" / "players_ENG-Premier League_2526_shooting.html",
    Path.home() / "soccerdata" / "data" / "FBref" / "players_ENG-Premier League_2525_shooting.html",
]


def _looks_like_shooting_html(text: str) -> bool:
    t = text.lower()
    if "just a moment" in t and "challenges.cloudflare" in t:
        return False
    return "stats_shooting" in t or ("sot/90" in t and "sh/90" in t)


def find_soccerdata_shooting() -> Path | None:
    for p in SOCCERDATA_CANDIDATES:
        if not p.is_file():
            continue
        try:
            head = p.read_text(encoding="utf-8", errors="replace")[:500_000]
        except OSError:
            continue
        if _looks_like_shooting_html(head):
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Explicit .html path (e.g. Downloads/Premier-League-Stats.htm)",
    )
    args = ap.parse_args()

    src: Path | None = args.source
    if src is not None:
        if not src.is_file():
            print(f"ERROR: not found: {src}")
            return 1
    else:
        src = find_soccerdata_shooting()

    if src is None:
        print("No shooting HTML found.")
        print("Save in Chrome (no Playwright):")
        print("  https://fbref.com/en/comps/9/shooting/Premier-League-Stats")
        print(f"  → {DEST}")
        print("Or pass: --source path\\to\\Premier-League-Stats.htm")
        return 1

    text = src.read_text(encoding="utf-8", errors="replace")
    if not _looks_like_shooting_html(text):
        print(f"ERROR: {src} is not a valid FBref shooting page (Cloudflare or wrong page).")
        return 1

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, DEST)
    print(f"Copied {src} -> {DEST} ({DEST.stat().st_size:,} bytes)")
    print("Next: py -3.14 Sports\\Soccer\\scripts\\step4b_attach_fbref_xg_soccer.py --refresh --season 2025-2026")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
