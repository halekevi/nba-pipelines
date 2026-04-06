/**
 * Shared site nav: slate date, last sync, clock, hamburger, theme, scroll-hide.
 * Exposes window.ProporacleNav.updateLastSyncFromPipelineStatus(d) for pages
 * that already fetch /api/pipeline/status (e.g. home slate cards).
 */
(function () {
  "use strict";

  function formatSlateDateDisplay(isoYmd) {
    let dt;
    if (isoYmd && typeof isoYmd === "string" && isoYmd.length >= 10) {
      const parts = isoYmd.slice(0, 10).split("-").map(Number);
      dt = new Date(parts[0], parts[1] - 1, parts[2]);
    } else {
      dt = new Date();
    }
    return dt.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  }

  function parsePipelineModified(s) {
    if (!s || typeof s !== "string") return null;
    const t = Date.parse(s.trim().replace(" ", "T"));
    return Number.isFinite(t) ? t : null;
  }

  function formatRelativeAgo(msAgo) {
    if (msAgo < 0) msAgo = 0;
    const sec = Math.floor(msAgo / 1000);
    if (sec < 45) return "just now";
    const min = Math.floor(sec / 60);
    if (min < 60) return min === 1 ? "1 min ago" : `${min} mins ago`;
    const hr = Math.floor(min / 60);
    if (hr < 48) return hr === 1 ? "1 hr ago" : `${hr} hrs ago`;
    const d = Math.floor(hr / 24);
    return d === 1 ? "1 day ago" : `${d} days ago`;
  }

  function updateNavLastSyncFromPipelineStatus(d) {
    const el = document.getElementById("nav-last-sync");
    if (!el) return;
    let best = 0;
    const sports = ["combined", "nba", "nba1h", "nba1q", "cbb", "wcbb", "nhl", "soccer", "mlb"];
    for (const k of sports) {
      const m = d && d[k] && d[k].slate && d[k].slate.modified;
      const t = parsePipelineModified(m);
      if (t > best) best = t;
    }
    if (best > 0) el.textContent = "Last sync: " + formatRelativeAgo(Date.now() - best);
    else el.textContent = "Last sync: —";
  }

  async function applyHeroSlateDate() {
    try {
      const res = await fetch("/api/slate-display-date", { cache: "no-store" });
      if (!res.ok) return;
      const j = await res.json();
      if (!j || !j.date) return;
      const el = document.getElementById("slate-date");
      if (el) el.textContent = formatSlateDateDisplay(j.date);
    } catch (e) {}
  }

  async function refreshNavSync() {
    try {
      const res = await fetch("/api/pipeline/status", { cache: "no-store" });
      if (!res.ok) return;
      updateNavLastSyncFromPipelineStatus(await res.json());
    } catch (e) {}
  }

  function initClock() {
    const tick = () => {
      const c = document.getElementById("clock");
      if (c) c.textContent = new Date().toLocaleTimeString();
    };
    tick();
    setInterval(tick, 1000);
  }

  window.ProporacleNav = {
    updateLastSyncFromPipelineStatus,
    refreshNavSync,
  };

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    if (document.body) document.body.classList.toggle("light-mode", theme === "light");
    try {
      localStorage.setItem("proporacle-theme", theme);
      localStorage.setItem("theme", theme);
    } catch (e) {}
    const moon = document.getElementById("theme-toggle")?.querySelector(".tt-moon");
    const sun = document.getElementById("theme-toggle")?.querySelector(".tt-sun");
    if (moon) moon.style.display = theme === "dark" ? "" : "none";
    if (sun) sun.style.display = theme === "dark" ? "none" : "";
  }

  window.applyTheme = applyTheme;
  window.toggleTheme = function () {
    const curr = document.documentElement.getAttribute("data-theme") || "dark";
    applyTheme(curr === "dark" ? "light" : "dark");
  };

  function bindHamburger() {
    const ham = document.getElementById("hamburger");
    const mob = document.getElementById("mobile-menu");
    if (!ham || !mob) return;
    ham.addEventListener("click", () => {
      ham.classList.toggle("open");
      mob.classList.toggle("open");
    });
    document.addEventListener("click", (e) => {
      if (!ham.contains(e.target) && !mob.contains(e.target)) {
        ham.classList.remove("open");
        mob.classList.remove("open");
      }
    });
  }

  function bindNavScrollHide() {
    const nav = document.querySelector(".snav");
    if (!nav) return;
    const mq = window.matchMedia("(max-width: 900px), (pointer: coarse)");
    let lastY = window.scrollY || 0;
    let ticking = false;
    const hideAfter = 140;
    const minDelta = 8;
    const update = () => {
      ticking = false;
      if (mq.matches) {
        nav.classList.remove("nav-hidden");
        lastY = window.scrollY || 0;
        return;
      }
      const y = window.scrollY || 0;
      const delta = y - lastY;
      if (y < hideAfter || delta < -minDelta) nav.classList.remove("nav-hidden");
      else if (delta > minDelta) nav.classList.add("nav-hidden");
      lastY = y;
    };
    window.addEventListener(
      "scroll",
      () => {
        if (!ticking) {
          ticking = true;
          window.requestAnimationFrame(update);
        }
      },
      { passive: true }
    );
    window.addEventListener("resize", update);
    update();
  }

  function init() {
    const saved = localStorage.getItem("proporacle-theme") || "dark";
    applyTheme(saved);
    bindHamburger();
    bindNavScrollHide();
    applyHeroSlateDate();
    initClock();
    refreshNavSync();
    setInterval(refreshNavSync, 60000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
