#!/usr/bin/env python3
"""One-screen STRONG builder pool + played bets + opt3 shadow tally."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = REPO / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from grade_strong_builder_tickets import (  # noqa: E402
    grade_ticket_legs,
    iter_tickets,
    load_graded,
)
from combined_export_trust import classify_combined_export_file  # noqa: E402

STRONG_GLOB = "strong_tickets_live_*.txt"
SHADOW_TRACK = REPO / "data" / "reports" / "winrate_goblin_opt3_shadow_track.json"
BACKTEST_JSON = REPO / "data" / "reports" / "winrate_goblin_policy_backtest.json"
EXPORT_GLOB = "combined_slate_tickets_*.json"
EXPORT_DIR = REPO / "ui_runner" / "data"

STRONG_TARGET_PCT = 40.0
OPT3_BASELINE_PCT = 23.3
MIN_N_BAR = 30
BACKTEST_DIVERGENCE_PP = 15.0


def _is_likely_backfill_export(path: Path, date_str: str) -> bool:
    trust, _ = classify_combined_export_file(path, date_str)
    return trust == "backfill"


def _export_dates() -> list[str]:
    dates: list[str] = []
    for p in EXPORT_DIR.glob(EXPORT_GLOB):
        name = p.name
        if not name.startswith("combined_slate_tickets_") or not name.endswith(".json"):
            continue
        if "_winrate_" in name or "_high_leg_" in name or "_long_parlay_" in name:
            continue
        d = name.replace("combined_slate_tickets_", "").replace(".json", "")
        if len(d) == 10 and d[4] == "-" and d[7] == "-":
            dates.append(d)
    return sorted(set(dates))


def _grade_strong_builder_for_date(date_str: str) -> dict[str, int | float | None]:
    export_path = EXPORT_DIR / f"combined_slate_tickets_{date_str}.json"
    if not export_path.is_file() or _is_likely_backfill_export(export_path, date_str):
        return {"wins": 0, "losses": 0, "decided": 0, "built": 0, "ungraded": 0, "win_pct": None}
    graded = load_graded(date_str)
    if not graded:
        return {"wins": 0, "losses": 0, "decided": 0, "built": 0, "ungraded": 0, "win_pct": None}
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    wins = losses = ungraded = built = 0
    for _, t in iter_tickets(payload):
        if not t.get("strong_builder"):
            continue
        built += 1
        res = grade_ticket_legs(t.get("legs") or [], graded)
        if res == "WIN":
            wins += 1
        elif res == "LOSS":
            losses += 1
        else:
            ungraded += 1
    decided = wins + losses
    win_pct = round(100.0 * wins / decided, 1) if decided else None
    return {
        "wins": wins,
        "losses": losses,
        "decided": decided,
        "built": built,
        "ungraded": ungraded,
        "win_pct": win_pct,
    }


def _load_strong_builder_pool() -> dict:
    """Automated strong_builder pool: latest day + cumulative (live exports only)."""
    latest_date = None
    latest: dict[str, int | float | None] = {}
    cum_w = cum_l = cum_decided = cum_built = 0
    for d in _export_dates():
        day = _grade_strong_builder_for_date(d)
        if day["built"] == 0 and day["decided"] == 0:
            continue
        if day["decided"]:
            latest_date = d
            latest = day
        cum_w += int(day["wins"] or 0)
        cum_l += int(day["losses"] or 0)
        cum_decided += int(day["decided"] or 0)
        cum_built += int(day["built"] or 0)
    cum_pct = round(100.0 * cum_w / cum_decided, 1) if cum_decided else None
    return {
        "latest_date": latest_date,
        "latest": latest,
        "cumulative_wins": cum_w,
        "cumulative_losses": cum_l,
        "cumulative_decided": cum_decided,
        "cumulative_built": cum_built,
        "cumulative_win_pct": cum_pct,
    }


def _load_strong_tally() -> dict[str, int | float | None]:
    reports = REPO / "data" / "reports"
    files = sorted(reports.glob(STRONG_GLOB), key=lambda p: p.stem)
    if not files:
        return {
            "wins": 0,
            "losses": 0,
            "played": 0,
            "win_pct": None,
            "source": None,
            "last_played_date": None,
        }

    latest_file = files[-1]
    text = latest_file.read_text(encoding="utf-8", errors="replace")
    source = latest_file.name
    last_played_date = None
    m_date = re.search(r"(\d{4}-\d{2}-\d{2})", source)
    if m_date:
        last_played_date = m_date.group(1)

    w = l = played = None
    m = re.search(r"STRONG\s+W:\s*(\d+)\s*\|\s*STRONG\s+L:\s*(\d+)", text, re.I)
    if m:
        w, l = int(m.group(1)), int(m.group(2))
        played = w + l
    if played is None:
        m2 = re.search(r"STRONG\s+played:\s*(\d+)", text, re.I)
        if m2:
            played = int(m2.group(1))
    if w is None and played is not None:
        m3 = re.search(r"(\d+)/(\d+)\s+wins", text, re.I)
        if m3:
            w, played = int(m3.group(1)), int(m3.group(2))
            l = played - w

    win_pct = (100.0 * w / played) if played and w is not None and played > 0 else None
    return {
        "wins": w if w is not None else 0,
        "losses": l if l is not None else 0,
        "played": played if played is not None else 0,
        "win_pct": win_pct,
        "source": source,
        "last_played_date": last_played_date,
    }


def _load_shadow_track() -> dict:
    if not SHADOW_TRACK.is_file():
        return {}
    try:
        return json.loads(SHADOW_TRACK.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_backtest_opt3_ticket_hr() -> float | None:
    if not BACKTEST_JSON.is_file():
        return None
    try:
        data = json.loads(BACKTEST_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None
    for row in data.get("ticket_summary") or []:
        if str(row.get("policy") or "").lower() == "opt3":
            hr = row.get("ticket_hit_rate")
            return float(hr) * 100.0 if hr is not None else None
    return None


def main() -> int:
    builder = _load_strong_builder_pool()
    strong = _load_strong_tally()
    shadow = _load_shadow_track()
    backtest_opt3_pct = _load_backtest_opt3_ticket_hr()

    sw = int(shadow.get("shadow_paid_total") or 0)
    sd = int(shadow.get("shadow_decided_total") or 0)
    shadow_pct = (
        100.0 * float(shadow.get("shadow_ticket_hit_rate"))
        if shadow.get("shadow_ticket_hit_rate") is not None
        else (100.0 * sw / sd if sd else None)
    )

    bw = int(shadow.get("baseline_paid_total") or 0)
    bd = int(shadow.get("baseline_decided_total") or 0)
    baseline_pct = (
        100.0 * float(shadow.get("baseline_ticket_hit_rate"))
        if shadow.get("baseline_ticket_hit_rate") is not None
        else (100.0 * bw / bd if bd else None)
    )

    print("=== Prop hit-rate daily check ===\n")

    # STRONG builder pool (automated — all strong_builder slips, not bets placed)
    print("=== STRONG BUILDER POOL (all slips, automated) ===")
    print("  Question: is the STRONG builder logic sound? (not your personal P&L)")
    ld = builder.get("latest_date")
    latest = builder.get("latest") or {}
    if ld and latest.get("decided"):
        lw = int(latest.get("wins") or 0)
        ll = int(latest.get("losses") or 0)
        ln = int(latest.get("decided") or 0)
        lb = int(latest.get("built") or 0)
        lp = latest.get("win_pct")
        print(f"  Latest: {ld}  {lw}/{ln} = {lp:.1f}%  (built={lb}, ungraded={latest.get('ungraded', 0)})")
    else:
        print("  Latest: — (no graded strong_builder export)")
    cw = int(builder.get("cumulative_wins") or 0)
    cl = int(builder.get("cumulative_losses") or 0)
    cn = int(builder.get("cumulative_decided") or 0)
    cp = builder.get("cumulative_win_pct")
    if cn:
        print(f"  Cumulative: {cw}/{cn} = {cp:.1f}%  (target {STRONG_TARGET_PCT:.0f}%)")
    else:
        print("  Cumulative: —")
    if cn >= MIN_N_BAR:
        print(f"  FLAG:   n>={MIN_N_BAR} — builder validation bar reached")
    else:
        print(f"  FLAG:   n<{MIN_N_BAR} — keep accumulating ({max(0, MIN_N_BAR - cn)} to go)")
    print("  Source: grade_strong_builder_tickets.py on ui_runner/data/combined_slate_tickets_*.json")
    print("  Note: excludes 06-23/24/25 backfill exports")
    print()

    # STRONG played (manual — actual bets only)
    sp = int(strong.get("played") or 0)
    sw_s = int(strong.get("wins") or 0)
    sl_s = int(strong.get("losses") or 0)
    s_pct = strong.get("win_pct")
    print("=== STRONG PLAYED (your actual bets) ===")
    print("  Question: did the tickets you personally played cash?")
    print(f"  Cumulative: {sw_s}W-{sl_s}L  (n={sp})", end="")
    if s_pct is not None:
        print(f"  = {s_pct:.1f}%")
    else:
        print()
    if strong.get("last_played_date"):
        print(f"  Last played: {strong['last_played_date']}")
    if strong.get("source"):
        print(f"  Source: data/reports/{strong['source']}")
    if sp >= MIN_N_BAR:
        print(f"  FLAG:   n>={MIN_N_BAR} — sample bar reached")
    else:
        print(f"  FLAG:   n<{MIN_N_BAR} — log only when you actually play ({MIN_N_BAR - sp} to go)")
    print()

    # opt3 shadow
    print("opt3 shadow (Goblin Tier A — production track)")
    print(f"  Paid:   {sw}/{sd} decidable tickets")
    if shadow_pct is not None:
        print(f"  Win %:  {shadow_pct:.1f}%")
    else:
        print("  Win %:  —")
    if baseline_pct is not None:
        print(f"  Baseline (main track): {bw}/{bd} = {baseline_pct:.1f}% (ref {OPT3_BASELINE_PCT:.1f}%)")
        if shadow_pct is not None:
            print(f"  vs baseline: {shadow_pct - baseline_pct:+.1f}pp")
    if sd >= MIN_N_BAR:
        print(f"  FLAG:   n>={MIN_N_BAR} — sample bar reached (live exports only)")
    else:
        print(f"  FLAG:   n<{MIN_N_BAR} — keep accumulating ({max(0, MIN_N_BAR - sd)} to go)")
    if shadow.get("ready_to_ship") is True:
        print("  SHIP:   ready_to_ship=true (live n>=30 and shadow beats baseline)")
    elif shadow.get("ready_to_ship") is False:
        reason = shadow.get("ready_to_ship_reason") or "not eligible"
        print(f"  SHIP:   ready_to_ship=false — {reason}")

    if backtest_opt3_pct is not None and shadow_pct is not None:
        gap = shadow_pct - backtest_opt3_pct
        print(f"  Backtest opt3 ticket HR (06-09–17 replay): {backtest_opt3_pct:.1f}%")
        print(f"  Live vs backtest: {gap:+.1f}pp")
        if abs(gap) > BACKTEST_DIVERGENCE_PP:
            print(
                f"  WARNING: live shadow diverges from backtest by >{BACKTEST_DIVERGENCE_PP:.0f}pp "
                f"— check methodology (anchor ticket, baseline definition), not just sample size"
            )
    elif backtest_opt3_pct is not None:
        print(f"  Backtest opt3 ticket HR: {backtest_opt3_pct:.1f}% (no live shadow HR yet)")

    if shadow.get("updated_at"):
        print(f"  Track updated: {shadow['updated_at']}")
    print(f"  Source: {SHADOW_TRACK.relative_to(REPO)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
