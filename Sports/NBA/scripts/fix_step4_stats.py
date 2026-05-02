import argparse, unicodedata, pandas as pd

def remove_accents(name):
    if not isinstance(name, str): return name
    nfkd = unicodedata.normalize("NFKD", name)
    return "".join([c for c in nfkd if not unicodedata.combining(c)])

def normalize_name(name):
    return remove_accents(name).strip() if isinstance(name, str) else name

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--cache", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()

print(f"Loading {args.input}...")
step4 = pd.read_csv(args.input)
print(f"  {len(step4)} rows")

print(f"Loading {args.cache}...")
cache = pd.read_csv(args.cache)
print(f"  {len(cache)} rows")

print("\nBuilding normalized cache lookup...")
cache["PLAYER_NORM"] = cache["PLAYER"].apply(normalize_name)
cache["TEAM_NORM"] = cache["TEAM"].fillna("").str.strip().str.upper()

cache_lookup = cache.groupby(["PLAYER_NORM", "TEAM_NORM", "GAME_DATE"]).agg({"PTS": "mean", "REB": "mean"}).reset_index()
cache_lookup.columns = ["PLAYER_NORM", "TEAM_NORM", "GAME_DATE", "season_avg", "last_5_avg"]

print(f"  Created lookup with {len(cache_lookup)} combinations")

print("\nMatching step4 rows to cache...")
matched = 0
for idx, row in step4.iterrows():
    if pd.notna(row.get("season_avg")) and row.get("season_avg") != "": 
        continue
    
    player_norm = normalize_name(row["player"])
    team = str(row.get("team", "")).strip().upper()
    
    matches = cache_lookup[(cache_lookup["PLAYER_NORM"] == player_norm) & (cache_lookup["TEAM_NORM"] == team)]
    
    if len(matches) > 0:
        m = matches.iloc[-1]
        step4.at[idx, "season_avg"] = m["season_avg"]
        step4.at[idx, "last_5_avg"] = m["last_5_avg"]
        matched += 1

print(f"  ✓ Matched {matched} rows")

print(f"\nSaving {args.output}...")
step4.to_csv(args.output, index=False)
print(f"  ✓ Done")
