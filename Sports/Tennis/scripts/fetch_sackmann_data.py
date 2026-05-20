#!/usr/bin/env python3
"""Download Jeff Sackmann ATP/WTA match CSVs and build combined files."""

from __future__ import annotations

import argparse
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_TENNIS_ROOT = Path(__file__).resolve().parents[1]
_OUT = _TENNIS_ROOT / "data" / "sackmann"
_ATP_BASE = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/"
_WTA_BASE = "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master/"
_YEARS = (2023, 2024, 2025)
_MAX_AGE_DAYS = 7


def _download(url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PropORACLE/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            dest.write_bytes(resp.read())
        return True
    except Exception as exc:
        print(f"  [warn] download failed {url}: {exc}")
        return False


def _needs_refresh(path: Path) -> bool:
    if not path.is_file():
        return True
    age_days = (time.time() - path.stat().st_mtime) / 86400.0
    return age_days > _MAX_AGE_DAYS


def _fetch_tour(tour: str, base: str, years: tuple[int, ...]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for year in years:
        name = f"{tour}_matches_{year}.csv"
        url = f"{base}{name}"
        dest = _OUT / name
        if _needs_refresh(dest):
            print(f"  Downloading {name}...")
            if not _download(url, dest):
                if dest.is_file():
                    print(f"  Using cached {name}")
                else:
                    continue
        else:
            print(f"  Using fresh cache {name}")
        try:
            frames.append(pd.read_csv(dest, low_memory=False))
        except Exception as exc:
            print(f"  [warn] read {dest}: {exc}")
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["tour"] = tour.upper()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="Re-download even if <7 days old")
    args = ap.parse_args()
    if args.force:
        global _MAX_AGE_DAYS
        _MAX_AGE_DAYS = -1

    _OUT.mkdir(parents=True, exist_ok=True)
    atp = _fetch_tour("atp", _ATP_BASE, _YEARS)
    wta = _fetch_tour("wta", _WTA_BASE, _YEARS)

    if not atp.empty:
        p = _OUT / "atp_matches_combined.csv"
        atp.to_csv(p, index=False)
        print(f"Wrote {p} ({len(atp):,} rows)")
    else:
        print("ATP: no data (offline or missing cache)")

    if not wta.empty:
        p = _OUT / "wta_matches_combined.csv"
        wta.to_csv(p, index=False)
        print(f"Wrote {p} ({len(wta):,} rows)")
    else:
        print("WTA: no data (offline or missing cache)")

    meta = _OUT / "last_fetch.txt"
    meta.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    return 0 if (not atp.empty or not wta.empty) else 1


if __name__ == "__main__":
    raise SystemExit(main())
