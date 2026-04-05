# Sunday Apr 5, 2026 — Prop Oracle ticket sheet (pipeline-reconciled)
**Generated from on-disk step outputs (not from `outputs/2026-04-05/`, which is absent in this repo snapshot).**
**SKILL.md:** `/mnt/skills/user/slateiq/SKILL.md` not available in this environment.

## Reconciliation summary
| Check | Result |
|-------|--------|
| `outputs/2026-04-05/` dated step8 copies | **Missing** — used `NBA/data/outputs/step8_all_direction_clean.xlsx`, `CBB/step6_ranked_cbb.xlsx`, `MLB/step8_mlb_direction_clean.xlsx` |
| NBA slate on disk | **Not Sunday priority slate** — teams present are mainly SAS, DET, MIA, DEN, WAS, PHI, DEN/SAS, MIA/WAS (no BKN/LAC/PHX/BOS/MIL/ORL/DAL/LAL/GSW/HOU/OKC/CLE blocks). Pre-analyzed NBA stars → **UNVERIFIED** vs this file. |
| MLB COL vs PHI @ Coors | **Not in pipeline** — PHI appears vs **TEX**; COL game on disk is **MIA @ COL**. |
| CBB Oklahoma (OU) | **0 rows** — only **WVU** rows found for tournament proxy. |

---

## Task 1 — Reconciliation (pre-ranked Tier A/B)
| Pre-rank | Player | Pipeline | Failure / note |
|----------|--------|----------|----------------|
| 1 | Bryce Harper | Total bases OVER | VERIFIED — **Goblin** OVER; ML=0.779, L5=0.80, edge=0.30; matchup **PHI vs TEX** (not Coors in this file). |
| 2 | Kawhi Leonard | points OVER | Not found on current NBA step8 (slate does not include that team/player). |
| 3 | Devin Booker | points OVER | Not found on current NBA step8 (slate does not include that team/player). |
| 4 | Domantas Sabonis | rebounds OVER | Not found on current NBA step8 (slate does not include that team/player). |
| 5 | Stephen Curry | threes OVER | Not found on current NBA step8 (slate does not include that team/player). |
| 6 | Jayson Tatum | points OVER | Not found on current NBA step8 (slate does not include that team/player). |
| 7 | Giannis Antetokounmpo | PRA OVER | Not found on current NBA step8 (slate does not include that team/player). |
| 8 | James Harden | assists OVER | Not found on current NBA step8 (slate does not include that team/player). |
| 9 | Paolo Banchero | points OVER | Not found on current NBA step8 (slate does not include that team/player). |
| 10 | Luka Doncic | points OVER | Not found on current NBA step8 (slate does not include that team/player). |
| 11 | Anthony Davis | rebounds OVER | Not found on current NBA step8 (slate does not include that team/player). |
| 12 | Kevin Durant | points OVER | Not found on current NBA step8 (slate does not include that team/player). |
| 13 | Oklahoma lead guard | points OVER | No Oklahoma / OU rows in `CBB/step6_ranked_cbb.xlsx` (UNVERIFIED). |
| 14 | WVU big | rebounds OVER | Generic label — pipeline has named WVU players (e.g. Huff/Eaglestaff); use specific player row or mark UNVERIFIED for aggregate label. |
| 15 | Kyle Schwarber | total bases OVER | FOUND but **FAIL Goblin gate**: L5 hit rate 0.20 < **0.60** (ML=0.919). Do not ticket as Goblin. |

## Task 2 — Final ranked prop sheet (top 15 by composite)
_**Bold** = Goblin pick type. **⚠️** = not applicable here (all rows below are VERIFIED from snapshot)._

