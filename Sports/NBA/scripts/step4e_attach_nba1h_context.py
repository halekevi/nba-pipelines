#!/usr/bin/env python3
"""
NBA1H step4e — first-half player profiles from graded archive + 1H pace proxies.

Run after step4 for NBA1H period slates. Does not block pipeline on missing data.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[3]
_PROFILES = Path(__file__).resolve().parents[1] / "data" / "nba1h_player_profiles.json"
_PACE_CACHE = Path(__file__).resolve().parents[1] / "data" / "nba_team_pace_cache.json"
_MIN_SAMPLE = 5
_MAX_PROFILE_AGE_HOURS = 24


def _norm_player(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _norm_prop(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _parse_hit(row: dict) -> int | None:
    h = row.get("hit")
    if h in (0, 1, True, False):
        return int(h)
    t = str(row.get("result") or "").strip().upper()
    if t in ("HIT", "WIN", "W", "1"):
        return 1
    if t in ("MISS", "LOSS", "L", "0"):
        return 0
    return None


def build_profiles(*, graded_dir: Path | None = None) -> dict[str, dict]:
    root = graded_dir or (_REPO / "mobile" / "www")
    buckets: dict[str, list[dict]] = {}
    for path in sorted(root.glob("graded_props_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        props = data if isinstance(data, list) else data.get("props", [])
        for p in props:
            if not isinstance(p, dict):
                continue
            if str(p.get("sport", "")).strip().upper() != "NBA1H":
                continue
            if p.get("grading_suspect"):
                continue
            hit = _parse_hit(p)
            if hit is None:
                continue
            player = str(p.get("player") or "").strip()
            prop = _norm_prop(p.get("prop") or p.get("prop_type"))
            if not player or not prop:
                continue
            try:
                line = float(p.get("line"))
                actual = float(p.get("actual_value"))
            except (TypeError, ValueError):
                line = actual = None
            direction = str(p.get("direction") or p.get("over_under") or "").strip().upper()
            key = f"{_norm_player(player)}_{prop}"
            buckets.setdefault(key, []).append(
                {
                    "player": player,
                    "prop_type": prop,
                    "hit": hit,
                    "direction": direction,
                    "line": line,
                    "actual": actual,
                }
            )

    profiles: dict[str, dict] = {}
    for key, rows in buckets.items():
        n = len(rows)
        if n < _MIN_SAMPLE:
            continue
        overs = [r for r in rows if r["direction"] == "OVER"]
        unders = [r for r in rows if r["direction"] == "UNDER"]
        actuals = [r["actual"] for r in rows if r["actual"] is not None]
        lines = [r["line"] for r in rows if r["line"] is not None]
        mean_actual = float(np.mean(actuals)) if actuals else None
        mean_line = float(np.mean(lines)) if lines else None
        consistency = None
        if actuals and mean_actual and mean_actual > 0:
            consistency = float(1.0 - (np.std(actuals) / mean_actual))
            consistency = float(np.clip(consistency, 0.0, 1.0))
        ratio = None
        if mean_line is not None and mean_actual and mean_actual > 0:
            ratio = float(mean_line / mean_actual)
        profiles[key] = {
            "player": rows[0]["player"],
            "prop_type": rows[0]["prop_type"],
            "h1_hit_rate_over": float(np.mean([r["hit"] for r in overs])) if overs else None,
            "h1_hit_rate_under": float(np.mean([r["hit"] for r in unders])) if unders else None,
            "h1_mean_actual": mean_actual,
            "h1_line_mean": mean_line,
            "h1_sample_n": n,
            "h1_consistency": consistency,
            "h1_line_value_ratio": ratio,
        }
    return profiles


def _load_pace_cache() -> dict:
    if not _PACE_CACHE.is_file():
        return {}
    try:
        return json.loads(_PACE_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _team_pace(team: str, cache: dict) -> float | None:
    if not team or not cache:
        return None
    for k in (team, team.upper(), team.lower()):
        v = cache.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def attach_to_dataframe(df: pd.DataFrame, profiles: dict[str, dict]) -> pd.DataFrame:
    out = df.copy()
    pace_cache = _load_pace_cache()
    cols = [
        "h1_hit_rate_over",
        "h1_hit_rate_under",
        "h1_mean_actual",
        "h1_line_mean",
        "h1_sample_n",
        "h1_consistency",
        "h1_line_value_ratio",
        "q1_pace_proxy",
        "h1_implied_total",
    ]
    for c in cols:
        if c not in out.columns:
            out[c] = np.nan

    player_col = "player" if "player" in out.columns else "Player"
    team_col = next((c for c in ("team", "Team", "team_abbr") if c in out.columns), None)
    line_col = next((c for c in ("line", "Line", "game_line") if c in out.columns), None)
    prop_col = next((c for c in ("prop_type", "prop", "Prop Type") if c in out.columns), None)

    for idx, row in out.iterrows():
        player = str(row.get(player_col) or "")
        prop = _norm_prop(row.get(prop_col) if prop_col else "")
        pkey = f"{_norm_player(player)}_{prop}"
        prof = profiles.get(pkey)
        if prof and int(prof.get("h1_sample_n") or 0) >= _MIN_SAMPLE:
            for k in (
                "h1_hit_rate_over",
                "h1_hit_rate_under",
                "h1_mean_actual",
                "h1_line_mean",
                "h1_sample_n",
                "h1_consistency",
                "h1_line_value_ratio",
            ):
                out.at[idx, k] = prof.get(k)
        team = str(row.get(team_col) or "") if team_col else ""
        pace = _team_pace(team, pace_cache)
        if pace is not None:
            out.at[idx, "q1_pace_proxy"] = round(pace * 0.48, 2)
        team_imp = None
        for c in ("team_implied_total", "implied_team_total"):
            if c in out.columns:
                try:
                    team_imp = float(row.get(c))
                    break
                except (TypeError, ValueError):
                    pass
        if team_imp is not None and np.isfinite(team_imp):
            out.at[idx, "h1_implied_total"] = round(team_imp * 0.48, 2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default="")
    ap.add_argument("--force-refresh-profiles", action="store_true")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_absolute():
        inp = (_REPO / inp).resolve()
    out = Path(args.output or args.input)
    if not out.is_absolute():
        out = (_REPO / out).resolve()

    if not inp.is_file():
        print(f"ERROR: missing input {inp}")
        return 1

    refresh = args.force_refresh_profiles
    if not refresh and _PROFILES.is_file():
        age_h = (time.time() - _PROFILES.stat().st_mtime) / 3600.0
        refresh = age_h > _MAX_PROFILE_AGE_HOURS
    if refresh or not _PROFILES.is_file():
        profiles = build_profiles()
        _PROFILES.parent.mkdir(parents=True, exist_ok=True)
        _PROFILES.write_text(json.dumps(profiles, indent=2), encoding="utf-8")
        print(f"Built {len(profiles)} NBA1H player profiles -> {_PROFILES}")
    else:
        profiles = json.loads(_PROFILES.read_text(encoding="utf-8"))
        print(f"Using cached profiles ({len(profiles)} keys)")

    df = pd.read_csv(inp, dtype=str, encoding="utf-8-sig").fillna("")
    if df.empty:
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False, encoding="utf-8-sig")
        return 0

    df = attach_to_dataframe(df, profiles)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    n = len(df)
    filled = int(pd.to_numeric(df.get("h1_sample_n"), errors="coerce").notna().sum()) if "h1_sample_n" in df.columns else 0
    print(f"Wrote {out} ({n} rows, h1_sample_n filled on {filled})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
