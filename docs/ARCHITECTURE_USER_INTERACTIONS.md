# PropORACLE — C4 diagrams (user interactions)

C4 views of how people interact with PropORACLE: browsing slates and tickets, reviewing grades and income, using payout tools, running pipelines from Home, and using the Android app.

**How to view:** Open this file in Cursor/VS Code Markdown preview, or on GitHub. Diagrams use [Mermaid C4](https://mermaid.js.org/syntax/c4.html).

---

## Level 1 — System context

Who touches the system and what they care about. External APIs (PrizePicks, ESPN) are pipeline inputs; bettors do not call them directly.

```mermaid
C4Context
    title PropORACLE — System Context (User Interactions)

    Person(bettor, "Bettor / Analyst", "Reviews daily slates, built tickets, grades, P&L, and payout math")
    Person(operator, "Operator", "Runs or schedules pipelines, grading, and publishes artifacts for the UI")

    System(proporacle, "PropORACLE", "Multi-sport prop pipeline + web UI for slate review, ticket building, grading, and income tracking")

    System_Ext(prizepicks, "PrizePicks API", "Daily prop lines (pipeline fetch only)")
    System_Ext(stats, "ESPN / stat caches", "Player and team context for enrichment")
    System_Ext(railway, "Railway (optional)", "Hosts Flask when not running locally")
    System_Ext(prizeplatform, "PrizePicks app/site", "Where the bettor places real entries (outside PropORACLE)")

    Rel(bettor, proporacle, "Browses slates, tickets, grades, income, payouts", "HTTPS")
    Rel(operator, proporacle, "Triggers pipeline steps, grades, publishes UI files", "Browser UI and/or PowerShell on PC")
    Rel(operator, prizepicks, "Indirect — pipeline Step 1 fetch", "API")
    Rel(operator, stats, "Indirect — pipeline enrichment", "Cache / API")
    Rel(proporacle, railway, "Deploys web app", "Docker / Gunicorn")
    Rel(bettor, prizeplatform, "Places bets", "Mobile / web")
    Rel(proporacle, prizepicks, "Fetches slates", "Batch")
    Rel(proporacle, stats, "Enriches props", "Batch")
```

---

## Level 2 — Container diagram

Runtime pieces involved in **user-visible** behavior (not every script in `scripts/`).

```mermaid
C4Container
    title PropORACLE — Containers (User Interaction Path)

    Person(bettor, "Bettor / Analyst")
    Person(operator, "Operator")

    Container_Boundary(client, "Client devices") {
        Container(browser, "Web browser", "Chrome, Safari, etc.", "Loads Flask pages and calls JSON APIs")
        Container(mobile, "PropORACLE Android app", "Capacitor WebView", "Bundled static www/ OR remote Railway URL; optional OTA bundle")
    }

    Container_Boundary(host, "Application host") {
        Container(flask, "PropORACLE Web App", "Python Flask (ui_runner/app.py)", "Pages: Home, Tickets, Grades, Income, Payout. APIs: slate, tickets, grades, pipeline run, mobile bundle")
        Container(batch, "Pipeline & grader jobs", "PowerShell + Python subprocesses", "run_pipeline.ps1, run_daily.ps1, run_grader.ps1; also spawned by POST /api/run")
    }

    Container_Boundary(data, "Published & persistent data") {
        ContainerDb(artifacts, "UI artifacts", "HTML, JSON, XLSX under ui_runner/templates and outputs/", "Read by Flask; written by pipeline/grader scripts")
        ContainerDb(sqlite, "Ticket / entries DB", "SQLite (e.g. data/db/MyTicketPerformance.db)", "Income and entry capture when configured")
        ContainerDb(cache, "Caches & grade history", "JSON/CSV under data/, sport folders", "Consistency API, grade-history, model metrics")
    }

    System_Ext(railway, "Railway", "Optional public HTTPS host")
    System_Ext(prizepicks, "PrizePicks API")
    System_Ext(prizeplatform, "PrizePicks (betting)")

    Rel(bettor, browser, "Uses")
    Rel(bettor, mobile, "Uses")
    Rel(operator, browser, "Runs pipelines from Home", "POST /api/run")
    Rel(operator, batch, "Schedules daily run", "Task Scheduler / PowerShell")

    Rel(browser, flask, "HTTPS — pages + /api/*", "Same origin")
    Rel(mobile, flask, "Remote mode: same as browser", "HTTPS")
    Rel(mobile, artifacts, "Bundled mode: file:// or packaged www/", "No Flask required for static pages")

    Rel(flask, artifacts, "Serves templates, JSON, static eval HTML")
    Rel(flask, sqlite, "Reads/writes entries & income paths")
    Rel(flask, cache, "Reads consistency, grade history")
    Rel(flask, batch, "Starts subprocess from commands.json", "POST /api/run → job poll")
    Rel(batch, artifacts, "Writes slate_latest.json, tickets, graded HTML")
    Rel(batch, cache, "Refreshes caches, consistency JSON")
    Rel(batch, prizepicks, "Step 1 fetch")

    Rel(flask, railway, "Deployed as main:app", "Gunicorn")
    Rel(bettor, prizeplatform, "Places bets outside PropORACLE")
```

---

## Level 3 — Web app components (user-facing)

Inside the Flask container: main surfaces a user navigates via `_site_nav.html` (Home · Tickets · Grades · Income · Payouts).

```mermaid
C4Component
    title PropORACLE Web App — User-Facing Components

    Person(bettor, "Bettor / Analyst")

    Container_Boundary(flask, "ui_runner/app.py + routes") {
        Component(home, "Home / Slate hub", "index.html", "Today's slate, sport filters, pipeline status, optional run controls")
        Component(tickets, "Tickets UI", "/tickets + uniform-tickets-panel.js", "Built slips; /api/uniform-tickets/*")
        Component(grades, "Grades hub", "/grades, slate_eval_*.html", "Graded props, ticket eval pages; /api/grades/*")
        Component(income, "Income / P&L", "/income", "Grade history, sport breakdown; /api/grade-history")
        Component(payout, "Payout tools", "/payout, ladder, log", "Multiplier estimate, rate cards, observation log")
        Component(pipeline_api, "Pipeline control API", "POST /api/run, /api/job/*", "Operator triggers commands.json steps from Home")
        Component(slate_api, "Slate & ticket APIs", "/api/slate*, /api/full-slate, /api/tickets-*", "JSON for home and ticket widgets")
        Component(mobile_api, "Mobile OTA API", "/api/mobile/bundle-*", "Version check + zip for Capacitor OTA")
        Component(consistency_api, "Player consistency API", "routes/consistency.py", "/api/hot-players, /api/player-consistency")
    }

    ContainerDb(artifacts, "ui_runner/templates + static")
    Container(batch, "Subprocess runner", "RunJob + commands.json")

    Rel(bettor, home, "Opens /")
    Rel(bettor, tickets, "Opens /tickets")
    Rel(bettor, grades, "Opens /grades")
    Rel(bettor, income, "Opens /income")
    Rel(bettor, payout, "Opens /payout")

    Rel(home, slate_api, "fetch slate, status")
    Rel(home, pipeline_api, "Operator: start job")
    Rel(tickets, slate_api, "uniform tickets JSON")
    Rel(grades, artifacts, "Static eval HTML + JSON APIs")
    Rel(income, artifacts, "Grade history files")
    Rel(payout, artifacts, "Rate cards, observations CSV")

    Rel(slate_api, artifacts, "Reads slate_latest.json, sport JSON")
    Rel(pipeline_api, batch, "spawn py/ps steps")
    Rel(mobile_api, artifacts, "Zip mobile/www for OTA")
    Rel(consistency_api, artifacts, "player_consistency.json")
```

---

## User journeys (dynamic)

Typical **bettor** session vs **operator** workflow.

### Bettor — review and decide (no pipeline write)

```mermaid
sequenceDiagram
    actor Bettor
    participant Browser
    participant Flask as PropORACLE Web
    participant Artifacts as UI artifacts

    Bettor->>Browser: Open app (/, Railway or localhost)
    Browser->>Flask: GET /
    Flask->>Artifacts: Read slate_latest.json, config
    Flask-->>Browser: Home (slate cards, nav)

    Bettor->>Browser: Tickets
    Browser->>Flask: GET /api/uniform-tickets/latest
    Flask->>Artifacts: tickets / uniform JSON
    Flask-->>Browser: Built slips panel

    Bettor->>Browser: Grades
    Browser->>Flask: GET /api/grades/* or static ticket_eval_*.html
    Flask->>Artifacts: Graded props / eval HTML
    Flask-->>Browser: Hit rates, ticket KPIs

    Bettor->>Browser: Income
    Browser->>Flask: GET /api/grade-history
    Flask->>Artifacts: Grade history / breakdown cache
    Flask-->>Browser: P&L view

    Bettor->>Browser: Payout (optional)
    Browser->>Flask: POST /api/payout/estimate-mult
    Flask-->>Browser: Multiplier estimate

    Note over Bettor: Places entries in PrizePicks app (outside PropORACLE)
```

### Operator — refresh slate for everyone

```mermaid
sequenceDiagram
    actor Operator
    participant Browser
    participant Flask as PropORACLE Web
    participant Batch as Pipeline subprocess
    participant Artifacts as UI artifacts

    alt From Home UI
        Operator->>Browser: Home → Run pipeline step
        Browser->>Flask: POST /api/run {pipeline, command_id}
        Flask->>Batch: subprocess (commands.json)
        loop Poll progress
            Browser->>Flask: GET /api/job/{id}
            Flask-->>Browser: status, log lines
        end
        Batch->>Artifacts: Write step outputs, combined tickets
    else Scheduled / desktop
        Operator->>Batch: run_pipeline.ps1 / run_daily.ps1
        Batch->>Artifacts: Publish templates + outputs/
    end

    Batch->>Artifacts: slate_latest.json, tickets_latest.json, eval HTML
    Note over Bettor: Next page load sees updated artifacts
```

### Mobile — bundled vs remote

```mermaid
flowchart LR
    subgraph bundled["Bundled APK (default)"]
        M1[Capacitor WebView] --> W1[mobile/www static HTML]
        W1 -.optional.-> API1[LAN/Railway Flask APIs]
    end

    subgraph remote["Remote mode (sync:url)"]
        M2[Capacitor WebView] --> F2[Railway Flask]
        F2 --> W2[Same pages as browser]
    end

    subgraph ota["OTA update (remote host)"]
        M3[proporacle-ota.js] --> V[/api/mobile/bundle-version]
        V --> Z[/api/mobile/bundle.zip]
        Z --> W3[Refresh mobile/www in WebView storage]
    end
```

---

## Interaction map (pages → APIs)

Quick reference for the main nav tabs (see `ui_runner/templates/_site_nav.html`).

| User goal | Page | Primary APIs / assets |
|-----------|------|------------------------|
| See today's slate & model context | `/` Home | `/api/slate`, `/api/full-slate`, `/api/slate-display-date`, `/api/pipeline/status`, `/api/hot-players` |
| Review built tickets | `/tickets` | `/api/uniform-tickets/latest`, `/api/uniform-tickets/<date>` |
| Check results & eval | `/grades` | `/api/graded-props`, `/api/grades/*`, `slate_eval_<date>.html`, `ticket_eval_<date>.html` |
| Track P&L | `/income` | `/api/grade-history` |
| Payout math & logging | `/payout` | `/api/payout/estimate-mult`, `/api/payout/rate-cards`, POST log endpoints |
| Run pipeline (operator) | Home controls | `POST /api/run`, `GET /api/job/<id>`, `GET /api/jobs` |
| Mobile offline UI | Capacitor `www/` | Static HTML; OTA: `/api/mobile/bundle-version`, `bundle.zip` |

---

## Related docs

- [USE_CASE_DIAGRAM.md](USE_CASE_DIAGRAM.md) — **UML use case diagram** (PlantUML + catalog)
- [PROJECT_LAYOUT.md](PROJECT_LAYOUT.md) — folder contracts
- [guides/APP_SYSTEM_STATUS.md](guides/APP_SYSTEM_STATUS.md) — pipeline flow (batch-centric)
- [mobile/README.md](../mobile/README.md) — bundled vs remote Android
