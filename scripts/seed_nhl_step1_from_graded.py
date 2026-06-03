#!/usr/bin/env python3
"""Seed outputs/<date>/nhl/step1_nhl_props.csv from graded_nhl_<date>.xlsx archives."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DEFAULT_TZ = "America/New_York"
GRADED_RE = re.compile(r"^graded_nhl_(?P<date>\d{4}-\d{2}-\d{2})(?:_mlbackfill)?\.xlsx$", re.I)
STEP8_RE = re.compile(r"^step8_nhl_direction_clean_(?P<date>\d{4}-\d{2}-\d{2})\.xlsx$", re.I)

# step1_fetch_prizepicks_nhl.NHL_CSV_FIELDNAMES + game_date/delta_pct seen on disk
STEP1_COLUMNS = [
    "projection_id",
    "player_id",
    "player_name",
    "team",
    "position",
    "stat_type",
    "line_score",
    "standard_line",
    "pick_type",
    "is_promo",
    "description",
    "away_team",
    "home_team",
    "game_start",
    "game_id",
    "fetched_at",
    "game_date",
]


def _synthetic_id(*parts: object) -> str:
    raw = "|".join(str(p or "").strip().lower() for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _pick(row: pd.Series, *names: str) -> str:
    for name in names:
        if name in row.index:
            val = row[name]
            if pd.notna(val) and str(val).strip():
                return str(val).strip()
    return ""


def _pick_float(row: pd.Series, *names: str) -> float | None:
    for name in names:
        if name not in row.index:
            continue
        val = row[name]
        if pd.isna(val):
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return None


def _normalize_pick_type(raw: str, direction: str = "") -> str:
    s = (raw or "").strip().lower()
    if s in ("standard", "goblin", "demon", "promo"):
        return s
    d = (direction or "").strip().lower()
    if d in ("over", "under"):
        return "standard"
    return "standard"


def _game_start_iso(date_str: str, hour_et: int = 19) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=hour_et, minute=0, second=0, tzinfo=ZoneInfo(DEFAULT_TZ)
    )
    return dt.isoformat(timespec="milliseconds")


def _is_prop_level(df: pd.DataFrame) -> bool:
    cols = {str(c).strip().lower() for c in df.columns}
    return bool(
        cols
        & {
            "player",
            "player_name",
            "prop_type",
            "prop_type_raw",
            "prop_type_norm",
        }
    )


def _candidate_paths(repo: Path, slate_date: str) -> list[Path]:
    out_day = repo / "outputs" / slate_date
    names = [
        f"graded_nhl_{slate_date}.xlsx",
        f"graded_nhl_{slate_date}_mlbackfill.xlsx",
        f"step8_nhl_direction_clean_{slate_date}.xlsx",
    ]
    candidates: list[Path] = []
    for name in names:
        p = out_day / name
        if p.is_file():
            candidates.append(p)
    for path in repo.glob(f"outputs/**/graded_nhl_{slate_date}.xlsx"):
        if path not in candidates:
            candidates.append(path)
    for path in repo.glob(f"outputs/**/graded_nhl_{slate_date}_mlbackfill.xlsx"):
        if path not in candidates:
            candidates.append(path)
    for path in repo.glob(f"outputs/**/step8_nhl_direction_clean_{slate_date}.xlsx"):
        if path not in candidates:
            candidates.append(path)
    return candidates


def discover_graded_paths(
    repo: Path,
    *,
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Path]:
    """Map slate date -> best prop-level source (graded > mlbackfill > dated step8)."""
    dates: list[str] = []
    if date:
        dates = [date]
    else:
        seen: set[str] = set()
        for path in repo.glob("outputs/**/*"):
            if not path.is_file():
                continue
            for pat in (GRADED_RE, STEP8_RE):
                m = pat.match(path.name)
                if m:
                    seen.add(m.group("date"))
        dates = sorted(seen)

    found: dict[str, Path] = {}
    for d in dates:
        if start and d < start:
            continue
        if end and d > end:
            continue
        for path in _candidate_paths(repo, d):
            try:
                head = pd.read_excel(path, engine="openpyxl", nrows=5)
            except Exception:
                continue
            if _is_prop_level(head):
                found[d] = path
                break
    return dict(sorted(found.items()))


def graded_to_step1_rows(df: pd.DataFrame, slate_date: str) -> list[dict]:
    if df.empty:
        return []

    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    fetched_at = f"{slate_date}T12:00:00Z"
    default_start = _game_start_iso(slate_date)
    rows: list[dict] = []

    for _, r in df.iterrows():
        player = _pick(r, "player_name", "player", "Player")
        team = _pick(r, "team", "Team")
        if not player or not team:
            continue

        stat = _pick(
            r,
            "stat_type",
            "prop_type_raw",
            "prop_type_norm",
            "prop_type",
            "Prop",
        )
        line = _pick_float(r, "line_score", "line", "Line")
        if line is None:
            continue

        opp = _pick(r, "opp_team", "opponent", "opp", "Opp")
        pick_type = _normalize_pick_type(
            _pick(r, "pick_type", "Pick Type"),
            _pick(r, "bet_direction", "direction", "bet_direction"),
        )
        standard_line = _pick_float(r, "standard_line", "Standard Line")
        if standard_line is None:
            standard_line = line

        is_home = r.get("is_home") if "is_home" in r.index else None
        home_team = ""
        away_team = ""
        if opp:
            if is_home is True or str(is_home).strip().lower() in ("1", "true", "yes", "home"):
                home_team, away_team = team, opp
            elif is_home is False or str(is_home).strip().lower() in ("0", "false", "no", "away"):
                away_team, home_team = team, opp
            else:
                description_opp = opp
                away_team = ""
                home_team = ""
        else:
            description_opp = ""

        game_start = _pick(r, "game_start", "start_time", "game_time")
        if not game_start:
            game_start = default_start

        game_id = _pick(r, "game_id", "pp_game_id")
        if not game_id:
            game_id = _synthetic_id(team, opp or "?", slate_date)

        projection_id = _pick(r, "projection_id", "pp_projection_id")
        if not projection_id:
            projection_id = _synthetic_id(player, team, stat, line, pick_type, game_id)

        player_id = _pick(r, "player_id", "nhl_player_id", "espn_player_id")

        description = _pick(r, "description")
        if not description and opp:
            description = opp

        position = _pick(r, "position", "position_group", "player_role", "player_type")

        rows.append(
            {
                "projection_id": projection_id,
                "player_id": player_id,
                "player_name": player,
                "team": team,
                "position": position,
                "stat_type": stat,
                "line_score": line,
                "standard_line": standard_line,
                "pick_type": pick_type,
                "is_promo": False,
                "description": description,
                "away_team": away_team,
                "home_team": home_team,
                "game_start": game_start,
                "game_id": game_id,
                "fetched_at": fetched_at,
                "game_date": slate_date,
            }
        )

    if not rows:
        return []

    out = pd.DataFrame(rows)
    dedup_cols = [
        c
        for c in ("player_name", "stat_type", "line_score", "game_id", "pick_type")
        if c in out.columns
    ]
    if dedup_cols:
        out = out.drop_duplicates(subset=dedup_cols, keep="last")
    return out.to_dict("records")


def seed_date(
    repo: Path,
    slate_date: str,
    graded_path: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[int, str]:
    out_dir = repo / "outputs" / slate_date / "nhl"
    out_path = out_dir / "step1_nhl_props.csv"

    if out_path.is_file() and not force:
        try:
            existing = pd.read_csv(out_path, encoding="utf-8-sig")
            if len(existing) > 0:
                return len(existing), "skip_existing"
        except Exception:
            pass

    df = pd.read_excel(graded_path, engine="openpyxl")
    rows = graded_to_step1_rows(df, slate_date)
    n = len(rows)

    if dry_run:
        return n, "dry_run"

    if n == 0:
        return 0, "empty"

    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=STEP1_COLUMNS).to_csv(out_path, index=False, encoding="utf-8-sig")
    return n, "seeded"


def dates_missing_step1(repo: Path, graded: dict[str, Path]) -> dict[str, Path]:
    missing: dict[str, Path] = {}
    for d, path in graded.items():
        step1 = repo / "outputs" / d / "nhl" / "step1_nhl_props.csv"
        if not step1.is_file():
            missing[d] = path
            continue
        try:
            df = pd.read_csv(step1, encoding="utf-8-sig")
            if df.empty:
                missing[d] = path
        except Exception:
            missing[d] = path
    return missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="Single slate date YYYY-MM-DD")
    ap.add_argument("--all", action="store_true", help="All graded dates missing step1")
    ap.add_argument("--dry-run", action="store_true", help="Print counts only; do not write")
    ap.add_argument("--force", action="store_true", help="Overwrite non-empty step1")
    ap.add_argument("--repo-root", type=Path, default=REPO)
    ap.add_argument("--start", default="2026-02-19", help="With --all: min date")
    ap.add_argument("--end", default="2026-03-31", help="With --all: max date")
    args = ap.parse_args()

    repo = args.repo_root.resolve()
    if not args.date and not args.all:
        ap.error("Specify --date or --all")

    graded = discover_graded_paths(
        repo,
        date=args.date,
        start=None if args.date else args.start,
        end=None if args.date else args.end,
    )
    if not graded:
        print("No graded_nhl_*.xlsx found under outputs/")
        return 1

    targets = dates_missing_step1(repo, graded) if args.all else graded
    if args.date and args.date not in graded:
        print(f"No graded workbook for {args.date}")
        return 1
    if args.date:
        targets = {args.date: graded[args.date]}

    if not targets:
        print("All targets already have non-empty step1.")
        return 0

    total = 0
    for d, path in sorted(targets.items()):
        n, status = seed_date(repo, d, path, dry_run=args.dry_run, force=args.force)
        total += n
        src = path.relative_to(repo) if path.is_relative_to(repo) else path
        if status == "dry_run":
            print(f"  [dry-run] {d}: would seed {n:,} rows from {src}")
        elif status == "skip_existing":
            print(f"  [skip] {d}: step1 already has {n:,} rows")
        elif status == "empty":
            print(f"  [warn] {d}: 0 rows from {src}")
        elif status == "not_prop_level":
            print(f"  [warn] {d}: not a prop-level workbook ({src})")
        else:
            out = repo / "outputs" / d / "nhl" / "step1_nhl_props.csv"
            print(f"  seeded {d}: {n:,} rows -> {out.relative_to(repo)}")

    print(f"\nDone. {len(targets)} date(s), {total:,} row(s){' (dry-run)' if args.dry_run else ''}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
