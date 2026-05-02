import requests, json

headers = {"User-Agent": "Mozilla/5.0"}

# Get a recent arg.1 event
sb = requests.get(
    "https://site.api.espn.com/apis/site/v2/sports/soccer/arg.1/scoreboard?limit=10",
    headers=headers
).json()

ev  = sb["events"][0]
eid = ev["id"]
print(f"Event: {eid} - {ev['name']}")

# Fetch summary
s = requests.get(
    f"https://site.api.espn.com/apis/site/v2/sports/soccer/arg.1/summary?event={eid}",
    headers=headers
).json()

print(f"Summary keys: {list(s.keys())}")
rosters = s.get("rosters", [])
print(f"Rosters: {len(rosters)}")
if rosters:
    entries = rosters[0].get("roster", [])
    print(f"Entries: {len(entries)}")
    if entries:
        print(json.dumps(entries[0], indent=2)[:1000])
else:
    # Check if boxscore has something
    bs = s.get("boxscore", {})
    print(f"Boxscore keys: {list(bs.keys())}")
    print(json.dumps(bs, indent=2)[:1000])
