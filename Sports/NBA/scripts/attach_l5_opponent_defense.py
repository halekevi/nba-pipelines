#!/usr/bin/env python3
"""
attach_l5_opponent_defense.py
------------------------------
For each slate row with ESPN_ATHLETE_ID, look up the player's last N games in
proporacle_ref.db (before the slate game date) and map each opponent to
season OVERALL_DEF_RANK / DEF_TIER from defense_team_summary.csv.

This answers "how tough were the defenses they actually played against recently?"
as opposed to step3's "tonight's opponent season profile" only.

Run (after step8 CSV exists):
  py -3.14 attach_l5_opponent_defense.py \\
      --input  ..\\data\\outputs\\step8_all_direction.csv \\
      --output ..\\data\\outputs\\step8_all_direction_l5def.csv

Optional: --n 5  --defense ..\\data\\cache\\defense_team_summary.csv
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

_NBA_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.defense_tiers import def_tier_from_overall_rank  # noqa: E402

TEAM_ALIAS_FIX = {
    "BRK": "BKN",
    "BKN": "BRK",
    "GS": "GSW",
    "NO": "NOP",
    "NY": "NYK",
    "SA": "SAS",
    "PHO": "PHX",
    "WSH": "WAS",
    "UTAH": "UTA",
}


def norm_team(t: object) -> str:
    if t is None or (isinstance(t, float) and pd.isna(t)):
        return ""
    s = str(t).strip().upper()
    return TEAM_ALIAS_FIX.get(s, s)


def norm_espn_id(raw: object) -> str:
    """Match proporacle_ref.db keys (integers as plain strings, not '4432810.0')."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    s = str(raw).strip()
    if s.lower() in ("", "nan", "none"):
        return ""
    if "." in s:
        try:
            f = float(s)
            if f == int(f):
                s = str(int(f))
        except ValueError:
            pass
    return s


def _slate_before_date(row: pd.Series) -> str:
    gd = str(row.get("game_date", "")).strip()
    if gd and gd.lower() not in ("", "nan", "none"):
        return gd[:10]
    st = str(row.get("start_time", "")).strip()
    if st and "T" in st:
        return st.split("T", 1)[0][:10]
    if st and len(st) >= 10 and st[4] == "-" and st[7] == "-":
        return st[:10]
    return ""


def _opp_from_row(team: str, home: str, away: str) -> str:
    t, h, a = norm_team(team), norm_team(home), norm_team(away)
    if t == h:
        return a
    if t == a:
        return h
    return ""


def load_defense_ranks(path: Path) -> tuple[dict[str, float], dict[str, str], int]:
    d = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    d["k"] = d["TEAM_ABBREVIATION"].map(norm_team)
    rnk: dict[str, float] = {}
    tier: dict[str, str] = {}
    for _, row in d.iterrows():
        k = str(row["k"]).strip()
        if not k:
            continue
        rnk[k] = float(pd.to_numeric(row.get("OVERALL_DEF_RANK", ""), errors="coerce") or float("nan"))
        tier[k] = str(row.get("DEF_TIER", "")).strip()
    n_teams = len([x for x in rnk if pd.notna(rnk[x])])
    return rnk, tier, max(n_teams, 1)