| Rank | Player | Prop | Direction | Tier | ML Prob | L5 HR | L10 HR | Edge | Composite | Pipeline status | Confidence |
|------|--------|------|-----------|------|---------|-------|--------|------|-----------|-----------------|------------|
| 1 | George Springer | **Total Bases** | OVER | A | 0.9155 | 0.800 | — | 2.300 | 9.1500 | VERIFIED (MLB) | High |
| 2 | George Springer | **Hits** | OVER | A | 0.9175 | 0.800 | — | 0.900 | 8.5000 | VERIFIED (MLB) | High |
| 3 | Daulton Varsho | **Total Bases** | OVER | A | 0.9134 | 0.800 | — | 1.500 | 8.3100 | VERIFIED (MLB) | High |
| 4 | Daulton Varsho | **Hits** | OVER | A | 0.9168 | 0.800 | — | 0.700 | 7.8300 | VERIFIED (MLB) | High |
| 5 | Ernie Clement | **Total Bases** | OVER | A | 0.9175 | 0.800 | — | 0.900 | 7.5800 | VERIFIED (MLB) | High |
| 6 | Ernie Clement | **Hits** | OVER | A | 0.9172 | 0.800 | — | 0.500 | 7.1600 | VERIFIED (MLB) | High |
| 7 | Andrés Giménez | **Total Bases** | OVER | A | 0.9169 | 0.600 | — | 0.500 | 5.9400 | VERIFIED (MLB) | High |
| 8 | Colson Montgomery | **Total Bases** | OVER | A | 0.9205 | 1.000 | — | 3.100 | 5.5000 | VERIFIED (MLB) | High |
| 9 | Brendan Donovan | **Total Bases** | OVER | A | 0.9168 | 1.000 | — | 2.700 | 5.0200 | VERIFIED (MLB) | High |
| 10 | Michael Harris II | **Total Bases** | OVER | A | 0.9187 | 1.000 | — | 2.900 | 4.8900 | VERIFIED (MLB) | High |
| 11 | Shea Langeliers | **Total Bases** | OVER | A | 0.9190 | 1.000 | — | 3.700 | 4.6900 | VERIFIED (MLB) | High |
| 12 | Ronald Acuña Jr. | **Total Bases** | OVER | A | 0.9137 | 1.000 | — | 2.100 | 4.6100 | VERIFIED (MLB) | High |
| 13 | Colson Montgomery | **Hits** | OVER | A | 0.9128 | 1.000 | — | 1.300 | 4.6000 | VERIFIED (MLB) | High |
| 14 | Josh Naylor | **Total Bases** | OVER | A | 0.9093 | 1.000 | — | 1.700 | 4.5900 | VERIFIED (MLB) | High |
| 15 | Shea Langeliers | **Hits** | OVER | A | 0.9152 | 1.000 | — | 2.300 | 4.5100 | VERIFIED (MLB) | High |

_Addendum — **Bryce Harper** **Total Bases** OVER (**Goblin**) is **VERIFIED** for Tier A narrative but **Rank Score 1.680** sits below the MLB-heavy top 15 on this snapshot (**PHI vs TEX**, not Coors in file)._

## Task 3 — Three finalized tickets (snapshot slate)
_Legs pass gates on **this** file. **No player is repeated across tickets.** Ticket 1 prefers **three different NBA matchups**._
**TICKET 1 — Goblin — NBA**
- Leg 1: **Keldon Johnson** | Assists | OVER | Goblin | ML:0.919 | L5:1.00
- Leg 2: **Paul Reed** | Assists | OVER | Goblin | ML:0.919 | L5:1.00
- Leg 3: **Davion Mitchell** | 3-PT Made | OVER | Goblin | ML:0.919 | L5:0.80
- **Combined:** 77.6% (product of leg ML probs) | **Edge justification:** Three Goblin overs on **distinct** matchups (SAS@DEN, DET@PHI, MIA@WAS).

**TICKET 2 — Standard — NBA**
- Leg 1: Devin Vassell | Rebounds | OVER | Standard | ML:0.908 | L5:0.80
- Leg 2: Bam Adebayo | Rebounds | OVER | Standard | ML:0.908 | L5:0.60
- Leg 3: Caris LeVert | Pts+Rebs | OVER | Standard | ML:0.905 | L5:1.00
- **Combined:** 74.6% | **Edge justification:** Standard overs with L5 ≥ 0.55, ML ≥ 0.55, and |edge| ≥ 0.04; no overlap with Ticket 1 players.

