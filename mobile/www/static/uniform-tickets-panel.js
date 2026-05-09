/* Uniform-bucket Tickets panel — shared between /tickets and /grades.
 *
 * Markup contract (rendered by _uniform_tickets_panel.html):
 *   .utp-root[data-mode="tickets|grades"]
 *     button.utp-toggle (only on /tickets — /grades wires its own tab)
 *     section.utp-panel (hidden by default on /tickets, always shown when active tab on /grades)
 *
 * Data sources (preferred order):
 *   1. window.UTP_INLINE  — fully embedded payload (for static mobile bundle)
 *   2. /api/uniform-tickets/<date>          (Flask app)
 *   3. uniform_tickets_<date>.json          (relative, mobile bundle)
 */
(function () {
  'use strict';

  const BUCKETS = ['elite', 'premium', 'strong', 'value'];
  const BUCKET_LABEL = {
    elite: 'Elite (~75–98% / leg)',
    premium: 'Premium (~65–75%)',
    strong: 'Strong (~55–65%)',
    value: 'Value (~45–55%)',
  };

  const fmtPct = (v) => {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
    const f = Number(v);
    return `${(f <= 1 ? f * 100 : f).toFixed(1)}%`;
  };
  const fmtMoney = (v) => {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
    const f = Number(v);
    const sign = f >= 0 ? '+' : '−';
    return `${sign}$${Math.abs(f).toFixed(2)}`;
  };
  const fmt2 = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(2));

  const evClass = (v) => {
    const f = Number(v);
    if (!Number.isFinite(f)) return 'utp-num-flat';
    if (f > 0.001) return 'utp-num-pos';
    if (f < -0.001) return 'utp-num-neg';
    return 'utp-num-flat';
  };

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const k in attrs) {
        if (k === 'class') node.className = attrs[k];
        else if (k === 'html') node.innerHTML = attrs[k];
        else if (k.startsWith('on') && typeof attrs[k] === 'function') node.addEventListener(k.slice(2), attrs[k]);
        else if (attrs[k] !== undefined && attrs[k] !== null) node.setAttribute(k, attrs[k]);
      }
    }
    (children || []).forEach((c) => {
      if (c == null) return;
      node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return node;
  }

  async function fetchJson(urls) {
    for (const url of urls) {
      try {
        const r = await fetch(url, { cache: 'no-store' });
        if (r.ok) return await r.json();
      } catch (_e) { /* try next */ }
    }
    return null;
  }

  async function loadDates() {
    if (window.UTP_INLINE_DATES && Array.isArray(window.UTP_INLINE_DATES.dates)) {
      return window.UTP_INLINE_DATES.dates;
    }
    const j = await fetchJson([
      '/api/uniform-tickets/dates',
      'uniform_tickets_dates.json',
    ]);
    if (j && Array.isArray(j.dates)) return j.dates;
    return [];
  }

  async function loadDate(date) {
    if (window.UTP_INLINE && window.UTP_INLINE.date === date) {
      return window.UTP_INLINE;
    }
    return fetchJson([
      `/api/uniform-tickets/${date}`,
      `uniform_tickets_${date}.json`,
    ]);
  }

  async function loadLatest() {
    if (window.UTP_INLINE) return window.UTP_INLINE;
    return fetchJson([
      '/api/uniform-tickets/latest',
      'uniform_tickets_latest.json',
    ]);
  }

  async function loadBacktest() {
    if (window.UTP_INLINE_BACKTEST) return window.UTP_INLINE_BACKTEST;
    return fetchJson([
      '/api/uniform-tickets/backtest',
      'uniform_tickets_backtest.json',
    ]);
  }

  function renderSummary(rows) {
    if (!rows || !rows.length) {
      return el('div', { class: 'utp-empty' }, ['No tickets summary available for this date.']);
    }
    const head = el('thead', null, [
      el('tr', null, [
        el('th', null, ['Size']),
        el('th', null, ['Bucket']),
        el('th', null, ['# Tickets']),
        el('th', null, ['Avg Joint P(hit)']),
        el('th', null, ['Avg Payout']),
        el('th', null, ['Pred EV / $1']),
        el('th', null, ['Decided']),
        el('th', null, ['All-Hit']),
        el('th', null, ['Realized %']),
      ]),
    ]);
    const body = el('tbody');
    rows.forEach((r) => {
      body.appendChild(
        el('tr', null, [
          el('td', null, [String(r.size)]),
          el('td', null, [
            el('span', { class: `utp-bucket-pill ${r.bucket}` }, [String(r.bucket || '—').toUpperCase()]),
          ]),
          el('td', null, [String(r.n_tickets || 0)]),
          el('td', null, [fmtPct(r.avg_joint_p_hit)]),
          el('td', null, [`${fmt2(r.avg_payout)}×`]),
          el('td', { class: evClass(r.avg_expected_profit_per_$1) }, [fmtMoney(r.avg_expected_profit_per_$1)]),
          el('td', null, [String(r.decided || 0)]),
          el('td', null, [String(r.all_hit_count || 0)]),
          el('td', null, [r.realized_all_hit_rate == null ? '—' : fmtPct(r.realized_all_hit_rate)]),
        ])
      );
    });
    return el('table', { class: 'utp-summary-table' }, [head, body]);
  }

  function renderBacktest(rows) {
    if (!rows || !rows.length) {
      return el('div', { class: 'utp-empty' }, ['Backtest data not available yet.']);
    }
    const head = el('thead', null, [
      el('tr', null, [
        el('th', null, ['Size']),
        el('th', null, ['Bucket']),
        el('th', null, ['# Tickets']),
        el('th', { title: 'Tickets where ≥1 leg voided (paid as smaller tier when remaining all hit)' }, ['Voided']),
        el('th', null, ['Pred Joint']),
        el('th', { title: 'Average banner power-play multiplier at the original size' }, ['Banner Pay']),
        el('th', { title: 'Average realized payout including void→smaller-tier wins (1.0× = refund, 0× = bust)' }, ['Eff Pay']),
        el('th', null, ['Realized %']),
        el('th', null, ['Wilson Low']),
        el('th', null, ['Realized EV / $1']),
      ]),
    ]);
    const body = el('tbody');
    rows.forEach((r) => {
      body.appendChild(
        el('tr', null, [
          el('td', null, [String(r.size)]),
          el('td', null, [
            el('span', { class: `utp-bucket-pill ${r.bucket}` }, [String(r.bucket || '—').toUpperCase()]),
          ]),
          el('td', null, [String(r.n_tickets || 0)]),
          el('td', null, [String(r.n_void_tickets || 0)]),
          el('td', null, [fmtPct(r.avg_joint_pred)]),
          el('td', null, [`${fmt2(r.avg_payout)}×`]),
          el('td', null, [`${fmt2(r.avg_effective_payout)}×`]),
          el('td', null, [fmtPct(r.realized_all_hit_rate)]),
          el('td', null, [fmtPct(r.wilson_low)]),
          el('td', { class: evClass(r['realized_ev_per_$1']) }, [fmtMoney(r['realized_ev_per_$1'])]),
        ])
      );
    });
    return el('table', { class: 'utp-backtest-table' }, [head, body]);
  }

  function renderLeg(leg) {
    const lineStr = leg.line == null ? '' : String(leg.line);
    const dirStr = leg.direction || '';
    const propStr = leg.prop || '';
    const playerStr = leg.player || '';
    const sportStr = leg.sport || '';
    const teamStr = leg.team ? `${leg.team}${leg.opp_team ? ` vs ${leg.opp_team}` : ''}` : '';
    const result = String(leg.result || '').toUpperCase();
    const resultClass = result === 'HIT' || result === 'MISS' ? result : 'OTHER';
    const probStr = leg.est_p == null ? '' : ` · ${fmtPct(leg.est_p)}`;
    const pickType = leg.pick_type ? ` · ${leg.pick_type}` : '';
    return el('li', { class: 'utp-leg' }, [
      el('div', { class: 'utp-leg-main' }, [
        el('div', { class: 'utp-leg-name', title: `${playerStr} — ${propStr}` }, [`${playerStr} — ${propStr}`]),
        el('div', { class: 'utp-leg-line' }, [
          `${sportStr}${teamStr ? ` · ${teamStr}` : ''} · ${dirStr} ${lineStr}${pickType}${probStr}`.trim(),
        ]),
      ]),
      el('span', { class: `utp-leg-result ${resultClass}` }, [result || '—']),
    ]);
  }

  function renderTicket(t) {
    const bucket = String(t.bucket || '').toLowerCase();
    const size = t.size || (t.legs ? t.legs.length : 0);
    const joint = t.joint_p_hit;
    const payout = t.power_payout;
    const ev = t['expected_profit_per_$1'];
    const allHit = (t.legs || []).every((l) => String(l.result || '').toUpperCase() === 'HIT');
    const allDecided = (t.legs || []).every((l) => ['HIT', 'MISS'].includes(String(l.result || '').toUpperCase()));
    const verdict = !allDecided ? '—' : (allHit ? 'WIN' : 'LOSS');
    const verdictCls = !allDecided ? '' : (allHit ? 'utp-num-pos' : 'utp-num-neg');

    return el('article', { class: `utp-ticket bucket-${bucket}` }, [
      el('div', { class: 'utp-ticket-head' }, [
        el('span', null, [`${size}-Leg`]),
        el('span', { class: `utp-bucket-pill ${bucket}` }, [bucket.toUpperCase()]),
      ]),
      el('div', { class: 'utp-ticket-stats' }, [
        el('span', null, [
          'Joint ',
          el('b', null, [fmtPct(joint)]),
        ]),
        el('span', null, [
          'Payout ',
          el('b', null, [`${fmt2(payout)}×`]),
        ]),
        el('span', { class: evClass(ev) }, [
          'EV ',
          el('b', null, [fmtMoney(ev)]),
        ]),
        el('span', { class: verdictCls }, [
          'Result ',
          el('b', null, [verdict]),
        ]),
      ]),
      el('ul', { class: 'utp-leg-list' }, (t.legs || []).map(renderLeg)),
    ]);
  }

  function renderTickets(tickets, filterSize, filterBucket) {
    const filtered = (tickets || []).filter((t) => {
      if (filterSize && String(t.size) !== String(filterSize)) return false;
      if (filterBucket && filterBucket !== 'all') {
        if (String(t.bucket || '').toLowerCase() !== filterBucket) return false;
      }
      return true;
    });
    if (!filtered.length) {
      return el('div', { class: 'utp-empty' }, ['No tickets match the current filters.']);
    }
    const grid = el('div', { class: 'utp-tickets-grid' });
    filtered.slice(0, 60).forEach((t) => grid.appendChild(renderTicket(t)));
    return grid;
  }

  // ── controller ──────────────────────────────────────────────────────────────

  async function controller(rootEl) {
    const state = {
      view: 'today',           // 'today' | 'backtest'
      payload: null,           // /api/uniform-tickets/<date>
      backtest: null,
      dates: [],
      filterSize: '',
      filterBucket: 'all',
    };

    const titleEl   = rootEl.querySelector('[data-utp="title"]');
    const dateLabel = rootEl.querySelector('[data-utp="date-label"]');
    const dateInput = rootEl.querySelector('[data-utp="date-input"]');
    const sizeSel   = rootEl.querySelector('[data-utp="size-filter"]');
    const bucketSel = rootEl.querySelector('[data-utp="bucket-filter"]');
    const summaryHost  = rootEl.querySelector('[data-utp="summary-host"]');
    const ticketsHost  = rootEl.querySelector('[data-utp="tickets-host"]');
    const backtestHost = rootEl.querySelector('[data-utp="backtest-host"]');
    const todayWrap    = rootEl.querySelector('[data-utp="today-wrap"]');
    const backtestWrap = rootEl.querySelector('[data-utp="backtest-wrap"]');
    const tabBtns      = rootEl.querySelectorAll('[data-utp-tab]');
    const footEl       = rootEl.querySelector('[data-utp="foot"]');

    function paint() {
      summaryHost.replaceChildren(renderSummary((state.payload || {}).summary || []));
      ticketsHost.replaceChildren(
        renderTickets((state.payload || {}).tickets || [], state.filterSize, state.filterBucket)
      );
      backtestHost.replaceChildren(renderBacktest((state.backtest || {}).rows || []));
      if (titleEl && state.payload && state.payload.date) {
        titleEl.textContent = `Uniform Tickets — ${state.payload.date}`;
      }
      if (dateLabel && state.payload && state.payload.date) {
        dateLabel.textContent = state.payload.date;
      }
      if (footEl) {
        const ts = (state.payload && state.payload.generated_at) || '';
        const n = (state.payload && state.payload.n_tickets) || 0;
        footEl.textContent = ts ? `Generated ${ts} · ${n} tickets` : `${n} tickets`;
      }
      todayWrap.hidden = state.view !== 'today';
      backtestWrap.hidden = state.view !== 'backtest';
      tabBtns.forEach((b) => {
        b.classList.toggle('active', b.getAttribute('data-utp-tab') === state.view);
      });
    }

    async function setDate(date) {
      const payload = await loadDate(date);
      if (!payload) return;
      state.payload = payload;
      if (dateInput) dateInput.value = date;
      paint();
    }

    if (sizeSel) sizeSel.addEventListener('change', () => { state.filterSize = sizeSel.value; paint(); });
    if (bucketSel) bucketSel.addEventListener('change', () => { state.filterBucket = bucketSel.value; paint(); });
    tabBtns.forEach((b) => {
      b.addEventListener('click', () => { state.view = b.getAttribute('data-utp-tab'); paint(); });
    });
    if (dateInput) {
      dateInput.addEventListener('change', () => {
        if (dateInput.value) setDate(dateInput.value);
      });
    }

    state.dates = await loadDates();
    if (dateInput && state.dates.length) {
      dateInput.min = state.dates[state.dates.length - 1];
      dateInput.max = state.dates[0];
    }

    state.payload = await loadLatest();
    state.backtest = await loadBacktest();
    paint();

    return {
      setDate,
      element: rootEl,
    };
  }

  function init() {
    document.querySelectorAll('.utp-root').forEach((root) => {
      if (root.__utpInit) return;
      root.__utpInit = true;

      const toggleBtn = root.querySelector('[data-utp="toggle"]');
      const panel = root.querySelector('.utp-panel');
      let started = false;

      const start = () => {
        if (started) return;
        started = true;
        controller(root).then((c) => { root.__utp = c; });
      };

      if (toggleBtn && panel) {
        toggleBtn.addEventListener('click', () => {
          const open = !panel.hasAttribute('hidden');
          if (open) {
            panel.setAttribute('hidden', '');
          } else {
            panel.removeAttribute('hidden');
            start();
          }
        });
      } else {
        // Auto-open mode (used inside Grades tab — already visible).
        start();
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.UniformTicketsPanel = { init };
})();
