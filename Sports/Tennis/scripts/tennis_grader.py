#!/usr/bin/env python3
"""
tennis_grader.py — Grade tennis props from ESPN ATP/WTA completed matches.

Reads slate (step8 CSV or XLSX), matches player + prop to scoreboard stats,
writes graded_tennis_{date}.xlsx.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
# Sports/Tennis/scripts -> monorepo root.
_REPO_ROOT = _SCRIPT_DIR.parents[3]
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from tennis_shared import iter_scoreboard_matches, norm_key, norm_tennis_prop

VALID_TENNIS_PROPS = {"aces", "double_faults", "games_won", "sets_won", "match_total_games"}


def _actual_key(prop_norm: str) -> str | None:
    m = {
        "aces": "aces",
        "double_faults": "double_faults",
        "games_won": "games_won",
        "sets_won": "sets_won",
        "match_total_games": "match_total_games",
    }
    return m.get(prop_norm)


def _load_slate(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    if path.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(path, sheet_name="ALL", engine="openpyxl", dtype=str).fillna("")
    return pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")


def _grade(direction: str, line: float, actual: float | None) -> tuple[str, str, str]:
    """
    Returns (result, notes, void_reason_code).

    ``void_reason_code`` is empty for HIT/MISS; for VOID it is aligned with
    ``validate_unacceptable_voids.py`` defaults (NO_DATA / DNP) where applicable.
    """
    if actual is None:
        return "VOID", "NO_MATCH_OR_INCOMPLETE", "NO_DATA"
    d = direction.strip().upper()
    if d == "OVER":
        return ("HIT", "", "") if actual >= line else ("MISS", "", "")
    if d == "UNDER":
        return ("HIT", "", "") if actual < line else ("MISS", "", "")
    return "VOID", "NO_DIRECTION", "NO_DIRECTION"


def _filter_slate_for_grade_date(slate: pd.DataFrame, target: str) -> pd.DataFrame:
    """Keep rows whose slate game time calendar day matches ``target`` (YYYY-MM-DD) when possible."""
    for col in ("start_time", "game_time", "game_datetime", "game_date", "slate_date"):
        if col not in slate.columns:
            continue
        s = slate[col].astype(str).str.strip()
        m = s.str.slice(0, 10) == target
        if bool(m.any()):
            return slate.loc[m].copy()
    return slate


_DEF_GRADED_COLS = [
    "player",
    "prop_type",
    "prop_norm",
    "line",
    "direction",
    "actual",
    "result",
    "reason",
    "notes",
]


def _empty_graded() -> pd.DataFrame:
    return pd.DataFrame(columns=_DEF_GRADED_COLS)


def _parse_iso_date(value: str) -> date | None:
    s = str(value).strip()[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _step8_bundle_date(target: str) -> str:
    """Pipeline bundle folder for match day ``target`` (props fetched on target - 1)."""
    d = _parse_iso_date(target)
    if d is None:
        return target
    return (d - timedelta(days=1)).isoformat()


def _bundle_step8_candidates(bundle_root: Path, match_date: str, bundle_date: str) -> list[Path]:
    """Step8 paths under one ``outputs/<bundle_date>/`` folder."""
    tennis_dir = bundle_root / "tennis"
    out: list[Path] = [
        tennis_dir / "step8_tennis_direction_clean.xlsx",
        tennis_dir / "step8_tennis_direction.csv",
        bundle_root / f"step8_tennis_direction_clean_{match_date}.xlsx",
        bundle_root / f"step8_tennis_direction_clean_{bundle_date}.xlsx",
    ]
    if tennis_dir.is_dir():
        out.extend(sorted(tennis_dir.glob("step8_*.csv")))
        out.extend(sorted(tennis_dir.glob("step8_*.xlsx")))
    return out


def _default_slate_candidates(target: str) -> list[Path]:
    offset_date = _step8_bundle_date(target)
    cands: list[Path] = []
    for bundle in (offset_date, target):
        root = _REPO_ROOT / "outputs" / bundle
        cands.extend(_bundle_step8_candidates(root, target, bundle))
    cands.extend(
        [
            _REPO_ROOT / "Tennis" / "outputs" / "step8_tennis_direction_clean.xlsx",
            _REPO_ROOT / "Tennis" / "outputs" / "step8_tennis_direction.csv",
            _REPO_ROOT / "Sports" / "Tennis" / "outputs" / "step8_tennis_direction_clean.xlsx",
            _REPO_ROOT / "Sports" / "Tennis" / "outputs" / "step8_tennis_direction.csv",
        ]
    )
    seen: set[Path] = set()
    ordered: list[Path] = []
    for p in cands:
        if p in seen:
            continue
        seen.add(p)
        ordered.append(p)
    return ordered


def _resolve_step8_slate(target: str) -> tuple[Path | None, str | None, bool]:
    """Return (path, bundle_date_used, used_prior_day_bundle)."""
    offset_date = _step8_bundle_date(target)
    for p in _default_slate_candidates(target):
        if not p.is_file():
            continue
        try:
            rel = p.resolve().relative_to((_REPO_ROOT / "outputs").resolve())
            bundle_used = rel.parts[0] if rel.parts else None
        except ValueError:
            bundle_used = None
        used_offset = bundle_used == offset_date
        return p, bundle_used, used_offset
    return None, None, False


def _slate_field(row: pd.Series, *keys: str) -> str:
    for key in keys:
        if key not in row.index:
            continue
        val = row.get(key)
        if val is None:
            continue
        s = str(val).strip()
        if s and s.lower() not in ("nan", "none", "null"):
            return s
    return ""


def main() -> None:
    print("[Tennis grader] Starting...")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="Slate date YYYY-MM-DD (UTC date on ESPN match)")
    ap.add_argument("--output", default="", help="Output graded .xlsx path")
    ap.add_argument("--slate", default="", help="step8 CSV or XLSX (default: Tennis outputs)")
    args = ap.parse_args()

    target = str(args.date).strip()[:10]
    out = Path(args.output) if str(args.output).strip() else _REPO_ROOT / "outputs" / target / f"graded_tennis_{target}.xlsx"
    if not out.is_absolute():
        out = _REPO_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)

    default_cands: list[Path] = []
    if str(args.slate).strip():
        slate_path = Path(str(args.slate).strip())
        if not slate_path.is_absolute():
            slate_path = _REPO_ROOT / slate_path
    else:
        offset_date = _step8_bundle_date(target)
        default_cands = _default_slate_candidates(target)
        resolved, bundle_used, used_offset = _resolve_step8_slate(target)
        slate_path = resolved if resolved is not None else Path()
        if slate_path.is_file():
            if used_offset:
                print(
                    f"Tennis: using step8 from {bundle_used or offset_date} "
                    f"(tomorrow-fetch offset)"
                )
            elif bundle_used == target:
                print(
                    f"Tennis: no X-1 step8 found, falling back to grade date "
                    f"({target})"
                )

    if not slate_path.is_file():
        print("[Tennis grader] ERROR: no tennis step8 slate file found.")
        if default_cands:
            print("  Default search paths:")
            for p in default_cands:
                print(f"    - {p}")
        else:
            print(f"  --slate does not exist: {slate_path}")
        _empty_graded().to_excel(out, sheet_name="graded", index=False)
        sys.exit(1)

    slate = _load_slate(slate_path)
    if slate.empty:
        print(f"[Tennis grader] ERROR: slate file has no rows: {slate_path}")
        _empty_graded().to_excel(out, sheet_name="graded", index=False)
        sys.exit(1)

    # Normalize slate columns
    colmap = {
        "Player": "player",
        "Prop": "prop_type",
        "Line": "line",
        "Direction": "direction",
        "final_bet_direction": "direction",
    }
    for a, b in colmap.items():
        if a in slate.columns and b not in slate.columns:
            slate[b] = slate[a]

    n_slate = len(slate)
    slate_f = _filter_slate_for_grade_date(slate, target)
    if slate_f.empty and n_slate:
        print(
            f"[Tennis grader] WARN: no slate rows matched --date {target} on start_time/game_date; "
            "grading full slate."
        )
        slate_f = slate
    elif len(slate_f) < n_slate:
        print(f"[Tennis grader] Date filter {target}: {len(slate_f)}/{n_slate} slate rows")
    slate = slate_f

    by_player_day: dict[str, dict[str, float]] = {}
    for tour in ("ATP", "WTA"):
        for m in iter_scoreboard_matches(tour):
            dt = str(m.get("match_date_utc") or "")[:10]
            if dt != target:
                continue
            pk = norm_key(str(m.get("player") or ""))
            if not pk:
                continue
            by_player_day[pk] = {
                "aces": float(m.get("aces") or 0),
                "double_faults": float(m.get("double_faults") or 0),
                "games_won": float(m.get("games_won") or 0),
                "sets_won": float(m.get("sets_won") or 0),
                "match_total_games": float(m.get("match_total_games") or 0),
            }

    rows: list[dict[str, object]] = []
    skipped_non_tennis = 0
    for _, r in slate.iterrows():
        player = str(r.get("player", "")).strip()
        pk = norm_key(player)
        prop_raw = str(r.get("prop_type", "")).strip()
        pnorm = norm_tennis_prop(prop_raw)
        if pnorm not in VALID_TENNIS_PROPS:
            skipped_non_tennis += 1
            continue
        ak = _actual_key(pnorm)
        direction = str(r.get("direction", r.get("final_bet_direction", ""))).strip()
        try:
            line = float(r.get("line", "") or r.get("Line", ""))
        except (TypeError, ValueError):
            line = float("nan")
        stats = by_player_day.get(pk) if pk else None
        actual = None
        if stats is not None and ak:
            actual = stats.get(ak)
        res, note, void_reason = _grade(direction, line, actual)
        note_out = note or ("" if pk in by_player_day else "PLAYER_OR_DATE_NOT_FOUND")
        rows.append(
            {
                "player": player,
                "prop_type": prop_raw,
                "prop_norm": pnorm,
                "line": line,
                "direction": direction,
                "actual": actual if actual is not None else "",
                "result": res,
                "reason": void_reason if res == "VOID" else "",
                "notes": note_out,
                "ml_prob": _slate_field(r, "ml_prob", "ML Prob"),
                "tier": _slate_field(r, "tier", "Tier"),
                "edge": _slate_field(r, "edge", "Edge"),
                "pick_type": _slate_field(r, "pick_type", "Pick Type"),
                "deviation_level": _slate_field(r, "deviation_level", "Deviation Level"),
                "team": _slate_field(r, "team", "Team"),
                "blended_score": _slate_field(r, "blended_score", "Blended Score"),
            }
        )

    df = pd.DataFrame(rows) if rows else _empty_graded()
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="graded", index=False)
        if not df.empty:
            df.to_excel(w, sheet_name="Box Raw", index=False)
    if skipped_non_tennis:
        print(f"[Tennis grader] Skipped non-tennis props: {skipped_non_tennis}")
    print(f"[Tennis grader] Saved -> {out}  rows={len(df)}")


if __name__ == "__main__":
    main()