**TICKET 3 — Mixed — MLB + CBB + NBA**
- Leg 1: **Bryce Harper** | Total Bases | OVER | Goblin | ML:0.779 | L5:0.80
- Leg 2: Honor Huff | Points | OVER | Standard | ML:0.588 | L5:0.80
- Leg 3: **Julian Champagnie** | Assists | OVER | Goblin | ML:0.919 | L5:0.80
- **Combined:** 42.1% | **Edge justification:** Cross-sport uncorrelated legs; Harper row is **PHI vs TEX** in file (not Coors).

## Task 4 — Blowout minute cap checker (OKC–UTA, CLE–IND)
| Game | Pipeline rows | 15% projection haircut + L5 re-check |
|------|---------------|----------------------------------------|
| OKC vs UTA | **0** props for OKC/UTA on disk | **N/A — DROP** (no rows to evaluate; cannot certify ADJUSTED-VIABLE). |
| CLE vs IND | **0** rows for CLE/IND | **N/A — DROP** (same). |
| Note | — | **Donovan Mitchell** pre-analysis ≠ **Davion Mitchell** (MIA) in file — do not conflate. |

## Task 5 — MLB Coors alert (COL vs PHI)
| Item | Result |
|------|--------|
| PHI hitter props @ Coors in pipeline | **None** — **0** rows with PHI + COL in `Team`/`Opp`. |
| PHI@COL barrel / TB proxy rank | **Deferred** until slate includes that matchup. |
| Pitcher strikeout props (any COL game on disk) | **STRUCTURAL FADE** for Coors-style K overs — file shows **MIA@COL** K props (e.g. Quintana/Meyer rows); **do not play K OVER** in that environment per house rules. |

**PHI-side hitter props in pipeline** (rows with Team = PHI; n=55 after dropping obvious club-mapping errors: Adolis García, Otto Kemp). Matchup **PHI vs TEX** — not Coors:

- Alec Bohm | Hits | Demon | OVER | vs TEX
- Alec Bohm | Hits | Goblin | OVER | vs TEX
- Alec Bohm | Home Runs | Demon | OVER | vs TEX
- Alec Bohm | Runs | Demon | OVER | vs TEX
- Alec Bohm | Total Bases | Demon | OVER | vs TEX
- Alec Bohm | Total Bases | Goblin | OVER | vs TEX
- Alec Bohm | Walks | Demon | OVER | vs TEX
- Bryce Harper | Hits | Demon | OVER | vs TEX
- Bryce Harper | Hits | Goblin | OVER | vs TEX
- Bryce Harper | Home Runs | Demon | OVER | vs TEX
- Bryce Harper | Runs | Demon | OVER | vs TEX
- Bryce Harper | Stolen Bases | Demon | OVER | vs TEX
- Bryce Harper | Total Bases | Demon | OVER | vs TEX
- Bryce Harper | Total Bases | Goblin | OVER | vs TEX
- Bryce Harper | Walks | Demon | OVER | vs TEX
- Edmundo Sosa | Hits | Demon | OVER | vs TEX
- Edmundo Sosa | Hits | Goblin | OVER | vs TEX
- Edmundo Sosa | Home Runs | Demon | OVER | vs TEX
- Edmundo Sosa | Runs | Demon | OVER | vs TEX
- Edmundo Sosa | Stolen Bases | Demon | OVER | vs TEX
- Edmundo Sosa | Total Bases | Demon | OVER | vs TEX
- Edmundo Sosa | Total Bases | Goblin | OVER | vs TEX
- J.T. Realmuto | Hits | Demon | OVER | vs TEX
- J.T. Realmuto | Hits | Goblin | OVER | vs TEX
- J.T. Realmuto | Home Runs | Demon | OVER | vs TEX
- J.T. Realmuto | Runs | Demon | OVER | vs TEX
- J.T. Realmuto | Stolen Bases | Demon | OVER | vs TEX
- J.T. Realmuto | Total Bases | Demon | OVER | vs TEX
- J.T. Realmuto | Total Bases | Goblin | OVER | vs TEX
- J.T. Realmuto | Walks | Demon | OVER | vs TEX
- Justin Crawford | Hits | Demon | OVER | vs TEX
- Justin Crawford | Hits | Goblin | OVER | vs TEX
- Justin Crawford | Runs | Demon | OVER | vs TEX
- Justin Crawford | Stolen Bases | Demon | OVER | vs TEX
- Justin Crawford | Total Bases | Demon | OVER | vs TEX
- Justin Crawford | Total Bases | Standard | UNDER | vs TEX
- Justin Crawford | Walks | Demon | OVER | vs TEX
- Kyle Schwarber | Hits | Demon | OVER | vs TEX
- Kyle Schwarber | Hits | Goblin | OVER | vs TEX
- Kyle Schwarber | Home Runs | Demon | OVER | vs TEX

