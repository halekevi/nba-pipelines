"""
Purge stale (all-zero) game log cache entries for stat types that were previously
broken due to missing SKATER_MAP / GOALIE_MAP entries in step5.

Run this ONCE before re-running step5 to force fresh fetches for any newly-fixed
stats. Currently covers (skaters): time_on_ice, plus/minus, power_play_points,
faceoffs_won, hits — and (goalies): goalie_saves, goalie_fantasy_score (the
prefixed keys were never present in GOALIE_MAP, so every cached game read 0.0
and forced UNDER 5/5 hit rates at every line).

Usage:
    py bust_gamelog_cache.py --cache cache\nhl_gamelog_cache.json
"""
import argparse
import json

STALE_STATS = {
    "time_on_ice", "plus/minus", "power_play_points", "faceoffs_won", "hits",
    "goalie_saves", "goalie_fantasy_score",
}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default="cache/nhl_gamelog_cache.json")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    with open(args.cache) as f:
        cache = json.load(f)

    to_delete = []
    for key, values in cache.items():
        parts = key.split(":")
        if len(parts) < 2:
            continue
        stat = parts[1]
        if stat in STALE_STATS:
            # Only purge if all values are 0.0 (i.e. was never populated correctly)
            if values and all(v == 0.0 for v in values):
                to_delete.append(key)

    print(f"Cache entries: {len(cache)} total, {len(to_delete)} stale to purge")
    for k in to_delete[:10]:
        print(f"  Removing: {k}")
    if len(to_delete) > 10:
        print(f"  ... and {len(to_delete)-10} more")

    if args.dry_run:
        print("\n[DRY RUN] No changes written.")
        return

    for k in to_delete:
        del cache[k]

    with open(args.cache, "w") as f:
        json.dump(cache, f)

    print(f"\n✅ Purged {len(to_delete)} stale entries. Cache now has {len(cache)} entries.")

if __name__ == "__main__":
    main()
