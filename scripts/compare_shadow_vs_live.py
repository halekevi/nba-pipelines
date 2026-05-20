#!/usr/bin/env python3
"""Compare live vs shadow ticket hit rates from graded_props JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent


def _parse_hit(result: object) -> int | None:
    t = str(result or "").strip().upper()
    if t in ("HIT", "WIN", "W", "1", "TRUE"):
        return 1
    if t in ("MISS", "LOSS", "L", "0", "FALSE"):
        return 0
    return None


def _as_bool(v: object) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    return s in ("1", "true", "yes", "y")


def load_day_rows(root: Path, date_str: str) -> pd.DataFrame:
    path = root / "mobile" / "www" / f"graded_props_{date_str}.json"
    if not path.is_file():
        return pd.DataFrame()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return pd.DataFrame()
    chunk = data if isinstance(data, list) else data.get("props", data.get("rows", []))
    if not isinstance(chunk, list):
        return pd.DataFrame()
    rows: list[dict] = []
    for r in chunk:
        if not isinstance(r, dict):
            continue
        hit = r.get("hit")
        if hit is None:
            hit = _parse_hit(r.get("result"))
        if hit is None:
            continue
        tid = r.get("ticket_id")
        tid_s = str(tid).strip() if tid is not None and str(tid).strip().lower() not in ("", "nan", "none") else None
        rows.append(
            {
                "date": date_str,
                "sport": str(r.get("sport", "")).strip().upper(),
                "hit": int(hit),
                "ticket_id": tid_s,
                "on_ticket": _as_bool(r.get("on_ticket")),
                "on_shadow_ticket": _as_bool(r.get("on_shadow_ticket")),
                "graded_at": str(r.get("graded_at") or date_str)[:10],
            }
        )
    return pd.DataFrame(rows)


def compute_ticket_win_rate(props_df: pd.DataFrame, *, pool: str) -> tuple[float | None, pd.DataFrame]:
    """Group by ticket_id; ticket wins when all legs hit. pool: live | shadow | any."""
    if props_df.empty or "ticket_id" not in props_df.columns:
        return None, pd.DataFrame()
    sub = props_df.dropna(subset=["ticket_id"]).copy()
    if sub.empty:
        return None, pd.DataFrame()
    if pool == "live":
        sub = sub.loc[sub["on_ticket"]]
    elif pool == "shadow":
        sub = sub.loc[sub["on_shadow_ticket"]]
    if sub.empty:
        return None, pd.DataFrame()
    ticket_results = (
        sub.groupby("ticket_id")
        .agg(
            all_hit=("hit", lambda x: bool((x == 1).all())),
            n_legs=("hit", "count"),
            sport=("sport", "first"),
            date=("graded_at", "first"),
        )
        .reset_index()
    )
    if ticket_results.empty:
        return None, ticket_results
    return float(ticket_results["all_hit"].mean()), ticket_results


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    out: list[dict] = []
    for date_str, g in df.groupby("date"):
        def hr(mask: pd.Series) -> float | None:
            sub = g.loc[mask]
            return float(sub["hit"].mean()) if len(sub) else None

        live_hr = hr(g["on_ticket"])
        shadow_hr = hr(g["on_shadow_ticket"])
        all_hr = float(g["hit"].mean())
        delta = (live_hr - shadow_hr) if live_hr is not None and shadow_hr is not None else None
        out.append(
            {
                "date": date_str,
                "live_hr": live_hr,
                "shadow_hr": shadow_hr,
                "all_hr": all_hr,
                "delta": delta,
                "live_n": int(g["on_ticket"].sum()),
                "shadow_n": int(g["on_shadow_ticket"].sum()),
                "all_n": len(g),
            }
        )
    return pd.DataFrame(out).sort_values("date")


def print_sport_breakdown(df: pd.DataFrame) -> None:
    if df.empty:
        return
    print("\nPer sport (pooled):")
    for sport, g in df.groupby("sport"):
        live = g.loc[g["on_ticket"]]
        shadow = g.loc[g["on_shadow_ticket"]]
        live_hr = float(live["hit"].mean()) if len(live) else float("nan")
        shadow_hr = float(shadow["hit"].mean()) if len(shadow) else float("nan")
        print(
            f"  {sport:<8} live={live_hr:.1%} (n={len(live):4d})  "
            f"shadow={shadow_hr:.1%} (n={len(shadow):4d})  "
            f"all={g['hit'].mean():.1%} (n={len(g)})"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    paths = sorted((_REPO / "mobile" / "www").glob("graded_props_*.json"))
    if args.days > 0:
        paths = paths[-args.days :]
    frames = []
    for path in paths:
        date_str = path.stem.replace("graded_props_", "")[:10]
        day = load_day_rows(_REPO, date_str)
        if not day.empty:
            frames.append(day)
    if not frames:
        print("No graded_props files with decided rows found.")
        return 1

    df = pd.concat(frames, ignore_index=True)
    daily = summarize(df)
    print(f"{'Date':<12} {'Live HR':>8} {'Shadow HR':>10} {'All HR':>8} {'Delta':>8} {'live_n':>7} {'shadow_n':>9}")
    for _, r in daily.iterrows():
        live = f"{100*r['live_hr']:.1f}%" if r["live_hr"] is not None and not np.isnan(r["live_hr"]) else "—"
        shadow = f"{100*r['shadow_hr']:.1f}%" if r["shadow_hr"] is not None and not np.isnan(r["shadow_hr"]) else "—"
        delta = f"{100*r['delta']:+.1f}pp" if r["delta"] is not None and not np.isnan(r["delta"]) else "—"
        print(
            f"{r['date']:<12} {live:>8} {shadow:>10} {100*r['all_hr']:>7.1f}% {delta:>8} "
            f"{int(r['live_n']):7d} {int(r['shadow_n']):9d}"
        )

    def trail_avg(col: str, n: int) -> float | None:
        s = daily[col].dropna().tail(n)
        return float(s.mean()) if len(s) else None

    print("\nTrailing averages:")
    for label, n in (("7-day", 7), ("30-day", 30)):
        live = trail_avg("live_hr", n)
        shadow = trail_avg("shadow_hr", n)
        delta = trail_avg("delta", n)
        if live is not None and shadow is not None:
            print(
                f"  {label}: live={live:.1%} shadow={shadow:.1%} delta={delta:+.1%} "
                f"(positive delta = gates helping)"
            )
        else:
            print(f"  {label}: insufficient ticket-tagged rows")

    print_sport_breakdown(df)

    live_wr, live_tix = compute_ticket_win_rate(df, pool="live")
    shadow_wr, shadow_tix = compute_ticket_win_rate(df, pool="shadow")
    leg_live = df.loc[df["on_ticket"]]
    leg_shadow = df.loc[df["on_shadow_ticket"]]
    leg_live_hr = float(leg_live["hit"].mean()) if len(leg_live) else None
    leg_shadow_hr = float(leg_shadow["hit"].mean()) if len(leg_shadow) else None

    print("\n=== Ticket vs leg hit rates (pooled) ===")
    if live_wr is not None:
        print(f"  Ticket win rate (live): {live_wr:.1%} on {len(live_tix)} tickets")
    else:
        print("  Ticket win rate (live): — (no ticket_id on live-tagged legs)")
    if shadow_wr is not None:
        print(f"  Ticket win rate (shadow): {shadow_wr:.1%} on {len(shadow_tix)} tickets")
    else:
        print("  Ticket win rate (shadow): — (no ticket_id on shadow-tagged legs)")
    if leg_live_hr is not None:
        print(f"  Leg hit rate (live): {leg_live_hr:.1%} (n={len(leg_live)} legs)")
    if leg_shadow_hr is not None:
        print(f"  Leg hit rate (shadow): {leg_shadow_hr:.1%} (n={len(leg_shadow)} legs)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
