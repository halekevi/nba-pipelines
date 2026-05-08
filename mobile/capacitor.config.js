/**
 * Capacitor shell for PropORACLE.
 *
 * Bundled mode (default): do **not** set `server.url`. Capacitor loads `webDir` (`www/`) from the
 * APK — same idea as deleting/commenting a static `server: { url: ... }` block in older configs.
 *
 * Default (no PROPORACLE_SERVER_URL): the WebView loads **bundled files** from `webDir` (`www/`)
 * inside the APK — no Railway, no LAN dev server, no "mobile web" remote host.
 *
 * Optional remote UI: set PROPORACLE_SERVER_URL before sync (https Railway or http LAN), then:
 *   npm run sync:remote
 * or
 *   npm run sync:android
 *
 * PowerShell (remote only):
 *   $env:PROPORACLE_SERVER_URL="https://your-app.up.railway.app"; npm run sync:android
 */
const config = {
  appId: "com.proporacle.app",
  appName: "PropORACLE",
  webDir: "www",
  // Force bundled APK assets only. Never load remote PROPORACLE_SERVER_URL.
  // This prevents accidental "web form" rendering from stale env vars.
  server: {
    androidScheme: "https"
  },
};

module.exports = config;
