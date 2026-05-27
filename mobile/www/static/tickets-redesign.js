(function () {
  if (!document.body.classList.contains('page-tickets')) return;

  var SPORT_COLORS = {
    NBA: '#3B82F6', WNBA: '#9333EA', MLB: '#EF4444',
    NHL: '#06B6D4', Tennis: '#22C55E', Soccer: '#F97316',
    CBB: '#F59E0B', NFL: '#6366F1'
  };

  function sportFromTitle(title) {
    var t = (title || '').toUpperCase();
    if (t.indexOf('WNBA') !== -1) return 'WNBA';
    if (t.indexOf('NBA') !== -1) return 'NBA';
    if (t.indexOf('MLB') !== -1) return 'MLB';
    if (t.indexOf('NHL') !== -1) return 'NHL';
    if (t.indexOf('TENNIS') !== -1) return 'Tennis';
    if (t.indexOf('SOCCER') !== -1) return 'Soccer';
    if (t.indexOf('CBB') !== -1) return 'CBB';
    if (t.indexOf('NFL') !== -1) return 'NFL';
    return null;
  }

  function waitForTickets(cb) {
    var el = document.querySelector('.tickets-built');
    if (el) { cb(el); return; }
    var obs = new MutationObserver(function () {
      var found = document.querySelector('.tickets-built');
      if (found) { obs.disconnect(); cb(found); }
    });
    obs.observe(document.body, { childList: true, subtree: true });
  }

  function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function kpiCard(val, lbl) {
    return '<div class="rd-kpi-card"><div class="rd-kpi-val">' + val + '</div><div class="rd-kpi-lbl">' + lbl + '</div></div>';
  }

  function rdSortTickets(built, sortBy) {
    built.querySelectorAll('.ticket-group-section').forEach(function (sec) {
      var body = sec.querySelector('.ticket-group-body');
      if (!body) return;
      var items = Array.from(body.children);
      var ticketItems = items.filter(function(el) {
        return el.classList.contains('ticket') || el.querySelector('.ticket');
      });
      if (!ticketItems.length) return;

      function getKpi(el, re) {
        var val = NaN;
        el.querySelectorAll('.kpi').forEach(function (kpi) {
          var lbl = kpi.querySelector('.kpi-label');
          var v   = kpi.querySelector('.kpi-val');
          if (lbl && v && re.test(lbl.textContent)) val = parseFloat(v.textContent);
        });
        return val;
      }

      ticketItems.sort(function (a, b) {
        var ta = a.classList.contains('ticket') ? a : a.querySelector('.ticket');
        var tb = b.classList.contains('ticket') ? b : b.querySelector('.ticket');
        if (!ta || !tb) return 0;
        if (sortBy === 'ev')   return getKpi(tb, /ev/i) - getKpi(ta, /ev/i);
        if (sortBy === 'pwin') return getKpi(tb, /p.win|pwin/i) - getKpi(ta, /p.win|pwin/i);
        if (sortBy === 'legs') return getKpi(tb, /legs|leg\b/i) - getKpi(ta, /legs|leg\b/i);
        return 0;
      });

      ticketItems.forEach(function (el) { body.appendChild(el); });
    });
  }

  waitForTickets(function (built) {

    /* 1. Tag sections with sport */
    built.querySelectorAll('.ticket-group-section').forEach(function (sec) {
      var header = sec.querySelector('.group-title');
      var sport = sportFromTitle(header ? header.textContent : '');
      if (sport) {
        sec.setAttribute('data-sport', sport);
        var color = SPORT_COLORS[sport] || '';
        if (color) sec.style.setProperty('--rd-sport-color', color);
      }
      /* count badge */
      var tickets = sec.querySelectorAll('.ticket');
      var hdr = sec.querySelector('.ticket-group-header');
      if (hdr && tickets.length && !hdr.querySelector('.rd-group-count')) {
        var badge = document.createElement('span');
        badge.className = 'rd-group-count';
        badge.textContent = tickets.length + ' ticket' + (tickets.length !== 1 ? 's' : '');
        var evBadge = hdr.querySelector('.group-ev-badge');
        if (evBadge) hdr.insertBefore(badge, evBadge); else hdr.appendChild(badge);
      }
    });

    /* 2. Sport dot + rec badge on each ticket */
    built.querySelectorAll('.ticket').forEach(function (t) {
      var sec = t.closest('.ticket-group-section');
      var sport = sec ? sec.getAttribute('data-sport') : null;
      var color = sport ? (SPORT_COLORS[sport] || '') : '';
      if (color) t.style.setProperty('--rd-sport-color', color);
      var hdr = t.querySelector('.ticket-hdr');
      if (hdr && sport && !hdr.querySelector('.rd-sport-dot')) {
        var dot = document.createElement('span');
        dot.className = 'rd-sport-dot';
        dot.style.background = color;
        dot.title = sport;
        hdr.insertBefore(dot, hdr.firstChild);
      }
      if (hdr && !hdr.querySelector('.rd-rec-badge')) {
        var recText = '';
        var recKpi = t.querySelector('[data-kpi="recommendation"]');
        if (recKpi) recText = recKpi.textContent.trim();
        if (!recText) {
          t.querySelectorAll('.kpi').forEach(function (kpi) {
            var lbl = kpi.querySelector('.kpi-label');
            var val = kpi.querySelector('.kpi-val');
            if (lbl && val && /rec/i.test(lbl.textContent)) recText = val.textContent.trim();
          });
        }
        if (recText) {
          var rbadge = document.createElement('span');
          var upper = recText.toUpperCase();
          rbadge.className = 'rd-rec-badge ' +
            (upper === 'STRONG' ? 'rd-rec-badge--strong' : upper === 'OK' ? 'rd-rec-badge--ok' : 'rd-rec-badge--skip');
          rbadge.textContent = upper;
          hdr.appendChild(rbadge);
        }
      }
    });

    /* 3. Summary KPI strip */
    var allTickets = built.querySelectorAll('.ticket');
    var strongCount = Array.from(allTickets).filter(function(t) {
      var r = t.querySelector('[data-kpi="recommendation"]');
      return r && /strong/i.test(r.textContent);
    }).length;
    var filterBar = built.querySelector('.ticket-filter-bar');
    if (filterBar && !built.querySelector('.rd-summary-strip')) {
      var strip = document.createElement('div');
      strip.className = 'rd-summary-strip';
      strip.innerHTML = kpiCard(built.querySelectorAll('.ticket-group-section').length, 'Groups') +
                        kpiCard(allTickets.length, 'Slips') +
                        kpiCard(strongCount, 'Strong EV');
      filterBar.parentNode.insertBefore(strip, filterBar);
    }

    /* 4. Hero strip — top 3 by EV */
    if (!built.querySelector('.rd-hero-strip')) {
      var heroData = [];
      allTickets.forEach(function (t) {
        var noEl = t.querySelector('.ticket-no');
        var name = noEl ? noEl.textContent.trim() : '';
        var evVal = NaN, pwinVal = NaN, legs = 0;
        t.querySelectorAll('.kpi').forEach(function (kpi) {
          var lbl = kpi.querySelector('.kpi-label');
          var val = kpi.querySelector('.kpi-val');
          if (!lbl || !val) return;
          var l = lbl.textContent.toLowerCase();
          if (/ev/.test(l))               evVal   = parseFloat(val.textContent);
          if (/p.win|pwin|win prob/i.test(l)) pwinVal = parseFloat(val.textContent);
          if (/^legs?$/i.test(l.trim()))   legs    = parseInt(val.textContent) || 0;
        });
        var sec2 = t.closest('.ticket-group-section');
        var sp = sec2 ? (sec2.getAttribute('data-sport') || '') : '';
        if (!isNaN(evVal) && name) heroData.push({ name: name, ev: evVal, pwin: pwinVal, sport: sp, legs: legs, el: t });
      });
      heroData.sort(function(a,b){ return b.ev - a.ev; });
      var top3 = heroData.slice(0, 3);
      if (top3.length) {
        var hstrip = document.createElement('div');
        hstrip.className = 'rd-hero-strip';
        var hlabel = document.createElement('div');
        hlabel.className = 'rd-hero-label';
        hlabel.textContent = '\u2B50 Today\'s best \u2014 highest EV';
        hstrip.appendChild(hlabel);
        var cards = document.createElement('div');
        cards.className = 'rd-hero-cards';
        top3.forEach(function(item, idx) {
          var color = SPORT_COLORS[item.sport] || '#D4AF37';
          var card = document.createElement('div');
          card.className = 'rd-hero-card';
          card.style.setProperty('--rd-sport-color', color);
          var evFmt = isNaN(item.ev) ? '\u2014' : item.ev.toFixed(2);
          var pwinFmt = isNaN(item.pwin) ? '' : item.pwin.toFixed(1) + '%';
          var legStr = item.legs ? item.legs + '-leg' : '';
          card.innerHTML = '<div class="rd-hero-rank">#' + (idx+1) + ' pick</div>' +
            '<div class="rd-hero-name">' + escHtml(item.name) + '</div>' +
            '<div class="rd-hero-meta">' + escHtml(item.sport) + (legStr ? ' \u00B7 ' + legStr : '') + '</div>' +
            '<div class="rd-hero-foot"><span class="rd-hero-ev">EV ' + evFmt + '</span>' +
            (pwinFmt ? '<span class="rd-hero-pwin">P(win) ' + escHtml(pwinFmt) + '</span>' : '') + '</div>';
          card.addEventListener('click', function() {
            item.el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            item.el.style.transition = 'box-shadow .4s';
            item.el.style.boxShadow = '0 0 0 2px ' + color + ', 0 8px 24px rgba(0,0,0,.4)';
            setTimeout(function() { item.el.style.boxShadow = ''; }, 1800);
          });
          cards.appendChild(card);
        });
        hstrip.appendChild(cards);
        var summaryStrip = built.querySelector('.rd-summary-strip');
        var insertBefore = summaryStrip || filterBar;
        if (insertBefore) insertBefore.parentNode.insertBefore(hstrip, insertBefore);
      }
    }

  }); /* end waitForTickets */

})();
