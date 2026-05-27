/**
 * WNBA Matchup Edge panel — Slate Explorer (#sp-wnba)
 */
(function (global) {
  const API = "/api/wnba/matchup-edge";
  const FALLBACK_JSON = "data/wnba_matchup_edge.json";
  const PROP_NORM_SEARCH = {
    pts: ["points", "pts"],
    reb: ["rebounds", "reb"],
    ast: ["assists", "ast"],
    fg3m: ["3-pointer", "3pt", "3-pointers", "fg3m"],
    stl: ["steals", "stl"],
    blk: ["blocks", "blk"],
    stocks: ["stocks", "stl+blk"],
    pra: ["pts+reb+ast", "pra", "points + rebounds + assists"],
  };

  let data = null;
  let panelEl = null;
  let initialized = false;

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function tierClass(tier) {
    const t = String(tier || "").toLowerCase();
    if (t === "elite" || t === "above avg") return "tier-elite";
    if (t === "weak" || t === "below avg") return "tier-weak";
    return "";
  }

  function edgeLabel(edge) {
    return String(edge || "NEUTRAL").replace("_", " ");
  }

  function ensurePanel() {
    const existing = document.getElementById("wnba-matchup-edge-panel");
    if (existing) {
      panelEl = existing;
      bindPanelEvents();
      return existing;
    }
    const sp = document.getElementById("sp-wnba");
    if (!sp) return null;
    const details = document.createElement("details");
    details.id = "wnba-matchup-edge-panel";
    details.className = "wnba-me-panel";
    details.open = true;
    details.innerHTML =
      '<summary>Matchup Edge — WNBA defense lookup</summary>' +
      '<div class="wnba-me-body">' +
      '<div class="wnba-me-loading" id="wnba-me-loading">Loading matchup data…</div>' +
      '<div id="wnba-me-content" style="display:none">' +
      '<div class="wnba-me-controls">' +
      '<div class="wnba-me-field"><label>Team</label><select id="wnba-me-team"></select></div>' +
      '<div class="wnba-me-field"><label>Category</label><select id="wnba-me-cat"></select></div>' +
      '<div class="wnba-me-field"><label>Opponent</label><select id="wnba-me-opp" disabled></select></div>' +
      '<button type="button" class="wnba-me-find" id="wnba-me-find">Find props ↗</button>' +
      "</div>" +
      '<div class="wnba-me-cards" id="wnba-me-cards"></div>' +
      '<div class="wnba-me-table-wrap">' +
      '<table class="wnba-me-table"><thead><tr>' +
      "<th>Player</th><th>Pos</th><th id='wnba-me-avg-h'>Avg</th><th>Game score</th>" +
      "<th>Edge vs opp</th><th>Notes</th>" +
      "</tr></thead><tbody id='wnba-me-tbody'></tbody></table>" +
      "</div>" +
      '<div class="wnba-me-legend" id="wnba-me-legend"></div>' +
      "</div></div>";
    const toolbar = sp.querySelector(".slate-toolbar");
    if (toolbar) sp.insertBefore(details, toolbar);
    else sp.prepend(details);
    panelEl = details;
    bindPanelEvents();
    return details;
  }

  function bindPanelEvents() {
    const teamSel = document.getElementById("wnba-me-team");
    const catSel = document.getElementById("wnba-me-cat");
    const findBtn = document.getElementById("wnba-me-find");
    if (teamSel && !teamSel.dataset.bound) {
      teamSel.dataset.bound = "1";
      teamSel.addEventListener("change", onTeamChange);
    }
    if (catSel && !catSel.dataset.bound) {
      catSel.dataset.bound = "1";
      catSel.addEventListener("change", render);
    }
    if (findBtn && !findBtn.dataset.bound) {
      findBtn.dataset.bound = "1";
      findBtn.addEventListener("click", findProps);
    }
  }

  async function loadData() {
    if (data) return data;
    try {
      const res = await fetch(API, { cache: "no-store" });
      if (res.ok) {
        data = await res.json();
        if (!data.error) return data;
      }
    } catch (_) { /* static / offline fallback */ }
    const fb = await fetch(FALLBACK_JSON, { cache: "no-store" });
    if (!fb.ok) throw new Error("matchup edge JSON unavailable");
    data = await fb.json();
    return data;
  }

  function populateSelectors() {
    if (!data) return;
    const teamSel = document.getElementById("wnba-me-team");
    const catSel = document.getElementById("wnba-me-cat");
    if (!teamSel || !catSel) return;

    const teams = (data.teams || []).slice().sort((a, b) =>
      String(a.name).localeCompare(String(b.name))
    );
    teamSel.innerHTML = teams
      .map((t) => {
        const mu = (data.matchups || {})[t.slate_abbr] || {};
        const opp = mu.opponent_name || mu.opponent_slate || "—";
        const label = t.name + (mu.opponent_slate ? " vs " + opp : "");
        return (
          '<option value="' +
          esc(t.slate_abbr) +
          '">' +
          esc(label) +
          "</option>"
        );
      })
      .join("");

    catSel.innerHTML = (data.categories || [])
      .map((c) => '<option value="' + esc(c.id) + '">' + esc(c.label) + "</option>")
      .join("");

    onTeamChange();
  }

  function onTeamChange() {
    const team = document.getElementById("wnba-me-team")?.value;
    const oppSel = document.getElementById("wnba-me-opp");
    if (!oppSel || !data || !team) return;
    const mu = (data.matchups || {})[team] || {};
    const opp = mu.opponent_slate || "";
    const oppName = mu.opponent_name || opp;
    oppSel.innerHTML = opp
      ? '<option value="' + esc(opp) + '">' + esc(oppName) + "</option>"
      : '<option value="">—</option>';
    render();
  }

  function currentBlock() {
    const team = document.getElementById("wnba-me-team")?.value;
    const cat = document.getElementById("wnba-me-cat")?.value;
    if (!team || !cat || !data) return null;
    return (data.players_by_team_cat || {})[team + "|" + cat] || null;
  }

  function countEdges(players) {
    let top = 0,
      ok = 0;
    (players || []).forEach((p) => {
      if (p.edge === "TOP_EDGE") top++;
      else if (p.edge === "OK_EDGE") ok++;
    });
    return { top, ok };
  }

  function render() {
    const block = currentBlock();
    const team = document.getElementById("wnba-me-team")?.value;
    const cat = document.getElementById("wnba-me-cat")?.value;
    const catLabel =
      (data.categories || []).find((c) => c.id === cat)?.label || cat;
    const cards = document.getElementById("wnba-me-cards");
    const tbody = document.getElementById("wnba-me-tbody");
    const avgH = document.getElementById("wnba-me-avg-h");
    const legend = document.getElementById("wnba-me-legend");
    if (!block || !cards || !tbody) return;

    const mu = (data.matchups || {})[team] || {};
    const opp = block.opponent || {};
    const oppRank = opp.def_rank != null ? opp.def_rank : mu.opponent_def_rank;
    const oppTier = opp.def_tier || mu.opponent_def_tier || "";
    const oppName = opp.name || mu.opponent_name || opp.slate_abbr || "—";
  const counts = countEdges(block.players);

    cards.innerHTML =
      '<div class="wnba-me-card"><div class="lbl">Opp def rank</div><div class="val ' +
      tierClass(oppTier) +
      '">#' +
      esc(oppRank != null ? oppRank : "—") +
      "</div></div>" +
      '<div class="wnba-me-card"><div class="lbl">Opp def tier</div><div class="val ' +
      tierClass(oppTier) +
      '">' +
      esc(oppTier || "—") +
      "</div></div>" +
      '<div class="wnba-me-card"><div class="lbl">Top edge plays</div><div class="val edge-top">' +
      counts.top +
      "</div></div>" +
      '<div class="wnba-me-card"><div class="lbl">OK edge plays</div><div class="val edge-ok">' +
      counts.ok +
      "</div></div>" +
      '<div class="wnba-me-card"><div class="lbl">Team def rank</div><div class="val">#' +
      esc(mu.team_def_rank != null ? mu.team_def_rank : "—") +
      "</div></div>";

    if (avgH) avgH.textContent = catLabel.split(" ")[0] + " avg";

    tbody.innerHTML = (block.players || [])
      .map(
        (p) =>
          "<tr><td><strong>" +
          esc(p.player) +
          "</strong></td><td>" +
          esc(p.pos || "—") +
          "</td><td>" +
          esc(p.season_avg) +
          "</td><td>" +
          esc(p.game_score) +
          '</td><td><span class="wnba-me-edge ' +
          esc(p.edge) +
          '">' +
          edgeLabel(p.edge) +
          "</span></td><td>" +
          esc(p.notes) +
          "</td></tr>"
      )
      .join("");

    const title =
      "Top " +
      esc((data.teams || []).find((t) => t.slate_abbr === team)?.name || team) +
      " — " +
      esc(catLabel) +
      " | vs " +
      esc(oppName) +
      " (def #" +
      esc(oppRank != null ? oppRank : "?") +
      ")";
    if (panelEl) {
      const sum = panelEl.querySelector("summary");
      if (sum) sum.textContent = "Matchup Edge — " + title;
    }

    if (legend && data.edge_legend) {
      legend.innerHTML =
        "<strong>Edge logic:</strong> " +
        Object.entries(data.edge_legend)
          .map(([k, v]) => "<strong>" + k.replace("_", " ") + ":</strong> " + esc(v))
          .join(" · ");
    }
  }

  function findProps() {
    const team = document.getElementById("wnba-me-team")?.value;
    const cat = document.getElementById("wnba-me-cat")?.value;
    const block = currentBlock();
    if (!team || !cat) return;

    const terms = PROP_NORM_SEARCH[cat] || [cat];
    const topPlayers = (block?.players || [])
      .filter((p) => p.edge === "TOP_EDGE" || p.edge === "OK_EDGE")
      .map((p) => p.player);
    const searchParts = [...topPlayers, ...terms];
    const search = searchParts[0] || terms[0] || "";

    const input = document.getElementById("sf-wnba");
    if (input) {
      input.value = search;
      if (typeof global.filterSlate === "function") global.filterSlate("wnba", search);
    }
    const overBtn = document.getElementById("sfb-wnba-over");
    if (overBtn && !overBtn.classList.contains("on")) overBtn.click();
    document.getElementById("st-wnba")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function init() {
    if (initialized) {
      render();
      return;
    }
    ensurePanel();
    const loading = document.getElementById("wnba-me-loading");
    const content = document.getElementById("wnba-me-content");
    try {
      await loadData();
      if (loading) loading.style.display = "none";
      if (content) content.style.display = "block";
      populateSelectors();
      initialized = true;
    } catch (e) {
      if (loading) loading.textContent = "Matchup data unavailable — run WNBA pipeline.";
      console.warn("WNBA matchup edge:", e);
    }
  }

  function onPanelOpen() {
    ensurePanel();
    init();
  }

  const origToggle = global.toggleSlatePanel;
  if (typeof origToggle === "function") {
    global.toggleSlatePanel = function (sport) {
      origToggle(sport);
      if (sport === "wnba") onPanelOpen();
    };
  }

  function boot() {
    if (document.getElementById("wnba-matchup-edge-panel")) {
      bindPanelEvents();
    }
    if (document.getElementById("sp-wnba")?.classList.contains("open")) {
      onPanelOpen();
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  global.WnbaMatchupEdge = { init, onPanelOpen, render };
})(typeof window !== "undefined" ? window : globalThis);
