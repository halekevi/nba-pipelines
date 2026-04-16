# PrizePicks Payout API Discovery (Read-Only)

Date: 2026-04-09  
Repo: `PropORACLE`

## Task 1 — Existing Step1 API usage

### Soccer fetch (`Soccer/scripts/step1_fetch_prizepicks_soccer.py`)

- Base URL:
  - `https://api.prizepicks.com/projections`
- Query params used:
  - `league_id`
  - `per_page`
  - `single_stat=true`
  - `in_game=false|true`
  - `game_mode=pickem`
- Headers:
  - `User-Agent`
  - `Accept: application/json`
  - `Referer: https://app.prizepicks.com/`
  - `Origin: https://app.prizepicks.com`
- Auth/session approach:
  - No explicit auth token, cookie, bearer, device id, or account session.
  - Anonymous board fetch only.

### NBA fetch (`NBA/scripts/step1_fetch_prizepicks_api.py`)

- Base URL:
  - `https://api.prizepicks.com/projections`
- Query params used:
  - `league_id`
  - `per_page`
  - `single_stat=true`
  - `in_game=false`
- Headers (effective request headers in `_api_get`):
  - `User-Agent`
  - `Accept`
  - `Referer`
  - `Origin`
- Auth/session approach:
  - Uses a `requests.Session` for browser-like behavior.
  - No explicit auth token/cookie in code.
  - Anonymous projections endpoint only.

## Task 2 — Slip/payout endpoint probes

Test setup:

- Method: `POST`
- Entry amount: `1`
- Minimal 2-leg payload with real projection IDs from NBA step1:
  - `11233679`, `11234142`
- Headers:
  - `User-Agent`, `Accept`, `Content-Type`, `Referer`, `Origin`

Payload used:

```json
{
  "data": {
    "type": "entry",
    "attributes": {
      "entry_amount": 1,
      "lineup_attributes": {
        "picks_attributes": [
          { "projection_id": "11233679", "position": "over" },
          { "projection_id": "11234142", "position": "over" }
        ]
      }
    }
  }
}
```

### Endpoint results

- `POST https://api.prizepicks.com/api/v1/entries/new` -> `403`
- `POST https://api.prizepicks.com/api/v1/slips/payout` -> `403`
- `POST https://api.prizepicks.com/api/v1/projections/payout` -> `403`
- `POST https://api.prizepicks.com/api/v1/picks/payout` -> `403`
- `POST https://api.prizepicks.com/entries/new` -> `403`
- `POST https://api.prizepicks.com/slips/payout` -> `403`
- `POST https://api.prizepicks.com/projections/payout` -> `403`
- `POST https://api.prizepicks.com/picks/payout` -> `403`

Response body pattern:

- PerimeterX anti-bot block payload (`appId: PXZNeitfzP`, captcha script URLs).
- No payout/multiplier field present in blocked responses.

## Task 3 — Working endpoint documentation status

### Working payout endpoint

- Not identified in this unauthenticated, non-browser probe context.
- All tested payout/slip endpoints were blocked by anti-bot (`403` + PerimeterX challenge).

### Required headers / auth

- Current step1 code only uses lightweight browser headers for anonymous projections.
- This is insufficient for payout/slip endpoints due to bot protection.
- In practice, payout discovery likely requires:
  - browser session context (cookies/challenge token),
  - anti-bot clearance,
  - possibly authenticated user session.

### Request/response structure

- Request payload template above is syntactically valid for discovery attempts.
- Multiplier response fields could not be observed due to pre-auth/bot block.

## Conclusion

- Projections data is accessible anonymously via `GET /projections`.
- Slip/payout APIs are currently blocked from this headless probe path.
- Next step for exact multiplier integration is to source a valid browser-authenticated API call pattern (without storing credentials in repo), then replay read-only payout requests using that session context.
