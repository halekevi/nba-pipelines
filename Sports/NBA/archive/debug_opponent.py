import pandas as pd

cache = pd.read_csv('nba_espn_boxscore_cache.csv', low_memory=False)

# Build opponent lookup (same as script)
lookup = {}
for event_id, group in cache.groupby('EVENT_ID'):
    teams = group['TEAM'].unique()
    if len(teams) == 2:
        team_a, team_b = teams[0], teams[1]
        lookup[(event_id, team_a)] = team_b
        lookup[(event_id, team_b)] = team_a

print(f"Total opponent mappings: {len(lookup)}")
print()

# Check Luka's games
luka_games = cache[cache['PLAYER'] == 'Luka Doncic']
print(f"Luka games in cache: {len(luka_games)}")
print()

# Check each game for opponent
print("Luka LAL games and their opponents:")
for _, row in luka_games[luka_games['TEAM'] == 'LAL'].iterrows():
    event_id = row['EVENT_ID']
    team = row['TEAM']
    opponent = lookup.get((event_id, team))
    game_date = row['GAME_DATE']
    print(f"  {game_date} | Event {event_id} | LAL vs {opponent}")

