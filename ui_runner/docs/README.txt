propOracle UI — docs folder
===========================

Flask serves pages and JSON from ../templates/ (not from this folder).

Files here (e.g. tickets_latest.json / tickets_latest.html) are documentation
or snapshot samples only. The live tickets/slate JSON that Railway/GitHub raw
uses are always under ../templates/.

Railway / deploy — Grades tab
-----------------------------
- Slate iframe: /grades/slate_eval_{date}.html → file must exist under ui_runner/templates/
  (build_grades_html.py or run_grader.ps1 copies there). Commit HTML to git; .gitignore does
  NOT ignore these by default (see repo .gitignore).
- Tickets iframe: ticket_eval_{date}.html from scripts/build_ticket_eval.py --date …
- Prop evaluation + CLV: need data/cache/*_props_history.db on the host OR run
  scripts/export_grades_props_bundle.py --date YYYY-MM-DD and commit
  ui_runner/data/grades_props/YYYY-MM-DD.json for bundled props without SQLite.
- The Grades page calls GET /api/grades/report_dates to list which slate_eval dates exist on
  disk (avoids relying on dozens of HEAD probes through a CDN).

Canonical Grades + Prop setup (2026-05)
----------------------------------------
- Canonical entry page is /grades (indexGrades.html).
- Canonical slate source is /grades/slate_eval_{date}.html (same-origin iframe).
- Canonical date discovery is GET /api/grades/report_dates.
- Canonical props source order:
  1) GET /api/graded-props?date=YYYY-MM-DD when response contains props[]
  2) GET /api/grades/page-rows?date=YYYY-MM-DD (paged fallback used on Railway summary payloads)
  3) GET /api/grades/props?date=YYYY-MM-DD (archive DB / bundle fallback)
- Canonical toolbar behavior:
  - Toolbar may be moved into the slate iframe.
  - iframe window actions (switchTab / stepDate / jumpToDate) must bridge to parent handlers,
    otherwise Prop/Ticket tab buttons appear unclickable.
- If slate looks blank:
  - Check browser console for inline script SyntaxError first.
  - Verify /api/grades/report_dates and /grades/slate_eval_{date}.html both return 200.
- If Prop tab is empty but slate exists:
  - Verify /api/graded-props payload shape (props[] vs summary-only count/breakdown).
  - If summary-only, /api/grades/page-rows fallback should populate cards.
