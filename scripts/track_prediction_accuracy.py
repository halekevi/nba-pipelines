#!/usr/bin/env python3
"""Append daily prediction accuracy slices from graded_props JSON to SQLite."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
_DB = _REPO / "data" / "accuracy_tracking.db"


def _parse_hit(result: object) -> int | None:
    t = str(result or "").strip().upper()
    if t in ("HIT", "WIN", "W", "1", "TRUE"):
        return 1
    if t in ("MISS", "LOSS", "L", "0", "FALSE"):
        return 0
    return None


def _line_bucket(line: object) -> str:
    try:
        v = float(line)
    except (TypeError, ValueError):
        return "unknown"
    if v < 5:
        return "lt5"
    if v <= 15:
        return "5_15"
    return "gt15"


def _status(hr: float) -> str:
    if hr > 0.55:
        return "STRONG"
    if hr >= 0.45:
        return "OK"
    if hr >= 0.35:
        return "WEAK"
    return "POOR"


def load_graded_rows(root: Path, *, days: int) -> pd.DataFrame:
    paths = sorted((root / "mobile" / "www").glob("graded_props_*.json"))
    if days > 0:
        paths = paths[-days:]
    rows: list[dict] = []
    for path in paths:
        date_str = path.stem.replace("graded_props_", "")[:10]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        chunk = data if isinstance(data, list) else data.get("props", data.get("rows", []))
        if not isinstance(chunk, list):
            continue
        for r in chunk:
            if not isinstance(r, dict):
                continue
            hit = _parse_hit(r.get("result"))
            if hit is None:
                continue
            rows.append(
                {
                    "date": date_str,
                    "sport": str(r.get("sport", "")).strip().upper(),
                    "direction": str(r.get("direction", r.get("over_under", ""))).strip().upper(),
                    "tier": str(r.get("tier", "")).strip().upper() or "—",
                    "pick_type": str(r.get("pick_type", "")).strip().title() or "Standard",
                    "line_bucket": _line_bucket(r.get("line")),
                    "hit": int(hit),
                    "edge": pd.to_numeric(r.get("edge"), errors="coerce"),
                    "ml_prob": pd.to_numeric(r.get("ml_prob"), errors="coerce"),
                }
            )
    return pd.DataFrame(rows)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS accuracy_slices (
            sport TEXT,
            direction TEXT,
            tier TEXT,
            pick_type TEXT,
            line_bucket TEXT,
            hit_rate REAL,
            n INTEGER,
            mean_edge REAL,
            mean_ml_prob REAL,
            calibration_error REAL,
            window_days INTEGER,
            computed_at TEXT,
            PRIMARY KEY (sport, direction, tier, pick_type, line_bucket, window_days)
        );
        CREATE TABLE IF NOT EXISTS daily_accuracy (
            date TEXT,
            sport TEXT,
            direction TEXT,
            tier TEXT,
            pick_type TEXT,
            line_bucket TEXT,
            hit_rate REAL,
            n INTEGER,
            computed_at TEXT,
            PRIMARY KEY (date, sport, direction, tier, pick_type, line_bucket)
        );
        """
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--db", type=Path, default=_DB)
    args = ap.parse_args()

    df = load_graded_rows(_REPO, days=args.days)
    if df.empty:
        print("No decided graded rows found.")
        return 1

    args.db.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = sqlite3.connect(args.db)
    ensure_schema(conn)

    grp_cols = ["sport", "direction", "tier", "pick_type", "line_bucket"]
    summary_rows: list[dict] = []

    for keys, g in df.groupby(grp_cols, dropna=False):
        n = len(g)
        if n == 0:
            continue
        hr = float(g["hit"].mean())
        mean_edge = float(g["edge"].mean()) if g["edge"].notna().any() else None
        mean_ml = float(g["ml_prob"].mean()) if g["ml_prob"].notna().any() else None
        cal_err = abs(hr - mean_ml) if mean_ml is not None else None
        sport, direction, tier, pick_type, line_bucket = keys
        conn.execute(
            """
            INSERT OR REPLACE INTO accuracy_slices
            (sport, direction, tier, pick_type, line_bucket, hit_rate, n, mean_edge,
             mean_ml_prob, calibration_error, window_days, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sport,
                direction,
                tier,
                pick_type,
                line_bucket,
                hr,
                n,
                mean_edge,
                mean_ml,
                cal_err,
                int(args.days),
                now,
            ),
        )
        summary_rows.append(
            {
                "sport": sport,
                "direction": direction,
                "tier": tier,
                "pick_type": pick_type,
                "line_bucket": line_bucket,
                "hit_rate": hr,
                "n": n,
                "mean_edge": mean_edge,
                "cal_err": cal_err,
                "status": _status(hr),
            }
        )

    for date_str, gday in df.groupby("date"):
        for keys, g in gday.groupby(grp_cols, dropna=False):
            n = len(g)
            if n == 0:
                continue
            sport, direction, tier, pick_type, line_bucket = keys
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_accuracy
                (date, sport, direction, tier, pick_type, line_bucket, hit_rate, n, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    date_str,
                    sport,
                    direction,
                    tier,
                    pick_type,
                    line_bucket,
                    float(g["hit"].mean()),
                    n,
                    now,
                ),
            )

    conn.commit()
    conn.close()

    out = pd.DataFrame(summary_rows).sort_values("n", ascending=False)
    print(f"Wrote {len(out)} slices -> {args.db} (window={args.days}d, rows={len(df):,})")
    print(
        f"{'Sport':<8} {'Dir':<6} {'Tier':<4} {'Pick':<8} {'Bucket':<6} "
        f"{'HR':>6} {'N':>7} {'CalErr':>7} {'Status':<8}"
    )
    for _, r in out.head(40).iterrows():
        cal = f"{r['cal_err']:.3f}" if r["cal_err"] is not None and not np.isnan(r["cal_err"]) else "—"
        print(
            f"{r['sport']:<8} {r['direction']:<6} {r['tier']:<4} {r['pick_type']:<8} {r['line_bucket']:<6} "
            f"{100*r['hit_rate']:5.1f}% {int(r['n']):7d} {cal:>7} {r['status']:<8}"
        )
    for _, r in out.iterrows():
        if r["n"] > 100 and r["hit_rate"] < 0.35:
            print(f"ALERT: {r['sport']} {r['direction']} {r['tier']} {r['pick_type']} underperforming (HR={r['hit_rate']:.1%}, n={int(r['n'])})")
        if r["n"] > 100 and r["hit_rate"] > 0.60:
            print(f"STRONG: {r['sport']} {r['direction']} {r['tier']} {r['pick_type']} outperforming (HR={r['hit_rate']:.1%}, n={int(r['n'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
