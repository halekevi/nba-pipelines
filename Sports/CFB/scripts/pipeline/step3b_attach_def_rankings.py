#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[4]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
from utils.defense_tiers import (  # noqa: E402
    assert_def_tier_column,
    def_tier_from_overall_rank,
    format_def_tier_counts,
    normalize_def_tier_label,
)

# Mapping from PrizePicks team abbreviations -> sr_name in cfb_def_rankings.csv
ABBR_TO_SR = {
    # ── Power conferences ─────────────────────────────────────────────
    'ALA':  'Alabama',           'ARK':  'Arkansas',
    'BC':   'Boston College',    'BUT':  'Butler',
    'CAL':  'California',        'COLO': 'Colorado',
    'CONN': 'Connecticut',       'CREI': 'Creighton',
    'DEP':  'DePaul',            'FLA':  'Florida',
    'GC':   'Grand Canyon',      'GMU':  'George Mason',
    'GONZ': 'Gonzaga',           'IOWA': 'Iowa',
    'JOES': "Saint Joseph's",    'KSU':  'Kansas State',
    'LSU':  'Louisiana State',   'MD':   'Maryland',
    'MISS': 'Mississippi',       'MSST': 'Mississippi State',
    'MSU':  'Michigan State',    'NEB':  'Nebraska',
    'ORE':  'Oregon',            'ORST': 'Oregon State',
    'OSU':  'Ohio State',        'PEPP': 'Pepperdine',
    'PITT': 'Pittsburgh',        'PORT': 'Portland',
    'PROV': 'Providence',        'PUR':  'Purdue',
    'SCU':  'Santa Clara',       'SDSU': 'San Diego State',
    'SEA':  'Seattle',           'SJU':  "St. John's (NY)",
    'SMC':  "Saint Mary's (CA)", 'SMU':  'Southern Methodist',
    'STAN': 'Stanford',          'TEX':  'Texas',
    'TXAM': "Texas A&M",         'UGA':  'Georgia',
    'UNLV': 'Nevada-Las Vegas',  'USD':  'San Diego',
    'USU':  'Utah State',        'VAN':  'Vanderbilt',
    'VILL': 'Villanova',         'WAKE': 'Wake Forest',
    'WIS':  'Wisconsin',         'XAV':  'Xavier',
    # ── Short aliases ─────────────────────────────────────────────────
    'UK':   'Kentucky',          'UNC':  'North Carolina',
    'KU':   'Kansas',            'IU':   'Indiana',
    'OU':   'Oklahoma',          'UVA':  'Virginia',
    'USC':  'Southern California','UCLA': 'UCLA',
    'DUKE': 'Duke',              'SYR':  'Syracuse',
    'MARQ': 'Marquette',         'NOVA': 'Villanova',
    'UT':   'Utah',              'MICH': 'Michigan',
    'PSU':  'Penn State',        'MSS':  'Mississippi State',
    'RUTG': 'Rutgers',           'NW':   'Northwestern',
    'MN':   'Minnesota',         'ILL':  'Illinois',
    # ── Previously missing (caused 259 miss rows) ─────────────────────
    'FAU':  'Florida Atlantic',  'WICH': 'Wichita State',
    'TEM':  'Temple',            'MEM':  'Memphis',
    'BRY':  'Bryant',            'UMBC': 'Maryland-Baltimore County',
    # ── Additional common PrizePicks abbrs ────────────────────────────
    'AFA':  'Air Force',         'AKR':  'Akron',
    'APP':  'Appalachian State', 'ARIZ': 'Arizona',
    'ARST': 'Arizona State',     'ASU':  'Arizona State',
    'AUB':  'Auburn',            'BALL': 'Ball State',
    'BAY':  'Baylor',            'BELM': 'Belmont',
    'BGSU': 'Bowling Green',     'BRAD': 'Bradley',
    'BYU':  'Brigham Young',     'BUFF': 'Buffalo',
    'CHAR': 'Charlotte',         'CIN':  'Cincinnati',
    'CLEM': 'Clemson',           'CLT':  'Charlotte',
    'COLST':'Colorado State',    'DAV':  'Davidson',
    'DAY':  'Dayton',            'DRK':  'Drake',
    'DRX':  'Drexel',            'DUQ':  'Duquesne',
    'ECU':  'East Carolina',     'ETSU': 'East Tennessee State',
    'FLA':  'Florida',           'FLST': 'Florida State',
    'FOR':  'Fordham',           'FRES': 'Fresno State',
    'FUR':  'Furman',            'GTWN': 'Georgetown',
    'GASO': 'Georgia Southern',  'GAST': 'Georgia State',
    'GT':   'Georgia Tech',      'HAW':  'Hawaii',
    'HOU':  'Houston',           'IDHO': 'Idaho',
    'ILST': 'Illinois State',    'INST': 'Indiana State',
    'IONA': 'Iona',              'IAST': 'Iowa State',
    'JKST': 'Jacksonville State','JMU':  'James Madison',
    'KENT': 'Kent State',        'LA':   'Louisiana',
    'LBST': 'Long Beach State',  'LIB':  'Liberty',
    'LOU':  'Louisville',        'LOY':  'Loyola (IL)',
    'LMU':  'Loyola Marymount',  'MRST': 'Marist',
    'MRSH': 'Marshall',          'MASS': 'Massachusetts',
    'MTSU': 'Middle Tennessee',  'MIZ':  'Missouri',
    'MIST': 'Missouri State',    'MON':  'Montana',
    'MOST': 'Montana State',     'MUR':  'Murray State',
    'NCST': 'NC State',          'NAU':  'Northern Arizona',
    'NEV':  'Nevada',            'NH':   'New Hampshire',
    'NM':   'New Mexico',        'NMST': 'New Mexico State',
    'NIU':  'Northern Illinois', 'NIU':  'Northern Illinois',
    'ND':   'Notre Dame',        'OAK':  'Oakland',
    'OHIO': 'Ohio',              'OKST': 'Oklahoma State',
    'ODU':  'Old Dominion',      'ORL':  'Oral Roberts',
    'PAC':  'Pacific',           'PENN': 'Pennsylvania',
    'RICE': 'Rice',              'RICH': 'Richmond',
    'RMR':  'Robert Morris',     'SAC':  'Sacramento State',
    'SAML': 'Sam Houston',       'SAMF': 'Samford',
    'SFU':  'San Francisco',     'SJST': 'San Jose State',
    'HALL': 'Seton Hall',        'SIEN': 'Siena',
    'SAL':  'South Alabama',     'SC':   'South Carolina',
    'SDAK': 'South Dakota',      'SDST': 'South Dakota State',
    'USF':  'South Florida',     'SOU':  'Southern',
    'SIU':  'Southern Illinois', 'STBN': 'St. Bonaventure',
    'STTH': 'St. Thomas',        'SFA':  'Stephen F. Austin',
    'STET': 'Stetson',           'TCU':  'TCU',
    'TENN': 'Tennessee',         'TNST': 'Tennessee State',
    'TNTC': 'Tennessee Tech',    'TLDO': 'Toledo',
    'TOWN': 'Towson',            'TROY': 'Troy',
    'TUL':  'Tulane',            'TLSA': 'Tulsa',
    'UAB':  'UAB',               'UCF':  'UCF',
    'UCSD': 'UC San Diego',      'UNO':  'New Orleans',
    'UTEP': 'UTEP',              'UTSA': 'UTSA',
    'UTAH': 'Utah',              'UTV':  'Utah Valley',
    'VCU':  'Virginia Commonwealth','VT': 'Virginia Tech',
    'WAG':  'Wagner',            'WASH': 'Washington',
    'WAST': 'Washington State',  'WEB':  'Weber State',
    'WVU':  'West Virginia',     'WKU':  'Western Kentucky',
    'WMU':  'Western Michigan',  'WYO':  'Wyoming',
    'YALE': 'Yale',
    # ── Missing entries (added 2026-03-12) ────────────────────────────
    'CSU':  'Colorado State',    'FSU':  'Florida State',
    'L-IL': 'Loyola (IL)',       'OKLA': 'Oklahoma',
    'SJSU': 'San Jose State',    'TULN': 'Tulane',
    'UNM':  'New Mexico',        'UNT':  'North Texas',
    # ── Missing entries (added 2026-03-13) ────────────────────────────
    # Alternate PrizePicks spellings for teams already mapped under different abbrs
    'TOL':  'Toledo',            # PrizePicks uses TOL; map had TLDO
    'SBON': "St. Bonaventure",   # PrizePicks uses SBON; map had STBN
    'ISU':  'Iowa State',        # PrizePicks uses ISU; map had IAST
    # Genuinely missing teams
    'GW':   'George Washington',
    'SLU':  'Saint Louis',
    'PV':   'Prairie View',        # DB uses PRAIRIE VIEW
    'MIA':  'Miami (FL)',
    'AAMU': 'Alabama A&M',
    # ── Missing entries (added 2026-03-16) ────────────────────────────
    'QUC':  'Quinnipiac',
    'LIU':  'Long Island University',
    'KENN': 'Kennesaw State',
    'CBU':  'California Baptist',
    'SIE':  'Siena',
    'NDSU': 'North Dakota State',
    'MCNS': 'McNeese State',
    'HP':   'High Point',
    'HOW':  'Howard',
    'HOF':  'Hofstra',
    'M-OH': 'Miami (OH)',
    'WRST': 'Wright State',
    'LEH':  'Lehigh',
    'MIZZ': 'Missouri',
    'TTU':  'Texas Tech',
    'UNI':  'Northern Iowa',
    'UCI':  'UC Irvine',
    # Primary value should match cfb_def_rankings.csv sr_name ("UNC Wilmington");
    # ABBR_ALTERNATES still lists legacy spellings.
    'UNCW': 'UNC Wilmington',
}

