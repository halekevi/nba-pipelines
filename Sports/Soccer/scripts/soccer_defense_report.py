#!/usr/bin/env python3
"""
soccer_defense_report.py — REWRITTEN

Key changes vs original:
  - 13 leagues (added Argentina, Brazil, Liga MX, Eredivisie, Primeira Liga, Scottish Prem)
  - Outputs `pp_name` column = PrizePicks-style short name (e.g. LEIPZIG, CELTA, INTER MIAMI)
  - step3 merges on pp_name instead of team_name → fixes all the NaN defense mismatches
  - Master PP_NAME_MAP here (single source of truth)

Usage:
  py soccer_defense_report.py
  py soccer_defense_report.py --out soccer_defense_summary.csv
"""
from __future__ import annotations
import argparse
import sys
import time
import random
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from utils.defense_tiers import def_tier_from_overall_rank

ESPN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

LEAGUES = [
    ("eng.1",          "EPL"),
    ("eng.2",          "Championship"),
    ("uefa.champions", "UCL"),
    ("usa.1",          "MLS"),
    ("esp.1",          "La Liga"),
    ("ger.1",          "Bundesliga"),
    ("ita.1",          "Serie A"),
    ("fra.1",          "Ligue 1"),
    ("arg.1",          "Argentina"),
    ("bra.1",          "Brazil"),
    ("mex.1",          "Liga MX"),
    ("ned.1",          "Eredivisie"),
    ("por.1",          "Primeira Liga"),
    ("sco.1",          "Scottish Prem"),
    ("usa.nwsl",       "NWSL"),
    ("aus.1",          "A-League"),
    ("eng.w.1",        "WSL"),
    ("tur.1",          "Süper Lig"),
    ("gre.1",          "Super League Greece"),
]

ESPN_STANDINGS  = "https://site.api.espn.com/apis/v2/sports/soccer/{slug}/standings"
ESPN_TEAM_STATS = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/teams/{team_id}/statistics"

