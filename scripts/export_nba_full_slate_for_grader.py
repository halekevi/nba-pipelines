"""
Build slate_grader-compatible NBA workbook (sheet ALL) from combined_slate_tickets Full Slate.

When step8 date-filter leaves almost no rows, this matches the ~1k+ NBA props on the ticket sheet.

Usage:
  py scripts/export_nba_full_slate_for_grader.py --input outputs/2026-05-01/combined_slate_tickets_2026-05-01_131728.xlsx \\
      --output outputs/2026-05-01/nba_full_slate_for_grade_2026-05-01.xlsx --date 2026-05-01
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
SHEET = "Full Slate"

# (source column on Full Slate, output column for slate_grader load_nba)
COL_MAP: list[tuple[str, str]] = [
    ("Player", "Player"),
    ("Tier", "Tier"),
    ("Rank Score", "Rank Score"),
    ("Team", "Team"),
    ("Opp", "Opp"),
    ("Game Time", "Game Time"),
    ("Prop", "Prop"),
    ("Pick Type", "Pick Type"),
    ("Line", "Line"),
    ("Dir", "Direction"),
    ("Edge", "Edge"),
    ("Def Tier", "Def Tier"),
    ("Min Tier", "Min Tier"),
    ("Shot Role", "Shot Role"),
    ("Usage Role", "Usage Role"),
    ("Void Reason", "Void Reason"),
    ("ML Prob", "ML Prob"),
    ("ML Edge", "ML Edge"),
    ("Edge Score", "Edge Score"),
    ("Blended Score", "Blended Score"),
]


def _pick_input(path_or_glob: str) -> Path:
    p = Path(path_or_glob)
    if p.is_file():
        return p.resolve()
    for base in (REPO_ROOT, Path.cwd()):
        hits = sorted(base.glob(path_or_glob), key=lambda x: x.stat().st_mtime, reverse=True)
        if hits:
            return hits[0].resolve()
    raise FileNotFoundError(f"No file matched: {path_or_glob}")


def _filter_grade_date(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    """Match grade date to Game Time. Full Slate often uses 'MM/DD HH:MM' with no year."""
    if not date_str or df.empty:
        return df
    col = None
    for c in ("Game Time", "game_time", "game_start"):
        if c in df.columns:
            col = c
            break
    if not col:
        return df
    try:
        target = pd.to_datetime(date_str).date()
    except Exception:
        return df
    ts = pd.to_datetime(df[col], errors="coerce")
    # Month/day only — avoids 1900/default-year parses missing the slate year
    mask = (ts.dt.month == target.month) & (ts.dt.day == target.day) & ts.notna()
    if not mask.any():
        if ts.notna().sum() == 0:
            print("WARN: No parseable Game Time; keeping all rows for export.", file=sys.stderr)
            return df
        print(
            f"WARN: Game Time MD filter ({target.month:02d}-{target.day:02d}) for {date_str}: "
            "0 rows — export empty.",
            file=sys.stderr,
        )
        return df.iloc[0:0].copy()
    print(f"INFO: Game Time filter ({date_str}): kept {int(mask.sum())}/{len(df)} rows")
    return df.loc[mask].copy()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="combined_slate_tickets xlsx path or glob under repo")
    ap.add_argument("--output", required=True, help="Target xlsx (sheet ALL)")
    ap.add_argument("--date", required=True, help="Grade date YYYY-MM-DD")
    args = ap.parse_args()

    src = _pick_input(args.input)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    df = pd.read_excel(src, sheet_name=SHEET, engine="openpyxl")
    if "Sport" not in df.columns:
        print(f"ERROR: {SHEET} missing Sport. Columns: {list(df.columns)}", file=sys.stderr)
        sys.exit(2)

    sport_u = df["Sport"].astype(str).str.strip().str.upper()
    nba = df.loc[sport_u == "NBA"].copy()
    nba = _filter_grade_date(nba, args.date.strip())

    if nba.empty:
        if out.exists():
            out.unlink()
        print(f"WARN: 0 NBA rows to export from {src.name}", file=sys.stderr)
        sys.exit(0)

    pieces: dict[str, pd.Series] = {}
    for src_c, dest_c in COL_MAP:
        if src_c in nba.columns:
            pieces[dest_c] = nba[src_c]

    if "Projection" not in pieces:
        if "Proj" in nba.columns:
            pieces["Projection"] = nba["Proj"]
        elif "Projection" in nba.columns:
            pieces["Projection"] = nba["Projection"]

    out_df = pd.DataFrame(pieces)

    with pd.ExcelWriter(out, engine="openpyxl") as w:
        out_df.to_excel(w, sheet_name="ALL", index=False)

    print(f"OK {len(out_df)} NBA rows -> {out} (from {src.name})")


if __name__ == "__main__":
    main()
