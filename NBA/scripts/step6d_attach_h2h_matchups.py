#!/usr/bin/env python3
"""
step6d_attach_h2h_matchups.py (NBA) — DB version
PropOracle-NBA-S6d: Last Game vs Opponent (H2H Matchup Stats)

Migrated from flat CSV cache to proporacle_ref.db.
Queries the nba table directly — no nba_espn_boxscore_cache.csv needed.

OUTPUT COLUMNS (unchanged):
  h2h_last_stat, h2h_last_date, h2h_games_vs_opp, h2h_avg, h2h_over_rate
"""

from __future__ import annotations

import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8")
    _sys.stderr.reconfigure(encoding="utf-8")
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
    candidate = _here / "data" / "cache" / "proporacle_ref.db"
    if candidate.exists():
        DB_PATH = candidate
        break
    _here = _here.parent

# ── Prop type/prop_norm → DB column map ──────────────────────────────────────
PROP_TO_COL = {
    # display names
    "Points": "pts",
    "Rebounds": "reb",
    "Assists": "ast",
    "Steals": "stl",
    "Blocks": "blk",
    "Turnovers": "tov",
    "3-Pt Made": "fg3m",
    "Free Throws Made": "ftm",
    "Fantasy Score": "fantasy_score",
    "Pts+Rebs+Asts": "pra",
    "Pts+Rebs": "pr",
    "Pts+Asts": "pa",
    "Rebs+Asts": "ra",
    "Reb+Ast": "ra",
    "Blks+Stls": "bs",
    # prop_norm aliases
    "pts": "pts",
    "reb": "reb",
    "ast": "ast",
    "stl": "stl",
    "blk": "blk",
    "tov": "tov",
    "fg3m": "fg3m",
    "ftm": "ftm",
    "fantasy": "fantasy_score",
    "pra": "pra",
    "pr": "pr",
    "pa": "pa",
    "ra": "ra",
    "bs": "bs",
}

# ── Team normalization ────────────────────────────────────────────────────────
_ESPN_TO_PIPELINE = {
    "NY": "NYK", "NO": "NOP", "SA": "SAS", "GS": "GSW",
    "BKN": "BRK", "PHO": "PHX", "WSH": "WAS", "UTAH": "UTA",
}

def _norm_team(t: str) -> str:
    if not t or pd.isna(t):
        return ""
    t = str(t).strip().upper()
    return _ESPN_TO_PIPELINE.get(t, t)

def _norm_name(n: str) -> str:
    if not n or pd.isna(n):
        return ""
    s = unicodedata.normalize("NFD", str(n).strip().lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _resolve_stat_col(row: pd.Series) -> str | None:
    pt = str(row.get("prop_type", "") or "").strip()
    pn = str(row.get("prop_norm", "") or "").strip().lower()
    return PROP_TO_COL.get(pt) or PROP_TO_COL.get(pn)


# ── Core DB query ─────────────────────────────────────────────────────────────

def _get_h2h_games(con: sqlite3.Connection, player_norm: str,
                   opp_team: str, stat_col: str,
                   before_date: str = "") -> pd.DataFrame:
    """Return all games where player faced opp_team for the given stat column."""
    opp = _norm_team(opp_team)
    if not opp or stat_col not in PROP_TO_COL.values():
        return pd.DataFrame()

    date_clause = "AND game_date < ?" if (before_date and len(before_date) == 10) else ""
    params = [player_norm, opp, opp, opp, opp]
    if date_clause:
        params.append(before_date)

    sql = f"""
        SELECT game_date, team, home_team, away_team, {stat_col}
        FROM nba
        WHERE lower(player) = ?
          AND (upper(home_team) = ? OR upper(away_team) = ?
               OR upper(home_team) = ? OR upper(away_team) = ?)
        {date_clause}
        ORDER BY game_date ASC
    """
    try:
        rows = con.execute(sql, params).fetchall()
    except Exception:
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["game_date", "team", "home_team", "away_team", stat_col])
    df[stat_col] = pd.to_numeric(df[stat_col], errors="coerce")

    # Confirm opponent in each row matches opp_team
    df["_opp"] = df.apply(
        lambda r: _norm_team(r["home_team"])
        if _norm_team(r["team"]) == _norm_team(r["away_team"])
        else _norm_team(r["away_team"]),
        axis=1
    )
    df = df[df["_opp"] == opp].drop(columns=["_opp"])

    return df.sort_values("game_date")


def _compute_h2h(games: pd.DataFrame, stat_col: str, line: float) -> dict:
    result = {
        "h2h_last_stat":    np.nan,
        "h2h_last_date":    "",
        "h2h_games_vs_opp": 0,
        "h2h_avg":          np.nan,
        "h2h_over_rate":    np.nan,
    }
    if games.empty:
        return result

    result["h2h_games_vs_opp"] = len(games)

    # Last 10 for avg and over rate
    recent = games.tail(10)
    vals = recent[stat_col].dropna()
    if not vals.empty:
        result["h2h_avg"] = round(float(vals.mean()), 2)
        if pd.notna(line):
            try:
                result["h2h_over_rate"] = round(float((vals > float(line)).mean()), 2)
            except Exception:
                pass

    # Last game
    last = games.iloc[-1]
    result["h2h_last_date"] = str(last["game_date"])[:10]
    if pd.notna(last[stat_col]):
        result["h2h_last_stat"] = last[stat_col]

    return result


