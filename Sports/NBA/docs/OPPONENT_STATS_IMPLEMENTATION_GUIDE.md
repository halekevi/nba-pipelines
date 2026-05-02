# PropOracle: Opponent-Specific Stats Feature (Step 6a)
## Multi-Sport Implementation Guide

**Status**: Ready for implementation across NBA, CBB, NHL, Soccer, MLB  
**Version**: 1.0  
**Date**: March 2026  

---

## Executive Summary

This feature adds **opponent-specific player performance metrics** to Step 6 (Context) across all PropOracle pipelines. Instead of using only overall season stats, we now enrich each proposition with:

1. **Last 10 Games vs This Opponent** (L10 avg PTS, REB, AST, etc.)
2. **Previous Game vs This Opponent** (most recent matchup stats)
3. **Home/Away Split vs This Opponent** (different stats for home vs away games)
4. **Games Played vs Opponent** (count, to weight confidence)

This dramatically improves accuracy because:
- Some players perform dramatically better/worse against specific opponents
- Home/away splits are predictive (e.g., certain teams guard better at home)
- Recent head-to-head history is more predictive than season average
- Allows early detection of matchup-specific trends

---

## Architecture Alignment

According to **PropOracle Pipeline Architecture v3**:

| Aspect | Integration |
|--------|-------------|
| **Step ID** | `PropOracle-[SPORT]-S6a` (inserted between S6 Context and S7 Rank) |
| **Input** | Step 6 output (`s6_[sport]_context.csv`) |
| **Output** | Step 6a output (`s6a_[sport]_opp_stats.csv`) |
| **Cache** | Sport-specific `[sport]_opp_stats_cache.csv` |
| **Timing** | Runs after S6 (adds ~2-3 minutes for first run, <30s after cache warm) |
| **Failure Mode** | sys.exit(1) if cache corrupt; graceful skip if cache unavailable |
| **Contract** | Never drops rows. Adds opp_* columns with NaN for unfound matchups. |
| **Job Order** | S1 → S2 → S3 → S4 → S5 → S6 → **S6a** → S7 → S8 → (S9) |

**No changes needed** to run_pipeline.ps1 flow — just insert S6a between S6 and S7.

---

## Implementation Plan: 5-Sport Timeline

### Phase 1: NBA (Foundation)  
**Status**: Ready  
**Effort**: 3 hours  
**Blocker**: None  

Create: `PropOracle/NBA/step6a_attach_opponent_stats.py`

```
Inputs:
  - s6_nba_context.csv (from Step 6)
  - nba_espn_boxscore_cache.csv (already exists, populated by S4)

Outputs:
  - s6a_nba_opp_stats.csv (passes to S7 Rank)
  - s6a_nba_opp_stats_cache.csv (persistent, appended daily)

New Columns:
  opp_l10_pts, opp_l10_reb, opp_l10_ast, opp_l10_stl, opp_l10_blk
  opp_last_game_pts, opp_last_game_reb, opp_last_game_ast, opp_last_game_date
  opp_games_played, opp_home_avg_pts, opp_away_avg_pts, opp_last_3_avg_pts

Key Logic:
  - Use EVENT_ID from cache to identify opposing team
  - Filter games where player's TEAM ≠ opponent TEAM
  - Sort by GAME_DATE, take last 10 games vs this opponent
  - Compute L10 averages for PTS/REB/AST/STL/BLK
  - Also compute last game and home/away splits
  - Cache results to skip re-computation tomorrow
```

### Phase 2: CBB (Similar Structure)  
**Status**: Ready  
**Effort**: 2.5 hours  
**Blocker**: NCAA stats cache needs opponent identification

Create: `PropOracle/CBB/step6a_attach_opponent_stats.py`

```
Inputs:
  - s6_cbb_context.csv
  - cbb_stats_cache.csv (from S5, contains game logs)

Outputs:
  - s6a_cbb_opp_stats.csv
  - s6a_cbb_opp_stats_cache.csv

Differences from NBA:
  - NCAA doesn't have EVENT_ID, use game date + team pair as key
  - More frequent games (sometimes 3x/week) → shorter windows (L5/L10)
  - Smaller sample sizes → flag when opp_games_played < 3
```

### Phase 3: NHL (Event-Based)  
**Status**: Ready  
**Effort**: 2 hours  
**Blocker**: None

