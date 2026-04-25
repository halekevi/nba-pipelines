import os
import shutil
import re
import json
from pathlib import Path
import combined_slate_tickets

def process_template(file_path, templates_dir):
    """Recursively processes Jinja2 includes and strips placeholders."""
    if not file_path.exists():
        print(f"WARNING: Template file not found: {file_path}")
        return ""

    content = file_path.read_text(encoding="utf-8")

    # Handle {% include '...' %}
    def replace_include(match):
        include_name = match.group(1).strip("'\"")
        include_path = templates_dir / include_name
        return process_template(include_path, templates_dir)

    content = re.sub(r'\{%\s*include\s+(.*?)\s*%\}', replace_include, content)

    # Replace absolute-style static paths with relative ones
    content = content.replace('href="/static/', 'href="static/')
    content = content.replace('src="/static/', 'src="static/')
    content = content.replace("url('/static/", "url('static/")
    content = content.replace('url("/static/', 'url("static/')
    content = content.replace("url('/static/css/", "url('static/")
    content = content.replace('url("/static/css/', 'url("static/')
    content = content.replace("url('static/css/", "url('static/")
    content = content.replace('url("static/css/', 'url("static/')

    return content

def generate_bundle():
    # Define paths
    ROOT_DIR = Path(__file__).resolve().parent.parent
    STATIC_DIR = ROOT_DIR / "ui_runner" / "static"
    TEMPLATES_DIR = ROOT_DIR / "ui_runner" / "templates"
    MOBILE_WWW_DIR = ROOT_DIR / "mobile" / "www"
    DATA_DIR = ROOT_DIR / "data"

    # Ensure mobile/www exists and is clean
    if MOBILE_WWW_DIR.exists():
        for item in MOBILE_WWW_DIR.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    else:
        MOBILE_WWW_DIR.mkdir(parents=True, exist_ok=True)

    # Copy static assets
    print(f"Copying static assets from {STATIC_DIR} to {MOBILE_WWW_DIR / 'static'}...")
    shutil.copytree(STATIC_DIR, MOBILE_WWW_DIR / "static")

    # Process templates and write to mobile/www root
    # Tickets page source priority (mobile app should show the Tickets generator page):
    # 1) tickets_latest.html (generator + slips UI)
    # 2) tickets_built.html
    # 3) latest dated ticket_eval_YYYY-MM-DD.html (fallback only)
    # 4) ticket_eval_latest.html
    ticket_source = "tickets_latest.html"
    if not (TEMPLATES_DIR / ticket_source).exists():
        if (TEMPLATES_DIR / "tickets_built.html").exists():
            ticket_source = "tickets_built.html"
        else:
            dated_ticket_pages = sorted(
                [p for p in TEMPLATES_DIR.glob("ticket_eval_*.html") if re.fullmatch(r"ticket_eval_\d{4}-\d{2}-\d{2}\.html", p.name)],
                reverse=True
            )
            if dated_ticket_pages:
                ticket_source = dated_ticket_pages[0].name
            elif (TEMPLATES_DIR / "ticket_eval_latest.html").exists():
                ticket_source = "ticket_eval_latest.html"

    PAGES = {
        "index.html": "index.html",
        ticket_source: "tickets.html",
        "indexGrades.html": "grades.html",
        "dashboard_income.html": "income.html",
        "payout_calculator.html": "payout.html"
    }

    for src_name, dest_name in PAGES.items():
        src_path = TEMPLATES_DIR / src_name
        dest_path = MOBILE_WWW_DIR / dest_name

        if src_path.exists():
            print(f"Processing {src_path} to {dest_path}...")

            # Process includes recursively
            content = process_template(src_path, TEMPLATES_DIR)

            # Fix navigation links for static bundle
            content = content.replace('href="/"', 'href="index.html"')
            content = content.replace('href="/tickets"', 'href="tickets.html"')
            content = content.replace('href="/grades"', 'href="grades.html"')
            content = content.replace('href="/income"', 'href="income.html"')
            content = content.replace('href="/payout"', 'href="payout.html"')

            # Mobile bundle runs from local files (not Railway routes).
            # Rewrite grades page report/API paths to local assets.
            if dest_name == "grades.html":
                content = content.replace(
                    "const REPORT_URL_TEMPLATE = '/grades/slate_eval_{date}.html';",
                    "const REPORT_URL_TEMPLATE = 'slate_eval_{date}.html';"
                )
                content = content.replace(
                    "const TICKET_EVAL_URL_TEMPLATE = '/grades/ticket_eval_{date}.html';",
                    "const TICKET_EVAL_URL_TEMPLATE = 'ticket_eval_{date}.html';"
                )
                content = content.replace(
                    "fetch('/api/grades/report_dates', { cache: 'no-store' })",
                    "fetch('grades_report_dates.json', { cache: 'no-store' })"
                )
                # Offline/mobile: shim Grades API calls to local bundled JSON files.
                grades_mobile_bootstrap = """
<script>
(function () {
  const _origFetch = window.fetch.bind(window);
  function _jsonResp(obj) {
    return new Response(JSON.stringify(obj), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });
  }
  function _dateParamFrom(urlStr) {
    try {
      const u = new URL(urlStr, window.location.href);
      return u.searchParams.get('date') || '';
    } catch (_e) { return ''; }
  }
  function _extractDate(raw) {
    const m = String(raw || '').match(/\\d{4}-\\d{2}-\\d{2}/);
    return m ? m[0] : '';
  }
  async function _loadGradedPropsByDate(ds) {
    const d = _extractDate(ds);
    if (!d) return null;
    const file = `graded_props_${d}.json`;
    const r = await _origFetch(file, { cache: 'no-store' });
    if (!r.ok) return null;
    return r.json();
  }
  window.fetch = async function (input, init) {
    const urlStr = (typeof input === 'string') ? input : (input && input.url ? input.url : String(input || ''));
    const path = urlStr.replace(window.location.origin, '');

    if (path.includes('/api/grades/report_dates')) {
      return _origFetch('grades_report_dates.json', init);
    }
    if (path.includes('/api/grades/insights')) {
      return _origFetch('grades_insights.json', init);
    }
    if (path.includes('/api/grades/archive_dates')) {
      return _origFetch('grades_archive_dates.json', init);
    }
    if (path.includes('/api/grade-history')) {
      return _origFetch('data/grade_history.json', init);
    }
    if (path.includes('/api/graded-props')) {
      const ds = _dateParamFrom(urlStr);
      const j = await _loadGradedPropsByDate(ds);
      if (j) return _jsonResp(j);
      return _jsonResp({ ok: true, date: _extractDate(ds), props: [], source: 'mobile_bundle_missing' });
    }
    if (path.includes('/api/grades/props')) {
      const ds = _dateParamFrom(urlStr);
      const j = await _loadGradedPropsByDate(ds);
      const props = Array.isArray(j && j.props) ? j.props : [];
      let nHit = 0, nMiss = 0;
      props.forEach((p) => {
        const ru = String((p && p.result) || '').toUpperCase();
        if (ru === 'HIT') nHit += 1;
        else if (ru === 'MISS') nMiss += 1;
      });
      return _jsonResp({ ok: true, props: props, n_hit: nHit, n_miss: nMiss, n: props.length, n_returned: props.length, truncated: false });
    }
    return _origFetch(input, init);
  };
})();
</script>
"""
                content = content.replace("</body>", grades_mobile_bootstrap + "\n</body>")
            elif dest_name == "index.html":
                # Home page slate data must come from bundled JSON in offline/mobile mode.
                content = content.replace(
                    'fetch("/api/slate", {cache: \'no-store\'})',
                    "fetch('slate_latest.json', {cache: 'no-store'})"
                )
                content = content.replace(
                    "fetch('/api/slate', {cache: 'no-store'})",
                    "fetch('slate_latest.json', {cache: 'no-store'})"
                )
                content = content.replace(
                    'fetch("/api/pipeline/status", {cache:\'no-store\'})',
                    "fetch('pipeline_status.json', {cache:'no-store'})"
                )
                content = content.replace(
                    'fetch(`/api/slate-sport/${encodeURIComponent(sport)}`, {cache: \'no-store\'})',
                    "fetch(`slate_sport_${encodeURIComponent(sport)}.json`, {cache: 'no-store'})"
                )
                content = content.replace(
                    "fetch('/api/slate-excel', {cache: 'no-store'})",
                    "fetch('slate_excel.json', {cache: 'no-store'})"
                )
            elif dest_name == "income.html":
                # Jinja strips can leave invalid JS in static bundle.
                content = re.sub(r"const\s+points\s*=\s*;", "const points = [];", content)
                mobile_income_bootstrap = """
  <script>
    (function () {
      const HISTORY_URL = 'data/grade_history.json';
      const fmtMoney = (n) => (Number.isFinite(n) ? `$${n.toFixed(2)}` : '$0.00');
      const fmtPct = (n) => (Number.isFinite(n) ? `${n.toFixed(1)}%` : '0.0%');
      const clsFor = (n, pos = 'num-pos', neg = 'num-neg') => (n > 0 ? pos : (n < 0 ? neg : ''));

      function parseRows(raw) {
        const rows = Array.isArray(raw) ? raw : (raw && Array.isArray(raw.runs) ? raw.runs : []);
        return rows
          .map((r) => {
            const tickets = Number(r.n_tickets ?? r.tickets ?? 0);
            const wins = Number(r.wins ?? 0);
            const guarantees = Number(r.guarantees ?? 0);
            const losses = Number(r.losses ?? 0);
            const decided = Math.max(0, wins + losses);
            const paid = Math.max(0, wins + guarantees);
            const net = Number(r.net_dollars ?? r.net_per_10 ?? 0);
            const roi = Number(r.roi_pct ?? ((tickets > 0) ? (net / (tickets * 10) * 100) : 0));
            return {
              date: String(r.date || ''),
              tickets, wins, guarantees, losses, decided, paid,
              net, roi
            };
          })
          .filter((r) => /^\\d{4}-\\d{2}-\\d{2}$/.test(r.date))
          .sort((a, b) => a.date.localeCompare(b.date));
      }

      function render(rowsAsc) {
        const rowsDesc = [...rowsAsc].reverse();
        const totalTickets = rowsAsc.reduce((s, r) => s + r.tickets, 0);
        const totalDecided = rowsAsc.reduce((s, r) => s + r.decided, 0);
        const totalPaid = rowsAsc.reduce((s, r) => s + r.paid, 0);
        const totalNet = rowsAsc.reduce((s, r) => s + r.net, 0);
        const winRate = totalDecided > 0 ? (totalPaid / totalDecided) * 100 : 0;
        const roi = totalTickets > 0 ? (totalNet / (totalTickets * 10)) * 100 : 0;

        let streak = '—';
        let streakLen = 0;
        let streakSign = 0;
        for (let i = rowsAsc.length - 1; i >= 0; i--) {
          const sign = rowsAsc[i].net > 0 ? 1 : (rowsAsc[i].net < 0 ? -1 : 0);
          if (sign === 0) continue;
          if (streakSign === 0) { streakSign = sign; streakLen = 1; continue; }
          if (sign === streakSign) streakLen += 1; else break;
        }
        if (streakLen > 0) streak = `${streakSign > 0 ? 'W' : 'L'}${streakLen}`;

        const sumVals = document.querySelectorAll('.summary-grid .sum-value');
        if (sumVals.length >= 5) {
          sumVals[0].textContent = String(totalTickets);
          sumVals[1].textContent = `${winRate.toFixed(1)}% (${totalPaid}/${totalDecided})`;
          sumVals[2].textContent = fmtMoney(totalNet);
          sumVals[2].className = `sum-value ${clsFor(totalNet, 'sum-pos', 'sum-neg')}`;
          sumVals[3].textContent = fmtPct(roi);
          sumVals[3].className = `sum-value ${clsFor(roi, 'sum-pos', 'sum-neg')}`;
          sumVals[4].textContent = streak;
        }

        const dailyBody = document.querySelectorAll('.panel .tbl tbody')[0];
        if (dailyBody) {
          dailyBody.innerHTML = rowsDesc.map((r) => `
            <tr>
              <td>${r.date}</td>
              <td>${r.tickets}</td>
              <td>${r.wins}</td>
              <td>${r.losses}</td>
              <td>${r.guarantees}</td>
              <td class="${clsFor(r.net)}">${fmtMoney(r.net)}</td>
              <td class="${clsFor(r.roi)}">${fmtPct(r.roi)}</td>
            </tr>
          `).join('');
        }

        const emptyNote = document.querySelector('.empty-note');
        if (emptyNote) emptyNote.style.display = rowsAsc.length ? 'none' : '';

        const pts = [];
        let cum = 0;
        for (const r of rowsAsc) { cum += r.net; pts.push({ date: r.date, cum_net: Number(cum.toFixed(2)) }); }
        const host = document.getElementById('cum-chart');
        if (!host) return;
        if (!pts.length) {
          host.innerHTML = '<div class="empty-note">No cumulative data available yet.</div>';
          return;
        }
        if (typeof Plotly !== 'undefined') {
          Plotly.newPlot('cum-chart', [{
            x: pts.map(p => p.date),
            y: pts.map(p => p.cum_net),
            type: 'scatter',
            mode: 'lines+markers',
            line: { color: '#7fc7d9', width: 3 },
            marker: { size: 6, color: '#d4af37' },
            hovertemplate: '%{x}<br>$%{y:.2f}<extra></extra>'
          }], {
            autosize: true,
            margin: { t: 14, r: 20, b: 42, l: 58 },
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(255,255,255,0.02)',
            font: { color: 'rgba(255,255,255,0.85)' },
            xaxis: { title: '', gridcolor: 'rgba(255,255,255,0.06)' },
            yaxis: { title: '$', tickprefix: '$', gridcolor: 'rgba(255,255,255,0.06)', zerolinecolor: 'rgba(255,255,255,0.15)' }
          }, { displayModeBar: false, responsive: true });
        } else {
          host.innerHTML = '<div class="empty-note">Chart could not load (network or CDN blocked). Daily table still works.</div>';
        }
      }

      fetch(HISTORY_URL, { cache: 'no-store' })
        .then((r) => (r.ok ? r.json() : []))
        .then((raw) => render(parseRows(raw)))
        .catch(() => render([]));
    })();
  </script>
"""
                content = content.replace("</body>", mobile_income_bootstrap + "\n</body>")
            elif dest_name == "tickets.html":
                # Prefer rendering the tickets generator/slips view from fresh tickets_latest.json
                # into tickets_built.html so mobile matches /tickets platform content.
                tickets_json = TEMPLATES_DIR / "tickets_latest.json"
                tickets_built_tpl = TEMPLATES_DIR / "tickets_built.html"
                if tickets_json.exists() and tickets_built_tpl.exists():
                    try:
                        payload = json.loads(tickets_json.read_text(encoding="utf-8"))
                        tickets_body_html, page_title = combined_slate_tickets.render_tickets_body_html(payload)
                        content = process_template(tickets_built_tpl, TEMPLATES_DIR)
                        content = content.replace("{{ tickets_body|safe }}", tickets_body_html)
                        content = content.replace("{{ page_title }}", page_title or "PropOracle Tickets")
                    except Exception:
                        # Fallback to whatever source page was selected above.
                        pass
                # Manual builder sport chips: keep visible on touch devices.
                content = content.replace(
                    "btn.style.opacity = active ? '1' : '.55';",
                    "btn.style.opacity = active ? '1' : '.88';"
                )
                content = content.replace(
                    "btn.style.filter = active ? 'none' : 'grayscale(0.2)';",
                    "btn.style.filter = 'none';"
                )
                # Tickets bundle is sourced from ticket_eval pages (Grades nav active by default).
                # Force Tickets tab active in both top nav and mobile menu for tickets.html.
                content = content.replace(
                    'href="grades.html" class="active" title="Ticket evaluation hub"',
                    'href="grades.html" class="" title="Ticket evaluation hub"'
                )
                content = content.replace(
                    'href="grades.html" class="active"',
                    'href="grades.html" class=""'
                )
                content = content.replace(
                    'href="tickets.html" class=""',
                    'href="tickets.html" class="active"'
                )

            # Strip remaining Jinja2 placeholders, control blocks, and comments
            content = re.sub(r'\{\{.*?\}\}', '', content, flags=re.DOTALL)
            content = re.sub(r'\{%.*?%\}', '', content, flags=re.DOTALL)
            content = re.sub(r'\{#.*?#\}', '', content, flags=re.DOTALL)
            if dest_name == "income.html":
                # After Jinja stripping, invalid assignment can remain.
                content = re.sub(r"const\s+points\s*=\s*;", "const points = [];", content)

            dest_path.write_text(content, encoding="utf-8")
        else:
            print(f"WARNING: {src_path} not found, skipping...")

    # Copy dated grade report files for offline/mobile date navigation.
    report_dates = []
    for report_path in sorted(TEMPLATES_DIR.glob("slate_eval_*.html")):
        shutil.copy2(report_path, MOBILE_WWW_DIR / report_path.name)
        # Keep YYYY-MM-DD only.
        stem = report_path.stem.replace("slate_eval_", "")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stem):
            report_dates.append(stem)

    ticket_eval_dates = []
    for ticket_path in sorted(TEMPLATES_DIR.glob("ticket_eval_*.html")):
        shutil.copy2(ticket_path, MOBILE_WWW_DIR / ticket_path.name)
        stem = ticket_path.stem.replace("ticket_eval_", "")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stem):
            ticket_eval_dates.append(stem)

    graded_props_dates = []
    archive_row_counts = {}
    for gp in sorted(TEMPLATES_DIR.glob("graded_props_*.json")):
        shutil.copy2(gp, MOBILE_WWW_DIR / gp.name)
        stem = gp.stem.replace("graded_props_", "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", stem):
            continue
        graded_props_dates.append(stem)
        try:
            j = json.loads(gp.read_text(encoding="utf-8"))
            rows = j.get("props") if isinstance(j, dict) else []
            archive_row_counts[stem] = len(rows) if isinstance(rows, list) else 0
        except Exception:
            archive_row_counts[stem] = 0

    # Lightweight local replacement for /api/grades/report_dates.
    report_dates_payload = {
        "ok": True,
        "slate_eval_dates": sorted(report_dates, reverse=True),
        "ticket_eval_dates": sorted(ticket_eval_dates, reverse=True)
    }
    (MOBILE_WWW_DIR / "grades_report_dates.json").write_text(
        json.dumps(report_dates_payload, ensure_ascii=True, indent=2),
        encoding="utf-8"
    )

    (MOBILE_WWW_DIR / "grades_archive_dates.json").write_text(
        json.dumps({
            "ok": True,
            "dates": sorted(set(graded_props_dates), reverse=True),
            "row_counts": archive_row_counts,
        }, ensure_ascii=True, indent=2),
        encoding="utf-8"
    )

    # Minimal insights payload so CLV/Calibration panel always renders in bundle mode.
    (MOBILE_WWW_DIR / "grades_insights.json").write_text(
        json.dumps({
            "ok": True,
            "calibration": [],
            "clv_by_sport": [],
            "edge_bucket_hit_rate": [],
            "clv_by_prop_type": [],
            "clv_by_tier": [],
        }, ensure_ascii=True, indent=2),
        encoding="utf-8"
    )

    # Home page local slate source for mobile/offline bundle.
    src_slate_latest = TEMPLATES_DIR / "slate_latest.json"
    if src_slate_latest.exists():
        try:
            slate_payload = json.loads(src_slate_latest.read_text(encoding="utf-8"))
        except Exception:
            slate_payload = {}

        sports_payload = slate_payload.get("sports") if isinstance(slate_payload, dict) else {}
        if not isinstance(sports_payload, dict):
            sports_payload = {}

        # Build flat `picks` array expected by Home page JS (`d.picks` from /api/slate).
        mobile_picks = []
        for sport_key, rows in sports_payload.items():
            if not isinstance(rows, list):
                continue
            for r in rows:
                if not isinstance(r, dict):
                    continue
                player = str(r.get("player") or "").strip()
                initials = "".join([w[0] for w in player.split()[:2]]).upper() if player else "—"
                hit_rate = r.get("hit_rate")
                hit_pct = None
                try:
                    if hit_rate is not None:
                        hit_pct = float(hit_rate) * 100.0
                except Exception:
                    hit_pct = None
                mobile_picks.append({
                    "sport": str(sport_key).upper(),
                    "initials": initials,
                    "player": player,
                    "team": r.get("team"),
                    "opp": r.get("opp"),
                    "prop": r.get("prop"),
                    "pick_type": r.get("pick_type"),
                    # Home code expects both `pick` and `pick_type` in different places.
                    "pick": r.get("pick_type"),
                    "line": r.get("line"),
                    "dir": r.get("dir"),
                    "edge": r.get("edge"),
                    "hit": hit_pct,
                    "hit_rate": r.get("hit_rate"),
                    "l5_over": r.get("l5_over"),
                    "l5_under": r.get("l5_under"),
                    "l10_over": r.get("l10_over"),
                    "l10_under": r.get("l10_under"),
                    "l5_avg": r.get("l5_avg"),
                    "season_avg": r.get("season_avg"),
                    "actual_series": r.get("actual_series"),
                    "line_series": r.get("line_series"),
                    "game_time": r.get("game_time"),
                })

        # Write mobile-compatible slate payload (keeps original fields + adds `picks`).
        mobile_slate_payload = dict(slate_payload) if isinstance(slate_payload, dict) else {}
        mobile_slate_payload["picks"] = mobile_picks
        (MOBILE_WWW_DIR / "slate_latest.json").write_text(
            json.dumps(mobile_slate_payload, ensure_ascii=True),
            encoding="utf-8"
        )

        # Static replacements for /api/slate-sport/<sport>
        for sport_key, rows in sports_payload.items():
            safe_rows = rows if isinstance(rows, list) else []
            (MOBILE_WWW_DIR / f"slate_sport_{sport_key}.json").write_text(
                json.dumps({"ok": True, "sport": sport_key, "rows": safe_rows}, ensure_ascii=True),
                encoding="utf-8"
            )

        # Static replacement for /api/pipeline/status (used by home status cards).
        slate_date = str((slate_payload.get("date") if isinstance(slate_payload, dict) else "") or "").strip()
        modified = f"{slate_date} 12:00:00" if slate_date else ""
        status_sports = ["nba", "nba1h", "nba1q", "cbb", "nhl", "soccer", "mlb", "nfl", "tennis", "combined"]
        status_payload = {}
        for s in status_sports:
            exists = bool((sports_payload.get(s) if isinstance(sports_payload, dict) else []) or (s == "combined" and sports_payload))
            status_payload[s] = {"slate": {"exists": exists, "modified": modified if exists else ""}}
        (MOBILE_WWW_DIR / "pipeline_status.json").write_text(
            json.dumps(status_payload, ensure_ascii=True, indent=2),
            encoding="utf-8"
        )

        # Static replacement for /api/slate-excel used by Combined slate table.
        combined_columns = [
            "Tier", "Rank Score", "Player", "Team", "Opp", "Prop", "Pick Type",
            "Line", "Dir", "Edge", "Hit Rate", "L5 Over", "L5 Under", "Game Time"
        ]
        combined_rows = []
        for rows in sports_payload.values():
            if not isinstance(rows, list):
                continue
            for r in rows:
                if not isinstance(r, dict):
                    continue
                combined_rows.append([
                    r.get("tier"),
                    r.get("rank_score"),
                    r.get("player"),
                    r.get("team"),
                    r.get("opp"),
                    r.get("prop"),
                    r.get("pick_type"),
                    r.get("line"),
                    r.get("dir"),
                    r.get("edge"),
                    r.get("hit_rate"),
                    r.get("l5_over"),
                    r.get("l5_under"),
                    r.get("game_time"),
                ])
        combined_rows.sort(key=lambda row: abs(float(row[9] or 0.0)), reverse=True)
        (MOBILE_WWW_DIR / "slate_excel.json").write_text(
            json.dumps({"sheets": {"combined": {"columns": combined_columns, "rows": combined_rows}}}, ensure_ascii=True),
            encoding="utf-8"
        )

    # Copy local grade history data for offline/mobile P&L page.
    src_grade_history = DATA_DIR / "grade_history.json"
    if src_grade_history.exists():
        mobile_data_dir = MOBILE_WWW_DIR / "data"
        mobile_data_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_grade_history, mobile_data_dir / "grade_history.json")

    print("Mobile bundle generation complete.")

if __name__ == "__main__":
    generate_bundle()
