# Payout Ladder Observations Backlog

**Source:** manual PrizePicks screenshots, April 2026  
**Status:** captured but not yet added as distance-keyed ladder rows  
**Format:** add to `payout_ladder.json` with `goblin_distances` once standard_line distances are confirmed from step8 output.

## How to promote to ladder

1. Run: `py -3.14 scripts\find_calibration_candidates.py`
2. Find the player in goblin candidates — note distance
3. Add row to `data/payout_ladder.json` with `goblin_distances` field
4. Re-run: `py -3.14 scripts\combined_slate_tickets.py --write-web`

For Leaderboard/reverted payout screenshots, keep rows in `data/payout_ladder_reverted.json` and run with `--use-reverted-ladder`.

## Observations

### 6-Leg Power (Leaderboard / Reverted-Payout UI)

| date       | composition | observed_multiplier | notes |
|------------|-------------|---------------------|-------|
| 2026-04-30 | likely 0S+6G+0D | 3.0x (`$20 -> $60`) | Screenshot shows "Leaderboard" and "Learn more about lineups with reverted payouts" banner. Treat as special mode; do **not** promote to canonical `payout_ladder.json` without confirming standard (non-reverted) slip context and line deltas. |
| 2026-04-30 | likely 1S+5G+0D | 4.5x (`$20 -> $90`) | Same caveat: leaderboard/reverted context may override normal ladder payouts. Keep as observation only. |
| 2026-04-30 | likely mixed 6-leg | 10.5x (`$20 -> $210`) | Same caveat: observation captured from leaderboard view; requires non-reverted counterpart before ladder promotion. |

### 2-Leg Power (1 Standard + 1 Goblin)

| date       | goblin_distances | sweep | min_guarantee | source       | notes |
|------------|------------------|-------|---------------|--------------|-------|
| 2026-04-30 | [0]              | 3.0x  | 2.7x          | pp_observed  | Confirmed from live slip UI; promoted as exact distance-keyed row. |
| 2026-04-30 | [1]              | 3.0x  | 2.5x          | pp_estimated | Added to complete distance-keyed ladder coverage; replace with observed slip when captured. |
| 2026-04-30 | [2]              | 3.0x  | 2.3x          | pp_estimated | Added to complete distance-keyed ladder coverage; replace with observed slip when captured. |
| 2026-04-30 | [3]              | 3.0x  | 2.1x          | pp_estimated | Added to complete distance-keyed ladder coverage; replace with observed slip when captured. |

### 3-Leg All Goblin

| date       | flex_sweep | flex_partial | power_first | power_3/3 | notes |
|------------|------------|--------------|-------------|-----------|-------|
| 2026-05-27 | 3.0x       | 1.7x (2/3)   | 6.0x        | 2.0x      | WNBA **reversion** slip: Clark 17.5 G / std 19.5 (Δ2), Thomas 11.5 G / std 16.5 (Δ5), Copper 14.5 G / std 19 (Δ4.5). `goblin_distances` **[2, 4.5, 5]**. |
| 2026-05-27 | —          | —            | —           | **3.75x** | WNBA **standard** 2S+1G Power (submitted): Gray 4.5 2PT S, Howard 11.5 PTS G (std **16.5**, Δ5), Carter 15.5 PTS S. Promoted to `payout_ladder.json` `[5]`. |
| 2026-04-13 | 3.0x       | 0.5x         | 3.75x       | —         | 3 goblin, distances unknown — need step8 confirmation |

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