# PrizePicks sometimes ships only one side of a game (blank pp_opp_team for all rows).
# Map affected pp_game_id -> opponent's PrizePicks team abbr (must exist in ABBR_TO_SR).
PP_GAME_ID_FALLBACK_OPP_ABBR = {
    "145397": "GW",   # New Mexico vs George Washington (NIT 2026-03-22)
    "145399": "CAL",  # Saint Joseph's vs California (NIT 2026-03-21/22)
}


def norm_key(x) -> str:
    if pd.isna(x):
        return ""
    return str(x).strip().upper()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",   required=True)
    ap.add_argument("--defense", default="", help="Path to CBB defense rankings CSV (optional if DB is populated)")
    ap.add_argument("--output",  required=True)
    args = ap.parse_args()

    df = pd.read_csv(args.input, dtype=str).fillna("")

    # ── Load defense: DB direct read first, CSV fallback ────────────────────
    import sys as _sys, json as _json
    from pathlib import Path as _Path
    def_df = None

    _db_path = None
    _here = _Path(__file__).resolve().parent
    for _ in range(8):
        _candidate = _here / "data" / "cache" / "proporacle_ref.db"
        if _candidate.exists():
            _db_path = _candidate
            break
        _here = _here.parent

    if _db_path:
        try:
            import sqlite3 as _sq
            _con = _sq.connect(str(_db_path))
            _rows = _con.execute(
                "SELECT team, extra_json, def_tier, updated_at FROM defense WHERE sport='cbb' AND team IS NOT NULL"
            ).fetchall()
            _con.close()
            if len(_rows) >= 20:
                _records = []
                for _team, _xj, _tier, _updated in _rows:
                    _d = {}
                    try: _d = _json.loads(_xj) if _xj else {}
                    except Exception: pass
                    _records.append({
                        "sr_name":      _team,
                        "overall_rank": _d.get("overall_rank"),
                        "opp_ppg":      _d.get("opp_ppg"),
                        "def_tier":     _tier,
                    })
                def_df = pd.DataFrame(_records).fillna("")
                print(f"→ CBB defense loaded from DB ({len(def_df)} teams, updated {_rows[0][3]})")
            else:
                print("→ CBB defense DB empty — falling back to CSV")
        except Exception as _e:
            print(f"→ DB read failed ({_e}) — falling back to CSV")

    if def_df is None:
        if not args.defense:
            raise SystemExit("❌ No CBB defense data in DB and --defense not provided")
        print(f"→ Loading defense CSV: {args.defense}")
        def_df = pd.read_csv(args.defense, dtype=str).fillna("")

    # Detect columns in defense file
    rank_col = next((c for c in ["overall_rank","OVERALL_DEF_RANK","def_rank","rank"] if c in def_df.columns), None)
    ppg_col  = next((c for c in ["opp_ppg","def_ppg","ppg","opp_def_ppg"]            if c in def_df.columns), None)
    tier_col = next((c for c in ["def_tier","tier","opp_def_tier"]                   if c in def_df.columns), None)
    name_col = next((c for c in ["sr_name","team","school","team_name"]              if c in def_df.columns), None)

    if not name_col:
        raise SystemExit("Defense file has no recognizable team name column.")

    # Build lookup: sr_name (uppercase) -> payload
    by_sr = {}
    for _, r in def_df.iterrows():
        key = norm_key(r[name_col])
        # handle HTML entities
        key = key.replace("&AMP;", "&")
        by_sr[key] = {
            "rank": r[rank_col] if rank_col else None,
            "ppg":  r[ppg_col]  if ppg_col  else None,
            "tier": r[tier_col] if tier_col else None,
        }

    # Also normalise ABBR_TO_SR values to uppercase for lookup
    abbr_map = {k: v.upper().replace("&AMP;", "&") for k, v in ABBR_TO_SR.items()}

    _n_teams_cbb = 362
    if rank_col and rank_col in def_df.columns:
        _rnum_all = pd.to_numeric(def_df[rank_col], errors="coerce")
        _mx = _rnum_all.max()
        if pd.notna(_mx) and float(_mx) > 0:
            _n_teams_cbb = int(max(float(_mx), float(len(def_df))))

    # Alternate DB spellings tried when primary name misses
    ABBR_ALTERNATES = {
        'PV':   ['PRAIRIE VIEW', 'PRAIRIE VIEW A&M'],
        'MCNS': ['MCNEESE STATE', 'MCNEESE'],
        'LIU':  ['LONG ISLAND UNIVERSITY', 'LIU BROOKLYN', 'LONG ISLAND'],
        'KENN': ['KENNESAW STATE', 'KENNESAW ST.'],
        'M-OH': ['MIAMI (OH)', 'MIAMI OHIO', 'MIAMI (OHIO)'],
        'UCI':  ['UC IRVINE', 'CALIFORNIA-IRVINE', 'UC-IRVINE'],
        'CBU':  ['CALIFORNIA BAPTIST', 'CAL BAPTIST'],
        'UNCW': ['NORTH CAROLINA-WILMINGTON', 'UNC WILMINGTON', 'NC-WILMINGTON'],
    }

    opp_ranks, opp_ppg, opp_tiers = [], [], []
    misses = 0

    opp_col = "opp_team_abbr" if "opp_team_abbr" in df.columns else None

    for _, row in df.iterrows():
        abbr = norm_key(row.get(opp_col, "")) if opp_col else ""
        if not abbr and "pp_game_id" in df.columns:
            _gid = str(row.get("pp_game_id", "")).strip()
            _fb = PP_GAME_ID_FALLBACK_OPP_ABBR.get(_gid, "")
            if _fb:
                abbr = norm_key(_fb)
        sr   = abbr_map.get(abbr, "")
        payload = by_sr.get(sr) if sr else None

        # Try alternate DB spellings if primary missed
        if payload is None and abbr in ABBR_ALTERNATES:
            for alt in ABBR_ALTERNATES[abbr]:
                payload = by_sr.get(alt)
                if payload:
                    break

        # fallback: try matching pp_opp_team as full name directly
        if payload is None:
            for fc in ["pp_opp_team", "opp_team", "opponent"]:
                if fc in df.columns:
                    fn = norm_key(row.get(fc, "")).replace("&AMP;", "&")
                    payload = by_sr.get(fn)
                    if payload:
                        break

        if payload is None:
            misses += 1
            opp_ranks.append(None)
            opp_ppg.append(None)
            opp_tiers.append(None)
        else:
            rank_val = payload["rank"]
            tier_val = payload["tier"]
            rnum = pd.to_numeric(rank_val, errors="coerce")
            if pd.notna(rnum):
                tier_val = def_tier_from_overall_rank(int(rnum), _n_teams_cbb)
            elif tier_val:
                tier_val = normalize_def_tier_label(tier_val) or str(tier_val).strip()
            opp_ranks.append(rank_val)
            opp_ppg.append(payload["ppg"])
            opp_tiers.append(tier_val)

    df["opp_def_rank"]     = opp_ranks
    df["opp_def_ppg"]      = opp_ppg
    df["opp_def_tier"]     = opp_tiers
    df["def_tier"]         = opp_tiers
    df["OVERALL_DEF_RANK"] = opp_ranks

    _dt_mask = df["def_tier"].astype(str).str.strip().ne("") & df["def_tier"].notna()
    if _dt_mask.any():
        assert_def_tier_column(df.loc[_dt_mask], "def_tier", allow_empty=False)
    print(f"[CBB step3b] {format_def_tier_counts(df, 'def_tier')}")

    _rank_num = pd.to_numeric(df["OVERALL_DEF_RANK"], errors="coerce")
    miss_mask = _rank_num.isna()
    if miss_mask.any():
        _opp_col = "opp_team_abbr" if "opp_team_abbr" in df.columns else None
        if _opp_col:
            _unmatched = (
                df.loc[miss_mask, _opp_col]
                .dropna()
                .astype(str)
                .str.strip()
            )
            _unmatched = _unmatched[_unmatched.ne("")].unique()
            if len(_unmatched) > 0:
                print(
                    f"WARNING: {len(_unmatched)} teams unmatched in defense "
                    f"rankings: {_unmatched}"
                )

    df.to_csv(args.output, index=False)
    print(f"✅ Defense attached. Output={args.output} | rows={len(df)} | missing_def_rows={misses}")


if __name__ == "__main__":
    main()
