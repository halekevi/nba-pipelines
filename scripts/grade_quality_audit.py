"""Grade-quality auditor.

Runs after grading and flags (sport, prop) groups where the actuals look
broken (mass-zero / mass-blank actuals, suspicious OVER vs UNDER asymmetry).
Prevents silent regressions like the NHL shots_on_goal / power_play_points
bug that drove false 100% UNDER hit rates.

Usage:
    python scripts/grade_quality_audit.py --date 2026-05-08
    python scripts/grade_quality_audit.py --date 2026-05-08 --strict
        # exit non-zero when any group is flagged (CI / pipeline guard)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent

# Per-sport allowlist of props where the *actual* stat is legitimately zero
# in a high share of rows (e.g. soccer goals / assists). Auditor will not
# flag these on zero-share alone; it still checks OVER/UNDER asymmetry.
LEGIT_RARE_EVENT = {
    ("SOCCER", "Assists"),
    ("SOCCER", "Goals"),
    ("SOCCER", "Goal + Assist"),
    ("NHL", "Goals"),
    ("NHL", "Assists"),
    ("NHL", "Power Play Points"),
    ("NBA", "Triple-Double"),
    ("NBA", "Double-Double"),
    ("MLB", "Home Runs"),
    ("MLB", "Stolen Bases"),
    ("MLB", "Triples"),
    ("WNBA", "Triple-Double"),
    ("WNBA", "Double-Double"),
}

ZERO_SHARE_FLAG = 0.85
OVERUNDER_ASYMMETRY_FLAG = 0.85


def _load_graded(date: str) -> pd.DataFrame:
    candidates = [
        REPO / "mobile" / "www" / f"graded_props_{date}.json",
        REPO / "ui_runner" / "templates" / f"graded_props_{date}.json",
    ]
    src = next((p for p in candidates if p.exists()), None)
    if src is None:
        raise SystemExit(f"No graded_props_{date}.json found")
    payload = json.loads(src.read_text(encoding="utf-8"))
    return pd.DataFrame(payload.get("props") or [])


def audit(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    res = df.get("result", pd.Series("", index=df.index)).astype(str).str.upper()
    df = df.assign(_res=res)
    df = df[df["_res"].isin(["HIT", "MISS"])].copy()
    if df.empty:
        return pd.DataFrame()

    df["sport_u"] = df["sport"].astype(str).str.upper().str.strip()
    df["prop_u"] = df["prop"].astype(str).str.strip()
    df["dir_u"] = df["direction"].astype(str).str.upper().str.strip()
    df["is_hit"] = (df["_res"] == "HIT").astype(int)

    av = df["actual_value"].astype(str).str.strip()
    df["zero_actual"] = av.isin(["0.0", "0", "0.00", ""]).astype(int)

    rows: list[dict] = []
    for (sp, prop), g in df.groupby(["sport_u", "prop_u"], dropna=False):
        n = int(len(g))
        if n < 30:
            continue
        zshare = float(g["zero_actual"].mean())
        over = g[g["dir_u"] == "OVER"]
        under = g[g["dir_u"] == "UNDER"]
        n_o, n_u = int(len(over)), int(len(under))
        hr_o = float(over["is_hit"].mean()) if n_o else float("nan")
        hr_u = float(under["is_hit"].mean()) if n_u else float("nan")

        flags: list[str] = []
        legit = (sp, prop) in LEGIT_RARE_EVENT
        if zshare >= ZERO_SHARE_FLAG and not legit:
            flags.append(f"zero_actual_share={zshare:.2f}")
        if (
            n_o >= 30
            and n_u >= 30
            and pd.notna(hr_o)
            and pd.notna(hr_u)
            and hr_u >= OVERUNDER_ASYMMETRY_FLAG
            and hr_o <= (1.0 - OVERUNDER_ASYMMETRY_FLAG)
        ):
            flags.append(f"under_hit={hr_u:.2f}_vs_over_hit={hr_o:.2f}")
        if not flags:
            continue
        rows.append(
            {
                "sport": sp,
                "prop": prop,
                "n": n,
                "n_over": n_o,
                "n_under": n_u,
                "hit_rate_over": hr_o,
                "hit_rate_under": hr_u,
                "zero_actual_share": zshare,
                "flags": ";".join(flags),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["sport", "prop", "n", "n_over", "n_under",
                                     "hit_rate_over", "hit_rate_under",
                                     "zero_actual_share", "flags"])
    return pd.DataFrame(rows).sort_values(["sport", "n"], ascending=[True, False])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--strict", action="store_true", help="non-zero exit if anything flagged")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else REPO / "outputs" / "grade_quality"
    out_dir.mkdir(parents=True, exist_ok=True)

    df = _load_graded(args.date)
    flagged = audit(df)

    out_csv = out_dir / f"grade_quality_audit_{args.date}.csv"
    out_json = out_dir / f"grade_quality_audit_{args.date}.json"
    flagged.to_csv(out_csv, index=False)
    out_json.write_text(
        json.dumps(
            {
                "date": args.date,
                "n_decided": int((df["result"].astype(str).str.upper().isin(["HIT", "MISS"])).sum())
                if not df.empty
                else 0,
                "flagged_groups": flagged.to_dict(orient="records"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    if flagged.empty:
        print(f"[grade_quality_audit] {args.date}: clean — 0 broken groups.")
        return 0

    print(f"[grade_quality_audit] {args.date}: {len(flagged)} broken-grade groups detected:")
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 20)
    print(flagged.to_string(index=False))
    print(f"Wrote {out_csv}")
    return 2 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
