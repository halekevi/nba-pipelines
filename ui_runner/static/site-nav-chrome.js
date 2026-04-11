/**
 * Shared site nav: slate date, local date/time, hamburger, theme, scroll-hide.
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
  // Backwards-compat: some page scripts call this directly.
  window.applyHeroSlateDate = applyHeroSlateDate;

  function formatLocalDateTime() {
    return new Date().toLocaleString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function initLocalDateTime() {
    const el = document.getElementById("nav-local-datetime");
    if (!el) return;
    const tick = () => {
      el.textContent = formatLocalDateTime();
    };
    tick();
    setInterval(tick, 1000);
  }

  window.ProporacleNav = {
    /** @deprecated Nav no longer shows pipeline last sync; kept so older inline callers do not throw. */
    updateLastSyncFromPipelineStatus: function () {},
    refreshNavSync: async function () {},
  };

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    document.documentElement.classList.toggle("light-theme", theme === "light");
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
    const nl = document.getElementById("nav-links");
    if (!ham || !mob) return;
    const syncNavLinksOpen = () => {
      if (!nl) return;
      nl.classList.toggle("open", ham.classList.contains("open"));
    };
    ham.addEventListener("click", () => {
      ham.classList.toggle("open");
      mob.classList.toggle("open");
      syncNavLinksOpen();
    });
    document.addEventListener("click", (e) => {
      if (!ham.contains(e.target) && !mob.contains(e.target)) {
        ham.classList.remove("open");
        mob.classList.remove("open");
        syncNavLinksOpen();
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
    initLocalDateTime();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
