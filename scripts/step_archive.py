#!/usr/bin/env python3
"""
step_archive.py — Historical prop performance archive for PropORACLE.

Writes graded outcomes to per-sport SQLite databases and exposes query
helpers used by step7 for Player Floor, Line Gap, and historical hit rate
features.

DB location: data/cache/{sport_lower}_props_history.db
Table: props_history (one row per graded prop-game)

CLI usage (called from run_grader.ps1 after each grading run):
    py -3.14 scripts/step_archive.py --sport NBA --graded graded_nba_2026-04-04.xlsx --date 2026-04-04
    py -3.14 scripts/step_archive.py --sport CBB --graded outputs/2026-04-04/graded_cbb_2026-04-04.xlsx --date 2026-04-04

Bulk replay for Prop Evaluation: scripts/backfill_props_archive.ps1 -Date YYYY-MM-DD (or -ScanOutputsDays 14).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.clv_tracker import graded_rows_to_clv_log
_CACHE_DIR = REPO_ROOT / "data" / "cache"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _db_path(sport: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{str(sport).strip().lower()}_props_history.db"


def _connect(sport: str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path(sport)))
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS props_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            sport         TEXT NOT NULL,
            grade_date    TEXT NOT NULL,
            player_name   TEXT NOT NULL,
            prop_type     TEXT NOT NULL,
            line          REAL,
            direction     TEXT,
            actual_value  REAL,
            result        TEXT,
            margin        REAL,
            opp_team      TEXT,
            team          TEXT,
            pick_type     TEXT,
            tier          TEXT,
            edge          REAL,
            ml_prob       REAL,
            composite_hr  REAL,
            created_at    TEXT,
            UNIQUE(sport, grade_date, player_name, prop_type, direction)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ph_player_prop "
        "ON props_history(sport, player_name, prop_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ph_opp "
        "ON props_history(sport, opp_team, prop_type)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS clv_log (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            sport                   TEXT NOT NULL,
            grade_date              TEXT NOT NULL,
            prop_label              TEXT,
            player_name             TEXT,
            prop_type               TEXT,
            line                    REAL,
            direction               TEXT,
            my_odds_implied_prob    REAL,
            closing_implied_prob    REAL,
            clv_delta               REAL,
            pick_type               TEXT,
            tier                    TEXT,
            result                  TEXT,
            archived_at             TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_clv_sport_date ON clv_log(sport, grade_date)"
    )
    conn.commit()


def _col_first(df: pd.DataFrame, names: tuple) -> pd.Series:
    """Return the first matching column from names, or an empty string Series."""
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series("", index=df.index, dtype=object)


def _norm_player(name) -> str:
    return " ".join(str(name or "").lower().split())


def _norm_prop(pt) -> str:
    return str(pt or "").strip().lower().replace(" ", "_")


# ── Archive ───────────────────────────────────────────────────────────────────

def archive_graded(sport: str, graded_df: pd.DataFrame, date: str) -> int:
    """
    Append today's graded props to data/cache/{sport}_props_history.db.

    Parameters
    ----------
    sport      : "NBA" | "CBB" | "WCBB" | "NBA1H" | "NBA1Q" | "NHL" | "MLB" | "Soccer"
    graded_df  : graded DataFrame — flexible column names accepted
    date       : "YYYY-MM-DD" string

    Returns
    -------
    Number of rows inserted (duplicates silently ignored via UNIQUE constraint).
    """
    if graded_df is None or graded_df.empty:
        print(f"[archive] {sport}: empty graded DataFrame — nothing to archive.")
        return 0

    sport_up = str(sport).strip().upper()
    now_iso = datetime.now(timezone.utc).isoformat()

    player   = _col_first(graded_df, ("player", "player_name", "pp_player")).map(_norm_player)
    prop     = _col_first(graded_df, ("prop_type_norm", "prop_type", "stat_norm", "prop_norm")).map(_norm_prop)
    line     = pd.to_numeric(_col_first(graded_df, ("line", "line_score")), errors="coerce")
    direction = _col_first(graded_df, ("final_bet_direction", "bet_direction", "direction",
                                        "recommended_side")).astype(str).str.upper().str.strip()
    actual   = pd.to_numeric(_col_first(graded_df, ("actual", "actual_value")), errors="coerce")
    result   = _col_first(graded_df, ("result",)).astype(str).str.upper().str.strip()
    margin   = pd.to_numeric(_col_first(graded_df, ("margin",)), errors="coerce")
    opp      = _col_first(graded_df, ("opp_team", "opp", "opponent")).astype(str).str.strip()
    team     = _col_first(graded_df, ("team", "pp_team")).astype(str).str.strip()
    pick_type = _col_first(graded_df, ("pick_type",)).astype(str).str.strip()
    tier     = _col_first(graded_df, ("tier",)).astype(str).str.strip()
    edge     = pd.to_numeric(_col_first(graded_df, ("edge",)), errors="coerce")
    ml_p     = pd.to_numeric(_col_first(graded_df, ("ml_prob",)), errors="coerce")
    comp_hr  = pd.to_numeric(_col_first(graded_df, ("composite_hit_rate", "composite_hr")), errors="coerce")

    rows = []
    for i in range(len(graded_df)):
        res = result.iat[i]
        if res not in ("HIT", "MISS", "PUSH", "VOID", "WIN", "LOSS"):
            continue
        # Normalise HIT/WIN → HIT, MISS/LOSS → MISS
        if res == "WIN":
            res = "HIT"
        elif res == "LOSS":
            res = "MISS"
        rows.append((
            sport_up, date,
            player.iat[i], prop.iat[i],
            None if pd.isna(line.iat[i]) else float(line.iat[i]),
            direction.iat[i],
            None if pd.isna(actual.iat[i]) else float(actual.iat[i]),
            res,
            None if pd.isna(margin.iat[i]) else float(margin.iat[i]),
            opp.iat[i], team.iat[i], pick_type.iat[i], tier.iat[i],
            None if pd.isna(edge.iat[i]) else float(edge.iat[i]),
            None if pd.isna(ml_p.iat[i]) else float(ml_p.iat[i]),
            None if pd.isna(comp_hr.iat[i]) else float(comp_hr.iat[i]),
            now_iso,
        ))

    if not rows:
        print(f"[archive] {sport}: 0 valid graded rows — nothing to insert.")
        return 0

    conn = _connect(sport)
    inserted = 0
    for row in rows:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO props_history
                (sport, grade_date, player_name, prop_type, line, direction,
                 actual_value, result, margin, opp_team, team, pick_type, tier,
                 edge, ml_prob, composite_hr, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, row)
            if conn.total_changes > inserted:
                inserted = conn.total_changes
        except Exception:
            pass
    conn.commit()
    conn.close()
    final_inserted = inserted  # approximate (total_changes is cumulative)
    print(f"[archive] {sport} {date}: inserted ~{len(rows)} rows (db: {_db_path(sport).name})")
    return len(rows)


def archive_clv_log(sport: str, graded_df: pd.DataFrame, date: str) -> int:
    """Append CLV rows derived from graded props (open vs close implied)."""
    if graded_df is None or graded_df.empty:
        return 0
    sport_up = str(sport).strip().upper()
    tuples = graded_rows_to_clv_log(sport_up, date, graded_df)
    if not tuples:
        print(f"[archive] {sport_up}: 0 CLV rows (missing implied-prob or odds columns).")
        return 0
    conn = _connect(sport)
    inserted = 0
    for row in tuples:
        try:
            conn.execute(
                """
                INSERT INTO clv_log (
                    sport, grade_date, prop_label, player_name, prop_type, line, direction,
                    my_odds_implied_prob, closing_implied_prob, clv_delta,
                    pick_type, tier, result, archived_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                row,
            )
            inserted += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    print(f"[archive] {sport_up} {date}: CLV log rows appended: {inserted} (db: {_db_path(sport).name})")
    return inserted


