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
cd H:\halek\ProfileFromC\Desktop\PropORACLE\MLB
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

## Direct API (curl_cffi) — long-term contract

The NBA/MLB **direct API** path uses `curl_cffi` with `impersonate=chromeNNN` (default
`chrome120`, overridable via `PROPORACLE_CURL_IMPERSONATE`). PrizePicks/Cloudflare
often reject traffic when **TLS says one Chrome major** but **User-Agent / Sec-CH-UA
say another**.

**Permanent rules in this repo:**

1. **Alignment:** When `curl_cffi` is installed, `NBA/scripts/step1_fetch_prizepicks_api.py`
   only rotates profiles whose Chrome/Edg **major matches** the impersonation string.
   If you bump impersonation to a new major, you **must** add matching entries to
   `_BROWSER_PROFILES` or the fetcher raises at startup (fail-fast).
2. **Regression tests:** Run `pytest tests/test_prizepicks_fetch_client_hints.py`.
3. **MLB resilience:** `MLB/scripts/step1_fetch_prizepicks_mlb.py` keeps dated snapshots
   under `MLB/outputs/step1_snapshots/` and can **fall back** when the live API is blocked,
   so the rest of the pipeline still has rows. That does not replace a healthy fetch;
   it prevents a total blank slate.

PrizePicks can still return **403** from the network side; the engineering guarantee is
**consistent fingerprints**, **gentle retries**, and **fallback data** — not that every
request succeeds.

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
| CBB   | 20        |
| CFB   | 15        |
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
cd H:\halek\ProfileFromC\Desktop\PropORACLE\{SPORT}
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

## `scripts/capture_entries.py` — My Entries / ticket harvest

This script (`scripts/capture_entries.py`, run from the repo root) also talks to PrizePicks. It **defaults to the same trusted profile** as step1:

- **If** `~/.pp_browser_profile` exists (from `setup_prizepicks_profile.py`) **with** `Default/Network/Cookies` or `Default/Cookies`, that directory is used automatically.
- **Otherwise** it uses **`./local/browser_session`** (created on first use). If you still have a non-empty **`./browser_session`** at the repo root from an older setup, that path is used until you move or remove it.
- **Optional second profile** (e.g. parallel harvest): keep a separate Playwright user data dir at **`./local/browser_session_harvest2`** and pass `--session-dir` explicitly. Both `local/` and root-level `browser_session*` folders are gitignored.

Run (visible browser, **not** headless):

```powershell
cd H:\halek\ProfileFromC\Desktop\PropORACLE
py -3.14 -u scripts/capture_entries.py
```

**Press & Hold / bot screen:** complete the challenge in the window, then **click or press a key** once when the script says it is waiting for manual interaction (up to **7 minutes** by default). If you skip that step, DataDome cookies may not attach and API calls can return **403**.

If challenges keep appearing: log into PP in real Chrome, close Chrome, re-run `setup_prizepicks_profile.py`, then run `scripts/capture_entries.py` again.

**API 401 on “settled pages” / “detail fetch 0 successful”:** the harvester uses `page.request` (same cookies as the open tab), not a bare `context.request`, so pagination and `/v1/entries/{id}` / prediction calls stay authenticated after you log in.

**Still seeing Press & Hold / DataDome often:** `scripts/capture_entries.py` now (1) tries **system Google Chrome** before Playwright’s bundled Chromium, (2) drops Playwright’s default **`--enable-automation`** flag, (3) applies **playwright-stealth** on every new tab if installed, (4) does a **board warm-up** (`--warmup-league-id`, default NBA `7`) before hammering the entries API. Install stealth: `py -3.14 -m pip install playwright-stealth`. Use real Chrome + refreshed `setup_prizepicks_profile.py` cookies when challenges never clear.

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
