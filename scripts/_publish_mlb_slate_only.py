#!/usr/bin/env python3
"""Merge refreshed MLB step8 into slate_latest.json (preserve other sports)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import combined_slate_tickets as cst  # noqa: E402


def main() -> int:
    date = (sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")).strip()[:10]
    candidates = [
        REPO / "outputs" / date / "mlb" / "step8_mlb_direction_clean.xlsx",
        REPO / "outputs" / date / f"step8_mlb_direction_clean_{date}.xlsx",
        REPO / "Sports" / "MLB" / "step8_mlb_direction_clean.xlsx",
    ]
    mlb_path = next((p for p in candidates if p.is_file()), None)
    if mlb_path is None:
        print(f"Missing MLB step8 for {date} (tried {[str(p) for p in candidates]})")
        return 1

    mlb = cst.load_mlb(str(mlb_path))
    mlb = cst.enforce_target_date(mlb, "MLB", date, allow_cross_date_fallback=True)
    print(f"MLB rows: {len(mlb)} from {mlb_path}")

    rows = cst.dataframe_to_slate_sport_rows(mlb)
    gen_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    for outdir in (REPO / "mobile" / "www", REPO / "ui_runner" / "templates"):
        slate_path = outdir / "slate_latest.json"
        if slate_path.is_file():
            payload = json.loads(slate_path.read_text(encoding="utf-8"))
        else:
            payload = {"date": date, "sports": {}}
        sports = payload.get("sports") if isinstance(payload.get("sports"), dict) else {}
        sports["mlb"] = rows
        payload["sports"] = sports
        payload["date"] = date
        payload["generated_at"] = gen_at
        payload = cst._sanitize_for_json(payload)
        slate_path.write_text(
            json.dumps(payload, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        (outdir / "slate_sport_mlb.json").write_text(
            json.dumps({"ok": True, "sport": "mlb", "date": date, "generated_at": gen_at, "rows": rows}, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        total = sum(len(v) for v in sports.values() if isinstance(v, list))
        print(f"  {slate_path}  mlb={len(rows)}  all_sports={total}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
