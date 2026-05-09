"""Publish uniform-bucket ticket artifacts for the UI.

Reads the raw outputs of `build_uniform_tickets.py` from
``outputs/tickets/uniform_tickets_<date>_top.json`` and the rolling
backtest summary at ``outputs/tickets/backtest_summary.csv``, then
emits compact JSON files into ``ui_runner/templates/`` and a simple
manifest of available dates so the Flask app and the static mobile
bundle can render the new "Uniform Tickets" sections without having
to know about the underlying ticket builder.

Outputs (all under ui_runner/templates/):
  uniform_tickets_<date>.json        per-date payload (summary + tickets)
  uniform_tickets_latest.json        most recent date's payload
  uniform_tickets_dates.json         {"dates": ["2026-05-08", ...]}
  uniform_tickets_backtest.json      rolling backtest table (per size,bucket)

Usage
    python scripts/build_uniform_tickets_artifacts.py --date 2026-05-08
    python scripts/build_uniform_tickets_artifacts.py --all
    python scripts/build_uniform_tickets_artifacts.py --build-missing
        # runs build_uniform_tickets.py for any strict_<date>.csv that lacks
        # a matching uniform_tickets_<date>_top.json, then publishes.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TICKETS_DIR = REPO / "outputs" / "tickets"
STRICT_DIR = REPO / "outputs" / "strict_mode"
TEMPLATES_DIR = REPO / "ui_runner" / "templates"
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

BUCKETS = ("elite", "premium", "strong", "value")


def _parse_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_int(v, default=0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _summarise(tickets: list[dict]) -> list[dict]:
    """Per (size, bucket) totals + averages + simple EV column."""
    agg: dict[tuple[int, str], dict] = {}
    for t in tickets:
        size = _parse_int(t.get("size"))
        bucket = str(t.get("bucket") or "")
        if size <= 0 or not bucket:
            continue
        key = (size, bucket)
        slot = agg.setdefault(
            key,
            {
                "size": size,
                "bucket": bucket,
                "n_tickets": 0,
                "sum_joint_p_hit": 0.0,
                "sum_power_payout": 0.0,
                "sum_expected_profit": 0.0,
                "all_hit_count": 0,
                "decided": 0,
            },
        )
        slot["n_tickets"] += 1
        slot["sum_joint_p_hit"] += _parse_float(t.get("joint_p_hit"))
        slot["sum_power_payout"] += _parse_float(t.get("power_payout"))
        slot["sum_expected_profit"] += _parse_float(t.get("expected_profit_per_$1"))
        legs = t.get("legs") or []
        results = [str(leg.get("result") or "").upper() for leg in legs]
        if all(r in ("HIT", "MISS") for r in results) and results:
            slot["decided"] += 1
            if all(r == "HIT" for r in results):
                slot["all_hit_count"] += 1

    rows: list[dict] = []
    for (size, bucket), slot in agg.items():
        n = max(slot["n_tickets"], 1)
        rows.append(
            {
                "size": size,
                "bucket": bucket,
                "n_tickets": slot["n_tickets"],
                "avg_joint_p_hit": round(slot["sum_joint_p_hit"] / n, 4),
                "avg_payout": round(slot["sum_power_payout"] / n, 3),
                "avg_expected_profit_per_$1": round(slot["sum_expected_profit"] / n, 4),
                "decided": slot["decided"],
                "all_hit_count": slot["all_hit_count"],
                "realized_all_hit_rate": (
                    round(slot["all_hit_count"] / slot["decided"], 4)
                    if slot["decided"] else None
                ),
            }
        )
    rows.sort(key=lambda r: (r["size"], BUCKETS.index(r["bucket"]) if r["bucket"] in BUCKETS else 9))
    return rows


def _trim_legs(t: dict) -> dict:
    """Keep only fields the UI renders, to keep payload small."""
    keep_top = (
        "size", "bucket", "joint_p_hit", "power_payout",
        "expected_profit_per_$1", "ticket_id",
    )
    keep_leg = (
        "sport", "player", "team", "opp_team", "prop", "line", "direction",
        "pick_type", "tier", "tier_override", "ml_prob", "meta_prob",
        "strict_label", "result", "est_p", "game_key",
    )
    out = {k: t.get(k) for k in keep_top if k in t}
    legs = t.get("legs") or []
    out["legs"] = [{k: leg.get(k) for k in keep_leg if k in leg} for leg in legs]
    return out


def _maybe_run_builder(date_str: str) -> bool:
    """Run scripts/build_uniform_tickets.py for the given date if outputs are missing."""
    out_json = TICKETS_DIR / f"uniform_tickets_{date_str}_top.json"
    if out_json.exists():
        return True
    strict_csv = STRICT_DIR / f"strict_{date_str}.csv"
    if not strict_csv.exists():
        print(f"[skip {date_str}] no strict_mode CSV found")
        return False
    script = REPO / "scripts" / "build_uniform_tickets.py"
    cmd = [sys.executable, str(script), "--date", date_str]
    print(f"[build {date_str}] {' '.join(cmd)}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[err {date_str}] builder failed: {res.stderr.strip() or res.stdout.strip()}")
        return False
    return out_json.exists()


def publish_date(date_str: str) -> Path | None:
    src = TICKETS_DIR / f"uniform_tickets_{date_str}_top.json"
    if not src.exists():
        return None
    raw = json.loads(src.read_text(encoding="utf-8"))
    tickets = [_trim_legs(t) for t in raw if isinstance(t, dict)]
    summary = _summarise(raw)
    payload = {
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_tickets": len(tickets),
        "summary": summary,
        "tickets": tickets,
    }
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = TEMPLATES_DIR / f"uniform_tickets_{date_str}.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"published {out_path.relative_to(REPO)} ({len(tickets)} tickets)")
    return out_path


def publish_backtest() -> Path | None:
    src = TICKETS_DIR / "backtest_summary.csv"
    if not src.exists():
        return None
    rows: list[dict] = []
    with src.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(
                {
                    "size": _parse_int(r.get("size")),
                    "bucket": str(r.get("bucket") or ""),
                    "n_tickets": _parse_int(r.get("n_tickets")),
                    "all_hit_count": _parse_int(r.get("all_hit_count")),
                    "n_void_tickets": _parse_int(r.get("n_void_tickets")),
                    "avg_joint_pred": _parse_float(r.get("avg_joint_pred")),
                    "avg_payout": _parse_float(r.get("avg_payout")),
                    "avg_effective_payout": _parse_float(r.get("avg_effective_payout")),
                    "realized_all_hit_rate": _parse_float(r.get("realized_all_hit_rate")),
                    "wilson_low": _parse_float(r.get("wilson_low")),
                    "realized_ev_per_$1": _parse_float(r.get("realized_ev_per_$1")),
                }
            )
    rows.sort(key=lambda r: (r["size"], BUCKETS.index(r["bucket"]) if r["bucket"] in BUCKETS else 9))
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rows": rows,
    }
    out_path = TEMPLATES_DIR / "uniform_tickets_backtest.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"published {out_path.relative_to(REPO)} ({len(rows)} rows)")
    return out_path


def write_manifest(dates: list[str]) -> Path:
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dates": sorted(set(dates), reverse=True),
    }
    out_path = TEMPLATES_DIR / "uniform_tickets_dates.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"published {out_path.relative_to(REPO)} ({len(payload['dates'])} dates)")
    return out_path


def write_latest_alias(latest_date: str) -> None:
    src = TEMPLATES_DIR / f"uniform_tickets_{latest_date}.json"
    if not src.exists():
        return
    dst = TEMPLATES_DIR / "uniform_tickets_latest.json"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"published {dst.relative_to(REPO)} (-> {latest_date})")


def discover_dates() -> list[str]:
    out: list[str] = []
    for p in sorted(TICKETS_DIR.glob("uniform_tickets_*_top.json")):
        m = DATE_RE.search(p.name)
        if m:
            out.append(m.group(0))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="Single date YYYY-MM-DD to publish")
    ap.add_argument("--all", action="store_true", help="Publish every date with raw outputs")
    ap.add_argument(
        "--build-missing",
        action="store_true",
        help="Run build_uniform_tickets.py for any strict_<date>.csv that lacks ticket outputs",
    )
    ap.add_argument(
        "--since",
        default=None,
        help="Only consider dates >= this YYYY-MM-DD (used with --all/--build-missing)",
    )
    args = ap.parse_args()

    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

    target_dates: list[str] = []
    if args.date:
        target_dates = [args.date]
    elif args.build_missing:
        for p in sorted(STRICT_DIR.glob("strict_*.csv")):
            m = DATE_RE.search(p.name)
            if not m:
                continue
            d = m.group(0)
            if args.since and d < args.since:
                continue
            target_dates.append(d)
    elif args.all:
        for d in discover_dates():
            if args.since and d < args.since:
                continue
            target_dates.append(d)
    else:
        target_dates = discover_dates()
        if not target_dates:
            ap.error("No uniform_tickets_*_top.json found; pass --date or --build-missing")

    if args.build_missing:
        for d in list(target_dates):
            if not _maybe_run_builder(d):
                target_dates.remove(d)

    published: list[str] = []
    for d in target_dates:
        if publish_date(d) is not None:
            published.append(d)

    if not published:
        print("No dates published")
        return 1

    write_manifest(published if args.all or args.build_missing else discover_published())
    write_latest_alias(max(published))
    publish_backtest()
    return 0


def discover_published() -> list[str]:
    out: list[str] = []
    for p in TEMPLATES_DIR.glob("uniform_tickets_*.json"):
        m = re.fullmatch(r"uniform_tickets_(\d{4}-\d{2}-\d{2})\.json", p.name)
        if m:
            out.append(m.group(1))
    return sorted(out, reverse=True)


if __name__ == "__main__":
    sys.exit(main())
