#!/usr/bin/env python3
"""
step6e_attach_intel.py
PropOracle-NBA-S6e: Attach Intel Layer

Reads directly from proporacle_ref.db (same DB used by step4/step6d) to compute:
  1. Season hit rate at the exact line being offered
  2. L5 / L10 / season avg from full-season DB (accurate, not truncated)
  3. Consistency score (CV%) — how variable the player is on this prop
  4. Opponent defense generosity vs league average for this prop type

All stats are computed live from the DB so they stay accurate automatically
as build_boxscore_ref.py adds new games nightly. No separate cache files needed.

Input:  data/outputs/step6d_with_h2h.csv
Output: data/outputs/step6e_with_intel.csv

New columns added:
  intel_season_games      — total games in DB for this player/prop
  intel_season_avg        — full-season average
  intel_season_std        — standard deviation
  intel_l5_avg            — last 5 games average
  intel_l10_avg           — last 10 games average
  intel_l5_vs_season      — L5 minus season avg (positive = trending up)
  intel_l10_vs_season     — L10 minus season avg
  intel_cv_pct            — coefficient of variation % (lower = more consistent)
  intel_season_hit_rate   — % of season games where player went OVER this line
  intel_cushion           — season avg minus line (positive = soft line)
  intel_opp_avg_allowed   — avg this stat allowed per player-game vs this opponent
  intel_opp_vs_league_pct — how much more/less than league avg opponent allows (+= generous)
  intel_opp_avg_allowed_pos   — same, filtered to player's position bucket (G/F/C)
  intel_opp_vs_league_pct_pos — position-split vs league (when sample >= 10)

Run:
  py -3.14 scripts/step6e_attach_intel.py
      --input  data/outputs/step6d_with_h2h.csv
      --output data/outputs/step6e_with_intel.csv
"""

from __future__ import annotations

import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import argparse
import sqlite3
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd


# ── DB path resolution ────────────────────────────────────────────────────────
_here = Path(__file__).resolve().parent
DB_PATH = Path("data/cache/proporacle_ref.db")
for _ in range(6):
    candidate = _here.parent / "data" / "cache" / "proporacle_ref.db"
    if candidate.exists():
        DB_PATH = candidate
        break
    _here = _here.parent


# ── Prop → DB column mapping ──────────────────────────────────────────────────
PROP_TO_DB = {
    # prop_type display names
    "Points":           "pts",
    "Rebounds":         "reb",
    "Assists":          "ast",
    "Steals":           "stl",
    "Blocks":           "blk",
    "Turnovers":        "tov",
    "3-Pt Made":        "fg3m",
    "FTM":              "ftm",
    "Free Throws Made": "ftm",
    "Fantasy Score":    "fantasy_score",
    "Pts+Rebs+Asts":    "pra",
    "Pts+Rebs":         "pr",
    "Pts+Asts":         "pa",
    "Reb+Ast":          "ra",
    "Blks+Stls":        "bs",
    # prop_norm short names
    "pts":              "pts",
    "reb":              "reb",
    "ast":              "ast",
    "stl":              "stl",
    "blk":              "blk",
    "tov":              "tov",
    "fg3m":             "fg3m",
    "3pm":              "fg3m",
    "ftm":              "ftm",
    "fantasy":          "fantasy_score",
    "pra":              "pra",
    "pr":               "pr",
    "pa":               "pa",
    "ra":               "ra",
    "bs":               "bs",
    "rebs+asts":        "ra",
    "blks+stls":        "bs",
}

# Case-insensitive prop_type aliases (slate casing varies)
_PROP_TO_DB_LOWER = {k.lower(): v for k, v in PROP_TO_DB.items()}

# ── Team normalisation: pipeline → DB ────────────────────────────────────────
TEAM_NORM = {
    "BRK": "BKN",  "PHO": "PHX",  "NOP": "NO",
    "NYK": "NY",   "SAS": "SA",   "GSW": "GS",
    "WAS": "WSH",  "UTA": "UTAH",
}

# ESPN position values in DB are G/F/C; slate may use PF/PG/SF/SG.
_POS_NORMALIZE: dict[str, str] = {
    "PG": "G", "SG": "G",
    "SF": "F", "PF": "F",
    "C": "C",
    "G": "G", "F": "F",
}


def _normalize_pos(pos: str) -> str | None:
    """Return DB-compatible position bucket or None if unrecognized."""
    return _POS_NORMALIZE.get(str(pos).strip().upper())


