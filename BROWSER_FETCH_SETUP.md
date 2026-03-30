# Browser Fetch Setup — Bot Detection Bypass

How PropOracle fetches live PrizePicks props without triggering DataDome/Cloudflare
bot detection. Follow this guide whenever adding a new sport pipeline.

---

## Why This Is Needed

PrizePicks uses **DataDome** (bot detection) and **Cloudflare** on their API.
Direct `requests` or `httpx` calls return 403 immediately. Even a plain Playwright
browser gets blocked in headless mode — DataDome fingerprints ~30 browser signals
to detect automation.

The solution: use a **visible Chromium browser** loaded with your **real Chrome
session cookies**, so PrizePicks sees a trusted, previously-verified human session.

---

## One-Time Setup (per machine)

### 1. Install dependencies

```powershell
py -3.14 -m pip install playwright playwright-stealth --break-system-packages
py -3.14 -m playwright install chromium
```

### 2. Log into PrizePicks in your real Chrome browser

Open Chrome → go to `app.prizepicks.com` → log in → navigate to any board.
This sets the DataDome trust cookies (`datadome`, `cf_clearance`, `_prizepicks_session`).

### 3. Copy your Chrome profile

Close Chrome **completely** (check Task Manager — kill any `chrome.exe`), then:

```powershell
cd C:\Users\halek\OneDrive\Desktop\PropORACLE\MLB
py -3.14 .\scripts\setup_prizepicks_profile.py
```

You should see:
```
✅ Default/Network/Cookies
✅ PrizePicks cookies found: ['datadome', 'cf_clearance', '_prizepicks_session', ...]
```

The key cookies to confirm are present:
| Cookie | Purpose |
|--------|---------|
| `datadome` | DataDome trust token — main bot bypass |
| `cf_clearance` | Cloudflare clearance token |
| `_prizepicks_session` | PrizePicks auth session |
| `remember_user_token` | Keeps you logged in |

---

## How It Works

```
Chrome Profile Copy (~/.pp_browser_profile)
         ↓
Playwright launches visible Chromium with your saved cookies
         ↓
PrizePicks sees: trusted IP + real cookies + non-headless browser
         ↓
DataDome passes the request → API fires normally
         ↓
Playwright intercepts api.prizepicks.com/projections response
         ↓
JSON payload parsed → CSV written → pipeline continues
```

**Why visible (not headless)?**
Headless Chromium has a different browser fingerprint that DataDome detects even
with stealth patches. `headless=False` with `--disable-blink-features=AutomationControlled`
passes as a real browser session.

**Why not just use requests with the cookies?**
DataDome also checks TLS fingerprint, HTTP/2 settings, and browser header order.
A raw `requests` session with copied cookies still fails. The browser is required.

---

## Adding a New Sport Pipeline

### 1. Copy `step1_fetch_prizepicks_mlb.py` as your template

The MLB step1 is the canonical reference implementation. Copy it and change:

```python
# Change this:
MLB_LEAGUE_ID = "2"
BOARD_URL     = f"https://app.prizepicks.com/board?league_id={MLB_LEAGUE_ID}"

# To your sport's league_id (see table below):
SPORT_LEAGUE_ID = "X"
BOARD_URL       = f"https://app.prizepicks.com/board?league_id={SPORT_LEAGUE_ID}"
```

Also update `TRACKABLE_PROPS` to the prop types for your sport, and the
`league_id=X` filter inside `handle_response()`.

### PrizePicks League IDs

| Sport | League ID |
|-------|-----------|
| NBA   | 7         |
| NFL   | 9         |
| MLB   | 2         |
| NHL   | 12        |
| WNBA  | 27        |
| CBB   | 8         |
| Soccer (generic) | 178 |
| PGA Golf | 15    |

> Verify these by opening DevTools on the PP board and watching the
> `/projections?league_id=X` call in the Network tab.

### 2. Update `CAPTURE_PATTERNS` if needed

The response handler filters to known API endpoints. If PP changes their API
shape, add the new URL pattern:

```python
CAPTURE_PATTERNS = [
    "api.prizepicks.com/projections",
    "api.prizepicks.com/boards",
    # add new patterns here if needed
]
```

### 3. Copy `setup_prizepicks_profile.py` to your sport's `scripts/` folder

Or just call the shared one from `MLB/scripts/` — it writes to `~/.pp_browser_profile`
which is shared across all sport pipelines.

### 4. Test in isolation before wiring into `run_pipeline.ps1`

