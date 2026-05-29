# PropORACLE — Architecture & User Interactions

> **Diagrams in this doc** render natively in GitHub, GitLab, and Cursor's Markdown preview (Mermaid support built-in). For full UML notation (ovals, stick figures, `<<include>>`), open the `.puml` files in `docs/diagrams/` with the PlantUML extension or paste into [plantuml.com](https://www.plantuml.com/plantuml).

---

## Table of contents

1. [C4 Level 1 — System context](#c4-level-1--system-context)
2. [C4 Level 2 — Containers](#c4-level-2--containers)
3. [C4 Level 3 — Flask API components](#c4-level-3--flask-api-components)
4. [Use case summary](#use-case-summary)
5. [Sport pipeline coverage](#sport-pipeline-coverage)
6. [Related files](#related-files)

---

## C4 Level 1 — System context

Who uses PropORACLE and what external systems it depends on.

```mermaid
C4Context
  title PropORACLE — System Context

  Person(bettor,     "Bettor / Analyst",  "Reviews daily props, EV scores, tickets, grades, and P&L")
  Person(operator,   "Operator",          "Runs pipelines, retrains ML model, grades slates")

  System(prop,       "PropORACLE",        "Multi-sport prop-betting analytics platform")

  System_Ext(sb,     "Sportsbook APIs",   "Odds and lines feed")
  System_Ext(stats,  "Stats APIs",        "NBA / NHL / MLB / Soccer stats")
  System_Ext(wt,     "WNBA / Tennis CDP", "Playwright stealth scrape")
  System_Ext(rail,   "Railway",           "Cloud hosting")
  System_Ext(pp,     "PrizePicks",        "Real-money entries (outside system)")

  Rel(bettor,    prop,  "Views props, tickets, grades, income", "HTTPS")
  Rel(operator,  prop,  "Runs pipelines, grades, publishes",    "PS1 / HTTPS")
  Rel(prop,      sb,    "Fetches odds + lines")
  Rel(prop,      stats, "Fetches player + game stats")
  Rel(prop,      wt,    "Scrapes slate data")
  Rel(prop,      rail,  "Deployed on")
  Rel(bettor,    pp,    "Places entries (external)")
```

---

## C4 Level 2 — Containers

Which container each user touches and how data flows through the system.

```mermaid
C4Container
  title PropORACLE — Containers

  Person(bettor,   "Bettor / Analyst")
  Person(operator, "Operator")

  System_Boundary(sys, "PropORACLE") {
    Container(web,      "Web App",       "Jinja2 + JS · Railway",         "Tickets, grades, income, payout, slate")
    Container(mob,      "Mobile App",    "Capacitor · server.url",        "Top edges, sparklines, OTA updates")
    Container(api,      "Flask API",     "Python · Gunicorn · Railway",   "/api/props /api/grades /api/tickets")
    Container(pipeline, "Pipeline",      "Python · run_daily.ps1",        "Steps 1–8 per sport, daily PS1 orch.")
    Container(ml,       "ML Model",      "XGBoost · edge_model_unified",  "AUC 0.7743 · Platt + isotonic cal.")
    ContainerDb(cache,  "JSON Cache",    "Flat-file · Railway",           "Step8 output + snapshot archives")
    Container(ps1,      "run_daily.ps1", "PowerShell · local",            "Orchestrates full daily run")
  }

  System_Ext(sb,    "Sportsbook APIs")
  System_Ext(stats, "Stats APIs")
  System_Ext(wt,    "WNBA/Tennis CDP")

  Rel(bettor,    web,      "Views picks, grades, income",   "HTTPS")
  Rel(bettor,    mob,      "Views top edges on mobile",     "HTTPS")
  Rel(operator,  ps1,      "Triggers daily run",            "PowerShell")
  Rel(operator,  web,      "Monitors pipeline, views data", "HTTPS")
  Rel(web,       api,      "Reads props, grades, tickets",  "JSON / HTTP")
  Rel(mob,       api,      "Reads top edges, sparklines",   "JSON / HTTP")
  Rel(api,       cache,    "Reads cached output",           "File I/O")
  Rel(ps1,       pipeline, "Triggers sport pipelines",      "subprocess")
  Rel(pipeline,  ml,       "Scores props via step7",        "pkl · predict_proba")
  Rel(pipeline,  cache,    "Writes step8 JSON output",      "File I/O")
  Rel(pipeline,  sb,       "Fetches odds + lines")
  Rel(pipeline,  stats,    "Fetches player + game stats")
  Rel(pipeline,  wt,       "Scrapes WNBA / Tennis slate")
```

---

## C4 Level 3 — Flask API components

Internal structure of the Flask API container.

```mermaid
C4Component
  title PropORACLE — Flask API Components

  Container_Boundary(api, "Flask API · Python / Gunicorn") {
    Component(home,    "Home route",          "/  /api/run",          "Slate UI, pipeline trigger")
    Component(tickets, "Tickets route",       "/tickets /api/tickets","EV sort, HIDE SKIP, Today's Best")
    Component(grades,  "Grades route",        "/grades /api/grades",  "Hub iframe, graded props feed")
    Component(income,  "Income route",        "/income",              "P&L dashboard")
    Component(payout,  "Payout route",        "/payout",              "Multiplier, rate cards, log")

    Component(edge,    "step8_edge_direction","utils/",               "edge = projection − line")
    Component(tier,    "tier_assignment",     "utils/",               "A–D tier, 5-label defense")
    Component(teval,   "build_ticket_eval",   "utils/",               "Pool exhaustion, outcome split")
    Component(train,   "train_edge_model",    "utils/",               "XGBoost retrain, temporal split")
  }

  ContainerDb(cache, "JSON Cache", "Flat-file")
  Container(ml,      "ML Model",   "XGBoost pkl")

  Rel(tickets, edge,  "reads edge direction")
  Rel(tickets, tier,  "reads tier labels")
  Rel(tickets, teval, "builds ticket pool")
  Rel(home,    train, "triggers retrain")
  Rel(edge,    cache, "reads step8 JSON")
  Rel(tier,    cache, "reads step8 JSON")
  Rel(teval,   cache, "reads step8 JSON")
  Rel(train,   ml,    "writes new pkl")
```

---

## Use case summary

### Actors

| Actor | Type | Description |
|---|---|---|
| Bettor / Analyst | Person | Primary consumer — browses slate, tickets, grades, income, payout tools, and mobile app |
| Operator | Person | Runs pipelines, grades slates, retrains model, publishes artifacts; also browses as Bettor |
| Task Scheduler | System actor | Automated — triggers `run_daily.ps1` and grader on schedule |
| PrizePicks | External | Real-money entries happen here; PropORACLE only supports research and ticket building |

### Use case packages

| Package | Use cases |
|---|---|
| **Slate & research** | View home slate, browse by sport, hot players / consistency, model performance, export Excel |
| **Tickets** | Latest tickets, by date, EV & win-rate summaries, ticket backtest |
| **Grades & evaluation** | Grades hub, browse graded props, slate eval report, ticket eval report |
| **Income & tracking** | Income / P&L dashboard, grade history & sport breakdown |
| **Payout tools** | Estimate multiplier, rate cards & combo table, log observation, payout ladder, export logs |
| **Mobile app** | Bundled offline UI, remote web UI in app shell, OTA bundle update |
| **Pipeline & ops** | Run step from UI, monitor job, pipeline status, daily pipeline, sport pipeline, grade slate, publish artifacts |

### Key `<<include>>` relationships

```
Run daily pipeline  ──includes──►  Run sport pipeline
Run sport pipeline  ──includes──►  Fetch PrizePicks slate
Run sport pipeline  ──includes──►  Enrich & rank props
Run sport pipeline  ──includes──►  Build combined tickets
Run daily pipeline  ──includes──►  Publish UI artifacts
Grade completed slate ─includes──► Publish UI artifacts
Run pipeline step (UI) ─includes─► Monitor pipeline job
OTA bundle update   ──extends───►  Verify deploy / health
```

---

## Sport pipeline coverage

| Sport | AUC (2026-05-25) | Step8 join rate | Notes |
|---|---|---|---|
| MLB | 0.7268 | ~99.1% | Strong — name_aliases fix resolved join rate |
| NBA | 0.6175 | — | Watch |
| NBA1H | 0.4511 | — | ⚠ Below random on May slice — suppress or investigate |
| NHL | 0.6905 | ~38% | ⚠ Low join rate — backfill 13 dates (Feb/Mar) |
| Soccer | 0.7478 | — | Strong — Level 2 opponent context planned |
| WNBA | 0.6954 | — | Chrome131 impersonation working |
| Tennis | 0.6624 | ~4% | ⚠ Step8 coverage gaps May 19/24 — excluded from 2026-05-25 retrain |

**Overall model AUC:** 0.7743 (117,548 rows · `edge_model_unified.pkl` · 2026-05-25)
**Backup:** `edge_model_unified_pre_retrain_2026-05-21.pkl`

---

## Related files

| File | Purpose |
|---|---|
| `docs/diagrams/c4-context.puml` | C4 Level 1 — System context (PlantUML) |
| `docs/diagrams/c4-containers.puml` | C4 Level 2 — Containers (PlantUML) |
| `docs/diagrams/c4-components-flask.puml` | C4 Level 3 — Flask API components (PlantUML) |
| `docs/diagrams/proporacle-use-cases.puml` | Full UML use case diagram (PlantUML) |
| `docs/USE_CASE_DIAGRAM.md` | Use case catalog + render instructions |
| `docs/PROJECT_LAYOUT.md` | Folder contracts |
| `utils/step8_edge_direction.py` | Canonical edge computation |
| `utils/train_edge_model.py` | ML model retraining (`--temporal-split`) |
| `utils/build_ticket_eval.py` | Ticket pool exhaustion + outcome eval |
| `run_daily.ps1` | Full daily pipeline orchestration |
