# Pipeline read living spec

**File:** `data/pipeline_read_checklist_matrix.csv`  
**Audit reference:** `data/reports/pipeline_read_audit_2026-06-02.json`  
**Checklist:** `data/pipeline_read_checklist.json`

## Status legend (Step7 / Step8 / Full Slate / Enrichment / Tickets)

| Value | Meaning |
|-------|---------|
| **Yes** | Present and used; acceptable fill on active 06-02 slate where applicable |
| **Partial** | Present but incomplete export, fill, or sport coverage |
| **Passthrough** | Carried from upstream; not computed in this layer |
| **Derived** | Computed in `utils/pipeline_read_enrichment.py` (or ticket merge from enrichment) |
| **In-score** | Used inside step7 `rank_score` but not exported as its own read column |
| **No** | Absent at this layer |
| **N/A** | Not applicable to this layer |

## Operational read (2026-06-02)

**Good enough to use today:** MLB, WNBA, NHL — form + edge + def tier + enriched probs. Step7 rank is correct even when Full Slate does not show every component.

| Sport | Factor into picks |
|-------|-------------------|
| **MLB** | Trust L5 over L10; ignore park/weather on FS until exported |
| **WNBA** | Usage boost empty on FS — do not over-weight `usage_role`; top3 def only in rank |
| **NHL** | Forwards have no `line_combo` — line context unknown for non-D props |
| **Tennis** | `distribution_std` ~42% — treat `confidence_score` as provisional on sparse rows |

## Priority gaps (from matrix)

1. **Tier 4:** `implied_prob` / `price` (odds source)
2. **Export:** MLB park/weather, WNBA top3/intel, NHL forward `line_combo`, Tennis hold/break
3. **Wiring:** `game_date`, `standard_line`, audit `read_fields_missing` ordering
4. **Data:** Alt-book lines (UD/DK), injury/B2B fill, usage_boost on FS

Update the CSV when a row moves from Partial/No → Yes.
