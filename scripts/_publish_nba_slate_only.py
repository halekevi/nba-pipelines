#!/usr/bin/env python3
"""Merge refreshed NBA step8 into slate_latest.json (preserve other sports)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import combined_slate_tickets as cst  # noqa: E402


def main() -> int:
    date = (sys.argv[1] if len(sys.argv) > 1 else "2026-05-28").strip()[:10]
    nba_path = REPO / "outputs" / date / f"step8_nba_direction_clean_{date}.xlsx"
    if not nba_path.is_file():
        print(f"Missing {nba_path}")
        return 1

    nba = cst.load_nba(str(nba_path))
    nba = cst.enforce_target_date(nba, "NBA", date, allow_cross_date_fallback=True)
    print(f"NBA rows: {len(nba)}")
    if "void_reason" in nba.columns:
        forced = nba[nba["void_reason"].astype(str) == "FORCED_OVER_NEG_EDGE"]
        print(f"  FORCED_OVER_NEG_EDGE: {len(forced)} (rank null: {int(forced['rank_score'].isna().sum())})")

    rows = cst.dataframe_to_slate_sport_rows(nba)
    gen_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    for outdir in (REPO / "mobile" / "www", REPO / "ui_runner" / "templates"):
        slate_path = outdir / "slate_latest.json"
        if slate_path.is_file():
            payload = json.loads(slate_path.read_text(encoding="utf-8"))
        else:
            payload = {"date": date, "sports": {}}
        sports = payload.get("sports") if isinstance(payload.get("sports"), dict) else {}
        sports["nba"] = rows
        payload["sports"] = sports
        payload["date"] = date
        payload["generated_at"] = gen_at
        payload = cst._sanitize_for_json(payload)
        slate_path.write_text(
            json.dumps(payload, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        (outdir / "slate_sport_nba.json").write_text(
            json.dumps({"ok": True, "sport": "nba", "rows": rows}, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        total = sum(len(v) for v in sports.values() if isinstance(v, list))
        print(f"  {slate_path}  nba={len(rows)}  all_sports={total}")

    disp = {"date": date}
    for p in (REPO / "mobile" / "www" / "slate_display_date.json", REPO / "ui_runner/templates/slate_display_date.json"):
        p.write_text(json.dumps(disp, indent=2) + "\n", encoding="utf-8")
    print(f"slate_display_date -> {date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