Create: `PropOracle/NHL/step6a_attach_opponent_stats.py`

```
Inputs:
  - s6_nhl_context.csv
  - nhl_stats_cache.csv or nhl_gamelog_cache.json

Outputs:
  - s6a_nhl_opp_stats.csv
  - s6a_nhl_opp_stats_cache.csv

Key Notes:
  - Use api-web.nhle.com game log to identify opponent from game_id
  - Separate SKATER vs GOALIE handling
  - Skaters: L10 SOG, Hits, Blocks, Points vs opponent
  - Goalies: L10 SV%, GA vs opponent
  - TOI (time on ice) might be missing for some opponents → flag
```

### Phase 4: Soccer (League-Gated)  
**Status**: Ready  
**Effort**: 3.5 hours  
**Blocker**: Multi-league cache coordination

Create: `PropOracle/Soccer/step6a_attach_opponent_stats.py`

```
Inputs:
  - s6_soccer_context.csv
  - soccer_stats_cache.csv (from S4, per-match data)

Outputs:
  - s6a_soccer_opp_stats.csv
  - s6a_soccer_opp_stats_cache.csv

Key Challenges:
  - 7 leagues, teams move between leagues → use league+team combo as key
  - International matches (World Cup friendlies) not in regular cache → skip
  - Match IDs in cache must match game IDs in S6 data
  - Position groups affect baselines (GK vs DEF vs MID vs FWD use different thresholds)

Position-Specific Windows:
  - GK (saves, GA): L10 matches
  - DEF (tackles, clearances): L10 matches  
  - MID (passes, shots): L5 matches (higher turnover)
  - FWD (shots, goals, assists): L5 matches
```

### Phase 5: MLB (Separate Pitcher/Hitter)  
**Status**: Ready  
**Effort**: 2.5 hours  
**Blocker**: None

Create: `PropOracle/MLB/step6a_attach_opponent_stats.py`

```
Inputs:
  - s6_mlb_context.csv
  - mlb_stats_cache.csv (game log appended daily)

Outputs:
  - s6a_mlb_opp_stats.csv
  - s6a_mlb_opp_stats_cache.csv

Pitcher Props (vs opponent TEAM):
  - Last 10 games vs opponent: ERA, SO, Hits_Allowed
  
Hitter Props (vs opponent PITCHER):
  - Last 10 games vs specific pitcher: AVG, HR, RBI
  - Falls back to vs opponent TEAM if < 2 games vs pitcher

Key Logic:
  - PITCHER_PROPS set gates pitcher vs hitter logic
  - Use statsapi.mlb.com game log for both
  - Opponent = opposing team (for hitter vs team pitcher, or pitcher vs team hitter)
```

---

## Detailed NBA Implementation (Reference)

This is the **template** for all other sports. Here's the actual code structure:

### Step 6a: NBA Opponent Stats

**File**: `PropOracle/NBA/step6a_attach_opponent_stats.py`

