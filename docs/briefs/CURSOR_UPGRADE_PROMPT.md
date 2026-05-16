# PropORACLE System Upgrade — Full Cursor Prompt

> Generated from live browser audit of `web-production-f280f.up.railway.app` on 2026-05-15.
> Covers NBA, NHL, MLB, WNBA data pipelines, `/api/slate` endpoint, and `ui_runner/templates/index.html`.
> Canonical repo root: `H:\halek\ProfileFromC\Desktop\PropORACLE`

---

## Repository alignment (apply when implementing in this repo)

- **Orchestration:** Full parallel pipeline lives in `run_pipeline.ps1` (and `scripts/run_daily.ps1`). The doc below references `run_all.ps1` — confirm that file exists in your branch; otherwise use `run_pipeline.ps1` for NHL/WNBA ordering.
- **Combine / slate JSON:** Primary module is **`scripts/combined_slate_tickets.py`** (not `combine_slate_*.py`).
- **Web framework:** This app uses Flask-style routes — use **`@app.get("/api/slate")`** (or equivalent) and `request.args.get("sport")`, not the older `@app.route` sketch shown in Task 1.
- **Two APIs:** **`GET /api/slate-sport/<sport>`** already returns per-sport rows. Task 1 can add **`?sport=`** filtering to `/api/slate`, or clients can standardize on `/api/slate-sport/nhl` etc.
- **Missing `/api/slate` fields:** Many targets (**`ml_prob`, `def_tier`, `team`, `opp`, `rank_score`, `image_url`, …**) are often already on **`tickets_latest.json` legs** — extend the **`pick_entry`** dict in `api_slate()` (`ui_runner/app.py` ~4301–4322) to pass them through.
- **Sport explorer payload:** **`_SLATE_SPORT_UI_KEYS`** in `app.py` controls which fields survive `/api/slate-sport` — extend it for Oracle Score, L10, `def_tier`, etc., or add a `verbose` mode if payload size grows.

---

## CONTEXT: What Was Found in the Live Audit

### API Layer (`/api/slate`)
- **`/api/slate` ignores the `?sport=` query param entirely.** Every call — regardless of `?sport=nba`, `?sport=nhl`, `?sport=MLB`, no param, etc. — returns the **same NBA-first combined JSON** (403 picks). The sport filter is silently dropped server-side.
- The combined `/api/slate` response includes these sports today: `NBA` (101), `NBA1Q` (13), `NBA1H` (1), `MLB` (271), `Soccer` (5), `Tennis` (12). **NHL = 0 picks. WNBA = 0 picks.** Both are completely absent from the live combined slate despite showing as "FRESH 2026-05-15" in the UI.
- `/api/slate` response schema per pick (41 fields):
  ```
  sport, initials, player, prop, line, pick, dir, hit, edge, abs_edge,
  projection, l5_over, l5_under, l10_over, l10_under, l5_avg, season_avg,
  actual_series[], line_series[], standard_projection, standard_line,
  g1..g10, stat_g1..stat_g10
  ```
- **Completely absent from API response**: `team`, `opp`, `tier`, `rank`, `ml_prob`, `def_tier`, `book_line`, `prop_line`, `game_time` (structured), `injury_status`, `pitcher` (MLB), `opponent_def_rank`

### Per-Sport Data Quality Findings

#### NBA (101 picks in combined slate)
- Props: `Pts+Rebs`, `Pts+Rebs+Asts`, `Points`, `Pts+Asts`, `Rebs+Asts`, `Rebounds`, `Assists`, `2PA`, `3PA`, `3PM`, `2PM`, `Steals`
- Pick types: `Standard`, `Goblin` ✅ (no Demon — correctly excluded)
- `l10_over` / `l10_under`: **ALL NULL** (101/101 nulls) — L10 streak data missing entirely for NBA
- `actual_series`: populated (10 games) ✅
- `line_series`: **ALL EMPTY** (length 0 for all picks) — synthetic chart history has no line anchor
- `season_avg`: populated ✅ but not displayed in UI
- `projection`: populated ✅ but not displayed in UI

