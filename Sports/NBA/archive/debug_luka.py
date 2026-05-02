import pandas as pd
import unicodedata

def _normalize_name(name_str):
    if not isinstance(name_str, str):
        return ""
    nfkd = unicodedata.normalize('NFKD', name_str)
    clean = ''.join([c for c in nfkd if not unicodedata.combining(c)])
    return clean.strip().lower()

slate = pd.read_csv('step6c_with_schedule_flags.csv', low_memory=False)
cache = pd.read_csv('nba_espn_boxscore_cache.csv', low_memory=False)

# Test Luka specifically
print("Testing Luka matching:")
slate_luka = slate[slate['player'] == 'Luka Dončić'].iloc[0]
print(f"  Slate: {slate_luka['player']} (team={slate_luka['team']}, opp={slate_luka['opp_team']})")
print(f"  Normalized: '{_normalize_name(slate_luka['player'])}'")
print()

cache_luka = cache[cache['PLAYER'] == 'Luka Doncic']
print(f"Cache Lukas found: {len(cache_luka)}")
if len(cache_luka) > 0:
    print(f"  Teams in cache: {cache_luka['TEAM'].unique()}")
    print(f"  PLAYER_NORM values: {cache_luka['PLAYER_NORM'].unique()}")
    print()
    
    # Try matching
    player_norm = _normalize_name('Luka Dončić')
    team = slate_luka['team']
    
    print(f"Looking for: PLAYER_NORM='{player_norm}', TEAM='{team}'")
    match = cache_luka[cache_luka['TEAM'] == team]
    print(f"Found: {len(match)} rows")
    if len(match) > 0:
        print(match[['PLAYER', 'PLAYER_NORM', 'TEAM', 'GAME_DATE']].head(3))

