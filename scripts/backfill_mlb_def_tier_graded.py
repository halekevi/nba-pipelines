#!/usr/bin/env python3
"""
Backfill empty def_tier on MLB rows in graded_props_*.json archives.

MLB graded exports historically omitted def_tier even though opp_team is present.
This script maps opp_team -> DEF_TIER from Sports/MLB/mlb_defense_summary.csv (same
source as step3_attach_defense_mlb.py).

Combo rows (opp_team like ATH/SF) use the first opponent abbrev for tier lookup.

Usage:
  py -3 scripts/backfill_mlb_def_tier_graded.py --dry-run
  py -3 scripts/backfill_mlb_def_tier_graded.py
  py -3 scripts/backfill_mlb_def_tier_graded.py --date 2026-05-28
  py -3 scripts/backfill_mlb_def_tier_graded.py --defense Sports/MLB/mlb_defense_summary.csv
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from utils.defense_tiers import normalize_def_tier_label  # noqa: E402

_GRADED_RE = re.compile(r"^graded_props_(\d{4}-\d{2}-\d{2})\.json$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Graded / PrizePicks abbrev -> defense summary TEAM_ABBREVIATION
_OPP_ALIAS: dict[str, str] = {
    "AZ": "AZ",
    "ARI": "AZ",
    "CHW": "CWS",
    "CWS": "CWS",
    "KCR": "KC",
    "KC": "KC",
    "SDP": "SD",
    "SD": "SD",
    "SFG": "SF",
    "SF": "SF",
    "TBR": "TB",
    "TB": "TB",
    "WSN": "WSH",
    "WAS": "WSH",
    "WSH": "WSH",
    "OAK": "ATH",
    "ATH": "ATH",
}


def _is_empty_def_tier(v: object) -> bool:
    s = str(v or "").strip().lower()
    return s in ("", "nan", "none", "null", "n/a", "na")


def _tier_for_opp(opp: object, lookup: dict[str, str]) -> str:
    raw = str(opp or "").strip().upper()
    if not raw:
        return ""
    parts = [p.strip() for p in raw.split("/") if p.strip()]
    if not parts:
        parts = [raw]
    for part in parts:
        key = _OPP_ALIAS.get(part, part)
        tier = lookup.get(key, "")
        if tier:
            return tier
    return ""


def load_defense_lookup(defense_path: Path) -> dict[str, str]:
    d = pd.read_csv(defense_path, dtype=str, encoding="utf-8-sig").fillna("")
    key_col = next(
        (c for c in ("TEAM_ABBREVIATION", "team_abbr", "TEAM_ABBR", "team") if c in d.columns),
        None,
    )
    if not key_col:
        raise RuntimeError(f"No team key column in {defense_path}")
    tier_col = next((c for c in ("DEF_TIER", "def_tier") if c in d.columns), None)
    if not tier_col:
        raise RuntimeError(f"No DEF_TIER column in {defense_path}")

    lookup: dict[str, str] = {}
    for _, row in d.iterrows():
        team = str(row[key_col]).strip().upper()
        if not team:
            continue
        tier = normalize_def_tier_label(row[tier_col])
        if tier:
            lookup[team] = tier
    return lookup


def _graded_json_dirs(repo: Path) -> list[Path]:
    dirs = [repo / "ui_runner" / "templates", repo / "mobile" / "www"]
    return [p for p in dirs if p.is_dir()]


def _iter_graded_paths(
    dirs: list[Path],
    date: str,
    *,
    min_date: str = "",
    max_date: str = "",
) -> list[Path]:
    out: list[Path] = []
    min_d = str(min_date or "").strip()[:10]
    max_d = str(max_date or "").strip()[:10]
    for base in dirs:
        if date:
            p = base / f"graded_props_{date}.json"
            if p.is_file():
                out.append(p)
        else:
            for p in sorted(base.glob("graded_props_*.json")):
                if ".bak_" in p.name:
                    continue
                m = _GRADED_RE.match(p.name)
                if not m:
                    continue
                fd = m.group(1)
                if min_d and fd < min_d:
                    continue
                if max_d and fd > max_d:
                    continue
                out.append(p)
    return out


def patch_file(path: Path, lookup: dict[str, str], *, dry_run: bool) -> tuple[int, int, int, int]:
    """Returns (mlb_checked, patched, already_filled, unmapped_opp)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        entries = data
        wrap_list = True
        root = None
    else:
        root = data
        entries = data.get("props") or data.get("picks") or data.get("rows") or []
        wrap_list = False

    n_chk = n_patch = n_have = n_miss = 0
    changed = False

    for entry in entries:
        if str(entry.get("sport") or "").strip().upper() != "MLB":
            continue
        n_chk += 1
        if not _is_empty_def_tier(entry.get("def_tier")):
            n_have += 1
            continue
        tier = _tier_for_opp(entry.get("opp_team"), lookup)
        if not tier:
            n_miss += 1
            continue
        entry["def_tier"] = tier
        n_patch += 1
        changed = True

    if changed and not dry_run:
        if wrap_list:
            out = entries
        else:
            out = dict(root)
            for k in ("props", "picks", "rows"):
                if k in out:
                    out[k] = entries
                    break
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    return n_chk, n_patch, n_have, n_miss


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", default=str(_REPO))
    ap.add_argument("--date", default="", help="Single YYYY-MM-DD (default: all graded JSON)")
    ap.add_argument("--from", dest="min_date", default="", metavar="DATE", help="Only files on/after date")
    ap.add_argument("--to", dest="max_date", default="", metavar="DATE", help="Only files on/before date")
    ap.add_argument(
        "--defense",
        default=str(_REPO / "Sports" / "MLB" / "mlb_defense_summary.csv"),
        help="Team pitching defense summary CSV",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo_root).resolve()
    date = str(args.date or "").strip()[:10]
    if date and not _DATE_RE.fullmatch(date):
        print(f"Invalid --date: {date}", file=sys.stderr)
        return 1

    defense_path = Path(args.defense)
    if not defense_path.is_file():
        print(f"Defense file not found: {defense_path}", file=sys.stderr)
        return 1

    lookup = load_defense_lookup(defense_path)
    print(f"Loaded {len(lookup)} team DEF_TIER labels from {defense_path.name}")

    dirs = _graded_json_dirs(repo)
    paths = _iter_graded_paths(
        dirs,
        date,
        min_date=str(args.min_date or "").strip()[:10],
        max_date=str(args.max_date or "").strip()[:10],
    )
    if not paths:
        print("No graded_props JSON files found.", file=sys.stderr)
        return 1

    total_chk = total_patch = total_have = total_miss = 0
    files_changed = 0

    for path in paths:
        chk, patch, have, miss = patch_file(path, lookup, dry_run=args.dry_run)
        total_chk += chk
        total_patch += patch
        total_have += have
        total_miss += miss
        if patch > 0:
            files_changed += 1
        dry = " (dry)" if args.dry_run else ""
        print(
            f"  {path.relative_to(repo)}: mlb={chk:,} patched={patch:,} "
            f"already={have:,} unmapped_opp={miss:,}{dry}"
        )

    print(
        f"\n{'=' * 60}\n"
        f"Files touched:     {files_changed}\n"
        f"MLB rows checked:  {total_chk:,}\n"
        f"def_tier patched:  {total_patch:,}\n"
        f"already had tier:  {total_have:,}\n"
        f"unmapped opp:      {total_miss:,}\n"
        f"{'DRY RUN — no files written' if args.dry_run else 'Done.'}"
    )

    if not args.dry_run and total_patch > 0:
        print(
            "\nNext:\n"
            "  py -3 scripts/backtest_player_tier_vs_defense.py --sport MLB --from 2026-05-06\n"
            "  py -3 scripts/build_retrain_dataset.py\n"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