def fetch_recent_opponents(
    con: sqlite3.Connection,
    espn_id: str,
    before_date: str,
    n: int,
) -> list[str]:
    if not espn_id or not before_date:
        return []
    q = """
        SELECT team, home_team, away_team
        FROM nba
        WHERE espn_athlete_id = ?
          AND game_date < ?
        ORDER BY game_date DESC
        LIMIT ?
    """
    rows = con.execute(q, (norm_espn_id(espn_id), before_date, int(n))).fetchall()
    out: list[str] = []
    for team, home, away in rows:
        o = _opp_from_row(team, home, away)
        if o:
            out.append(o)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="e.g. step8_all_direction.csv")
    ap.add_argument("--output", default="", help="default: input with _l5def before .csv")
    ap.add_argument(
        "--defense",
        default=str(_NBA_ROOT / "data" / "cache" / "defense_team_summary.csv"),
        help="defense_team_summary.csv",
    )
    ap.add_argument(
        "--db",
        default=str(_NBA_ROOT / "data" / "cache" / "proporacle_ref.db"),
        help="proporacle_ref.db",
    )
    ap.add_argument("--n", type=int, default=5, help="recent games (default 5)")
    args = ap.parse_args()

    inp = Path(args.input)
    out = Path(args.output) if args.output else inp.with_name(inp.stem + "_l5def" + inp.suffix)

    df = pd.read_csv(inp, dtype=str, encoding="utf-8-sig").fillna("")
    def_ranks, def_tiers, n_teams = load_defense_ranks(Path(args.defense))

    if not Path(args.db).exists():
        raise SystemExit(f"DB not found: {args.db}")

    con = sqlite3.connect(args.db)
    try:
        cache: dict[tuple[str, str], tuple[list[str], list[float], list[str]]] = {}

        def get_block(espn_id: str, before: str) -> tuple[list[str], list[float], list[str]]:
            key = (espn_id, before)
            if key in cache:
                return cache[key]
            opps = fetch_recent_opponents(con, espn_id, before, args.n)
            ranks: list[float] = []
            tiers: list[str] = []
            for o in opps:
                k = norm_team(o)
                r = def_ranks.get(k, float("nan"))
                ranks.append(r)
                tiers.append(def_tiers.get(k, "") or def_tier_from_overall_rank(r, n_teams))
            cache[key] = (opps, ranks, tiers)
            return cache[key]

        opps_col: list[str] = []
        avg_r: list[float] = []
        tnote: list[str] = []
        delta: list[float] = []
        ng: list[int] = []

        id_col = "ESPN_ATHLETE_ID" if "ESPN_ATHLETE_ID" in df.columns else ""
        if not id_col:
            raise SystemExit("Input must include ESPN_ATHLETE_ID (use full step8 CSV, not clean XLSX).")

        ton_col = "OVERALL_DEF_RANK" if "OVERALL_DEF_RANK" in df.columns else ""

        for _, row in df.iterrows():
            before = _slate_before_date(row)
            eid = norm_espn_id(row.get(id_col, ""))
            if not eid or not before:
                opps_col.append("")
                avg_r.append(float("nan"))
                tnote.append("")
                delta.append(float("nan"))
                ng.append(0)
                continue
            opps, ranks, _tiers = get_block(eid, before)
            valid = [x for x in ranks if pd.notna(x)]
            avg = float(sum(valid) / len(valid)) if valid else float("nan")
            opps_col.append("|".join(opps))
            avg_r.append(round(avg, 2) if pd.notna(avg) else float("nan"))
            ng.append(len(opps))

            tonight_r = float("nan")
            if ton_col:
                tonight_r = float(pd.to_numeric(row.get(ton_col, ""), errors="coerce"))
            if pd.notna(avg) and pd.notna(tonight_r):
                # Negative => tonight's opp rank is lower (tougher D) than recent average.
                delta.append(round(float(tonight_r) - avg, 2))
            else:
                delta.append(float("nan"))

            if not opps:
                tnote.append("no recent games in DB")
            elif not valid:
                tnote.append("opponents not in defense file")
            elif pd.notna(tonight_r) and pd.notna(avg):
                if tonight_r < avg - 0.51:
                    tnote.append("tonight tougher than recent opp avg")
                elif tonight_r > avg + 0.51:
                    tnote.append("tonight softer than recent opp avg")
                else:
                    tnote.append("tonight ~ same class as recent opps")
            else:
                tnote.append(f"L5 avg def rank {avg:.1f}")

        extra = pd.DataFrame(
            {
                "recent_l5_opp_teams": opps_col,
                "recent_l5_opp_def_avg_rank": avg_r,
                "recent_l5_games_found": ng,
                "tonight_def_rank_minus_recent_l5_avg": delta,
                "recent_l5_def_schedule_note": tnote,
            }
        )
        df = pd.concat([df.reset_index(drop=True), extra], axis=1)

    finally:
        con.close()

    df.to_csv(out, index=False, encoding="utf-8-sig")
    filled = sum(1 for x in ng if x > 0)
    print(f"✅ Saved → {out}  rows={len(df)}  with>0 recent games={filled}")


if __name__ == "__main__":
    main()
