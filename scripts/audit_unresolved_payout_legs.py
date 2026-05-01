#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from payout_leg_resolver import PayoutLegResolver


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit unresolved payout legs and suggest snapshot matches.")
    ap.add_argument("--sport", default="NHL")
    ap.add_argument("--date", default="")
    ap.add_argument("--input", default="ui_runner/data/payout_ticket_legs.csv")
    ap.add_argument("--output", default="outputs/unresolved_payout_legs_report.csv")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent.parent
    in_path = (repo / args.input).resolve()
    out_path = (repo / args.output).resolve()
    if not in_path.exists():
        raise SystemExit(f"Input file not found: {in_path}")

    df = pd.read_csv(in_path, low_memory=False)
    if df.empty:
        print("No rows in payout ticket legs log.")
        return 0

    sp = str(args.sport or "").strip().upper()
    if "sport" in df.columns and sp:
        df = df[df["sport"].astype(str).str.upper() == sp]
    if args.date and "date" in df.columns:
        df = df[df["date"].astype(str).str.startswith(str(args.date))]

    if "delta_quality" in df.columns:
        unresolved = df[df["delta_quality"].astype(str).str.lower().isin({"", "unresolved"})].copy()
    else:
        unresolved = df.copy()
    resolver = PayoutLegResolver(repo)

    rows: list[dict] = []
    for _, r in unresolved.iterrows():
        res = resolver.resolve_leg(
            date=str(r.get("date", "")),
            sport=str(r.get("sport", "")),
            player=str(r.get("player", "")),
            prop=str(r.get("prop", r.get("prop_type", ""))),
            direction=str(r.get("direction", "")),
            played_line=r.get("line"),
            pick_type=str(r.get("pick_type", "Standard")),
        )
        rows.append(
            {
                "date": r.get("date", ""),
                "ticket_id": r.get("ticket_id", ""),
                "sport": r.get("sport", ""),
                "player": r.get("player", ""),
                "prop": r.get("prop", r.get("prop_type", "")),
                "line": r.get("line", ""),
                "direction": r.get("direction", ""),
                "pick_type": r.get("pick_type", ""),
                "current_delta_quality": r.get("delta_quality", ""),
                "candidate_delta_quality": res.get("delta_quality", ""),
                "candidate_matched_snapshot_path": res.get("matched_snapshot_path", ""),
                "candidate_matched_standard_line": res.get("matched_standard_line", ""),
                "candidate_delta_method": res.get("delta_method", ""),
                "candidate_delta": res.get("delta", ""),
            }
        )

    out = pd.DataFrame(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f"Wrote {len(out)} rows -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