#### MLB (271 picks in combined slate)
- Props: `Hits Allowed`, `Total Bases`, `Pitcher Strikeouts`, `Walks Allowed`, `Hits`, `Pitching Outs`, `Walks`, `Hitter Strikeouts`, `Runs`
- Pick types: `Goblin`, `Standard` ✅
- `l10_over` / `l10_under`: **ALL NULL** (271/271)
- `actual_series`: populated (8 games) ✅ but shorter window than NBA
- `line_series`: **ALL EMPTY**
- **Zero pitcher context** — no `pitcher_name`, `pitcher_hand`, `pitch_mix` data anywhere in the payload
- OPP column in the UI table is populated ✅

#### NHL (0 picks in combined slate — FRESH but empty)
- NHL Slate card shows **PENDING / "Not Yet Run"** in the UI
- NHL picks are **completely absent** from `/api/slate` response
- Grades page (May 14): NHL showed **509 total props** — pipeline ran yesterday, not today
- This is a **daily execution gap** — NHL step is not being triggered on today's date

#### WNBA (0 picks in combined slate — FRESH but empty)
- WNBA Slate card shows as **FRESH 2026-05-15** but contributes **0 picks** to combined slate
- WNBA is absent from `/api/slate` entirely
- Known issue from sprint notes: stale canonical file at `Sports/WNBA/step8_wnba_direction_clean.xlsx`, missing `mobile/www/data/` publish step
- The WNBA pipeline ran (step8 exists) but output is not being serialized into the combined JSON

#### Soccer (5 picks in combined slate)
- **OPP column shows "UNKN..."** for most rows — opponent team lookup is failing/truncated
- `l10_over` / `l10_under`: populated ✅ (only sport with L10 data)
- `actual_series`: populated (5 games) ✅

#### Tennis (12 picks)
- `actual_series`: **ALL EMPTY** (length 0 for all 12 picks) — no game history data at all
- `l10_over` / `l10_under`: populated ✅

### UI Layer (`ui_runner/templates/index.html`)
- **Top Edges section**: collapsed, never expands — render bug, section header click handler not working
- **L5 Streaks section**: collapsed, never expands — same issue
- **L10 Streaks section**: collapsed, never expands — same issue
- **Player row click**: no detail panel opens — no drill-down UX at all
- **`tier` and `rank` columns** show in the slate table but come from a separate enrichment pass, not from `/api/slate` — fragile join
- **`ml_prob`**: computed by XGBoost pipeline but **never serialized into `/api/slate`** and never displayed to user
- **`def_tier`**: computed but only visible in Grades HTML, not per-pick in slate table
- **`season_avg` and `projection`**: in API response but not rendered as columns
- **Fantasy Score props** appearing in Top Edges (known bug from last session, fix deployed but needs verification)
- **`/api/slate?sport=X`** routing is broken — the sport filter param is ignored server-side

---

## UPGRADE TASKS

### TASK 1 — Fix `/api/slate` Sport Routing (CRITICAL)
**File**: `ui_runner/app.py`

The `/api/slate` endpoint ignores `?sport=` and always returns the same combined JSON. Fix it so:
1. `GET /api/slate` → returns full combined slate (all sports merged)
2. `GET /api/slate?sport=NBA` → returns only NBA picks
3. `GET /api/slate?sport=MLB` → returns only MLB picks
4. `GET /api/slate?sport=NHL` → returns only NHL picks
5. `GET /api/slate?sport=WNBA` → returns only WNBA picks
6. Sport matching should be **case-insensitive** (accept `nba`, `NBA`, `Nba`)

```python
# Current (broken) pattern in app.py — fix this:
@app.route('/api/slate')
def api_slate():
    # sport param is never read — always returns same data
    ...

# Target pattern:
@app.route('/api/slate')
def api_slate():
    sport_filter = request.args.get('sport', '').upper()
    data = load_combined_slate()  # loads all picks
    if sport_filter:
        data['picks'] = [p for p in data['picks'] if p.get('sport', '').upper() == sport_filter]
    return jsonify(data)
```

