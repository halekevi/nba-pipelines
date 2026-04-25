# Chrome Debug Setup for PrizePicks Payout Engine

The payout engine script connects to an **already logged-in** Chrome session and reads multipliers without submitting entries.

## 1) Close Chrome fully

Close all Chrome windows first.

## 2) Launch Chrome with remote debugging

Windows command:

```powershell
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir=C:\chrome_debug
```

## 3) Open PrizePicks and log in

In that debug Chrome window:

- go to `https://app.prizepicks.com/`
- complete login / anti-bot checks
- keep that tab open

## 4) Run payout engine

From repo root:

```powershell
py -3.14 scripts/fetch_prizepicks_payouts.py
```

Optional flags:

- `--cdp-url http://localhost:9222`
- `--max-ui-combos 2000`
- `--min-est-ev 0.80`
- `--delay-sec 0.5`

## Safety

- Script is designed as **read-only**.
- It must **not click Submit/Enter/Buy**.
- It only builds/clears slips and reads payout multipliers from DOM/network.

---

## MLB step1 fetch over CDP (DataDome escape hatch)

Playwright can attach to Chrome you start yourself so you solve login or DataDome **once** in a real window; the fetch script then reuses that warm session (`connect_over_cdp`) instead of launching a cold browser.

### 1) Launch Chrome with your PrizePicks profile and a debug port

Close other Chrome instances that use the same profile first, then:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
    --remote-debugging-port=9222 `
    --user-data-dir="$env:USERPROFILE\.pp_browser_profile"
```

### 2) Confirm PrizePicks in that window

Open `https://app.prizepicks.com/`, confirm you are logged in and not stuck on a challenge.

### 3) Run MLB step1 attached to that session

From repo root (verifies `http://127.0.0.1:9222/json/version` before running):

```powershell
pwsh -NoProfile -File scripts\run_mlb_step1_chrome_debug.ps1
```

Or manually:

```powershell
cd H:\halek\ProfileFromC\Desktop\PropORACLE\MLB
py -3.14 .\scripts\step1_fetch_prizepicks_mlb.py --cdp http://127.0.0.1:9222 --output step1_mlb_props.csv
```

Optional: set `PROPORACLE_LOG_PLAYWRIGHT_UA=1` in the environment to print `navigator.userAgent` after the board loads (useful when comparing CDP vs launched Chromium). The MLB step1 script also reconfigures stdout to UTF-8 on Windows so emoji logs do not require `PYTHONIOENCODING` when you run it outside `run_pipeline.ps1`.

### How to read results

- **`[200]`** on `api.prizepicks.com` projections with `league_id=2` over CDP, but **scheduled runs still `403`** on the same endpoints, usually means the **saved profile session** (cookies / age / warmth) is the weak link, not the intercept logic. Refresh the profile periodically (re-login via `setup_prizepicks_profile.py` or a manual session in that user data dir).
- A practical check order: **CDP run first** (confirms end-to-end fetch), then **normal profile launch** (confirms whether recent anti-bot / UA / geo changes are enough for unattended runs).

### Troubleshooting: CDP works, scheduled profile path does not

Use this to interpret the next unattended run (e.g. morning pipeline without `--cdp`):

| What you see | Likely meaning |
| --- | --- |
| CDP run **`[200]`** on MLB projections | Script and intercept path are fine; session in the browser you attached was warm / trusted. |
| Same day, profile-only run **`[200]`** | DataDome / UA / geo / stealth / warm-up nav fixes are enough; scheduled runs are self-sufficient. |
| CDP **`[200]`**, profile-only **`[403]`** on API calls | Cookies in `~/.pp_browser_profile` are stale or scored cold; Playwright is not inheriting the same trust as your manual Chrome. |

**Refreshing stale profile cookies (when you get the 403 row above):**

1. Close all Chrome windows that use the PrizePicks profile.
2. Start Chrome with the **same** user data dir the pipeline uses, e.g.  
   `"C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="%USERPROFILE%\.pp_browser_profile"`
3. Open `https://app.prizepicks.com/`, complete login and any DataDome challenge, browse until the board loads normally.
4. Close Chrome cleanly so the profile writes to disk.
5. Run the pipeline again (or only MLB step1). The next launch should pick up fresh cookies.

Alternatively re-seed the profile with `setup_prizepicks_profile.py` if that is how you normally refresh the copy.

