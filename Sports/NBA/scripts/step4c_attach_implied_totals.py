#!/usr/bin/env python3
"""step4c_attach_implied_totals.py — team implied totals from game_total + spread."""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs
from nba_stats_api import norm_team

log = logging.getLogger("nba.step4c")

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
TOTALS_CACHE = _DATA_DIR / "nba_implied_totals_cache.json"
VI_URL = "https://www.vegasinsider.com/nba/odds/las-vegas/"
TIMEOUT = 10

def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(path)


def _game_script(team_imp: float | None, opp_imp: float | None) -> str:
    if team_imp is None or opp_imp is None:
        return "pick_em"
    delta = float(team_imp) - float(opp_imp)
    if delta >= 8:
        return "heavy_favorite"
    if delta >= 3:
        return "slight_favorite"
    if abs(delta) < 3:
        return "pick_em"
    return "underdog"


def _derive_from_row(row: pd.Series) -> tuple[float | None, float | None]:
    gt = pd.to_numeric(row.get("game_total"), errors="coerce")
    spread = pd.to_numeric(row.get("spread"), errors="coerce")
    if pd.isna(gt):
        return None, None
    if pd.isna(spread):
        spread = 0.0
    half = float(gt) / 2.0
    sp = float(spread)
    home_imp = half - (sp / 2.0)
    away_imp = half + (sp / 2.0)
    is_home = str(row.get("home_away", row.get("is_home", ""))).strip().lower() in (
        "home",
        "h",
        "1",
        "true",
    )
    if is_home:
        return home_imp, away_imp
    return away_imp, home_imp


def _scrape_vegasinsider() -> dict[str, dict]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    try:
        time.sleep(0.5)
        r = requests.get(VI_URL, headers=headers, timeout=TIMEOUT)
        if r.status_code != 200:
            return {}
        tables = pd.read_html(io.StringIO(r.text))
        out: dict[str, dict] = {}
        for tbl in tables:
            cols = [str(c).lower() for c in tbl.columns]
            if not any("total" in c for c in cols):
                continue
            for _, row in tbl.iterrows():
                try:
                    gt = float(row.iloc[-1]) if len(row) else None
                except (TypeError, ValueError):
                    continue
                if gt and gt > 150:
                    key = str(row.iloc[0])[:40]
                    out[key] = {"game_total": gt, "spread": 0.0}
        return out
    except Exception as exc:
        log.warning("VegasInsider scrape failed: %s", exc)
        return {}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="step4_with_stats.csv")
    ap.add_argument("--output", default="step4_with_stats.csv")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")
    cache = _load_json(TOTALS_CACHE)

    if args.refresh or not cache:
        scraped = _scrape_vegasinsider()
        if scraped:
            cache["vegasinsider_latest"] = {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "games": scraped,
            }
            _save_json(TOTALS_CACHE, cache)

    if "game_script_context" not in df.columns:
        df["game_script_context"] = "pick_em"
    df["game_script_context"] = df["game_script_context"].astype(object)

    for c in ("team_implied_total", "opp_implied_total"):
        if c not in df.columns:
            df[c] = np.nan

    hit = 0
    n = len(df)
    warned = False

    for idx, row in df.iterrows():
        team_imp, opp_imp = _derive_from_row(row)
        if team_imp is None and not warned:
            if "game_total" not in df.columns or df["game_total"].isna().all():
                log.warning(
                    "No game_total/spread in step4 — team_implied_total left null "
                    "(VegasInsider fallback did not match rows)."
                )
            warned = True
        if team_imp is not None:
            df.at[idx, "team_implied_total"] = round(team_imp, 1)
            df.at[idx, "opp_implied_total"] = round(opp_imp, 1) if opp_imp is not None else np.nan
            df.at[idx, "game_script_context"] = _game_script(team_imp, opp_imp)
            hit += 1
            gdate = str(row.get("game_date", ""))[:10]
            home = norm_team(row.get("pp_home_team", row.get("home_team", "")))
            away = norm_team(row.get("pp_away_team", row.get("away_team", "")))
            if home and away and gdate:
                key = f"{home}_{away}_{gdate}"
                cache.setdefault("games", {})[key] = {
                    "game_total": float(row.get("game_total", np.nan))
                    if pd.notna(row.get("game_total"))
                    else None,
                    "team_implied": team_imp,
                    "opp_implied": opp_imp,
                }

    if cache:
        _save_json(TOTALS_CACHE, cache)

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    copy_pipeline_output_to_dated_dirs(
        output_path=args.output,
        df=df,
        sport_dir_name="NBA",
        repo_root=_REPO_ROOT,
    )
    print(f"NBA implied totals: {hit}/{n} rows ({hit / max(n, 1):.1%})")
    print(f"✅ Saved → {args.output}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ NBA step4c failed. {type(e).__name__}: {e}")
        sys.exit(1)
