import json, csv, urllib.request

# Get Kaprizov's NHL ID
nhl_id = None
with open('NHL/outputs/step4_nhl_with_stats.csv') as f:
    for row in csv.DictReader(f):
        if 'kaprizov' in row.get('player_name','').lower():
            nhl_id = row.get('nhl_player_id')
            break

print('NHL ID:', nhl_id)

# What the cache has
with open('NHL/cache/nhl_gamelog_cache.json') as f:
    cache = json.load(f)
key = nhl_id + ':shots_on_goal:20252026'
print('Cache key:', key)
print('Cached values:', cache.get(key, 'NOT FOUND'))

# What the API actually returns
url = 'https://api-web.nhle.com/v1/player/' + nhl_id + '/game-log/20252026/2'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=15) as r:
    data = json.load(r)
games = data.get('gameLog', [])[:10]

print()
print('API last 10 shots values:')
for g in games:
    print(' ', g['gameDate'], ' shots=', g.get('shots', 'MISSING'))
