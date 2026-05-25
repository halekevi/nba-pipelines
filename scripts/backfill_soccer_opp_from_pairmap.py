#!/usr/bin/env python3
"""
backfill_soccer_opp_from_pairmap.py
------------------------------------
Recovers blank/UNKNOWN_OPP values in Soccer step8 xlsx archives using
game_pair_map() on co-located step1 CSV files — zero external API calls.

For each date under outputs/<date>/soccer/:
  1. Loads the step1 CSV (s1_soccer_props_<date>.csv or variant)
  2. Builds a pp_game_id → (TEAM_A, TEAM_B) pair map from ALL rows on that date
  3. Opens the step8 xlsx and fills any blank/UNKNOWN_OPP cells using the pair map
  4. Writes the xlsx back in-place (unless --dry-run)

Also patches the step3 CSV if present (step3_soccer_with_defense_<date>.csv),
and ui_runner/templates/graded_props_<date>.json (retrain opp_team source).

Usage:
    # Dry-run — see what would change
    py -3.14 scripts/backfill_soccer_opp_from_pairmap.py --repo-root . --dry-run

    # Live run — all dates
    py -3.14 scripts/backfill_soccer_opp_from_pairmap.py --repo-root .

    # Single date
    py -3.14 scripts/backfill_soccer_opp_from_pairmap.py --repo-root . --date 2026-05-10

    # After running, rebuild training data:
    py -3.14 scripts/build_retrain_dataset.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Optional

import pandas as pd

# ── repo-root bootstrap ───────────────────────────────────────────────────────
def _bootstrap(repo: Path) -> None:
    soc_scripts = repo / "Sports" / "Soccer" / "scripts"
    for p in [str(repo), str(soc_scripts)]:
        if p not in sys.path:
            sys.path.insert(0, p)


# ── constants ─────────────────────────────────────────────────────────────────
_UNKNOWN = frozenset({"", "NAN", "NONE", "NULL", "UNKNOWN", "UNKNOWN_OPP"})
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _is_unknown(v: object) -> bool:
    return str(v or "").strip().upper() in _UNKNOWN


def _norm_player(value: object) -> str:
    s = unicodedata.normalize("NFKC", str(value or ""))
    s = s.casefold().strip()
    return re.sub(r"\s+", " ", s)


def _norm_prop(value: object) -> str:
    s = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"[\s_]+", "", s.casefold().strip())


def _norm_line(value: object) -> str:
    try:
        return str(round(float(str(value).replace(",", "")), 4))
    except (TypeError, ValueError):
        return str(value or "").strip()


# ── step1 CSV discovery ───────────────────────────────────────────────────────
def _find_step1_csv(date_dir: Path, date: str) -> Optional[Path]:
    """Try several naming conventions for the step1 soccer CSV."""
    candidates = [
        date_dir / "soccer" / "step1_soccer_props.csv",
        date_dir / "soccer" / f"s1_soccer_props_{date}.csv",
        date_dir / "soccer" / f"step1_soccer_{date}.csv",
        date_dir / "soccer" / f"step1_soccer_props_{date}.csv",
        date_dir / f"s1_soccer_props_{date}.csv",
        date_dir / f"step1_soccer_{date}.csv",
    ]
    for p in candidates:
        if p.is_file():
            return p
    for p in sorted(date_dir.rglob("s1_soccer*.csv")):
        return p
    for p in sorted(date_dir.rglob("step1_soccer*.csv")):
        return p
    return None


def _find_step8_xlsx(date_dir: Path, date: str) -> Optional[Path]:
    candidates = [
        date_dir / "soccer" / "step8_soccer_direction_clean.xlsx",
        date_dir / "soccer" / f"step8_soccer_direction_clean_{date}.xlsx",
        date_dir / f"step8_soccer_direction_clean_{date}.xlsx",
    ]
    for p in candidates:
        if p.is_file():
            return p
    for p in sorted(date_dir.rglob("step8_soccer_direction_clean*.xlsx")):
        return p
    # parent copies without soccer/ subfolder
    for p in sorted(date_dir.rglob("step8_soccer*.xlsx")):
        return p
    return None


def _find_step3_csv(date_dir: Path, date: str) -> Optional[Path]:
    candidates = [
        date_dir / "soccer" / "step3_soccer_with_defense.csv",
        date_dir / "soccer" / f"step3_soccer_with_defense_{date}.csv",
        date_dir / f"step3_soccer_with_defense_{date}.csv",
    ]
    for p in candidates:
        if p.is_file():
            return p
    for p in sorted(date_dir.rglob("step3_soccer*.csv")):
        return p
    return None


# ── pair map builder from step1 CSV ──────────────────────────────────────────
def load_enriched_step1(step1_path: Path) -> pd.DataFrame:
    """Load step1 and run fill_opp_team_column (home/away + pair-map)."""
    from soccer_opp_utils import fill_opp_team_column  # type: ignore

    df = pd.read_csv(step1_path, dtype=str, encoding="utf-8-sig").fillna("")
    return fill_opp_team_column(df)


def build_pair_map_from_step1(step1_df: pd.DataFrame) -> dict[str, tuple[str, str]]:
    from soccer_opp_utils import game_pair_map  # type: ignore

    return game_pair_map(step1_df)


def build_opp_lookup(step1_df: pd.DataFrame) -> dict[tuple[str, str, str], str]:
    """player|prop|line -> opp_team (for step8 xlsx which lacks pp_game_id)."""
    prop_col = "prop_type" if "prop_type" in step1_df.columns else "prop"
    lookup: dict[tuple[str, str, str], str] = {}
    for row in step1_df.itertuples(index=False):
        opp = str(getattr(row, "opp_team", "") or "").strip().upper()
        if _is_unknown(opp):
            continue
        key = (
            _norm_player(getattr(row, "player", "")),
            _norm_prop(getattr(row, prop_col, "")),
            _norm_line(getattr(row, "line", "")),
        )
        lookup.setdefault(key, opp)
    return lookup


# ── apply pair map to a dataframe ─────────────────────────────────────────────
def apply_pair_map(
    df: pd.DataFrame,
    pair_map: dict[str, tuple[str, str]],
    opp_lookup: dict[tuple[str, str, str], str] | None = None,
) -> tuple[pd.DataFrame, int]:
    """
    Fill blank opp_team cells using pair_map (when pp_game_id present) or
  player+prop+line lookup from enriched step1 (step8 xlsx fallback).
    Returns (patched_df, n_recovered).
    """
    from soccer_opp_utils import opp_from_pair  # type: ignore

    out = df.copy()

    col_map: dict[str, str] = {}
    for c in out.columns:
        cl = c.strip().lower()
        if cl in ("opp_team", "opp", "opponent"):
            col_map["opp_team"] = c
        if cl in ("pp_game_id", "game_id", "gameid"):
            col_map["pp_game_id"] = c
        if cl in ("team", "player_team"):
            col_map["team"] = c
        if cl in ("player",):
            col_map["player"] = c
        if cl in ("prop", "prop_type"):
            col_map["prop"] = c
        if cl in ("line",):
            col_map["line"] = c

    opp_col = col_map.get("opp_team")
    if not opp_col:
        return out, 0

    gid_col = col_map.get("pp_game_id")
    team_col = col_map.get("team")
    player_col = col_map.get("player")
    prop_col = col_map.get("prop")
    line_col = col_map.get("line")

    n_recovered = 0
    for idx, row in out.iterrows():
        if not _is_unknown(row[opp_col]):
            continue

        inferred = ""
        if gid_col and team_col:
            gid = str(row[gid_col] or "").strip()
            team = str(row[team_col] or "").strip().upper()
            inferred = opp_from_pair(gid, team, pair_map)

        if _is_unknown(inferred) and opp_lookup and player_col and prop_col and line_col:
            key = (
                _norm_player(row[player_col]),
                _norm_prop(row[prop_col]),
                _norm_line(row[line_col]),
            )
            inferred = opp_lookup.get(key, "")

        if inferred and not _is_unknown(inferred):
            out.at[idx, opp_col] = inferred
            n_recovered += 1

    return out, n_recovered


# ── patch xlsx in-place ───────────────────────────────────────────────────────
def patch_xlsx(
    xlsx_path: Path,
    pair_map: dict[str, tuple[str, str]],
    opp_lookup: dict[tuple[str, str, str], str],
    dry_run: bool,
) -> tuple[int, int]:
    """Returns (n_unknown_before, n_recovered)."""
    df = pd.read_excel(xlsx_path, engine="openpyxl")

    opp_col = next(
        (c for c in df.columns if c.strip().lower() in ("opp_team", "opp", "opponent")),
        None,
    )
    if not opp_col:
        return 0, 0

    n_before = int(df[opp_col].apply(_is_unknown).sum())
    if n_before == 0:
        return 0, 0

    patched, n_recovered = apply_pair_map(df, pair_map, opp_lookup)

    if not dry_run and n_recovered > 0:
        patched.to_excel(xlsx_path, engine="openpyxl", index=False)

    return n_before, n_recovered


# ── patch step3 CSV in-place ──────────────────────────────────────────────────
def patch_csv(
    csv_path: Path,
    pair_map: dict[str, tuple[str, str]],
    opp_lookup: dict[tuple[str, str, str], str],
    dry_run: bool,
) -> tuple[int, int]:
    """Returns (n_unknown_before, n_recovered)."""
    df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig").fillna("")

    opp_col = next(
        (c for c in df.columns if c.strip().lower() in ("opp_team", "opp", "opponent")),
        None,
    )
    if not opp_col:
        return 0, 0

    n_before = int(df[opp_col].apply(_is_unknown).sum())
    if n_before == 0:
        return 0, 0

    patched, n_recovered = apply_pair_map(df, pair_map, opp_lookup)

    if not dry_run and n_recovered > 0:
        patched.to_csv(csv_path, index=False, encoding="utf-8-sig")

    return n_before, n_recovered


def patch_step1(step1_path: Path, dry_run: bool) -> tuple[int, int, pd.DataFrame]:
    """Write enriched step1 back; returns (unknown_before, recovered, enriched df)."""
    raw = pd.read_csv(step1_path, dtype=str, encoding="utf-8-sig").fillna("")
    before = int(raw["opp_team"].apply(_is_unknown).sum()) if "opp_team" in raw.columns else 0
    df = load_enriched_step1(step1_path)
    after = int(df["opp_team"].apply(_is_unknown).sum()) if "opp_team" in df.columns else 0
    recovered = before - after
    if before > 0 and not dry_run:
        df.to_csv(step1_path, index=False, encoding="utf-8-sig")
    return before, recovered, df


def patch_graded_json(
    repo: Path,
    date: str,
    opp_lookup: dict[tuple[str, str, str], str],
    dry_run: bool,
) -> tuple[int, int]:
    """Patch graded_props JSON (retrain opp_team source). Returns (before, recovered)."""
    path = repo / "ui_runner" / "templates" / f"graded_props_{date}.json"
    if not path.is_file():
        return 0, 0

    data = json.loads(path.read_text(encoding="utf-8"))
    props = data.get("props") or []
    before = 0
    recovered = 0
    for row in props:
        if str(row.get("sport", "")).strip() != "Soccer":
            continue
        if not _is_unknown(row.get("opp_team", "")):
            continue
        before += 1
        key = (
            _norm_player(row.get("player", "")),
            _norm_prop(row.get("prop", "")),
            _norm_line(row.get("line", "")),
        )
        new_opp = opp_lookup.get(key, "")
        if new_opp and not _is_unknown(new_opp):
            row["opp_team"] = new_opp
            recovered += 1

    if recovered and not dry_run:
        data["count"] = len(props)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return before, recovered


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill Soccer opp_team via game_pair_map")
    ap.add_argument("--repo-root", required=True, help="Path to PropORACLE repo root")
    ap.add_argument("--date", default="", help="Single YYYY-MM-DD date (default: all dates)")
    ap.add_argument("--dry-run", action="store_true", help="Report changes without writing files")
    ap.add_argument(
        "--min-unknown",
        type=int,
        default=1,
        help="Skip dates with fewer than this many UNKNOWN_OPP rows in step1 (default 1)",
    )
    args = ap.parse_args()

    repo = Path(args.repo_root).resolve()
    _bootstrap(repo)

    outputs = repo / "outputs"
    if not outputs.is_dir():
        print(f"outputs/ not found at {outputs}")
        return 1

    if args.date:
        dates = [args.date.strip()]
    else:
        dates = sorted(
            p.name for p in outputs.iterdir() if p.is_dir() and _DATE_RE.fullmatch(p.name)
        )

    total_step1_unknown = 0
    total_step1_recovered = 0
    total_xlsx_unknown = 0
    total_xlsx_recovered = 0
    total_graded_unknown = 0
    total_graded_recovered = 0
    dates_improved = 0
    dates_skipped_no_step1 = 0

    for date in dates:
        date_dir = outputs / date

        step1 = _find_step1_csv(date_dir, date)
        if not step1:
            dates_skipped_no_step1 += 1
            continue

        try:
            s1_before, s1_recovered, enriched = patch_step1(step1, args.dry_run)
        except Exception as e:
            print(f"  [{date}] step1 load failed: {e}")
            continue

        if s1_before < args.min_unknown:
            continue

        pair_map = build_pair_map_from_step1(enriched)
        opp_lookup = build_opp_lookup(enriched)

        if not pair_map and not opp_lookup:
            print(f"  [{date}] empty pair_map from {step1.name}")
            continue

        total_step1_unknown += s1_before
        total_step1_recovered += s1_recovered
        s1_residual = s1_before - s1_recovered

        n_before_xlsx = n_rec_xlsx = 0
        step8 = _find_step8_xlsx(date_dir, date)
        if step8:
            try:
                n_before_xlsx, n_rec_xlsx = patch_xlsx(step8, pair_map, opp_lookup, args.dry_run)
            except Exception as e:
                print(f"  [{date}] xlsx patch failed: {e}")

        n_before_csv = n_rec_csv = 0
        step3 = _find_step3_csv(date_dir, date)
        if step3:
            n_before_csv, n_rec_csv = patch_csv(step3, pair_map, opp_lookup, args.dry_run)

        g_before, g_rec = patch_graded_json(repo, date, opp_lookup, args.dry_run)

        total_xlsx_unknown += n_before_xlsx
        total_xlsx_recovered += n_rec_xlsx
        total_graded_unknown += g_before
        total_graded_recovered += g_rec

        if s1_recovered > 0 or n_rec_xlsx > 0 or g_rec > 0:
            dates_improved += 1

        tag = "(dry)" if args.dry_run else "ok"
        csv_note = f" | step3: {n_rec_csv}/{n_before_csv}" if step3 else ""
        print(
            f"  [{date}] step1: {s1_recovered}/{s1_before} (residual {s1_residual}) | "
            f"xlsx: {n_rec_xlsx}/{n_before_xlsx} | "
            f"graded: {g_rec}/{g_before}{csv_note}  {tag}"
        )

    s1_pct = total_step1_recovered / total_step1_unknown if total_step1_unknown else 0.0
    dry_note = " (DRY RUN — no files written)" if args.dry_run else ""
    print(
        f"\n{'=' * 60}\n"
        f"Dates improved:          {dates_improved}\n"
        f"Dates skipped (no s1):   {dates_skipped_no_step1}\n"
        f"step1 UNKNOWN_OPP:       {total_step1_unknown:,}\n"
        f"step1 recovered:         {total_step1_recovered:,} ({s1_pct:.1%})\n"
        f"step1 residual:          {total_step1_unknown - total_step1_recovered:,}\n"
        f"xlsx recovered:          {total_xlsx_recovered:,} / {total_xlsx_unknown:,}\n"
        f"graded JSON recovered:   {total_graded_recovered:,} / {total_graded_unknown:,}\n"
        f"{dry_note}"
    )

    if not args.dry_run and (total_step1_recovered > 0 or total_graded_recovered > 0):
        print(
            "\nNext step:\n"
            "  py -3.14 scripts/build_retrain_dataset.py\n"
            "  py -3.14 scripts/train_edge_model.py --input-csv data/retrain_dataset.csv "
            "--temporal-split --temporal-date-column file_date\n"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
