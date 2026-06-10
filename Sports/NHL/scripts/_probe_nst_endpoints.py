#!/usr/bin/env python3
"""Probe NST endpoints for 2-man line pairs."""
import io
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nhl_pp_api import current_season_id
from nst_client import _linestats_params, _playerteams_params, nst_key, parse_tables

k = nst_key()
sid = current_season_id()
headers = {"nst-key": k, "User-Agent": "Mozilla/5.0 (PropORACLE/1.0)"}


def probe(name: str, path: str, params: dict) -> None:
    q = dict(params)
    q["key"] = k
    url = f"https://data.naturalstattrick.com/{path}"
    r = requests.get(url, params=q, headers=headers, timeout=60)
    has_table = "<table" in r.text.lower()
    print(f"\n=== {name} ===")
    print(f"URL: {r.url}")
    print(f"status={r.status_code} len={len(r.text)} table={has_table}")
    if not has_table:
        return
    tables = parse_tables(r.text, label=name)
    if not tables:
        return
    df = tables[0]
    df.columns = [str(c).strip() for c in df.columns]
    line_col = next(
        (c for c in df.columns if "line" in str(c).lower() or str(c).lower() in ("player", "player 1")),
        df.columns[1] if len(df.columns) > 1 else df.columns[0],
    )
    col2 = "Player 2" if "Player 2" in df.columns else None
    if col2:
        pairs = (df["Player 1"].astype(str) + " - " + df[col2].astype(str)).head(3).tolist()
        dash = len(df)
    else:
        pairs = df[line_col].head(3).tolist()
        dash = df[line_col].astype(str).str.contains(r"\s-\s", regex=True, na=False).sum()
    print(f"cols[:6]={list(df.columns)[:6]} rows={len(df)} dash={dash}")
    print(f"sample={pairs}")


# playerteams variants
for lines in ("2", "3"):
    p = _playerteams_params(sid, sit="5v5", team="CAR", lines=lines)
    probe(f"playerteams lines={lines}", "playerteams.php", p)

# linestats — omit view, explicit lines
p = _linestats_params(sid, sit="5v5", team="CAR", lines="2")
probe("linestats lines=2 (no view)", "linestats.php", p)

# linestats with strict=incl (WOWY form extras)
p2 = {**p, "strict": "incl", "p1": "0", "p2": "0", "p3": "0", "p4": "0", "p5": "0"}
probe("linestats + strict + p=0", "linestats.php", p2)

# Try playerteams with pos=F per runbook
p3 = _playerteams_params(sid, sit="5v5", team="CAR", lines="2")
p3["pos"] = "F"
p3["stdoi"] = "oi"
probe("playerteams pos=F stdoi=oi lines=2", "playerteams.php", p3)