PP_NAME_MAP: Dict[str, str] = {
    # EPL
    "Manchester City": "MAN CITY", "Arsenal": "ARSENAL", "Liverpool": "LIVERPOOL",
    "Chelsea": "CHELSEA", "Tottenham Hotspur": "SPURS", "Manchester United": "MAN UNITED",
    "Newcastle United": "NEWCASTLE", "Aston Villa": "ASTON VILLA",
    "Brighton & Hove Albion": "BRIGHTON", "Brighton": "BRIGHTON",
    "West Ham United": "WEST HAM", "Brentford": "BRENTFORD", "Fulham": "FULHAM",
    "Crystal Palace": "CRYSTAL PALACE", "Wolverhampton Wanderers": "WOLVES",
    "Everton": "EVERTON", "Nottingham Forest": "NOTTM FOREST",
    "Bournemouth": "BOURNEMOUTH", "Ipswich Town": "IPSWICH",
    "Leicester City": "LEICESTER", "Southampton": "SOUTHAMPTON",
    # Bundesliga
    "Bayern Munich": "BAYERN MUNICH", "Bayer Leverkusen": "LEVERKUSEN",
    "Borussia Dortmund": "DORTMUND", "RB Leipzig": "LEIPZIG",
    "Eintracht Frankfurt": "FRANKFURT", "SC Freiburg": "FREIBURG",
    "Wolfsburg": "WOLFSBURG", "TSG Hoffenheim": "HOFFENHEIM",
    "Mainz": "MAINZ", "1. FSV Mainz 05": "MAINZ",
    "Werder Bremen": "WERDER BREMEN", "FC Augsburg": "AUGSBURG",
    "VfL Bochum": "BOCHUM", "Borussia Mönchengladbach": "GLADBACH",
    "1. FC Union Berlin": "UNION BERLIN", "VfB Stuttgart": "STUTTGART",
    "1. FC Heidenheim": "HEIDENHEIM", "FC St. Pauli": "ST. PAULI",
    "Holstein Kiel": "KIEL", "Hamburg SV": "HAMBURG",
    "FC Cologne": "KOLN", "1. FC Köln": "KOLN",
    # La Liga
    "Real Madrid": "REAL MADRID", "FC Barcelona": "BARCELONA", "Barcelona": "BARCELONA",
    "Atletico Madrid": "ATLETICO", "Atlético de Madrid": "ATLETICO",
    "Athletic Club": "ATHLETIC BILBAO", "Real Sociedad": "REAL SOCIEDAD",
    "Villarreal": "VILLARREAL", "Real Betis": "BETIS", "Valencia": "VALENCIA",
    "Osasuna": "OSASUNA", "Getafe": "GETAFE", "Celta Vigo": "CELTA",
    "Rayo Vallecano": "RAYO", "Sevilla": "SEVILLA", "RCD Mallorca": "MALLORCA",
    "Mallorca": "MALLORCA", "Girona": "GIRONA", "UD Las Palmas": "LAS PALMAS",
    "Las Palmas": "LAS PALMAS", "Deportivo Alaves": "ALAVES",
    "Real Valladolid": "VALLADOLID", "CD Leganes": "LEGANES", "RCD Espanyol": "ESPANYOL",
    # Serie A
    "Inter Milan": "INTER", "FC Internazionale Milano": "INTER",
    "AC Milan": "MILAN", "Juventus": "JUVENTUS", "Napoli": "NAPOLI",
    "SS Lazio": "LAZIO", "Lazio": "LAZIO", "AS Roma": "ROMA", "Roma": "ROMA",
    "Atalanta": "ATALANTA", "Fiorentina": "FIORENTINA", "Torino": "TORINO",
    "Bologna": "BOLOGNA", "Udinese": "UDINESE", "Genoa": "GENOA",
    "Hellas Verona": "VERONA", "Cagliari": "CAGLIARI", "Lecce": "LECCE",
    "Parma": "PARMA", "Empoli": "EMPOLI", "Venezia": "VENEZIA",
    "Como": "COMO", "AC Monza": "MONZA",
    # Ligue 1
    "Paris Saint-Germain": "PSG", "Olympique de Marseille": "MARSEILLE",
    "Marseille": "MARSEILLE", "Olympique Lyonnais": "LYON", "Lyon": "LYON",
    "AS Monaco": "MONACO", "Monaco": "MONACO", "Lille": "LILLE",
    "OGC Nice": "NICE", "Nice": "NICE", "Lens": "LENS", "RC Lens": "LENS",
    "Stade Rennais": "RENNES", "Rennes": "RENNES", "RC Strasbourg": "STRASBOURG",
    "Toulouse": "TOULOUSE", "Montpellier": "MONTPELLIER", "Nantes": "NANTES",
    "Stade de Reims": "REIMS", "Reims": "REIMS", "Le Havre": "LE HAVRE",
    "AS Saint-Etienne": "ST-ETIENNE", "Saint-Etienne": "ST-ETIENNE",
    "Angers": "ANGERS", "Auxerre": "AUXERRE",
    "Stade Brestois 29": "BREST", "Brest": "BREST",
    # MLS
    "Inter Miami CF": "INTER MIAMI", "Inter Miami": "INTER MIAMI",
    "LA Galaxy": "LA GALAXY", "Los Angeles FC": "LAFC", "LAFC": "LAFC",
    "Seattle Sounders FC": "SEATTLE", "Portland Timbers": "PORTLAND",
    "Colorado Rapids": "COLORADO", "New York City FC": "NYCFC",
    "New York Red Bulls": "NY RED BULLS", "Atlanta United FC": "ATLANTA UNITED",
    "Orlando City SC": "ORLANDO", "Orlando City": "ORLANDO",
    "Columbus Crew": "COLUMBUS", "Chicago Fire FC": "CHICAGO",
    "Toronto FC": "TORONTO", "CF Montréal": "MONTREAL",
    "New England Revolution": "NEW ENGLAND", "Philadelphia Union": "PHILADELPHIA",
    "D.C. United": "DC UNITED", "FC Cincinnati": "CINCINNATI",
    "Nashville SC": "NASHVILLE", "Charlotte FC": "CHARLOTTE",
    "Austin FC": "AUSTIN", "FC Dallas": "FC DALLAS",
    "Houston Dynamo FC": "HOUSTON", "Houston Dynamo": "HOUSTON",
    "Sporting Kansas City": "SPORTING KC", "Minnesota United FC": "MINNESOTA",
    "St. Louis City SC": "ST. LOUIS",
    "St. Louis CITY SC": "ST. LOUIS",
    "St. Louis City": "ST. LOUIS",
    "Vancouver Whitecaps FC": "VANCOUVER", "San Jose Earthquakes": "SAN JOSE",
    "San Diego FC": "SAN DIEGO",
    # EFL Championship
    "Middlesbrough": "MIDDLESBROUGH",
    "Birmingham City": "BIRMINGHAM",
    "Sunderland": "SUNDERLAND",
    "Leeds United": "LEEDS",
    # Argentina — pp_name must match Step 2 opp_team exactly (uppercased)
    "Boca Juniors": "BOCA JUNIORS",
    "River Plate": "RIVER",
    "Racing Club": "RACING",
    "Independiente": "INDEPENDIENTE",
    "San Lorenzo": "SAN LORENZO",
    "Estudiantes de La Plata": "ESTUDIANTES",
    "Estudiantes": "ESTUDIANTES",
    "Vélez Sársfield": "VÉLEZ",
    "Velez Sarsfield": "VÉLEZ",
    "Talleres": "TALLERES",
    "Talleres de Córdoba": "TALLERES",
    "Lanús": "LANÚS",
    "Lanus": "LANÚS",
    "Huracán": "HURACÁN",
    "Huracan": "HURACÁN",
    "Tigre": "TIGRE",
    "Defensa y Justicia": "DEF Y JUSTICIA",
    "Godoy Cruz": "GODOY CRUZ",
    "Belgrano": "BELGRANO",
    "Platense": "PLATENSE",
    "Club Atlético Platense": "PLATENSE",
    "Newell's Old Boys": "NEWELL'S",
    "Rosario Central": "ROSARIO",
    "Atlético Tucumán": "ATLETICO TUCUMAN",
    "Central Córdoba": "CENTRAL CÓRDOBA",
    "Instituto": "INSTITUTO",
    "Instituto Atlético Central Córdoba": "INSTITUTO",
    "Barracas Central": "BARRACAS",
    "Argentinos Juniors": "ARGENTINOS",
    "Unión": "UNIÓN",
    "Union de Santa Fe": "UNIÓN",
    "Riestra": "RIESTRA",
    "Deportivo Riestra": "RIESTRA",
    "Gimnasia y Esgrima La Plata": "GELP",
    "Gimnasia La Plata": "GELP",
    "Gimnasia (La Plata)": "GELP",
    "Gimnasia": "GELP",
    "Aldosivi": "ALDOSIVI",
    "Club Atlético Aldosivi": "ALDOSIVI",
    "Sarmiento": "SARMIENTO",
    "San Martín": "SAN MARTIN",
    # ESPN returns city-qualified names for Argentine teams — add both variants
    "Talleres (Córdoba)": "TALLERES",
    "Unión (Santa Fe)": "UNIÓN",
    "Vélez Sársfield": "VÉLEZ",
    "Vélez Sarsfield": "VÉLEZ",
    "Instituto (Córdoba)": "INSTITUTO",
    "Instituto Atlético Central Córdoba (Córdoba)": "INSTITUTO",
    "Barracas Central (Buenos Aires)": "BARRACAS",
    "Argentinos Juniors (Buenos Aires)": "ARGENTINOS",
    "Riestra (Buenos Aires)": "RIESTRA",
    "Deportivo Riestra": "RIESTRA",
    "River Plate (Buenos Aires)": "RIVER",
    "Boca Juniors (Buenos Aires)": "BOCA JUNIORS",
    "Independiente (Avellaneda)": "INDEPENDIENTE",
    "Racing Club (Avellaneda)": "RACING",
    "San Lorenzo (Buenos Aires)": "SAN LORENZO",
    "Estudiantes (La Plata)": "ESTUDIANTES",
    "Lanús (Lanús)": "LANÚS",
    "Huracán (Buenos Aires)": "HURACÁN",
    "Tigre (Victoria)": "TIGRE",
    "Defensa y Justicia (Florencio Varela)": "DEF Y JUSTICIA",
    "Godoy Cruz (Mendoza)": "GODOY CRUZ",
    "Belgrano (Córdoba)": "BELGRANO",
    "Platense (Buenos Aires)": "PLATENSE",
    "Newell's Old Boys (Rosario)": "NEWELL'S",
    "Rosario Central (Rosario)": "ROSARIO",
    "Atlético Tucumán (San Miguel de Tucumán)": "ATLETICO TUCUMAN",
    "Central Córdoba (Santiago del Estero)": "CENTRAL CÓRDOBA",
    "Aldosivi (Mar del Plata)": "ALDOSIVI",
    "Gimnasia y Esgrima (La Plata)": "GELP",
    "Sarmiento (Junín)": "SARMIENTO",
    # Brazil
    "Flamengo": "FLAMENGO", "Palmeiras": "PALMEIRAS", "Fluminense": "FLUMINENSE",
    "São Paulo FC": "SAO PAULO", "São Paulo": "SAO PAULO",
    "Sport Club Corinthians Paulista": "CORINTHIANS", "Corinthians": "CORINTHIANS",
    "Atlético Mineiro": "ATLETICO MG", "Internacional": "INTERNACIONAL",
    "Grêmio": "GREMIO", "Santos": "SANTOS", "Vasco da Gama": "VASCO",
    "Botafogo": "BOTAFOGO", "Cruzeiro": "CRUZEIRO",
    "Fortaleza": "FORTALEZA", "EC Bahia": "BAHIA", "Bahia": "BAHIA",
    # Liga MX
    "Club América": "AMERICA", "Cruz Azul": "CRUZ AZUL",
    "CD Guadalajara": "CHIVAS", "Tigres UANL": "TIGRES",
    "CF Monterrey": "MONTERREY", "Pachuca": "PACHUCA", "Toluca": "TOLUCA",
    "León": "LEON", "Pumas UNAM": "PUMAS", "Atlas": "ATLAS",
    "Necaxa": "NECAXA", "Mazatlán FC": "MAZATLAN",
    "FC Juárez": "JUAREZ", "Querétaro": "QUERETARO",
    # EFL Championship (additional)
    "Wrexham": "WREXHAM", "Swansea City": "SWANSEA",
    "West Bromwich Albion": "WEST BROM", "Sheffield United": "SHEFF UTD",
    "Preston North End": "PRESTON", "Burnley": "BURNLEY",
    "Blackburn Rovers": "BLACKBURN", "Hull City": "HULL",
    "Watford": "WATFORD", "Oxford United": "OXFORD",
    "Bristol City": "BRISTOL CITY", "Queens Park Rangers": "QPR",
    "Cardiff City": "CARDIFF", "Derby County": "DERBY",
    "Sheffield Wednesday": "SHEFF WED", "Portsmouth": "PORTSMOUTH",
    "Norwich City": "NORWICH", "Luton Town": "LUTON",
    "Plymouth Argyle": "PLYMOUTH", "Millwall": "MILLWALL",
    "Stoke City": "STOKE CITY", "Coventry City": "COVENTRY CITY",
    # Saudi Pro League
    "Al-Ittihad Club": "ITTIHAD", "Al Ittihad": "ITTIHAD",
    "Al-Nassr FC": "NASSR", "Al Nassr": "NASSR",
    "Al-Hilal SFC": "HILAL", "Al Hilal": "HILAL",
    "Al-Ahli Saudi FC": "AHLI", "Al Ahli": "AHLI",
    "Al-Qadsiah FC": "QADSIAH", "Al Qadsiah": "QADSIAH",
    "Al-Ettifaq FC": "ETTIFAQ", "Al Ettifaq": "ETTIFAQ",
    "Al Riyadh": "RIYADH", "Al-Riyadh SC": "RIYADH",
    "Al Fayha": "FAYHA", "Al-Fayha": "FAYHA",
    "Al Shabab": "SHABAB", "Al-Shabab FC": "SHABAB",
    "Al Fateh": "FATEH", "Al-Fateh SC": "FATEH",
    "Al Qadisiyah": "QADISIYAH",
    "Al Hazem": "HAZEM", "Al Khaleej": "KHALEEJ",
    "Al Ta'ee": "TAEE", "Al Okhdood": "OKHDOOD",
    "Damac FC": "DAMAC", "Al Wehda": "WEHDA",
    # NWSL
    "Washington Spirit": "SPIRIT", "Portland Thorns FC": "THORNS",
    "Portland Thorns": "THORNS",
    "North Carolina Courage": "NC COURAGE",
    "OL Reign FC": "REIGN", "OL Reign": "REIGN",
    "Chicago Red Stars": "RED STARS",
    "Orlando Pride": "ORLANDO PRIDE",
    "Houston Dash": "HOUSTON DASH",
    "San Diego Wave FC": "SD WAVE", "San Diego Wave": "SD WAVE",
    "Angel City FC": "ANGEL CITY",
    "Gotham FC": "GOTHAM", "NJ/NY Gotham FC": "GOTHAM",
    "Kansas City Current": "KC CURRENT",
    "Racing Louisville FC": "LOUISVILLE",
    "Bay FC": "BAY FC",
    # A-League (Australia)
    "Perth Glory": "PERTH", "Wellington Phoenix": "WELLINGTON",
    "Melbourne City FC": "MELBOURNE CITY",
    "Melbourne Victory": "MELBOURNE VICTORY",
    "Sydney FC": "SYDNEY", "Western Sydney Wanderers": "WSW",
    "Brisbane Roar": "BRISBANE", "Adelaide United": "ADELAIDE",
    "Macarthur FC": "MACARTHUR", "Central Coast Mariners": "CENTRAL COAST",
    "Newcastle Jets": "NEWCASTLE JETS", "Western United": "WESTERN UNITED",
    "Auckland FC": "AUCKLAND",
    # Süper Lig (Turkey)
    "Galatasaray": "GALATASARAY", "Fenerbahçe": "FENERBAHCE",
    "Besiktas JK": "BESIKTAS", "Beşiktaş": "BESIKTAS",
    "Trabzonspor": "TRABZONSPOR",
    # Super League Greece
    "Olympiacos": "OLYMPIACOS", "Panathinaikos": "PANATHINAIKOS",
    "PAOK": "PAOK", "AEK Athens": "AEK",
}


