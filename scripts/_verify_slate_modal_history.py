#!/usr/bin/env python3
"""Verify WNBA slate modal history fields survive API slim + chart path."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "ui_runner"))

from app import _slim_slate_sport_row  # noqa: E402


def history_series_for_pick(p: dict, n: int = 5) -> list[float] | None:
    """Minimal mirror of index.html historySeriesForPick stored path."""
    stored = []
    for x in (p.get("actual_series") or [])[:n]:
        try:
            stored.append(float(x))
        except (TypeError, ValueError):
            continue
    if len(stored) >= min(3, n):
        mx, mn = max(stored), min(stored)
        if mx > 1.25 or mn < 0:
            return stored
    ex: list[float] = []
    for i in range(1, 11):
        v = p.get(f"stat_g{i}") or p.get(f"g{i}")
        if v is None:
            break
        ex.append(float(v))
    if len(ex) >= min(3, n):
        mx, mn = max(ex[:n]), min(ex[:n])
        if mx > 1.25 or mn < 0:
            return ex[:n]
    return None


def main() -> int:
    bundled_path = REPO / "mobile/www/slate_sport_wnba.json"
    with bundled_path.open(encoding="utf-8") as f:
        rows = json.load(f)["rows"]

    combo = next(r for r in rows if r.get("player") == "Ariel Atkins + Alyssa Thomas")
    single = next(
        r for r in rows if r.get("player") == "Ariel Atkins" and r.get("prop") == "Assists"
    )

    ok = True
    for label, raw in [("COMBO", combo), ("SINGLE", single)]:
        slim = _slim_slate_sport_row(raw)
        hist = history_series_for_pick(slim, 5)
        act = slim.get("actual_series") or []
        print(f"=== {label}: {raw['player']} | {raw['prop']} ===")
        print(f"  slim actual_series[:5]: {act[:5]}")
        print(f"  slim stat_g1: {slim.get('stat_g1')}")
        print(f"  historySeriesForPick(5): {hist}")
        renders = bool(hist and len(hist) >= 3)
        print(f"  chart renders: {renders}")
        print()
        if label == "COMBO" and act[:5] != [12.0, 9.0, 9.0, 11.0, 9.0]:
            print("FAIL: combo actual_series[:5] mismatch")
            ok = False
        if not renders:
            print(f"FAIL: {label} chart would not render")
            ok = False

    # Fallback path: history copied from ALL_SLATE-shaped pick
    fb = {
        "player": combo["player"],
        "prop": combo["prop"],
        "line": combo["line"],
        "dir": combo["dir"],
        "sport": "WNBA",
        "edge": combo["edge"],
        "actual_series": combo["actual_series"],
        "stat_g1": combo["stat_g1"],
        "g1": combo["g1"],
    }
    fb_hist = history_series_for_pick(fb, 5)
    print("=== FALLBACK (ALL_SLATE with history fields) ===")
    print(f"  actual_series[:5]: {fb['actual_series'][:5]}")
    print(f"  chart renders: {bool(fb_hist)}")
    if not fb_hist:
        ok = False

    if ok:
        print("\nPASS: combo + single-player modal chart paths OK")
        return 0
    print("\nFAIL: verification failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
