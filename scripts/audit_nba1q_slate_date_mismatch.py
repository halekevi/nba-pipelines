#!/usr/bin/env python3
"""Flag step8 NBA1Q slates where the outputs folder (or filename) date ≠ Game Time day(s).

Scans ``outputs/**/step8_nba1q*.xlsx``, reads ``ALL`` (or first sheet) ``Game Time``,
and compares to the ``YYYY-MM-DD`` in the parent folder path and in the file stem.

Example::

    python scripts/audit_nba1q_slate_date_mismatch.py
    python scripts/audit_nba1q_slate_date_mismatch.py --outputs-dir outputs

Run after a pipeline day so ``outputs/<date>/`` exists locally.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

_FOLDER_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")
_STEM_DATES = re.compile(r"(20\d{2}-\d{2}-\d{2})")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _folder_date_fixed(path: Path) -> str | None:
    for part in path.parts:
        m = _FOLDER_DATE.fullmatch(part)
        if m:
            return m.group(1)
    return None


def _dates_in_stem(stem: str) -> list[str]:
    return sorted(set(_STEM_DATES.findall(stem)))


def _game_time_dates(path: Path) -> tuple[set[str], str | None]:
    try:
        xl = pd.ExcelFile(path, engine="openpyxl")
    except Exception as e:
        return set(), f"open_error:{e}"
    sheet = "ALL" if "ALL" in xl.sheet_names else xl.sheet_names[0]
    try:
        df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
    except Exception as e:
        return set(), f"read_error:{e}"
    col = None
    for c in df.columns:
        cl = str(c).strip().lower()
        if cl in ("game time", "game_time"):
            col = c
            break
    if col is None:
        return set(), "no_game_time_col"
    ts = pd.to_datetime(df[col], errors="coerce")
    days = {str(d) for d in ts.dt.date.dropna().unique()}
    return days, None


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit NBA1Q step8 folder/file dates vs Game Time.")
    ap.add_argument(
        "--outputs-dir",
        type=Path,
        default=None,
        help="Directory to scan (default: <repo>/outputs)",
    )
    args = ap.parse_args()
    root = _repo_root()
    out_dir = args.outputs_dir or (root / "outputs")
    if not out_dir.is_dir():
        print(f"No such directory: {out_dir}", file=sys.stderr)
        return 1

    paths = sorted(out_dir.rglob("step8_nba1q*.xlsx"))
    if not paths:
        print(f"No step8_nba1q*.xlsx under {out_dir}")
        return 0

    rows_out: list[str] = []
    mismatches = 0
    for p in paths:
        rel = p.relative_to(out_dir) if p.is_relative_to(out_dir) else p
        fdate = _folder_date_fixed(p.resolve())
        stem_dates = _dates_in_stem(p.stem)
        gdays, err = _game_time_dates(p)
        g_sorted = ",".join(sorted(gdays))
        stem_s = ",".join(stem_dates) if stem_dates else ""
        if err:
            line = f"{rel}\tfolder={fdate or ''}\tfile_dates={stem_s}\tgame_days=\tERROR={err}"
            rows_out.append(line)
            continue
        if not gdays:
            rows_out.append(f"{rel}\tfolder={fdate or ''}\tfile_dates={stem_s}\tgame_days=(none)\tERROR=no_parseable_game_days")
            continue

        ok = True
        reasons: list[str] = []
        if fdate and fdate not in gdays:
            ok = False
            reasons.append(f"folder_not_in_game_days({fdate} vs {g_sorted})")
        for sd in stem_dates:
            if sd not in gdays:
                ok = False
                reasons.append(f"stem_date_not_in_game_days({sd})")
        if len(gdays) > 1:
            reasons.append(f"multi_game_days({g_sorted})")

        flag = "OK" if ok and len(gdays) == 1 else "CHECK"
        if not ok:
            mismatches += 1
            flag = "MISMATCH"
        note = "; ".join(reasons) if reasons else ""
        rows_out.append(
            f"{rel}\tfolder={fdate or ''}\tfile_dates={stem_s}\tgame_days={g_sorted}\t{flag}\t{note}"
        )

    print("path\tfolder\tfile_dates\tgame_days\tstatus\tnote")
    for line in rows_out:
        print(line)

    print(f"\nScanned {len(paths)} file(s); mismatches={mismatches}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