def _get_recent_games(con: sqlite3.Connection, player_norm: str,
                      stat_col: str, before_date: str = "", n: int = 10) -> pd.DataFrame:
    """Fallback: recent games for player/stat regardless of opponent."""
    if not player_norm or not stat_col:
        return pd.DataFrame()
    date_clause = "AND game_date < ?" if (before_date and len(before_date) == 10) else ""
    params = [player_norm]
    if date_clause:
        params.append(before_date)
    try:
        rows = con.execute(
            f"""
            SELECT game_date, team, home_team, away_team, {stat_col}
            FROM nba
            WHERE lower(player) = ?
              AND {stat_col} IS NOT NULL
              {date_clause}
            ORDER BY game_date DESC
            LIMIT {int(n)}
            """,
            params,
        ).fetchall()
    except Exception:
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["game_date", "team", "home_team", "away_team", stat_col])
    df[stat_col] = pd.to_numeric(df[stat_col], errors="coerce")
    return df.sort_values("game_date")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="PropOracle-NBA-S6d: H2H Stats (DB version)")
    ap.add_argument("--input",  required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--db",     default="", help="Override DB path")
    ap.add_argument("--cache",  default="", help="Legacy flag (ignored)")
    args = ap.parse_args()

    print("╔════════════════════════════════════════════════════════════════════════════╗")
    print("║      PropOracle-NBA-S6d: H2H Matchup Stats  (DB version)                     ║")
    print("╚════════════════════════════════════════════════════════════════════════════╝")
    print()

    db_path = Path(args.db) if args.db else DB_PATH

    print(f"[S6d] Loading: {args.input}")
    try:
        df = pd.read_csv(args.input, dtype={"nba_player_id": str}, low_memory=False)
    except FileNotFoundError:
        print(f"❌ Input not found: {args.input}")
        _sys.exit(1)

    print(f"  {len(df)} rows")

    # Init output columns
    for col in ["h2h_last_stat", "h2h_last_date", "h2h_games_vs_opp", "h2h_avg", "h2h_over_rate"]:
        df[col] = np.nan
    df["h2h_last_date"] = ""
    df["h2h_games_vs_opp"] = 0

    # Graceful fallback if no DB
    if not db_path.exists():
        print(f"⚠️  DB not found at {db_path} — filling with NaN")
        df.to_csv(args.output, index=False)
        print(f"✅ {args.output} (without H2H stats)")
        return

    con = sqlite3.connect(str(db_path))
    try:
        count = con.execute("SELECT COUNT(*) FROM nba").fetchone()[0]
        print(f"[S6d] DB: {count:,} NBA rows available")
    except Exception as e:
        print(f"⚠️  DB error: {e} — filling with NaN")
        df.to_csv(args.output, index=False)
        return

    print("[S6d] Computing H2H stats...")
    print()

    try:
        from tqdm import tqdm as _tqdm
    except ImportError:
        import subprocess as _sp
        _sp.check_call([_sys.executable, "-m", "pip", "install", "tqdm",
                        "--break-system-packages", "-q"])
        from tqdm import tqdm as _tqdm

    h2h_last_stats, h2h_last_dates, h2h_counts, h2h_avgs, h2h_over_rates = [], [], [], [], []
    h2h_sources = []
    matched = 0
    fallback_matched = 0

    for _, row in _tqdm(df.iterrows(), total=len(df), desc="S6d h2h lookup", unit="row"):
        player    = str(row.get("player", "")).strip()
        opp_team  = str(row.get("opp_team", "")).strip()
        prop_type = str(row.get("prop_type", "Points"))
        game_date = str(row.get("start_time", ""))[:10]
        line      = row.get("line", np.nan)

        stat_col = _resolve_stat_col(row)

        if not player or not opp_team or not stat_col:
            h2h_last_stats.append(np.nan)
            h2h_last_dates.append("")
            h2h_counts.append(0)
            h2h_avgs.append(np.nan)
            h2h_over_rates.append(np.nan)
            h2h_sources.append("")
            continue

        games = _get_h2h_games(con, _norm_name(player), opp_team, stat_col, before_date=game_date)
        source = "opp"
        if games.empty:
            games = _get_recent_games(con, _norm_name(player), stat_col, before_date=game_date, n=10)
            source = "recent" if not games.empty else ""
        stats = _compute_h2h(games, stat_col, line)

        if pd.notna(stats["h2h_last_stat"]):
            matched += 1
            if source == "recent":
                fallback_matched += 1

        h2h_last_stats.append(stats["h2h_last_stat"])
        h2h_last_dates.append(stats["h2h_last_date"])
        h2h_counts.append(stats["h2h_games_vs_opp"])
        h2h_avgs.append(stats["h2h_avg"])
        h2h_over_rates.append(stats["h2h_over_rate"])
        h2h_sources.append(source)

    df["h2h_last_stat"]    = h2h_last_stats
    df["h2h_last_date"]    = h2h_last_dates
    df["h2h_games_vs_opp"] = h2h_counts
    df["h2h_avg"]          = h2h_avgs
    df["h2h_over_rate"]    = h2h_over_rates
    df["h2h_source"]       = h2h_sources

    con.close()

    df.to_csv(args.output, index=False)

    print()
    print(f"[S6d] ✅ Matched {matched}/{len(df)} H2H combos ({100*matched/len(df):.1f}%)")
    if fallback_matched:
        print(f"  fallback recent-history matches: {fallback_matched}")
    print(f"  h2h_avg filled:       {df['h2h_avg'].notna().sum()}/{len(df)}")
    print(f"  h2h_over_rate filled: {df['h2h_over_rate'].notna().sum()}/{len(df)}")
    print(f"✅ Saved → {args.output}")


if __name__ == "__main__":
    main()
