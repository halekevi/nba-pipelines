#!/usr/bin/env python3
"""
Concatenate multiple fetch_draftkings_player_props.py CSVs (same schema) into one file
for combined_slate_tickets.py alt-book merge (board_sport distinguishes leagues).

Skips missing paths. Writes DK_OUTPUT_COLUMNS; empty inputs -> empty CSV with headers.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from pickem_step1_schema import DK_OUTPUT_COLUMNS  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Merge DraftKings pickem CSVs for alt-book join")
    ap.add_argument("--inputs", nargs="+", required=True, help="One or more DK fetch CSV paths")
    ap.add_argument("-o", "--output", required=True, help="Merged CSV path")
    args = ap.parse_args()

    frames: list[pd.DataFrame] = []
    for raw in args.inputs:
        p = Path(str(raw).strip().strip('"'))
        if not p.is_file():
            continue
        try:
            df = pd.read_csv(p, dtype=str, encoding="utf-8-sig").fillna("")
        except Exception as e:
            print(f"[merge-dk] skip {p}: {e}", file=sys.stderr)
            continue
        if df.empty:
            continue
        for c in DK_OUTPUT_COLUMNS:
            if c not in df.columns:
                df[c] = ""
        frames.append(df[DK_OUTPUT_COLUMNS])

    if not frames:
        out = pd.DataFrame(columns=DK_OUTPUT_COLUMNS)
    else:
        out = pd.concat(frames, ignore_index=True)
        out = out.drop_duplicates(subset=["projection_id"], keep="first").reset_index(drop=True)

    outp = Path(args.output)
    outp.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(outp, index=False, encoding="utf-8-sig")
    print(f"[merge-dk] {len(out)} rows -> {outp}")


if __name__ == "__main__":
    main()
