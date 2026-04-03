#!/usr/bin/env python3
"""
mlb_grade_date.py
=================
For a calendar date, pull real stats from the MLB Stats API for every player/prop
on a step8 clean slate, write outputs/<date>/actuals_mlb_<date>.csv, then run
nhl_soccer_grader to produce graded_mlb_<date>.xlsx (same layout as run_grader.ps1).

  py -3.14 scripts/mlb_grade_date.py --date yesterday
  py -3.14 scripts/mlb_grade_date.py --date 2026-03-30 \\
      --slate outputs/2026-03-30/step8_mlb_direction_clean_2026-03-30.xlsx
  py -3.14 scripts/mlb_grade_date.py --date 2026-03-30 --grade-only
  py -3.14 scripts/mlb_grade_date.py --date yesterday --tiers A,B

Requires: pandas, openpyxl, requests (already used elsewhere in the repo).

Postponed / canceled games: there is no game log row for that calendar date, so actuals stay
empty. After grading, this script queries the MLB Stats API schedule and sets
``void_reason_grade`` to ``POSTPONED`` for VOID + NO_ACTUAL legs whose ``team`` played in a
postponed or canceled game that day (so Excel and ticket eval show why there is no stat).
"""

from __future__ import annotations

import argparse
import importlib
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]

# Lazily filled: schedule entries for postponed games often omit abbreviation; resolve by team id.
_MLB_TEAM_ID_TO_ABBR: Dict[int, str] | None = None


def _mlb_team_id_to_abbr_map() -> Dict[int, str]:
    global _MLB_TEAM_ID_TO_ABBR
    if _MLB_TEAM_ID_TO_ABBR is not None:
        return _MLB_TEAM_ID_TO_ABBR
    try:
        import requests
    except ImportError:
        _MLB_TEAM_ID_TO_ABBR = {}
        return _MLB_TEAM_ID_TO_ABBR
    m: Dict[int, str] = {}
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/teams",
            params={"sportId": 1},
            timeout=60,
        )
        r.raise_for_status()
        for t in r.json().get("teams") or []:
            tid = t.get("id")
            ab = t.get("abbreviation") or t.get("fileCode") or t.get("teamCode")
            if tid is not None and ab:
                m[int(tid)] = str(ab).strip().upper()
    except Exception as exc:
        print(f"  WARNING: MLB teams lookup failed (postponed labeling): {exc}")
    _MLB_TEAM_ID_TO_ABBR = m
    return _MLB_TEAM_ID_TO_ABBR


def _postponed_void_label_from_game(game: dict) -> str:
    """Human-readable void reason from one schedule game node (makeup date + status reason)."""
    resched = str(game.get("rescheduleGameDate") or game.get("rescheduleDate") or "").strip()
    if "T" in resched:
        resched = resched.split("T", 1)[0]
    elif len(resched) > 10:
        resched = resched[:10]
    reason = str((game.get("status") or {}).get("reason") or "").strip()
    parts = ["POSTPONED"]
    if resched:
        parts.append(f"makeup {resched}")
    if reason:
        reason_short = reason[:48] + ("…" if len(reason) > 48 else "")
        parts.append(reason_short)
    return " · ".join(parts)


def _team_abbrs_for_schedule_game(game: dict, idmap: Dict[int, str]) -> Set[str]:
    abbrs: Set[str] = set()
    for side in ("away", "home"):
        node = (game.get("teams") or {}).get(side) or {}
        team = node.get("team") or {}
        for key in ("abbreviation", "fileCode", "triCode"):
            v = team.get(key)
            if v:
                abbrs.add(str(v).strip().upper())
        tid = team.get("id")
        if tid is not None:
            ab = idmap.get(int(tid))
            if ab:
                abbrs.add(ab)
    return abbrs


def mlb_postponed_team_labels_for_date(iso_date: str) -> Dict[str, str]:
    """
    For each team abbreviation on the slate ``Team`` column: void_reason_grade text when that
    club had a postponed/canceled game on ``iso_date`` (includes makeup date from the API when present).
    """
    try:
        import requests
    except ImportError:
        return {}
    d = str(iso_date).strip()[:10]
    url = "https://statsapi.mlb.com/api/v1/schedule"
    try:
        r = requests.get(url, params={"sportId": 1, "date": d}, timeout=45)
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:
        print(f"  WARNING: MLB schedule fetch failed for {d}: {exc}")
        return {}
    idmap = _mlb_team_id_to_abbr_map()
    labels: Dict[str, str] = {}
    for day in payload.get("dates") or []:
        for g in day.get("games") or []:
            det = str((g.get("status") or {}).get("detailedState") or "").lower()
            if "postpon" not in det and "cancel" not in det:
                continue
            label = _postponed_void_label_from_game(g)
            for ab in _team_abbrs_for_schedule_game(g, idmap):
                prev = labels.get(ab)
                if prev is None:
                    labels[ab] = label
                    continue
                # Prefer the richer label (makeup date) if we see multiple PPD entries.
                if "makeup" in label and "makeup" not in prev:
                    labels[ab] = label
    return labels