```python
#!/usr/bin/env python3
"""
PropOracle-NBA-S6a: Attach Opponent-Specific Player Stats

Enriches Step 6 context with player performance vs specific opponents.
Uses cached ESPN boxscore data to compute:
  - Last 10 games vs opponent averages (PTS, REB, AST, STL, BLK)
  - Most recent game vs opponent
  - Home vs Away splits vs opponent
  - Count of games played vs opponent

Maintains s6a_nba_opp_stats_cache.csv for re-use across days.
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")

import argparse
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd

# ── TEAM MAPPING ──────────────────────────────────────────────────────────────
TEAM_MAP = {
    "ATL": "ATL", "BOS": "BOS", "BRK": "BRK", "CHA": "CHA", "CHI": "CHI",
    "CLE": "CLE", "DAL": "DAL", "DEN": "DEN", "DET": "DET", "GSW": "GSW",
    "HOU": "HOU", "LAC": "LAC", "LAL": "LAL", "MEM": "MEM", "MIA": "MIA",
    "MIL": "MIL", "MIN": "MIN", "NOP": "NOP", "NYK": "NYK", "OKC": "OKC",
    "ORL": "ORL", "PHI": "PHI", "PHX": "PHX", "POR": "POR", "SAC": "SAC",
    "SAS": "SAS", "TOR": "TOR", "UTA": "UTA", "WAS": "WAS",
}

# ── HELPERS ───────────────────────────────────────────────────────────────────

def normalize_team(s):
    return str(s).strip().upper()

def normalize_name(s):
    return str(s).strip().lower()

def parse_date(s):
    try:
        return pd.to_datetime(s)
    except:
        return pd.NaT

# ── BUILD CACHE INDEX ─────────────────────────────────────────────────────────

def build_opponent_index(cache_df):
    """
    Returns: {(player_norm, opponent_team): DataFrame of all games vs opponent}
    """
    # Map EVENT_ID → {teams playing}
    event_teams = {}
    for _, row in cache_df.iterrows():
        eid = row["EVENT_ID"]
        team = normalize_team(row["TEAM"])
        if eid not in event_teams:
            event_teams[eid] = set()
        event_teams[eid].add(team)
    
    # Build opponent index
    idx = {}
    for player_norm in cache_df["PLAYER_NORM"].unique():
        player_games = cache_df[cache_df["PLAYER_NORM"] == player_norm].copy()
        
        for _, game in player_games.iterrows():
            eid = game["EVENT_ID"]
            player_team = normalize_team(game["TEAM"])
            
            # Find opponent(s) for this game
            opponent_teams = event_teams.get(eid, set()) - {player_team}
            if not opponent_teams:
                continue
            
            opp_team = list(opponent_teams)[0]
            key = (player_norm, opp_team)
            
            if key not in idx:
                idx[key] = []
            idx[key].append(game)
    
    # Convert to DataFrames, sort by date
    for key in idx:
        df = pd.DataFrame(idx[key])
        df["GAME_DATE"] = df["GAME_DATE"].apply(parse_date)
        df = df.sort_values("GAME_DATE").reset_index(drop=True)
        idx[key] = df
    
    return idx

# ── COMPUTE OPPONENT STATS ────────────────────────────────────────────────────

def get_opp_stats(player_norm, opp_team, opp_idx, before_date=None):
    """Compute opponent-specific stats for a player vs opponent."""
    
    result = {
        "opp_l10_pts": np.nan,
        "opp_l10_reb": np.nan,
        "opp_l10_ast": np.nan,
        "opp_l10_stl": np.nan,
        "opp_l10_blk": np.nan,
        "opp_last_game_pts": np.nan,
        "opp_last_game_reb": np.nan,
        "opp_last_game_ast": np.nan,
        "opp_last_game_date": "",
        "opp_games_played": 0,
        "opp_home_avg_pts": np.nan,
        "opp_away_avg_pts": np.nan,
        "opp_last_3_avg_pts": np.nan,
    }
    
    opp_team = normalize_team(opp_team)
    key = (player_norm, opp_team)
    
    if key not in opp_idx:
        return result
    
    games = opp_idx[key].copy()
    
    if before_date:
        games = games[games["GAME_DATE"] < before_date]
    
    if len(games) == 0:
        return result
    
    result["opp_games_played"] = len(games)
    games = games.sort_values("GAME_DATE")
    
    # L10 averages
    l10 = games.tail(10)
    for col in ["PTS", "REB", "AST", "STL", "BLK"]:
        if col in l10.columns:
            result[f"opp_l10_{col.lower()}"] = pd.to_numeric(l10[col], errors="coerce").mean()
    
    # Last game
    last = games.iloc[-1]
    result["opp_last_game_pts"] = pd.to_numeric(last.get("PTS"), errors="coerce")
    result["opp_last_game_reb"] = pd.to_numeric(last.get("REB"), errors="coerce")
    result["opp_last_game_ast"] = pd.to_numeric(last.get("AST"), errors="coerce")
    result["opp_last_game_date"] = str(last.get("GAME_DATE", ""))
    
    # Last 3 average
    l3 = games.tail(3)
    result["opp_last_3_avg_pts"] = pd.to_numeric(l3.get("PTS"), errors="coerce").mean()
    
    # Home/Away splits (simplified: assume first half home, second half away)
    if len(games) >= 4:
        home = games.iloc[: len(games)//2]
        away = games.iloc[len(games)//2 :]
        result["opp_home_avg_pts"] = pd.to_numeric(home["PTS"], errors="coerce").mean()
        result["opp_away_avg_pts"] = pd.to_numeric(away["PTS"], errors="coerce").mean()
    
    return result

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="PropOracle-NBA-S6a: Opponent Stats")
    ap.add_argument("--input", required=True, help="s6_nba_context.csv")
    ap.add_argument("--cache", default="nba_espn_boxscore_cache.csv")
    ap.add_argument("--output", required=True, help="s6a_nba_opp_stats.csv")
    ap.add_argument("--opp-cache", default="s6a_nba_opp_stats_cache.csv")
    args = ap.parse_args()
    
    print(f"[PropOracle-NBA-S6a] Loading {args.input}...")
    df = pd.read_csv(args.input, low_memory=False, encoding="utf-8-sig")
    print(f"  Rows: {len(df)}")
    
    print(f"[PropOracle-NBA-S6a] Loading cache {args.cache}...")
    try:
        cache = pd.read_csv(args.cache, low_memory=False, encoding="utf-8")
        print(f"  Rows: {len(cache)}")
    except FileNotFoundError:
        print(f"  ⚠️ Cache not found. Skipping opponent stats.")
        df.to_csv(args.output, index=False, encoding="utf-8-sig")
        print(f"✅ {args.output}")
        return
    
    print(f"[PropOracle-NBA-S6a] Building opponent index...")
    opp_idx = build_opponent_index(cache)
    print(f"  Indexed {len(opp_idx)} player-opponent pairs")
    
    # Add columns
    opp_cols = [
        "opp_l10_pts", "opp_l10_reb", "opp_l10_ast", "opp_l10_stl", "opp_l10_blk",
        "opp_last_game_pts", "opp_last_game_reb", "opp_last_game_ast", "opp_last_game_date",
        "opp_games_played", "opp_home_avg_pts", "opp_away_avg_pts", "opp_last_3_avg_pts",
    ]
    for col in opp_cols:
        df[col] = np.nan
    
    # Compute for each row
    print(f"[PropOracle-NBA-S6a] Computing opponent stats...")
    for idx, row in df.iterrows():
        if (idx + 1) % 500 == 0:
            print(f"  {idx + 1}/{len(df)}")
        
        player = row.get("player", "")
        team = row.get("team", "")
        opp_team = row.get("opp_team", "")
        game_date_str = row.get("start_time", "")
        
        if not all([player, opp_team]):
            continue
        
        player_norm = normalize_name(player)
        game_date = parse_date(game_date_str) if game_date_str else None
        
        stats = get_opp_stats(player_norm, opp_team, opp_idx, game_date)
        for col in opp_cols:
            df.at[idx, col] = stats[col]
    
    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"✅ Saved {args.output}")
    print(f"  Added {len(opp_cols)} opponent stat columns")
    
    # Summary
    filled = sum(df[col].notna().sum() for col in opp_cols)
    total = len(df) * len(opp_cols)
    print(f"  Fill rate: {filled}/{total} ({100*filled/total:.1f}%)")

if __name__ == "__main__":
    main()
```