_…and 15 additional PHI hitter rows._

**Pitcher strikeout props involving COL (sample — STRUCTURAL FADE for K OVER at altitude):**

- Max Meyer | Pitcher Strikeouts | Goblin | OVER | MIA vs COL
- Jose Quintana | Pitcher Strikeouts | Goblin | OVER | COL vs MIA
- Max Meyer | Pitcher Strikeouts | Standard | UNDER | MIA vs COL
- Jose Quintana | Pitcher Strikeouts | Standard | OVER | COL vs MIA
- Max Meyer | Pitcher Strikeouts | Goblin | OVER | MIA vs COL
- Jose Quintana | Pitcher Strikeouts | Demon | OVER | COL vs MIA
- Max Meyer | Pitcher Strikeouts | Demon | OVER | MIA vs COL
- Jose Quintana | Pitcher Strikeouts | Demon | OVER | COL vs MIA
- Jose Quintana | Pitcher Strikeouts | Demon | OVER | COL vs MIA
- Max Meyer | Pitcher Strikeouts | Demon | OVER | MIA vs COL
- Max Meyer | Pitcher Strikeouts | Demon | OVER | MIA vs COL

## Task 6 — Warning log (pre-analysis vs pipeline)
- Kawhi Leonard … — NOT FOUND (NBA slate mismatch).
- Devin Booker … — NOT FOUND.
- Domantas Sabonis … — NOT FOUND.
- Stephen Curry … — NOT FOUND.
- Jayson Tatum … — NOT FOUND.
- Giannis … — NOT FOUND.
- James Harden … — NOT FOUND.
- Paolo Banchero … — NOT FOUND.
- Luka Doncic … — NOT FOUND.
- Anthony Davis … — NOT FOUND.
- Kevin Durant … — NOT FOUND.
- Shai Gilgeous-Alexander … — NOT FOUND (no OKC).
- Donovan Mitchell (CLE) … — NOT FOUND (no CLE; Davion Mitchell is MIA).
- Oklahoma lead guard … — NOT FOUND (no OU in CBB file).
- Kyle Schwarber TB Goblin — **Low L5** (0.20) vs **0.60 Goblin floor** → FAILED gate.
- Coors PHI hitter list — matchup absent → UNVERIFIED for Sunday script.

## Props to avoid (re-stated + data-driven)
1. **All Sunday pre-analysis NBA stars** until `step8` reflects Apr 5 matchups — current file is a different slate.
2. **Kyle Schwarber Goblin TB** — L5 hit rate **0.20** vs required **0.60** for Goblin.
3. **Pitcher strikeouts OVER** in **COL home** games in file — structural fade.
4. **Demon / Goblin UNDER** — never allowed by rules (scan picks before submit).
5. **Harper Demon / low-ML rows** — ignore non-Goblin Harper lines with ML < 0.55 for ticket use.

## TOP 5 singles (from **this** snapshot only)
1. **George Springer** — Total Bases OVER (Goblin) | composite 9.150
2. **George Springer** — Hits OVER (Goblin) | composite 8.500
3. **Daulton Varsho** — Total Bases OVER (Goblin) | composite 8.310
4. **Daulton Varsho** — Hits OVER (Goblin) | composite 7.830
5. **Ernie Clement** — Total Bases OVER (Goblin) | composite 7.580
