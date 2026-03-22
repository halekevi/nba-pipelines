import { useState, useMemo, useEffect, useCallback } from "react";

// ── Data ────────────────────────────────────────────────────────────────────
const DEFAULT_POWER_BASE = { 2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0, 6: 37.5 };
const DEFAULT_FLEX_BASE = {
  2: { 2: 3.0 },
  3: { 3: 3.0, 2: 1.0 },
  4: { 4: 6.0, 3: 1.5 },
  5: { 5: 10.0, 4: 2.0, 3: 0.4 },
  6: { 6: 25.0, 5: 2.0, 4: 0.4 },
};
const DEFAULT_GOBLIN_POWER = { 1: 0.84, 2: 0.747, 3: 0.707 };
const DEFAULT_GOBLIN_FLEX  = { 1: 0.8,  2: 0.72,  3: 0.6 };
const DEFAULT_DEMON_POWER  = { 1: 1.627, 2: 2.4,  3: 2.72 };
const DEFAULT_DEMON_FLEX   = { 1: 1.6,  2: 1.52,  3: 1.56 };
const SETTINGS_KEY = "pp_payout_calc_settings_v2";
const TICKET_LOG_KEY = "po_ticket_log_v1";
const LEG_TYPES = ["Standard","Goblin -1","Goblin -2","Goblin -3","Demon +1","Demon +2","Demon +3"];
const LOGGER_SPORTS = ["NBA", "CBB", "Soccer", "NHL", "Mixed"];
const LOGGER_PROP_TYPES = [
  "Points", "Rebounds", "Assists", "PRA", "Pts+Asts", "Pts+Rebs", "Rebs+Asts", "Threes", "Steals", "Blocks",
  "Turnovers", "Fantasy Score", "FTA", "FG Attempted", "Goalie Saves", "Shots", "Shots on Target", "Goals",
  "Passes", "Tackles",
];
const LOGGER_RESULTS = ["PENDING", "WON", "LOST", "PUSH", "REFUNDED"];

function todayISODate() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function newLogLeg(tier = "Standard") {
  return { player: "", prop: "Points", line: "", direction: "OVER", tier, hit_rate: 0.52 };
}

function genLogId() {
  return Math.random().toString(36).substr(2, 9);
}

// ── Helpers ─────────────────────────────────────────────────────────────────
const r2 = x => Math.round(Number(x) * 100) / 100;
const money = x => Number(x).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function classify(t) {
  if (t.startsWith("Goblin")) return { kind: "goblin", dev: parseInt(t.split("-")[1], 10) };
  if (t.startsWith("Demon"))  return { kind: "demon",  dev: parseInt(t.split("+")[1], 10) };
  return { kind: "standard", dev: 0 };
}
function legEmoji(t) {
  if (t.startsWith("Goblin")) return "👺";
  if (t.startsWith("Demon"))  return "😈";
  return "⭐";
}
function legColor(t) {
  if (t.startsWith("Goblin")) return "#a855f7";
  if (t.startsWith("Demon"))  return "#ef4444";
  return "#3b82f6";
}

function calcPayouts({ legs, stake, mode, exactPower, exactFlex, tables, mods }) {
  const n = legs.length;
  if (n < 2) return null;
  let pm = 1, fm = 1;
  for (const leg of legs) {
    const { kind, dev } = classify(leg);
    if (kind === "goblin") { pm *= mods.goblinPower[dev] ?? mods.goblinPower[1]; fm *= mods.goblinFlex[dev] ?? mods.goblinFlex[1]; }
    if (kind === "demon")  { pm *= mods.demonPower[dev]  ?? mods.demonPower[1];  fm *= mods.demonFlex[dev]  ?? mods.demonFlex[1]; }
  }
  const basePower = tables.powerBase[n] ?? 37.5;
  const baseFlex  = tables.flexBase[n]  ?? {};
  const estPM = r2(basePower * pm);
  const estFM = r2((baseFlex[n] ?? 25) * fm);
  const powerMult  = (mode === "exact" && exactPower > 0) ? r2(exactPower) : estPM;
  const flexMult   = (mode === "exact" && exactFlex  > 0) ? r2(exactFlex)  : estFM;
  const partials = Object.entries(baseFlex)
    .map(([k, v]) => ({ correct: +k, base: +v, adj: mode === "estimate" ? r2(+v * fm) : r2(+v) }))
    .filter(p => p.correct < n)
    .sort((a, b) => b.correct - a.correct);
  return {
    n, pm, fm, basePower, baseFlexTop: baseFlex[n] ?? 0,
    powerMult, flexMult,
    powerWin: r2(stake * powerMult),
    flexWin:  r2(stake * flexMult),
    breakeven: powerMult > 0 ? r2(100 / powerMult) : 0,
    partials: partials.map(p => ({ ...p, win: r2(stake * p.adj) })),
  };
}

// ── Styles ───────────────────────────────────────────────────────────────────
const C = {
  bg: "#09090b", surface: "#111114", surface2: "#18181c", surface3: "#1e1e23",
  border: "#27272a", border2: "#3f3f46",
  text: "#fafafa", muted: "#71717a", dim: "#3f3f46",
  green: "#22c55e", blue: "#3b82f6", purple: "#a855f7",
  gold: "#f59e0b", red: "#ef4444",
};

const mono = "'DM Mono', monospace";
const syne = "'Syne', sans-serif";

