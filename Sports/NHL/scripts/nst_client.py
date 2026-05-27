#!/usr/bin/env python3
"""
Natural Stat Trick client (data.naturalstattrick.com).

Requires free NST access key: set NST_ACCESS_KEY or NST_KEY in the environment.
Caches parsed tables under Sports/NHL/data/ — never deletes prior seasons.

NST playerteams.php returns a server-rendered <table> when stdoi/toi/gpfilt/tgp
(and related filters) are set. linestats.php currently returns only the filter
shell (no <table>) to automated clients — line combo cache may stay empty until
NST serves that markup or we add a browser fetch path.
"""

from __future__ import annotations

import io
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import pandas as pd
import requests

log = logging.getLogger("nhl.nst")

NST_DATA = "https://data.naturalstattrick.com"
NST_BASE = f"{NST_DATA}/"
HEADERS = {"User-Agent": "Mozilla/5.0 (PropORACLE/1.0)"}
TIMEOUT = 30
SLEEP_S = 0.4

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
LINE_CACHE = _DATA_DIR / "nst_line_combos_cache.csv"
PLAYER_PP_CACHE = _DATA_DIR / "nst_player_pp_cache.csv"


def nst_key() -> str:
    return (os.environ.get("NST_ACCESS_KEY") or os.environ.get("NST_KEY") or "").strip()


def _season_param(season_id: int) -> str:
    """NHL seasonId 20242025 -> NST fromseason/thruseason 20242025."""
    return str(int(season_id))


def _nst_team(team: str) -> str:
    t = str(team or "all").strip().upper()
    return "ALL" if t in ("ALL", "A", "") else t


def _season_block(season_id: int) -> dict[str, str]:
    s = _season_param(season_id)
    return {"fromseason": s, "thruseason": s, "stype": "2"}


def _playerteams_params(
    season_id: int,
    *,
    sit: str,
    team: str,
    lines: str = "single",
) -> dict:
    """Query params that produce a populated playerteams.php table."""
    return {
        **_season_block(season_id),
        "sit": sit,
        "score": "all",
        "stdoi": "std",
        "rate": "n",
        "toi": "0",
        "gpfilt": "none",
        "tgp": "410",
        "loc": "B",
        "team": _nst_team(team),
        "pos": "S",
        "lines": lines,
        "draftteam": "ALL",
    }


def _linestats_params(
    season_id: int,
    *,
    sit: str,
    team: str,
    lines: str = "2",
) -> dict:
    """
    Query params for NST linestats.php line-pairs table (not WOWY).

    Use lines=2 for 2-man lines. Do not pass view= (view=log / view=wowy are WOWY UI).
  """
    return {
        **_season_block(season_id),
        "sit": sit,
        "score": "all",
        "rate": "n",
        "team": _nst_team(team),
        "vteam": "ALL",
        "loc": "B",
        "gpfilt": "none",
        "tgp": "410",
        "lines": lines,
        "draftteam": "ALL",
        "fd": "",
        "td": "",
    }