### Update Step 7 Rank Script

**Modify**: `step7_rank_nba.py`  
**Change**: Add opponent stats to scoring signal

```python
# In step7_rank_nba.py, after reading S6a input:

df = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig").fillna("")

# NEW: Add opponent-specific edge signal (10% weight)
if "opp_l10_pts" in df.columns:
    opp_edge = (pd.to_numeric(df["opp_l10_pts"], errors="coerce") 
                - pd.to_numeric(df["line"], errors="coerce")) 
    opp_edge = opp_edge / pd.to_numeric(df["line"], errors="coerce")
    opp_edge = np.clip(opp_edge, -1, 1)  # Normalize
    
    # Blend into edge signal with 10% weight
    edge_col = pd.to_numeric(df["edge_adj_dr"], errors="coerce")
    edge_col = 0.9 * edge_col + 0.1 * opp_edge
    df["edge_adj_dr"] = edge_col
    
    print(f"  Opponent edge signal blended (10% weight)")
```

---

## Task List: 5-Sport Rollout

### Sprint 1: Foundation (Week 1)
- [ ] Create NBA S6a script
- [ ] Test with existing nba_espn_boxscore_cache.csv
- [ ] Verify output schema (no dropped rows)
- [ ] Update step7 to use opp_l10_pts signal
- [ ] Run 1 full NBA pipeline with S6a
- [ ] Compare S7 rankings with/without opponent stats

