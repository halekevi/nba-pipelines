#!/usr/bin/env python3
"""
Backfill stack signal columns on graded_props_*.json archives and graded xlsx Box Raw.

Fills def_tier, L5/L10 counts, hit_rate, strat rates, and top-3 context from defense CSVs,
dated step8 slates, and strat lookup when missing on archive rows.

Usage:
  python scripts/backfill_graded_def_tier.py --dry-run
  python scripts/backfill_graded_def_tier.py --date 2026-06-08
  python scripts/backfill_graded_def_tier.py --workbooks
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from utils.graded_enrichment import (  # noqa: E402
    enrich_graded_for_analysis,
    is_empty_def_tier,
    is_empty_numeric,
    is_empty_value,
)
from utils.graded_schema import normalize_graded_df, recover_direction_if_missing  # noqa: E402
from utils.stack_context_cols import GRADED_SIGNAL_COLS  # noqa: E402

_GRADED_RE = re.compile(r"^graded_props_(\d{4}-\d{2}-\d{2})\.json$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_TOP3_INT_COLS = frozenset({"top3_weak_overperformer", "top3_elite_fader", "top3_def_context", "top3_under_context"})
_NUMERIC_COLS = frozenset({
    "l5_over", "l5_under", "l10_over", "l10_under", "l10_games_played",
    "hit_rate", "hit_rate_l5", "hit_rate_l10",
    "strat_hit_rate", "strat_n",
    "player_hr_historical", "opp_hr_historical",
    "confidence_score",
    "team_top3_rank", "team_bottom3_rank", "def_boost_hist",
})


def _apply_patch_value(entry: dict, col: str, val: object) -> None:
    if col in {"def_tier", "l10_streak", "sport_signal_maturity", "confidence_tier", "confidence_note"}:
        entry[col] = str(val)
    elif col in _TOP3_INT_COLS:
        entry[col] = int(np.nan_to_num(pd.to_numeric(val, errors="coerce"), nan=0.0))
    elif col in _NUMERIC_COLS:
        num = pd.to_numeric(val, errors="coerce")
        if pd.notna(num):
            entry[col] = float(num) if col in {"hit_rate", "strat_hit_rate", "team_top3_rank", "team_bottom3_rank", "def_boost_hist"} else int(num)
    else:
        entry[col] = str(val)


def _graded_json_dirs(repo: Path) -> list[Path]:
    return [p for p in (repo / "ui_runner" / "templates", repo / "mobile" / "www") if p.is_dir()]


def _iter_graded_json(
    dirs: list[Path],
    *,
    date: str,
    min_date: str,
    max_date: str,
) -> list[Path]:
    out: list[Path] = []
    for base in dirs:
        if date:
            p = base / f"graded_props_{date}.json"
            if p.is_file():
                out.append(p)
            continue
        for p in sorted(base.glob("graded_props_*.json")):
            if ".bak_" in p.name:
                continue
            m = _GRADED_RE.match(p.name)
            if not m:
                continue
            fd = m.group(1)
            if min_date and fd < min_date:
                continue
            if max_date and fd > max_date:
                continue
            out.append(p)
    return out


def _discover_workbooks(repo: Path) -> list[Path]:
    roots = [repo / "ui_runner" / "graded_slate", repo / "outputs"]
    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("graded_*.xlsx"):
            if "combined_tickets_graded" in p.name.lower():
                continue
            key = str(p.resolve())
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
    return sorted(out)


def _pick_sheet(xl: pd.ExcelFile) -> str | None:
    if "Box Raw" in xl.sheet_names:
        return "Box Raw"
    if "GRADED" in xl.sheet_names:
        return "GRADED"
    for s in xl.sheet_names:
        try:
            df = pd.read_excel(xl, sheet_name=s, nrows=3)
        except Exception:
            continue
        if "result" in {str(c).lower() for c in df.columns}:
            return s
    return xl.sheet_names[0] if xl.sheet_names else None


def _row_needs_patch(entry: dict, enriched: pd.Series) -> bool:
    for col in GRADED_SIGNAL_COLS:
        if col not in enriched.index:
            continue
        new = enriched.get(col)
        cur = entry.get(col)
        if col == "def_tier":
            if is_empty_def_tier(cur) and not is_empty_def_tier(new):
                return True
        elif col in _NUMERIC_COLS:
            if is_empty_numeric(cur) and not is_empty_numeric(new):
                return True
        elif col in _TOP3_INT_COLS:
            cur_i = int(np.nan_to_num(pd.to_numeric(cur, errors="coerce"), nan=0.0))
            new_i = int(np.nan_to_num(pd.to_numeric(new, errors="coerce"), nan=0.0))
            if cur_i == 0 and new_i != 0:
                return True
        elif is_empty_value(cur) and not is_empty_value(new):
            return True
    return False


def patch_json_file(path: Path, *, dry_run: bool) -> tuple[int, int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        entries = data
        wrap_list = True
        root = None
    else:
        root = data
        entries = data.get("props") or data.get("picks") or data.get("rows") or []
        wrap_list = False
    if not entries:
        return 0, 0

    rows = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        rows.append(
            {
                "sport": e.get("sport"),
                "player": e.get("player"),
                "prop_type": e.get("prop") or e.get("prop_type"),
                "prop": e.get("prop") or e.get("prop_type"),
                "line": e.get("line"),
                "direction": e.get("direction") or e.get("over_under"),
                "pick_type": e.get("pick_type"),
                "def_tier": e.get("def_tier"),
                "opp_team": e.get("opp_team"),
                "result": e.get("result"),
                **{c: e.get(c) for c in GRADED_SIGNAL_COLS},
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return 0, 0
    file_date = str(data.get("date") if isinstance(data, dict) else "")[:10] or path.stem.replace("graded_props_", "")[:10]
    df["_slate_date"] = file_date
    enriched_df = enrich_graded_for_analysis(df, stack_eligible=False, attach_context=False)

    checked = 0
    patched = 0
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        checked += 1
        er = enriched_df.iloc[i] if i < len(enriched_df) else None
        if er is None:
            continue
        if not _row_needs_patch(entry, er):
            continue
        for col in GRADED_SIGNAL_COLS:
            if col not in er.index:
                continue
            val = er.get(col)
            if col in _NUMERIC_COLS:
                if is_empty_numeric(val):
                    continue
            elif col in _TOP3_INT_COLS:
                if int(np.nan_to_num(pd.to_numeric(val, errors="coerce"), nan=0.0)) == 0:
                    continue
            elif is_empty_value(val):
                continue
            _apply_patch_value(entry, col, val)
        patched += 1

    if patched and not dry_run:
        if wrap_list:
            out = entries
        else:
            out = dict(root)
            for k in ("props", "picks", "rows"):
                if k in out:
                    out[k] = entries
                    break
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return checked, patched


def patch_workbook(path: Path, *, dry_run: bool) -> tuple[int, int]:
    try:
        xl = pd.ExcelFile(path, engine="openpyxl")
    except Exception:
        return 0, 0
    sheet = _pick_sheet(xl)
    if not sheet:
        return 0, 0
    try:
        df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
    except Exception:
        return 0, 0
    if df.empty:
        return 0, 0
    raw = df.copy()
    norm = recover_direction_if_missing(normalize_graded_df(df.copy()))
    if "_sport" not in norm.columns:
        name = path.name.lower()
        for sp in ("nba1q", "nba1h", "wnba", "wcbb", "cbb", "nba", "nhl", "soccer", "mlb", "tennis"):
            if sp in name:
                norm["_sport"] = sp.upper()
                break
    for part in path.parts:
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})", part)
        if m:
            norm["_slate_date"] = m.group(1)
            break
    enriched = enrich_graded_for_analysis(norm, stack_eligible=False, attach_context=False)
    if len(enriched) != len(raw):
        return 0, 0

    patched_rows = 0
    for col in ("def_tier", *STACK_CONTEXT_COLS):
        if col not in enriched.columns:
            continue
        display = col
        if col == "def_tier" and "Def Tier" in raw.columns:
            display = "Def Tier"
        if display not in raw.columns:
            raw[display] = np.nan if col not in _TOP3_INT_COLS else 0
        for i in range(len(raw)):
            old = raw.at[i, display]
            new = enriched.at[i, col]
            if col == "def_tier":
                if not is_empty_def_tier(old) or is_empty_def_tier(new):
                    continue
            elif col in _TOP3_INT_COLS:
                if int(pd.to_numeric(old, errors="coerce") or 0) != 0:
                    continue
                if int(pd.to_numeric(new, errors="coerce") or 0) == 0:
                    continue
            elif not is_empty_value(old) or is_empty_value(new):
                continue
            raw.at[i, display] = new
            patched_rows += 1

    if patched_rows and not dry_run:
        with pd.ExcelWriter(path, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            raw.to_excel(writer, sheet_name=sheet, index=False)
    return len(raw), (1 if patched_rows else 0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", default=str(_REPO))
    ap.add_argument("--date", default="", help="Single YYYY-MM-DD")
    ap.add_argument("--from", dest="min_date", default="", metavar="DATE")
    ap.add_argument("--to", dest="max_date", default="", metavar="DATE")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workbooks", action="store_true", help="Also patch graded_*.xlsx Box Raw sheets")
    ap.add_argument("--json-only", action="store_true", help="Skip workbook pass")
    args = ap.parse_args()

    repo = Path(args.repo_root).resolve()
    date = str(args.date or "").strip()[:10]
    if date and not _DATE_RE.fullmatch(date):
        print(f"Invalid --date: {date}", file=sys.stderr)
        return 1

    dirs = _graded_json_dirs(repo)
    paths = _iter_graded_json(
        dirs,
        date=date,
        min_date=str(args.min_date or "")[:10],
        max_date=str(args.max_date or "")[:10],
    )
    if not paths and not args.workbooks:
        print("No graded_props JSON files found.", file=sys.stderr)
        return 1

    total_chk = total_patch = 0
    for path in paths:
        chk, patch = patch_json_file(path, dry_run=args.dry_run)
        total_chk += chk
        total_patch += patch
        if patch:
            print(f"  JSON {path.relative_to(repo)}: rows={chk:,} patched={patch:,}{' (dry)' if args.dry_run else ''}")

    wb_changed = 0
    if args.workbooks and not args.json_only:
        for wb in _discover_workbooks(repo):
            if date and date not in str(wb):
                continue
            chk, changed = patch_workbook(wb, dry_run=args.dry_run)
            if changed:
                wb_changed += 1
                print(f"  XLSX {wb.relative_to(repo)}: rows={chk:,}{' (dry)' if args.dry_run else ''}")

    print(
        f"\nJSON rows checked: {total_chk:,}\n"
        f"JSON rows patched: {total_patch:,}\n"
        f"Workbooks touched: {wb_changed}\n"
        f"{'DRY RUN' if args.dry_run else 'Done.'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