const S = {
  page:     { minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "'Inter', sans-serif" },
  header:   { background: C.surface, borderBottom: `1px solid ${C.border}`, padding: "16px 28px", display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 16, position: "sticky", top: 0, zIndex: 50 },
  logo:     { display: "flex", alignItems: "center", gap: 12 },
  logoIcon: { width: 38, height: 38, background: "linear-gradient(135deg,#f59e0b,#ef4444)", borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20 },
  logoH1:   { fontFamily: syne, fontSize: 17, fontWeight: 800 },
  logoSub:  { fontFamily: mono, fontSize: 10, color: C.muted, letterSpacing: "1.5px", marginTop: 1 },
  tabs:     { background: C.surface2, borderBottom: `1px solid ${C.border}`, padding: "0 28px", display: "flex", gap: 2 },
  tabBtn:   (a) => ({ background: "none", border: "none", borderBottom: a ? `2px solid ${C.blue}` : "2px solid transparent", color: a ? C.text : C.muted, padding: "13px 18px", cursor: "pointer", fontFamily: mono, fontSize: 11, fontWeight: 500, letterSpacing: "1.5px", transition: "all .15s" }),
  wrap:     { maxWidth: 980, margin: "0 auto", padding: "28px 20px" },
  grid2:    { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 },
  secLabel: { fontFamily: mono, fontSize: 10, color: C.muted, letterSpacing: "2.5px", marginBottom: 14, textTransform: "uppercase" },
  legRow:   (color) => ({ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, background: C.surface2, borderRadius: 10, padding: "9px 12px", border: `1px solid ${color}28`, transition: "border-color .2s" }),
  legNum:   { fontFamily: mono, color: C.muted, fontSize: 11, width: 44, flexShrink: 0 },
  legSel:   (color) => ({ flex: 1, background: C.bg, border: `1px solid ${C.border2}`, borderRadius: 7, padding: "6px 10px", fontSize: 13, cursor: "pointer", outline: "none", fontFamily: mono, fontWeight: 500, color }),
  rmBtn:    { background: "rgba(239,68,68,.10)", color: C.red, border: "1px solid rgba(239,68,68,.18)", borderRadius: 7, padding: "5px 10px", cursor: "pointer", fontSize: 14, fontWeight: 700 },
  addBtn:   { width: "100%", padding: 10, background: "rgba(59,130,246,.05)", border: "1px dashed rgba(59,130,246,.30)", borderRadius: 10, color: C.blue, cursor: "pointer", fontFamily: mono, fontSize: 11, fontWeight: 500, letterSpacing: ".5px", marginTop: 4 },
  badge:    (bg, border, color) => ({ background: bg, border: `1px solid ${border}`, color, borderRadius: 99, padding: "4px 12px", fontFamily: mono, fontSize: 11, fontWeight: 500 }),
  card:     (c1, c2, bc) => ({ background: `linear-gradient(135deg,${c1},${c2})`, border: `1px solid ${bc}`, borderRadius: 14, padding: 18, marginBottom: 14 }),
  cardHead: { display: "flex", alignItems: "center", gap: 10, marginBottom: 16 },
  tag:      (bg, color, border) => ({ background: bg, color, border: `1px solid ${border}`, borderRadius: 6, padding: "3px 10px", fontFamily: mono, fontSize: 10, fontWeight: 500, letterSpacing: "1.5px" }),
  cardSub:  { color: C.muted, fontSize: 12 },
  metBox:   { background: "rgba(255,255,255,.03)", border: `1px solid ${C.border}`, borderRadius: 10, padding: 12, textAlign: "center" },
  metLabel: { fontFamily: mono, color: C.muted, fontSize: 9, letterSpacing: "2px", marginBottom: 6 },
  metVal:   (color, size = 24) => ({ fontFamily: syne, fontSize: size, fontWeight: 700, color }),
  metSub:   { fontFamily: mono, fontSize: 10, color: C.muted, marginTop: 4 },
  partWrap: { borderTop: "1px solid rgba(168,85,247,.15)", paddingTop: 12, marginTop: 14 },
  partBox:  { background: "rgba(168,85,247,.08)", border: "1px solid rgba(168,85,247,.20)", borderRadius: 10, padding: "10px 14px", textAlign: "center", minWidth: 80 },
  modBar:   { marginTop: 12, background: "rgba(255,255,255,.02)", borderRadius: 8, padding: "8px 12px", border: `1px solid ${C.border}` },
  input:    { background: C.surface2, color: C.text, border: `1px solid ${C.border2}`, borderRadius: 8, padding: "8px 12px", fontFamily: mono, fontSize: 14, fontWeight: 500, outline: "none", width: 100 },
  smallBtn: (a) => ({ background: a ? C.blue : C.surface2, color: a ? "#fff" : C.muted, border: `1px solid ${a ? C.blue : C.border}`, borderRadius: 7, padding: "6px 14px", cursor: "pointer", fontFamily: mono, fontSize: 11, fontWeight: 500, transition: "all .15s" }),
  table:    { width: "100%", borderCollapse: "collapse" },
  th:       { padding: "11px 14px", textAlign: "center", fontFamily: mono, color: C.muted, borderBottom: `1px solid ${C.border}`, fontSize: 10, letterSpacing: "1.5px", whiteSpace: "nowrap", background: C.surface2 },
  td:       { padding: "12px 14px", borderBottom: "1px solid rgba(255,255,255,.04)" },
  flexCard: { background: C.surface2, border: `1px solid ${C.border}`, borderRadius: 12, padding: "14px 18px", minWidth: 120 },
  logRowFlex: { display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, marginBottom: 8, background: C.surface2, borderRadius: 10, padding: "8px 10px", border: `1px solid ${C.border}` },
  logPlayerInp: { background: C.bg, color: C.text, border: `1px solid ${C.border2}`, borderRadius: 7, padding: "6px 8px", fontSize: 13, outline: "none", fontFamily: mono, fontWeight: 500, minWidth: 0, width: "100%" },
  logHitInp: { background: C.bg, color: C.muted, border: `1px solid ${C.border}`, borderRadius: 6, padding: "4px 4px", fontSize: 11, outline: "none", fontFamily: mono, width: 50, flexShrink: 0, textAlign: "center" },
  logSelSm: { background: C.bg, border: `1px solid ${C.border2}`, borderRadius: 7, padding: "5px 6px", fontSize: 11, cursor: "pointer", outline: "none", fontFamily: mono, color: C.text, maxWidth: "100%" },
  logLineInp: { background: C.bg, color: C.text, border: `1px solid ${C.border2}`, borderRadius: 7, padding: "5px 6px", fontSize: 12, outline: "none", fontFamily: mono, width: "100%", maxWidth: 64 },
  logDirWrap: { display: "flex", gap: 4, flexShrink: 0 },
  logBtnGreen: { width: "100%", padding: "12px 16px", background: C.green, color: "#fff", border: "none", borderRadius: 10, cursor: "pointer", fontFamily: syne, fontSize: 14, fontWeight: 700, marginTop: 12, transition: "opacity .15s" },
  logFlash: (visible) => ({ fontFamily: mono, fontSize: 12, color: C.green, textAlign: "center", marginTop: 10, opacity: visible ? 1 : 0, transition: "opacity .35s ease", minHeight: 18 }),
  logFilterRow: { display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 12 },
  logFilterLbl: { fontFamily: mono, fontSize: 9, color: C.muted, letterSpacing: "1.5px" },
  logCard: { background: C.surface2, border: `1px solid ${C.border}`, borderRadius: 12, marginBottom: 10, overflow: "hidden" },
  logCardHead: { display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, padding: "12px 14px", cursor: "pointer", justifyContent: "space-between" },
  logCardBody: { borderTop: `1px solid ${C.border}`, padding: "12px 14px", background: "rgba(0,0,0,.12)" },
  logExportBtn: { background: C.surface3, border: `1px solid ${C.border2}`, color: C.text, borderRadius: 8, padding: "8px 14px", cursor: "pointer", fontFamily: mono, fontSize: 11, fontWeight: 500 },
  logSportRow: { fontFamily: mono, fontSize: 10, color: C.muted, marginBottom: 4, lineHeight: 1.4 },
  logComboRow: { fontFamily: mono, fontSize: 10, color: C.muted, marginTop: 6, lineHeight: 1.5 },
  logFieldLbl: { fontFamily: mono, fontSize: 9, color: C.muted, letterSpacing: "1.2px", marginBottom: 6 },
  logPreviewBox: { background: "rgba(255,255,255,.03)", border: `1px solid ${C.border}`, borderRadius: 10, padding: "10px 12px", marginTop: 10, fontFamily: mono, fontSize: 12, color: C.text, lineHeight: 1.6 },
  logDashGrid: { display: "grid", gridTemplateColumns: "repeat(5, minmax(0, 1fr))", gap: 10, marginBottom: 12 },
};

function resultBadgeColors(res) {
  switch (res) {
    case "WON": return { bg: "rgba(34,197,94,.15)", border: "rgba(34,197,94,.35)", color: C.green };
    case "LOST": return { bg: "rgba(239,68,68,.12)", border: "rgba(239,68,68,.28)", color: C.red };
    case "PUSH": return { bg: "rgba(245,158,11,.12)", border: "rgba(245,158,11,.28)", color: C.gold };
    case "REFUNDED": return { bg: "rgba(59,130,246,.12)", border: "rgba(59,130,246,.28)", color: C.blue };
    default: return { bg: "rgba(113,113,122,.12)", border: "rgba(113,113,122,.25)", color: C.muted };
  }
}

