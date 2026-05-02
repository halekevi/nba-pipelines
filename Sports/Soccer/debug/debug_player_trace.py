import requests, json
from datetime import datetime, timedelta

headers = {"User-Agent": "Mozilla/5.0"}

# Alex Luna - Instituto - arg.1 - ESPN ID 148356
PLAYER_ID = "148356"
LEAGUE    = "arg.1"

def get_events(league, n_weeks=20):
    events = []
    seen   = set()
    today  = datetime.utcnow()
    for w in range(n_weeks):
        start = today - timedelta(weeks=w+1)
        end   = today - timedelta(weeks=w)
        dr    = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
        url   = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?limit=100&dates={dr}"
        r     = requests.get(url, headers=headers, timeout=15)
        data  = r.json()
        for ev in (data.get("events") or []):
            eid    = str(ev.get("id","")).strip()
            date   = str(ev.get("date","")).strip()
            stype  = ev.get("status",{}).get("type",{})
            completed = stype.get("completed", False) or stype.get("state","").lower() == "post"
            if eid and eid not in seen:
                seen.add(eid)
                events.append({"event_id": eid, "date": date, "completed": completed})
    events.sort(key=lambda x: x["date"], reverse=True)
    return events

print(f"Fetching {LEAGUE} events...")
events = get_events(LEAGUE, n_weeks=30)
print(f"Total events (including incomplete): {len(events)}")
print(f"Completed events: {sum(1 for e in events if e['completed'])}")
print(f"All events (first 10): {events[:10]}")

# Try to find Alex Luna in first 30 events regardless of completed flag
print(f"\nSearching for player {PLAYER_ID} in first 30 events...")
found = 0
for ev in events[:30]:
    eid  = ev["event_id"]
    url  = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{LEAGUE}/summary?event={eid}"
    data = requests.get(url, headers=headers, timeout=15).json()
    rosters = data.get("rosters", [])
    for roster in rosters:
        for entry in roster.get("roster", []):
            if str(entry.get("athlete", {}).get("id","")) == PLAYER_ID:
                print(f"  ✅ Found in event {eid} ({ev['date']}) completed={ev['completed']}")
                print(f"     Stats: {[s['name'] for s in entry.get('stats',[])[:5]]}")
                found += 1
                break
if found == 0:
    print(f"  ❌ Player {PLAYER_ID} not found in any of first 30 events")
    print(f"  Last 5 event dates: {[e['date'] for e in events[-5:]]}")
