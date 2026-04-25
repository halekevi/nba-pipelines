# PropORACLE Mobile (Capacitor)

This folder contains the Android wrapper for the PropORACLE Flask UI.

## Mainline → what the app actually runs

The Play Store / sideload **APK is only a WebView shell**. At runtime it loads **`server.url`** from `capacitor.config.js` (set when you run `npm run sync` / `sync:android` with `PROPORACLE_SERVER_URL`).

| Change on `main` | Reaches the app when… |
|------------------|-------------------------|
| Flask templates, `ui_runner/static/*.css`, JSON under `ui_runner/templates/`, Python routes | **No new APK needed.** Merge to `main` → Railway (or your host) deploys that commit → the WebView loads the updated site on next open or refresh. Confirm Railway is wired to **`main`** and deploy succeeds (see repo root `railway.toml`). |
| `mobile/capacitor.config.js`, Gradle, `MainActivity`, native plugins, or **changing the production base URL** | **New `npm run sync:android` + rebuild/install** so `capacitor.config.json` and native projects pick up the change; ship a new APK/AAB if you distribute binaries. |

**Operational checklist after merging UI work to `main`:** (1) Push `main`. (2) Wait for the web deploy (Railway) to finish. (3) Force-close the app or pull-to-refresh if the WebView cached an old asset; templates already use `?v=…` on many stylesheets to bust cache.

## Local Development & Deployment

### 1. Sync the Configuration
Before building the app, you must sync the server URL so the mobile app knows where to load the UI from. Run these commands from the `mobile` folder.

*   **For Physical Device (on LAN):**
    ```sh
    npm run sync:dev
    ```
    *(Note: This is currently pinned to `http://10.0.0.207:5173`. If your IP changes, see "Custom URL" below.)*

*   **For Android Emulator:**
    ```sh
    npm run sync:emulator
    ```
    *(Uses `http://10.0.2.2:5173` to point to your PC's localhost.)*

*   **For a Custom URL:**
    ```sh
    npm run sync:url --url=http://YOUR_IP:5173
    ```

*   **For production (Railway — recommended for a build that works on cellular):**
    ```powershell
    $env:PROPORACLE_SERVER_URL="https://YOUR-ACTUAL-APP.up.railway.app"
    npm run sync:prod
    ```
    `sync:prod` requires a non-empty `https://` URL and refuses the `YOUR-SERVICE` placeholder. You can use `npm run sync:android` instead if the variable is already set.

### 2. Reinstall the App
If you change the URL or apply Java fixes, you should perform a clean reinstall:

1.  **Uninstall** the existing app from your phone/emulator (long-press icon -> Uninstall).
2.  **Sync** (as shown in Step 1).
3.  **Run** from Android Studio (Green Play button) or via command line:
    ```sh
    npx cap run android
    ```

## Troubleshooting

### Repair: `Webpage not available` / `ERR_CONNECTION_ABORTED` (LAN IP in the error)

The shell was **synced for local dev** (e.g. `http://10.0.0.207:5173`). The phone cannot reach your PC (different network, PC off, IP changed, or port blocked), so the WebView shows “Webpage not available.”

1. **Set the live server URL** — Use your real Railway hostname (not `YOUR-SERVICE`):
   - Edit `mobile/capacitor.config.js` if you want the default when no env var is set, **or** (recommended) only set the env var when syncing so secrets stay out of git.
2. **Sync the native project** (from `mobile/`):
    ```powershell
    $env:PROPORACLE_SERVER_URL="https://YOUR-ACTUAL-APP.up.railway.app"
    npm run sync:prod
    ```
    (`npm run sync` or `npm run sync:android` also works once the variable is set.)
3. **Rebuild in Android Studio:** **Build → Clean Project**, then **Run** (green arrow) to reinstall on the device. Uninstall the old app first if the URL changed.

**Recommendation:** Keep production devices on the **public `https://` Railway URL** so the app works on Wi‑Fi and cellular.

*   **Webpage not available / Connection Refused (when you *intend* LAN dev):**
    *   Ensure the Flask/Vite server is running on your PC (listening on `0.0.0.0:5173`).
    *   Verify your phone is on the same Wi-Fi network as the PC.
    *   Check Windows Firewall: Allow inbound TCP traffic on port `5173`.
*   **Stale URL:** If the app still tries to load an old IP (e.g., `192.168.1.5`), perform a full uninstall from the device and run the `sync` command again.

## Recent Fixes
*   **MainActivity.java:** Uses a `WebViewListener` to forcefully hide the hamburger menu (`.hamburger`, `ion-menu-button`) once the page loads, without breaking Capacitor's bridge.
*   **Resources:** Added missing `colors.xml` to resolve "Resource not found" errors in the Android Studio editor.
