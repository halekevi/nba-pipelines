#!/usr/bin/env python3
"""Capture linestats.php network requests from CDP Chrome (NST UI)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9222"
OUT = Path(__file__).resolve().parents[1] / "cache" / "_linestats_network.json"


def main() -> None:
    hits: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = ctx.new_page()

        def on_request(req):
            u = req.url
            if "linestats.php" in u or "playerteams.php" in u:
                hits.append(
                    {
                        "method": req.method,
                        "url": u,
                        "post_data": req.post_data,
                    }
                )

        page.on("request", on_request)

        # Load Line Stats like a human: team CAR, 5v5 default, submit
        page.goto("https://www.naturalstattrick.com/linestats.php", wait_until="networkidle", timeout=120_000)
        page.wait_for_timeout(1500)
        page.select_option("select[name=team]", "CAR")
        page.wait_for_timeout(1000)
        if page.locator("select[name=sit]").count():
            page.select_option("select[name=sit]", "5v5")
        page.locator('input[type="submit"]').click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)

        # Also try data subdomain with same final URL if redirected
        final_url = page.url
        has_table = page.locator("table").count() > 0
        dt_rows = page.evaluate(
            """() => {
              if (!window.jQuery || !jQuery.fn.DataTable) return 0;
              const tbl = jQuery('table').first();
              if (!tbl.length || !jQuery.fn.DataTable.isDataTable(tbl)) return 0;
              return tbl.DataTable().rows().count();
            }"""
        )

        summary = {
            "final_url": final_url,
            "has_table": has_table,
            "datatable_rows": dt_rows,
            "requests": hits,
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
        page.close()


if __name__ == "__main__":
    main()
