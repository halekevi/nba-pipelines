"""
Step 3 — Attach Opponent Defense Context (NHL)
Pulls current NHL standings + team defensive stats to contextualize each prop.

For skaters: attaches opponent goals-against avg, shots-against avg, penalty kill %
For goalies: attaches opponent goals-for avg, shots-for avg, power play %

Usage:
    py step3_attach_defense_nhl.py --input outputs/step2_nhl_picktypes.csv --output outputs/step3_nhl_with_defense.csv

NOTE: Fetches live data from the NHL Stats API (no key needed).
      Also accepts --defense <csv> to use a pre-built defense summary.
"""

import argparse
import csv
import json
import sys
import time

import pandas as pd
import urllib.request
from datetime import datetime
from pathlib import Path

_NHL_ROOT = Path(__file__).resolve().parents[3]
if str(_NHL_ROOT) not in sys.path:
    sys.path.insert(0, str(_NHL_ROOT))
from utils.defense_tiers import (  # noqa: E402
    assert_def_tier_column,
    def_tier_from_overall_rank,
    format_def_tier_counts,
)
try:
    from tqdm import tqdm as _tqdm
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "tqdm", "--break-system-packages", "-q"])
    from tqdm import tqdm as _tqdm

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

NHL_API = "https://api.nhle.com/stats/rest/en"
# standings endpoint
STANDINGS_URL = "https://api-web.nhle.com/v1/standings/now"


def _current_season() -> str:
    now = datetime.now()
    start_year = now.year if now.month >= 10 else now.year - 1
    return f"{start_year}{start_year + 1}"


def _build_team_stats_url() -> str:
    """NHL /team/summary endpoint (bulk /team + gameTypeId cayenne returns 400)."""
    s = _current_season()
    return (
        f"{NHL_API}/team/summary?isAggregate=false&isGame=false"
        f"&sort=%5B%7B%22property%22%3A%22gamesPlayed%22%2C%22direction%22%3A%22DESC%22%7D%5D"
        f"&start=0&limit=50&factCayenneExp=gamesPlayed%3E%3D1"
        f"&cayenneExp=gameTypeId%3D2%20and%20seasonId%3E%3D{s}%20and%20seasonId%3C%3D{s}"
    )


def _build_name_to_abbrev_from_standings() -> dict[str, str]:
    """Map teamFullName (stats API) -> tri-code from api-web standings."""
    data = fetch_json(STANDINGS_URL)
    out: dict[str, str] = {
        "utah hockey club": "UTA",
    }
    for entry in data.get("standings", []):
        abbrev = (entry.get("teamAbbrev") or {}).get("default", "")
        name = (entry.get("teamName") or {}).get("default", "")
        if abbrev and name:
            out[name.strip().lower()] = abbrev.strip().upper()
    return out


def _stats_from_team_record(rec: dict) -> dict:
    return {
        "opp_gaa": round(float(rec.get("goalsAgainstPerGame", 0) or 0), 3),
        "opp_saa": round(float(rec.get("shotsAgainstPerGame", 0) or 0), 3),
        "opp_pk_pct": round(float(rec.get("penaltyKillPct", 0) or 0), 3),
        "opp_gf_per_game": round(float(rec.get("goalsForPerGame", 0) or 0), 3),
        "opp_sf_per_game": round(float(rec.get("shotsForPerGame", 0) or 0), 3),
        "opp_pp_pct": round(float(rec.get("powerPlayPct", 0) or 0), 3),
        "opp_wins": int(rec.get("wins", 0) or 0),
        "opp_gp": int(rec.get("gamesPlayed", 0) or 0),
    }


def _parse_team_summary_records(records: list, name_to_abbr: dict[str, str]) -> dict:
    teams: dict = {}
    for rec in records:
        abbrev = str(rec.get("teamAbbrev", "") or "").strip().upper()
        if not abbrev:
            full = str(rec.get("teamFullName", "") or "").strip().lower()
            abbrev = name_to_abbr.get(full, "")
        if not abbrev:
            continue
        teams[abbrev] = _stats_from_team_record(rec)
    return teams


