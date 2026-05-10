import os
import sys
import shutil
import re
import json
from datetime import datetime, timezone
from pathlib import Path
import combined_slate_tickets

_ROOT_FOR_UTILS = Path(__file__).resolve().parent.parent
if str(_ROOT_FOR_UTILS) not in sys.path:
    sys.path.insert(0, str(_ROOT_FOR_UTILS))
from utils.proporacle_data_root import grade_history_read_paths  # noqa: E402


def _write_ota_config(mobile_www: Path) -> None:
    """Enable Capacitor DIY OTA when PROPORACLE_OTA_BASE_URL is set (Railway public URL, no trailing slash)."""
    raw_base = (os.environ.get("PROPORACLE_OTA_BASE_URL") or "").strip().rstrip("/")
    flag = (os.environ.get("PROPORACLE_OTA_ENABLED") or "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        enabled = False
    elif raw_base:
        enabled = True
    else:
        enabled = flag in ("1", "true", "yes", "on")
    raw_interval = (os.environ.get("PROPORACLE_OTA_CHECK_INTERVAL_MS") or "").strip()
    try:
        check_interval_ms = int(raw_interval) if raw_interval else 3_600_000
    except ValueError:
        check_interval_ms = 3_600_000
    payload = {
        "enabled": bool(enabled and raw_base),
        "baseUrl": raw_base,
        "checkIntervalMs": check_interval_ms,
    }
    (mobile_www / "ota-config.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _first_existing_path(candidates):
    for p in candidates:
        if p is not None and Path(p).exists():
            return Path(p)
    return None


def _mtime_utc_string(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

# Keep aligned with ui_runner/app.py page_income _SPORT_BREAKDOWN_ORDER.
SPORT_BREAKDOWN_ORDER = ("NBA", "CBB", "WNBA", "MLB", "SOCCER", "TENNIS", "NHL", "NFL")


def _normalize_sport_label(raw):
    s = str(raw or "").strip().upper()
    aliases = {"NCAAB": "CBB", "WCBB": "CBB", "NCAAF": "NFL", "NBA1Q": "NBA", "NBA1H": "NBA"}
    return aliases.get(s, s)


def _read_template_json_date(templates_dir: Path) -> str:
    """Prefer tickets_latest date (matches /api/slate-display-date), then slate_latest."""
    for name in ("tickets_latest.json", "slate_latest.json"):
        p = templates_dir / name
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            ds = str((data or {}).get("date") or "").strip()[:10]
            if len(ds) == 10 and ds[4] == "-" and ds[7] == "-":
                return ds
        except Exception:
            continue
    return ""


def _merged_combined_rows_for_mobile(sports_payload: dict) -> list:
    """All sport rows merged + sorted by rank_score (matches app /api/slate-sport/combined)."""
    out = []
    for sk, rows in (sports_payload or {}).items():
        key = str(sk).strip().lower()
        if key == "combined":
            continue
        if not isinstance(rows, list):
            continue
        for r in rows:
            if not isinstance(r, dict):
                continue
            row = dict(r)
            if not str(row.get("sport") or "").strip():
                row["sport"] = str(sk).strip().upper()
            out.append(row)

    def _rank(x):
        try:
            v = x.get("rank_score")
            return float(v) if v is not None else float("-inf")
        except (TypeError, ValueError):
            return float("-inf")

    out.sort(key=_rank, reverse=True)
    return out


def _build_mobile_sport_breakdown(templates_dir):
    stats = {s: {"decided": 0, "paid": 0, "net_dollars": 0.0} for s in SPORT_BREAKDOWN_ORDER}
    for gp in sorted(templates_dir.glob("graded_props_*.json")):
        try:
            payload = json.loads(gp.read_text(encoding="utf-8"))
        except Exception:
            continue
        props = payload.get("props") if isinstance(payload, dict) else None
        if not isinstance(props, list):
            continue
        for row in props:
            if not isinstance(row, dict):
                continue
            sport = _normalize_sport_label(row.get("sport"))
            if sport not in stats:
                continue
            result = str(row.get("result") or "").strip().upper()
            if result == "HIT":
                stats[sport]["decided"] += 1
                stats[sport]["paid"] += 1
                stats[sport]["net_dollars"] += 10.0
            elif result == "MISS":
                stats[sport]["decided"] += 1
                stats[sport]["net_dollars"] -= 10.0
    rows = []
    for sport in SPORT_BREAKDOWN_ORDER:
        decided = int(stats[sport]["decided"])
        paid = int(stats[sport]["paid"])
        win_rate = (paid / decided) if decided > 0 else None
        rows.append(
            {
                "sport": sport,
                "decided": decided,
                "paid": paid,
                "win_rate": win_rate,
                "net_dollars": round(float(stats[sport]["net_dollars"]), 2),
            }
        )
    return {"ok": True, "rows": rows, "source": "graded_props_json"}


def _extract_series_from_row(row):
    actual = row.get("actual_series")
    line = row.get("line_series")
    if isinstance(actual, list) and actual:
        actual_vals = []
        for v in actual:
            try:
                actual_vals.append(float(v))
            except Exception:
                pass
        line_vals = []
        if isinstance(line, list):
            for v in line:
                try:
                    line_vals.append(float(v))
                except Exception:
                    pass
        return actual_vals, line_vals

    # Fallback for pipeline rows carrying G1..G10 and line_G1..line_G10.
    actual_vals = []
    line_vals = []
    for i in range(1, 11):
        av = row.get(f"g{i}")
        lv = row.get(f"line_g{i}")
        try:
            if av is not None:
                actual_vals.append(float(av))
        except Exception:
            pass
        try:
            if lv is not None:
                line_vals.append(float(lv))
        except Exception:
            pass
    return actual_vals, line_vals


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

    # Robust path relativization for mobile bundle (Capacitor file:// protocol)
    # Fix src and href attributes (e.g. <img src="/static/logo.png"> -> <img src="static/logo.png">)
    # Handles variations in quoting and whitespace around the '='.
    content = re.sub(
        r'(src|href)\s*=\s*(["\'])\s*/static/',
        r'\1=\2static/',
        content,
        flags=re.IGNORECASE
    )

    # Fix CSS url() references and flatten /static/css/ -> static/ (assets are copied to flat static/ dir)
    # Handles url("/static/..."), url('/static/...'), and url(/static/...) with optional leading slash.
    content = re.sub(
        r'url\(\s*(["\']?)\s*/?static/(?:css/)?',
        r'url(\1static/',
        content,
        flags=re.IGNORECASE
    )

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
        "payout_calculator.html": "payout.html",
        "payout_log.html": "payout_log.html",
        "payout_ladder.html": "payout_ladder.html",
        "payout_examples.html": "payout_examples.html",
    }

    for src_name, dest_name in PAGES.items():
        src_path = TEMPLATES_DIR / src_name
        dest_path = MOBILE_WWW_DIR / dest_name

        if src_path.exists():
            print(f"Processing {src_path} to {dest_path}...")

            # Process includes recursively
            content = process_template(src_path, TEMPLATES_DIR)

            # Fix navigation links for static bundle using robust regex
            # (Matches href="/", href="/tickets", etc., with flexible quoting and whitespace)
            NAV_MAP = {
                "/": "index.html",
                "/tickets": "tickets.html",
                "/grades": "grades.html",
                "/income": "income.html",
                "/payout": "payout.html",
                "/payout/log": "payout_log.html",
                "/payout/ladder": "payout_ladder.html",
                "/payout/examples": "payout_examples.html",
            }
            for route, target in NAV_MAP.items():
                content = re.sub(
                    rf'href\s*=\s*(["\'])\s*{re.escape(route)}\s*\1',
                    f'href="{target}"',
                    content,
                    flags=re.IGNORECASE
                )

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
    if (path.includes('/api/uniform-tickets/dates')) {
      return _origFetch('uniform_tickets_dates.json', init);
    }
    if (path.includes('/api/uniform-tickets/backtest')) {
      return _origFetch('uniform_tickets_backtest.json', init);
    }
    if (path.includes('/api/uniform-tickets/latest')) {
      return _origFetch('uniform_tickets_latest.json', init);
    }
    {
      const m = path.match(/\\/api\\/uniform-tickets\\/(\\d{4}-\\d{2}-\\d{2})$/);
      if (m) {
        return _origFetch(`uniform_tickets_${m[1]}.json`, init);
      }
    }
    if (path.includes('/api/grades/insights')) {
      return _jsonResp({ calibration: [], clv_by_sport: [], edge_bucket_hit_rate: [], clv_by_prop_type: [], clv_by_tier: [] });
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
                    "fetch_smart('slate_latest.json')"
                )
                content = content.replace(
                    "fetch('/api/slate', {cache: 'no-store'})",
                    "fetch_smart('slate_latest.json')"
                )
                # Pipeline status: template spacing varies — use regex so replacement always applies.
                content = re.sub(
                    r'fetch\(\s*"/api/pipeline/status"\s*,\s*\{\s*cache\s*:\s*[\'"]no-store[\'"]\s*\}\s*\)',
                    "fetch_smart('pipeline_status.json')",
                    content,
                )
                content = content.replace(
                    'fetch(`/api/slate-sport/${encodeURIComponent(sport)}`, {cache: \'no-store\'})',
                    "fetch_smart(`slate_sport_${encodeURIComponent(sport)}.json`)"
                )
                content = content.replace(
                    "fetch('/api/slate-excel', {cache: 'no-store'})",
                    "fetch_smart('slate_excel.json')"
                )
                # Combined slate JSON (no Railway — mobile/www only).
                content = re.sub(
                    r"fetch\(\s*['\"]/api/slate-sport/combined['\"]\s*,\s*\{\s*cache\s*:\s*['\"]no-store['\"]\s*\}\s*\)",
                    "fetch_smart('slate_sport_combined.json')",
                    content,
                )

                # Inject fetch_smart and fetch logic for remote-priority
                smart_fetch_js = """
<script>
async function fetch_smart(localPath) {
  // If we have a remote override URL for this file type, prefer it
  let remoteUrl = null;
  if (localPath === 'slate_latest.json' && window.SLATE_JSON_URL) remoteUrl = window.SLATE_JSON_URL;
  if (localPath === 'tickets_latest.json' && window.TICKETS_JSON_URL) remoteUrl = window.TICKETS_JSON_URL;

  if (remoteUrl) {
    try {
      const resp = await fetch(remoteUrl, { cache: 'no-store' });
      if (resp.ok) return resp;
    } catch (e) {
      console.warn("SmartFetch remote failed, falling back to local:", localPath, e);
    }
  }
  return fetch(localPath, { cache: 'no-store' });
}
</script>
"""
                ota_js = '<script defer src="static/proporacle-ota.js"></script>'
                content = content.replace("<head>", f"<head>\n{smart_fetch_js}\n{ota_js}")
            elif dest_name == "income.html":
                # Jinja strips can leave invalid JS in static bundle.
                content = re.sub(r"const\s+points\s*=\s*;", "const points = [];", content)
                mobile_income_bootstrap = """
  <script>
    (function () {
      const HISTORY_URL = 'data/grade_history.json';
      const SPORT_BREAKDOWN_URL = 'sport_breakdown.json';
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
            // net_per_10 is per-ticket avg ($-per-$10-stake). Multiply by tickets to get day total.
            const net = (r.net_dollars != null)
              ? Number(r.net_dollars)
              : (r.net_per_10 != null ? tickets * Number(r.net_per_10) : 0);
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

      function renderSportBreakdown(rows) {
        const body = document.querySelectorAll('.panel .tbl tbody')[1];
        if (!body) return;
        const safe = Array.isArray(rows) ? rows : [];
        body.innerHTML = safe.map((r) => {
          const decided = Number(r.decided || 0);
          const paid = Number(r.paid || 0);
          const winRate = decided > 0 ? (paid / decided) * 100 : NaN;
          const net = Number(r.net_dollars || 0);
          const winText = Number.isFinite(winRate) ? `${winRate.toFixed(1)}%` : '—';
          return `
            <tr>
              <td>${String(r.sport || '')}</td>
              <td>${decided}</td>
              <td>${paid}</td>
              <td>${winText}</td>
              <td class="${clsFor(net)}">${fmtMoney(net)}</td>
            </tr>
          `;
        }).join('');
      }

      fetch(HISTORY_URL, { cache: 'no-store' })
        .then((r) => (r.ok ? r.json() : []))
        .then((raw) => render(parseRows(raw)))
        .catch(() => render([]));

      fetch(SPORT_BREAKDOWN_URL, { cache: 'no-store' })
        .then((r) => (r.ok ? r.json() : { rows: [] }))
        .then((j) => renderSportBreakdown(j && j.rows))
        .catch(() => renderSportBreakdown([]));
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
                # Offline/mobile: shim Uniform-tickets API to local JSON files.
                tickets_mobile_bootstrap = """
<script>
(function () {
  const _origFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const urlStr = (typeof input === 'string') ? input : (input && input.url ? input.url : String(input || ''));
    const path = urlStr.replace(window.location.origin, '');
    if (path.includes('/api/uniform-tickets/dates')) {
      return _origFetch('uniform_tickets_dates.json', init);
    }
    if (path.includes('/api/uniform-tickets/backtest')) {
      return _origFetch('uniform_tickets_backtest.json', init);
    }
    if (path.includes('/api/uniform-tickets/latest')) {
      return _origFetch('uniform_tickets_latest.json', init);
    }
    const m = path.match(/\\/api\\/uniform-tickets\\/(\\d{4}-\\d{2}-\\d{2})$/);
    if (m) {
      return _origFetch(`uniform_tickets_${m[1]}.json`, init);
    }
    return _origFetch(input, init);
  };
})();
</script>
"""
                content = content.replace("</body>", tickets_mobile_bootstrap + "\n</body>")
            elif dest_name == "payout.html":
                # Offline/mobile: rate cards must load from bundled JSON (no /api route in file:// mode).
                content = content.replace(
                    "fetch('/api/payout/rate-cards')",
                    "fetch('payout_rate_cards.json', { cache: 'no-store' })"
                )
                content = content.replace(
                    'fetch("/api/payout/rate-cards")',
                    "fetch('payout_rate_cards.json', { cache: 'no-store' })"
                )

            # Resolve Flask url_for('static', filename='…') before stripping {{ }} — otherwise
            # <img src="{{ url_for(...) }}?v=…"> becomes src="?v=…" and the logo 404s in the APK.
            content = re.sub(
                r"\{\{\s*url_for\s*\(\s*['\"]static['\"]\s*,\s*filename\s*=\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}",
                r"static/\1",
                content,
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

    # Copy uniform-bucket ticket artifacts (built by build_uniform_tickets_artifacts.py).
    uniform_ticket_dates = []
    for ut in sorted(TEMPLATES_DIR.glob("uniform_tickets_*.json")):
        shutil.copy2(ut, MOBILE_WWW_DIR / ut.name)
        m = re.fullmatch(r"uniform_tickets_(\d{4}-\d{2}-\d{2})\.json", ut.name)
        if m:
            uniform_ticket_dates.append(m.group(1))
    if uniform_ticket_dates and not (MOBILE_WWW_DIR / "uniform_tickets_dates.json").exists():
        (MOBILE_WWW_DIR / "uniform_tickets_dates.json").write_text(
            json.dumps(
                {"dates": sorted(set(uniform_ticket_dates), reverse=True)},
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

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
                actual_series, line_series = _extract_series_from_row(r)
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
                    "actual_series": actual_series,
                    "line_series": line_series,
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

        merged_combined = _merged_combined_rows_for_mobile(sports_payload)
        (MOBILE_WWW_DIR / "slate_sport_combined.json").write_text(
            json.dumps({"ok": True, "sport": "combined", "rows": merged_combined}, ensure_ascii=True),
            encoding="utf-8"
        )

        # Static replacement for /api/pipeline/status (used by home status cards).
        slate_date = _read_template_json_date(TEMPLATES_DIR) or str(
            (slate_payload.get("date") if isinstance(slate_payload, dict) else "") or ""
        ).strip()[:10]
        modified_default = f"{slate_date} 12:00:00" if slate_date else ""
        status_sports = ["nba", "nba1h", "nba1q", "cbb", "nhl", "soccer", "mlb", "nfl", "tennis", "wnba", "combined"]
        R = ROOT_DIR
        combined_candidates = list(R.glob("combined_slate_tickets_*.xlsx"))
        _out_root = R / "outputs"
        if _out_root.is_dir():
            combined_candidates.extend(_out_root.glob("*/combined_slate_tickets_*.xlsx"))
        combined_artifact = (
            max(combined_candidates, key=lambda p: p.stat().st_mtime) if combined_candidates else None
        )
        artifact_by_sport = {
            "nba": _first_existing_path(
                [
                    R / "outputs" / slate_date / "nba" / "step8_all_direction_clean.xlsx",
                    R / "Sports" / "NBA" / "data" / "outputs" / "step8_all_direction_clean.xlsx",
                ]
            ),
            "nba1h": _first_existing_path(
                [
                    R / "outputs" / slate_date / "nba1h" / "step8_nba1h_direction_clean.xlsx",
                    R / "Sports" / "NBA" / "step8_nba1h_direction_clean.xlsx",
                ]
            ),
            "nba1q": _first_existing_path(
                [
                    R / "outputs" / slate_date / "nba1q" / "step8_nba1q_direction_clean.xlsx",
                    R / "Sports" / "NBA" / "step8_nba1q_direction_clean.xlsx",
                ]
            ),
            "cbb": _first_existing_path(
                [R / "Sports" / "CBB" / "step6_ranked_cbb.xlsx", R / "CBB" / "step6_ranked_cbb.xlsx"]
            ),
            "nhl": _first_existing_path(
                [
                    R / "outputs" / slate_date / "nhl" / "step8_nhl_direction_clean.xlsx",
                    R / "Sports" / "NHL" / "outputs" / "step8_nhl_direction_clean.xlsx",
                ]
            ),
            "soccer": _first_existing_path(
                [
                    R / "outputs" / slate_date / "soccer" / "step8_soccer_direction_clean.xlsx",
                    R / "Sports" / "Soccer" / "outputs" / "step8_soccer_direction_clean.xlsx",
                ]
            ),
            "mlb": _first_existing_path(
                [
                    R / "Sports" / "MLB" / "step8_mlb_direction_clean.xlsx",
                    R / "Sports" / "MLB" / "outputs" / "step8_mlb_direction_clean.xlsx",
                ]
            ),
            "nfl": _first_existing_path(
                [
                    R / "outputs" / slate_date / "nfl" / "step8_nfl_direction_clean.xlsx",
                    R / "Sports" / "NFL" / "outputs" / "step8_nfl_direction_clean.xlsx",
                ]
            ),
            "tennis": _first_existing_path(
                [
                    R / "outputs" / slate_date / "tennis" / "step8_tennis_direction_clean.xlsx",
                    R / "Sports" / "Tennis" / "outputs" / "step8_tennis_direction_clean.xlsx",
                ]
            ),
            "wnba": _first_existing_path(
                [
                    R / "outputs" / slate_date / "wnba" / "step8_wnba_direction_clean.xlsx",
                    R / "outputs" / slate_date / "step8_wnba_direction_clean_{}.xlsx".format(slate_date),
                    R / "Sports" / "WNBA" / "outputs" / "step8_wnba_direction_clean.xlsx",
                    R / "Sports" / "WNBA" / "step8_wnba_direction_clean.xlsx",
                    R / "Sports" / "WNBA" / "step8_wnba_direction.xlsx",
                    R / "WNBA" / "step8_wnba_direction_clean.xlsx",
                    R / "WNBA" / "step8_wnba_direction.xlsx",
                ]
            ),
        }
        status_payload = {}
        for s in status_sports:
            art = combined_artifact if s == "combined" else artifact_by_sport.get(s)
            has_rows = bool((sports_payload.get(s) if isinstance(sports_payload, dict) else []))
            has_artifact = bool(art and art.exists())
            exists = bool(
                has_rows
                or has_artifact
                or (s == "combined" and isinstance(sports_payload, dict) and bool(sports_payload))
            )
            mod_str = ""
            if exists:
                if has_artifact:
                    mod_str = _mtime_utc_string(art)
                elif modified_default:
                    mod_str = modified_default
            status_payload[s] = {"slate": {"exists": exists, "modified": mod_str}}
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

    # Same date field as /api/slate-display-date — bundled apps cannot call the API offline.
    _ymd = _read_template_json_date(TEMPLATES_DIR)
    (MOBILE_WWW_DIR / "slate_display_date.json").write_text(
        json.dumps({"date": _ymd}, ensure_ascii=True),
        encoding="utf-8",
    )

    # Copy grade history for offline/mobile P&L (same resolution as Flask /income).
    mobile_data_dir = MOBILE_WWW_DIR / "data"
    mobile_data_dir.mkdir(parents=True, exist_ok=True)
    _gh_candidates = grade_history_read_paths(ROOT_DIR, templates_dir=TEMPLATES_DIR)
    _gh_src = _first_existing_path(_gh_candidates)
    if _gh_src is not None:
        shutil.copy2(_gh_src, mobile_data_dir / "grade_history.json")

    # WNBA step8 clean workbook for mobile/offline consumers.
    src_wnba_step8 = ROOT_DIR / "Sports" / "WNBA" / "outputs" / "step8_wnba_direction_clean.xlsx"
    if src_wnba_step8.exists():
        shutil.copy2(src_wnba_step8, mobile_data_dir / "step8_wnba_direction_clean.xlsx")

    # Payout tab offline/mobile dependency.
    src_payout_rate_cards = DATA_DIR / "payout_rate_cards.json"
    if src_payout_rate_cards.exists():
        shutil.copy2(src_payout_rate_cards, MOBILE_WWW_DIR / "payout_rate_cards.json")
    src_payout_ladder_examples = ROOT_DIR / "ui_runner" / "data" / "payout_ladder_examples.json"
    if src_payout_ladder_examples.exists():
        shutil.copy2(src_payout_ladder_examples, MOBILE_WWW_DIR / "payout_ladder_examples.json")

    # Offline/mobile Sport Breakdown source for income page.
    (MOBILE_WWW_DIR / "sport_breakdown.json").write_text(
        json.dumps(_build_mobile_sport_breakdown(TEMPLATES_DIR), ensure_ascii=True, indent=2),
        encoding="utf-8",
    )

    _write_ota_config(MOBILE_WWW_DIR)

    print("Mobile bundle generation complete.")

if __name__ == "__main__":
    generate_bundle()