def _norm_name(n: str) -> str:
    if not n or pd.isna(n):
        return ""
    s = unicodedata.normalize("NFD", str(n).strip().lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _norm_team(t: str) -> str:
    if not t or pd.isna(t):
        return ""
    t = str(t).strip().upper()
    return TEAM_NORM.get(t, t)


def _db_col(row: pd.Series) -> str | None:
    pt = str(row.get("prop_type", "") or "").strip()
    pn = str(row.get("prop_norm", "") or "").strip().lower()
    return (
        PROP_TO_DB.get(pt)
        or PROP_TO_DB.get(pn)
        or _PROP_TO_DB_LOWER.get(pt.lower())
    )


# ── DB queries ────────────────────────────────────────────────────────────────

def _player_vals(con: sqlite3.Connection, player_norm: str,
                 db_col: str, before_date: str = "") -> list[float]:
    """All season values for player/stat, newest first, optionally before a date.
    DB stores players with accents already stripped (e.g. 'Luka Doncic'),
    so matching on lower(player) against our normalized name works correctly.
    """
    if not db_col or not player_norm:
        return []
    date_clause = "AND game_date < ?" if before_date else ""
    params: list = [player_norm]
    if before_date:
        params.append(before_date)
    try:
        rows = con.execute(f"""
            SELECT {db_col}
            FROM nba
            WHERE lower(player) = ?
              AND {db_col} IS NOT NULL
              {date_clause}
            ORDER BY game_date DESC
        """, params).fetchall()
        return [float(r[0]) for r in rows if r[0] is not None]
    except Exception:
        return []


def _opp_vals(con: sqlite3.Connection, opp_team: str,
              db_col: str, position: str | None = None) -> list[float]:
    """Per-player-game values allowed by opp_team for a stat (full season).
    DB has no opp_team column — derive opponent from home_team/away_team vs team.
    A player's opponent is: home_team if the player's team == away_team, else away_team.
    Optional position filters nba.position to G/F/C bucket.
    """
    if not db_col or not opp_team:
        return []
    try:
        pos_clause = " AND upper(position) = upper(?)" if position else ""
        params: list = [opp_team, opp_team, opp_team]
        if position:
            params.append(position)
        rows = con.execute(f"""
            SELECT {db_col}
            FROM nba
            WHERE (
                (upper(team) != upper(?) AND (upper(home_team) = upper(?) OR upper(away_team) = upper(?)))
            )
              AND {db_col} IS NOT NULL
            {pos_clause}
        """, params).fetchall()
        return [float(r[0]) for r in rows if r[0] is not None]
    except Exception:
        return []


# ── Per-row intel computation ─────────────────────────────────────────────────

EMPTY_INTEL = {
    "intel_season_games":      np.nan,
    "intel_season_avg":        np.nan,
    "intel_season_std":        np.nan,
    "intel_l5_avg":            np.nan,
    "intel_l10_avg":           np.nan,
    "intel_l5_vs_season":      np.nan,
    "intel_l10_vs_season":     np.nan,
    "intel_cv_pct":            np.nan,
    "intel_season_hit_rate":   np.nan,
    "intel_cushion":           np.nan,
    "intel_opp_avg_allowed":        np.nan,
    "intel_opp_vs_league_pct":      np.nan,
    "intel_opp_avg_allowed_pos":    np.nan,
    "intel_opp_vs_league_pct_pos":  np.nan,
}


def compute_intel(row: pd.Series, con: sqlite3.Connection,
                  league_avgs: dict) -> dict:
    result = dict(EMPTY_INTEL)

    db_col      = _db_col(row)
    if not db_col:
        return result

    player_norm = _norm_name(str(row.get("player", "")))
    opp_team    = _norm_team(str(row.get("opp_team", "")))
    game_date   = str(row.get("start_time", ""))[:10]
    line        = row.get("line", np.nan)

    # ── Player season stats ───────────────────────────────────────────────────
    vals = _player_vals(con, player_norm, db_col, before_date=game_date)

    if vals:
        arr = np.array(vals, dtype=float)
        n   = len(arr)
        avg = float(np.mean(arr))
        std = float(np.std(arr, ddof=1)) if n > 1 else 0.0
        cv  = round(std / avg * 100, 1) if avg > 0 else 999.0

        l5_avg  = float(np.mean(arr[:5]))
        l10_avg = float(np.mean(arr[:10]))

        result["intel_season_games"]  = n
        result["intel_season_avg"]    = round(avg, 3)
        result["intel_season_std"]    = round(std, 3)
        result["intel_l5_avg"]        = round(l5_avg, 3)
        result["intel_l10_avg"]       = round(l10_avg, 3)
        result["intel_l5_vs_season"]  = round(l5_avg - avg, 3)
        result["intel_l10_vs_season"] = round(l10_avg - avg, 3)
        result["intel_cv_pct"]        = cv

        try:
            line_f = float(line)
            hits   = int(np.sum(arr > line_f))
            result["intel_season_hit_rate"] = round(hits / n * 100, 1)
            result["intel_cushion"]         = round(avg - line_f, 3)
        except (TypeError, ValueError):
            pass

    # ── Opponent defense ──────────────────────────────────────────────────────
    if opp_team:
        raw_pos = str(row.get("pos", row.get("position", ""))).strip()
        db_pos = _normalize_pos(raw_pos)

        stat_for_opp = db_col

        # ── Pooled (all positions) ────────────────────────────────────────────
        opp_vals = _opp_vals(con, opp_team, stat_for_opp)
        # 3-PT Made: some DB builds are sparse on fg3m vs opp; fg3a is a stable
        # "3PT volume allowed" proxy aligned with opponent 3PT defense curves.
        if not opp_vals and db_col == "fg3m":
            stat_for_opp = "fg3a"
            opp_vals = _opp_vals(con, opp_team, stat_for_opp)
        if opp_vals:
            opp_avg = float(np.mean(opp_vals))
            lg_avg = league_avgs.get(stat_for_opp) or league_avgs.get(db_col)
            result["intel_opp_avg_allowed"] = round(opp_avg, 3)
            if lg_avg and lg_avg > 0:
                result["intel_opp_vs_league_pct"] = round(
                    (opp_avg / lg_avg - 1) * 100, 1
                )

        # ── Position-split (≥10 player-games vs opp; else pooled only) ───────
        if db_pos:
            opp_vals_pos = _opp_vals(con, opp_team, stat_for_opp, position=db_pos)
            if not opp_vals_pos and db_col == "fg3m":
                opp_vals_pos = _opp_vals(con, opp_team, "fg3a", position=db_pos)
            if len(opp_vals_pos) >= 10:
                opp_avg_pos = float(np.mean(opp_vals_pos))
                lg_avg = league_avgs.get(stat_for_opp) or league_avgs.get(db_col)
                result["intel_opp_avg_allowed_pos"] = round(opp_avg_pos, 3)
                if lg_avg and lg_avg > 0:
                    result["intel_opp_vs_league_pct_pos"] = round(
                        (opp_avg_pos / lg_avg - 1) * 100, 1
                    )

    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  default="data/outputs/step6d_with_h2h.csv")
    ap.add_argument("--output", default="data/outputs/step6e_with_intel.csv")
    ap.add_argument("--db",     default="", help="Override DB path")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else DB_PATH

    # Graceful fallback if DB not found
    if not db_path.exists():
        print(f"⚠️  DB not found at {db_path} — passing through without intel")
        print(f"   Run: py scripts/build_boxscore_ref.py --backfill --days 150")
        df = pd.read_csv(args.input, encoding="utf-8-sig")
        for col in EMPTY_INTEL:
            df[col] = np.nan
        df.to_csv(args.output, index=False, encoding="utf-8")
        print(f"✅ Saved (no intel) → {args.output}")
        return

    print(f"[S6e] Input:  {args.input}")
    df = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig").fillna("")
    print(f"      {len(df)} rows")

    con = sqlite3.connect(str(db_path))
    db_rows = con.execute("SELECT COUNT(*) FROM nba").fetchone()[0]
    db_min  = con.execute("SELECT MIN(game_date) FROM nba").fetchone()[0]
    db_max  = con.execute("SELECT MAX(game_date) FROM nba").fetchone()[0]
    print(f"[S6e] DB: {db_rows:,} rows | {db_min} → {db_max}")

    # Pre-compute league averages once (include fg3a for 3PM opp-defense fallback)
    league_avgs: dict = {}
    for col in set(PROP_TO_DB.values()) | {"fg3a"}:
        try:
            v = con.execute(
                f"SELECT AVG({col}) FROM nba WHERE {col} IS NOT NULL"
            ).fetchone()[0]
            if v is not None:
                league_avgs[col] = float(v)
        except Exception:
            pass
    print(f"[S6e] League averages ready for {len(league_avgs)} stat columns")
    print("[S6e] Computing intel per row...")

    intel_rows = []
    matched = 0
    for _, row in df.iterrows():
        intel = compute_intel(row, con, league_avgs)
        intel_rows.append(intel)
        if not np.isnan(intel.get("intel_season_avg", np.nan)):
            matched += 1

    con.close()

    intel_df = pd.DataFrame(intel_rows, index=df.index)
    out = pd.concat([df, intel_df], axis=1)
    out.to_csv(args.output, index=False, encoding="utf-8")

    pct = matched / len(df) * 100 if len(df) else 0
    print(f"\n[S6e] Results:")
    print(f"  Matched rows:              {matched}/{len(df)} ({pct:.1f}%)")
    print(f"  intel_season_hit_rate:     {intel_df['intel_season_hit_rate'].notna().sum()} filled")
    print(f"  intel_cv_pct:              {intel_df['intel_cv_pct'].notna().sum()} filled")
    print(f"  intel_opp_vs_league_pct:   {intel_df['intel_opp_vs_league_pct'].notna().sum()} filled")
    print(f"  intel_cushion:             {intel_df['intel_cushion'].notna().sum()} filled")
    print(f"✅ Saved → {args.output}")


if __name__ == "__main__":
    main()
