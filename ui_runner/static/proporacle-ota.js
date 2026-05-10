/**
 * DIY OTA (Android): if ota-config.json enables a Railway baseUrl, compare
 * /api/mobile/bundle-version with localStorage and download bundle.zip via OtaBundlePlugin.
 */
(function () {
  var LS_KEY = "proporacle_ota_bundle_version";

  function waitOtaPlugin(ms) {
    return new Promise(function (resolve, reject) {
      var t0 = Date.now();
      var id = setInterval(function () {
        try {
          var C = window.Capacitor;
          if (C && C.isNativePlatform && C.isNativePlatform() && C.Plugins && C.Plugins.OtaBundle) {
            clearInterval(id);
            resolve();
            return;
          }
        } catch (e) {}
        if (Date.now() - t0 > ms) {
          clearInterval(id);
          reject(new Error("ota_cap_timeout"));
        }
      }, 40);
    });
  }

  function run() {
    try {
      var C = window.Capacitor;
      if (!C || !C.isNativePlatform || !C.isNativePlatform()) return;
      if (C.getPlatform() !== "android") return;
    } catch (e) {
      return;
    }
    waitOtaPlugin(4000)
      .then(function () {
        return fetch("ota-config.json", { cache: "no-store" });
      })
      .then(function (res) {
        if (!res || !res.ok) return null;
        return res.json();
      })
      .then(function (cfg) {
        if (!cfg || !cfg.enabled || !cfg.baseUrl) return null;
        var base = String(cfg.baseUrl).replace(/\/$/, "");
        var Ota = window.Capacitor.Plugins.OtaBundle;
        return Ota.reapplyIfPresent()
          .then(function (rr) {
            if (rr && rr.reapplied) return { stop: true };
            return fetch(base + "/api/mobile/bundle-version", { cache: "no-store" }).then(function (vres) {
              if (!vres.ok) return { stop: true };
              return vres.json().then(function (vj) {
                var remote = String((vj && vj.version) || "").trim();
                if (!remote) return { stop: true };
                var local = "";
                try {
                  local = String(localStorage.getItem(LS_KEY) || "").trim();
                } catch (e) {}
                if (local === remote) return { stop: true };
                var zipUrl = base + "/api/mobile/bundle.zip";
                return Ota.applyBundleFromUrl({ url: zipUrl }).then(function () {
                  try {
                    localStorage.setItem(LS_KEY, remote);
                  } catch (e) {}
                });
              });
            });
          })
          .then(function (x) {
            if (x && x.stop) return;
          });
      })
      .catch(function (e) {
        console.warn("[proporacle-ota]", e);
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
})();