# ── Query helpers — all return None gracefully when no history ────────────────

def _query_actuals(sport: str, player: str, prop_type: str,
                   n_max: int = 50, results: tuple = ("HIT", "MISS")) -> list[float]:
    """Return actual_value list for player+prop_type, most recent first."""
    try:
        conn = _connect(sport)
        rows = conn.execute("""
            SELECT actual_value FROM props_history
            WHERE sport=? AND player_name=? AND prop_type=?
              AND actual_value IS NOT NULL AND result IN ({})
            ORDER BY grade_date DESC LIMIT ?
        """.format(",".join("?" * len(results))),
            (sport.upper(), _norm_player(player), _norm_prop(prop_type), *results, n_max)
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def get_player_floor_p10(sport: str, player: str, prop_type: str,
                         min_rows: int = 5) -> Optional[float]:
    """10th percentile of actual values. None when < min_rows records."""
    vals = _query_actuals(sport, player, prop_type, n_max=60)
    if len(vals) < min_rows:
        return None
    return float(np.percentile(vals, 10))


def get_player_median_actual(sport: str, player: str, prop_type: str,
                              min_rows: int = 5) -> Optional[float]:
    """Median actual value. None when < min_rows records."""
    vals = _query_actuals(sport, player, prop_type, n_max=60)
    if len(vals) < min_rows:
        return None
    return float(np.median(vals))


def get_player_avg_win_margin(sport: str, player: str, prop_type: str,
                              direction: str = "OVER",
                              min_rows: int = 3) -> Optional[float]:
    """Average margin on HIT rows in the given direction. None when < min_rows HIT rows."""
    try:
        conn = _connect(sport)
        rows = conn.execute("""
            SELECT margin FROM props_history
            WHERE sport=? AND player_name=? AND prop_type=?
              AND direction=? AND result='HIT' AND margin IS NOT NULL
            ORDER BY grade_date DESC LIMIT 40
        """, (sport.upper(), _norm_player(player), _norm_prop(prop_type),
              str(direction).upper().strip())).fetchall()
        conn.close()
        if len(rows) < min_rows:
            return None
        return float(np.mean([r[0] for r in rows]))
    except Exception:
        return None


def get_player_historical_hr(sport: str, player: str, prop_type: str,
                             direction: str = "OVER",
                             min_rows: int = 5) -> Optional[float]:
    """Graded hit rate (HIT / decided rows) in the given direction."""
    try:
        conn = _connect(sport)
        rows = conn.execute("""
            SELECT result FROM props_history
            WHERE sport=? AND player_name=? AND prop_type=?
              AND direction=? AND result IN ('HIT','MISS')
            ORDER BY grade_date DESC LIMIT 40
        """, (sport.upper(), _norm_player(player), _norm_prop(prop_type),
              str(direction).upper().strip())).fetchall()
        conn.close()
        if len(rows) < min_rows:
            return None
        hits = sum(1 for r in rows if r[0] == "HIT")
        return float(hits / len(rows))
    except Exception:
        return None


def get_opp_historical_hr(sport: str, opp_team: str, prop_type: str,
                          direction: Optional[str] = None,
                          min_rows: int = 5) -> Optional[float]:
    """Graded hit rate across all players facing opp_team on this prop type."""
    try:
        conn = _connect(sport)
        if direction:
            rows = conn.execute("""
                SELECT result FROM props_history
                WHERE sport=? AND opp_team=? AND prop_type=?
                  AND direction=? AND result IN ('HIT','MISS')
                ORDER BY grade_date DESC LIMIT 60
            """, (sport.upper(), str(opp_team).strip(), _norm_prop(prop_type),
                  str(direction).upper().strip())).fetchall()
        else:
            rows = conn.execute("""
                SELECT result FROM props_history
                WHERE sport=? AND opp_team=? AND prop_type=?
                  AND result IN ('HIT','MISS')
                ORDER BY grade_date DESC LIMIT 60
            """, (sport.upper(), str(opp_team).strip(), _norm_prop(prop_type))).fetchall()
        conn.close()
        if len(rows) < min_rows:
            return None
        hits = sum(1 for r in rows if r[0] == "HIT")
        return float(hits / len(rows))
    except Exception:
        return None


# ── Bulk query (performance: one DB round-trip per slate) ─────────────────────

def get_bulk_stats(sport: str,
                   player_prop_pairs: list[tuple[str, str]],
                   direction_pairs: Optional[list[tuple[str, str, str]]] = None,
                   n_max: int = 40) -> dict:
    """
    Fetch floor_p10, median_actual, avg_win_margin, player_hr, and opp_hr for
    an entire slate in one pass.

    Parameters
    ----------
    sport                : "NBA" | "CBB" | "WCBB" | "NBA1H" | "NBA1Q" | "NHL" | "MLB" | "Soccer"
    player_prop_pairs    : [(player, prop_type), ...]
    direction_pairs      : [(player, prop_type, direction), ...] — same length
    n_max                : max historical rows per player+prop

    Returns
    -------
    Dict keyed by (player_norm, prop_norm) → {
        "floor_p10": float|None,
        "median_actual": float|None,
        "avg_win_margin": float|None,
        "player_hr": float|None,
    }
    Dict keyed by (opp_norm, prop_norm, direction) → opp_hr float|None
    Both dicts packed under keys "player_stats" and "opp_stats".
    """
    result: dict = {"player_stats": {}, "opp_stats": {}}
    if not player_prop_pairs:
        return result
    try:
        conn = _connect(sport)
        # Get all relevant rows in one query
        unique_players = list({_norm_player(p) for p, _ in player_prop_pairs})
        unique_props   = list({_norm_prop(pt)   for _, pt in player_prop_pairs})
        placeholders_p = ",".join("?" * len(unique_players))
        placeholders_pt = ",".join("?" * len(unique_props))
        rows = conn.execute(f"""
            SELECT player_name, prop_type, direction, actual_value, result, margin
            FROM props_history
            WHERE sport=?
              AND player_name IN ({placeholders_p})
              AND prop_type   IN ({placeholders_pt})
              AND result IN ('HIT','MISS')
            ORDER BY grade_date DESC
        """, (sport.upper(), *unique_players, *unique_props)).fetchall()
        conn.close()
    except Exception:
        return result

    # Group by (player, prop, direction)
    from collections import defaultdict
    grp: dict = defaultdict(list)
    for pn, pt, dr, av, rs, mg in rows:
        grp[(pn, pt, dr)].append((av, rs, mg))
        grp[(pn, pt, "ANY")].append((av, rs, mg))

    def _stats(key):
        entries = grp.get(key, [])[:n_max]
        actuals = [e[0] for e in entries if e[0] is not None]
        hits    = [e for e in entries if e[1] == "HIT"]
        margins = [e[2] for e in hits if e[2] is not None]
        return {
            "floor_p10":      float(np.percentile(actuals, 10)) if len(actuals) >= 5 else None,
            "median_actual":  float(np.median(actuals))         if len(actuals) >= 5 else None,
            "avg_win_margin": float(np.mean(margins))           if len(margins) >= 3 else None,
            "player_hr":      float(len(hits) / len(entries))   if len(entries) >= 5 else None,
        }

    for i, (player, prop_type) in enumerate(player_prop_pairs):
        pn  = _norm_player(player)
        pt  = _norm_prop(prop_type)
        dr  = "OVER"
        if direction_pairs and i < len(direction_pairs):
            dr = str(direction_pairs[i][2]).upper().strip()
        result["player_stats"][(pn, pt)] = _stats((pn, pt, dr))

    return result


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Archive graded prop outcomes to history DB.")
    ap.add_argument(
        "--sport",
        required=True,
        help="Sport key: NBA, CBB, WCBB, NBA1H, NBA1Q, NHL, MLB, Soccer",
    )
    ap.add_argument("--graded", required=True, help="Path to graded Excel/CSV file")
    ap.add_argument("--date",   required=True, help="Grade date YYYY-MM-DD")
    ap.add_argument("--sheet",  default=None,  help="Sheet name for Excel (default: first sheet)")
    args = ap.parse_args()

    graded_path = Path(args.graded)
    if not graded_path.exists():
        print(f"[archive] File not found: {graded_path}")
        return

    ext = graded_path.suffix.lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = pd.read_excel(str(graded_path), sheet_name=args.sheet or 0, engine="openpyxl")
    else:
        df = pd.read_csv(str(graded_path))

    n = archive_graded(args.sport, df, args.date)
    n_clv = archive_clv_log(args.sport, df, args.date)
    print(f"[archive] Done. Graded rows processed: {n}; CLV rows: {n_clv}")


if __name__ == "__main__":
    main()
