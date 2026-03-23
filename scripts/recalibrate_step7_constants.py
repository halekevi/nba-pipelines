#!/usr/bin/env python3
"""
Recalibrate step-rank constants from recent graded outcomes.

Default behavior is safe/read-only:
  - Computes recommended updates from last N days of graded workbooks
  - Writes JSON + Markdown report under outputs/recalibration/
  - Prints a concise summary

Optional:
  --apply  Apply recommendations directly to target rank scripts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ensure_local_cache import ensure_local_cache  # noqa: E402

ensure_local_cache(str(REPO_ROOT))
import build_player_consistency as bpc  # noqa: E402


@dataclass
class TargetSpec:
    sport: str
    file_path: Path
    weight_const: str | None
    over_prior_const: str | None
    under_const: str | None


TARGETS: dict[str, TargetSpec] = {
    "NBA": TargetSpec(
        sport="NBA",
        file_path=REPO_ROOT / "NBA" / "scripts" / "step7_rank_props.py",
        weight_const="_PROP_WEIGHTS",
        over_prior_const="_PROP_HR_PRIOR_OVER",
        under_const="_PROP_HR_PRIOR_UNDER_OVERRIDE",
    ),
    "CBB": TargetSpec(
        sport="CBB",
        file_path=REPO_ROOT / "CBB" / "scripts" / "pipeline" / "step6_rank_props_cbb.py",
        weight_const="_PROP_WEIGHTS",
        over_prior_const="_PROP_HIT_RATE_PRIOR",
        under_const=None,
    ),
    "NHL": TargetSpec(
        sport="NHL",
        file_path=REPO_ROOT / "NHL" / "scripts" / "step7_rank_props_nhl.py",
        weight_const="STAT_STABILITY",
        over_prior_const=None,
        under_const=None,
    ),
    "Soccer": TargetSpec(
        sport="Soccer",
        file_path=REPO_ROOT / "Soccer" / "scripts" / "step7_rank_props_soccer.py",
        weight_const="_PROP_WEIGHTS",
        over_prior_const="_PROP_HIT_RATE_PRIOR",
        under_const=None,
    ),
}


def _today() -> str:
    return dt.date.today().isoformat()


def _norm_col(df: pd.DataFrame) -> dict[str, str]:
    return {str(c).strip().lower().replace(" ", "_"): c for c in df.columns}


def _col(nc: dict[str, str], *names: str) -> str | None:
    for n in names:
        k = n.strip().lower().replace(" ", "_")
        if k in nc:
            return nc[k]
    return None


def _parse_date(v: Any) -> pd.Timestamp | None:
    return bpc._parse_date(v)


def _parse_result(v: Any) -> int | None:
    return bpc._parse_result(v)


def _date_from_filename(path: Path) -> pd.Timestamp | None:
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", path.name)
    if m:
        try:
            return pd.Timestamp(m.group(1))
        except Exception:
            return None
    m2 = re.search(r"(20\d{6})", path.name)
    if m2:
        try:
            return pd.to_datetime(m2.group(1), format="%Y%m%d", errors="coerce")
        except Exception:
            return None
    return None


def _to_prop_norm(prop: str) -> str:
    x = re.sub(r"[^a-z0-9]+", "", str(prop).lower())
    alias = {
        "points": "pts",
        "rebounds": "reb",
        "assists": "ast",
        "steals": "stl",
        "blocks": "blk",
        "turnovers": "tov",
        "fantasyscore": "fantasy",
        "ptsa sts": "pa",
        "ptsasts": "pa",
        "ptsrebs": "pr",
        "rebsasts": "ra",
        "threes": "fg3m",
        "3ptmade": "fg3m",
        "3ptattempted": "fg3a",
        "fieldgoalsattempted": "fga",
        "fieldgoalsmade": "fgm",
        "freethrowsattempted": "fta",
        "freethrowsmade": "ftm",
        "personalfouls": "pf",
        "shotsongoal": "shots_on_goal",
        "blockedshots": "blocked_shots",
        "goalsallowed": "goals_allowed",
        "fantasy": "fantasy_score",
        "fantasypoints": "fantasy_score",
    }
    return alias.get(x, x)


def _load_grades(
    sport: str,
    days: int,
    excluded_props: set[str] | None = None,
    min_date: pd.Timestamp | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    out_dir = REPO_ROOT / "outputs"
    if not out_dir.is_dir():
        return pd.DataFrame()

    start = pd.Timestamp(_today()) - pd.Timedelta(days=int(days))
    if min_date is not None:
        start = max(start.normalize(), pd.Timestamp(min_date).normalize())
    pattern = dict(bpc.GRADED_GLOBS).get(sport)
    if not pattern:
        return pd.DataFrame()

    for path in out_dir.rglob(pattern):
        df = bpc._read_graded_frame(path)
        if df is None or df.empty:
            continue
        file_date = _date_from_filename(path)
        if file_date is not None and file_date.normalize() < start.normalize():
            continue
        nc = _norm_col(df)
        c_prop = _col(nc, "prop_type", "stat_type", "prop type")
        c_dir = _col(nc, "direction")
        c_res = _col(nc, "result")
        c_date = _col(nc, "date", "created_at")
        if not all([c_prop, c_dir, c_res]):
            continue
        for _, r in df.iterrows():
            hit = _parse_result(r.get(c_res))
            if hit is None:
                continue
            d = _parse_date(r.get(c_date)) if c_date else None
            if d is None:
                d = file_date
            if d is not None and d.normalize() < start.normalize():
                continue
            direction = str(r.get(c_dir) or "").strip().upper()
            if direction not in ("OVER", "UNDER"):
                continue
            prop = bpc._normalize_prop_type(str(r.get(c_prop) or ""), sport)
            prop_norm = _to_prop_norm(prop)
            if not prop_norm:
                continue
            if excluded_props and prop_norm in excluded_props:
                continue
            rows.append(
                {
                    "sport": sport,
                    "prop_norm": prop_norm,
                    "direction": direction,
                    "hit": int(hit),
                }
            )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _shrink_rate(hit_rate: float, n: int, base: float = 0.50, k: int = 20) -> float:
    if n <= 0:
        return base
    return ((hit_rate * n) + (base * k)) / (n + k)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _extract_const_block(text: str, const_name: str) -> tuple[int, int, str] | None:
    pat = re.compile(rf"(?ms)^({re.escape(const_name)}\s*=\s*\{{.*?^\}})")
    m = pat.search(text)
    if not m:
        return None
    return m.start(1), m.end(1), m.group(1)


def _extract_mapping(block: str) -> tuple[list[str], dict[str, float]]:
    order: list[str] = []
    vals: dict[str, float] = {}
    for m in re.finditer(r'^\s*"([^"]+)"\s*:\s*([-+]?\d+(?:\.\d+)?)', block, flags=re.M):
        k = m.group(1).strip()
        v = float(m.group(2))
        order.append(k)
        vals[k] = v
    return order, vals


def _format_mapping_block(const_name: str, order: list[str], values: dict[str, float]) -> str:
    seen = set()
    keys = [k for k in order if k in values]
    for k in keys:
        seen.add(k)
    for k in sorted(values.keys()):
        if k not in seen:
            keys.append(k)
    lines = [f"{const_name} = {{"]
    for k in keys:
        lines.append(f'    "{k}": {values[k]:.3f},')
    lines.append("}")
    return "\n".join(lines)


def _recommend(df: pd.DataFrame, min_count: int) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """
    Returns:
      weights_by_prop, over_prior_by_prop, under_prior_override_by_prop
    """
    if df.empty:
        return {}, {}, {}, {}, {}, {}

    agg_prop = (
        df.groupby("prop_norm", dropna=False)["hit"]
        .agg(n="count", hr="mean")
        .reset_index()
    )
    agg_dir = (
        df.groupby(["prop_norm", "direction"], dropna=False)["hit"]
        .agg(n="count", hr="mean")
        .reset_index()
    )

    rec_w: dict[str, float] = {}
    rec_over: dict[str, float] = {}
    rec_under: dict[str, float] = {}
    n_weight: dict[str, int] = {}
    n_over: dict[str, int] = {}
    n_under: dict[str, int] = {}

    for _, r in agg_prop.iterrows():
        n = int(r["n"])
        if n < min_count:
            continue
        prop = str(r["prop_norm"])
        hr = float(r["hr"])
        shr = _shrink_rate(hr, n, base=0.50, k=30)
        # Translate hit-rate advantage into moderate weight adjustment.
        w = 1.0 + (shr - 0.50) * 1.6
        rec_w[prop] = round(_clamp(w, 0.80, 1.20), 3)
        n_weight[prop] = n

    over_map: dict[str, tuple[int, float]] = {}
    under_map: dict[str, tuple[int, float]] = {}
    for _, r in agg_dir.iterrows():
        prop = str(r["prop_norm"])
        d = str(r["direction"]).upper()
        n = int(r["n"])
        hr = float(r["hr"])
        if d == "OVER":
            over_map[prop] = (n, hr)
        elif d == "UNDER":
            under_map[prop] = (n, hr)

    props = sorted(set(over_map.keys()) | set(under_map.keys()))
    for prop in props:
        if prop in over_map:
            n, hr = over_map[prop]
            if n >= min_count:
                rec_over[prop] = round(_clamp(_shrink_rate(hr, n, base=0.53, k=20), 0.35, 0.80), 3)
                n_over[prop] = n
        if prop in under_map:
            n_u, hr_u = under_map[prop]
            if n_u >= min_count:
                rec_under[prop] = round(_clamp(_shrink_rate(hr_u, n_u, base=0.50, k=20), 0.30, 0.80), 3)
                n_under[prop] = n_u

    return rec_w, rec_over, rec_under, n_weight, n_over, n_under


def _apply_to_file(
    spec: TargetSpec,
    rec_weights: dict[str, float],
    rec_over: dict[str, float],
    rec_under: dict[str, float],
    max_delta: float,
    force_large_shifts: bool,
    apply: bool,
) -> tuple[str, str, list[str]]:
    original = spec.file_path.read_text(encoding="utf-8")
    updated = original
    notes: list[str] = []

    def merge_const(const_name: str, rec: dict[str, float]) -> None:
        nonlocal updated, notes
        b = _extract_const_block(updated, const_name)
        if not b:
            notes.append(f"{spec.sport}: constant not found: {const_name}")
            return
        s, e, block = b
        order, vals = _extract_mapping(block)
        if not vals:
            notes.append(f"{spec.sport}: no parseable entries for {const_name}")
            return
        changed = 0
        for k, v in rec.items():
            if k not in vals:
                continue
            old = vals[k]
            delta = abs(old - v)
            if delta < 0.001:
                continue
            if (not force_large_shifts) and delta > max_delta:
                notes.append(
                    f"{spec.sport}: {const_name}.{k} skipped (delta {delta:.3f} > max_delta {max_delta:.3f})"
                )
                continue
            vals[k] = v
            changed += 1
        if changed == 0:
            notes.append(f"{spec.sport}: no changes for {const_name}")
            return
        new_block = _format_mapping_block(const_name, order, vals)
        updated = updated[:s] + new_block + updated[e:]
        notes.append(f"{spec.sport}: {const_name} updated keys={changed}")

    if spec.weight_const:
        merge_const(spec.weight_const, rec_weights)
    if spec.over_prior_const:
        merge_const(spec.over_prior_const, rec_over)
    if spec.under_const:
        merge_const(spec.under_const, rec_under)

    if apply and updated != original:
        spec.file_path.write_text(updated, encoding="utf-8")
    return original, updated, notes


def main() -> None:
    ap = argparse.ArgumentParser(description="Recalibrate rank constants from graded outcomes.")
    ap.add_argument("--days", type=int, default=14, help="Use last N days of graded files.")
    ap.add_argument("--min-count", type=int, default=30, help="Minimum sample per prop/direction.")
    ap.add_argument("--sports", default="NBA,CBB,NHL,Soccer", help="Comma list. Supported: NBA,CBB,NHL,Soccer")
    ap.add_argument("--max-delta", type=float, default=0.08, help="Reject single-run changes larger than this unless forced.")
    ap.add_argument("--force-large-shifts", action="store_true", help="Allow updates larger than --max-delta.")
    ap.add_argument("--exclude-props", default="", help="Comma list of normalized props to skip (e.g. fg3a,shots_on_goal).")
    ap.add_argument("--min-date", default="", help="Optional hard floor date (YYYY-MM-DD) for graded rows/files.")
    ap.add_argument("--apply", action="store_true", help="Apply updates directly to target files.")
    args = ap.parse_args()

    sports = [s.strip() for s in args.sports.split(",") if s.strip()]
    excluded_props = {x.strip() for x in str(args.exclude_props).split(",") if x.strip()}
    min_date: pd.Timestamp | None = None
    if str(args.min_date).strip():
        min_date = pd.to_datetime(str(args.min_date).strip(), errors="coerce")
        if pd.isna(min_date):
            raise SystemExit(f"Invalid --min-date: {args.min_date}. Use YYYY-MM-DD.")
    out_dir = REPO_ROOT / "outputs" / "recalibration"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    all_summary: dict[str, Any] = {
        "timestamp": ts,
        "days": args.days,
        "min_count": args.min_count,
        "max_delta": args.max_delta,
        "force_large_shifts": bool(args.force_large_shifts),
        "exclude_props": sorted(excluded_props),
        "min_date": None if min_date is None else str(min_date.date()),
        "apply": bool(args.apply),
        "sports": {},
    }
    report_lines = [
        f"# Recalibration Report ({ts})",
        "",
        f"- Days: **{args.days}**",
        f"- Min Count: **{args.min_count}**",
        f"- Max Delta Clamp: **{args.max_delta:.3f}**",
        f"- Force Large Shifts: **{args.force_large_shifts}**",
        f"- Excluded Props: **{', '.join(sorted(excluded_props)) if excluded_props else '(none)'}**",
        f"- Min Date Override: **{str(min_date.date()) if min_date is not None else '(none)'}**",
        f"- Apply: **{args.apply}**",
        "",
    ]

    any_changes = False
    for sport in sports:
        spec = TARGETS.get(sport)
        if not spec:
            report_lines.append(f"## {sport}\nUnsupported sport key.\n")
            continue
        df = _load_grades(sport, args.days, excluded_props=excluded_props, min_date=min_date)
        rec_w, rec_over, rec_under, n_weight, n_over, n_under = _recommend(df, args.min_count)
        if not any([spec.weight_const, spec.over_prior_const, spec.under_const]):
            notes = [f"{sport}: structural difference — no target constants configured (read-only summary only)."]
            orig = spec.file_path.read_text(encoding="utf-8")
            upd = orig
        else:
            orig, upd, notes = _apply_to_file(
                spec,
                rec_w,
                rec_over,
                rec_under,
                args.max_delta,
                args.force_large_shifts,
                args.apply,
            )

        diff = ""
        if orig != upd:
            any_changes = True
            diff = "".join(
                difflib.unified_diff(
                    orig.splitlines(keepends=True),
                    upd.splitlines(keepends=True),
                    fromfile=str(spec.file_path),
                    tofile=str(spec.file_path),
                    n=2,
                )
            )
        all_summary["sports"][sport] = {
            "rows": int(len(df)),
            "recommended_weights": len(rec_w),
            "recommended_over_priors": len(rec_over),
            "recommended_under_overrides": len(rec_under),
            "n_weight": n_weight,
            "n_over": n_over,
            "n_under": n_under,
            "notes": notes,
            "changed": bool(orig != upd),
        }

        report_lines.append(f"## {sport}")
        report_lines.append(f"- rows used: **{len(df)}**")
        report_lines.append(f"- recommended weight updates: **{len(rec_w)}**")
        report_lines.append(f"- recommended OVER prior updates: **{len(rec_over)}**")
        report_lines.append(f"- recommended UNDER override updates: **{len(rec_under)}**")
        sample_items: list[tuple[str, int]] = []
        for k, n in n_weight.items():
            sample_items.append((f"weight:{k}", n))
        for k, n in n_over.items():
            sample_items.append((f"over:{k}", n))
        for k, n in n_under.items():
            sample_items.append((f"under:{k}", n))
        if sample_items:
            sample_items.sort(key=lambda x: (x[1], x[0]))
            report_lines.append("- sample counts for recommended constants:")
            for label, n in sample_items[:80]:
                report_lines.append(f"  - {label} -> n={n}")
        low_n = sorted(
            {k for k, n in {**n_weight, **n_over, **n_under}.items() if n <= (args.min_count + 10)}
        )
        if low_n:
            report_lines.append(f"- near-floor sample props (<= min+10): **{', '.join(low_n[:20])}**")
        large_moves: list[str] = []
        for const_name, rec_map in [
            (spec.weight_const, rec_w),
            (spec.over_prior_const, rec_over),
            (spec.under_const, rec_under),
        ]:
            if not const_name:
                continue
            block = _extract_const_block(orig, const_name)
            if not block:
                continue
            _, _, btxt = block
            _, cur = _extract_mapping(btxt)
            for k, v in rec_map.items():
                if k in cur:
                    d = v - cur[k]
                    if abs(d) >= 0.05:
                        large_moves.append(f"{const_name}.{k}: {cur[k]:.3f} -> {v:.3f} (Δ {d:+.3f})")
        if large_moves:
            report_lines.append("- large moves (|Δ| >= 0.05):")
            for lm in large_moves[:50]:
                report_lines.append(f"  - {lm}")
        directional_clean = sorted(set(k for k in rec_over.keys() if k in rec_under and rec_over[k] < 0.5 < rec_under[k]))
        if directional_clean:
            report_lines.append(f"- directional-clean props (OVER<0.5 and UNDER>0.5): **{', '.join(directional_clean[:20])}**")
        for n in notes:
            report_lines.append(f"- {n}")
        if diff:
            report_lines.append("")
            report_lines.append("```diff")
            report_lines.append(diff[:120000])
            report_lines.append("```")
        report_lines.append("")

    j_path = out_dir / f"recalibrate_constants_{ts}.json"
    md_path = out_dir / f"recalibrate_constants_{ts}.md"
    j_path.write_text(json.dumps(all_summary, indent=2), encoding="utf-8")
    md_path.write_text("\n".join(report_lines), encoding="utf-8")

    print(f"Saved summary: {j_path}")
    print(f"Saved report:  {md_path}")
    if args.apply:
        print("Applied changes." if any_changes else "No file changes applied.")
    else:
        print("Recommend-only mode (no file edits).")


if __name__ == "__main__":
    main()