def _standings_fallback_teams() -> dict:
    """Goals-only fallback when /team/summary is unavailable."""
    data = fetch_json(STANDINGS_URL)
    teams: dict = {}
    for entry in data.get("standings", []):
        abbrev = entry.get("teamAbbrev", {}).get("default", "")
        if not abbrev:
            continue
        gp = int(entry.get("gamesPlayed", 1) or 1)
        ga = int(entry.get("goalAgainst", 0) or 0)
        gf = int(entry.get("goalFor", 0) or 0)
        teams[abbrev.upper()] = {
            "opp_gaa": round(ga / max(gp, 1), 3),
            "opp_saa": 0.0,
            "opp_pk_pct": 0.0,
            "opp_gf_per_game": round(gf / max(gp, 1), 3),
            "opp_sf_per_game": 0.0,
            "opp_pp_pct": 0.0,
            "opp_wins": int(entry.get("wins", 0) or 0),
            "opp_gp": gp,
        }
    return teams


def fetch_json(url: str) -> dict:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        print(f"  Warning: fetch failed for {url}: {exc}")
        return {}


def fetch_team_defense_stats() -> dict:
    """
    Returns dict keyed by team abbrev with defensive metrics.
    """
    print("Fetching NHL team stats from NHL API (/team/summary)...")
    name_to_abbr = _build_name_to_abbrev_from_standings()
    summary_url = _build_team_stats_url()

    def _load_summary() -> dict:
        data = fetch_json(summary_url)
        return _parse_team_summary_records(data.get("data", []), name_to_abbr)

    teams = _load_summary()
    if not teams:
        print("  /team/summary empty — retrying once...")
        time.sleep(0.3)
        teams = _load_summary()

    if not teams:
        print("  /team/summary failed — trying standings (partial), then /team/summary enrich...")
        teams = _standings_fallback_teams()
        enrich = _load_summary()
        for abbrev, stats in enrich.items():
            if abbrev not in teams:
                teams[abbrev] = stats
            elif teams[abbrev].get("opp_saa") in (0, 0.0, None):
                teams[abbrev].update(stats)
    elif any(v.get("opp_saa") in (0, 0.0, None) for v in teams.values()):
        enrich = _load_summary()
        for abbrev, stats in enrich.items():
            if abbrev in teams and teams[abbrev].get("opp_saa") in (0, 0.0, None):
                teams[abbrev].update(stats)

    print(f"  Got defense stats for {len(teams)} teams")
    return teams


def build_defense_tier(teams: dict) -> dict:
    """
    Rank teams by goals-against avg (lower GAA = better defense).
    Quintile labels: Elite / Above Avg / Avg / Below Avg / Weak (same as utils.defense_tiers).
    """
    sorted_teams = sorted(teams.items(), key=lambda x: x[1].get("opp_gaa", 3.0))
    n_total = len(sorted_teams)
    n_pool = max(n_total, 1)
    tiers = {}
    for i, (abbrev, _) in enumerate(sorted_teams):
        rank = i + 1
        tier = def_tier_from_overall_rank(rank, n_pool)
        tiers[abbrev] = {"def_rank": rank, "def_tier": tier}
    return tiers


def read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(rows: list[dict], path: str):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} rows -> {path}")


