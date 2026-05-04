"""
Build slate_grader-compatible NBA workbook (sheet ALL) from combined_slate_tickets Full Slate.

When step8 date-filter leaves almost no rows, this matches the ~1k+ NBA props on the ticket sheet.

Ticket-building (combined_slate_tickets) intentionally keeps Demon legs off the Full Slate pool
(EV / pool pick-type gates). For grading we still need Demon rows in ALL so slate_grader can emit
HIT/MISS. When Full Slate has zero Demons, we merge Demon rows from the dated NBA step8 workbook.

Other sports grade from step8 xlsx directly in run_grader.ps1 — only NBA uses this export path.

Usage:
  py scripts/export_nba_full_slate_for_grader.py --input outputs/2026-05-01/combined_slate_tickets_2026-05-01_131728.xlsx \\
      --output outputs/2026-05-01/nba_full_slate_for_grade_2026-05-01.xlsx --date 2026-05-01
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
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
    ("Game Date", "Game Date"),
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
    """Match grade date to Game Date (preferred) or Game Time (MM/DD often has no year)."""
    if not date_str or df.empty:
        return df
    ds = str(date_str).strip()[:10]
    if "Game Date" in df.columns:
        gd = df["Game Date"].astype(str).str.strip().str[:10]
        nonempty = gd.ne("") & gd.ne("nan") & gd.ne("None")
        if nonempty.any():
            mask = gd.eq(ds) & nonempty
            if mask.any():
                print(f"INFO: Game Date filter ({date_str}): kept {int(mask.sum())}/{len(df)} rows")
                return df.loc[mask].copy()
            print(
                f"WARN: Game Date filter for {date_str}: 0 rows — export empty.",
                file=sys.stderr,
            )
            return df.iloc[0:0].copy()
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


def _resolve_nba_step8_path(grade_date: str) -> Path | None:
    ds = str(grade_date).strip()[:10]
    for p in (
        REPO_ROOT / "outputs" / ds / f"step8_nba_direction_clean_{ds}.xlsx",
        REPO_ROOT / "Sports" / "NBA" / f"step8_nba_direction_clean_{ds}.xlsx",
    ):
        if p.is_file():
            return p
    return None


def _map_step8_demon_rows_to_grader(
    step8: pd.DataFrame,
    *,
    grade_date: str,
    out_columns: list[str],
) -> pd.DataFrame:
    """
    Map pipeline step8 (human-readable headers) onto slate_grader ALL sheet column names.
    Only Demon rows; same Game Date / Game Time filter as Full Slate NBA slice.
    """
    s8 = _filter_grade_date(step8.copy(), grade_date)
    if s8.empty or "Pick Type" not in s8.columns:
        return pd.DataFrame(columns=out_columns)
    dem = s8.loc[s8["Pick Type"].astype(str).str.strip().str.upper() == "DEMON"].copy()
    # Grading slate: keep every Demon row from step8. (combined_slate_tickets still drops Demon+OVER
    # for ticket hygiene — step8 encodes Demon sides as OVER by convention.)
    if dem.empty:
        return pd.DataFrame(columns=out_columns)

    dir_series = dem["Direction"] if "Direction" in dem.columns else dem.get("Dir", pd.Series(np.nan, index=dem.index))

    ds = str(grade_date).strip()[:10]
    if "Game Date" in dem.columns:
        g = dem["Game Date"].astype(str).str.strip().str[:10]
        nonempty = g.ne("") & g.ne("nan") & g.ne("None")
        gd_out = np.where(nonempty.to_numpy(), g.to_numpy(), ds)
        gd_out = pd.Series(gd_out, index=dem.index, dtype=object)
    else:
        gd_out = pd.Series(ds, index=dem.index, dtype=object)

    ml_prob = pd.to_numeric(dem.get("ML Prob", np.nan), errors="coerce")
    ml_edge = dem.get("ML Edge", pd.Series(np.nan, index=dem.index))
    if isinstance(ml_edge, pd.Series) and not ml_edge.notna().any():
        ml_edge = ml_prob - 0.5

    proj = dem.get("Projection", dem.get("Proj", pd.Series(np.nan, index=dem.index)))

    mapped = pd.DataFrame(
        {
            "Player": dem.get("Player"),
            "Tier": dem.get("Tier"),
            "Rank Score": dem.get("Rank Score"),
            "Team": dem.get("Team"),
            "Opp": dem.get("Opp"),
            "Game Date": gd_out,
            "Game Time": dem.get("Game Time"),
            "Prop": dem.get("Prop"),
            "Pick Type": dem.get("Pick Type"),
            "Line": dem.get("Line"),
            "Direction": dir_series,
            "Edge": dem.get("Edge"),
            "Def Tier": dem.get("Def Tier"),
            "Min Tier": dem.get("Min Tier"),
            "Shot Role": dem.get("Shot Role"),
            "Usage Role": dem.get("Usage Role"),
            "Void Reason": dem.get("Void Reason"),
            "ML Prob": ml_prob,
            "ML Edge": pd.to_numeric(ml_edge, errors="coerce"),
            "Edge Score": dem.get("Edge Score"),
            "Blended Score": dem.get("Blended Score"),
            "Projection": proj,
        }
    )
    for c in out_columns:
        if c not in mapped.columns:
            mapped[c] = np.nan
    return mapped.reindex(columns=out_columns)


def _merge_step8_demons_if_missing(
    out_df: pd.DataFrame,
    *,
    grade_date: str,
    src_name: str,
) -> pd.DataFrame:
    pt = out_df.get("Pick Type", pd.Series(dtype=object)).astype(str).str.strip().str.upper()
    if pt.eq("DEMON").any():
        return out_df
    step8_path = _resolve_nba_step8_path(grade_date)
    if not step8_path:
        print(
            f"[export_nba_full_slate_for_grader] No Demon rows on Full Slate; "
            f"step8 not found for merge (date={grade_date}).",
            file=sys.stderr,
        )
        return out_df
    s8 = pd.read_excel(step8_path, engine="openpyxl")
    demon_part = _map_step8_demon_rows_to_grader(s8, grade_date=grade_date, out_columns=list(out_df.columns))
    if demon_part.empty:
        print(
            f"[export_nba_full_slate_for_grader] No Demon rows in step8 after date filter: {step8_path.name}",
            file=sys.stderr,
        )
        return out_df
    merged = pd.concat([out_df, demon_part], ignore_index=True)
    keys = [k for k in ("Player", "Prop", "Pick Type", "Line", "Direction") if k in merged.columns]
    if keys:
        before = len(merged)
        merged = merged.drop_duplicates(subset=keys, keep="first")
        dup = before - len(merged)
        if dup:
            print(
                f"[export_nba_full_slate_for_grader] drop_duplicates removed {dup} overlapping row(s) "
                f"(keys={keys})",
                file=sys.stderr,
            )
    n_dem = int(merged["Pick Type"].astype(str).str.strip().str.upper().eq("DEMON").sum())
    print(
        f"[export_nba_full_slate_for_grader] Merged {len(demon_part)} Demon row(s) from step8 "
        f"({step8_path.name}); ALL sheet Demon count={n_dem} (from {src_name})"
    )
    return merged


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
    out_df = _merge_step8_demons_if_missing(out_df, grade_date=args.date.strip(), src_name=src.name)

    with pd.ExcelWriter(out, engine="openpyxl") as w:
        out_df.to_excel(w, sheet_name="ALL", index=False)

    print(f"OK {len(out_df)} NBA rows -> {out} (from {src.name})")


if __name__ == "__main__":
    main()
