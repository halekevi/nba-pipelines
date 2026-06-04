# Cursor prompt: cross-book `implied_prob` via `line_movement`

**Files:** `utils/line_movement.py`, `scripts/combined_slate_tickets.py`, `utils/pipeline_read_enrichment.py`, sport step8 scripts (MLB/WNBA/NHL)

## CONTEXT

- `ODDS_API_KEY` is set in the environment (~491 requests remaining on free tier).
- `utils/line_movement.py` already fetches player prop snapshots from api.the-odds-api.com and joins `open_line` / `line_movement` / `line_direction_shift` to step5 rows via `enrich_with_line_movement()`.
- `_parse_odds_events()` currently extracts `point` (the line) but drops `over_price` / `under_price`. `_american_to_prob()` already exists.
- Bookmaker priority: DraftKings > FanDuel > BetMGM > …
- Join keys: `PIPELINE_PROP_TO_ODDS_MARKET` + `normalize_player_name`.
- Sports in scope: MLB, NHL, NBA, WNBA, Soccer (same as line movement today).
- Tennis: Odds API has no player props — leave `implied_prob` null, no error.
- Quota: snapshots cached under `cache/line_movement_*_{date}.json` — extend schema in place; one `force_refresh` pass backfills prices.

## TASK 1 — `utils/line_movement.py`

1. In `_parse_odds_events()`, from the priority bookmaker per event, extract `over_price` and `under_price` (American int) per `(player, market, line)` and store in the snapshot dict.
2. Reuse `_american_to_prob` as `_american_to_implied` (alias); return `None` on missing/0.
3. In `enrich_with_line_movement()`, add `implied_prob_over`, `implied_prob_under`, and direction-aware `implied_prob` (OVER/UNDER; default OVER).
4. Extend cache read/write for `over_price` / `under_price`; old caches without prices → `None` (backward compatible).
5. Add `force_refresh` and optional `date` to `fetch_line_snapshot` / `enrich_with_line_movement`.

## TASK 2 — step8 passthrough (MLB, WNBA, NHL)

Add `implied_prob`, `implied_prob_over`, `implied_prob_under` to keep lists, XLSX column widths, and NHL `build_display_row()`. Place adjacent to `open_line` / `line_movement`.

## TASK 3 — `combined_slate_tickets.py`

Rename map + `FULL_SLATE_COLS` + `FULL_SLATE_EXTRA_HDRS` / widths for all three implied columns.

## TASK 4 — `pipeline_read_enrichment.py`

- `cross_edge = hit_prob_selected - implied_prob` when both present.
- Add `implied_prob`, `cross_edge` to `READ_SLATE_EXPORT_KEYS` and header rename map.
- Informational only — not required for `pick_type_eligible`.

## VALIDATION

```powershell
py -3.14 -c "
from utils.line_movement import enrich_with_line_movement
import pandas as pd
df = pd.read_csv('outputs/2026-06-04/mlb/step5_mlb_hit_rates.csv')
out = enrich_with_line_movement(df, sport_key='baseball_mlb', markets=[
    'batter_hits','batter_total_bases','batter_rbis','batter_home_runs',
    'pitcher_strikeouts','pitcher_hits_allowed','pitcher_walks'],
    date='2026-06-04', force_refresh=True)
print('implied_prob fill:', out['implied_prob'].notna().sum(), '/', len(out))
print(out[out['implied_prob'].notna()][['player','prop_type','implied_prob',
      'implied_prob_over','implied_prob_under']].head(5).to_string())
"
```

Regen step8 MLB + WNBA + NHL, run `-CombinedOnly`, check Full Slate fill rates. Tennis should stay 0%.

**Do not** change `hit_prob_*` columns. **Do not** add Tennis step8 implied_prob.
