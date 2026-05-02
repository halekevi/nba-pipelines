#!/usr/bin/env python3
"""
step6a_attach_opponent_stats_NBA.py  (DB version)
PropOracle-NBA-S6a: Opponent-Specific Player Performance Stats

Migrated from flat CSV cache to proporacle_ref.db.
Queries the nba table directly — no nba_espn_boxscore_cache.csv needed.

OUTPUT COLUMNS (unchanged):
  opp_l10_pts, opp_l10_reb, opp_l10_ast, opp_l10_stl, opp_l10_blk,
  opp_last_game_pts, opp_last_game_reb, opp_last_game_ast,
  opp_last_game_date, opp_games_played, opp_home_avg_pts,
  opp_away_avg_pts, opp_last_3_avg_pts
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

# ── DB path resolution (walk-up pattern matching step4_db_reader) ─────────────
_here = Path(__file__).resolve().parent
DB_PATH = Path("data/cache/proporacle_ref.db")
for _ in range(6):
    candidate = _here / "data" / "cache" / "proporacle_ref.db"
    if candidate.exists():
        DB_PATH = candidate
        break
    _here = _here.parent

# ── Output columns (identical to old CSV-based version) ───────────────────────
OPP_COLS = [
    "opp_l10_pts", "opp_l10_reb", "opp_l10_ast", "opp_l10_stl", "opp_l10_blk",
    "opp_last_game_pts", "opp_last_game_reb", "opp_last_game_ast",
    "opp_last_game_date", "opp_games_played", "opp_home_avg_pts",
    "opp_away_avg_pts", "opp_last_3_avg_pts",
]

# ── Team normalization ────────────────────────────────────────────────────────
_ESPN_TO_PIPELINE = {
    "NY": "NYK", "NO": "NOP", "SA": "SAS", "GS": "GSW",
    "BKN": "BRK", "PHO": "PHX", "WSH": "WAS",
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


# ── Core DB query ─────────────────────────────────────────────────────────────

def _get_player_vs_opp(con: sqlite3.Connection, player_norm: str,
                        opp_team: str, before_date: str = "") -> pd.DataFrame:
    """
    Return all games where player faced opp_team, optionally before a date.
    Uses home_team/away_team columns to identify the opponent.
    """
    opp = _norm_team(opp_team)
    if not opp:
        return pd.DataFrame()

    params = [player_norm, opp, opp, opp, opp]
    date_clause = ""
    if before_date and len(before_date) == 10:
        date_clause = "AND game_date < ?"
        params.append(before_date)

    sql = f"""
        SELECT game_date, team, home_team, away_team,
               pts, reb, ast, stl, blk
        FROM nba
        WHERE lower(player) = ?
          AND (upper(home_team) = ? OR upper(away_team) = ?
               OR upper(home_team) = ? OR upper(away_team) = ?)
        {date_clause}
        ORDER BY game_date ASC
    """
    # Also try with pipeline normalization
    opp_variants = list({opp, _norm_team(opp)})

    try:
        rows = con.execute(sql, params).fetchall()
    except Exception:
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["game_date", "team", "home_team",
                                      "away_team", "pts", "reb", "ast", "stl", "blk"])
    for col in ["pts", "reb", "ast", "stl", "blk"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Filter to rows where the opponent is actually opp_team
    # (player's team != opp, and opp appears in the game)
    df["_opp_norm"] = df.apply(
        lambda r: _norm_team(r["home_team"])
        if _norm_team(r["team"]) == _norm_team(r["away_team"])
        else _norm_team(r["away_team"]),
        axis=1
    )
    df = df[df["_opp_norm"] == opp].drop(columns=["_opp_norm"])

    return df.sort_values("game_date")


def _compute_opp_stats(games: pd.DataFrame) -> dict:
    result = {col: np.nan for col in OPP_COLS}
    if games.empty:
        return result

    result["opp_games_played"] = len(games)

    # Last 10 averages
    l10 = games.tail(10)
    for stat in ["pts", "reb", "ast", "stl", "blk"]:
        v = l10[stat].mean()
        if not np.isnan(v):
            result[f"opp_l10_{stat}"] = round(v, 2)

    # Last game
    last = games.iloc[-1]
    result["opp_last_game_date"] = str(last["game_date"])[:10]
    for stat in ["pts", "reb", "ast"]:
        val = last[stat]
        if not pd.isna(val):
            result[f"opp_last_game_{stat}"] = val

    # Last 3 avg pts
    l3_pts = games.tail(3)["pts"].mean()
    if not np.isnan(l3_pts):
        result["opp_last_3_avg_pts"] = round(l3_pts, 2)

    # Home/away splits
    home_g = games[games["team"].str.upper() == games["home_team"].str.upper()]
    away_g = games[games["team"].str.upper() != games["home_team"].str.upper()]
    if not home_g.empty:
        v = home_g["pts"].mean()
        if not np.isnan(v):
            result["opp_home_avg_pts"] = round(v, 2)
    if not away_g.empty:
        v = away_g["pts"].mean()
        if not np.isnan(v):
            result["opp_away_avg_pts"] = round(v, 2)

    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="PropOracle-NBA-S6a: Opponent Stats (DB version)")
    ap.add_argument("--input",     required=True)
    ap.add_argument("--output",    required=True)
    ap.add_argument("--db",        default="", help="Override DB path")
    ap.add_argument("--cache",     default="", help="Legacy flag (ignored)")
    ap.add_argument("--opp-cache", default="", help="Legacy flag (ignored)")
    ap.add_argument("--max-rows",  type=int, default=0)
    args = ap.parse_args()

    print("""
