#!/usr/bin/env python3
"""
step4_attach_player_stats_soccer.py  (DB version)

Replaces live ESPN summary API calls (was 30+ min, 1000+ API calls)
with indexed reads from proporacle_ref.db.

The DB is populated nightly by build_boxscore_ref.py (called from run_grader.ps1).

Usage:
    py step4_attach_player_stats_soccer.py \
        --input  step3_soccer_with_defense.csv \
        --output step4_soccer_with_stats.csv

Soccer lookup is by espn_player_id. The ID is attached upstream in step1/step2.
If espn_player_id is missing from your slate, the row gets stat_status=NO_ID.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Set

import pandas as pd

# Walk up from this file to find scripts/step4_db_reader.py
_here = Path(__file__).resolve().parent
for _ in range(6):
    if (_here / "scripts" / "step4_db_reader.py").exists():
        sys.path.insert(0, str(_here / "scripts"))
        break
    _here = _here.parent
from step4_db_reader import open_db, attach_stats, db_summary, DB_PATH

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs

SOCCER_TEAM_KEY_MAP = {
    # slate full name -> DB code
    "GIBRALTAR": "GIB",
    "ALBANIA": "ALB",   # may not exist in DB yet, will return -1 gracefully
    "BOSNIA": "BIH",
    "CZECHIA": "CZE",
    "DENMARK": "DEN",
    "ITALY": "ITA",
    "KOSOVO": "KOS",
    "LATVIA": "LAT",
    "N. IRELAND": "NIR",
    "N. MACEDONIA": "MKD",
    "POLAND": "POL",
    "ROMANIA": "ROU",
    "SLOVAKIA": "SVK",
    "SWEDEN": "SWE",
    "UKRAINE": "UKR",
    "WALES": "WAL",
    "TÜRKIYE": "TUR",
    "REP. IRELAND": "IRL",
    "SAN LORENZO": "SLO",
    "ARGENTINOS": "ARGJ",
    "RIESTRA": "RIE",
    "LANÚS": "LAN",
    # NWSL clubs
    "CHICAGO STARS": "CHI",
    "KC CURRENT": "KC",
    "PRIDE": "ORL",   # Orlando Pride
    "REIGN": "SEA",   # OL Reign (Seattle)
    "ROYALS": "UTA",   # Utah Royals
    "SAN DIEGO WAVE": "SD",
    "SPIRIT": "WAS",
    "THORNS": "POR",   # Portland Thorns
}


def _parse_slate_game_date(row: pd.Series) -> str:
    for col in ("game_date", "game_start", "start_time", "fetched_at"):
        raw = str(row.get(col, "") or "").strip()
        if not raw:
            continue
        ts = pd.to_datetime(raw, utc=True, errors="coerce")
        if pd.notna(ts):
            return ts.strftime("%Y-%m-%d")
        if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
            return raw[:10]
    return ""


def compute_rest_days(con, team: str, game_date: str, table: str = "soccer") -> int:
    team = str(team or "").strip().upper()
    game_date = str(game_date or "").strip()
    if len(game_date) >= 10:
        game_date = game_date[:10]
    if not team or len(game_date) < 10:
        return -1
    try:
        prev = con.execute(
            f"SELECT MAX(game_date) FROM {table} WHERE team = ? AND game_date < ?",
            (team, game_date),
        ).fetchone()
        prev_date = prev[0] if prev and prev[0] else None
        if not prev_date:
            return -1
        days = (
            datetime.strptime(game_date, "%Y-%m-%d")
            - datetime.strptime(str(prev_date)[:10], "%Y-%m-%d")
        ).days
        return int(days)
    except Exception:
        return -1


def _soccer_db_slate_team_overlap(con, slate_teams: Set[str]) -> bool:
    try:
        rows = con.execute(
            "SELECT DISTINCT team FROM soccer WHERE team IS NOT NULL AND team != '' "
            "ORDER BY team LIMIT 20"
        ).fetchall()
        db_sample = [str(r[0]).strip().upper() for r in rows if r and r[0]]
    except Exception:
        db_sample = []
    print(f"[B2B] Soccer DB teams (first 20): {db_sample}")
    slate_list = sorted(t for t in slate_teams if t)[:20]
    print(f"[B2B] Soccer slate teams (first 20): {slate_list}")
    if not slate_list or not db_sample:
        return False
    try:
        db_all = {
            str(r[0]).strip().upper()
            for r in con.execute(
                "SELECT DISTINCT team FROM soccer WHERE team IS NOT NULL AND team != ''"
            ).fetchall()
            if r and r[0]
        }
    except Exception:
        db_all = set(db_sample)
    mapped_slate = {SOCCER_TEAM_KEY_MAP.get(t, t) for t in slate_teams if t}
    overlap = mapped_slate & db_all
    if len(overlap) == 0:
        return False
    return len(overlap) >= max(1, int(0.25 * len(slate_list)))


def attach_b2b_columns(
    df: pd.DataFrame, con, table: str = "soccer", sport_label: str = "Soccer", enabled: bool = True
) -> pd.DataFrame:
    out = df.copy()
    out["days_rest"] = -1
    out["is_back_to_back"] = 0
    out["opp_days_rest"] = -1
    out["opp_b2b"] = 0
    if not enabled:
        # TODO: Soccer team key mismatch — verify soccer DB table uses same team keys as step3 slate.
        print(f"[B2B] {sport_label}: {len(out)} rows, 0 back-to-backs found (team key mismatch; days_rest=-1)")
        return out
    if "team" not in out.columns:
        print(f"[B2B] {sport_label}: 0 rows, 0 back-to-backs found (no team column)")
        return out

    game_dates = out.apply(_parse_slate_game_date, axis=1)
    rest_cache: dict[tuple[str, str], int] = {}

    def _lookup(team_val: str, gd: str) -> int:
        raw_team = str(team_val or "").strip().upper()
        gd_s = str(gd or "").strip()[:10]
        if not raw_team or len(gd_s) < 10:
            return -1
        db_team = SOCCER_TEAM_KEY_MAP.get(raw_team, raw_team)
        key = (db_team, gd_s)
        if key not in rest_cache:
            rest_cache[key] = compute_rest_days(con, db_team, gd_s, table=table)
        return rest_cache[key]

    out["days_rest"] = [_lookup(out.at[i, "team"], game_dates.at[i]) for i in out.index]
    out["is_back_to_back"] = (pd.to_numeric(out["days_rest"], errors="coerce") == 1).astype(int)
    if "opp_team" in out.columns:
        out["opp_days_rest"] = [_lookup(out.at[i, "opp_team"], game_dates.at[i]) for i in out.index]
        out["opp_b2b"] = (pd.to_numeric(out["opp_days_rest"], errors="coerce") == 1).astype(int)
    b2b_n = int((out["is_back_to_back"] == 1).sum())
    print(f"[B2B] {sport_label}: {len(out)} rows, {b2b_n} back-to-backs found")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",       default="step3_soccer_with_defense.csv")
    ap.add_argument("--cache",       default="",
                    help="Legacy arg — ignored (DB is the cache now)")
    ap.add_argument("--output",      default="step4_soccer_with_stats.csv")
    ap.add_argument("--n",           type=int, default=10)
    ap.add_argument("--db",          default="", help="Override DB path")
    ap.add_argument("--show-misses", action="store_true",
                    help="Print players with NO_DATA or NO_ID")
    ap.add_argument("--summary",     action="store_true")
    # Legacy args accepted but ignored (kept for run_pipeline.ps1 compatibility)
    ap.add_argument("--workers",     type=int, default=6)
    ap.add_argument("--season",      default="2025")
    ap.add_argument("--debug_misses",  default="")
    ap.add_argument("--debug_player",  default="")
    ap.add_argument("--debug_player_raw", default="")
    ap.add_argument("--league",      default="")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else DB_PATH
    con = open_db(db_path)

    if args.summary:
        db_summary(con)
        return

    print(f"→ Loading slate: {args.input}")
    slate = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig").fillna("")
    print(f"  {len(slate)} rows")

    # espn_player_id is the primary key for soccer DB lookups
    id_col = next(
        (c for c in ["espn_player_id", "ESPN_PLAYER_ID", "espn_id"] if c in slate.columns),
        None
    )
    if not id_col:
        raise SystemExit(
            "No espn_player_id column found — soccer step4 requires ESPN IDs.\n"
            f"Columns available: {list(slate.columns)}"
        )

    print(f"\n→ Attaching Soccer stats from DB (id_col={id_col}, n={args.n})...")
    match_before = float((slate[id_col].astype(str).str.strip() != "").mean())
    print(f"[SOCCER ID] step4 input match rate: {match_before*100:.1f}% "
          f"({int((slate[id_col].astype(str).str.strip()!='').sum())}/{len(slate)})")
    slate, counts = attach_stats(
        slate, "soccer", con,
        id_col=id_col,
        n=args.n
    )
    match_after = float((slate[id_col].astype(str).str.strip() != "").mean())
    print(f"[SOCCER ID] step4 post-attach match rate: {match_after*100:.1f}% "
          f"({int((slate[id_col].astype(str).str.strip()!='').sum())}/{len(slate)})")

    if args.show_misses or args.debug_misses:
        bad = slate[slate["stat_status"].isin(["NO_DATA", "NO_ID"])][[
            id_col, "player", "prop_norm", "line", "stat_status"
        ]].drop_duplicates()
        if not bad.empty:
            print(f"\n⚠️  Missing stats ({len(bad)} rows):")
            print(bad.to_string(index=False))
        if args.debug_misses and not bad.empty:
            bad.to_csv(args.debug_misses, index=False, encoding="utf-8-sig")
            print(f"Wrote misses → {args.debug_misses}")

    slate_teams = set(slate["team"].astype(str).str.strip().str.upper().unique()) if "team" in slate.columns else set()
    soccer_b2b_ok = _soccer_db_slate_team_overlap(con, slate_teams)
    slate = attach_b2b_columns(slate, con, table="soccer", sport_label="Soccer", enabled=soccer_b2b_ok)

    slate.to_csv(args.output, index=False, encoding="utf-8-sig")
    copy_pipeline_output_to_dated_dirs(
        output_path=args.output,
        df=slate,
        sport_dir_name="Soccer",
        repo_root=_REPO_ROOT,
    )
    unresolved = slate[slate[id_col].astype(str).str.strip() == ""].copy()
    if not unresolved.empty:
        if "start_time" in unresolved.columns and unresolved["start_time"].astype(str).str.strip().ne("").any():
            ds = pd.to_datetime(unresolved["start_time"], errors="coerce").min()
            date_tag = ds.strftime("%Y-%m-%d") if pd.notna(ds) else datetime.now().strftime("%Y-%m-%d")
        else:
            date_tag = datetime.now().strftime("%Y-%m-%d")
        out_unmatched = Path(args.output).resolve().parent / f"unmatched_soccer_players_{date_tag}.csv"
        keep_cols = [c for c in ["player", "team", "prop_type", "pp_game_id"] if c in unresolved.columns]
        unresolved[keep_cols].rename(columns={"player": "player_name"}).drop_duplicates().to_csv(
            out_unmatched, index=False, encoding="utf-8-sig"
        )
        print(f"[SOCCER ID] Wrote unresolved players: {out_unmatched}")
    print(f"\n✅ Saved → {args.output}  ({len(slate)} rows)")
    print("\nstat_status breakdown:")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        if v > 0:
            print(f"  {k:25s} {v:>5}")


if __name__ == "__main__":
    main()
