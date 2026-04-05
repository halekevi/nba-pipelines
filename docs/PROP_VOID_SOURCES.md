# Where prop VOIDs come from (and what fixes them)

Prop Evaluation shows **`result`** from `props_history` (or bundled JSON). A **VOID** with a numeric **actual** usually meant the grader could not resolve **line** or **side**, or an eligibility flag forced VOID before the box score was applied.

## By pipeline

| Source | Typical VOID cause | In-repo fix |
|--------|-------------------|-------------|
| **NBA / NBA1H / NBA1Q / WCBB** (`slate_grader.py`) | Upstream `void_reason` (blocked / dropped) forced VOID while actuals existed; `Series.get("line", …)` returned NaN when `line` column existed empty and `line_score` had the value | Grade from actual+line when outcome is decidable; `_row_first_numeric` / `_coalesce_line_from_line_score` |
| **CBB** (`grade_cbb_full_slate.py`) | `line` empty but `line_score` filled; direction only in `bet_direction` while `_dir` column was wrong | Per-row `line`/`line_score`/`Line` coalesce; per-row OVER/UNDER from multiple columns; `line_num`/`dir_played` match that row |
| **NHL** (`nhl_grader_advanced.py`) | Same NaN `line` key issue on slate rows | `first_numeric_in_slate_row` for line; direction via `first_over_under_in_slate_row` |
| **MLB / Soccer** (`nhl_soccer_grader.py`) | Missing line from wrong column; missing `bet_direction`; pushes stored as **VOID** | Line + direction coalesce; **PUSH** result (not VOID) on exact tie |
| **Soccer advanced** (`soccer_grader_advanced.py`) | Line/direction only read from single columns | Shared slate row helpers |
| **Any sport (archive/UI)** | Stale graded workbook already written | `step_archive` recomputes HIT/MISS/PUSH from actual+line+side; `utils/prop_reconcile.py` on archive + `/api/grades/props` + bundle export |

## Legitimate VOID (expected)

- **No actual** in the actuals file (DNP, no join, wrong date).
- **No numeric line** after all coalesces.
- **No OVER/UNDER** on the row (after column fallbacks).
- **Non-O/U markets** (some specials) — reconciliation only applies standard O/U math.

## Safety net (always on for Prop Evaluation API)

`reconcile_props_history_dict` runs on each row returned by `/api/grades/props`, so the UI can show HIT/MISS even if an older graded file or DB row still says VOID.
