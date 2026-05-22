#!/usr/bin/env python3
"""
Expand Sports/Soccer/cache/soccer_defense_summary.csv with missing leagues.

Primary source: fbref.com (Squad Standard Stats — goals against).
Fallback: ESPN standings API when fbref is blocked (403) or --source espn.

Usage:
  py -3.14 scripts/expand_soccer_defense_db.py --dry-run
  py -3.14 scripts/expand_soccer_defense_db.py
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import sys
import time
import unicodedata
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Comment

_REPO = Path(__file__).resolve().parents[1]
_CSV_PATH = _REPO / "Sports" / "Soccer" / "cache" / "soccer_defense_summary.csv"
_HTML_CACHE = _REPO / "data" / "cache" / "fbref_html"

if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_REPO / "Sports" / "Soccer" / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "Sports" / "Soccer" / "scripts"))

from utils.defense_tiers import def_tier_from_overall_rank  # noqa: E402

try:
    from soccer_defense_report import PP_NAME_MAP as _BASE_PP_MAP  # type: ignore
except Exception:
    _BASE_PP_MAP = {}

FIELDNAMES = [
    "team_name",
    "pp_name",
    "league",
    "gp",
    "goals_conceded",
    "goals_conceded_pg",
    "shots_conceded_pg",
    "clean_sheets",
    "wins",
    "draws",
    "losses",
    "OVERALL_DEF_RANK",
    "DEF_TIER",
]

FBREF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://fbref.com/",
}

ESPN_HEADERS = {
    "User-Agent": FBREF_HEADERS["User-Agent"],
    "Accept": "application/json",
}

# PrizePicks-style short names for new leagues (merged with soccer_defense_report map).
EXPAND_PP_NAME_MAP: dict[str, str] = {
    # Saudi (xlsx shorthand)
    "Neom SC": "NEOM",
    "Al Kholood": "OKHDOOD",
    "Al Taawoun": "TAAWOUN",
    "Al Najma": "NAJMA",
    # Copa Libertadores
    "Bolívar": "BOLIVAR",
    "Peñarol": "PENAROL",
    "Liga de Quito": "LDU QUITO",
    "LDU Quito": "LDU QUITO",
    "Independiente Medellín": "MEDELLIN",
    "Independiente del Valle": "IND. DEL VALLE",
    "Independiente Santa Fe": "SANTA FE",
    "Deportivo La Guaira": "LA GUAIRA",
    "Deportes Tolima": "TOLIMA",
    "Coquimbo Unido": "COQUIMBO",
    "Always Ready": "ALWAYS READY",
    "Sporting Cristal": "CRISTAL",
    "Cerro Porteño": "CERRO PORTENO",
    "Universidad Católica": "UNIV CATOLICA",
    "Universitario": "UNIVERSITARIO",
    "Libertad": "LIBERTAD",
    "Nacional": "NACIONAL",
    "Cusco FC": "CUSCO",
    "Barcelona SC": "BARCELONA SC",
    "Universidad Central": "UCV",
    "Atlético Junior": "JUNIOR",
    # UEFA Nations (xlsx uses country names)
    "Czechia": "CZECHIA",
    "North Macedonia": "N. MACEDONIA",
    "Türkiye": "TURKEY",
}

PP_NAME_MAP: dict[str, str] = {**_BASE_PP_MAP, **EXPAND_PP_NAME_MAP}

LEAGUE_CONFIGS = [
    {
        "league": "Saudi Pro League",
        "fbref_url": (
            "https://fbref.com/en/comps/70/2024-2025/stats/"
            "2024-2025-Saudi-Professional-League-Stats"
        ),
        "fbref_html": "saudi_summary.html",
        "espn_slug": "ksa.1",
    },
    {
        "league": "Copa Libertadores",
        "fbref_url": "https://fbref.com/en/comps/14/2025/stats/2025-Copa-Libertadores-Stats",
        "fbref_html": "libertadores_summary.html",
        "espn_slug": "conmebol.libertadores",
    },
    {
        "league": "UEFA Nations League",
        "fbref_url": (
            "https://fbref.com/en/comps/218/2024-2025/stats/"
            "2024-2025-UEFA-Nations-League-Stats"
        ),
        "fbref_html": "nations_league_summary.html",
        "espn_slug": "uefa.nations",
        "espn_fallback_slug": "uefa.euro",
        "nations_league_a_only": False,
    },
]

# Nations not in active ESPN standings (gp=0) — still emit rows so Opp aliases resolve.
NATIONS_PLACEHOLDER_GCPG = 1.5

MIN_GP = 3
REQUEST_DELAY_SEC = 1.5


def _pp_name(display_name: str) -> str:
    if display_name in PP_NAME_MAP:
        return PP_NAME_MAP[display_name]
    s = unicodedata.normalize("NFKD", str(display_name or ""))
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.upper().strip()
    for suffix in (" FC", " SC", " CF", " AC", " SV", " FK", " SK", " AFC", " SFC"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    return s


def _load_existing_keys(csv_path: Path) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    if not csv_path.is_file():
        return keys
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            pp = str(row.get("pp_name", "")).strip().upper()
            lg = str(row.get("league", "")).strip().lower()
            if pp and lg:
                keys.add((pp, lg))
    return keys


def _rank_and_tier(rows: list[dict[str, Any]], league: str) -> list[dict[str, Any]]:
    eligible = [r for r in rows if int(r.get("gp") or 0) >= MIN_GP]
    n = len(eligible)
    if n == 0:
        for r in rows:
            r["OVERALL_DEF_RANK"] = 1
            r["DEF_TIER"] = "Avg"
        return rows

    sorted_rows = sorted(eligible, key=lambda r: float(r["goals_conceded_pg"]))
    rank_by_id = {id(r): i + 1 for i, r in enumerate(sorted_rows)}
    mid = max(1, (n + 1) // 2)

    for r in rows:
        gp = int(r.get("gp") or 0)
        if gp < MIN_GP:
            r["OVERALL_DEF_RANK"] = mid
            r["DEF_TIER"] = "Avg"
        else:
            rank = rank_by_id[id(r)]
            r["OVERALL_DEF_RANK"] = rank
            r["DEF_TIER"] = def_tier_from_overall_rank(rank, n)
        r["league"] = league
    return rows


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(FBREF_HEADERS)
    return s


def _uncomment_fbref_tables(soup: BeautifulSoup) -> None:
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        if "table" in str(comment):
            soup.append(BeautifulSoup(comment, "lxml"))


def _norm_header(h: str) -> str:
    return re.sub(r"\s+", " ", str(h or "").strip().lower())


def _parse_fbref_squad_table(table) -> list[dict[str, Any]]:
    """Parse fbref squad stats table for GA / MP / W-D-L."""
    rows_out: list[dict[str, Any]] = []
    thead = table.find("thead")
    if not thead:
        return rows_out

    header_rows = thead.find_all("tr")
    col_names: list[str] = []
    for tr in header_rows:
        cells = tr.find_all(["th", "td"])
        names = [_norm_header(c.get_text(" ", strip=True)) for c in cells]
        if any("squad" in n or n == "team" for n in names):
            col_names = names
            break
    if not col_names:
        last = header_rows[-1]
        col_names = [_norm_header(c.get_text(" ", strip=True)) for c in last.find_all(["th", "td"])]

    def _col_idx(*needles: str) -> int | None:
        for i, name in enumerate(col_names):
            for nd in needles:
                if nd in name:
                    return i
        return None

    idx_squad = _col_idx("squad", "team")
    idx_gp = _col_idx("mp", "matches", "games played", "gp")
    idx_ga = _col_idx("ga", "goals against", "goals conceded")
    idx_w = _col_idx("w", "wins")
    idx_d = _col_idx("d", "draws")
    idx_l = _col_idx("l", "losses")
    idx_cs = _col_idx("cs", "clean sheets")

    if idx_squad is None or idx_gp is None or idx_ga is None:
        return rows_out

    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) <= max(idx_squad, idx_gp, idx_ga):
            continue
        squad_cell = cells[idx_squad]
        link = squad_cell.find("a")
        team_name = (link.get_text(strip=True) if link else squad_cell.get_text(strip=True)).strip()
        if not team_name or team_name.lower() in ("squad", "team"):
            continue

        def _cell_int(i: int | None) -> int:
            if i is None or i >= len(cells):
                return 0
            txt = cells[i].get_text(strip=True).replace(",", "")
            try:
                return int(float(txt))
            except ValueError:
                return 0

        gp = _cell_int(idx_gp)
        ga = _cell_int(idx_ga)
        if gp <= 0:
            continue

        rows_out.append(
            {
                "team_name": team_name,
                "pp_name": _pp_name(team_name),
                "gp": gp,
                "goals_conceded": float(ga),
                "goals_conceded_pg": round(ga / gp, 3),
                "shots_conceded_pg": "",
                "clean_sheets": _cell_int(idx_cs),
                "wins": _cell_int(idx_w),
                "draws": _cell_int(idx_d),
                "losses": _cell_int(idx_l),
            }
        )
    return rows_out


def fetch_fbref_league(
    url: str,
    league: str,
    *,
    html_cache_name: str | None = None,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    html: str | None = None
    if html_cache_name:
        cache_file = _HTML_CACHE / html_cache_name
        if cache_file.is_file():
            html = cache_file.read_text(encoding="utf-8", errors="replace")
            print(f"  [fbref] loaded cached HTML: {cache_file.name}")

    if html is None:
        sess = session or _session()
        time.sleep(REQUEST_DELAY_SEC + random.uniform(0, 0.5))
        try:
            resp = sess.get(url, timeout=45)
            if resp.status_code == 403:
                print(f"  [fbref] {league}: HTTP 403 (blocked) — will try ESPN fallback")
                return []
            resp.raise_for_status()
            html = resp.text
        except Exception as exc:
            print(f"  [fbref] {league}: request failed ({exc})")
            return []

    soup = BeautifulSoup(html, "lxml")
    _uncomment_fbref_tables(soup)

    parsed: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        tid = str(table.get("id", ""))
        if "stats" not in tid and "squad" not in tid:
            batch = _parse_fbref_squad_table(table)
            if batch and len(batch) >= 4:
                parsed = batch
            continue
        batch = _parse_fbref_squad_table(table)
        if len(batch) > len(parsed):
            parsed = batch

    if not parsed:
        print(f"  [fbref] {league}: no squad table parsed from page")
    else:
        print(f"  [fbref] {league}: parsed {len(parsed)} teams")
    return parsed


def _espn_get(url: str) -> dict | None:
    time.sleep(0.35 + random.uniform(0, 0.15))
    try:
        r = requests.get(url, headers=ESPN_HEADERS, timeout=25)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        print(f"  [espn] GET failed: {url} ({exc})")
        return None


def _espn_standings_rows(
    data: dict,
    *,
    nations_league_a_only: bool = False,
    allow_zero_gp: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in data.get("children") or []:
        group_name = str(group.get("name", ""))
        if nations_league_a_only and not group_name.upper().startswith("GROUP A"):
            continue
        entries = group.get("standings", {}).get("entries") or group.get("entries") or []
        for entry in entries:
            ti = entry.get("team", {})
            team_name = str(ti.get("displayName", ti.get("name", ""))).strip()
            if not team_name:
                continue

            stats: dict[str, float] = {}
            for s in entry.get("stats") or []:
                key = str(s.get("name", "")).lower()
                try:
                    stats[key] = float(s.get("value", 0))
                except (TypeError, ValueError):
                    pass

            gp = int(stats.get("gamesplayed", stats.get("gp", 0)) or 0)
            gc = float(stats.get("pointsagainst", stats.get("goalsagainst", 0)) or 0)
            wins = int(stats.get("wins", 0) or 0)
            draws = int(stats.get("ties", stats.get("draws", 0)) or 0)
            losses = int(stats.get("losses", 0) or 0)
            cs = int(stats.get("cleansheets", 0) or 0)

            if gp <= 0 and not allow_zero_gp:
                continue

            gcpg = round(gc / gp, 3) if gp > 0 else NATIONS_PLACEHOLDER_GCPG
            rows.append(
                {
                    "team_name": team_name,
                    "pp_name": _pp_name(team_name),
                    "gp": gp,
                    "goals_conceded": gc,
                    "goals_conceded_pg": gcpg,
                    "shots_conceded_pg": "",
                    "clean_sheets": cs,
                    "wins": wins,
                    "draws": draws,
                    "losses": losses,
                }
            )
    return rows


def fetch_espn_league(
    slug: str,
    league: str,
    *,
    nations_league_a_only: bool = False,
    allow_zero_gp: bool = False,
    extra_slug: str | None = None,
) -> list[dict[str, Any]]:
    data = _espn_get(f"https://site.api.espn.com/apis/v2/sports/soccer/{slug}/standings")
    if not data:
        print(f"  [espn] {league}: no standings data")
        return []

    rows = _espn_standings_rows(
        data,
        nations_league_a_only=nations_league_a_only,
        allow_zero_gp=allow_zero_gp,
    )

    if extra_slug:
        extra = _espn_get(f"https://site.api.espn.com/apis/v2/sports/soccer/{extra_slug}/standings")
        if extra:
            rows.extend(_espn_standings_rows(extra, allow_zero_gp=False))

    if allow_zero_gp and rows and all(int(r["gp"]) == 0 for r in rows):
        print(f"  [espn] {league}: standings gp=0 — using placeholder gc/pg for international teams")

    rows = _dedupe_by_pp(rows)
    print(f"  [espn] {league}: fetched {len(rows)} teams")
    return rows


def _dedupe_by_pp(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = str(r["pp_name"]).upper()
        prev = best.get(key)
        if prev is None or int(r.get("gp") or 0) > int(prev.get("gp") or 0):
            best[key] = r
    return list(best.values())


def _filter_new(
    rows: list[dict[str, Any]], league: str, existing: set[tuple[str, str]]
) -> list[dict[str, Any]]:
    lg_key = league.strip().lower()
    new_rows: list[dict[str, Any]] = []
    for r in rows:
        pp = str(r["pp_name"]).strip().upper()
        if (pp, lg_key) in existing:
            continue
        new_rows.append(r)
    return new_rows


def _rows_to_csv_dicts(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for r in rows:
        out.append(
            {
                "team_name": r["team_name"],
                "pp_name": r["pp_name"],
                "league": r["league"],
                "gp": str(r["gp"]),
                "goals_conceded": str(r["goals_conceded"]),
                "goals_conceded_pg": str(r["goals_conceded_pg"]),
                "shots_conceded_pg": str(r.get("shots_conceded_pg", "") or ""),
                "clean_sheets": str(r.get("clean_sheets", 0)),
                "wins": str(r.get("wins", 0)),
                "draws": str(r.get("draws", 0)),
                "losses": str(r.get("losses", 0)),
                "OVERALL_DEF_RANK": str(r["OVERALL_DEF_RANK"]),
                "DEF_TIER": r["DEF_TIER"],
            }
        )
    return out


def expand_league(
    cfg: dict[str, Any],
    existing: set[tuple[str, str]],
    source: str,
    session: requests.Session | None,
) -> list[dict[str, Any]]:
    league = cfg["league"]
    raw: list[dict[str, Any]] = []

    if source in ("auto", "fbref"):
        raw = fetch_fbref_league(
            cfg["fbref_url"],
            league,
            html_cache_name=cfg.get("fbref_html"),
            session=session,
        )

    if not raw and source in ("auto", "espn"):
        if source == "fbref":
            print(f"  [warn] {league}: fbref empty — falling back to ESPN ({cfg['espn_slug']})")
        raw = fetch_espn_league(
            cfg["espn_slug"],
            league,
            nations_league_a_only=bool(cfg.get("nations_league_a_only")),
            allow_zero_gp=league == "UEFA Nations League",
            extra_slug=cfg.get("espn_fallback_slug"),
        )

    raw = _dedupe_by_pp(raw)
    raw = _filter_new(raw, league, existing)
    if not raw:
        return []
    return _rank_and_tier(raw, league)


def main() -> int:
    ap = argparse.ArgumentParser(description="Append missing soccer defense leagues to CSV cache.")
    ap.add_argument(
        "--csv",
        default=str(_CSV_PATH),
        help="Path to soccer_defense_summary.csv",
    )
    ap.add_argument(
        "--source",
        choices=("auto", "fbref", "espn"),
        default="auto",
        help="Data source: fbref first (auto), fbref-only, or espn-only",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print rows without writing CSV")
    args = ap.parse_args()

    csv_path = Path(args.csv).resolve()
    existing = _load_existing_keys(csv_path)
    print(f"Existing teams in CSV: {len(existing)} (pp_name+league keys)")

    session = _session() if args.source != "espn" else None
    all_new: list[dict[str, Any]] = []
    per_league: dict[str, int] = {}

    for cfg in LEAGUE_CONFIGS:
        league = cfg["league"]
        print(f"\n=== {league} ===")
        new_rows = expand_league(cfg, existing, args.source, session)
        per_league[league] = len(new_rows)
        for r in new_rows:
            existing.add((str(r["pp_name"]).upper(), league.lower()))
        all_new.extend(new_rows)

    print("\n--- Summary ---")
    for league, n in per_league.items():
        print(f"  {league}: {n} rows added")
    print(f"  Total new rows: {len(all_new)}")

    if not all_new:
        print("Nothing to append.")
        return 0

    csv_rows = _rows_to_csv_dicts(all_new)
    if args.dry_run:
        print("\n[dry-run] Sample rows (first 5 per league):")
        shown: dict[str, int] = {}
        for row in csv_rows:
            lg = row["league"]
            if shown.get(lg, 0) >= 5:
                continue
            shown[lg] = shown.get(lg, 0) + 1
            print(
                f"  {row['pp_name']:20} | {row['team_name']:28} | "
                f"gc/pg={row['goals_conceded_pg']} | rank={row['OVERALL_DEF_RANK']} | {row['DEF_TIER']}"
            )
        return 0

    write_header = not csv_path.is_file() or csv_path.stat().st_size == 0
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(csv_rows)

    print(f"\nAppended {len(csv_rows)} rows to {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