def mlb_postponed_team_abbrs_for_date(iso_date: str) -> Set[str]:
    """Team abbreviations with a postponed/canceled game on ``iso_date``."""
    return set(mlb_postponed_team_labels_for_date(iso_date).keys())


def _apply_mlb_postponed_void_labels(graded: pd.DataFrame, iso_date: str) -> None:
    """Mark VOID+NO_ACTUAL rows when the team's game that day was PPD/canceled (schedule API)."""
    team_labels = mlb_postponed_team_labels_for_date(iso_date)
    if not team_labels:
        return
    need = {"team", "result", "actual", "void_reason_grade"}
    if not need.issubset(set(graded.columns)):
        return
    patched = 0
    for idx in graded.index:
        if str(graded.at[idx, "result"]).strip().upper() != "VOID":
            continue
        if str(graded.at[idx, "void_reason_grade"] or "").strip().upper() != "NO_ACTUAL":
            continue
        act = graded.at[idx, "actual"]
        if act is not None and not (isinstance(act, float) and np.isnan(act)):
            continue
        t = str(graded.at[idx, "team"] or "").strip().upper()
        if t and t in team_labels:
            graded.at[idx, "void_reason_grade"] = team_labels[t]
            patched += 1
    if patched:
        sample = next(iter(team_labels.values()), "POSTPONED")
        print(
            f"  [MLB] Set postponed void_reason_grade on {patched} leg(s) "
            f"({iso_date}; teams: {', '.join(sorted(team_labels))}; e.g. {sample!r})"
        )


