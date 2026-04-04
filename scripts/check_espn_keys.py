"""
check_espn_keys.py
Run this once to print the actual stat key names ESPN uses in boxscores.
"""
import requests, json

HEADERS = {"User-Agent": "Mozilla/5.0"}

# Step 1: get a recent game ID
sb = requests.get(
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=20260312",
    headers=HEADERS, timeout=10
).json()

game_id = sb["events"][0]["id"]
print(f"Using game: {sb['events'][0]['name']} (id={game_id})\n")

# Step 2: fetch boxscore
data = requests.get(
    f"https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={game_id}",
    headers=HEADERS, timeout=15
).json()

# Step 3: print all keys and one player's raw stats
for team_block in data.get("boxscore", {}).get("players", []):
    team = team_block.get("team", {}).get("abbreviation", "")
    for stat_group in team_block.get("statistics", []):
        keys = stat_group.get("keys", [])
        athletes = stat_group.get("athletes", [])
        # Find first player with actual stats
        for ath in athletes:
            stats = ath.get("stats", [])
            if stats and stats[0] != "DNP" and len(stats) == len(keys):
                name = ath["athlete"]["displayName"]
                print(f"=== {team} — {name} ===")
                for k, v in zip(keys, stats):
                    print(f"  {k:40s} = {v}")
                print()
                break
    break  # just show one team
