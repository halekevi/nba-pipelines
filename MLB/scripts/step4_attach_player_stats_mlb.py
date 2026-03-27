#!/usr/bin/env python3
"""
step4_attach_player_stats_mlb.py  (MLB Pipeline)

Pulls last-N game stats from the official MLB Stats API:
  https://statsapi.mlb.com/api/v1/people/{id}/stats?stats=gameLog&group=hitting&season={year}
  https://statsapi.mlb.com/api/v1/people/{id}/stats?stats=gameLog&group=pitching&season={year}

Handles:
  - Hitter props: hits, total_bases, home_runs, rbi, runs, walks,
                  stolen_bases, fantasy_score, hits_runs_rbi, singles, doubles, triples
  - Pitcher props: strikeouts, pitching_outs, innings_pitched, hits_allowed,
                   earned_runs, walks_allowed, batters_faced

Outputs:
  step4_mlb_with_stats.csv
  mlb_stats_cache.csv   (grows over time — don't delete)

Run:
  py -3.14 step4_attach_player_stats_mlb.py \
    --input step3_mlb_with_defense.csv \
    --cache mlb_stats_cache.csv \
    --output step4_mlb_with_stats.csv
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

# Ensure <repo>/PropOracle is on sys.path so we can import PropOracle-level helpers.
_PROPORACLE_ROOT = Path(__file__).resolve().parents[1]
if str(_PROPORACLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROPORACLE_ROOT))

from scripts.db_utils import ensure_mlb_schema, log_pipeline_health, open_db, upsert_rows

COMBO_SEP = "|"

MLB_HEADERS = {
    # Browser-like headers to avoid intermittent MLB Stats API 405/blocks.
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Origin": "https://www.mlb.com",
    "Referer": "https://www.mlb.com/",
    "Connection": "keep-alive",
}

GAMELOG_URL = (
    "https://statsapi.mlb.com/api/v1/people/{player_id}/stats"
    "?stats=gameLog&group={group}&season={season}&language=en"
)

PITCHER_PROPS = {
    "strikeouts", "pitching_outs", "innings_pitched",
    "hits_allowed", "earned_runs", "walks_allowed", "batters_faced",
}


def _sleep(base: float = 0.4) -> None:
    time.sleep(max(0.0, base + random.uniform(0, 0.3)))


def _get(url: str, retries: int = 3) -> Optional[dict]:
    for attempt in range(1, retries + 1):
        try:
            _sleep(0.4)
            r = requests.get(url, headers=MLB_HEADERS, timeout=20)
            if r.status_code == 404:
                return None

            # Treat 405 as soft rate-limiting / blocking; back off briefly then retry.
            if r.status_code in (405, 429):
                if attempt < retries:
                    time.sleep(1.0 + 0.75 * attempt)  # short, non-hammering backoff
                    continue
                log_pipeline_health(
                    "mlb.step4_attach_player_stats",
                    "mlb_api_get_blocked",
                    extra={"url": url, "status_code": r.status_code, "attempts": retries},
                    start=Path(__file__),
                )
                return None

            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt < retries:
                time.sleep(2.0 * attempt)
                continue
            log_pipeline_health(
                "mlb.step4_attach_player_stats",
                "mlb_api_get_failed",
                extra={"url": url, "attempts": retries},
                start=Path(__file__),
            )
    return None


def _parse_ids(mlb_player_id: str) -> List[str]:
    s = str(mlb_player_id).strip()
    if not s or s == "nan":
        return []

    def _norm_id_token(token: str) -> str:
        t = str(token).strip()
        if not t:
            return ""
        try:
            # CSV round-trips can turn IDs into "123456.0" strings.
            n = float(t)
            if np.isnan(n):
                return ""
            i = int(n)
            return str(i) if i > 0 else ""
        except Exception:
            return t if t.isdigit() else ""

    if COMBO_SEP in s:
        return [nid for nid in (_norm_id_token(p) for p in s.split(COMBO_SEP)) if nid]
    nid = _norm_id_token(s)
    return [nid] if nid else []


def fmt_num(x: float) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    return f"{float(x):.3f}".rstrip("0").rstrip(".")


def _ip_to_outs(ip_str) -> float:
    """Convert 'innings pitched' string like '6.1' to decimal outs (6*3+1=19)."""
    try:
        ip = float(ip_str)
        full   = int(ip)
        partial = round((ip - full) * 10)   # .1 → 1 out, .2 → 2 outs
        return float(full * 3 + partial)
    except (TypeError, ValueError):
        return np.nan


def derive_hitter_stat(game: dict, prop_norm: str) -> float:
    """Extract a stat value from a MLB Stats API game log entry (hitter)."""
    s = game.get("stat") or {}

    def g(key, default=np.nan):
        v = s.get(key)
        try:
            return float(v) if v is not None and str(v).strip() not in ("", "-", ".---") else default
        except (ValueError, TypeError):
            return default

    h  = g("hits",        0)
    hr = g("homeRuns",    0)
    bb = g("baseOnBalls", 0)
    sb = g("stolenBases", 0)
    rbi= g("rbi",         0)
    r  = g("runs",        0)
    ab = g("atBats",      0)

    # singles = hits - doubles - triples - HR
    d2 = g("doubles",  0)
    t3 = g("triples",  0)
    sg = max(0.0, h - d2 - t3 - hr)

    total_bases = sg * 1 + d2 * 2 + t3 * 3 + hr * 4
    fantasy     = h * 3 + d2 * 2 + t3 * 5 + hr * 7 + rbi * 2 + r * 2 + bb * 2 + sb * 5
    hits_r_rbi  = h + r + rbi

    mapping = {
        "hits":           h,
        "total_bases":    total_bases,
        "home_runs":      hr,
        "rbi":            rbi,
        "runs":           r,
        "walks":          bb,
        "stolen_bases":   sb,
        "fantasy_score":  fantasy,
        "hits_runs_rbi":  hits_r_rbi,
        "singles":        sg,
        "doubles":        d2,
        "triples":        t3,
    }
    return mapping.get(prop_norm, np.nan)


def derive_pitcher_stat(game: dict, prop_norm: str) -> float:
    """Extract a stat value from a MLB Stats API game log entry (pitcher)."""
    s = game.get("stat") or {}

    def g(key, default=np.nan):
        v = s.get(key)
        try:
            return float(v) if v is not None and str(v).strip() not in ("", "-", ".---") else default
        except (ValueError, TypeError):
            return default

    ip_str    = s.get("inningsPitched", "0")
    outs      = _ip_to_outs(ip_str)
    ip_dec    = float(outs) / 3.0 if not np.isnan(outs) else np.nan

    so        = g("strikeOuts",      0)
    ha        = g("hits",            0)
    er        = g("earnedRuns",      0)
    bb        = g("baseOnBalls",     0)
    bf        = g("battersFaced",    0)

    mapping = {
        "strikeouts":      so,
        "pitching_outs":   outs,
        "innings_pitched": ip_dec,
        "hits_allowed":    ha,
        "earned_runs":     er,
        "walks_allowed":   bb,
        "batters_faced":   bf,
    }
    return mapping.get(prop_norm, np.nan)


# ── Cache management ──────────────────────────────────────────────────────────

CACHE_COLS = [
    "MLB_PLAYER_ID", "SEASON", "GAME_DATE", "GAME_ID",
    "PLAYER_TYPE", "PROP_NORM", "STAT_VALUE",
]

def load_cache(path: Path) -> pd.DataFrame:
    if path.exists():
        try:
            df = pd.read_csv(path, dtype=str, low_memory=False).fillna("")
            print(f"  Loaded cache: {len(df)} rows from {path.name}")
            return df
        except Exception as e:
            print(f"  ⚠️ Could not load cache: {e}")
    return pd.DataFrame(columns=CACHE_COLS)


def save_cache(cache: pd.DataFrame, path: Path) -> None:
    cache.to_csv(path, index=False, encoding="utf-8-sig")


def fetch_game_log(player_id: str, group: str, season: str) -> List[dict]:
    """Fetch raw game log entries from MLB Stats API."""
    url  = GAMELOG_URL.format(player_id=player_id, group=group, season=season)
    data = _get(url)
    if not data:
        return []
    for stat_block in (data.get("stats") or []):
        splits = stat_block.get("splits") or []
        if splits:
            return splits
    return []


def update_cache(
    cache: pd.DataFrame,
    player_id: str,
    player_type: str,
    season: str,
    n_games: int,
) -> Tuple[pd.DataFrame, int]:
    """Fetch game log and add new rows to cache."""
    group = "pitching" if player_type == "pitcher" else "hitting"

    existing_game_ids = set(
        cache.loc[
            (cache["MLB_PLAYER_ID"].astype(str) == str(player_id)) &
            (cache["SEASON"].astype(str)         == str(season)),
            "GAME_ID",
        ].astype(str).tolist()
    )

    splits  = fetch_game_log(player_id, group, season)
    # Most-recent first
    splits  = list(reversed(splits))
    added   = 0
    new_rows = []

    prop_list = (
        ["strikeouts", "pitching_outs", "innings_pitched",
         "hits_allowed", "earned_runs", "walks_allowed", "batters_faced"]
        if player_type == "pitcher" else
        ["hits", "total_bases", "home_runs", "rbi", "runs", "walks",
         "stolen_bases", "fantasy_score", "hits_runs_rbi", "singles", "doubles", "triples"]
    )
    derive_fn = derive_pitcher_stat if player_type == "pitcher" else derive_hitter_stat

    for split in splits:
        game_id  = str(split.get("game", {}).get("gamePk", "")).strip()
        date_str = str(split.get("date", "")).strip()
        if not game_id:
            continue
        if game_id in existing_game_ids:
            continue

        for prop_norm in prop_list:
            val = derive_fn(split, prop_norm)
            # ── Bouncer: reject impossible/junk values ───────────────────────
            if val is not None and not (isinstance(val, float) and np.isnan(val)):
                try:
                    v = float(val)
                except Exception:
                    continue
                if v < 0:
                    continue
                # generous caps
                if prop_norm in ("hits", "total_bases", "hits_runs_rbi") and v > 25:
                    continue
                if prop_norm in ("home_runs", "rbi", "runs", "walks", "stolen_bases") and v > 10:
                    continue
                if prop_norm in ("strikeouts", "pitching_outs", "batters_faced") and v > 100:
                    continue
                if prop_norm in ("innings_pitched",) and v > 15:
                    continue
                if prop_norm in ("earned_runs", "hits_allowed", "walks_allowed") and v > 30:
                    continue

            new_rows.append({
                "MLB_PLAYER_ID": str(player_id),
                "SEASON":        str(season),
                "GAME_DATE":     date_str,
                "GAME_ID":       game_id,
                "PLAYER_TYPE":   player_type,
                "PROP_NORM":     prop_norm,
                "STAT_VALUE":    fmt_num(val) if not np.isnan(val) else "",
            })

        existing_game_ids.add(game_id)
        added += 1
        if added >= n_games:
            break

    if new_rows:
        cache = pd.concat([cache, pd.DataFrame(new_rows)], ignore_index=True)

    return cache, added


def get_vals_from_cache(
    cache: pd.DataFrame,
    player_id: str,
    prop_norm: str,
    season: str,
    n: int = 10,
) -> List[float]:
    """Return most-recent N stat values from cache for player+prop+season."""
    mask = (
        (cache["MLB_PLAYER_ID"].astype(str) == str(player_id)) &
        (cache["SEASON"].astype(str)         == str(season))    &
        (cache["PROP_NORM"].astype(str)       == str(prop_norm)) &
        (cache["STAT_VALUE"].astype(str).str.strip() != "")
    )
    sub = cache.loc[mask].copy()
    if sub.empty:
        return []

    sub["GAME_DATE"] = pd.to_datetime(sub["GAME_DATE"], errors="coerce")
    sub = sub.sort_values("GAME_DATE", ascending=False)
    vals = pd.to_numeric(sub["STAT_VALUE"], errors="coerce").dropna().tolist()
    return vals[:n]


def calc_hit_context(vals: List[float], line: float, k: int = 5):
    recent = vals[:k] if len(vals) >= k else vals
    if not recent:
        return 0, 0, 0, np.nan, np.nan, np.nan
    over  = sum(1 for v in recent if v >  line)
    under = sum(1 for v in recent if v <  line)
    push  = sum(1 for v in recent if v == line)
    played = len(recent)
    hr_all = over / played if played else np.nan
    denom  = over + under
    hr_ou  = over  / denom if denom else np.nan
    ur_ou  = under / denom if denom else np.nan
    return over, under, push, hr_all, hr_ou, ur_ou


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",        default="MLB/scripts/step3_mlb_with_defense.csv")
    ap.add_argument("--cache",        default="MLB/scripts/mlb_stats_cache.csv")
    ap.add_argument("--output",       default="MLB/scripts/step4_mlb_with_stats.csv")
    ap.add_argument("--db",           default="", help="Override DB path (default: data/cache/proporacle_ref.db)")
    ap.add_argument("--n",            type=int,   default=10, help="Games per player")
    ap.add_argument("--season",       default="2026")
    ap.add_argument("--debug_misses", default="")
    args = ap.parse_args()

    print(f"→ Loading Step3: {args.input}")
    slate = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig").fillna("")

    # Central DB mirror (MLB game logs)
    db_path = Path(args.db) if args.db else None
    con = open_db(db_path)
    ensure_mlb_schema(con)

    cache_path = Path(args.cache)
    cache      = load_cache(cache_path)

    N         = int(args.n)
    stat_cols = [f"stat_g{i}" for i in range(1, N + 1)]
    out_cols  = stat_cols + [
        "stat_last5_avg", "stat_last10_avg", "stat_season_avg",
        "last5_over", "last5_under", "last5_push", "last5_hit_rate",
        "line_hit_rate_over_ou_5", "line_hit_rate_under_ou_5",
        "line_hit_rate_over_ou_10", "line_hit_rate_under_ou_10",
        "stat_status",
    ]
    for c in out_cols:
        if c not in slate.columns:
            slate[c] = ""

    slate["_line_num"] = pd.to_numeric(slate.get("line", ""), errors="coerce")

    misses: list = []
    cache_updates = 0
    # Allow up to 2 refresh attempts per (player_id, player_type) in a single run.
    attempted_refresh: dict[tuple[str, str], int] = {}
    max_refresh_attempts = 2

    print(f"\n→ Attaching stats | rows={len(slate)}")

    for idx, row in slate.iterrows():
        prop         = str(row.get("prop_norm",     "")).lower().strip()
        player       = str(row.get("player",        "")).strip()
        team         = str(row.get("team",          "")).strip()
        ptype        = str(row.get("player_type",   "")).lower().strip()
        mlb_id_raw   = str(row.get("mlb_player_id", "")).strip()
        line         = row.get("_line_num", np.nan)
        try:
            line = float(line)
        except Exception:
            line = np.nan

        ids      = _parse_ids(mlb_id_raw)
        is_combo = (len(ids) > 1) or (
            str(row.get("is_combo_player", "")).strip().lower() in ("1", "true", "yes")
        )

        if not ids:
            slate.at[idx, "stat_status"] = "NO_MLB_PLAYER_ID"
            misses.append({"player": player, "team": team, "prop_norm": prop,
                           "line": str(row.get("line", "")), "mlb_player_id": mlb_id_raw})
            continue

        # Infer player_type from prop if missing
        if ptype not in ("pitcher", "hitter"):
            from step2_attach_picktypes_mlb import PITCHER_PROPS
            ptype = "pitcher" if prop in PITCHER_PROPS else "hitter"

        # ── Single player ──
        if not is_combo:
            pid = ids[0]
            cached_vals = get_vals_from_cache(cache, pid, prop, args.season, n=N)
            if len(cached_vals) < 3:
                key = (pid, ptype)
                attempts = attempted_refresh.get(key, 0)
            else:
                attempts = max_refresh_attempts

            if len(cached_vals) < 3 and attempts < max_refresh_attempts:
                attempted_refresh[key] = attempts + 1
                cache, added = update_cache(cache, pid, ptype, args.season, n_games=N)
                if added > 0:
                    cache_updates += added
                    save_cache(cache, cache_path)
                    # Mirror newly added cache rows into central DB
                    try:
                        from datetime import datetime, timezone
                        fresh = cache.loc[
                            (cache["MLB_PLAYER_ID"].astype(str) == str(pid)) &
                            (cache["SEASON"].astype(str) == str(args.season))
                        ].copy()
                        if not fresh.empty:
                            fresh["STAT_VALUE_NUM"] = pd.to_numeric(fresh["STAT_VALUE"], errors="coerce")
                            ts = datetime.now(timezone.utc).isoformat()
                            rows_db = []
                            for _, r in fresh.iterrows():
                                rows_db.append({
                                    "mlb_player_id": str(r.get("MLB_PLAYER_ID", "")).strip(),
                                    "season": str(r.get("SEASON", "")).strip(),
                                    "game_date": str(r.get("GAME_DATE", "")).strip()[:10],
                                    "game_id": str(r.get("GAME_ID", "")).strip(),
                                    "player_type": str(r.get("PLAYER_TYPE", "")).strip() or None,
                                    "prop_norm": str(r.get("PROP_NORM", "")).strip(),
                                    "stat_value": float(r["STAT_VALUE_NUM"]) if not pd.isna(r.get("STAT_VALUE_NUM")) else None,
                                    "updated_at": ts,
                                })
                            upsert_rows(con, "mlb_gamelog", rows_db)
                    except Exception as e:
                        log_pipeline_health(
                            "mlb.step4_attach_player_stats",
                            "db_mirror_failed",
                            extra={"mlb_player_id": pid, "error": f"{type(e).__name__}: {e}"},
                            start=Path(__file__),
                        )
                cached_vals = get_vals_from_cache(cache, pid, prop, args.season, n=N)

            if not cached_vals:
                slate.at[idx, "stat_status"] = "NO_CACHE_DATA"
                continue
            vals = cached_vals

        # ── Combo ──
        else:
            p_names = [str(row.get(f"player_{i}", "")).strip() or player for i in range(1, len(ids) + 1)]

            per_player_vals = []
            any_empty = False
            for i, pid in enumerate(ids):
                # Determine player type per player in combo
                sub_ptype = "hitter"  # combos are always hitter+hitter
                cv = get_vals_from_cache(cache, pid, prop, args.season, n=N)
                if len(cv) < 3:
                    key = (pid, sub_ptype)
                    attempts = attempted_refresh.get(key, 0)
                else:
                    attempts = max_refresh_attempts

                if len(cv) < 3 and attempts < max_refresh_attempts:
                    attempted_refresh[key] = attempts + 1
                    cache, added = update_cache(cache, pid, sub_ptype, args.season, n_games=N)
                    if added > 0:
                        cache_updates += added
                        save_cache(cache, cache_path)
                    cv = get_vals_from_cache(cache, pid, prop, args.season, n=N)
                if not cv:
                    any_empty = True
                    break
                per_player_vals.append(cv)

            if any_empty or not per_player_vals:
                slate.at[idx, "stat_status"] = "NO_CACHE_DATA"
                continue

            min_g = min(len(pv) for pv in per_player_vals)
            vals  = [float(sum(pv[i] for pv in per_player_vals)) for i in range(min_g)]

            if not vals:
                slate.at[idx, "stat_status"] = "INSUFFICIENT_GAMES"
                continue

        # ── Fill output ──
        for i in range(1, N + 1):
            v = vals[i - 1] if (i - 1) < len(vals) else np.nan
            slate.at[idx, f"stat_g{i}"] = fmt_num(v)

        def avg_k(k: int) -> float:
            s = vals[:k] if len(vals) >= k else vals
            return float(np.mean(s)) if s else np.nan

        slate.at[idx, "stat_last5_avg"]  = fmt_num(avg_k(5))
        slate.at[idx, "stat_last10_avg"] = fmt_num(avg_k(10))
        slate.at[idx, "stat_season_avg"] = fmt_num(float(np.mean(vals)) if vals else np.nan)

        if not np.isnan(line):
            o5, u5, p5, hr5, hr5_ou, ur5_ou = calc_hit_context(vals, line, k=5)
            slate.at[idx, "last5_over"]               = str(o5)
            slate.at[idx, "last5_under"]              = str(u5)
            slate.at[idx, "last5_push"]               = str(p5)
            slate.at[idx, "last5_hit_rate"]           = fmt_num(hr5)
            slate.at[idx, "line_hit_rate_over_ou_5"]  = fmt_num(hr5_ou)
            slate.at[idx, "line_hit_rate_under_ou_5"] = fmt_num(ur5_ou)

            _, _, _, _, hr10_ou, ur10_ou = calc_hit_context(vals, line, k=10)
            slate.at[idx, "line_hit_rate_over_ou_10"]  = fmt_num(hr10_ou)
            slate.at[idx, "line_hit_rate_under_ou_10"] = fmt_num(ur10_ou)

        slate.at[idx, "stat_status"] = "OK"

    if args.debug_misses and misses:
        pd.DataFrame(misses).drop_duplicates().to_csv(
            args.debug_misses, index=False, encoding="utf-8-sig"
        )
        print(f"Wrote misses → {args.debug_misses}")

    slate.drop(columns=["_line_num"], errors="ignore", inplace=True)
    slate.to_csv(args.output, index=False, encoding="utf-8-sig")

    print(f"\n✅ Saved → {args.output}")
    print(f"Cache updates: {cache_updates}")
    print("\nstat_status breakdown:")
    print(slate["stat_status"].astype(str).value_counts().to_string())
    con.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_pipeline_health(
            "mlb.step4_attach_player_stats",
            "run_failed",
            extra={"error": f"{type(e).__name__}: {e}"},
            start=Path(__file__),
        )
        print(f"❌ MLB step4 failed (logged). {type(e).__name__}: {e}")
