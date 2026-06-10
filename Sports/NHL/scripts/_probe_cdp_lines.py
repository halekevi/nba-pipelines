#!/usr/bin/env python3
import io
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nhl_pp_api import current_season_id
from nst_client import _linestats_params, _playerteams_params, nst_key, parse_tables

CDP = "http://127.0.0.1:9222"
key = nst_key()
sid = current_season_id()


def cdp_fetch(label: str, path: str, params: dict, *, submit: bool = False) -> None:
    q = dict(params)
    q["key"] = key
    url = f"https://data.naturalstattrick.com/{path}?" + urlencode(q)
    print(f"\n=== {label} ===")
    print("URL:", url[:120], "...")

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = ctx.new_page()
        page.goto(url, wait_until="networkidle", timeout=120_000)
        if submit:
            page.locator('input[type="submit"]').click()
            page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)
        html = page.content()
        print("final url:", page.url[:140])
        print("len:", len(html), "table:", "<table" in html.lower())
        if "<table" not in html.lower():
            if len(html) < 500:
                print(html)
            page.close()
            return
        ths = re.findall(r"<th[^>]*>([^<]+)</th>", html)
        print("ths[:6]:", ths[:6])
        tables = parse_tables(html, label=label)
        if not tables:
            page.close()
            return
        df = tables[0]
        df.columns = [str(c).strip() for c in df.columns]
        name_col = next(
            (
                c
                for c in df.columns
                if str(c).lower() in ("line", "player", "player 1")
            ),
            df.columns[1] if len(df.columns) > 1 else df.columns[0],
        )
        if "Player 2" in df.columns and "Player 1" in df.columns:
            lines = (
                df["Player 1"].astype(str).str.strip()
                + " - "
                + df["Player 2"].astype(str).str.strip()
            )
            dash = lines.str.contains(" - ", regex=False, na=False).sum()
            print(f"rows={len(df)} wowy pairs={dash} sample={lines.head(3).tolist()}")
        else:
            dash = df[name_col].astype(str).str.contains(r"\s-\s", regex=True, na=False).sum()
            print(f"rows={len(df)} dash={dash} sample={df[name_col].head(3).tolist()}")
        page.close()


# playerteams oi lines=2 (wrong endpoint baseline)
p_pt = _playerteams_params(sid, sit="5v5", team="CAR", lines="2")
p_pt["stdoi"] = "oi"
cdp_fetch("playerteams oi lines=2", "playerteams.php", p_pt)

# linestats lines=2 GET
p_ls = _linestats_params(sid, sit="5v5", team="CAR", lines="2")
cdp_fetch("linestats GET", "linestats.php", p_ls)

# linestats with two CAR stars (WOWY) — Aho + Jarvis NHL ids
p_wowy = {
    **p_ls,
    "view": "wowy",
    "strict": "incl",
    "p1": "8478427",  # Sebastian Aho
    "p2": "8482093",  # Seth Jarvis
    "p3": "0",
    "p4": "0",
    "p5": "0",
    "fd": "2025-10-07",
    "td": "2026-04-16",
    "tgp": "2000",
}
cdp_fetch("linestats WOWY Aho+Jarvis GET", "linestats.php", p_wowy)