def load_defense_csv(path: str) -> dict:
    teams = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            abbrev = row.get("team", "").upper()
            teams[abbrev] = row
    return teams


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="outputs/step2_nhl_picktypes.csv")
    parser.add_argument("--output", default="outputs/step3_nhl_with_defense.csv")
    parser.add_argument("--defense", default=None,
                        help="Optional pre-built defense summary CSV (team,opp_gaa,...)")
    args = parser.parse_args()

    rows = read_csv(args.input)

    # ── Load defense: DB first, CSV fallback, live API last resort ────────────
    teams = None
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _here = _Path(__file__).resolve().parent
        for _ in range(6):
            if (_here / "scripts" / "defense_db.py").exists():
                _sys.path.insert(0, str(_here / "scripts"))
                break
            _here = _here.parent
        from defense_db import load_defense_from_db, defense_freshness
        df_db = load_defense_from_db("nhl")
        if df_db is not None and len(df_db) >= 5:
            fresh = defense_freshness("nhl")
            print(f"→ Defense loaded from DB ({len(df_db)} teams, updated {fresh})")
            # Convert DataFrame back to the dict-of-dicts format this script expects
            teams = {}
            for _, row in df_db.iterrows():
                abbrev = str(row.get("team", "")).strip().upper()
                if abbrev:
                    teams[abbrev] = {k: row[k] for k in row.index if k != "team" and str(row[k]) not in ("nan", "None", "")}
        else:
            print("→ Defense DB empty for NHL — falling back")
    except Exception as _e:
        print(f"→ defense_db unavailable ({_e})")

    if teams is None:
        if args.defense:
            print(f"→ Loading defense CSV: {args.defense}")
            teams = load_defense_csv(args.defense)
        else:
            print("→ Fetching live NHL defense stats...")
            teams = fetch_team_defense_stats()

    tiers = build_defense_tier(teams)

    # Save defense summary for reference
    def_rows = []
    for abbrev, stats in sorted(teams.items()):
        def_rows.append({"team": abbrev, **stats, **tiers.get(abbrev, {})})
    _def_csv = Path("cache") / "nhl_defense_summary.csv"
    _def_csv.parent.mkdir(parents=True, exist_ok=True)
    write_csv(def_rows, str(_def_csv))

    defense_fields = [
        "opp_gaa", "opp_saa", "opp_pk_pct",
        "opp_gf_per_game", "opp_sf_per_game", "opp_pp_pct",
        "opp_gp", "def_rank", "def_tier",
    ]

    # ── Team abbreviation aliases (slate abbr -> NHL API abbr) ───────────────
    TEAM_ALIASES = {
        "LA":  "LAK",   # PrizePicks uses LA, NHL API uses LAK
        "NJ":  "NJD",   # PrizePicks uses NJ, NHL API uses NJD
        "SJ":  "SJS",   # PrizePicks uses SJ, NHL API uses SJS
        "TB":  "TBL",   # PrizePicks uses TB, NHL API uses TBL
        "CLB": "CBJ",   # alternate
        "ARZ": "UTA",   # Arizona -> Utah Hockey Club
    }

    results = []
    not_found = set()
    for row in _tqdm(rows, desc="  Attaching defense", unit="prop"):
        opp = row.get("opponent", "").upper()
        opp_lookup = TEAM_ALIASES.get(opp, opp)  # resolve alias if needed
        opp_stats = teams.get(opp_lookup, {})
        tier_info = tiers.get(opp_lookup, {})

        for field in defense_fields:
            if field in opp_stats:
                row[field] = opp_stats[field]
            elif field in tier_info:
                row[field] = tier_info[field]
            else:
                row[field] = ""
                if opp and not opp_stats:
                    not_found.add(opp)

        results.append(row)

    if not_found:
        print(f"  Could not find defense stats for: {sorted(not_found)}")

    _df_chk = pd.DataFrame(results)
    if "def_tier" in _df_chk.columns:
        _non_empty = _df_chk["def_tier"].astype(str).str.strip().ne("")
        assert_def_tier_column(_df_chk.loc[_non_empty], "def_tier", allow_empty=False)
        print(f"[NHL step3] {format_def_tier_counts(_df_chk, 'def_tier')}")

    write_csv(results, args.output)


if __name__ == "__main__":
    main()
