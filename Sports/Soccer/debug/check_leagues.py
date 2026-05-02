import urllib.request, json

url = 'https://api.prizepicks.com/leagues?state_code=NY&game_mode=pickem'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
j = json.loads(urllib.request.urlopen(req).read())
print("All leagues with props:")
for l in j.get('data', []):
    a = l.get('attributes', {})
    count = a.get('projections_count', 0)
    if count > 0:
        print(f"  ID={l['id']:>4}  props={count:>4}  {a['name']}")