def _pp_name(display_name: str) -> str:
    if display_name in PP_NAME_MAP:
        return PP_NAME_MAP[display_name]
    n = display_name.upper().strip()
    for suffix in (" FC", " SC", " CF", " AC", " SV", " FK", " SK", " AFC"):
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
    return n


def _get(url: str, retries: int = 3) -> Optional[dict]:
    for attempt in range(1, retries + 1):
        try:
            time.sleep(0.3 + random.uniform(0, 0.2))
            r = requests.get(url, headers=ESPN_HEADERS, timeout=20)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt < retries:
                time.sleep(2.0 * attempt)
    return None


def fetch_league_defense(slug: str, league_name: str) -> List[dict]:
    data = _get(ESPN_STANDINGS.format(slug=slug))
    if not data:
        print(f"  ⚠️  {league_name}: no standings data")
        return []

    rows = []
    groups = (
        data.get("standings", {}).get("groups")
        or data.get("children")
        or [data]
    )

    for group in groups:
        entries = (
            group.get("standings", {}).get("entries")
            or group.get("entries")
            or []
        )
        for entry in entries:
            ti        = entry.get("team", {})
            team_id   = str(ti.get("id", "")).strip()
            team_name = str(ti.get("displayName", ti.get("name", ""))).strip()

            stats: Dict[str, float] = {}
            for s in (entry.get("stats") or []):
                try:
                    stats[str(s.get("name","")).lower()] = float(s.get("value",""))
                except (TypeError, ValueError):
                    pass

            gp    = int(stats.get("gamesplayed", stats.get("gp", 0)) or 0)
            gc    = float(stats.get("pointsagainst", stats.get("goalsagainst", 0)) or 0)
            cs    = int(stats.get("cleansheets", 0) or 0)
            wins  = int(stats.get("wins", 0) or 0)
            draws = int(stats.get("draws", 0) or 0)
            losses= int(stats.get("losses", 0) or 0)

            rows.append({
                "team_name":         team_name,
                "pp_name":           _pp_name(team_name),
                "team_id":           team_id,
                "league":            league_name,
                "gp":                gp,
                "goals_conceded":    gc,
                "goals_conceded_pg": round(gc / max(gp, 1), 3),
                "clean_sheets":      cs,
                "wins":              wins,
                "draws":             draws,
                "losses":            losses,
                "shots_conceded_pg": None,
            })

    # Best-effort shots_conceded_pg from team stats
    for row in rows:
        tid = row.get("team_id", "")
        if not tid:
            continue
        sd = _get(ESPN_TEAM_STATS.format(slug=slug, team_id=tid))
        if not sd:
            continue
        for cat in (sd.get("splits", {}).get("categories") or []):
            for stat in (cat.get("stats") or []):
                n = str(stat.get("name", "")).lower()
                if any(x in n for x in ("shotsagainst", "shots against")):
                    try:
                        row["shots_conceded_pg"] = float(stat.get("value", ""))
                    except (TypeError, ValueError):
                        pass

    return rows


