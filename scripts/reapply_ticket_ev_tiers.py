#!/usr/bin/env python3
"""Recompute STRONG/OK/MARGINAL/SKIP on an existing tickets JSON using percentile EV cuts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from utils.ticket_ev_tiers import apply_slate_ev_tier_recommendations, tier_distribution_summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--path",
        default=str(REPO / "ui_runner" / "templates" / "tickets_latest.json"),
        help="tickets JSON to update in place",
    )
    ap.add_argument(
        "--also-docs",
        action="store_true",
        help="Also write ui_runner/docs/tickets_latest.json",
    )
    args = ap.parse_args()

    path = Path(args.path).resolve()
    if not path.is_file():
        print(f"ERROR: not found: {path}")
        return 1

    payload = json.loads(path.read_text(encoding="utf-8"))
    apply_slate_ev_tier_recommendations(payload)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] wrote {path}")

    if args.also_docs:
        docs = path.parent.parent / "docs" / path.name
        docs.parent.mkdir(parents=True, exist_ok=True)
        docs.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[OK] wrote {docs}")

    dist = tier_distribution_summary(payload)
    print(f"[tier-ev] distribution: {dist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
