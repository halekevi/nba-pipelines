#!/usr/bin/env python3
"""
find_calibration_candidates.py

Prints candidate legs for manual payout ladder calibration.
Run after pipeline, before opening PrizePicks.

Usage:
  py -3.14 scripts\\find_calibration_candidates.py
  py -3.14 scripts\\find_calibration_candidates.py --date 2026-04-13
  py -3.14 scripts\\find_calibration_candidates.py --sport NBA
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
PAYOUT_SAMPLES = REPO_ROOT / "data" / "payout_samples"
HAND_LOG = PAYOUT_SAMPLES / "payout_log_hand.csv"
HAND_HEADER = (
    "date,entry_type,n_legs,n_goblin,n_standard,n_demon,goblin_distances_sorted,"
    "flex_sweep_x,flex_partial_x,power_first_x,power_min_x,notes\n"
)


@dataclass
class SportPaths:
    key: str
    label: str
    csv_paths: tuple[Path, ...]


def _sport_catalog() -> list[SportPaths]:
    """Static fallbacks when outputs/{date}/ has no step8 CSV."""
    return [
        SportPaths(
            "NBA",
            "NBA",
            (REPO_ROOT / "Sports" / "NBA" / "data" / "outputs" / "step8_all_direction.csv",),
        ),
        SportPaths(
            "NHL",
            "NHL",
            (REPO_ROOT / "Sports" / "NHL" / "outputs" / "step8_nhl_direction_clean.csv",),
        ),
        SportPaths(
            "SOCCER",
            "Soccer",
            (
                REPO_ROOT / "Sports" / "Soccer" / "step8_soccer_direction.csv",
                REPO_ROOT / "Sports" / "Soccer" / "scripts" / "step8_soccer_direction.csv",
            ),
        ),
        SportPaths(
            "MLB",
            "MLB",
            (REPO_ROOT / "Sports" / "MLB" / "step8_mlb_direction.csv",),
        ),
    ]


def _outputs_date_csvs(when: str) -> list[tuple[str, Path]]:
    """Try outputs/{date}/*.csv for step8-like files; returns (sport_guess, path)."""
    root = REPO_ROOT / "outputs" / when
    if not root.is_dir():
        return []
    out: list[tuple[str, Path]] = []
    for p in sorted(root.glob("*.csv")):
        name = p.name.lower()
        if "step8" not in name:
            continue
        guess = "UNK"
        for token, lab in (
            ("nba", "NBA"),
            ("nhl", "NHL"),
            ("soccer", "SOCCER"),
            ("mlb", "MLB"),
        ):
            if token in name:
                guess = lab
                break
        out.append((guess, p))
    return out


def _lower_row(row: dict[str, Any]) -> dict[str, Any]:
    return {str(k).lower(): v for k, v in row.items()}


def _get(row: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in row and row[k] is not None and str(row[k]).strip() != "":
            return row[k]
    return None


def _parse_float(x: Any) -> float | None:
    if x is None:
        return None
    s = str(x).strip()
    if s == "" or s.lower() in {"nan", "none", "null"}:
        return None
    try:
        v = float(s)
        if not math.isfinite(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def _parse_hit_rate(row: dict[str, Any]) -> float | None:
    for key in (
        "composite_hit_rate",
        "line_hit_rate",
        "hit_rate",
        "composite_hr",
        "last5_hit_rate",
        "line_hit_rate_over_5",
        "line_hit_rate_over_ou_5",
    ):
        v = _parse_float(_get(row, key))
        if v is None:
            continue
        if v > 1.0 + 1e-6:
            v = v / 100.0
        return max(0.0, min(1.0, v))
    return None


def _tier_ok(row: dict[str, Any]) -> bool:
    t = _get(row, "tier", "pp_tier")
    if t is None:
        return False
    return str(t).strip().upper() in {"A", "B", "C"}


def _pick_norm(row: dict[str, Any]) -> str:
    return str(_get(row, "pick_type", "picktype") or "").strip().lower()


def _line_val(row: dict[str, Any]) -> float | None:
    return _parse_float(_get(row, "line_score", "line"))


def _std_line(row: dict[str, Any]) -> float | None:
    return _parse_float(_get(row, "standard_line", "std_line"))


def _player_name(row: dict[str, Any]) -> str:
    return str(_get(row, "player_name", "player") or "").strip() or "?"


def _prop_name(row: dict[str, Any]) -> str:
    return str(_get(row, "prop_type", "stat_type", "prop_display", "prop_norm") or "").strip() or "?"


def _proj_ids(row: dict[str, Any]) -> tuple[str, str]:
    pp = str(_get(row, "pp_projection_id", "projection_id") or "").strip()
    pid = str(_get(row, "projection_id") or "").strip()
    return pp, pid


def _edge_str(row: dict[str, Any]) -> str:
    for k in ("edge", "abs_edge", "edge_score", "blended_score"):
        v = _parse_float(_get(row, k))
        if v is not None:
            return f"{v:.2f}"
    return "-"


def _load_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            rdr = csv.DictReader(f)
            for raw in rdr:
                rows.append(_lower_row(raw))
    except OSError as e:
        print(f"[warn] could not read {path}: {e}", file=sys.stderr)
    return rows


def _filter_base(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if not _tier_ok(row):
            continue
        hr = _parse_hit_rate(row)
        if hr is None or hr < 0.70:
            continue
        pt = _pick_norm(row)
        if pt not in {"goblin", "standard"}:
            continue
        if _line_val(row) is None:
            continue
        out.append(row)
    return out


def _goblin_with_distance(rows: list[dict[str, Any]]) -> list[tuple[dict[str, Any], float]]:
    scored: list[tuple[dict[str, Any], float]] = []
    for row in rows:
        if _pick_norm(row) != "goblin":
            continue
        std = _std_line(row)
        ln = _line_val(row)
        if std is None:
            continue
        if abs(std) < 1e-9:
            continue
        dist = abs(std - ln)
        if not math.isfinite(dist):
            continue
        scored.append((row, float(dist)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def _standard_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    st = [r for r in rows if _pick_norm(r) == "standard"]
    st.sort(key=lambda r: (_parse_hit_rate(r) or 0.0), reverse=True)
    return st


def _fmt_pct(x: float | None) -> str:
    if x is None:
        return "-"
    return f"{100.0 * x:.0f}%"


def _fmt_row_table(
    sport: str,
    kind: str,
    entries: list[tuple[dict[str, Any], float | None]],
) -> None:
    if kind == "goblin":
        hdr = f"{'Sport':<6} {'Player':<22} {'Prop':<18} {'Line':>5} {'Std':>5} {'Dist':>5} {'HR':>5} {'Tier':>4}"
        print(hdr)
        print("-" * len(hdr))
        for row, dist in entries:
            ln = _line_val(row) or 0.0
            std = _std_line(row) or 0.0
            hr = _parse_hit_rate(row)
            tier = str(_get(row, "tier", "pp_tier") or "?").strip()[:4]
            print(
                f"{sport:<6} {_player_name(row)[:22]:<22} {_prop_name(row)[:18]:<18} "
                f"{ln:>5.1f} {std:>5.1f} {(dist or 0.0):>5.1f} {_fmt_pct(hr):>5} {tier:>4}"
            )
    else:
        hdr = f"{'Sport':<6} {'Player':<22} {'Prop':<18} {'Line':>5} {'HR':>5} {'Tier':>4} {'Edge':>6}"
        print(hdr)
        print("-" * len(hdr))
        for row, _ in entries:
            ln = _line_val(row) or 0.0
            hr = _parse_hit_rate(row)
            tier = str(_get(row, "tier", "pp_tier") or "?").strip()[:4]
            print(
                f"{sport:<6} {_player_name(row)[:22]:<22} {_prop_name(row)[:18]:<18} "
                f"{ln:>5.1f} {_fmt_pct(hr):>5} {tier:>4} {_edge_str(row):>6}"
            )


def _ensure_hand_log() -> None:
    PAYOUT_SAMPLES.mkdir(parents=True, exist_ok=True)
    if not HAND_LOG.is_file():
        HAND_LOG.write_text(HAND_HEADER, encoding="utf-8")
        print(f"[ok] created {HAND_LOG}")


def _load_sport_tables(when: str, sport_filter: str | None) -> dict[str, list[dict[str, Any]]]:
    """sport_key -> rows (lowercased keys)."""
    tables: dict[str, list[dict[str, Any]]] = {}
    wanted = None if not sport_filter else sport_filter.strip().upper()

    dated = _outputs_date_csvs(when)
    if dated:
        for guess, path in dated:
            if wanted and guess.upper() not in (wanted, "UNK"):
                continue
            key = guess if guess != "UNK" else "DATED"
            merged = tables.get(key, []) + _load_csv(path)
            tables[key] = merged
        if tables:
            return tables

    for sp in _sport_catalog():
        if wanted and sp.key != wanted:
            continue
        merged: list[dict[str, Any]] = []
        for p in sp.csv_paths:
            merged.extend(_load_csv(p))
        if merged:
            tables[sp.key] = merged
        else:
            print(f"[warn] no rows loaded for {sp.label}: tried {sp.csv_paths[0]}", file=sys.stderr)
    return tables


def _annotate_sport(sport: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for r in rows:
        r["_sport"] = sport
    return rows


def _rows_for_sport(base: list[dict[str, Any]], sport: str) -> list[dict[str, Any]]:
    su = sport.strip().upper()
    return [r for r in base if str(r.get("_sport", "")).strip().upper() == su]


def _pick_slip_sport_1g2s(base: list[dict[str, Any]], sport_filter: str) -> str | None:
    """One PrizePicks board: goblin + 2 standards must share a league."""
    order = ("NBA", "MLB", "NHL", "SOCCER")
    if sport_filter.strip().upper() != "ALL":
        sk = sport_filter.strip().upper()
        rs = _rows_for_sport(base, sk)
        g = _goblin_with_distance(rs)
        st = _standard_rows(rs)
        return sk if (g and len(st) >= 2) else None
    for sk in order:
        rs = _rows_for_sport(base, sk)
        if not rs:
            continue
        if _goblin_with_distance(rs) and len(_standard_rows(rs)) >= 2:
            return sk
    return None


def _pick_slip_sport_3g(base: list[dict[str, Any]], sport_filter: str, prefer: str | None) -> str | None:
    """Three goblins on one board."""
    order: tuple[str, ...]
    if sport_filter.strip().upper() != "ALL":
        order = (sport_filter.strip().upper(),)
    else:
        order = ()
        if prefer:
            order = (prefer,) + tuple(s for s in ("NBA", "MLB", "NHL", "SOCCER") if s != prefer)
        else:
            order = ("NBA", "MLB", "NHL", "SOCCER")
    for sk in order:
        rs = _rows_for_sport(base, sk)
        if len(_goblin_with_distance(rs)) >= 3:
            return sk
    return None


def _print_slip_leg(title: str, row: dict[str, Any], dist_note: str | None) -> None:
    sp = str(row.get("_sport", "?"))
    pp, pid = _proj_ids(row)
    dist_s = f"  dist={dist_note}" if dist_note is not None else ""
    print(f"    {title}: {_player_name(row)} ({sp}) — {_prop_name(row)} line={_line_val(row)}{dist_s}")
    print(f"      pp_projection_id={pp or '-'}  projection_id={pid or '-'}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Find step8 legs for payout ladder calibration.")
    ap.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Slate date YYYY-MM-DD (used for outputs/{date}/ and log template)",
    )
    ap.add_argument(
        "--sport",
        default="ALL",
        help="NBA | NHL | SOCCER | MLB | ALL",
    )
    args = ap.parse_args()
    when = str(args.date).strip()
    sport_arg = str(args.sport).strip().upper()

    _ensure_hand_log()

    raw_tables = _load_sport_tables(when, None if sport_arg == "ALL" else sport_arg)
    if not raw_tables:
        print("[error] no step8 data found for any sport. Run pipeline or fix paths.", file=sys.stderr)
        return 1

    all_rows: list[dict[str, Any]] = []
    for sk, tbl in raw_tables.items():
        if sk == "DATED" and sport_arg != "ALL":
            continue
        all_rows.extend(_annotate_sport(sk, tbl))

    base = _filter_base(all_rows)
    if not base:
        print("[error] no rows passed tier A/B/C + hit_rate>=0.70 + Goblin/Standard.", file=sys.stderr)
        return 1

    # Per-sport top lists for display
    print()
    print("=== GOBLIN CANDIDATES (known distance from standard) ===")
    any_goblin = False
    for sk in sorted(set(r.get("_sport", "") for r in base)):
        sport_rows = [r for r in base if r.get("_sport") == sk]
        scored = _goblin_with_distance(sport_rows)[:5]
        if not scored:
            continue
        any_goblin = True
        print()
        print(f"--- {sk} (top {len(scored)} by distance) ---")
        _fmt_row_table(sk, "goblin", scored)
    if not any_goblin:
        print("(none: no goblin rows with numeric standard_line and line)")

    print()
    print("=== STANDARD CANDIDATES ===")
    for sk in sorted(set(r.get("_sport", "") for r in base)):
        sport_rows = [r for r in base if r.get("_sport") == sk]
        tops = [(r, _parse_hit_rate(r)) for r in _standard_rows(sport_rows)[:5]]
        if not tops:
            continue
        print()
        print(f"--- {sk} (top {len(tops)} by hit rate) ---")
        _fmt_row_table(sk, "standard", tops)

    slip_sport = _pick_slip_sport_1g2s(base, sport_arg)
    if slip_sport is None:
        print(
            "[warn] No league has both ≥1 goblin (numeric standard_line) and ≥2 standard legs "
            "with tier A/B/C and hit_rate≥0.70. Relax filters or refresh step8.",
            file=sys.stderr,
        )
    pool = _rows_for_sport(base, slip_sport) if slip_sport else []
    scored_all = _goblin_with_distance(pool)
    standards_all = _standard_rows(pool)

    g1 = scored_all[0][0] if scored_all else None
    g1_dist = f"{scored_all[0][1]:.1f}" if scored_all else None
    s1 = standards_all[0] if len(standards_all) > 0 else None
    s2 = standards_all[1] if len(standards_all) > 1 else None
    if s1 is not None and s2 is not None and _player_name(s1) == _player_name(s2):
        s2 = standards_all[2] if len(standards_all) > 2 else None

    slip2_sport = _pick_slip_sport_3g(base, sport_arg, slip_sport)
    pool_g3 = _rows_for_sport(base, slip2_sport) if slip2_sport else []
    scored_three = _goblin_with_distance(pool_g3)

    print()
    print("=== SUGGESTED TEST SLIPS ===")
    if slip_sport:
        print(f"(All legs below are **{slip_sport}** — one PrizePicks board.)")
    if g1 and s1 and s2:
        print()
        print("Slip 1 (Flex 3-Leg: 1 goblin + 2 standard)")
        _print_slip_leg("Goblin", g1, g1_dist)
        _print_slip_leg("Standard", s1, None)
        _print_slip_leg("Standard", s2, None)
        print("    → Record: flex_sweep_x, flex_partial_x on PrizePicks")
    else:
        print()
        print("Slip 1 (Flex 3-Leg: 1 goblin + 2 standard) — insufficient legs in pool.")

    if slip2_sport and len(scored_three) >= 3:
        hi, mid, lo = scored_three[0], scored_three[len(scored_three) // 2], scored_three[-1]
        print()
        print(f"Slip 2 (Flex 3-Leg: all goblin, mixed distances) — **{slip2_sport}**")
        _print_slip_leg("Goblin", hi[0], f"{hi[1]:.1f}")
        _print_slip_leg("Goblin", mid[0], f"{mid[1]:.1f}")
        _print_slip_leg("Goblin", lo[0], f"{lo[1]:.1f}")
        print("    → Record: flex_sweep_x, flex_partial_x")
    else:
        print()
        print(
            "Slip 2 (Flex 3-Leg: all goblin, mixed distances) — "
            "need ≥3 goblins with standard_line on one league's step8."
        )

    if g1 and s1 and s2:
        print()
        print("Slip 3 (Power 3-Leg: 1 goblin + 2 standard) — same legs as Slip 1")
        _print_slip_leg("Goblin", g1, g1_dist)
        _print_slip_leg("Standard", s1, None)
        _print_slip_leg("Standard", s2, None)
        print("    → Record: power_first_x, power_min_x (if shown)")
    else:
        print()
        print("Slip 3 (Power 3-Leg) — skipped (same dependency as Slip 1).")

    d_slip2 = ""
    if slip2_sport and len(scored_three) >= 3:
        dists = sorted(
            [
                round(scored_three[0][1], 2),
                round(scored_three[len(scored_three) // 2][1], 2),
                round(scored_three[-1][1], 2),
            ]
        )
        d_slip2 = "+".join(str(x) for x in dists)
    gdist_slip1 = (g1_dist or "") if g1 else ""

    print()
    print("=== LOG TEMPLATE (copy to payout_log_hand.csv) ===")
    print(HAND_HEADER.strip())
    print(
        f"{when},flex,3,1,2,0,{gdist_slip1},,,,slip1-flex-1g2s"
    )
    print(f"{when},flex,3,3,0,0,{d_slip2},,,,slip2-flex-3g-mixed-dist")
    print(f"{when},power,3,1,2,0,{gdist_slip1},,,,slip3-power-1g2s")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
