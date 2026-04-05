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
