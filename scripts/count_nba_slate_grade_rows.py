#!/usr/bin/env python3
"""Emit row count of an NBA slate after the same date filter as slate_grader (digits only on stdout)."""
from __future__ import annotations

import argparse
import contextlib
import io
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from grading.slate_grader import filter_nba_slate_by_grade_date, load_nba  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slate", required=True, help="Path to NBA / NBA1H / NBA1Q slate .xlsx")
    ap.add_argument("--date", required=True, help="Grade date YYYY-MM-DD")
    args = ap.parse_args()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        df = load_nba(args.slate)
        df_f = filter_nba_slate_by_grade_date(df, args.date)
    sys.stdout.write(str(len(df_f)))


if __name__ == "__main__":
    main()