def fetch_html(path: str, params: dict) -> Optional[str]:
    key = nst_key()
    if not key:
        log.warning("NST_ACCESS_KEY not set — skipping live NST fetch")
        return None
    q = dict(params)
    q["key"] = key
    url = f"{NST_DATA}/{path.lstrip('/')}"
    try:
        time.sleep(SLEEP_S)
        r = requests.get(url, params=q, headers={**HEADERS, "nst-key": key}, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning("NST HTTP %s for %s", r.status_code, path)
            return None
        if "Just a moment" in r.text[:800]:
            log.warning("NST Cloudflare challenge — check access key or rate limits")
            return None
        return r.text
    except Exception as exc:
        log.warning("NST fetch failed: %s", exc)
        return None


def browser_fetch_html(
    path: str,
    params: dict,
    cdp_url: str = "http://127.0.0.1:9222",
    timeout: int = 30,
) -> Optional[str]:
    """
    Fetch NST page via Playwright CDP (connect to existing Chrome session).
    Falls back to None on any error — caller must handle gracefully.

    Setup (one-time):
      1. Launch Chrome: scripts/launch_nst_chrome_cdp.ps1
      2. Navigate to naturalstattrick.com, log in
      3. Run: py Sports/NHL/scripts/refresh_nst_cache.py --cdp
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("playwright not installed — skipping browser fetch")
        return None

    url = NST_BASE + path.lstrip("/")
    if params:
        url += "?" + urlencode(params)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(cdp_url)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            try:
                page.wait_for_selector("table", timeout=timeout * 1000)
            except Exception:
                pass
            html = page.content()
            page.close()
            return html
    except Exception as e:
        log.warning("[NST CDP] browser_fetch_html failed: %s", e)
        return None


def parse_tables(html: str, *, label: str = "") -> list[pd.DataFrame]:
    if not html:
        return []
    if "<table" not in html.lower():
        if label:
            log.warning(
                "NST %s: response has no <table> markup (len=%s) — cannot parse rows",
                label,
                len(html),
            )
        return []
    try:
        return pd.read_html(io.StringIO(html))
    except Exception as exc:
        log.warning("NST table parse failed%s: %s", f" ({label})" if label else "", exc)
        return []


def fetch_line_combos(
    season_id: int,
    team: str = "all",
    sit: str = "5v5",
    prefer_browser: bool = False,
    cdp_url: str = "http://127.0.0.1:9222",
    cdp_only: bool = False,
) -> pd.DataFrame:
    """
    Line combo stats (2-man / 3-man lines). sit: 5v5 | pp | etc.
    lines: 2 | 3 | … (NST linestats.php; not WOWY view=log)
    """
    params = _playerteams_params(season_id, sit=sit, team=team, lines="2")
    html = None
    if prefer_browser:
        html = browser_fetch_html("playerteams.php", params, cdp_url=cdp_url)
    elif not cdp_only:
        html = fetch_html("playerteams.php", params)

    if (not html or "<table" not in html.lower()) and not prefer_browser:
        log.info("[NST] requests fetch returned no table — trying CDP fallback")
        html = browser_fetch_html("playerteams.php", params, cdp_url=cdp_url)

    if not html or "<table" not in html.lower():
        log.warning("[NST] no table HTML from either path — returning empty")
        return pd.DataFrame()

    tables = parse_tables(html or "", label=f"playerteams {sit}")
    if not tables:
        return pd.DataFrame()
    df = tables[0].copy()
    df.columns = [str(c).strip() for c in df.columns]
    df["season_id"] = season_id
    df["sit"] = sit
    df["team_filter"] = _nst_team(team)
    df["fetched_at"] = datetime.now(timezone.utc).isoformat()
    if "Line" not in df.columns:
        for c in df.columns:
            if "line" in str(c).lower():
                df = df.rename(columns={c: "Line"})
                break
    return df


def fetch_player_pp(season_id: int, team: str = "all") -> pd.DataFrame:
    params = _playerteams_params(season_id, sit="pp", team=team, lines="single")
    html = fetch_html("playerteams.php", params)
    tables = parse_tables(html or "", label="playerteams pp")
    if not tables:
        return pd.DataFrame()
    df = tables[0].copy()
    df.columns = [str(c).strip() for c in df.columns]
    df["season_id"] = season_id
    df["team_filter"] = _nst_team(team)
    df["fetched_at"] = datetime.now(timezone.utc).isoformat()
    return df


def _append_cache(path: Path, fresh: pd.DataFrame, key_cols: list[str]) -> pd.DataFrame:
    if fresh.empty:
        return load_cache(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = load_cache(path)
    if old.empty:
        combined = fresh
    else:
        combined = pd.concat([old, fresh], ignore_index=True)
        subset = [c for c in key_cols if c in combined.columns]
        if subset:
            combined = combined.drop_duplicates(subset=subset, keep="last")
    tmp = path.with_suffix(".tmp.csv")
    combined.to_csv(tmp, index=False, encoding="utf-8-sig")
    tmp.replace(path)
    return combined


def load_cache(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


# NST export / linestats column names (Game Log shares schema with line pairs table).
_NST_LINE_CSV_ALIASES: dict[str, str] = {
    "Player": "Line",
    "Players": "Line",
    "Name": "Line",
    "Line": "Line",
    # Pass-through stats (canonical names unchanged)
    "Game": "Game",
    "TOI": "TOI",
    "CF": "CF",
    "CA": "CA",
    "CF%": "CF%",
    "FF": "FF",
    "FA": "FA",
    "FF%": "FF%",
    "SF": "SF",
    "SA": "SA",
    "SF%": "SF%",
    "GF": "GF",
    "GA": "GA",
    "GF%": "GF%",
    "xGF": "xGF",
    "xGA": "xGA",
    "xGF%": "xGF%",
    "SCF": "SCF",
    "SCA": "SCA",
    "SCF%": "SCF%",
    "HDCF": "HDCF",
    "HDCA": "HDCA",
    "HDCF%": "HDCF%",
    "HDGF": "HDGF",
    "HDGA": "HDGA",
    "HDGF%": "HDGF%",
    "On-Ice SH%": "On-Ice SH%",
    "On-Ice SV%": "On-Ice SV%",
    "PDO": "PDO",
    "Off. Zone Faceoffs": "Off. Zone Faceoffs",
    "Neu. Zone Faceoffs": "Neu. Zone Faceoffs",
    "Def. Zone Faceoffs": "Def. Zone Faceoffs",
    "Off. Zone Faceoff %": "Off. Zone Faceoff %",
}


def import_line_csv(
    csv_path: str,
    season_id: int,
    sit: str = "5v5",
    team_filter: str = "ALL",
) -> int:
    """
    Load a manually exported NST line stats CSV, normalize columns,
    inject metadata (season_id, sit, team_filter, fetched_at),
    and write to the line combos cache. Returns row count imported.
    """
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"NST import CSV not found: {path}")

    df = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    df.columns = df.columns.str.replace('\xa0', ' ', regex=False).str.strip()
    df.columns = [str(c).strip() for c in df.columns]

    rename = {
        src: dst
        for src, dst in _NST_LINE_CSV_ALIASES.items()
        if src in df.columns and src != dst
    }
    if rename:
        df = df.rename(columns=rename)

    if "Line" not in df.columns:
        df["Line"] = ""
    else:
        # Some NST exports duplicate each line; keep the row with populated stats.
        if "TOI" in df.columns:
            df = df.sort_values(by="TOI", ascending=False, na_position="last")
        df = df.drop_duplicates(subset=["Line"], keep="first")

    team_norm = _nst_team(team_filter)
    df["season_id"] = int(season_id)
    df["sit"] = str(sit).strip()
    df["team_filter"] = team_norm
    df["fetched_at"] = datetime.now(timezone.utc).isoformat()

    old = load_cache(LINE_CACHE)
    if not old.empty:
        for col in ("season_id", "sit", "team_filter"):
            if col not in old.columns:
                old[col] = ""
        keep = ~(
            (old["season_id"].astype(int) == int(season_id))
            & (old["sit"].astype(str) == str(sit).strip())
            & (old["team_filter"].astype(str).str.upper() == team_norm)
        )
        combined = pd.concat([old.loc[keep], df], ignore_index=True)
    else:
        combined = df

    key_cols = ["season_id", "sit", "team_filter", "Line"]
    subset = [c for c in key_cols if c in combined.columns]
    if subset:
        combined = combined.drop_duplicates(subset=subset, keep="last")

    LINE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LINE_CACHE.with_suffix(".tmp.csv")
    combined.to_csv(tmp, index=False, encoding="utf-8-sig")
    tmp.replace(LINE_CACHE)
    return len(df)


def refresh_line_cache(
    season_id: int,
    teams: Optional[list[str]] = None,
    prefer_browser: bool = False,
    cdp_url: str = "http://127.0.0.1:9222",
    cdp_only: bool = False,
) -> pd.DataFrame:
    teams = teams or ["all"]
    parts: list[pd.DataFrame] = []
    for team in teams:
        for sit in ("5v5", "pp"):
            df = fetch_line_combos(
                season_id,
                team=team,
                sit=sit,
                prefer_browser=prefer_browser,
                cdp_url=cdp_url,
                cdp_only=cdp_only,
            )
            if not df.empty:
                parts.append(df)
    if not parts:
        cached = load_cache(LINE_CACHE)
        if cached.empty:
            log.warning(
                "NST line combos: 0 rows (linestats.php returned no table HTML — "
                "PP cache may still refresh via playerteams.php)"
            )
        return cached
    fresh = pd.concat(parts, ignore_index=True)
    return _append_cache(
        LINE_CACHE,
        fresh,
        key_cols=["season_id", "sit", "team_filter", "Line"],
    )


def refresh_player_pp_cache(season_id: int, teams: Optional[list[str]] = None) -> pd.DataFrame:
    teams = teams or ["all"]
    parts = []
    for team in teams:
        df = fetch_player_pp(season_id, team=team)
        if not df.empty:
            parts.append(df)
    if not parts:
        return load_cache(PLAYER_PP_CACHE)
    fresh = pd.concat(parts, ignore_index=True)
    player_col = next((c for c in fresh.columns if str(c).lower() == "player"), None)
    key_cols = ["season_id", "team_filter"]
    if player_col:
        key_cols.append(player_col)
    return _append_cache(PLAYER_PP_CACHE, fresh, key_cols=key_cols)
