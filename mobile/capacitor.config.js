/**
 * Loads the deployed Flask app in the WebView.
 *
 * Production: set PROPORACLE_SERVER_URL to your live https URL, then from `mobile/`:
 *   npm run sync:prod
 * (or: npm run sync / npm run sync:android after exporting the variable).
 *
 * Do not ship an APK that still points at a LAN dev IP (e.g. 10.0.0.x:5173) unless you
 * only use it on the same Wi‑Fi with the dev server running.
 *
 * PowerShell example:
 *   $env:PROPORACLE_SERVER_URL="https://your-app.up.railway.app"; npm run sync:android
 */
/** @type {import('@capacitor/cli').CapacitorConfig} */
const serverUrl =
  (process.env.PROPORACLE_SERVER_URL || "").trim() ||
  "https://YOUR-SERVICE.up.railway.app";
const isCleartextLocal = serverUrl.startsWith("http://");

const config = {
  appId: "com.proporacle.app",
  appName: "PropORACLE",
  webDir: "www",
  server: {
    url: serverUrl,
    androidScheme: isCleartextLocal ? "http" : "https",
    cleartext: isCleartextLocal,
  },
};

module.exports = config;
