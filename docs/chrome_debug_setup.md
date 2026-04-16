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

