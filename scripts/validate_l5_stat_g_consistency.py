#!/usr/bin/env python3
"""
Validate last-5 (L5) metrics stored in pipeline / personal exports against
recomputed values from stat_g1..stat_g5 (or G1..G5) and line.

Uses the same over/under/push rules as step5_add_line_hit_rates*.py:
  over  = count(stat > line)
  under = count(stat < line)
  push  = count(stat == line)
  played = count(non-null stat in window)

Writes summary JSON + mismatch CSV under outputs/<date>/.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


def _get_stat_cols(df: pd.DataFrame, n: int) -> List[str]:
    return [f"stat_g{i}" for i in range(1, n + 1) if f"stat_g{i}" in df.columns]


def _ensure_stat_g_from_g_labels(df: pd.DataFrame) -> pd.DataFrame:
    """Copy G1..G5 into stat_g1..stat_g5 when the latter are missing."""
    out = df
    for i in range(1, 6):
        g = f"G{i}"
        sg = f"stat_g{i}"
        if sg not in out.columns and g in out.columns:
            out = out.copy()
            out[sg] = out[g]
    return out


def _compute_hits(
    df: pd.DataFrame,
    stat_cols: List[str],
    line_series: pd.Series,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    sub = df[stat_cols].apply(pd.to_numeric, errors="coerce")
    line = pd.to_numeric(line_series, errors="coerce")
    played = sub.notna().sum(axis=1).astype(float)
    over = sub.gt(line, axis=0).sum(axis=1).astype(float)
    under = sub.lt(line, axis=0).sum(axis=1).astype(float)
    push = sub.eq(line, axis=0).sum(axis=1).astype(float)
    denom_played = played.replace(0, np.nan)
    over_rate_played = over / denom_played
    under_rate_played = under / denom_played
    denom_ou = (over + under).replace(0, np.nan)
    over_rate_ou = over / denom_ou
    under_rate_ou = under / denom_ou
    return played, over, under, push, over_rate_played, under_rate_played, over_rate_ou, under_rate_ou


def _first_col(df: pd.DataFrame, names: Sequence[str]) -> Optional[str]:
    for n in names:
        if n in df.columns:
            return n
    return None


def _numeric_series(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def _close(a: pd.Series, b: pd.Series, rtol: float, atol: float) -> pd.Series:
    """Element-wise approximate equality; NaN matches NaN."""
    a = pd.to_numeric(a, errors="coerce")
    b = pd.to_numeric(b, errors="coerce")
    both_nan = a.isna() & b.isna()
    one_nan = a.isna() ^ b.isna()
    ok = np.isclose(a.to_numpy(dtype=float), b.to_numpy(dtype=float), rtol=rtol, atol=atol, equal_nan=True)
    return pd.Series(both_nan | (~one_nan & ok), index=a.index)


def _direction_series(df: pd.DataFrame) -> pd.Series:
    col = _first_col(df, ("direction", "Direction", "final_bet_direction", "recommended_side"))
    if not col:
        return pd.Series("OVER", index=df.index, dtype=object)
    return (
        df[col]
        .astype(str)
        .str.strip()
        .str.upper()
        .replace({"": "OVER"})
    )


def _unsupported_mask(df: pd.DataFrame) -> pd.Series:
    if "unsupported_prop" not in df.columns:
        return pd.Series(False, index=df.index)
    return (
        pd.to_numeric(df["unsupported_prop"], errors="coerce")
        .fillna(0)
        .astype(int)
        .eq(1)
    )


def _period_hit_rate_style(sport: str) -> bool:
    """
    NBA1Q / NBA1H Step 8 rebuild sets line_hit_rate_over_ou_5 to over/valid_n
    (games played), not over/(over+under). Match that when validating exports.
    """
    return sport.upper() in ("NBA1Q", "NBA1H")


@dataclass
class SourceDef:
    sport: str
    path: str
    sheet: Optional[str] = None


def _read_any(path: str, preferred_sheet: Optional[str]) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        xls = pd.ExcelFile(path, engine="openpyxl")
        sheet = preferred_sheet if preferred_sheet and preferred_sheet in xls.sheet_names else (
            "ALL" if "ALL" in xls.sheet_names else xls.sheet_names[0]
        )
        return pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
    return pd.read_csv(path, low_memory=False, encoding="utf-8-sig")


def _default_sources(repo_root: str, date: str) -> List[SourceDef]:
    cbb_dated = os.path.join(repo_root, "CBB", "outputs", date, "step6_ranked_cbb.xlsx")
    cbb_fallback = os.path.join(repo_root, "CBB", "step6_ranked_cbb.xlsx")
    cbb_path = cbb_dated if os.path.exists(cbb_dated) else cbb_fallback
    wcbb_dated = os.path.join(repo_root, "CBB", "outputs", date, "step6_ranked_wcbb.xlsx")
    wcbb_fallback = os.path.join(repo_root, "CBB", "step6_ranked_wcbb.xlsx")
    wcbb_path = wcbb_dated if os.path.exists(wcbb_dated) else wcbb_fallback
    return [
        SourceDef("NBA", os.path.join(repo_root, "NBA", "data", "outputs", "step8_all_direction_clean.xlsx"), "ALL"),
        SourceDef("CBB", cbb_path, "ALL"),
        SourceDef("NHL", os.path.join(repo_root, "NHL", "outputs", "step8_nhl_direction_clean.xlsx"), "All Props"),
        SourceDef("Soccer", os.path.join(repo_root, "Soccer", "outputs", "step8_soccer_direction_clean.xlsx"), "ALL"),
        SourceDef("MLB", os.path.join(repo_root, "MLB", "step8_mlb_direction_clean.xlsx"), "ALL"),
        SourceDef("NBA1Q", os.path.join(repo_root, "NBA", "step8_nba1q_direction_clean.xlsx"), "ALL"),
        SourceDef("NBA1H", os.path.join(repo_root, "NBA", "step8_nba1h_direction_clean.xlsx"), "ALL"),
        SourceDef("WCBB", wcbb_path, "ALL"),
    ]


def evaluate_file(
    src: SourceDef,
    rtol: float,
    atol: float,
    min_games: int,
    compare_directional_hit: bool,
    l5_avg_atol: float,
) -> dict:
    out: dict = {
        "sport": src.sport,
        "path": src.path,
        "exists": False,
        "rows": 0,
        "rows_with_stat_g": 0,
        "rows_checked": 0,
        "mismatch_count": 0,
        "skipped_no_line": 0,
        "skipped_unsupported": 0,
        "skipped_insufficient_games": 0,
        "missing_stat_g_columns": True,
        "mismatch_csv_rows": [],
    }

    if not os.path.exists(src.path):
        out["note"] = "file_missing"
        return out

    df = _read_any(src.path, src.sheet)
    out["exists"] = True
    out["rows"] = int(len(df))
    if df.empty:
        return out

    df = _ensure_stat_g_from_g_labels(df)
    stat5 = _get_stat_cols(df, 5)
    if not stat5:
        return out
    out["missing_stat_g_columns"] = False
    out["rows_with_stat_g"] = int(len(df))

    line_col = _first_col(df, ("line", "Line"))
    if not line_col:
        out["note"] = "no_line_column"
        return out

    line_s = df[line_col]
    played, over, under, push, orp, urp, orou, urou = _compute_hits(df, stat5, line_s)

    line_num = pd.to_numeric(line_s, errors="coerce")
    sub5 = df[stat5].apply(pd.to_numeric, errors="coerce")
    n_stats = sub5.notna().sum(axis=1)
    l5_avg = sub5.mean(axis=1)

    unsup = _unsupported_mask(df)
    direction = _direction_series(df)
    total_ou = over + under
    hit_over_ou = over / total_ou.where(total_ou > 0)
    hit_under_ou = under / total_ou.where(total_ou > 0)
    hit_directional = hit_over_ou.where(direction.ne("UNDER"), hit_under_ou)

    period_hr = _period_hit_rate_style(src.sport)
    # "Hit Rate (5g)" / line_hit_rate_over_ou_5: full game = OU-ex-push; 1Q/1H Step 8 = over/played
    hr_over_display = orp if period_hr else orou
    hr_under_display = urp if period_hr else urou

    ok = (
        (~unsup)
        & line_num.notna()
        & (n_stats > 0)
        & (played >= min_games)
    )
    out["skipped_unsupported"] = int(unsup.sum())
    out["skipped_no_line"] = int((~unsup & line_num.isna()).sum())
    out["skipped_insufficient_games"] = int(
        (~unsup & line_num.notna() & (n_stats > 0) & (played < min_games)).sum()
    )
    out["rows_checked"] = int(ok.sum())

    checks: List[Tuple[str, pd.Series, Sequence[str], float]] = [
        ("line_games_played_5", played, ("line_games_played_5",), atol),
        ("line_hits_over_5", over, ("line_hits_over_5", "last5_over", "L5 Over", "l5_over"), atol),
        ("line_hits_under_5", under, ("line_hits_under_5", "last5_under", "L5 Under", "l5_under"), atol),
        ("line_hits_push_5", push, ("line_hits_push_5", "last5_push"), atol),
        (
            "line_hit_rate_over_ou_5",
            hr_over_display,
            ("line_hit_rate_over_ou_5", "Hit Rate (5g)"),
            atol,
        ),
        ("line_hit_rate_under_ou_5", hr_under_display, ("line_hit_rate_under_ou_5",), atol),
        ("line_hit_rate_over_5", orp, ("line_hit_rate_over_5",), atol),
        ("line_hit_rate_under_5", urp, ("line_hit_rate_under_5",), atol),
        ("l5_avg", l5_avg, ("stat_last5_avg", "Last 5 Avg", "l5_avg"), max(atol, l5_avg_atol)),
    ]

    if compare_directional_hit:
        checks.append(
            (
                "hit_rate_directional",
                hit_directional,
                ("directional_hit_rate", "pick_hit_rate"),
                atol,
            )
        )

    mismatch_mask = pd.Series(False, index=df.index)
    mismatch_fields: Dict[str, int] = {}

    for logical_name, computed, aliases, use_atol in checks:
        stored_col = _first_col(df, aliases)
        if not stored_col:
            continue
        stored = _numeric_series(df, stored_col)
        cmp_ok = _close(computed, stored, rtol=rtol, atol=use_atol)
        bad = ok & ~cmp_ok
        n_bad = int(bad.sum())
        if n_bad:
            mismatch_fields[f"{logical_name}!={stored_col}"] = n_bad
        mismatch_mask |= bad

    out["mismatch_count"] = int(mismatch_mask.sum())
    out["mismatch_by_field"] = mismatch_fields

    if mismatch_mask.any():
        id_cols = [
            _first_col(df, ("player", "Player", "player_name")),
            _first_col(df, ("prop_type", "Prop", "prop_norm", "stat_type")),
            _first_col(df, ("team", "Team", "team_abbr")),
        ]
        for idx in df.index[mismatch_mask]:
            row = {"row_index": int(idx) if isinstance(idx, (int, np.integer)) else str(idx)}
            for ic in id_cols:
                if ic:
                    row[ic] = df.at[idx, ic]
            row["line"] = df.at[idx, line_col]
            row["direction"] = direction.at[idx]
            for i, c in enumerate(stat5, start=1):
                row[c] = df.at[idx, c]
            row["cmp_played"] = float(played.at[idx])
            row["cmp_over"] = float(over.at[idx])
            row["cmp_under"] = float(under.at[idx])
            row["cmp_push"] = float(push.at[idx])
            row["cmp_line_hit_rate_display_over"] = (
                float(hr_over_display.at[idx]) if pd.notna(hr_over_display.at[idx]) else None
            )
            row["cmp_l5_avg"] = float(l5_avg.at[idx]) if pd.notna(l5_avg.at[idx]) else None
            row["cmp_hit_rate_dir"] = float(hit_directional.at[idx]) if pd.notna(hit_directional.at[idx]) else None
            for logical_name, computed, aliases, _use_atol in checks:
                stored_col = _first_col(df, aliases)
                if not stored_col:
                    continue
                row[f"stored__{stored_col}"] = df.at[idx, stored_col]
                row[f"cmp__{logical_name}"] = (
                    float(computed.at[idx]) if pd.notna(computed.at[idx]) else None
                )
            out["mismatch_csv_rows"].append(row)

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare L5 columns to stat_g1..stat_g5 + line.")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--repo-root", default="")
    ap.add_argument("--input", action="append", default=[], help="Extra file to check (repeatable).")
    ap.add_argument("--sheet", default="", help="Sheet name for --input Excel files (default: auto).")
    ap.add_argument("--sport", default="CUSTOM", help="Sport label for --input rows in the report.")
    ap.add_argument("--rtol", type=float, default=1e-5)
    ap.add_argument(
        "--atol",
        type=float,
        default=0.007,
        help="Absolute tolerance (hit rates in exports are often rounded to 2 decimals).",
    )
    ap.add_argument("--min-games", type=int, default=1, help="Only check rows with at least this many non-null stat_g values in g1..g5.")
    ap.add_argument(
        "--l5-avg-atol",
        type=float,
        default=0.11,
        help="Extra tolerance for Last 5 Avg vs mean(stat_g1..5) (exports often round to 1 decimal).",
    )
    ap.add_argument(
        "--compare-directional-hit",
        action="store_true",
        help="Compare direction-flipped OU%% to directional_hit_rate / pick_hit_rate only (not Hit Rate (5g)).",
    )
    ap.add_argument("--no-default-sources", action="store_true", help="Only process paths from --input.")
    args = ap.parse_args()

    repo_root = args.repo_root.strip() or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    d = args.date
    sources: List[SourceDef] = []
    if not args.no_default_sources:
        sources.extend(_default_sources(repo_root, d))
    for p in args.input:
        path = os.path.abspath(p)
        sheet = args.sheet.strip() or None
        sources.append(SourceDef(args.sport, path, sheet))

    results = []
    all_mismatch_rows: List[dict] = []

    for s in sources:
        r = evaluate_file(
            s,
            rtol=args.rtol,
            atol=args.atol,
            min_games=args.min_games,
            compare_directional_hit=args.compare_directional_hit,
            l5_avg_atol=args.l5_avg_atol,
        )
        results.append(r)
        status = "SKIP" if not r.get("exists") or r.get("missing_stat_g_columns") else (
            "OK" if r.get("mismatch_count", 0) == 0 else "MISMATCH"
        )
        print(f"[L5-G] {s.sport} {status} rows={r.get('rows', 0)} checked={r.get('rows_checked', 0)} "
              f"mismatches={r.get('mismatch_count', 0)} path={s.path}")
        for k, v in (r.get("mismatch_by_field") or {}).items():
            print(f"         - {k}: {v}")
        for row in r.get("mismatch_csv_rows", []):
            row["_sport"] = s.sport
            row["_path"] = s.path
            all_mismatch_rows.append(row)

    out_dir = os.path.join(repo_root, "outputs", d)
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, f"l5_stat_g_consistency_{d}.json")
    csv_path = os.path.join(out_dir, f"l5_stat_g_mismatches_{d}.csv")

    results_json = []
    for r in results:
        rj = {k: v for k, v in r.items() if k != "mismatch_csv_rows"}
        results_json.append(rj)

    payload = {
        "date": d,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "tolerance": {"rtol": args.rtol, "atol": args.atol, "min_games": args.min_games},
        "results": results_json,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)

    if all_mismatch_rows:
        pd.DataFrame(all_mismatch_rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame(columns=["_sport", "_path", "row_index"]).to_csv(csv_path, index=False, encoding="utf-8-sig")

    print(f"[L5-G] JSON -> {json_path}")
    print(f"[L5-G] mismatches CSV -> {csv_path}")

    any_checked = any(r.get("rows_checked", 0) for r in results)
    any_mismatch = any(r.get("mismatch_count", 0) for r in results)
    if any_mismatch:
        return 3
    if not any_checked and sources:
        print("[L5-G] No rows checked (missing stat_g columns or no valid line/stat rows).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
