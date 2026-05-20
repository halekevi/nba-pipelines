#!/usr/bin/env python3
"""Bake tickets_built.html shell + tickets_latest.json into static tickets.html for mobile."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))

from combined_slate_tickets import render_tickets_body_html  # noqa: E402


def main() -> int:
    tpl_path = _REPO / "ui_runner" / "templates" / "tickets_built.html"
    json_path = _REPO / "ui_runner" / "templates" / "tickets_latest.json"
    winrate_path = _REPO / "ui_runner" / "templates" / "tickets_winrate_latest.json"

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    winrate_payload = None
    if winrate_path.is_file():
        winrate_payload = json.loads(winrate_path.read_text(encoding="utf-8"))
    body, title = render_tickets_body_html(
        payload, _non_ev_slips_removed=0, winrate_payload=winrate_payload
    )
    tpl = tpl_path.read_text(encoding="utf-8")
    html = (
        tpl.replace("{{ tickets_body|safe }}", body)
        .replace("{{ page_title }}", title or "PropOracle Tickets")
        .replace("{{ ui_build_id|default('', true) }}", "20260520-local")
        .replace("{{ deploy_git_sha|default('', true) }}", "6cf0ef18")
    )

    out_paths = [
        _REPO / "mobile" / "www" / "tickets.html",
        _REPO / "ui_runner" / "templates" / "tickets_baked_preview.html",
    ]
    for p in out_paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(html, encoding="utf-8")
        print(f"Wrote {p}")

    # Sync JSON to mobile
    for name in ("tickets_latest.json", "tickets_winrate_latest.json"):
        src = _REPO / "ui_runner" / "templates" / name
        if src.is_file():
            dst = _REPO / "mobile" / "www" / name
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"Copied {name} -> mobile/www/")

    checks = {
        "p_win": "p_win" in body,
        "P(WIN)": "P(WIN)" in body or "P (WIN)" in body,
        "winrate-best": "winrate-best" in body or "Today's Best" in body,
    }
    print("UI checks:", checks)
    if winrate_path.is_file():
        wr = json.loads(winrate_path.read_text(encoding="utf-8"))
        n = sum(len(g.get("tickets") or []) for g in wr.get("groups") or [])
        print(f"Win-rate JSON: {n} slips, mode={wr.get('mode')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