> **Note:** In this codebase use `@app.get("/api/slate")` and the existing `_build_picks()` / response helpers.

---

### TASK 2 — Add Missing Fields to `/api/slate` Response (CRITICAL)
**Files**: `ui_runner/app.py`, the slate-building pipeline steps

The following fields are computed in the pipeline but not serialized into the API response. Add them to every pick object in the `/api/slate` output:

| Field | Source | Description |
|---|---|---|
| `team` | pipeline step output | Player's team abbreviation (e.g. "MIN", "SAS") |
| `opp` | pipeline step output | Opponent team abbreviation |
| `ml_prob` | `edge_model_unified.pkl` inference | XGBoost probability (0.0–1.0) |
| `tier` | tier assignment logic | A/B/C/D within pick group |
| `rank` | rank_score computation | Numeric rank score (e.g. 1.89) |
| `def_tier` | defense tier computation | "Elite" / "Above Avg" / "Avg" / "Below Avg" / "Weak" |
| `opponent_def_rank` | defense data join | Numeric rank of opponent defense |
| `book_line` | PrizePicks API response | Raw book line (may differ from standard_line) |
| `prop_line` | PrizePicks API response | PrizePicks prop line |
| `game_time` | schedule data | ISO datetime string for tip/first pitch |
| `injury_status` | ESPN injury feed or pipeline flag | "Active" / "GTD" / "OUT" / null |

For MLB specifically, also add:
| Field | Source |
|---|---|
| `pitcher_name` | pitching matchup data |
| `pitcher_hand` | L/R handedness |

**Implementation**: In the function that builds the picks JSON for the API response, ensure the pick dict includes all of the above. If any field is unavailable for a given sport/pick, serialize it as `null` rather than omitting the key (this keeps the schema consistent for the frontend).

> **Note:** Ticket legs in `combined_slate_tickets.py` already include many of these — prefer **pass-through** from `leg` before re-deriving from the model.

---

### TASK 3 — Fix NHL Pipeline Daily Execution (CRITICAL)
**Files**: `scripts/run_all.ps1` or equivalent orchestration script, NHL pipeline steps

**Problem**: NHL shows as PENDING / "Not Yet Run" today (2026-05-15) despite showing 509 props in yesterday's Grades (May 14). The NHL step is not being triggered daily.

Actions:
1. Check `run_all.ps1` (or equivalent) — confirm NHL step is included and not commented out
2. Verify the NHL step runs **before** the combine step that builds the combined slate JSON
3. Add a timeout guard: if NHL step takes >1200 seconds, log a warning but don't block combined slate generation
4. Add `Write-Host` logging before/after each sport step so failures are visible in Railway logs
5. Ensure the NHL output file path matches what the combine step expects

> **Note:** Use **`run_pipeline.ps1`** NHL parallel job section if `run_all.ps1` is absent.

---

### TASK 4 — Fix WNBA Output Not Appearing in Combined Slate (CRITICAL)
**Files**: `Sports/WNBA/` pipeline, combine step, `ui_runner/app.py`

**Problem**: WNBA pipeline appears to run (step8 exists, card shows FRESH) but 0 WNBA picks appear in `/api/slate`. Two known sub-issues:
1. Stale canonical file at `Sports/WNBA/step8_wnba_direction_clean.xlsx` — regenerate from latest data
2. Missing `mobile/www/data/` publish step — the WNBA output is not being written to the directory the combine step reads from

Actions:
1. In the combine step, verify it reads from the correct WNBA output path (check for path mismatch between WNBA step8 output and combine step input)
2. Add explicit logging: `print(f"WNBA picks loaded: {len(wnba_picks)}")` in the combine step
3. If `step8_wnba_direction_clean.xlsx` is stale, add a step that regenerates it from `step7` output on each pipeline run rather than reading a cached file
4. Add WNBA to the `mobile/www/data/` publish step so it mirrors correctly

