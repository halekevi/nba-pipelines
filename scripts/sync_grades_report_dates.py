#!/usr/bin/env python3
"""Write ui_runner/templates/grades_report_dates.json from on-disk grade HTML files."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "ui_runner" / "templates"
OUT = TEMPLATES / "grades_report_dates.json"

_SLATE_RE = re.compile(r"^slate_eval_(\d{4}-\d{2}-\d{2})\.html$")
_TICKET_RE = re.compile(r"^ticket_eval_(\d{4}-\d{2}-\d{2})\.html$")


def _dates(pat: re.Pattern[str]) -> list[str]:
    found: list[str] = []
    for base in (TEMPLATES, TEMPLATES / "archive"):
        if not base.is_dir():
            continue
        for p in base.iterdir():
            if not p.is_file():
                continue
            m = pat.match(p.name)
            if m:
                found.append(m.group(1))
    return sorted(set(found))


def main() -> None:
    payload = {
        "ok": True,
        "slate_eval_dates": _dates(_SLATE_RE),
        "ticket_eval_dates": _dates(_TICKET_RE),
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} ({len(payload['slate_eval_dates'])} slate, {len(payload['ticket_eval_dates'])} ticket)")


if __name__ == "__main__":
    main()