def grader_norm_prop(s: str) -> str:
    """Match nhl_soccer_grader._norm_prop (strip to alphanumeric)."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def resolve_date(s: str) -> str:
    t = str(s).strip().lower()
    if t in ("yesterday", "yst", "yday"):
        return (date.today() - timedelta(days=1)).isoformat()
    if t == "today":
        return date.today().isoformat()
    return str(s).strip()[:10]


def _merge_id_cache(path: Path, mapping: Dict[str, str]) -> None:
    rows = [{"player_norm": k, "mlb_player_id": v} for k, v in sorted(mapping.items()) if v]
    if not rows:
        return
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def _tier_set(tiers_arg: str) -> Optional[set[str]]:
    if not (tiers_arg or "").strip():
        return None
    return {t.strip().upper() for t in tiers_arg.split(",") if t.strip()}


def _filter_mlb_slate_display_by_game_date(df: pd.DataFrame, d: str) -> pd.DataFrame:
    """Drop slate rows whose Game Date is set and does not match the fetch/grade day."""
    if "Game Date" not in df.columns:
        return df
    want = str(d).strip()[:10]
    raw = pd.to_datetime(df["Game Date"], errors="coerce")
    if not raw.notna().any():
        return df
    day = raw.dt.strftime("%Y-%m-%d")
    ok = day == want
    unk = raw.isna()
    n0 = len(df)
    out = df.loc[ok | unk].copy()
    if len(out) == 0 and n0 > 0:
        print(
            f"  WARNING: Game Date filter {want} would remove all {n0} rows "
            f"(slate has no rows for that day); using full slate"
        )
        return df
    if len(out) < n0:
        print(f"  [Slate] Game Date filter {want}: kept {len(out)}/{n0} rows")
    return out


def _filter_slate_display_df(df: pd.DataFrame, want: Optional[set[str]]) -> pd.DataFrame:
    """Filter clean-xlsx dataframe (Title Case columns) by Tier."""
    if not want:
        return df
    col = "Tier" if "Tier" in df.columns else None
    if not col:
        print("  WARNING: --tiers set but no 'Tier' column; keeping all rows")
        return df
    m = df[col].astype(str).str.strip().str.upper().isin(want)
    out = df.loc[m].copy()
    print(f"  Tier filter {sorted(want)}: {len(out)}/{len(df)} rows (display slate)")
    return out


def _filter_graded_slate(slate: pd.DataFrame, want: Optional[set[str]]) -> pd.DataFrame:
    """Filter grader slate (lowercase columns after ColMap) by tier."""
    if not want:
        return slate
    col = "tier" if "tier" in slate.columns else None
    if not col:
        print("  WARNING: --tiers set but no 'tier' column after slate load; keeping all rows")
        return slate
    m = slate[col].astype(str).str.strip().str.upper().isin(want)
    out = slate.loc[m].copy()
    print(f"  Tier filter {sorted(want)}: {len(out)}/{len(slate)} rows (graded slate)")
    return out


def _fetch_actuals_csv(
    slate_df: pd.DataFrame,
    d: str,
    season: str,
    id_cache_path: Path,
    update_id_cache: bool,
) -> Tuple[pd.DataFrame, Dict[str, str], int, int, int]:
    mlb_scripts = REPO_ROOT / "MLB" / "scripts"
    sys.path.insert(0, str(mlb_scripts))
    s2 = importlib.import_module("step2_attach_picktypes_mlb")
    s4 = importlib.import_module("step4_attach_player_stats_mlb")

    ptype_col = "Player Type" if "Player Type" in slate_df.columns else None

    id_map: Dict[str, str] = {}
    if id_cache_path.is_file():
        cdf = pd.read_csv(id_cache_path, dtype=str, low_memory=False).fillna("")
        if "player_norm" in cdf.columns and "mlb_player_id" in cdf.columns:
            for a, b in zip(cdf["player_norm"].map(s2.norm_name), cdf["mlb_player_id"]):
                if a and str(b).strip():
                    id_map[a] = str(b).strip()

    names = sorted({str(x).strip() for x in slate_df["Player"].tolist() if str(x).strip()})
    print(f"  Resolving MLB IDs ({len(names)} unique names)...")
    for nm in names:
        key = s2.norm_name(nm)
        if not key:
            continue
        if id_map.get(key):
            continue
        pid = s2.search_mlb_player(nm)
        if pid:
            id_map[key] = pid
            print(f"    + {nm} -> {pid}")

    if update_id_cache:
        _merge_id_cache(id_cache_path, id_map)

    target_date = pd.Timestamp(d).date()
    log_cache: Dict[Tuple[str, str], List[dict]] = {}

    def get_splits(player_id: str, group: str) -> List[dict]:
        k = (player_id, group)
        if k not in log_cache:
            log_cache[k] = s4.fetch_game_log(player_id, group, season)
        return log_cache[k]

    def split_for_date(player_id: str, ptype: str) -> Optional[dict]:
        group = "pitching" if ptype == "pitcher" else "hitting"
        for sp in get_splits(player_id, group):
            gd = str(sp.get("date") or "").strip()
            if not gd:
                continue
            try:
                if pd.to_datetime(gd).date() == target_date:
                    return sp
            except Exception:
                continue
        return None

    out_rows: Dict[Tuple[str, str], dict] = {}
    no_id = no_game = bad_prop = 0

    for _, r in slate_df.iterrows():
        player = str(r["Player"]).strip()
        prop_disp = str(r["Prop"]).strip()
        if not player or not prop_disp:
            continue
        key = s2.norm_name(player)
        mlb_id = (id_map.get(key) or "").strip()
        if not mlb_id:
            no_id += 1
            continue

        prop_norm = s2.norm_prop(prop_disp)
        ptype = s2.player_type(prop_norm)
        if ptype_col:
            pt = str(r.get(ptype_col, "")).lower()
            if "pitch" in pt:
                ptype = "pitcher"
            elif "hit" in pt:
                ptype = "hitter"
        if ptype not in ("pitcher", "hitter"):
            bad_prop += 1
            continue

        spl = split_for_date(mlb_id, ptype)
        if spl is None:
            no_game += 1
            continue

        derive = s4.derive_pitcher_stat if ptype == "pitcher" else s4.derive_hitter_stat
        val = float(derive(spl, prop_norm))
        if np.isnan(val):
            bad_prop += 1
            continue

        gkey = grader_norm_prop(prop_disp)
        nk = (key, gkey)
        if nk in out_rows:
            continue
        team = ""
        if "Team" in slate_df.columns:
            team = str(r.get("Team", "")).strip()
        out_rows[nk] = {
            "player": player,
            "prop": gkey,
            "actual": val,
            "game_date": d,
            "mlb_player_id": mlb_id,
            "team": team,
        }

    act_df = pd.DataFrame(list(out_rows.values()))
    return act_df, id_map, no_id, no_game, bad_prop


def _run_grader(
    slate_path: Path,
    actuals_path: Path,
    out_dir: Path,
    d: str,
    tier_filter: Optional[set[str]],
) -> None:
    scripts_dir = str(REPO_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    nsg = importlib.import_module("nhl_soccer_grader")

    print(f"\n  Running MLB grader...")
    slate = nsg.load_slate(slate_path, "MLB", grade_date=d)
    slate = _filter_graded_slate(slate, tier_filter)
    actuals = nsg.load_actuals(actuals_path)
    graded = nsg.grade(slate, actuals, "MLB")
    _apply_mlb_postponed_void_labels(graded, d)
    graded_path = out_dir / f"graded_mlb_{d}.xlsx"
    if tier_filter:
        tier_tag = "-".join(sorted(tier_filter))
        graded_path = out_dir / f"graded_mlb_{d}_tier_{tier_tag}.xlsx"
    nsg.save_graded(graded, graded_path, "MLB", d)
    print(f"  Done -> {graded_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch MLB actuals and grade slate for a date.")
    ap.add_argument("--date", default="yesterday", help="YYYY-MM-DD or yesterday/today")
    ap.add_argument(
        "--slate",
        default="",
        help="step8 clean xlsx (default: outputs/<date>/step8_mlb_direction_clean_<date>.xlsx)",
    )
    ap.add_argument("--output-dir", default="", help="Default: outputs/<date>")
    ap.add_argument(
        "--actuals",
        default="",
        help="Override actuals CSV path (--grade-only or after manual edit)",
    )
    ap.add_argument(
        "--id-cache",
        default=str(REPO_ROOT / "MLB" / "mlb_id_cache.csv"),
        help="step2 ID cache (player_norm, mlb_player_id)",
    )
    ap.add_argument("--season", default="", help="Season year (default: year of --date)")
    ap.add_argument("--sheet", default="ALL", help="Workbook sheet name")
    ap.add_argument(
        "--tiers",
        default="",
        help="Comma tiers to include only, e.g. A or A,B (writes graded_mlb_<date>_tier_<tags>.xlsx when set)",
    )
    ap.add_argument(
        "--skip-grade",
        action="store_true",
        help="Only write actuals CSV; do not run nhl_soccer_grader",
    )
    ap.add_argument(
        "--grade-only",
        action="store_true",
        help="Skip API fetch; use existing actuals_mlb_<date>.csv (or --actuals)",
    )
    ap.add_argument(
        "--update-id-cache",
        action="store_true",
        help="Write newly resolved IDs back to --id-cache",
    )
    args = ap.parse_args()

    d = resolve_date(args.date)
    season = (args.season or "").strip() or d[:4]
    tier_want = _tier_set(args.tiers)

    out_dir = Path(args.output_dir) if args.output_dir else REPO_ROOT / "outputs" / d
    out_dir.mkdir(parents=True, exist_ok=True)

    slate_path = Path(args.slate) if args.slate else out_dir / f"step8_mlb_direction_clean_{d}.xlsx"
    if not slate_path.is_file():
        fb = REPO_ROOT / "MLB" / "step8_mlb_direction_clean.xlsx"
        if fb.is_file():
            print(f"  Slate not at {slate_path}; using {fb}")
            slate_path = fb
        else:
            print(f"ERROR: slate not found: {slate_path}")
            sys.exit(1)

    actuals_path = Path(args.actuals) if args.actuals else out_dir / f"actuals_mlb_{d}.csv"

    if args.grade_only:
        if not actuals_path.is_file():
            print(f"ERROR: --grade-only requires actuals at {actuals_path}")
            sys.exit(1)
        if args.skip_grade:
            print("ERROR: --grade-only and --skip-grade together do nothing useful")
            sys.exit(1)
        _run_grader(slate_path, actuals_path, out_dir, d, tier_want)
        return

    slate_df = pd.read_excel(slate_path, sheet_name=args.sheet)
    slate_df.columns = [str(c).strip() for c in slate_df.columns]
    req = ("Player", "Prop")
    miss = [c for c in req if c not in slate_df.columns]
    if miss:
        print(f"ERROR: sheet missing columns {miss}; have {list(slate_df.columns)}")
        sys.exit(1)

    slate_df = _filter_mlb_slate_display_by_game_date(slate_df, d)
    slate_df = _filter_slate_display_df(slate_df, tier_want)

    act_df, _idm, no_id, no_game, bad_prop = _fetch_actuals_csv(
        slate_df,
        d,
        season,
        Path(args.id_cache),
        args.update_id_cache,
    )
    act_df.sort_values(["player", "prop"]).to_csv(actuals_path, index=False, encoding="utf-8-sig")
    print(f"  Wrote {len(act_df)} actual rows -> {actuals_path}")
    print(f"  (no mlb id: {no_id}, no game on {d}: {no_game}, bad prop/stat: {bad_prop})")

    if args.skip_grade:
        return

    _run_grader(slate_path, actuals_path, out_dir, d, tier_want)


if __name__ == "__main__":
    main()