> **Note:** See `publish_wnba_slate_merge_into_web` and `_wnba_slate_rows_from_step8_fallback` in **`scripts/combined_slate_tickets.py`** / `ui_runner/app.py`.

---

### TASK 5 — Fix L10 Streak Data for NBA and MLB (HIGH)
**Files**: NBA and MLB pipeline step that computes `l10_over`/`l10_under`

**Problem**: `l10_over` and `l10_under` are NULL for **all 101 NBA picks** and **all 271 MLB picks**. Only Soccer and Tennis have populated L10 data. This means the L10 Streaks section on the home page has no data to display.

Actions:
1. Find the step that computes `l10_over`/`l10_under` for NBA — confirm it is running and writing to the correct output column
2. Check the join key used to merge L10 data onto picks — likely a player name / prop type join that is failing silently
3. Add a validation check: after the L10 computation step, `assert df['l10_over'].notna().mean() > 0.5, "L10 data missing for >50% of picks"`
4. For MLB: same audit — L10 computation step needs to be verified
5. Once fixed, the L5/L10 Streaks sections on the home page should auto-populate since the UI already handles that data

---

### TASK 6 — Fix Top Edges / L5 Streaks / L10 Streaks Home Page Collapse Bug (HIGH)
**File**: `ui_runner/templates/index.html`

**Problem**: The "TOP EDGES", "L5 STREAKS", and "L10 STREAKS" sections on the home page show as collapsed accordion rows with a `►` arrow, but clicking them never expands the content. The click handler is either not bound or the section content is empty/missing.

Actions:
1. Find the click handler for the TOP EDGES section toggle — confirm it is correctly bound to the section element
2. Check whether the section content fails to render because there is no data (empty array) — if so, add a loading state and a "No data available" fallback so the section at least expands to show something
3. The Top Edges section should display the top 10 picks by `abs_edge` across all sports, sorted descending, as mini cards
4. L5 Streaks: top 10 picks where `l5_over >= 4` or `l5_under >= 4`
5. L10 Streaks: top 10 picks where `l10_over >= 8` or `l10_under >= 8` (will require Task 5 to be fixed first)
6. Each mini card should show: player name, prop, line, type badge (Goblin/Standard), edge, hit%, L5 streak indicator

---

### TASK 7 — Surface `ml_prob` as "Oracle Score" in the Slate Table (HIGH)
**File**: `ui_runner/templates/index.html`

**Problem**: The XGBoost `ml_prob` is computed (AUC 0.75) but is invisible to the user. This is PropORACLE's biggest competitive differentiator vs PropFinder's "PF Rating."

Actions:
1. After Task 2 adds `ml_prob` to the API response, add an **"Oracle Score"** column to the slate table
2. Display it as a percentage badge (e.g. "74%" in amber/gold color)
3. Make the column sortable
4. Show it in the player card detail view (Task 8) as the primary score metric
5. In the column header tooltip, describe it as: "XGBoost ML win probability"
6. Color coding:
   - ≥ 70%: green badge
   - 55–69%: amber badge
   - < 55%: gray badge

---

### TASK 8 — Add Player Detail Panel on Row Click (HIGH)
**File**: `ui_runner/templates/index.html`

**Problem**: Clicking any row in the slate table does nothing. The actual_series data (10 game history), ml_prob, def_tier, edge, and projection are all in the API response but never shown in a drill-down view.

Actions:
Add a slide-in or expand-in-place detail panel when a slate row is clicked. The panel should show:

1. **Header**: Player name, team vs opponent, game time, prop type
2. **Oracle Score badge**: `ml_prob` as percentage (large, prominent)
3. **Key stats row**: Line | Edge | Hit% | L5 | Projection | Season Avg
4. **Tier badges**: Pick tier (A/B/C/D) and Def Tier (Elite → Weak)
5. **Game history sparkline/bar chart**: `actual_series` (last 10 games) with the line drawn as a horizontal reference
6. **Streak indicators**: L5 O/U count with colored dots (green=over, red=under)
7. For MLB picks: pitcher name if available
8. Close on: second click, Escape key, or clicking outside panel

