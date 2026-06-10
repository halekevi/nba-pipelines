#!/usr/bin/env python3
import sys
from pathlib import Path
from urllib.parse import urlencode
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nhl_pp_api import current_season_id
from nst_client import _linestats_params, nst_key

params = _linestats_params(current_season_id(), sit="5v5", team="CAR", lines="2")
params["key"] = nst_key()
url = "https://data.naturalstattrick.com/linestats.php?" + urlencode(params)

out = Path(__file__).resolve().parents[1] / "cache" / "_linestats_probe.html"
with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
    page = browser.contexts[0].new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=120000)
    html = page.content()
    out.write_text(html, encoding="utf-8")
    print("saved", out, "len", len(html))
    print("iframes", page.locator("iframe").count())
    print("forms", page.locator("form").count())
    print("table tags", html.lower().count("<table"))
    page.close()
