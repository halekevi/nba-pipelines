# Payout Ladder Observations Backlog

**Source:** manual PrizePicks screenshots, April 2026  
**Status:** captured but not yet added as distance-keyed ladder rows  
**Format:** add to `payout_ladder.json` with `goblin_distances` once standard_line distances are confirmed from step8 output.

## How to promote to ladder

1. Run: `py -3.14 scripts\find_calibration_candidates.py`
2. Find the player in goblin candidates — note distance
3. Add row to `data/payout_ladder.json` with `goblin_distances` field
4. Re-run: `py -3.14 scripts\combined_slate_tickets.py --write-web`

## Observations

### 3-Leg All Goblin

| date       | flex_sweep | flex_partial | power_first | notes |
|------------|------------|--------------|-------------|-------|
| 2026-04-13 | 3.0x       | 0.5x         | 3.75x       | 3 goblin, distances unknown — need step8 confirmation |

### 2 Goblin + 1 Standard (flex)

| date       | flex_sweep | flex_partial | power_first | goblin_distances | notes |
|------------|------------|--------------|-------------|------------------|-------|
| 2026-04-13 | varies     | varies       | varies      | TBD              | same mix, different payouts by line — need distance keys |

### 1 Demon + 1 Goblin + 1 Standard

| date       | flex_sweep | flex_partial | goblin_dist | demon_dist | notes |
|------------|------------|--------------|-------------|------------|-------|
| 2026-04-13 | varies     | varies       | TBD         | TBD        | demon distance drives large payout swings |

## Priority queue (fill these next)

1. 2g1s: goblin_distances=[1,2] — most common NBA shape
2. 2g1s: goblin_distances=[2,3]
3. 1g2s: goblin_distances=[1]
4. 1g2s: goblin_distances=[2]
5. 3g: goblin_distances=[1,1,1]
6. 3g: goblin_distances=[2,2,2]
7. 1d1g1s: capture demon_distance when available

The priority queue in this file is your weekly calibration target. Each session with `find_calibration_candidates.py` → PrizePicks → log → promote one row from the backlog. After filling rows 1-4 you'll have exact coverage for the most common NBA goblin ticket shapes.
