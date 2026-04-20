/**
 * Loads the deployed Flask app in the WebView. Set PROPORACLE_SERVER_URL to your
 * Railway public URL (https only), then run: npm run sync
 *
 * PowerShell: $env:PROPORACLE_SERVER_URL="https://your-app.up.railway.app"; npm run sync
 */
/** @type {import('@capacitor/cli').CapacitorConfig} */
const config = {
  appId: "com.proporacle.app",
  appName: "PropORACLE",
  webDir: "www",
  server: {
    url:
      (process.env.PROPORACLE_SERVER_URL || "").trim() ||
      "https://YOUR-SERVICE.up.railway.app",
    androidScheme: "https",
  },
};

module.exports = config;
