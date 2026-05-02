import pandas as pd

slate = pd.read_csv('step6c_with_schedule_flags.csv', low_memory=False)
cache = pd.read_csv('nba_espn_boxscore_cache.csv', low_memory=False)

print("Sample slate players:")
print(slate[['player', 'is_combo_player']].head(10))
print()

print("Sample cache players:")
print(cache[['PLAYER', 'PLAYER_NORM']].drop_duplicates().head(10))
print()

# Check combo players
combos = slate[slate['is_combo_player'] == True]
print(f"Combo players in slate: {len(combos)}")
print(f"Single players: {len(slate[slate['is_combo_player'] != True])}")