Use the existing `actual_series` array from the API response — no new data fetch needed.

---

### TASK 9 — Fix Soccer OPP Column (MEDIUM)
**File**: Soccer pipeline output / combine step

**Problem**: OPP column shows "UNKN..." (truncated "UNKNOWN") for most Soccer picks in the slate table. The opponent team lookup is failing.

Actions:
1. In the Soccer pipeline, find where `opp` is assigned and trace why it resolves to "UNKNOWN"
2. Check the ESPN team ID → abbreviation mapping for Soccer leagues (Premier League, Liga MX, etc.)
3. If the team name is long, store the full name in a separate `opp_full` field and use a short abbreviation (4 chars max) for the `opp` field displayed in the table
4. Fallback: use the first 4 chars of the league team name if no abbreviation is found

---

### TASK 10 — Add `line_series` Population for NBA and MLB (MEDIUM)
**File**: NBA and MLB pipeline steps, chart logic in `index.html`

**Problem**: `line_series` is empty (length 0) for all NBA and MLB picks. This means the synthetic chart fallback (used when `actual_series` is short) has no line anchor, causing the zero-line bug described in the session context (charts sitting at 0 while footer shows the real line).

Actions:
1. In the pipeline step that builds the per-pick JSON, populate `line_series` as an array of the prop line value repeated N times (same length as `actual_series`): e.g. if `standard_line = 15` and `actual_series` has 10 entries, set `line_series = [15, 15, 15, 15, 15, 15, 15, 15, 15, 15]`
2. This gives the chart a stable horizontal reference line even in synthetic mode
3. Also apply the `bookLineNumForPick(p)` fix from the prior session — confirm it uses `coercePropLine(p)` which checks `standard_line`, `book_line`, and `prop_line` in order

---

### TASK 11 — Add Tennis `actual_series` (MEDIUM)
**File**: Tennis pipeline step

**Problem**: All 12 Tennis picks have `actual_series = []` (empty). There is no game history data. This makes tennis charts completely empty.

Actions:
1. For Tennis, pull last N match results for the player-prop combination
2. For `Total Games` props: store total games played per set per match
3. For `Total Sets` props: store set counts per match
4. Minimum viable: populate at least 5 historical results
5. If historical data is unavailable from the current source, add a `data_quality` flag: `"historical_data": false` so the UI can show "Limited history" instead of an empty chart

---

### TASK 12 — Add `def_tier` Badge to Slate Table Rows (MEDIUM)
**File**: `ui_runner/templates/index.html`

**Problem**: `def_tier` (Elite/Above Avg/Avg/Below Avg/Weak) is computed but only visible deep in the Grades HTML. PropFinder shows opponent matchup quality per pick.

Actions:
1. After Task 2 adds `def_tier` to the API response, add a **DEF** column to the slate table
2. Color-code:
   - Elite: bright green `🟢`
   - Above Avg: light green
   - Avg: gray
   - Below Avg: orange
   - Weak: red `🔴`
3. Show as a colored pill badge, not just text
4. Make the column sortable and filterable (add "DEF TIER" to the existing filter bar)

---

### TASK 13 — Add `season_avg` and `projection` as Visible Columns (LOW)
**File**: `ui_runner/templates/index.html`

Both fields are already in the API response but not rendered. Add them as optional columns (hidden by default, toggleable via a column picker or shown in the expanded detail panel from Task 8).

- `projection`: show as "PROJ" column with the projected stat value
- `season_avg`: show as "AVG" column
- Both should appear in the player detail panel prominently even if hidden from the table

---

### TASK 14 — MLB Pitcher Context (LOW, but High Value)
**File**: MLB pipeline steps

PropFinder's top differentiator is per-pitcher filtering. Even basic pitcher name surfacing closes a major gap.