╔════════════════════════════════════════════════════════════════════════════╗
║          PropOracle-NBA-S6a: Opponent Stats  (DB version)                     ║
╚════════════════════════════════════════════════════════════════════════════╝
""")

    db_path = Path(args.db) if args.db else DB_PATH

    # Load input
    print(f"[S6a] Loading: {args.input}")
    try:
        df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")
    except FileNotFoundError:
        print(f"❌ Input not found: {args.input}")
        _sys.exit(1)

    if len(df) == 0:
        df.to_csv(args.output, index=False, encoding="utf-8-sig")
        return

    if args.max_rows > 0:
        df = df.head(args.max_rows)
    print(f"  Rows: {len(df)}")

    # Init output columns
    for col in OPP_COLS:
        df[col] = np.nan

    # Graceful fallback if DB missing
    if not db_path.exists():
        print(f"⚠️  DB not found at {db_path} — filling with NaN")
        df.to_csv(args.output, index=False, encoding="utf-8-sig")
        print(f"✅ {args.output} (without opponent stats)")
        return

    con = sqlite3.connect(str(db_path))
    try:
        count = con.execute("SELECT COUNT(*) FROM nba").fetchone()[0]
        print(f"[S6a] DB: {count:,} NBA rows available")
    except Exception as e:
        print(f"⚠️  DB error: {e} — filling with NaN")
        df.to_csv(args.output, index=False, encoding="utf-8-sig")
        return

    print(f"[S6a] Computing opponent stats...")

    try:
        from tqdm import tqdm as _tqdm
    except ImportError:
        import subprocess as _sp
        _sp.check_call([_sys.executable, "-m", "pip", "install", "tqdm",
                        "--break-system-packages", "-q"])
        from tqdm import tqdm as _tqdm

    results = []
    matched = 0

    for _, row in _tqdm(df.iterrows(), total=len(df), desc="S6a opp stats", unit="row"):
        player   = str(row.get("player", "")).strip()
        opp_team = str(row.get("opp_team", "")).strip()
        game_date = str(row.get("start_time", ""))[:10]

        if not player or not opp_team:
            results.append({col: np.nan for col in OPP_COLS})
            continue

        games = _get_player_vs_opp(con, _norm_name(player), opp_team, before_date=game_date)
        stats = _compute_opp_stats(games)
        if not np.isnan(stats.get("opp_games_played", np.nan)):
            matched += 1
        results.append(stats)

    opp_df = pd.DataFrame(results, index=df.index)
    opp_df["opp_last_game_date"] = opp_df["opp_last_game_date"].astype(str).replace("nan", "")
    for col in OPP_COLS:
        df[col] = opp_df[col]

    con.close()

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    filled = df["opp_l10_pts"].notna().sum()
    print(f"\n[S6a] ✅ Matched {matched}/{len(df)} player-vs-opponent combos")
    print(f"  opp_l10_pts filled: {filled}/{len(df)} ({100*filled/len(df):.1f}%)")
    print(f"✅ Saved → {args.output}")


if __name__ == "__main__":
    main()
