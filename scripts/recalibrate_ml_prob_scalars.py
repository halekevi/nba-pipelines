#!/usr/bin/env python3
"""
Recommend ML_PROB_CALIBRATION_SCALARS from graded props (mobile/www graded_props_*.json).

Scalars map Platt/isotonic ml_prob toward observed hit rates per (sport, pick_type, direction).
Does not retrain the XGBoost model — use refresh_slice_isotonic.py for isotonic refresh.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPTS = Path(__file__).resolve().parent
_REPO = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from edge_predict_utils import ML_PROB_CALIBRATION_SCALARS  # noqa: E402

logger = logging.getLogger(__name__)

# Per-sport scalar ceiling overrides (recalibrate script only; not live inference).
CLIP_HI_OVERRIDES: dict[str, float] = {
    "SOCCER": 3.0,  # default 2.50 — Soccer standard OVER severely underestimated
}

# Target hit rates for linear scalar tuning (post-isotonic).
_SLICE_TARGETS: dict[tuple[str, str, str], float] = {
    ("NBA", "standard", "OVER"): 0.50,
    ("NBA", "standard", "UNDER"): 0.50,
    ("NBA", "goblin", "OVER"): 0.65,
    ("WNBA", "standard", "OVER"): 0.50,
    ("WNBA", "standard", "UNDER"): 0.50,
    ("WNBA", "goblin", "OVER"): 0.65,
    ("NHL", "standard", "OVER"): 0.50,
    ("NHL", "standard", "UNDER"): 0.50,
    ("NHL", "goblin", "OVER"): 0.65,
    ("NHL", "demon", "OVER"): 0.35,
    ("MLB", "standard", "OVER"): 0.50,
    ("MLB", "goblin", "OVER"): 0.65,
    ("MLB", "demon", "OVER"): 0.35,
    ("SOCCER", "standard", "OVER"): 0.50,
    ("SOCCER", "goblin", "OVER"): 0.65,
    ("SOCCER", "demon", "OVER"): 0.35,
}


def _norm_sport(s: object) -> str:
    u = str(s or "").strip().upper()
    if u in ("SOC", "FOOTBALL", "SOCCER"):
        return "SOCCER"
    if u == "TENNIS":
        return "TENNIS"
    return u


def _norm_pick(s: object) -> str:
    return str(s or "").strip().lower()


def _norm_dir(s: object) -> str:
    d = str(s or "").strip().upper()
    if d in ("OVER", "O"):
        return "OVER"
    if d in ("UNDER", "U"):
        return "UNDER"
    return d


def _parse_hit(result: object) -> int | None:
    if result is None or (isinstance(result, float) and np.isnan(result)):
        return None
    t = str(result).strip().upper()
    if t in ("HIT", "WIN", "W", "1", "TRUE"):
        return 1
    if t in ("MISS", "LOSS", "L", "0", "FALSE"):
        return 0
    if t in ("VOID", "PUSH", "DNP", "CANCELLED", "CANCELED"):
        return None
    try:
        v = int(float(result))
        if v in (0, 1):
            return v
    except (TypeError, ValueError):
        pass
    return None


def load_graded_json_rows(
    root: Path,
    *,
    sport: str | None = None,
    max_files: int | None = None,
    min_date: str | None = None,
    include_suspect: bool = False,
) -> pd.DataFrame:
    rows: list[dict] = []
    paths = sorted((root / "mobile" / "www").glob("graded_props_*.json"))
    paths = [p for p in paths if ".bak_" not in p.name]
    if max_files:
        paths = paths[-int(max_files) :]
    min_d = str(min_date or "").strip()[:10]
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        file_date = str(data.get("date") or path.stem.replace("graded_props_", ""))[:10]
        if min_d and len(min_d) == 10 and file_date < min_d:
            continue
        chunk = data if isinstance(data, list) else data.get("props", data.get("rows", []))
        if not isinstance(chunk, list):
            continue
        for r in chunk:
            if not isinstance(r, dict):
                continue
            if not include_suspect and r.get("grading_suspect"):
                continue
            sp = _norm_sport(r.get("sport"))
            if sport and sp != _norm_sport(sport):
                continue
            hit = _parse_hit(r.get("result"))
            if hit is None:
                continue
            mp = pd.to_numeric(r.get("ml_prob"), errors="coerce")
            if mp is None or (isinstance(mp, float) and np.isnan(mp)):
                continue
            pt = _norm_pick(r.get("pick_type"))
            dr = _norm_dir(r.get("direction") or r.get("over_under"))
            if dr not in ("OVER", "UNDER") or not pt:
                continue
            rows.append(
                {
                    "sport": sp,
                    "pick_type": pt,
                    "direction": dr,
                    "hit": int(hit),
                    "ml_prob": float(mp),
                    "source_file": path.name,
                }
            )
    return pd.DataFrame(rows)


def _is_ticket_eligible_slice(pick_type: str, direction: str) -> bool:
    """Mirror combined_slate_tickets drop_demon_over_rows — demon+OVER is unbookable."""
    return not (_norm_pick(pick_type) == "demon" and _norm_dir(direction) == "OVER")


def exclude_unbookable_demon_over(
    df: pd.DataFrame,
    *,
    include_demon: bool = False,
) -> pd.DataFrame:
    if df.empty or include_demon:
        return df
    pt = df["pick_type"].astype(str).str.strip().str.lower()
    dr = df["direction"].astype(str).str.strip().str.upper()
    demon_over_mask = pt.eq("demon") & dr.eq("OVER")
    excluded = int(demon_over_mask.sum())
    if excluded > 0:
        logger.info("Excluding %s demon+OVER rows (unbookable)", f"{excluded:,}")
    return df.loc[~demon_over_mask].copy()


def recommend_scalars(
    df: pd.DataFrame,
    *,
    min_n: int = 50,
    clip_lo: float = 0.25,
    clip_hi: float = 2.50,
    use_actual_target: bool = False,
) -> pd.DataFrame:
    out_rows: list[dict] = []
    if df.empty:
        return pd.DataFrame(out_rows)

    for (sp, pt, dr), g in df.groupby(["sport", "pick_type", "direction"], sort=False):
        n = len(g)
        mean_p = float(g["ml_prob"].mean())
        actual_hr = float(g["hit"].mean())
        key = (str(sp), str(pt), str(dr))
        target = actual_hr if use_actual_target else _SLICE_TARGETS.get(key, 0.50)
        sport_clip_hi = float(CLIP_HI_OVERRIDES.get(str(sp).upper(), clip_hi))
        if n < min_n or mean_p <= 0.01:
            rec = None
            note = f"n<{min_n}" if n < min_n else "mean_p too low"
        else:
            rec = float(np.clip(target / mean_p, clip_lo, sport_clip_hi))
            note = "ok"
        cur = ML_PROB_CALIBRATION_SCALARS.get(key)
        out_rows.append(
            {
                "sport": sp,
                "pick_type": pt,
                "direction": dr,
                "n": n,
                "mean_ml_prob": round(mean_p, 4) if n else None,
                "actual_hit_rate": round(actual_hr, 4) if n else None,
                "target_hit_rate": round(target, 4),
                "current_scalar": cur,
                "recommended_scalar": round(rec, 4) if rec is not None else None,
                "ticket_eligible": _is_ticket_eligible_slice(pt, dr),
                "clip_hi": sport_clip_hi,
                "status": note,
            }
        )
    return pd.DataFrame(out_rows).sort_values(["sport", "pick_type", "direction"])


def _format_scalars_dict(rows: pd.DataFrame) -> str:
    lines = ["ML_PROB_CALIBRATION_SCALARS: dict[tuple[str, str, str], float] = {"]
    ok = rows[rows["recommended_scalar"].notna()].copy()
    for _, r in ok.iterrows():
        sp, pt, dr = r["sport"], r["pick_type"], r["direction"]
        val = r["recommended_scalar"]
        lines.append(f'    ("{sp}", "{pt}", "{dr}"): {val},  # n={int(r["n"])} hr={r["actual_hit_rate"]}')
    lines.append("}")
    return "\n".join(lines)


def _apply_to_edge_predict_utils(rows: pd.DataFrame, sport_filter: str | None) -> int:
    path = _SCRIPTS / "edge_predict_utils.py"
    text = path.read_text(encoding="utf-8")
    updated = 0
    inserted: list[str] = []
    for _, r in rows.iterrows():
        if pd.isna(r.get("recommended_scalar")):
            continue
        sp, pt, dr = str(r["sport"]), str(r["pick_type"]), str(r["direction"])
        if sport_filter and sp.upper() != _norm_sport(sport_filter):
            continue
        if not pt or pt == "—" or dr not in ("OVER", "UNDER"):
            continue
        val = float(r["recommended_scalar"])
        pat = rf'\(\s*"{re.escape(sp)}"\s*,\s*"{re.escape(pt)}"\s*,\s*"{re.escape(dr)}"\s*\)\s*:\s*[-0-9.]+'
        repl = f'("{sp}", "{pt}", "{dr}"): {val}'
        new_text, n = re.subn(pat, repl, text, count=1)
        if n:
            text = new_text
            updated += 1
        else:
            inserted.append(f'    ("{sp}", "{pt}", "{dr}"): {val},')
    if inserted:
        marker = "\n}\n\n_SLICE_CAL_PATH"
        block = "\n" + "\n".join(inserted) + marker
        if marker in text:
            text = text.replace(marker, block, 1)
            updated += len(inserted)
    if updated:
        path.write_text(text, encoding="utf-8")
    return updated


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sport",
        default=None,
        help="Filter to one sport (e.g. WNBA, NBA, Soccer → SOCCER)",
    )
    ap.add_argument("--min-n", type=int, default=50, help="Min graded rows per slice")
    ap.add_argument("--max-files", type=int, default=None, help="Only use last N graded JSON files")
    ap.add_argument(
        "--min-date",
        default="",
        metavar="YYYY-MM-DD",
        help="Only graded_props files on/after this date",
    )
    ap.add_argument(
        "--include-suspect",
        action="store_true",
        help="Include NBA1Q/NBA1H rows flagged grading_suspect",
    )
    ap.add_argument("--use-actual-target", action="store_true", help="Target = slice hit rate (not policy default)")
    ap.add_argument("--apply", action="store_true", help="Patch edge_predict_utils.py scalars in place")
    ap.add_argument(
        "--include-demon",
        action="store_true",
        help="Include demon+OVER rows (diagnostic; default excludes unbookable demon+OVER)",
    )
    ap.add_argument("--out-csv", type=Path, default=None, help="Write recommendations CSV")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    min_date = str(args.min_date or "").strip()[:10] or None
    df = load_graded_json_rows(
        _REPO,
        sport=args.sport,
        max_files=args.max_files,
        min_date=min_date,
        include_suspect=bool(args.include_suspect),
    )
    if df.empty:
        print("No graded rows with ml_prob + result found.")
        return 1

    print(f"Loaded {len(df):,} graded rows from mobile/www/graded_props_*.json")
    if min_date:
        print(f"  min_date >= {min_date}")
    df = exclude_unbookable_demon_over(df, include_demon=bool(args.include_demon))
    if df.empty:
        print("No rows left after demon+OVER exclusion.")
        return 1
    if args.sport:
        print(f"  sport filter: {args.sport.upper()} → {len(df):,} rows")

    rec = recommend_scalars(df, min_n=args.min_n, use_actual_target=args.use_actual_target)
    print(rec.to_string(index=False))

    out_csv = args.out_csv or (_REPO / "outputs" / "calibration" / "ml_prob_scalar_recommendations.csv")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    rec.to_csv(out_csv, index=False)
    print(f"\nWrote {out_csv}")

    print("\n--- Suggested ML_PROB_CALIBRATION_SCALARS block ---")
    sport_only = rec
    if args.sport:
        sport_only = rec[rec["sport"].astype(str).str.upper() == args.sport.upper()]
    print(_format_scalars_dict(sport_only))

    if args.apply:
        n = _apply_to_edge_predict_utils(rec, args.sport)
        print(f"\nPatched {n} scalar(s) in scripts/edge_predict_utils.py")
    else:
        print("\nDry run — pass --apply to update edge_predict_utils.py")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
