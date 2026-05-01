# PropORACLE Mobile (Capacitor)

**Canonical Android project (this machine):** `H:\halek\ProfileFromC\Desktop\PropORACLE\mobile\android`  
Always run **`npm run sync:*`**, **`npx cap copy android`**, and Android Studio from the **`H:\halek\ProfileFromC\Desktop\PropORACLE\mobile`** folder so Gradle and `app/src/main/assets/public` stay in sync. If you also have a OneDrive clone, do **not** open `...\OneDrive\...\mobile\android` for builds — use the H: tree, or run **`npm run open:android:studio`** (runs `open-android-studio.ps1`, which `cd`s to that `mobile` path then `cap open android`). Edit `MobileDir` in `open-android-studio.ps1` if you relocate the repo.

Android (and optional iOS) **native shell** around a WebView. There are **two ways** the UI can load:

| Mode | What loads | When to use |
|------|------------|-------------|
| **Bundled (default)** | Files under `mobile/www/` copied into the APK | You want the app to **not** depend on Railway or LAN; you ship UI updates by rebuilding the APK after refreshing `www/`. |
| **Remote** | `PROPORACLE_SERVER_URL` (https Railway or http LAN) baked at `cap sync` | You want the WebView to always open the live site (same as mobile browser, but inside the shell). |

**Default repo config:** no `server.url` — the shell uses **bundled `www/`** only. That avoids Railway “Not Found” pages when no service exists at a placeholder hostname, and avoids LAN `ERR_CONNECTION_ABORTED` when the PC is unreachable.

## Bundled build (recommended for “mobile app files”)

1. Put your built static UI under `mobile/www/` (replace or extend the starter `index.html` as needed).
2. From the `mobile` folder:
   ```powershell
   npm run sync:bundle
   ```
   This clears `PROPORACLE_SERVER_URL` for this session and runs `cap sync android` so **`android/app/src/main/assets/capacitor.config.json` has no `server` block**.
3. Android Studio: **Build → Clean Project**, then **Run** to install.

To refresh the APK later, repeat after updating `www/`.

Equivalent CLI (from `mobile/`): `npx cap sync android` — same as `npm run sync:android` after `sync:bundle` clears the remote URL for that session.

If you **only** changed files under `www/` (no `capacitor.config.js` edits), you can copy assets into Android without a full sync: `npx cap copy android` (or `npm run copy:android`), then rebuild in Android Studio.

### Flask vs a truly offline `www/`

The main PropOracle **Flask** app runs on a server (e.g. Railway or your PC); **Python does not run inside the Android WebView.** To use **only** bundled files with **no** internet:

- Ship a **static** HTML/JS/CSS (or exported) UI into `mobile/www/`, then `npm run sync:bundle` and rebuild the APK.

If you need **live** Flask APIs without Railway, point the shell at your PC with **`server.url`** (LAN `http://192.168.x.x:5000` or similar), same Wi‑Fi as the phone, Flask listening on `0.0.0.0`, firewall open — use `npm run sync:url` / `sync:dev` with that URL (remote mode, not bundle).

## Remote build (live Railway / LAN)

Only when you explicitly want the WebView to load a URL:

```powershell
cd mobile
$env:PROPORACLE_SERVER_URL="https://your-real-app.up.railway.app"
npm run sync:remote
```

(`sync:remote` requires `https://`.) Then rebuild the APK. **Railway “Not Found”** means the hostname is wrong or no deployment is listening on that domain — fix the Railway project or the URL, not the Capacitor shell.

LAN dev (device on same Wi‑Fi as PC):

```powershell
npm run sync:dev
# or
npm run sync:url --url=http://YOUR_LAN_IP:5173
```

## What reaches users without a new APK

| Change | Bundled mode | Remote mode |
|--------|--------------|-------------|
| Flask templates / `ui_runner/static` on **Railway** | **No** — update `www/` and rebuild APK | **Yes** — redeploy web, user refreshes or reopens app |
| `mobile/www/` files | **Yes** — after `sync:bundle` + rebuild | N/A for UI (remote site owns UI) |
| `capacitor.config.js`, Gradle, native code | New sync + rebuild | New sync + rebuild |

## Reinstall checklist

1. Uninstall the old app if you switched **bundled ↔ remote**.
2. Run the appropriate `sync:*` command from `mobile/`.
3. **Build → Clean Project** → Run.

## Troubleshooting

* **`ERR_CONNECTION_ABORTED` to `http://10.x.x.x:5173`:** You synced **LAN remote** mode; PC unreachable or IP changed. Use **`npm run sync:bundle`** to return to bundled `www/`, or fix LAN and `sync:url`.

* **Railway “Not Found” / train page:** Remote URL points at a hostname with **no active Railway service**. Use **`sync:bundle`** to stop using Railway in the shell, or set `PROPORACLE_SERVER_URL` to the **exact** public URL shown in Railway for a running deployment, then `npm run sync:remote`.

* **Stale URL after switching modes:** Uninstall the app, then sync + reinstall.

## Recent fixes

* **MainActivity.java:** WebView listener hides duplicate chrome where needed.
* **colors.xml:** Resolves missing resource warnings in Android Studio.