Actions:
1. In the MLB pipeline, identify where the pitching matchup data comes from (ESPN, MLB API, or Rotowire feed)
2. Add `pitcher_name` and `pitcher_hand` (L/R) fields to each MLB pick object
3. Serialize into the API response (covered by Task 2)
4. In the UI, show pitcher name under the player name in the slate table for all MLB picks:
   ```
   José Ramírez
   vs. Gerrit Cole (R)
   ```
5. In the player detail panel (Task 8), show pitcher stats: ERA, K/9, recent form

---

## VALIDATION CHECKLIST

After implementing the above, verify:

- [ ] `GET /api/slate?sport=NHL` returns only NHL picks (not NBA)
- [ ] `GET /api/slate?sport=WNBA` returns WNBA picks (> 0)
- [ ] `GET /api/slate` response includes `ml_prob`, `tier`, `def_tier`, `team`, `opp` fields
- [ ] NBA picks: `l10_over` is not null for > 80% of picks
- [ ] MLB picks: `l10_over` is not null for > 80% of picks
- [ ] `line_series` length > 0 for NBA and MLB picks
- [ ] Tennis `actual_series` length > 0
- [ ] Soccer OPP column shows real abbreviations, not "UNKN..."
- [ ] TOP EDGES section expands and shows top 10 picks by edge
- [ ] L5/L10 STREAKS sections expand and show streak leaders
- [ ] Clicking a slate row opens a detail panel with game history chart
- [ ] "Oracle Score" (ml_prob) badge visible in slate table
- [ ] `def_tier` column visible in slate table with color coding
- [ ] NHL slate shows picks on days when NHL games are scheduled
- [ ] WNBA slate shows picks during WNBA season
- [ ] `/api/slate?sport=` filter works for all sports case-insensitively

---

## PRIORITY ORDER

| Priority | Task | Effort | Impact |
|---|---|---|---|
| P0 | Task 1 — Fix sport routing | 30 min | Unblocks all sport-specific fetches |
| P0 | Task 3 — NHL daily execution | 1–2h | NHL completely missing from today |
| P0 | Task 4 — WNBA output fix | 1–2h | WNBA completely missing from combined |
| P1 | Task 2 — Add fields to API | 2–3h | Unlocks Tasks 6, 7, 8, 12, 13 |
| P1 | Task 5 — Fix L10 nulls (NBA/MLB) | 2–3h | Required for streaks section |
| P1 | Task 6 — Fix collapse bug | 1h | Home page is currently dead above the fold |
| P1 | Task 7 — Oracle Score badge | 2h | Single biggest UX differentiator |
| P2 | Task 8 — Player detail panel | 4–6h | Biggest UX gap vs PropFinder |
| P2 | Task 10 — line_series population | 1h | Fixes zero-line chart bug |
| P2 | Task 9 — Soccer OPP fix | 1h | Data quality |
| P3 | Task 12 — def_tier badge | 2h | UX polish |
| P3 | Task 11 — Tennis actual_series | 2–3h | Data completeness |
| P3 | Task 13 — season_avg/projection | 1h | Easy column add |
| P3 | Task 14 — MLB pitcher context | 3–5h | Strategic differentiation |

---

## KEY FILES TO TOUCH

```
H:\halek\ProfileFromC\Desktop\PropORACLE\
├── ui_runner\
│   ├── app.py                          # Task 1, Task 2 (API endpoint)
│   └── templates\
│       └── index.html                  # Tasks 6, 7, 8, 12, 13
├── scripts\
│   ├── run_pipeline.ps1                # Task 3 (NHL daily exec) — verify vs run_all.ps1
│   └── combined_slate_tickets.py       # Tasks 2, 4 (field serialization, WNBA publish)
├── Sports\
│   ├── NBA\                            # Task 5 (L10 fix)
│   ├── MLB\                            # Tasks 5, 14 (L10, pitcher)
│   ├── NHL\                            # Task 3
│   ├── WNBA\                           # Task 4
│   ├── Soccer\                         # Task 9
│   └── Tennis\                         # Task 11
└── models\
    └── edge_model_unified.pkl          # Referenced in Task 2 (ml_prob source)
```