MIN_GP = 3


def add_ranks_and_tiers(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for league, grp in df.groupby("league"):
        g      = grp.copy().reset_index(drop=True)
        gp_num = pd.to_numeric(g["gp"], errors="coerce").fillna(0)
        ok     = gp_num >= MIN_GP
        n      = int(ok.sum())
        gcpg   = pd.to_numeric(g["goals_conceded_pg"], errors="coerce")
        ranked = gcpg.where(ok, other=float("nan"))
        g["OVERALL_DEF_RANK"] = ranked.rank(method="min", ascending=True).astype("Int64")
        mid = max(1, round((n + 1) / 2)) if n > 0 else 1
        g.loc[~ok, "OVERALL_DEF_RANK"] = mid

        def _tier(r, n=n):
            if n < 2:
                return "Avg"
            return def_tier_from_overall_rank(r, n)

        g["DEF_TIER"] = g.apply(
            lambda row: "Avg" if gp_num[row.name] < MIN_GP else _tier(row["OVERALL_DEF_RANK"]),
            axis=1
        )
        out.append(g)
    return pd.concat(out, ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="soccer_defense_summary.csv")
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    all_rows: List[dict] = []
    for slug, lname in LEAGUES:
        print(f"📡 Fetching {lname} ({slug})...")
        rows = fetch_league_defense(slug, lname)
        if rows:
            all_rows.extend(rows)
            print(f"  ✅ {lname}: {len(rows)} teams")
        else:
            print(f"  ⚠️  {lname}: no data")
        time.sleep(random.uniform(0.4, 0.8))

    if not all_rows:
        print("❌ No data fetched.")
        return

    df = pd.DataFrame(all_rows)
    df = add_ranks_and_tiers(df)

    front = ["team_name", "pp_name", "league", "gp",
             "goals_conceded", "goals_conceded_pg", "shots_conceded_pg",
             "clean_sheets", "wins", "draws", "losses",
             "OVERALL_DEF_RANK", "DEF_TIER"]
    cols = front + [c for c in df.columns if c not in front and c != "team_id"]
    df   = df[[c for c in cols if c in df.columns]]

    try:
        df.to_csv(args.out, index=False, encoding="utf-8-sig")
    except PermissionError:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        alt   = args.out.replace(".csv", f"_{stamp}.csv")
        df.to_csv(alt, index=False, encoding="utf-8-sig")
        args.out = alt

    print("\n" + "=" * 60)
    print("BEST DEFENSES (goals conceded/game)")
    print("=" * 60)
    for league, grp in df.groupby("league"):
        top = grp.sort_values("OVERALL_DEF_RANK").head(args.top)[
            ["pp_name", "OVERALL_DEF_RANK", "DEF_TIER", "goals_conceded_pg", "clean_sheets", "gp"]
        ]
        print(f"\n{league}")
        print(top.to_string(index=False))

    print(f"\n✅ Saved → {args.out}  ({len(df)} teams, {df['league'].nunique()} leagues)")
    print(f"Sample pp_names: {df['pp_name'].head(10).tolist()}")

    # ── Write to proporacle_ref.db ───────────────────────────────────────────────
    try:
        import sys as _sys
        from pathlib import Path as _Path
        # Search up to 6 levels for scripts/defense_db.py
        _here = _Path(__file__).resolve().parent
        for _ in range(6):
            if (_here / "scripts" / "defense_db.py").exists():
                _sys.path.insert(0, str(_here / "scripts"))
                break
            _here = _here.parent
        from defense_db import write_defense_to_db
        # Soccer uses pp_name as team key — write both pp_name and team columns
        # so load_defense_from_db can find either
        df_db = df.copy()
        df_db["pp_name"] = df_db["pp_name"].astype(str).str.strip().str.upper()
        df_db["team"]    = df_db["pp_name"]   # team = pp_name for soccer
        write_defense_to_db(df_db, sport="soccer")
        print(f"  ✅ defense_db: {len(df_db)} soccer teams written to DB")
    except Exception as _e:
        print(f"  ⚠️  Could not write to DB: {_e}")


if __name__ == "__main__":
    main()