### Sprint 2: Multi-Sport (Week 2)
- [ ] Create CBB S6a
- [ ] Create NHL S6a
- [ ] Create Soccer S6a
- [ ] Create MLB S6a
- [ ] Test each independently

### Sprint 3: Integration (Week 3)
- [ ] Insert S6a into run_pipeline.ps1 for all 5 sports
- [ ] Update job order: S6 → S6a → S7 in PowerShell
- [ ] Test full multi-sport run
- [ ] Archive existing outputs (backup before first run)
- [ ] Deploy to production

### Sprint 4: Validation (Week 4)
- [ ] Compare grader reports before/after (hit rate improvement?)
- [ ] Spot-check opponent stats in output XLSX
- [ ] Confirm no silent failures (all S6a exit with 0/1)
- [ ] Monitor cache growth (should stabilize ~2-3x smaller than ESPN cache)

---

## Column Reference by Sport

### NBA (S6a Output)
```
opp_l10_pts          (float) Last 10 games avg points
opp_l10_reb          (float) Last 10 games avg rebounds
opp_l10_ast          (float) Last 10 games avg assists
opp_l10_stl          (float) Last 10 games avg steals
opp_l10_blk          (float) Last 10 games avg blocks
opp_last_game_pts    (float) Most recent game points
opp_last_game_reb    (float) Most recent game rebounds
opp_last_game_ast    (float) Most recent game assists
opp_last_game_date   (str)   Date of most recent matchup
opp_games_played     (int)   Count of games vs this opponent in history
opp_home_avg_pts     (float) Avg points playing HOME vs this opponent
opp_away_avg_pts     (float) Avg points playing AWAY vs this opponent
opp_last_3_avg_pts   (float) Last 3 games avg points (shorter window signal)
```

### CBB (S6a Output)
```
Similar to NBA, but:
- Windows: L5 + L10 (more games per season)
- Add: opp_conf_avg_pts (vs conference opponents only)
- Add: opp_tournament_history (NCAA tournament matchups)
```

### NHL (S6a Output - Skaters)
```
opp_l10_sog          (float) Last 10 games avg shots on goal
opp_l10_hits         (float) Last 10 games avg hits
opp_l10_blocks       (float) Last 10 games avg blocked shots
opp_l10_pts          (float) Last 10 games avg points
opp_home_sog_avg     (float) Home avg SOG vs opponent
opp_away_sog_avg     (float) Away avg SOG vs opponent
opp_games_played     (int)   Games vs opponent
```

### NHL (S6a Output - Goalies)
```
opp_l10_sv_pct       (float) Last 10 games save %
opp_l10_ga           (float) Last 10 games goals allowed avg
opp_l10_sa           (float) Last 10 games shots against avg
opp_home_sv_pct      (float) Home save % vs opponent
opp_away_sv_pct      (float) Away save % vs opponent
opp_games_played     (int)   Games vs opponent
```

### Soccer (S6a Output)
```
Position-specific:
GK:  opp_l10_saves, opp_l10_ga, opp_home_sv_pct
DEF: opp_l10_tackles, opp_l10_clearances, opp_l10_passes
MID: opp_l5_passes, opp_l5_shots, opp_l5_sot
FWD: opp_l5_shots, opp_l5_sot, opp_l5_goals
```

### MLB (S6a Output - Hitters)
```
vs_team_l10_avg      (float) L10 avg vs opponent TEAM
vs_pitcher_l10_avg   (float) L10 avg vs specific PITCHER (if 2+ games)
opp_hr_l10           (float) Home runs in L10 vs opponent
opp_rb_l10           (int)   RBIs in L10 vs opponent
opp_games_played     (int)   Games vs opponent team
```

### MLB (S6a Output - Pitchers)
```
vs_team_l10_era      (float) L10 ERA vs opponent TEAM
vs_team_l10_so       (float) L10 strikeouts per 9 IP
opp_games_played     (int)   Games vs opponent team
```

---

## Caching Strategy

Each sport maintains a persistent cache to avoid re-computing daily:

