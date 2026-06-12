#!/usr/bin/env python3
"""
Golf step4 — ESPN PGA scoreboard round history cache + stat_g1..10 attachment.

Walks dated scoreboards (?dates=YYYYMMDD-YYYYMMDD) and caches per-round stats to
Sports/Golf/cache/golf_round_cache.csv, then joins onto step2 props.

Run:
  py -3.14 Sports/Golf/scripts/step4_attach_player_stats_golf.py \\
      --input outputs/2026-06-12/golf/step2_golf_context.csv \\
      --output outputs/2026-06-12/golf/step4_golf_with_stats.csv \\
      --cache Sports/Golf/cache/golf_round_cache.csv
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
from utils.player_name_utils import normalize_player_name

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard"
CACHE_COLUMNS = [
    "espn_id",
    "player_name",
    "player_key",
    "tournament_date",
    "tournament_name",
    "round",
    "strokes",
    "birdies",
    "bogeys",
    "pars",
    "birdies_or_better",
    "bogeys_or_worse",
    "holes_played",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _f(val: object) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _player_key(name: str) -> str:
    return normalize_player_name(name).lower()


def prop_stat_key(prop_type: str) -> str | None:
    s = str(prop_type or "").strip().lower()
    if not s or "matchup" in s:
        return None
    if "greens in regulation" in s or s == "gir":
        return None
    if "fairway" in s:
        return None
    if "stroke" in s:
        return "strokes"
    if "birdies" in s and "better" in s:
        return "birdies_or_better"
    if "bogey" in s and "worse" in s:
        return "bogeys_or_worse"
    if "pars" in s or s == "par":
        return "pars"
    return None


def _parse_round_stats(rnd: dict) -> dict[str, float | None]:
    strokes = _f(rnd.get("value"))
    birdies = bogeys = eagles = doubles = other = holes = None
    categories = (rnd.get("statistics") or {}).get("categories") or []
    stats = (categories[0].get("stats") if categories else None) or []
    if len(stats) >= 6:
        birdies = _f(stats[0].get("value"))
        bogeys = _f(stats[1].get("value"))
        eagles = _f(stats[2].get("value"))
        doubles = _f(stats[3].get("value"))
        other = _f(stats[4].get("value"))
        holes = _f(stats[5].get("value"))
    birdies_or_better = None
    if birdies is not None or eagles is not None:
        birdies_or_better = (birdies or 0.0) + (eagles or 0.0)
    bogeys_or_worse = None
    if bogeys is not None:
        bogeys_or_worse = (bogeys or 0.0) + (doubles or 0.0) + (other or 0.0)
    pars = None
    if holes and holes >= 9:
        if birdies_or_better is not None and bogeys_or_worse is not None:
            pars = holes - birdies_or_better - bogeys_or_worse
            if pars < 0:
                pars = max(0.0, holes - (birdies or 0.0) - (bogeys or 0.0))
    return {
        "strokes": strokes,
        "birdies": birdies,
        "bogeys": bogeys,
        "pars": pars,
        "birdies_or_better": birdies_or_better,
        "bogeys_or_worse": bogeys_or_worse,
        "holes_played": holes,
    }


def _event_date(ev: dict) -> str:
    raw = str(ev.get("date") or ev.get("startDate") or "").strip()
    if raw:
        return raw[:10]
    comps = ev.get("competitions") or []
    if comps:
        raw = str(comps[0].get("date") or "").strip()
        if raw:
            return raw[:10]
    return ""


def _fetch_scoreboard_range(start: date, end: date) -> list[dict]:
    dates = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
    try:
        r = requests.get(SCOREBOARD_URL, params={"dates": dates}, headers=HEADERS, timeout=25)
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:
        print(f"  [WARN] scoreboard fetch failed for {dates}: {exc}")
        return []
    return list(payload.get("events") or [])


def _rows_from_events(events: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for ev in events:
        t_name = str(ev.get("name") or "").strip()
        t_date = _event_date(ev)
        for comp in ev.get("competitions") or []:
            for p in comp.get("competitors") or []:
                ath = p.get("athlete") or {}
                pname = str(ath.get("displayName") or ath.get("shortName") or "").strip()
                if not pname:
                    continue
                pk = _player_key(pname)
                aid = str(ath.get("id") or "").strip()
                for ri, rnd in enumerate(p.get("linescores") or [], start=1):
                    parsed = _parse_round_stats(rnd)
                    strokes = parsed["strokes"]
                    holes = parsed["holes_played"]
                    if strokes is None or not holes or holes < 9:
                        continue
                    rows.append(
                        {
                            "espn_id": aid,
                            "player_name": pname,
                            "player_key": pk,
                            "tournament_date": t_date,
                            "tournament_name": t_name,
                            "round": ri,
                            "strokes": strokes,
                            "birdies": parsed["birdies"],
                            "bogeys": parsed["bogeys"],
                            "pars": parsed["pars"],
                            "birdies_or_better": parsed["birdies_or_better"],
                            "bogeys_or_worse": parsed["bogeys_or_worse"],
                            "holes_played": holes,
                        }
                    )
    return rows


def _cache_is_fresh(cache_path: Path, max_age_hours: float = 24.0) -> bool:
    if not cache_path.is_file():
        return False
    age_h = (time.time() - cache_path.stat().st_mtime) / 3600.0
    return age_h < max_age_hours


def load_round_cache(cache_path: Path) -> pd.DataFrame:
    if not cache_path.is_file():
        return pd.DataFrame(columns=CACHE_COLUMNS)
    try:
        df = pd.read_csv(cache_path, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception:
        return pd.DataFrame(columns=CACHE_COLUMNS)
    for c in CACHE_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df


def refresh_round_cache(cache_path: Path, weeks_back: int = 12) -> pd.DataFrame:
    today = date.today()
    start_floor = today - timedelta(days=weeks_back * 7 + 7)
    fetched: list[dict] = []
    for w in range(weeks_back):
        w_end = today - timedelta(days=w * 7)
        w_start = w_end - timedelta(days=6)
        events = _fetch_scoreboard_range(w_start, w_end)
        fetched.extend(_rows_from_events(events))
        time.sleep(0.25)
    new_df = pd.DataFrame(fetched)
    if new_df.empty:
        print("  [WARN] ESPN round fetch returned 0 rows")
        return load_round_cache(cache_path)
    for c in ("strokes", "birdies", "bogeys", "pars", "birdies_or_better", "bogeys_or_worse", "holes_played", "round"):
        if c in new_df.columns:
            new_df[c] = pd.to_numeric(new_df[c], errors="coerce")
    new_df["player_key"] = new_df["player_name"].map(_player_key)
    old = load_round_cache(cache_path)
    merged = pd.concat([old, new_df], ignore_index=True)
    merged = merged.drop_duplicates(
        subset=["player_key", "tournament_date", "round"],
        keep="last",
    )
    merged["tournament_date"] = merged["tournament_date"].astype(str).str[:10]
    merged = merged[merged["tournament_date"] >= start_floor.isoformat()].copy()
    merged = merged.sort_values(
        ["tournament_date", "round"],
        ascending=[False, False],
        kind="mergesort",
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(cache_path, index=False, encoding="utf-8-sig")
    print(f"  [Golf step4] cache refreshed -> {cache_path} ({len(merged)} round rows)")
    return merged


def ensure_round_cache(cache_path: Path, weeks_back: int, force_refresh: bool) -> pd.DataFrame:
    if not force_refresh and _cache_is_fresh(cache_path):
        df = load_round_cache(cache_path)
        print(f"  [Golf step4] using fresh cache ({len(df)} round rows)")
        return df
    return refresh_round_cache(cache_path, weeks_back=weeks_back)


def _round_values_for_player(cache: pd.DataFrame, player_key: str, stat_col: str) -> list[float]:
    if not player_key or stat_col not in cache.columns:
        return []
    sub = cache[cache["player_key"] == player_key].copy()
    if sub.empty:
        return []
    sub = sub.sort_values(["tournament_date", "round"], ascending=[False, False], kind="mergesort")
    vals: list[float] = []
    for v in pd.to_numeric(sub[stat_col], errors="coerce"):
        if pd.isna(v):
            continue
        vals.append(float(v))
    return vals


def attach_stats(df: pd.DataFrame, cache: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "player" not in out.columns:
        out["player"] = out.get("player_name", "")
    out["player_key"] = out["player"].astype(str).map(_player_key)
    for gi in range(1, 11):
        out[f"stat_g{gi}"] = np.nan
    for col in ("stat_last5_avg", "stat_last10_avg", "stat_season_avg"):
        if col not in out.columns:
            out[col] = np.nan
    if "unsupported_prop" not in out.columns:
        out["unsupported_prop"] = 0
    if "unsupported_reason" not in out.columns:
        out["unsupported_reason"] = ""

    cache_use = cache.copy()
    if "player_key" not in cache_use.columns:
        cache_use["player_key"] = cache_use["player_name"].map(_player_key)

    players_hit = 0
    total_rounds = 0
    unique_players = out["player_key"].nunique()

    for idx, row in out.iterrows():
        pk = str(row.get("player_key") or "").strip()
        stat_col = prop_stat_key(str(row.get("prop_type") or ""))
        if stat_col is None:
            out.at[idx, "unsupported_prop"] = 1
            out.at[idx, "unsupported_reason"] = "unsupported_golf_prop"
            continue
        vals = _round_values_for_player(cache_use, pk, stat_col)
        if vals:
            players_hit += 1
            total_rounds += len(vals)
        for gi, v in enumerate(vals[:10], start=1):
            out.at[idx, f"stat_g{gi}"] = v
        if vals:
            out.at[idx, "stat_last5_avg"] = float(np.mean(vals[:5]))
            out.at[idx, "stat_last10_avg"] = float(np.mean(vals[:10]))
            out.at[idx, "stat_season_avg"] = float(np.mean(vals))

    cache_players = cache_use["player_key"].nunique() if not cache_use.empty else 0
    print(
        f"[Golf step4] cache hit: {players_hit}/{len(out)} rows "
        f"({unique_players} slate players, {cache_players} cached players, {total_rounds} rounds loaded)"
    )
    return out


def main() -> None:
    print("[Golf step4] Starting...")
    ap = argparse.ArgumentParser(description="Golf step4 — ESPN round cache + stat_g* attachment.")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--cache", default="Sports/Golf/cache/golf_round_cache.csv")
    ap.add_argument("--weeks-back", type=int, default=12)
    ap.add_argument("--refresh-cache", action="store_true", help="Force ESPN re-fetch even if cache is fresh.")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_absolute():
        inp = _REPO / inp
    out = Path(args.output)
    if not out.is_absolute():
        out = _REPO / out
    cache_path = Path(args.cache)
    if not cache_path.is_absolute():
        cache_path = _REPO / cache_path

    if not inp.is_file():
        print(f"ERROR [Golf step4] missing input: {inp}")
        sys.exit(1)

    df = pd.read_csv(inp, dtype=str, low_memory=False).fillna("")
    if df.empty:
        print("ERROR [Golf step4] empty input")
        sys.exit(1)

    cache = ensure_round_cache(cache_path, weeks_back=max(1, int(args.weeks_back)), force_refresh=args.refresh_cache)
    enriched = attach_stats(df, cache)

    out.parent.mkdir(parents=True, exist_ok=True)
    enriched.to_csv(out, index=False, encoding="utf-8-sig")
    g1 = int(pd.to_numeric(enriched.get("stat_g1"), errors="coerce").notna().sum())
    l5 = int(pd.to_numeric(enriched.get("stat_last5_avg"), errors="coerce").notna().sum())
    print(f"[Golf step4] Wrote {out} ({len(enriched)} rows) | stat_g1={g1}/{len(enriched)} | stat_last5_avg={l5}/{len(enriched)}")


if __name__ == "__main__":
    main()
