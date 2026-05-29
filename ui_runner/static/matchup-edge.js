/**
 * Multi-sport Matchup Edge panels — Slate Explorer (#sp-{sport})
 */
(function (global) {
  const ME_SPORTS = ["nba", "nba1h", "nba1q", "wnba", "nhl", "mlb", "soccer", "cbb", "cfb", "nfl", "tennis"];
  const SKIP = new Set(["combined", "wcbb"]);

  const PROP_SEARCH = {
    pts: ["points", "pts"],
    reb: ["rebounds", "reb"],
    ast: ["assists", "ast"],
    fg3m: ["3-pointer", "3pt", "fg3m"],
    stl: ["steals"],
    blk: ["blocks"],
    pra: ["pts+reb+ast", "pra"],
    goals: ["goals"],
    assists: ["assists"],
    points: ["points"],
    shots: ["shots", "sog"],
    hits: ["hits"],
    strikeouts: ["strikeout", "k's"],
    total_bases: ["total bases"],
    home_runs: ["home run"],
    pass_yds: ["pass", "passing"],
    rush_yds: ["rush"],
    rec_yds: ["receiving", "rec yds"],
    match_total_games: ["total games", "games"],
    games_won: ["games won"],
    aces: ["aces"],
    double_faults: ["double fault"],
    break_points_won: ["break points", "break points won"],
  };

  const state = {};

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function pid(sport, part) {
    const legacy = document.getElementById("wnba-me-team");
    if (sport === "wnba" && legacy) return "wnba-me-" + part;
    return "me-" + sport + "-" + part;
  }

  function panelId(sport) {
    return sport === "wnba" && document.getElementById("wnba-matchup-edge-panel")
      ? "wnba-matchup-edge-panel"
      : "matchup-edge-" + sport;
  }

  function tierClass(tier) {
    const t = String(tier || "").toLowerCase();
    if (t === "elite" || t === "above avg") return "tier-elite";
    if (t === "weak" || t === "below avg") return "tier-weak";
    return "";
  }

  function edgeLabel(edge) {
    return String(edge || "NEUTRAL").replace(/_/g, " ");
  }

  function isOverEdge(edge) {
    return edge === "TOP_EDGE" || edge === "OK_EDGE";
  }

  function isUnderEdge(edge) {
    return edge === "TOP_UNDER" || edge === "OK_UNDER";
  }

  function edgeRank(edge) {
    if (edge === "TOP_EDGE" || edge === "TOP_UNDER") return 0;
    if (edge === "OK_EDGE" || edge === "OK_UNDER") return 1;
    if (edge === "NEUTRAL") return 2;
    return 3;
  }

  function apiUrl(sport) {
    return "/api/" + sport + "/matchup-edge";
  }

  function fallbackUrl(sport) {
    const rel = "data/" + sport + "_matchup_edge.json";
    if (
      global.location &&
      (global.location.protocol === "file:" || global.location.pathname.includes("/mobile"))
    ) {
      return rel;
    }
    return "/" + rel.replace(/^\//, "");
  }

  function ensurePanel(sport) {
    const id = panelId(sport);
    let panel = document.getElementById(id);
    if (panel) return panel;
    const sp = document.getElementById("sp-" + sport);
    if (!sp) return null;

    panel = document.createElement("details");
    panel.id = id;
    panel.className = "matchup-edge-panel me-sport-" + sport;
    panel.dataset.sport = sport;
    panel.open = true;
    const label = sport.toUpperCase().replace("NBA1H", "NBA 1H").replace("NBA1Q", "NBA 1Q");
    const isPlayer = sport === "tennis";
    const teamLbl = isPlayer ? "Player" : "Team";
    const oppLbl = isPlayer ? "Opponent player" : "Opponent";
    panel.innerHTML =
      '<summary>Matchup Edge — ' +
      label +
      (isPlayer ? " — opponent player lookup" : " defense lookup") +
      "</summary>" +
      '<div class="me-body">' +
      '<div class="me-loading" id="' +
      pid(sport, "loading") +
      '">Loading matchup data…</div>' +
      '<div id="' +
      pid(sport, "content") +
      '" style="display:none">' +
      '<div class="me-controls">' +
      '<div class="me-field"><label>' +
      teamLbl +
      '</label><select id="' +
      pid(sport, "team") +
      '"></select></div>' +
      '<div class="me-field"><label>Category</label><select id="' +
      pid(sport, "cat") +
      '"></select></div>' +
      '<div class="me-field"><label>' +
      oppLbl +
      '</label><select id="' +
      pid(sport, "opp") +
      '" disabled></select></div>' +
      '<div class="me-find-row">' +
      '<button type="button" class="me-find me-find-over" id="' +
      pid(sport, "find-over") +
      '">Find overs ↗</button>' +
      '<button type="button" class="me-find me-find-under" id="' +
      pid(sport, "find-under") +
      '">Find unders ↗</button>' +
      "</div>" +
      "</div>" +
      '<div class="me-cards" id="' +
      pid(sport, "cards") +
      '"></div>' +
      '<div class="me-table-wrap"><table class="me-table"><thead><tr>' +
      "<th>Player</th><th>Pos</th><th id='" +
      pid(sport, "avg-h") +
      "'>Avg</th><th>Game score</th>" +
      "<th>Edge vs opp</th><th>Notes</th>" +
      "</tr></thead><tbody id='" +
      pid(sport, "tbody") +
      "'></tbody></table></div>" +
      '<div class="me-legend" id="' +
      pid(sport, "legend") +
      '"></div></div></div>';

    const toolbar = sp.querySelector(".slate-toolbar");
    if (toolbar) sp.insertBefore(panel, toolbar);
    else sp.prepend(panel);
    bindEvents(sport);
    return panel;
  }

  function bindEvents(sport) {
    const teamSel = document.getElementById(pid(sport, "team"));
    const catSel = document.getElementById(pid(sport, "cat"));
    const findOverBtn = document.getElementById(pid(sport, "find-over"));
    const findUnderBtn = document.getElementById(pid(sport, "find-under"));
    if (teamSel && !teamSel.dataset.meBound) {
      teamSel.dataset.meBound = "1";
      teamSel.addEventListener("change", () => onTeamChange(sport));
    }
    if (catSel && !catSel.dataset.meBound) {
      catSel.dataset.meBound = "1";
      catSel.addEventListener("change", () => render(sport));
    }
    if (findOverBtn && !findOverBtn.dataset.meBound) {
      findOverBtn.dataset.meBound = "1";
      findOverBtn.addEventListener("click", () => findProps(sport, "OVER"));
    }
    if (findUnderBtn && !findUnderBtn.dataset.meBound) {
      findUnderBtn.dataset.meBound = "1";
      findUnderBtn.addEventListener("click", () => findProps(sport, "UNDER"));
    }
  }

  async function loadData(sport) {
    if (state[sport]?.data) return state[sport].data;
    let data = null;
    try {
      const res = await fetch(apiUrl(sport), { cache: "no-store" });
      if (res.ok) {
        data = await res.json();
        if (!data.error) {
          state[sport] = state[sport] || {};
          state[sport].data = data;
          return data;
        }
      }
    } catch (_) {}
    try {
      const fb = await fetch(fallbackUrl(sport), { cache: "no-store" });
      if (fb.ok) {
        data = await fb.json();
        state[sport] = state[sport] || {};
        state[sport].data = data;
        return data;
      }
    } catch (_) {}
    throw new Error("unavailable");
  }

  function populateSelectors(sport) {
    const data = state[sport]?.data;
    if (!data) return;
    const teamSel = document.getElementById(pid(sport, "team"));
    const catSel = document.getElementById(pid(sport, "cat"));
    if (!teamSel || !catSel) return;
    const playerMode = data.matchup_mode === "player";

    const blockKeys = Object.keys(data.players_by_team_cat || {});
    const teamsWithBlocks = new Set(blockKeys.map((k) => k.split("|")[0].toUpperCase()));
    const normAbbr = (s) => String(s || "").toUpperCase();
    const edgeRankFn = (e) => edgeRank(e);
    const bestEdgeScore = (abbr) => {
      const prefix = normAbbr(abbr);
      const blocks = data.players_by_team_cat || {};
      let best = 3;
      let maxPp = -Infinity;
      Object.keys(blocks).forEach((k) => {
        if (normAbbr(k.split("|")[0]) !== prefix) return;
        const block = blocks[k];
        const players = Array.isArray(block) ? block : block?.players || [];
        players.forEach((p) => {
          const r = edgeRankFn(p.edge);
          if (r < best) best = r;
          const pe = p.pp_edge;
          if (pe != null && !Number.isNaN(Number(pe)) && Number(pe) > maxPp) maxPp = Number(pe);
        });
      });
      return { rank: best, maxPp: maxPp === -Infinity ? -999 : maxPp };
    };
    const teams = (data.teams || [])
      .filter((t) => {
        const ab = normAbbr(t?.slate_abbr || t?.def_key);
        return !teamsWithBlocks.size || teamsWithBlocks.has(ab);
      })
      .slice()
      .sort((a, b) => {
        const abA = a.slate_abbr || a.def_key || "";
        const abB = b.slate_abbr || b.def_key || "";
        const scoreA = bestEdgeScore(abA);
        const scoreB = bestEdgeScore(abB);
        if (scoreA.rank !== scoreB.rank) return scoreA.rank - scoreB.rank;
        if (scoreB.maxPp !== scoreA.maxPp) return scoreB.maxPp - scoreA.maxPp;
        return String(a.name).localeCompare(String(b.name));
      });
    if (!teams.length && data.matchups) {
      Object.keys(data.matchups).forEach((k) => {
        const mu = data.matchups[k] || {};
        teams.push({
          slate_abbr: k,
          name: mu.opponent_name ? k : playerMode ? mu.opponent_name || k : k,
        });
      });
    }
    if (playerMode && !teams.length && data.players_by_team_cat) {
      const seen = new Set();
      Object.keys(data.players_by_team_cat).forEach((key) => {
        const pk = key.split("|")[0];
        if (seen.has(pk)) return;
        seen.add(pk);
        const block = data.players_by_team_cat[key];
        const nm = (block.players && block.players[0] && block.players[0].player) || pk;
        teams.push({ slate_abbr: pk, name: nm });
      });
    }
    teamSel.innerHTML = teams
      .map((t) => {
        const ab = t.slate_abbr || t.def_key;
        const oppInfo = opponentForTeam(sport, ab);
        const opp = oppInfo.oppName || oppInfo.opp || "";
        const label = (t.name || ab) + (opp ? " vs " + opp : "");
        return '<option value="' + esc(ab) + '">' + esc(label) + "</option>";
      })
      .join("");

    catSel.innerHTML = (data.categories || [])
      .map((c) => '<option value="' + esc(c.id) + '">' + esc(c.label) + "</option>")
      .join("");

    onTeamChange(sport);
  }

  function opponentForTeam(sport, team) {
    const data = state[sport]?.data;
    if (!data || !team) return { opp: "", oppName: "" };
    const mu = (data.matchups || {})[team] || {};
    let opp = mu.opponent_slate || "";
    let oppName = mu.opponent_name || opp;
    if (!opp) {
      const entry = Object.entries(data.players_by_team_cat || {}).find(([k]) =>
        k.startsWith(team + "|")
      );
      const blockOpp = entry ? entry[1].opponent || {} : {};
      opp = blockOpp.slate_abbr || "";
      oppName = blockOpp.name || opp;
    }
    return { opp, oppName, mu };
  }

  function onTeamChange(sport) {
    const data = state[sport]?.data;
    const team = document.getElementById(pid(sport, "team"))?.value;
    const catSel = document.getElementById(pid(sport, "cat"));
    const oppSel = document.getElementById(pid(sport, "opp"));
    if (!oppSel || !data || !team) return;
    if (catSel) {
      const teamCats = Object.keys(data.players_by_team_cat || {})
        .filter((k) => k.startsWith(team + "|"))
        .map((k) => k.split("|")[1]);
      if (teamCats.length && !teamCats.includes(catSel.value)) {
        catSel.value = teamCats[0];
      }
    }
    const { opp, oppName } = opponentForTeam(sport, team);
    oppSel.innerHTML = opp
      ? '<option value="' + esc(opp) + '">' + esc(oppName) + "</option>"
      : '<option value="">—</option>';
    render(sport);
  }

  function currentBlock(sport) {
    const data = state[sport]?.data;
    const team = document.getElementById(pid(sport, "team"))?.value;
    const cat = document.getElementById(pid(sport, "cat"))?.value;
    if (!team || !cat || !data) return null;
    return (data.players_by_team_cat || {})[team + "|" + cat] || null;
  }

  function render(sport) {
    const data = state[sport]?.data;
    const block = currentBlock(sport);
    const team = document.getElementById(pid(sport, "team"))?.value;
    const cat = document.getElementById(pid(sport, "cat"))?.value;
    const catLabel = (data?.categories || []).find((c) => c.id === cat)?.label || cat;
    const cards = document.getElementById(pid(sport, "cards"));
    const tbody = document.getElementById(pid(sport, "tbody"));
    const avgH = document.getElementById(pid(sport, "avg-h"));
    const legend = document.getElementById(pid(sport, "legend"));
    if (!block || !cards || !tbody || !data) return;

    const oppMeta = opponentForTeam(sport, team);
    const mu = oppMeta.mu || {};
    const opp = block.opponent || {};
    const oppRank = opp.def_rank != null ? opp.def_rank : mu.opponent_def_rank;
    const oppTier = opp.def_tier || mu.opponent_def_tier || "";
    const oppName = opp.name || oppMeta.oppName || mu.opponent_name || "—";
    const rankLbl = data.opp_metric_label || "Opp def rank";
    let top = 0,
      ok = 0,
      under = 0;
    (block.players || []).forEach((p) => {
      if (p.edge === "TOP_EDGE") top++;
      else if (p.edge === "OK_EDGE") ok++;
      else if (isUnderEdge(p.edge)) under++;
    });

    cards.innerHTML =
      '<div class="me-card"><div class="lbl">' +
      esc(rankLbl) +
      '</div><div class="val ' +
      tierClass(oppTier) +
      '">#' +
      esc(oppRank != null ? oppRank : "—") +
      '</div></div><div class="me-card"><div class="lbl">Opp def tier</div><div class="val ' +
      tierClass(oppTier) +
      '">' +
      esc(oppTier || "—") +
      '</div></div><div class="me-card"><div class="lbl">Top over</div><div class="val edge-top">' +
      top +
      '</div></div><div class="me-card"><div class="lbl">OK over</div><div class="val edge-ok">' +
      ok +
      '</div></div><div class="me-card"><div class="lbl">Under edge</div><div class="val edge-under">' +
      under +
      '</div></div><div class="me-card"><div class="lbl">' +
      (data.matchup_mode === "player" ? "Your rank" : "Team def rank") +
      '</div><div class="val">#' +
      esc(mu.team_def_rank != null ? mu.team_def_rank : "—") +
      "</div></div>";

    if (avgH) avgH.textContent = (catLabel || "Stat").split(" ")[0] + " avg";

    tbody.innerHTML = (block.players || [])
      .map(
        (p) => {
          const rankBadge = p.team_rank_label
            ? ' <span class="me-rank-badge">' + esc(p.team_rank_label) + "</span>"
            : p.bottom3_on_team
              ? ' <span class="me-rank-badge me-rank-b3">B' + esc(p.bottom_rank_on_team || "?") + "</span>"
              : "";
          return (
          "<tr><td><strong>" +
          esc(p.player) +
          "</strong>" +
          rankBadge +
          "</td><td>" +
          esc(p.pos || "—") +
          "</td><td>" +
          esc(p.season_avg) +
          "</td><td>" +
          esc(p.game_score) +
          '</td><td><span class="me-edge ' +
          esc(p.edge) +
          '">' +
          edgeLabel(p.edge) +
          "</span></td><td>" +
          esc(p.notes) +
          "</td></tr>"
          );
        }
      )
      .join("");

    const overBtn = document.getElementById(pid(sport, "find-over"));
    const underBtn = document.getElementById(pid(sport, "find-under"));
    const hasOver = (block.players || []).some((p) => isOverEdge(p.edge));
    const hasUnder = (block.players || []).some((p) => isUnderEdge(p.edge));
    if (overBtn) overBtn.disabled = !hasOver;
    if (underBtn) underBtn.disabled = !hasUnder;

    const panel = document.getElementById(panelId(sport));
    if (panel) {
      const sum = panel.querySelector("summary");
      if (sum)
        sum.textContent =
          "Matchup Edge — " +
          (data.display_name || sport.toUpperCase()) +
          " | " +
          catLabel +
          " vs " +
          oppName;
    }

    if (legend && data.edge_legend) {
      legend.innerHTML =
        "<strong>Edge logic:</strong> " +
        Object.entries(data.edge_legend)
          .map(([k, v]) => "<strong>" + k.replace(/_/g, " ") + ":</strong> " + esc(v))
          .join(" · ");
    }
  }

  function findProps(sport, direction) {
    const block = currentBlock(sport);
    const terms = PROP_SEARCH[document.getElementById(pid(sport, "cat"))?.value] || [];
    const wantUnder = String(direction || "").toUpperCase() === "UNDER";
    const overPlayers = (block?.players || []).filter((p) => isOverEdge(p.edge)).map((p) => p.player);
    const underPlayers = (block?.players || []).filter((p) => isUnderEdge(p.edge)).map((p) => p.player);
    const pool = wantUnder ? underPlayers : overPlayers;
    const search = pool[0] || terms[0] || "";

    const input = document.getElementById("sf-" + sport);
    if (input) {
      input.value = search;
      if (typeof global.filterSlate === "function") global.filterSlate(sport, search);
    }
    const dirBtn = document.getElementById("sfb-" + sport + (wantUnder ? "-under" : "-over"));
    if (dirBtn && !dirBtn.classList.contains("on")) dirBtn.click();
    document.getElementById("st-" + sport)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function init(sport) {
    if (SKIP.has(sport)) return;
    ensurePanel(sport);
    bindEvents(sport);
    const loading = document.getElementById(pid(sport, "loading"));
    const content = document.getElementById(pid(sport, "content"));
    try {
      await loadData(sport);
      if (loading) loading.style.display = "none";
      if (content) content.style.display = "block";
      populateSelectors(sport);
      // Race guard: if dropdown populated with 0 options, retry once after
      // a short delay (panel DOM may not have been ready on first paint)
      const teamSel = document.getElementById(pid(sport, "team"));
      if (teamSel && teamSel.options.length === 0) {
        await new Promise((r) => setTimeout(r, 250));
        populateSelectors(sport);
      }
      state[sport].ready = true;
    } catch (e) {
      if (loading)
        loading.textContent =
          "Matchup data unavailable — run: py -3 scripts/build_matchup_edge_json.py --sport " + sport;
      console.warn("Matchup edge", sport, e);
    }
  }

  function onPanelOpen(sport) {
    if (SKIP.has(sport)) return;
    // If data already loaded (e.g. panel closed and reopened), skip fetch
    // but always re-populate in case DOM was rebuilt
    if (state[sport]?.ready) {
      populateSelectors(sport);
    } else {
      init(sport);
    }
  }

  const origToggle = global.toggleSlatePanel;
  if (typeof origToggle === "function") {
    global.toggleSlatePanel = function (sport) {
      origToggle(sport);
      if (ME_SPORTS.includes(sport)) onPanelOpen(sport);
    };
  }

  function boot() {
    ME_SPORTS.forEach((s) => {
      ensurePanel(s);
      bindEvents(s);
      const card = document.getElementById("sc-" + s);
      if (card && !card.dataset.meBound) {
        card.dataset.meBound = "1";
        card.addEventListener("click", () => {
          setTimeout(() => onPanelOpen(s), 0);
        });
      }
      if (document.getElementById("sp-" + s)?.classList.contains("open")) onPanelOpen(s);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  global.MatchupEdge = { init: init, render: render, sports: ME_SPORTS };
})(typeof window !== "undefined" ? window : globalThis);
