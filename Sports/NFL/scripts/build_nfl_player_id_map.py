#!/usr/bin/env python3
"""
Build NFL ESPN athlete master + PrizePicks → ESPN ID map from roster pull.

Inputs:
  - data/rosters/nfl_rosters.csv
  - Latest data/NFL/step1_pp_nfl_*.csv (optional)

Outputs:
  - data/reference/nfl_athletes_master.csv
  - data/reference/pp_to_espn_id_map_nfl.csv
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
ROSTER_PATH = REPO_ROOT / "data" / "rosters" / "nfl_rosters.csv"
MASTER_PATH = REPO_ROOT / "data" / "reference" / "nfl_athletes_master.csv"
PP_MAP_PATH = REPO_ROOT / "data" / "reference" / "pp_to_espn_id_map_nfl.csv"

PP_GLOB_PATTERNS = (
    REPO_ROOT / "data" / "NFL" / "step1_pp_nfl_*.csv",
    REPO_ROOT / "Sports" / "NFL" / "data" / "step1_pp_nfl_*.csv",
    REPO_ROOT / "Sports" / "NFL" / "data" / "outputs" / "step1_pp_*.csv",
)

MASTER_COLS = ["espn_player_id", "player_name", "team_abbr", "position", "jersey", "status"]
PP_MAP_COLS = [
    "pp_player_id",
    "espn_athlete_id",
    "player_name",
    "team_abbr",
    "position",
    "source",
    "updated_at",
]

_SUFFIX_RE = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?", re.IGNORECASE)
_FUZZY_THRESHOLD = 88


def norm_name(s: str) -> str:
    s = s or ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9\s'-]", " ", s)
    s = _SUFFIX_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def token_sort_ratio(a: str, b: str) -> float:
    ta = " ".join(sorted(norm_name(a).split()))
    tb = " ".join(sorted(norm_name(b).split()))
    if not ta or not tb:
        return 0.0
    return SequenceMatcher(None, ta, tb).ratio() * 100.0


def find_latest_pp_csv() -> Path | None:
    candidates: list[Path] = []
    for pattern in PP_GLOB_PATTERNS:
        candidates.extend(pattern.parent.glob(pattern.name))
    candidates = [p for p in candidates if p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def build_master_from_roster(roster_path: Path) -> pd.DataFrame:
    df = pd.read_csv(roster_path, dtype=str).fillna("")
    if "player_id" not in df.columns:
        raise SystemExit(f"Roster missing player_id column: {roster_path}")

    master = (
        df.rename(columns={"player_id": "espn_player_id"})
        .drop_duplicates(subset=["espn_player_id"], keep="first")
        .loc[:, ["espn_player_id", "player_name", "team_abbr", "position", "jersey", "status"]]
        .copy()
    )
    master["team_abbr"] = master["team_abbr"].astype(str).str.strip().str.upper()
    master["_name_norm"] = master["player_name"].map(norm_name)
    return master


def _norm_team_abbr(raw: str) -> str:
    t = str(raw or "").strip().upper()
    aliases = {"WSH": "WAS", "JAC": "JAX", "LA": "LAR"}
    return aliases.get(t, t)


def match_pp_to_master(pp_df: pd.DataFrame, master: pd.DataFrame) -> pd.DataFrame:
    by_exact: dict[tuple[str, str], pd.Series] = {}
    by_team: dict[str, list[tuple[str, pd.Series]]] = {}

    for _, row in master.iterrows():
        n = str(row["_name_norm"])
        t = str(row["team_abbr"])
        if n and t:
            by_exact[(n, t)] = row
            by_team.setdefault(t, []).append((n, row))

    player_col = next(
        (c for c in ("player_name", "player", "Player") if c in pp_df.columns),
        None,
    )
    team_col = next(
        (c for c in ("team", "team_abbr", "pp_team", "Team") if c in pp_df.columns),
        None,
    )
    pid_col = next((c for c in ("player_id", "playerId") if c in pp_df.columns), None)

    if not player_col:
        raise SystemExit(f"No player column in PP CSV. Columns: {list(pp_df.columns)}")

    today = datetime.date.today().isoformat()
    rows: list[dict[str, str]] = []
    counts = {"OK_MAP": 0, "OK_TEAM": 0, "FUZZY_NAME_TEAM": 0, "NO_MATCH": 0}

    existing_map: dict[str, dict[str, str]] = {}
    if PP_MAP_PATH.is_file():
        old = pd.read_csv(PP_MAP_PATH, dtype=str).fillna("")
        for _, r in old.iterrows():
            pid = str(r.get("pp_player_id", "")).strip()
            if pid:
                existing_map[pid] = r.to_dict()

    for _, prow in pp_df.iterrows():
        pp_id = str(prow.get(pid_col, "")).strip() if pid_col else ""
        raw_name = str(prow.get(player_col, "")).strip()
        raw_team = _norm_team_abbr(prow.get(team_col, "")) if team_col else ""
        n = norm_name(raw_name)

        if pp_id and pp_id in existing_map:
            ent = existing_map[pp_id]
            rows.append(
                {
                    "pp_player_id": pp_id,
                    "espn_athlete_id": str(ent.get("espn_athlete_id", "")),
                    "player_name": raw_name or str(ent.get("player_name", "")),
                    "team_abbr": raw_team or str(ent.get("team_abbr", "")),
                    "position": str(ent.get("position", "")),
                    "source": str(ent.get("source", "PP_MAP") or "PP_MAP"),
                    "updated_at": today,
                }
            )
            counts["OK_MAP"] += 1
            continue

        if not n or not raw_team:
            rows.append(
                {
                    "pp_player_id": pp_id,
                    "espn_athlete_id": "",
                    "player_name": raw_name,
                    "team_abbr": raw_team,
                    "position": "",
                    "source": "NO_MATCH",
                    "updated_at": today,
                }
            )
            counts["NO_MATCH"] += 1
            continue

        hit = by_exact.get((n, raw_team))
        source = "EXACT_NAME_TEAM"
        if hit is None:
            best_row = None
            best_score = 0.0
            for cand_n, cand_row in by_team.get(raw_team, []):
                score = token_sort_ratio(n, cand_n)
                if score > best_score:
                    best_score = score
                    best_row = cand_row
            if best_row is not None and best_score >= _FUZZY_THRESHOLD:
                hit = best_row
                source = "FUZZY_NAME_TEAM"

        if hit is None:
            rows.append(
                {
                    "pp_player_id": pp_id,
                    "espn_athlete_id": "",
                    "player_name": raw_name,
                    "team_abbr": raw_team,
                    "position": "",
                    "source": "NO_MATCH",
                    "updated_at": today,
                }
            )
            counts["NO_MATCH"] += 1
            continue

        rows.append(
            {
                "pp_player_id": pp_id,
                "espn_athlete_id": str(hit["espn_player_id"]),
                "player_name": raw_name,
                "team_abbr": raw_team,
                "position": str(hit.get("position", "")),
                "source": source,
                "updated_at": today,
            }
        )
        if source == "EXACT_NAME_TEAM":
            counts["OK_TEAM"] += 1
        else:
            counts["FUZZY_NAME_TEAM"] += 1

    out = pd.DataFrame(rows)
    for col in PP_MAP_COLS:
        if col not in out.columns:
            out[col] = ""
    return out[PP_MAP_COLS], counts


def main() -> int:
    ap = argparse.ArgumentParser(description="Build NFL ESPN master + PP ID map.")
    ap.add_argument("--roster", default=str(ROSTER_PATH))
    ap.add_argument("--master", default=str(MASTER_PATH))
    ap.add_argument("--pp-map", default=str(PP_MAP_PATH))
    ap.add_argument("--pp-csv", default="", help="Override PP step1 CSV path.")
    args = ap.parse_args()

    roster_path = Path(args.roster)
    if not roster_path.is_file():
        print(f"[NFL id-map] ERROR: roster not found: {roster_path}", file=sys.stderr)
        return 1

    master = build_master_from_roster(roster_path)
    master_out = master.drop(columns=["_name_norm"], errors="ignore")
    master_path = Path(args.master)
    master_path.parent.mkdir(parents=True, exist_ok=True)
    master_out.to_csv(master_path, index=False, encoding="utf-8")
    print(f"[NFL id-map] Wrote master: {master_path} ({len(master_out)} athletes)")

    pp_path = Path(args.pp_csv) if args.pp_csv else find_latest_pp_csv()
    if pp_path is None or not pp_path.is_file():
        print(
            "[NFL id-map] WARN: No PP step1 CSV found — skipped pp_to_espn_id_map_nfl.csv "
            "(glob data/NFL/step1_pp_nfl_*.csv and Sports/NFL/data/...)"
        )
        return 0

    print(f"[NFL id-map] PP step1: {pp_path}")
    pp_df = pd.read_csv(pp_path, dtype=str).fillna("")
    pp_map, counts = match_pp_to_master(pp_df, master)
    map_path = Path(args.pp_map)
    map_path.parent.mkdir(parents=True, exist_ok=True)
    pp_map.to_csv(map_path, index=False, encoding="utf-8")
    print(f"[NFL id-map] Wrote PP map: {map_path} ({len(pp_map)} rows)")

    print("\n=== Match report ===")
    print(f"  OK_MAP:           {counts['OK_MAP']}")
    print(f"  OK_TEAM:          {counts['OK_TEAM']}  (EXACT_NAME_TEAM)")
    print(f"  FUZZY_NAME_TEAM:  {counts['FUZZY_NAME_TEAM']}")
    print(f"  NO_MATCH:         {counts['NO_MATCH']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