```powershell
cd C:\Users\halek\OneDrive\Desktop\PropORACLE\{SPORT}
py -3.14 .\scripts\step1_fetch_prizepicks_{sport}.py --timeout 90 --retries 2 --output outputs\step1_{sport}_props.csv
```

Confirm you see:
```
✓ Captured XXXX projections from https://api.prizepicks.com/projections?league_id=X...
✅ BOARD_OK
```

### 5. Wire into `run_pipeline.ps1`

```powershell
if (-not $SkipFetch) {
    $ok = Run-Step-Job "SPORT Step 1 - Fetch PrizePicks" $SPORTDir `
        ".\scripts\step1_fetch_prizepicks_{sport}.py" `
        "--timeout 90 --retries 2 --output outputs\step1_{sport}_props.csv"
}
```

---

## Maintenance — When Bot Detection Returns

DataDome trust cookies expire periodically (typically every 2–4 weeks).
Symptoms: step1 completes but logs `No api.prizepicks.com calls detected at all`.

**Fix (2 minutes):**

1. Open Chrome → visit `app.prizepicks.com` → click around any board page
2. Close Chrome completely
3. Run:
   ```powershell
   py -3.14 .\scripts\setup_prizepicks_profile.py
   ```
4. Confirm `datadome` and `cf_clearance` appear in the cookie list
5. Re-run your pipeline

You do **not** need to push anything — `~/.pp_browser_profile` is local only.

---

## Fallback: `--from-file`

If the browser intercept fails entirely (e.g. PP rotates their API structure),
all step1 scripts accept a `--from-file` flag to load a raw JSON payload:

```powershell
# 1. Open DevTools on app.prizepicks.com/board?league_id=2
# 2. Network tab → find the /projections call → right-click → Save as HAR
#    OR: Response tab → copy JSON → save to payload.json
# 3. Run:
py -3.14 .\scripts\step1_fetch_prizepicks_mlb.py --from-file payload.json --output outputs\step1_mlb_props.csv
```

This is the manual escape hatch when everything else fails.

---

## `capture_entries.py` — My Entries / ticket harvest

This script (`capture_entries.py` at the repo root) also talks to PrizePicks. It **defaults to the same trusted profile** as step1:

- **If** `~/.pp_browser_profile` exists (from `setup_prizepicks_profile.py`) **with** `Default/Network/Cookies` or `Default/Cookies`, that directory is used automatically.
- **Otherwise** it falls back to `./browser_session` (older behaviour).

Run (visible browser, **not** headless):

```powershell
cd C:\Users\halek\OneDrive\Desktop\PropORACLE
py -3.14 -u capture_entries.py
```

**Press & Hold / bot screen:** complete the challenge in the window, then **click or press a key** once when the script says it is waiting for manual interaction (up to **7 minutes** by default). If you skip that step, DataDome cookies may not attach and API calls can return **403**.

If challenges keep appearing: log into PP in real Chrome, close Chrome, re-run `setup_prizepicks_profile.py`, then run `capture_entries.py` again.

**API 401 on “settled pages” / “detail fetch 0 successful”:** the harvester uses `page.request` (same cookies as the open tab), not a bare `context.request`, so pagination and `/v1/entries/{id}` / prediction calls stay authenticated after you log in.

**Still seeing Press & Hold / DataDome often:** `capture_entries.py` now (1) tries **system Google Chrome** before Playwright’s bundled Chromium, (2) drops Playwright’s default **`--enable-automation`** flag, (3) applies **playwright-stealth** on every new tab if installed, (4) does a **board warm-up** (`--warmup-league-id`, default NBA `7`) before hammering the entries API. Install stealth: `py -3.14 -m pip install playwright-stealth`. Use real Chrome + refreshed `setup_prizepicks_profile.py` cookies when challenges never clear.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `No api.prizepicks.com calls detected` | DataDome blocking / cookies expired | Re-run `setup_prizepicks_profile.py` after visiting PP in Chrome |
| `Cookies file missing from copied profile` | Chrome stores cookies at `Default/Network/Cookies` (not `Default/Cookies`) | Already handled in `setup_prizepicks_profile.py` — re-run it |
| Bot detection popup appears | Session expired or DataDome challenge triggered | Click through once in the browser, then re-copy profile |
| `Executable doesn't exist` Playwright error | Playwright updated, browser binary missing | Run `py -3.14 -m playwright install chromium` |
| `league_id=X` skipped in logs | PP changed the URL structure for that sport | Check Network tab in DevTools for new URL pattern, update `CAPTURE_PATTERNS` |
| Step1 works but rows=0 after prop filter | New prop type names from PP | Add to `TRACKABLE_PROPS` set and `PROP_NORM_MAP` in step2 |