function resolveActualPayout({ result, ticketType, powerWin, flexWin, stake, payoutOverride }) {
  const st = Math.max(0, Number(stake) || 0);
  if (result === "PENDING") return null;
  if (result === "WON") {
    const o = payoutOverride;
    if (o !== null && o !== undefined && String(o).trim() !== "" && !isNaN(Number(o))) return r2(Number(o));
    return r2(ticketType === "flex" ? flexWin : powerWin);
  }
  if (result === "LOST") return 0;
  if (result === "PUSH" || result === "REFUNDED") return r2(st);
  return null;
}

// ── Component ────────────────────────────────────────────────────────────────
export default function PayoutCalculator() {
  const [tab, setTab] = useState("builder");
  const [stake, setStake] = useState(10);
  const [legs, setLegs] = useState(["Standard", "Standard", "Standard"]);
  const [mode, setMode] = useState("estimate");
  const [exactPower, setExactPower] = useState("");
  const [exactFlex, setExactFlex]   = useState("");
  const [refLegs, setRefLegs] = useState(4);
  const [showSettings, setShowSettings] = useState(false);

  const [powerBase, setPowerBase] = useState(DEFAULT_POWER_BASE);
  const [flexBase, setFlexBase]   = useState(DEFAULT_FLEX_BASE);
  const [goblinPower, setGoblinPower] = useState(DEFAULT_GOBLIN_POWER);
  const [goblinFlex, setGoblinFlex]   = useState(DEFAULT_GOBLIN_FLEX);
  const [demonPower, setDemonPower]   = useState(DEFAULT_DEMON_POWER);
  const [demonFlex, setDemonFlex]     = useState(DEFAULT_DEMON_FLEX);

  const [tickets, setTickets] = useState(() => {
    try {
      const raw = localStorage.getItem(TICKET_LOG_KEY);
      if (!raw) return [];
      const p = JSON.parse(raw);
      return Array.isArray(p) ? p : [];
    } catch { return []; }
  });
  const [logDate, setLogDate] = useState(() => todayISODate());
  const [logSport, setLogSport] = useState("NBA");
  const [logTicketType, setLogTicketType] = useState("power");
  const [logLegs, setLogLegs] = useState(() => [newLogLeg(), newLogLeg()]);
  const [logFormResult, setLogFormResult] = useState("PENDING");
  const [logFormActualPayout, setLogFormActualPayout] = useState("");
  const [logSuccess, setLogSuccess] = useState(false);
  const [expandedLogIds, setExpandedLogIds] = useState({});
  const [editingLogId, setEditingLogId] = useState(null);
  const [editLogResult, setEditLogResult] = useState("PENDING");
  const [editLogPayout, setEditLogPayout] = useState("");
  const [filterSport, setFilterSport] = useState("All");
  const [filterResult, setFilterResult] = useState("All");
  const [filterDateRange, setFilterDateRange] = useState("all");

  useEffect(() => {
    try {
      const s = JSON.parse(localStorage.getItem(SETTINGS_KEY));
      if (!s) return;
      if (s.powerBase)   setPowerBase(s.powerBase);
      if (s.flexBase)    setFlexBase(s.flexBase);
      if (s.goblinPower) setGoblinPower(s.goblinPower);
      if (s.goblinFlex)  setGoblinFlex(s.goblinFlex);
      if (s.demonPower)  setDemonPower(s.demonPower);
      if (s.demonFlex)   setDemonFlex(s.demonFlex);
    } catch {}
  }, []);

  useEffect(() => {
    try { localStorage.setItem(TICKET_LOG_KEY, JSON.stringify(tickets)); } catch {}
  }, [tickets]);

  useEffect(() => {
    if (!logSuccess) return;
    const t = setTimeout(() => setLogSuccess(false), 2000);
    return () => clearTimeout(t);
  }, [logSuccess]);

  const mods = { goblinPower, goblinFlex, demonPower, demonFlex };
  const tables = { powerBase, flexBase };

  const result = useMemo(() => calcPayouts({
    legs,
    stake: Math.max(1, Number(stake) || 10),
    mode,
    exactPower: parseFloat(exactPower) || 0,
    exactFlex:  parseFloat(exactFlex) || 0,
    tables, mods,
  }), [legs, stake, mode, exactPower, exactFlex, powerBase, flexBase, goblinPower, goblinFlex, demonPower, demonFlex]);

  const logCalcResult = useMemo(() => calcPayouts({
    legs: logLegs.map(l => l.tier),
    stake: Math.max(1, Number(stake) || 10),
    mode,
    exactPower: parseFloat(exactPower) || 0,
    exactFlex: parseFloat(exactFlex) || 0,
    tables, mods,
  }), [logLegs, stake, mode, exactPower, exactFlex, powerBase, flexBase, goblinPower, goblinFlex, demonPower, demonFlex]);

  const logWinProb = useMemo(() => {
    const ps = logLegs.map(l => {
      const x = Number(l.hit_rate);
      if (Number.isFinite(x) && x > 0 && x <= 1) return x;
      return 0.52;
    });
    return ps.reduce((a, b) => a * b, 1);
  }, [logLegs]);

  const logEv = useMemo(() => {
    const st = Math.max(1, Number(stake) || 10);
    if (!logCalcResult) return { ev: 0, roiPct: 0 };
    const ev = r2(logWinProb * logCalcResult.powerWin - st);
    const roiPct = st > 0 ? r2((ev / st) * 100) : 0;
    return { ev, roiPct };
  }, [logWinProb, logCalcResult, stake]);

  const filteredTickets = useMemo(() => {
    const now = new Date();
    const cutoff = (days) => {
      const d = new Date(now);
      d.setDate(d.getDate() - days);
      return d.toISOString().slice(0, 10);
    };
    let minD = null;
    if (filterDateRange === "7") minD = cutoff(7);
    else if (filterDateRange === "30") minD = cutoff(30);
    let list = [...tickets];
    if (filterSport !== "All") list = list.filter(t => t.sport === filterSport);
    if (filterResult !== "All") list = list.filter(t => t.result === filterResult);
    if (minD) list = list.filter(t => (t.date || "") >= minD);
    list.sort((a, b) => String(b.logged_at || "").localeCompare(String(a.logged_at || "")));
    return list;
  }, [tickets, filterSport, filterResult, filterDateRange]);

  const dashStats = useMemo(() => {
    const total = filteredTickets.length;
    const won = filteredTickets.filter(t => t.result === "WON").length;
    const lost = filteredTickets.filter(t => t.result === "LOST").length;
    const wrDenom = won + lost;
    const winRate = wrDenom > 0 ? r2((won / wrDenom) * 100) : 0;
    const totalStaked = filteredTickets.reduce((s, t) => s + (Number(t.stake) || 0), 0);
    const net = filteredTickets.reduce((s, t) => {
      if (t.result === "PENDING") return s;
      const ap = t.actual_payout != null ? Number(t.actual_payout) : 0;
      return s + (ap - (Number(t.stake) || 0));
    }, 0);
    const roi = totalStaked > 0 ? r2((net / totalStaked) * 100) : 0;
    return { total, winRate, totalStaked: r2(totalStaked), net: r2(net), roi, won, lost };
  }, [filteredTickets]);

  const sportBreakdown = useMemo(() => {
    const by = {};
    for (const t of filteredTickets) {
      const sp = t.sport || "Mixed";
      if (!by[sp]) by[sp] = { n: 0, won: 0, lost: 0, net: 0 };
      by[sp].n++;
      if (t.result === "WON") by[sp].won++;
      if (t.result === "LOST") by[sp].lost++;
      if (t.result !== "PENDING") {
        const ap = t.actual_payout != null ? Number(t.actual_payout) : 0;
        by[sp].net += ap - (Number(t.stake) || 0);
      }
    }
    return Object.entries(by).map(([sport, v]) => {
      const wd = v.won + v.lost;
      const wr = wd > 0 ? r2((v.won / wd) * 100) : 0;
      return { sport, n: v.n, won: v.won, lost: v.lost, wr, net: r2(v.net) };
    });
  }, [filteredTickets]);

  const comboBestWorst = useMemo(() => {
    const map = {};
    for (const t of filteredTickets) {
      if (t.result !== "WON" && t.result !== "LOST") continue;
      for (const leg of t.legs || []) {
        const pl = (leg.player || "").trim();
        if (!pl) continue;
        const prop = leg.prop || "";
        const k = `${pl.toLowerCase()}|${prop}`;
        if (!map[k]) map[k] = { wins: 0, losses: 0, label: `${pl} · ${prop}` };
        if (t.result === "WON") map[k].wins++;
        else map[k].losses++;
      }
    }
    const combos = Object.values(map)
      .filter(c => c.wins + c.losses >= 3)
      .map(c => ({
        ...c,
        rate: (c.wins + c.losses) > 0 ? r2((c.wins / (c.wins + c.losses)) * 100) : 0,
        n: c.wins + c.losses,
      }));
    combos.sort((a, b) => b.rate - a.rate || b.n - a.n);
    const best = combos[0] || null;
    const worst = combos.length ? combos[combos.length - 1] : null;
    return { best, worst };
  }, [filteredTickets]);

  const logTicket = useCallback(() => {
    const st = Math.max(1, Number(stake) || 10);
    if (!logCalcResult) return;
    if (logLegs.length < 2 || logLegs.length > 6) return;
    const winProb = logWinProb;
    const evVal = r2(winProb * logCalcResult.powerWin - st);
    const roiVal = st > 0 ? r2((evVal / st) * 100) : 0;
    const ap = resolveActualPayout({
      result: logFormResult,
      ticketType: logTicketType,
      powerWin: logCalcResult.powerWin,
      flexWin: logCalcResult.flexWin,
      stake: st,
      payoutOverride: logFormActualPayout,
    });
    const entry = {
      id: genLogId(),
      date: logDate,
      sport: logSport,
      ticket_type: logTicketType,
      stake: st,
      result: logFormResult,
      actual_payout: ap,
      power_mult: logCalcResult.powerMult,
      power_win: logCalcResult.powerWin,
      flex_mult: logCalcResult.flexMult,
      flex_win: logCalcResult.flexWin,
      win_prob: r2(winProb),
      ev: evVal,
      roi: roiVal,
      legs: logLegs.map(l => {
        const hr = Number(l.hit_rate);
        const hitOk = Number.isFinite(hr) ? r2(Math.min(1, Math.max(0, hr))) : 0.52;
        let lineVal = null;
        if (l.line !== "" && l.line != null && !isNaN(parseFloat(l.line))) lineVal = r2(parseFloat(l.line));
        return {
          player: l.player,
          prop: l.prop,
          line: lineVal,
          direction: l.direction,
          tier: l.tier,
          hit_rate: hitOk,
          result: null,
        };
      }),
      logged_at: new Date().toISOString(),
    };
    setTickets(prev => [entry, ...prev]);
    setLogDate(todayISODate());
    setLogSport("NBA");
    setLogTicketType("power");
    setLogLegs([newLogLeg(), newLogLeg()]);
    setLogFormResult("PENDING");
    setLogFormActualPayout("");
    setLogSuccess(true);
  }, [logCalcResult, logLegs, stake, logDate, logSport, logTicketType, logFormResult, logFormActualPayout, logWinProb]);

  const logThisFromBuilder = useCallback(() => {
    setLogLegs(legs.map(tier => newLogLeg(tier)));
    setLogTicketType("power");
    setTab("logger");
  }, [legs]);

  const exportCsv = useCallback(() => {
    const headers = ["date", "sport", "ticket_type", "stake", "legs", "result", "payout", "net", "ev", "win_prob", "roi"];
    for (let i = 1; i <= 6; i++) {
      headers.push(`leg${i}_player`, `leg${i}_prop`, `leg${i}_line`, `leg${i}_dir`, `leg${i}_tier`);
    }
    const rows = filteredTickets.map(t => {
      const ap = t.actual_payout != null ? t.actual_payout : "";
      const net = t.result === "PENDING" ? "" : r2((Number(ap) || 0) - (Number(t.stake) || 0));
      const legStr = (t.legs || []).length;
      const cells = [t.date, t.sport, t.ticket_type, t.stake, legStr, t.result, ap, net, t.ev, t.win_prob, t.roi];
      for (let i = 0; i < 6; i++) {
        const L = (t.legs || [])[i];
        if (L) cells.push(L.player, L.prop, L.line != null ? L.line : "", L.direction, L.tier);
        else cells.push("", "", "", "", "");
      }
      return cells.map(c => {
        const s = String(c ?? "");
        if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
        return s;
      }).join(",");
    });
    const bom = "\uFEFF";
    const csv = bom + [headers.join(","), ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `po_ticket_log_${todayISODate()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [filteredTickets]);

  const saveEditLog = useCallback(() => {
    if (!editingLogId) return;
    setTickets(prev => prev.map(t => {
      if (t.id !== editingLogId) return t;
      const ap = resolveActualPayout({
        result: editLogResult,
        ticketType: t.ticket_type,
        powerWin: t.power_win,
        flexWin: t.flex_win,
        stake: t.stake,
        payoutOverride: editLogPayout,
      });
      return { ...t, result: editLogResult, actual_payout: ap };
    }));
    setEditingLogId(null);
  }, [editingLogId, editLogResult, editLogPayout]);

  const startEditLog = useCallback((t) => {
    setEditingLogId(t.id);
    setEditLogResult(t.result);
    setEditLogPayout(t.actual_payout != null ? String(t.actual_payout) : "");
    setExpandedLogIds(p => ({ ...p, [t.id]: true }));
  }, []);

  const deleteTicket = useCallback((id) => {
    setTickets(prev => prev.filter(t => t.id !== id));
    setExpandedLogIds(p => { const q = { ...p }; delete q[id]; return q; });
    setEditingLogId(e => (e === id ? null : e));
  }, []);

  const toggleExpandLog = useCallback((id) => {
    setExpandedLogIds(p => ({ ...p, [id]: !p[id] }));
  }, []);

  const badgeCounts = useMemo(() => {
    const c = { Standard: 0, Goblin: 0, Demon: 0 };
    legs.forEach(l => { const { kind } = classify(l); c[kind === "goblin" ? "Goblin" : kind === "demon" ? "Demon" : "Standard"]++; });
    return c;
  }, [legs]);

  const badgeStyle = {
    Goblin:   S.badge("rgba(168,85,247,.10)", "rgba(168,85,247,.25)", C.purple),
    Demon:    S.badge("rgba(239,68,68,.10)",  "rgba(239,68,68,.25)",  C.red),
    Standard: S.badge("rgba(59,130,246,.10)", "rgba(59,130,246,.25)", C.blue),
  };

  const tableRows = LEG_TYPES;

  return (
    <div style={S.page}>
      <style>{`select option { background: #18181c; } button:hover { opacity: 0.88; }`}</style>

      {/* HEADER */}
      <div style={S.header}>
        <div style={S.logo}>
          <div style={S.logoIcon}>🎯</div>
          <div>
            <div style={S.logoH1}>Payout Calculator</div>
            <div style={S.logoSub}>POWER · FLEX · STANDARD · GOBLIN · DEMON</div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontFamily: mono, fontSize: 11, color: C.muted }}>STAKE $</span>
          <input
            type="number" min={1} value={stake}
            onChange={e => setStake(e.target.value)}
            style={S.input}
          />
        </div>
      </div>

      {/* TABS */}
      <div style={S.tabs}>
        {[["builder","🏗 BUILDER"],["logger","📋 LOGGER"],["reference","📊 REFERENCE"]].map(([id,label]) => (
          <button key={id} style={S.tabBtn(tab === id)} onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>

      <div style={S.wrap}>
        {tab === "builder" && (
          <div style={S.grid2}>
            {/* LEFT — Leg Builder */}
            <div>
              <div style={S.secLabel}>TICKET · {legs.length} LEGS</div>

              {/* Settings Toggle */}
              <button
                onClick={() => setShowSettings(s => !s)}
                style={{ display: "inline-flex", alignItems: "center", gap: 6, background: C.surface2, border: `1px solid ${C.border2}`, color: C.muted, borderRadius: 8, padding: "7px 12px", cursor: "pointer", fontFamily: mono, fontSize: 11, marginBottom: 12, transition: "all .15s" }}
              >
                ⚙ Settings
              </button>

              {showSettings && (
                <div style={{ marginBottom: 16, border: `1px solid ${C.border}`, borderRadius: 12, background: C.surface2, padding: 14 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, marginBottom: 10 }}>
                    <span style={{ fontSize: 12, color: C.muted }}>Update payout tables / modifiers</span>
                    <div style={{ display: "flex", gap: 8 }}>
                      {["Reset defaults"].map(label => (
                        <button key={label} onClick={() => { setPowerBase(DEFAULT_POWER_BASE); setFlexBase(DEFAULT_FLEX_BASE); setGoblinPower(DEFAULT_GOBLIN_POWER); setGoblinFlex(DEFAULT_GOBLIN_FLEX); setDemonPower(DEFAULT_DEMON_POWER); setDemonFlex(DEFAULT_DEMON_FLEX); }}
                          style={{ background: C.surface3, border: `1px solid ${C.border2}`, color: C.text, borderRadius: 7, padding: "6px 12px", cursor: "pointer", fontFamily: mono, fontSize: 11 }}>
                          {label}
                        </button>
                      ))}
                    </div>
                  </div>
                  {/* Power base */}
                  <div style={{ fontFamily: mono, fontSize: 10, color: C.muted, letterSpacing: "1.5px", marginBottom: 8 }}>STANDARD POWER MULTIPLIERS</div>
                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
                    {[2,3,4,5,6].map(n => (
                      <div key={n} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span style={{ fontFamily: mono, fontSize: 11, color: C.muted, width: 40 }}>{n}-leg</span>
                        <input value={powerBase[n]} onChange={e => { const x = parseFloat(e.target.value); if (!isNaN(x)) setPowerBase(p => ({...p,[n]:x})); }}
                          style={{ width: 72, background: C.bg, color: C.text, border: `1px solid ${C.border2}`, borderRadius: 7, padding: "5px 8px", fontFamily: mono, fontSize: 12, outline: "none" }}/>
                      </div>
                    ))}
                  </div>
                  {/* Estimate modifiers */}
                  <div style={{ fontFamily: mono, fontSize: 10, color: C.muted, letterSpacing: "1.5px", marginBottom: 8 }}>ESTIMATE MODIFIERS (PER LEG)</div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                    {[["Goblin Power","goblinPower",goblinPower,setGoblinPower],["Goblin Flex","goblinFlex",goblinFlex,setGoblinFlex],["Demon Power","demonPower",demonPower,setDemonPower],["Demon Flex","demonFlex",demonFlex,setDemonFlex]].map(([label,key,obj,setter]) => (
                      <div key={key} style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 12px" }}>
                        <div style={{ fontFamily: mono, fontSize: 10, color: C.muted, marginBottom: 8 }}>{label}</div>
                        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                          {[1,2,3].map(dev => (
                            <div key={dev} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                              <span style={{ fontFamily: mono, fontSize: 11, color: C.muted, width: 16 }}>{dev}</span>
                              <input value={obj[dev]} onChange={e => { const x = parseFloat(e.target.value); if (!isNaN(x)) setter(p => ({...p,[dev]:x})); }}
                                style={{ width: 62, background: C.surface2, color: C.text, border: `1px solid ${C.border2}`, borderRadius: 6, padding: "4px 7px", fontFamily: mono, fontSize: 12, outline: "none" }}/>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Mode toggle */}
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
                <span style={{ fontFamily: mono, fontSize: 10, color: C.muted, letterSpacing: "1px" }}>MODE:</span>
                {["estimate","exact"].map(m => (
                  <button key={m} onClick={() => setMode(m)} style={S.smallBtn(mode === m)}>
                    {m === "estimate" ? "Estimate" : "Exact Override"}
                  </button>
                ))}
              </div>

              {mode === "exact" && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 14 }}>
                  {[["Power Mult", exactPower, setExactPower], ["Flex Mult", exactFlex, setExactFlex]].map(([label, val, set]) => (
                    <div key={label}>
                      <div style={{ fontFamily: mono, fontSize: 10, color: C.muted, marginBottom: 6 }}>{label.toUpperCase()}</div>
                      <input placeholder="e.g. 45" value={val} onChange={e => set(e.target.value)}
                        style={{ width: "100%", background: C.surface2, color: C.text, border: `1px solid ${C.border2}`, borderRadius: 8, padding: "8px 10px", fontFamily: mono, fontSize: 13, outline: "none" }}/>
                    </div>
                  ))}
                </div>
              )}

              {/* Legs */}
              {legs.map((leg, i) => (
                <div key={i} style={S.legRow(legColor(leg))}>
                  <span style={{ fontSize: 15, width: 22, textAlign: "center" }}>{legEmoji(leg)}</span>
                  <span style={S.legNum}>LEG {i+1}</span>
                  <select value={leg} onChange={e => setLegs(p => p.map((x,j) => j===i ? e.target.value : x))} style={S.legSel(legColor(leg))}>
                    {LEG_TYPES.map(t => <option key={t} value={t}>{legEmoji(t)} {t}</option>)}
                  </select>
                  <button style={S.rmBtn} onClick={() => { if (legs.length > 2) setLegs(p => p.filter((_,j) => j !== i)); }}>×</button>
                </div>
              ))}

              {legs.length < 6 && (
                <button style={S.addBtn} onClick={() => setLegs(p => [...p, "Standard"])}>+ ADD LEG</button>
              )}

              <div style={{ display: "flex", gap: 6, marginTop: 14, flexWrap: "wrap" }}>
                {Object.entries(badgeCounts).filter(([,v]) => v > 0).map(([k,v]) => (
                  <span key={k} style={badgeStyle[k]}>{v}× {k}</span>
                ))}
              </div>
            </div>

            {/* RIGHT — Results */}
            <div>
              {!result ? (
                <div style={{ color: C.dim, textAlign: "center", padding: "60px 20px" }}>
                  <div style={{ fontSize: 32, marginBottom: 10, opacity: .5 }}>📊</div>
                  <div style={{ fontFamily: mono, fontSize: 12 }}>Add at least 2 legs to see payouts</div>
                </div>
              ) : (
                <>
                  <div style={S.secLabel}>RESULTS · ${money(Math.max(1, Number(stake)||10))} STAKE</div>

                  {/* Power Card */}
                  <div style={S.card("rgba(59,130,246,.08)","rgba(59,130,246,.02)","rgba(59,130,246,.20)")}>
                    <div style={S.cardHead}>
                      <span style={S.tag("rgba(59,130,246,.18)", C.blue, "rgba(59,130,246,.28)")}>POWER PLAY</span>
                      <span style={S.cardSub}>All {result.n} correct to win</span>
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
                      <div style={S.metBox}><div style={S.metLabel}>MULTIPLIER</div><div style={S.metVal(C.blue)}>{result.powerMult}x</div></div>
                      <div style={S.metBox}><div style={S.metLabel}>WIN AMOUNT</div><div style={S.metVal(C.green)}>${money(result.powerWin)}</div></div>
                      <div style={S.metBox}>
                        <div style={S.metLabel}>TO WIN $100</div>
                        <div style={S.metVal(C.gold, 20)}>${money(result.breakeven)}</div>
                        <div style={S.metSub}>Breakeven {result.breakeven}%</div>
                      </div>
                    </div>
                    <div style={{ marginTop: 10, fontFamily: mono, fontSize: 10, color: C.muted }}>
                      Base: {result.basePower}x · Mod: {r2(result.pm)}x {mode === "exact" ? "(override)" : "(est)"}
                    </div>
                  </div>

                  {/* Flex Card */}
                  <div style={S.card("rgba(168,85,247,.08)","rgba(168,85,247,.02)","rgba(168,85,247,.20)")}>
                    <div style={S.cardHead}>
                      <span style={S.tag("rgba(168,85,247,.18)", C.purple, "rgba(168,85,247,.28)")}>FLEX PLAY</span>
                      <span style={S.cardSub}>Partial wins allowed</span>
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                      <div style={S.metBox}><div style={S.metLabel}>{result.n}/{result.n} CORRECT</div><div style={S.metVal(C.purple)}>{result.flexMult}x</div></div>
                      <div style={S.metBox}><div style={S.metLabel}>WIN AMOUNT</div><div style={S.metVal(C.green)}>${money(result.flexWin)}</div></div>
                    </div>
                    {result.partials.length > 0 && (
                      <div style={S.partWrap}>
                        <div style={{ fontFamily: mono, fontSize: 9, color: C.muted, letterSpacing: "2px", marginBottom: 8 }}>PARTIAL PAYOUTS</div>
                        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                          {result.partials.map(p => (
                            <div key={p.correct} style={S.partBox}>
                              <div style={{ color: C.purple, fontFamily: mono, fontSize: 11, marginBottom: 3 }}>{p.correct}/{result.n}</div>
                              <div style={{ fontFamily: syne, fontSize: 18, fontWeight: 700 }}>{p.adj}x</div>
                              <div style={{ color: C.green, fontFamily: mono, fontSize: 11, marginTop: 4 }}>${money(p.win)}</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    <div style={{ marginTop: 10, fontFamily: mono, fontSize: 10, color: C.muted }}>
                      Base top: {result.baseFlexTop}x · Mod: {r2(result.fm)}x {mode === "exact" ? "(override)" : "(est)"}
                    </div>
                  </div>

                  {result.pm !== 1 && (
                    <div style={S.modBar}>
                      <div style={{ fontFamily: mono, fontSize: 10, color: C.muted }}>
                        Composite mod — Power: {r2(result.pm)}x · Flex: {r2(result.fm)}x
                      </div>
                    </div>
                  )}

                  <button type="button" onClick={logThisFromBuilder} style={{ width: "100%", padding: "11px 16px", marginTop: 18, background: C.surface3, color: C.text, border: `1px solid ${C.border2}`, borderRadius: 10, cursor: "pointer", fontFamily: syne, fontSize: 13, fontWeight: 700, transition: "opacity .15s" }}>
                    📋 LOG THIS TICKET
                  </button>
                </>
              )}
            </div>
          </div>
        )}

        {tab === "logger" && (
          <div style={S.grid2}>
            <div>
              <div style={S.secLabel}>LOG · NEW TICKET</div>

              <div style={{ marginBottom: 12 }}>
                <div style={S.logFieldLbl}>DATE</div>
                <input type="date" value={logDate} onChange={e => setLogDate(e.target.value)}
                  style={{ ...S.input, width: "100%", maxWidth: 220 }} />
              </div>

              <div style={{ marginBottom: 12 }}>
                <div style={S.logFieldLbl}>STAKE ($)</div>
                <input type="number" min={1} value={stake} onChange={e => setStake(e.target.value)}
                  style={{ ...S.input, width: "100%", maxWidth: 200 }} />
              </div>

              <div style={{ marginBottom: 12 }}>
                <div style={S.logFieldLbl}>SPORT</div>
                <select value={logSport} onChange={e => setLogSport(e.target.value)} style={{ ...S.input, width: "100%", maxWidth: 220, cursor: "pointer" }}>
                  {LOGGER_SPORTS.map(s => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>

              <div style={{ marginBottom: 12 }}>
                <div style={S.logFieldLbl}>TICKET TYPE</div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {[["power", "Power Play"], ["flex", "Flex Play"]].map(([v, lab]) => (
                    <button key={v} type="button" onClick={() => setLogTicketType(v)} style={S.smallBtn(logTicketType === v)}>{lab}</button>
                  ))}
                </div>
              </div>

              <div style={{ marginBottom: 8 }}>
                <div style={S.logFieldLbl}>LEGS ({logLegs.length})</div>
                {logLegs.map((leg, i) => (
                  <div key={i} style={S.logRowFlex}>
                    <span style={{ ...S.legNum, width: 52 }}>LEG {i + 1}</span>
                    <div style={{ flex: "1 1 200px", minWidth: 160 }}>
                      <input placeholder="Player" value={leg.player} onChange={e => setLogLegs(p => p.map((x, j) => j === i ? { ...x, player: e.target.value } : x))} style={S.logPlayerInp} />
                    </div>
                    <select value={leg.prop} onChange={e => setLogLegs(p => p.map((x, j) => j === i ? { ...x, prop: e.target.value } : x))} style={{ ...S.logSelSm, flex: "1 1 100px", minWidth: 100 }}>
                      {LOGGER_PROP_TYPES.map(pt => <option key={pt} value={pt}>{pt}</option>)}
                    </select>
                    <input type="text" inputMode="decimal" placeholder="Line" value={leg.line} onChange={e => setLogLegs(p => p.map((x, j) => j === i ? { ...x, line: e.target.value } : x))} style={S.logLineInp} />
                    <div style={S.logDirWrap}>
                      {["OVER", "UNDER"].map(d => (
                        <button key={d} type="button" onClick={() => setLogLegs(p => p.map((x, j) => j === i ? { ...x, direction: d } : x))} style={S.smallBtn(leg.direction === d)}>{d}</button>
                      ))}
                    </div>
                    <select value={leg.tier} onChange={e => setLogLegs(p => p.map((x, j) => j === i ? { ...x, tier: e.target.value } : x))} style={{ ...S.logSelSm, flex: "0 1 130px", minWidth: 120 }}>
                      {LEG_TYPES.map(t => <option key={t} value={t}>{legEmoji(t)} {t}</option>)}
                    </select>
                    <input type="number" step="0.01" min={0} max={1} title="Hit rate" value={leg.hit_rate} onChange={e => setLogLegs(p => p.map((x, j) => j === i ? { ...x, hit_rate: e.target.value } : x))} style={S.logHitInp} />
                    <button type="button" style={S.rmBtn} onClick={() => { if (logLegs.length > 2) setLogLegs(p => p.filter((_, j) => j !== i)); }}>×</button>
                  </div>
                ))}
                {logLegs.length < 6 && (
                  <button type="button" style={S.addBtn} onClick={() => setLogLegs(p => [...p, newLogLeg()])}>+ ADD LEG</button>
                )}
              </div>

              <div style={S.logPreviewBox}>
                <div style={{ color: C.muted, fontSize: 10, letterSpacing: "1.5px", marginBottom: 6 }}>PAYOUT PREVIEW</div>
                {!logCalcResult ? (
                  <span style={{ color: C.dim }}>Add at least 2 legs</span>
                ) : (
                  <>
                    <div>Power Play: <span style={{ color: C.blue }}>{logCalcResult.powerMult}x</span> → win <span style={{ color: C.green }}>${money(logCalcResult.powerWin)}</span></div>
                    <div>Flex Play: <span style={{ color: C.purple }}>{logCalcResult.flexMult}x</span> → win <span style={{ color: C.green }}>${money(logCalcResult.flexWin)}</span></div>
                  </>
                )}
              </div>

              <div style={{ marginTop: 14 }}>
                <div style={S.logFieldLbl}>RESULT</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {LOGGER_RESULTS.map(r => (
                    <button key={r} type="button" onClick={() => setLogFormResult(r)} style={S.smallBtn(logFormResult === r)}>{r}</button>
                  ))}
                </div>
              </div>

              {logFormResult === "WON" && (
                <div style={{ marginTop: 10 }}>
                  <div style={S.logFieldLbl}>ACTUAL PAYOUT ($) — optional override</div>
                  <input value={logFormActualPayout} onChange={e => setLogFormActualPayout(e.target.value)} placeholder={`Default ${logTicketType === "flex" ? money(logCalcResult?.flexWin ?? 0) : money(logCalcResult?.powerWin ?? 0)}`}
                    style={{ ...S.input, width: "100%", maxWidth: 200 }} />
                </div>
              )}

              <div style={{ marginTop: 14, ...S.logPreviewBox }}>
                <div style={{ color: C.muted, fontSize: 10, letterSpacing: "1.5px", marginBottom: 8 }}>EV SUMMARY (POWER WIN)</div>
                <div>Win probability: <span style={{ color: C.text }}>{(logWinProb * 100).toFixed(2)}%</span></div>
                <div>Expected value: <span style={{ color: C.green }}>${money(logEv.ev)}</span></div>
                <div>ROI: <span style={{ color: C.gold }}>{logEv.roiPct}%</span></div>
              </div>

              <button type="button" onClick={logTicket} disabled={!logCalcResult} style={{ ...S.logBtnGreen, opacity: logCalcResult ? 1 : 0.45, cursor: logCalcResult ? "pointer" : "not-allowed" }}>
                LOG TICKET
              </button>
              <div style={S.logFlash(logSuccess)}>Ticket logged ✓</div>
            </div>

            <div>
              <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 14 }}>
                <div style={S.secLabel}>HISTORY · STATS</div>
                <button type="button" onClick={exportCsv} style={S.logExportBtn}>📤 EXPORT CSV</button>
              </div>

              <div style={S.logDashGrid}>
                <div style={S.metBox}><div style={S.metLabel}>TICKETS</div><div style={S.metVal(C.text, 22)}>{dashStats.total}</div></div>
                <div style={S.metBox}><div style={S.metLabel}>WIN RATE</div><div style={S.metVal(C.blue, 22)}>{dashStats.won + dashStats.lost > 0 ? `${dashStats.winRate}%` : "—"}</div><div style={S.metSub}>{dashStats.won}W / {dashStats.lost}L</div></div>
                <div style={S.metBox}><div style={S.metLabel}>TOTAL STAKED</div><div style={S.metVal(C.gold, 20)}>${money(dashStats.totalStaked)}</div></div>
                <div style={S.metBox}><div style={S.metLabel}>NET P&amp;L</div><div style={S.metVal(dashStats.net >= 0 ? C.green : C.red, 20)}>${money(dashStats.net)}</div></div>
                <div style={S.metBox}><div style={S.metLabel}>ROI</div><div style={S.metVal(C.purple, 22)}>{dashStats.totalStaked > 0 ? `${dashStats.roi}%` : "—"}</div></div>
              </div>

              {sportBreakdown.length > 0 && (
                <div style={{ marginBottom: 12 }}>
                  <div style={{ ...S.logFieldLbl, marginBottom: 6 }}>BY SPORT</div>
                  {sportBreakdown.map(row => (
                    <div key={row.sport} style={S.logSportRow}>
                      {row.sport}: {row.n} tickets · {row.won + row.lost > 0 ? `${row.wr}% WR` : "—"} · ${money(row.net)} net
                    </div>
                  ))}
                </div>
              )}

              <div style={{ marginBottom: 14 }}>
                <div style={{ ...S.logFieldLbl, marginBottom: 6 }}>LEG COMBOS (3+ DECIDED)</div>
                {comboBestWorst.best ? (
                  <div style={S.logComboRow}>
                    <span style={{ color: C.green }}>Most reliable:</span> {comboBestWorst.best.label} · {comboBestWorst.best.rate}% ({comboBestWorst.best.wins}W / {comboBestWorst.best.losses}L)
                  </div>
                ) : (
                  <div style={S.logComboRow}>Most reliable: —</div>
                )}
                {comboBestWorst.worst && comboBestWorst.worst !== comboBestWorst.best ? (
                  <div style={S.logComboRow}>
                    <span style={{ color: C.red }}>Least reliable:</span> {comboBestWorst.worst.label} · {comboBestWorst.worst.rate}% ({comboBestWorst.worst.wins}W / {comboBestWorst.worst.losses}L)
                  </div>
                ) : (
                  <div style={S.logComboRow}>Least reliable: —</div>
                )}
              </div>

              <div style={S.logFilterRow}>
                <span style={S.logFilterLbl}>SPORT</span>
                {["All", ...LOGGER_SPORTS].map(s => (
                  <button key={s} type="button" onClick={() => setFilterSport(s)} style={S.smallBtn(filterSport === s)}>{s}</button>
                ))}
              </div>
              <div style={S.logFilterRow}>
                <span style={S.logFilterLbl}>RESULT</span>
                {["All", "WON", "LOST", "PENDING"].map(s => (
                  <button key={s} type="button" onClick={() => setFilterResult(s)} style={S.smallBtn(filterResult === s)}>{s}</button>
                ))}
              </div>
              <div style={{ ...S.logFilterRow, marginBottom: 16 }}>
                <span style={S.logFilterLbl}>DATE</span>
                {[["7", "Last 7 days"], ["30", "Last 30 days"], ["all", "All time"]].map(([v, lab]) => (
                  <button key={v} type="button" onClick={() => setFilterDateRange(v)} style={S.smallBtn(filterDateRange === v)}>{lab}</button>
                ))}
              </div>

              {filteredTickets.length === 0 ? (
                <div style={{ color: C.dim, fontFamily: mono, fontSize: 12, padding: "24px 0", textAlign: "center" }}>No tickets match filters</div>
              ) : (
                filteredTickets.map(t => {
                  const rb = resultBadgeColors(t.result);
                  const exp = !!expandedLogIds[t.id];
                  const payoutLabel = t.result === "PENDING" ? "—" : `$${money(t.actual_payout != null ? t.actual_payout : 0)}`;
                  return (
                    <div key={t.id} style={S.logCard}>
                      <div role="button" tabIndex={0} onClick={() => toggleExpandLog(t.id)} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleExpandLog(t.id); } }} style={S.logCardHead}>
                        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, flex: 1 }}>
                          <span style={{ fontFamily: mono, fontSize: 12, color: C.text }}>{t.date}</span>
                          <span style={{ fontFamily: mono, fontSize: 11, color: C.muted }}>{t.sport}</span>
                          <span style={{ fontFamily: mono, fontSize: 11, color: C.gold }}>${money(t.stake)}</span>
                          <span style={S.badge(rb.bg, rb.border, rb.color)}>{t.result}</span>
                          <span style={{ fontFamily: syne, fontSize: 14, fontWeight: 700, color: C.green }}>{payoutLabel}</span>
                        </div>
                        <span style={{ fontFamily: mono, fontSize: 11, color: C.muted }}>{exp ? "▼" : "▶"}</span>
                      </div>
                      {exp && (
                        <div style={S.logCardBody}>
                          {(t.legs || []).map((L, li) => (
                            <div key={li} style={{ fontFamily: mono, fontSize: 11, color: C.text, marginBottom: 6 }}>
                              {L.player || "—"} · {L.prop} · {L.line != null ? L.line : "—"} · {L.direction} · {L.tier}
                            </div>
                          ))}
                          <div style={{ fontFamily: mono, fontSize: 10, color: C.muted, marginTop: 8 }}>
                            EV at log: ${money(t.ev)} · Win prob: {(Number(t.win_prob) * 100).toFixed(2)}% · ROI (est): {t.roi}%
                          </div>

                          {editingLogId === t.id ? (
                            <div style={{ marginTop: 12, paddingTop: 12, borderTop: `1px solid ${C.border}` }}>
                              <div style={S.logFieldLbl}>EDIT RESULT</div>
                              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
                                {LOGGER_RESULTS.map(r => (
                                  <button key={r} type="button" onClick={() => setEditLogResult(r)} style={S.smallBtn(editLogResult === r)}>{r}</button>
                                ))}
                              </div>
                              <div style={S.logFieldLbl}>ACTUAL PAYOUT ($)</div>
                              <input value={editLogPayout} onChange={e => setEditLogPayout(e.target.value)} style={{ ...S.input, width: "100%", maxWidth: 200, marginBottom: 8 }} />
                              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                                <button type="button" onClick={saveEditLog} style={S.smallBtn(true)}>Save</button>
                                <button type="button" onClick={() => setEditingLogId(null)} style={S.smallBtn(false)}>Cancel</button>
                              </div>
                            </div>
                          ) : (
                            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
                              <button type="button" onClick={e => { e.stopPropagation(); startEditLog(t); }} style={S.smallBtn(false)}>Edit result</button>
                              <button type="button" onClick={e => { e.stopPropagation(); deleteTicket(t.id); }} style={{ ...S.rmBtn, fontFamily: mono, fontSize: 11 }}>Delete</button>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}

        {/* REFERENCE TAB */}
        {tab === "reference" && (
          <div>
            <div style={S.secLabel}>REFERENCE · STANDARD PAYOUTS</div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 20, flexWrap: "wrap" }}>
              <span style={{ fontFamily: mono, fontSize: 10, color: C.muted, letterSpacing: "2px" }}>LEG COUNT:</span>
              {[2,3,4,5,6].map(n => (
                <button key={n} style={S.smallBtn(refLegs === n)} onClick={() => setRefLegs(n)}>{n}-LEG</button>
              ))}
            </div>
            <div style={{ overflowX: "auto" }}>
              <table style={S.table}>
                <thead>
                  <tr>
                    {["TYPE","POWER","WIN $"+(Math.max(1,Number(stake)||10))+" (PP)","TO WIN $100","FLEX","WIN $"+(Math.max(1,Number(stake)||10))+" (FLEX)"].map((h,i) => (
                      <th key={h} style={{ ...S.th, textAlign: i === 0 ? "left" : "center" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {tableRows.map((row, idx) => {
                    const p = calcPayouts({ legs: Array(refLegs).fill(row), stake: Math.max(1,Number(stake)||10), mode: "estimate", exactPower: 0, exactFlex: 0, tables, mods });
                    if (!p) return null;
                    const color = legColor(row);
                    return (
                      <tr key={row} style={{ background: idx%2===0 ? "rgba(255,255,255,.015)" : "transparent" }}>
                        <td style={{ ...S.td, color, fontFamily: mono, fontSize: 12, fontWeight: 500 }}>{legEmoji(row)} {row}</td>
                        <td style={{ ...S.td, textAlign: "center", color: C.blue, fontFamily: mono, fontWeight: 500 }}>{p.powerMult}x</td>
                        <td style={{ ...S.td, textAlign: "center", color: C.green, fontFamily: mono }}>${money(p.powerWin)}</td>
                        <td style={{ ...S.td, textAlign: "center", color: C.gold, fontFamily: mono }}>${money(p.breakeven)}</td>
                        <td style={{ ...S.td, textAlign: "center", color: C.purple, fontFamily: mono, fontWeight: 500 }}>{p.flexMult}x</td>
                        <td style={{ ...S.td, textAlign: "center", color: C.green, fontFamily: mono }}>${money(p.flexWin)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div style={{ marginTop: 32 }}>
              <div style={S.secLabel}>FLEX PARTIAL PAYOUTS — STANDARD</div>
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                {[2,3,4,5,6].map(n => (
                  <div key={n} style={S.flexCard}>
                    <div style={{ fontFamily: syne, color: C.blue, fontWeight: 700, marginBottom: 10, fontSize: 13 }}>{n}-Leg Flex</div>
                    {Object.entries(flexBase[n]||{}).sort((a,b)=>b[0]-a[0]).map(([k,v]) => (
                      <div key={k} style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, gap: 16 }}>
                        <span style={{ fontFamily: mono, color: C.muted, fontSize: 11 }}>{k}/{n}</span>
                        <span style={{ fontFamily: mono, color: C.text, fontWeight: 500, fontSize: 11 }}>{v}x</span>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
