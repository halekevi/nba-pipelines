# NBA 1H / 1Q slate refresh (playoffs & late tips)

When the matchup panel shows the wrong opponent (e.g. SAS vs MIN instead of OKC), the usual cause is **stale `team`/`opp` on published slate JSON**, not defense CSVs. Matchup edge reads `opp` from the slate via `tonight_matchups()`.

## Date: use PrizePicks board date (ET)

Step1 filters by `--date` in **America/New_York**. Late playoff slates often tip on the **next calendar day** vs when you run the pipeline. If step1 reports `survived=0`, check `filtered_game_dates` in the log and re-run with that date.

## Period-only pipeline (no full NBA)

```powershell
powershell -File scripts\_run_nba_period_refresh.ps1 -Date 2026-05-30
```

Writes under `outputs/<date>/nba1h/` and `outputs/<date>/nba1q/`, and copies step8 clean xlsx to `Sports/NBA/`.

## Matchup edge + mobile (separate sport flags)

```bash
py -3 scripts/build_matchup_edge_json.py --sport nba1h
py -3 scripts/build_matchup_edge_json.py --sport nba1q
```

`build_matchup_edge_json.py` accepts **one** `--sport` per run (`nba1h nba1q` together is invalid).

`_resolve_slate_path` prefers `outputs/<date>/<sport>/step8_*` (newest date folder) before `slate_sport_*.json`.

## Publish UI slate

Either full combined write:

```bash
py -3 scripts/combined_slate_tickets.py --write-web --date <YYYY-MM-DD> ...
```

Or regenerate mobile bundle after updating `ui_runner/templates/slate_latest.json`:

```bash
py -3 scripts/generate_mobile_bundle.py
```

## Sanity checks

```bash
py -3 -c "import json; d=json.load(open('ui_runner/templates/nba1h_matchup_edge.json')); print(d['matchups'].get('SAS'))"

py -3 -c "import json; r=json.load(open('mobile/www/slate_sport_nba1h.json'))['rows']; print({(x['team'],x['opp']) for x in r if x['team']=='SAS'})"
```

Both should show **OKC**, not MIN.