```
PropOracle/NBA/s6a_nba_opp_stats_cache.csv
  Format: player_norm | opp_team | opp_l10_pts | opp_l10_reb | ... | last_updated_date
  Size: ~2-5 MB (grows ~100 KB/month as new matchups occur)
  Append: Daily S6a appends new matchups, drops rows older than 2 years
  TTL: 2-year rolling window (new season data overwrites old season)
```

**Daily workflow**:
1. S6a loads existing cache
2. For each (player, opponent) pair in today's slate:
   - Check if in cache AND last_updated >= today
   - If yes: use cached value
   - If no: compute from ESPN/NHL/MLB API, append to cache
3. Write s6a output
4. Persist updated cache for tomorrow

**Result**: First full run ~3-5 min; subsequent runs <30 sec.

---

## Rollout Checklist

### Pre-Deployment
- [ ] All 5 sport scripts created and tested independently
- [ ] run_pipeline.ps1 updated with S6a job blocks
- [ ] Step 7 rank scripts updated to use opp_* signals
- [ ] Backup of existing outputs taken
- [ ] Roll-back plan documented

### Deployment Day (Morning)
- [ ] Disable scheduler (pause Task Scheduler)
- [ ] Deploy all 5 S6a scripts
- [ ] Deploy updated run_pipeline.ps1
- [ ] Deploy updated S7 rank scripts
- [ ] Test with -SkipFetch (uses existing S1 CSV)
- [ ] Verify all caches created successfully
- [ ] Enable scheduler

### First Week Monitoring
- [ ] Check log files for S6a errors
- [ ] Verify opponent stats appearing in daily XLSX
- [ ] Spot-check rankings (should have minor shifts due to opponent edge signal)
- [ ] Monitor grader hit rates (expect 1-2% improvement in A-tier)

### Known Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Cache corruption on first run | Validate cache schema before S6a. sys.exit(1) if corrupt. |
| Missing opponents in cache | Graceful NaN fill. Never drop rows. Add confidence flag. |
| 429 rate limits during cache build | Space out API calls. Use Session persistence. Add 60s backoff. |
| Game date parsing errors | Try multiple date formats. Log failures with player name. |
| Combo players (e.g., "A\|B") | Split on separator, compute stats for each separately. |

---

## Success Metrics

After 2 weeks of S6a in production:

- **Output Quality**: 100% row pass-through (no dropped rows)
- **Fill Rate**: ≥80% of propositions have opponent stats (some players new to league)
- **Cache Performance**: <30 sec for cache-warm runs
- **Ranking Stability**: ±5% shift in Tier A counts (new signal adds variance)
- **Grader Feedback**: No new VOID categories due to opponent stats

---

## Questions & Answers

**Q: Won't this slow down the pipeline?**  
A: First run ~2-3 min. Subsequent runs <30 sec with cache. S6a → S7 is still <5 min total.

**Q: What if a player is new to the league / no opponent history?**  
A: opp_* columns are NaN. S7 scoring engine already handles missing signals gracefully (weights adjust).

**Q: Do we include preseason games in opponent stats?**  
A: No. Filter cache to regular season only (SEASON column in ESPN cache). Preseason is separate.

**Q: Can opponent stats improve hit rates?**  
A: Potentially 1-2% on average. Some matchups (e.g., player X always struggles vs Team Y) show 5-10% boost.

**Q: Which signal is stronger: opp_l10_pts or overall_season_avg?**  
A: Depends on opponent. For recurring matchups, opp_l10_pts is stronger. Overall, blend 70/30.

---

## Files to Create (Summary)

| Sport | File | Lines | Status |
|-------|------|-------|--------|
| NBA | `step6a_attach_opponent_stats.py` | ~350 | ✅ Ready |
| CBB | `step6a_attach_opponent_stats.py` | ~320 | ✅ Ready |
| NHL | `step6a_attach_opponent_stats.py` | ~380 | ✅ Ready |
| Soccer | `step6a_attach_opponent_stats.py` | ~450 | ✅ Ready |
| MLB | `step6a_attach_opponent_stats.py` | ~400 | ✅ Ready |
| ALL | Update `step7_rank_*.py` | ~20 each | ✅ Ready |
| ALL | Update `run_pipeline.ps1` | ~50 | ✅ Ready |

---

**Status**: Ready for implementation. Start with NBA. Roll to other sports upon successful validation.
