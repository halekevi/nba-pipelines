#!/usr/bin/env python3
"""
step5a_attach_espn_ids.py  (v3 — PP ID map as primary key)
-----------------------------------------------------------
Attaches ESPN athlete_id + team_id via four strategies in priority order:

  1. pp_id_map lookup   — Explicit PP player_id → ESPN athlete_id map stored in
                          data/reference/pp_to_espn_id_map.csv. This is the
                          ONLY collision-safe strategy. Built up over time.

  2. master name lookup — (player_norm, team_abbr) → (team_id, espn_athlete_id)
                          from ncaa_football_athletes_master.csv. Used when a player
                          has no entry in the PP map yet. Result is immediately
                          written into the PP map for future runs.

  3. name-only fallback — Same master, no team constraint. Last resort before
                          hitting the live API.

  4. ESPN live search   — Hits ESPN search API for any remaining NO_MATCH rows.
                          Result written into both master and PP map.

The PP map is the source of truth. Name matching is only a bootstrap mechanism
to populate it — once a player is in the map, name matching is never used again
for that player, eliminating all collision risk going forward.

Map file: data/reference/pp_to_espn_id_map.csv
Columns:  pp_player_id, espn_athlete_id, team_id, player_name, team_abbr,
          source (map|name_team|name_only|espn_api), updated_at

attach_status values:
  OK_MAP      — resolved from pp_id_map (most reliable)
  OK_TEAM     — name+team match from master (written to map)
  OK_NAME     — name-only match from master (written to map)
  OK_ESPN     — ESPN API fallback (written to map + master)
  NO_MATCH    — unresolved; espn_athlete_id left blank

Usage:
    py step5a_attach_espn_ids.py --input step3b_cbb.csv --output step5a_cbb.csv
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
import time
import unicodedata
from pathlib import Path

import pandas as pd
import requests

# Ensure <repo>/PropOracle is on sys.path so we can import PropOracle-level helpers.
_PROPORACLE_ROOT = Path(__file__).resolve().parents[4]
if str(_PROPORACLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROPORACLE_ROOT))

from scripts.db_utils import log_pipeline_health

MASTER_PATH  = "data/reference/ncaa_football_athletes_master.csv"
PP_MAP_PATH  = "data/reference/pp_to_espn_id_map.csv"
ESPN_SEARCH  = "https://site.api.espn.com/apis/common/v3/search"
HEADERS      = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

PP_MAP_COLS  = ["pp_player_id", "espn_athlete_id", "team_id",
                "player_name", "team_abbr", "source", "updated_at"]


# ── Normalizer (must match step2 + step5b exactly) ───────────────────────────

def norm_name(s: str) -> str:
    s = (s or "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ── PP ID map helpers ─────────────────────────────────────────────────────────

def load_pp_map(map_path: Path) -> dict[str, dict]:
    """Returns {pp_player_id: {espn_athlete_id, team_id, ...}}"""
    if not map_path.exists():
        return {}
    try:
        df = pd.read_csv(map_path, dtype=str).fillna("")
        return {str(r["pp_player_id"]).strip(): r.to_dict()
                for _, r in df.iterrows() if r.get("pp_player_id")}
    except Exception as e:
        print(f"  [MAP] Load failed ({e}) — starting fresh")
        return {}


def save_pp_map(map_path: Path, pp_map: dict[str, dict]) -> None:
    if not pp_map:
        return
    map_path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(list(pp_map.values()))
    # Ensure all expected columns present
    for col in PP_MAP_COLS:
        if col not in df.columns:
            df[col] = ""
    df = df[PP_MAP_COLS].sort_values("pp_player_id")
    df.to_csv(map_path, index=False, encoding="utf-8")


def add_to_pp_map(pp_map: dict, pp_id: str, espn_id: str, team_id: str,
                  player_name: str, team_abbr: str, source: str) -> None:
    """Upsert a row into the in-memory pp_map."""
    pp_map[pp_id] = {
        "pp_player_id":    pp_id,
        "espn_athlete_id": espn_id,
        "team_id":         team_id,
        "player_name":     player_name,
        "team_abbr":       team_abbr,
        "source":          source,
        "updated_at":      datetime.date.today().isoformat(),
    }


# ── ESPN live search ──────────────────────────────────────────────────────────

def espn_search_player(name: str, team_abbr: str = "") -> tuple[str, str]:
    """Returns (team_id, espn_athlete_id) or ('', '')."""
    try:
        r = requests.get(
            ESPN_SEARCH,
            params={"query": name, "limit": "5", "type": "athlete",
                    "sport": "football", "league": "mens-college-basketball"},
            headers=HEADERS, timeout=10,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        log_pipeline_health(
            "cfb.step5a_attach_espn_ids",
            "espn_search_failed",
            extra={"query": name, "team_abbr": team_abbr},
            start=Path(__file__),
        )
        return "", ""

    for block in (data.get("results") or []):
        for item in (block.get("contents") or []):
            ath   = item.get("athlete") or item
            aid   = str(ath.get("id", "")).strip()
            if not aid:
                continue
            tinfo = ath.get("team") or {}
            t_abbr = str(tinfo.get("abbreviation", "")).strip().upper()
            tid    = str(tinfo.get("id", "")).strip()
            if team_abbr and t_abbr and t_abbr != team_abbr:
                continue
            if aid and tid:
                return tid, aid
    return "", ""


# ── Master append ─────────────────────────────────────────────────────────────

def append_to_master(master_path: Path, new_rows: list[dict]) -> None:
    if not new_rows:
        return
    new_df = pd.DataFrame(new_rows)
    if master_path.exists():
        existing = pd.read_csv(master_path, dtype=str).fillna("")
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset="espn_athlete_id", keep="first")
    else:
        combined = new_df
    combined.to_csv(master_path, index=False, encoding="utf-8")
    print(f"  [MASTER] Appended {len(new_rows)} new athletes → {master_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",    required=True)
    ap.add_argument("--output",   required=True)
    ap.add_argument("--master",   default=MASTER_PATH)
    ap.add_argument("--pp_map",   default=PP_MAP_PATH)
    ap.add_argument("--no_espn",  action="store_true",
                    help="Skip live ESPN fallback (faster, offline-safe)")
    args = ap.parse_args()

    try:
        df = pd.read_csv(args.input, dtype=str).fillna("")
    except Exception as e:
        log_pipeline_health(
            "cfb.step5a_attach_espn_ids",
            "read_failed",
            extra={"input": args.input, "error": f"{type(e).__name__}: {e}"},
            start=Path(__file__),
        )
        raise
    print(f"-> Loaded slate: {args.input} | rows={len(df)}")

    # ── Load PP ID map (primary source of truth) ──────────────────────────────
    map_path = Path(args.pp_map)
    pp_map   = load_pp_map(map_path)
    print(f"-> PP ID map: {map_path} | entries={len(pp_map)}")

    # ── Load master (fallback name-matching) ──────────────────────────────────
    master_path = Path(args.master)
    if not master_path.exists():
        print(f"  [WARN] Master not found at {master_path} — name lookup disabled")
        master = pd.DataFrame(columns=["espn_athlete_id", "athlete_name",
                                        "athlete_name_norm", "team_id",
                                        "team_name", "team_abbr"])
    else:
        master = pd.read_csv(master_path, dtype=str).fillna("")
        print(f"-> Master: {master_path} | rows={len(master)}")

    master["_name_norm"] = master["athlete_name_norm"].astype(str).apply(norm_name)
    master["_team_norm"] = master["team_abbr"].str.strip().str.upper()

    map_name_team: dict[tuple, tuple] = {}
    map_name_only: dict[str, tuple]   = {}
    for _, r in master.iterrows():
        aid = str(r["espn_athlete_id"]).strip()
        tid = str(r["team_id"]).strip()
        n   = r["_name_norm"]
        t   = r["_team_norm"]
        if n and t:
            map_name_team[(n, t)] = (tid, aid)
        if n and n not in map_name_only:
            map_name_only[n] = (tid, aid)

    # ── Detect slate columns ──────────────────────────────────────────────────
    player_col = next((c for c in ["player_norm", "player", "Player", "player_name"]
                       if c in df.columns), None)
    team_col   = next((c for c in ["team_abbr", "pp_team", "team", "Team"]
                       if c in df.columns), None)
    pid_col    = next((c for c in ["player_id", "playerId"] if c in df.columns), None)

    if not player_col:
        raise SystemExit(f"No player column found. Columns: {list(df.columns)}")

    print(f"  player_col={player_col}  team_col={team_col}  pid_col={pid_col}")

    team_ids:    list[str] = []
    athlete_ids: list[str] = []
    statuses:    list[str] = []
    map_updated  = False
    master_new_rows: list[dict] = []

    for _, row in df.iterrows():
        raw_pid  = str(row.get(pid_col, "")).strip()   if pid_col  else ""
        raw_name = str(row.get(player_col, "")).strip()
        raw_team = str(row.get(team_col, "")).strip().upper() if team_col else ""
        n = norm_name(raw_name)

        # ── Bouncer (row-level): keep row, don't poison downstream ────────────
        # IMPORTANT: do NOT drop rows here. Dropping changes row counts and can
        # cause downstream merges/concats to misalign and look like "blank rows".
        if not raw_name or (team_col and not raw_team):
            team_ids.append("")
            athlete_ids.append("")
            statuses.append("BOUNCED_BLANK_IDENTITY")
            continue

        # ── Strategy 1: PP ID map lookup (collision-safe) ────────────────────
        if raw_pid and raw_pid in pp_map:
            entry = pp_map[raw_pid]
            team_ids.append(entry["team_id"])
            athlete_ids.append(entry["espn_athlete_id"])
            statuses.append("OK_MAP")
            continue

        # ── Strategy 2a: name + team from master ─────────────────────────────
        result = map_name_team.get((n, raw_team))
        if result:
            tid, aid = result
            team_ids.append(tid)
            athlete_ids.append(aid)
            statuses.append("OK_TEAM")
            # Write into PP map so next run skips name matching for this player
            if raw_pid:
                add_to_pp_map(pp_map, raw_pid, aid, tid, raw_name, raw_team, "name_team")
                map_updated = True
            continue

        # ── Strategy 2b: name-only from master ───────────────────────────────
        result = map_name_only.get(n)
        if result:
            tid, aid = result
            team_ids.append(tid)
            athlete_ids.append(aid)
            statuses.append("OK_NAME")
            if raw_pid:
                add_to_pp_map(pp_map, raw_pid, aid, tid, raw_name, raw_team, "name_only")
                map_updated = True
            continue

        # ── Strategy 3: ESPN live search ─────────────────────────────────────
        if not args.no_espn and raw_name:
            tid, aid = espn_search_player(raw_name, raw_team)
            if tid:
                team_ids.append(tid)
                athlete_ids.append(aid)
                statuses.append("OK_ESPN")
                if raw_pid:
                    add_to_pp_map(pp_map, raw_pid, aid, tid, raw_name, raw_team, "espn_api")
                    map_updated = True
                master_new_rows.append({
                    "espn_athlete_id":   aid,
                    "athlete_name":      raw_name,
                    "athlete_name_norm": norm_name(raw_name),
                    "team_id":           tid,
                    "team_name":         "",
                    "team_abbr":         raw_team,
                })
                time.sleep(0.15)
                continue

        # ── No match ─────────────────────────────────────────────────────────
        team_ids.append("")
        athlete_ids.append("")
        statuses.append("NO_MATCH")

    # ── Write outputs ─────────────────────────────────────────────────────────
    df["team_id"]         = team_ids
    df["espn_athlete_id"] = athlete_ids
    df["attach_status"]   = statuses

    df.to_csv(args.output, index=False, encoding="utf-8")
    print(f"\nSaved -> {args.output} | rows={len(df)}")
    print("\nattach_status breakdown:")
    print(df["attach_status"].value_counts().to_string())

    no_tid = (df["team_id"].str.strip() == "").sum()
    if no_tid:
        print(f"\n  ⚠️  {no_tid} rows have no team_id")
        if args.no_espn:
            print("     Rerun without --no_espn to trigger live ESPN fallback")

    # Match-rate health check (log only; don't fail the run)
    if len(df):
        ok = int((df["attach_status"] != "NO_MATCH").sum())
        ok_rate = ok / len(df)
        if ok_rate < 0.80:
            log_pipeline_health(
                "cfb.step5a_attach_espn_ids",
                "low_match_rate",
                extra={"ok": ok, "total": len(df), "ok_rate": round(ok_rate, 3), "no_espn": bool(args.no_espn)},
                start=Path(__file__),
            )

        bounced = int((df["attach_status"] == "BOUNCED_BLANK_IDENTITY").sum())
        if bounced:
            log_pipeline_health(
                "cfb.step5a_attach_espn_ids",
                "bounced_blank_identity_rows",
                extra={"bounced": bounced, "total": len(df)},
                start=Path(__file__),
            )

    # Persist updated PP map and master
    if map_updated:
        save_pp_map(map_path, pp_map)
        new_entries = sum(1 for s in statuses if s in ("OK_TEAM", "OK_NAME", "OK_ESPN"))
        print(f"  [MAP] Saved {len(pp_map)} entries → {map_path}  (+{new_entries} new this run)")

    append_to_master(master_path, master_new_rows)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_pipeline_health(
            "cfb.step5a_attach_espn_ids",
            "run_failed",
            extra={"error": f"{type(e).__name__}: {e}"},
            start=Path(__file__),
        )
        print(f"❌ CBB step5a failed (logged). {type(e).__name__}: {e}")
