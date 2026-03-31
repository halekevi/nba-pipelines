"""
espn_prop_analysis.py
=====================
Pulls every NBA boxscore from the 2025-26 season via ESPN's public API,
computes per-game stats for every player, then cross-references against
your graded PropOracle prop data to produce true season-long hit rates.

Usage (from your PropOracle directory):
    python espn_prop_analysis.py

Outputs:
    player_prop_season_hitrates.csv   — full results table
    player_prop_hitrates.html         — standalone dashboard (open in browser)

Requirements: requests, pandas  (pip install requests pandas)
"""

import requests, json, time, os, glob, re
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
import pandas as pd

# ── CONFIG ────────────────────────────────────────────────────────────────────

# Path to repo + cache outputs
REPO_ROOT = Path(__file__).resolve().parent
GRADED_DIR = str(REPO_ROOT)
GRADED_GLOB = os.path.join(GRADED_DIR, "**", "*graded*.xlsx")

# ESPN season dates — 2025-26 regular season
SEASON_START = "20251022"   # Oct 22 2025
SEASON_END   = "20260413"   # Apr 13 2026 (approx end)

# Prop name mapping: PropOracle prop_norm → ESPN stat key
PROP_MAP = {
    "points":       "points",
    "rebounds":     "totalRebounds",
    "assists":      "assists",
    "steals":       "steals",
    "blocked shots":"blocks",
    "blocks":       "blocks",
    "3-pt made":    "threePointFieldGoalsMade",
    "ftm":          "freeThrowsMade",
    "free throws made": "freeThrowsMade",
    "pts+rebs":     ["points","totalRebounds"],
    "pts+asts":     ["points","assists"],
    "rebs+asts":    ["totalRebounds","assists"],
    "pr":           ["points","totalRebounds"],
    "pa":           ["points","assists"],
    "pts+rebs+asts":["points","totalRebounds","assists"],
    "fantasy":      "espnFPS",          # ESPN fantasy score
    "fantasy score":"espnFPS",
    "blks+stls":    ["blocks","steals"],
}

HEADERS = {"User-Agent": "Mozilla/5.0 (PropOracle research tool)"}

# ── STEP 1: Fetch all game IDs for the season ─────────────────────────────────

def get_game_ids(start: str, end: str) -> list[str]:
    """Return all ESPN NBA game IDs between two YYYYMMDD dates."""
    game_ids = []
    current = datetime.strptime(start, "%Y%m%d")
    end_dt  = datetime.strptime(end,   "%Y%m%d")

    print(f"Fetching scoreboard dates {start} → {end}...")
    while current <= end_dt:
        date_str = current.strftime("%Y%m%d")
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            data = r.json()
            for event in data.get("events", []):
                game_ids.append(event["id"])
        except Exception as e:
            print(f"  Warning: {date_str} failed — {e}")
        current += timedelta(days=1)
        time.sleep(0.1)   # be polite

    print(f"  Found {len(game_ids)} games.")
    return game_ids


# ── STEP 2: Fetch boxscore for a single game ──────────────────────────────────

def fetch_boxscore(game_id: str) -> list[dict]:
    """
    Returns a list of player-stat dicts for one game.
    ESPN boxscore structure:
      data["boxscore"]["players"] = list of team blocks
      each team block has:
        team_block["team"]["abbreviation"]
        team_block["statistics"] = list of stat-group dicts, each with:
          stat_group["keys"]    = list of stat name strings
          stat_group["athletes"] = list of player dicts with:
            player["athlete"]["displayName"]
            player["stats"] = list of string values matching keys
    """
    url = (f"https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/"
           f"summary?event={game_id}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        data = r.json()
    except Exception as e:
        print(f"  Boxscore {game_id} failed: {e}")
        return []

    # Game date from header
    try:
        game_date = data["header"]["competitions"][0]["date"][:10]
    except (KeyError, IndexError):
        game_date = "unknown"

    rows = []
    for team_block in data.get("boxscore", {}).get("players", []):
        team_abbr = team_block.get("team", {}).get("abbreviation", "")

        for stat_group in team_block.get("statistics", []):
            # keys is a flat list of stat name strings
            keys = stat_group.get("keys", [])
            if not keys:
                continue

            for player_entry in stat_group.get("athletes", []):
                name = player_entry.get("athlete", {}).get("displayName", "")
                stats_raw = player_entry.get("stats", [])

                # Skip DNP / empty rows
                if not stats_raw or (len(stats_raw) > 0 and stats_raw[0] == "DNP"):
                    continue

                # Build lookup dict: stat_name -> raw string value
                stat_dict = {}
                for k, v in zip(keys, stats_raw):
                    stat_dict[k] = str(v) if v is not None else "0"

                def g(key):
                    """Get a plain numeric stat."""
                    try:
                        v = stat_dict.get(key, "0")
                        return float(v) if v not in ("", "--", None) else 0.0
                    except (ValueError, TypeError):
                        return 0.0

                def gm(key):
                    """Get the MADE count from a 'made-attempted' combined stat like '8-15'."""
                    try:
                        return float(str(stat_dict.get(key, "0-0")).split("-")[0])
                    except (ValueError, AttributeError):
                        return 0.0

                def ga(key):
                    """Get the ATTEMPTED count from a 'made-attempted' combined stat."""
                    try:
                        parts = str(stat_dict.get(key, "0-0")).split("-")
                        return float(parts[1]) if len(parts) > 1 else 0.0
                    except (ValueError, AttributeError):
                        return 0.0

                pts  = g("points")
                reb  = g("rebounds")
                ast  = g("assists")
                stl  = g("steals")
                blk  = g("blocks")
                tov  = g("turnovers")
                fgm  = gm("fieldGoalsMade-fieldGoalsAttempted")
                fga  = ga("fieldGoalsMade-fieldGoalsAttempted")
                fg_miss = max(fga - fgm, 0)
                ftm  = gm("freeThrowsMade-freeThrowsAttempted")
                fta  = ga("freeThrowsMade-freeThrowsAttempted")
                ft_miss = max(fta - ftm, 0)
                tpm  = gm("threePointFieldGoalsMade-threePointFieldGoalsAttempted")

                # ESPN fantasy score formula
                fps = pts + reb + 1.4*ast + stl + 1.4*blk - 0.7*tov + fgm - 0.8*fg_miss + 0.25*ftm - 0.8*ft_miss

                rows.append({
                    "player":                    name,
                    "team":                      team_abbr,
                    "date":                      game_date,
                    "game_id":                   game_id,
                    "points":                    pts,
                    "totalRebounds":             reb,
                    "assists":                   ast,
                    "steals":                    stl,
                    "blocks":                    blk,
                    "threePointFieldGoalsMade":  tpm,
                    "freeThrowsMade":            ftm,
                    "espnFPS":                   round(fps, 2),
                })

    return rows


# ── STEP 3: Load graded prop data ─────────────────────────────────────────────

def load_graded_data(glob_pattern: str) -> pd.DataFrame:
    dfs = []
    files = glob.glob(glob_pattern, recursive=True)
    skipped = []
    print(f"\nLoading {len(files)} graded files...")

    for path in files:
        bn = os.path.basename(path)
        try:
            xl = pd.ExcelFile(path)
            sheets = xl.sheet_names

            # ── Format 1: combined_tickets / combined_slate_tickets (LEG_RESULTS sheet) ──
            if "LEG_RESULTS" in sheets:
                df = pd.read_excel(path, sheet_name="LEG_RESULTS")
                # Require minimum viable columns
                if not {"player","prop_norm","line","leg_result"}.issubset(df.columns):
                    skipped.append(f"{bn} (LEG_RESULTS missing key cols)")
                    continue
                # Extract date from filename — handles several naming patterns
                import re
                m = re.search(r'(\d{4}-\d{2}-\d{2})', bn)
                date = m.group(1) if m else "unknown"
                df["date"] = date
                df["prop_norm"] = df["prop_norm"].str.lower().str.strip()
                df["sport"]  = df.get("sport",  pd.Series("NBA", index=df.index))
                df["dir"]    = df.get("dir",     pd.Series("OVER", index=df.index))
                df["actual"] = df.get("actual",  pd.Series(float("nan"), index=df.index))
                df["team"]   = df.get("team",    pd.Series("UNK", index=df.index))
                dfs.append(df[["date","player","team","sport","prop_norm",
                                "line","dir","actual","leg_result"]])

            # ── Format 2: nba_graded / cbb_graded (Box Raw sheet) ──
            elif "Box Raw" in sheets:
                df = pd.read_excel(path, sheet_name="Box Raw")
                # Normalise column names
                df.columns = df.columns.str.strip()
                sport = "NBA" if "nba" in bn.lower() else "CBB"
                m = re.search(r'(\d{4}-\d{2}-\d{2})', bn)
                date = m.group(1) if m else "unknown"

                # result column — try 'result' first
                result_col = "result" if "result" in df.columns else None
                if result_col is None:
                    skipped.append(f"{bn} (no result column)")
                    continue

                df = df[df[result_col].isin(["HIT","MISS"])].copy()
                df["date"]  = date
                df["sport"] = sport

                # Rename to standard names
                rename = {}
                if "prop_type_norm" in df.columns: rename["prop_type_norm"] = "prop_norm"
                if "bet_direction"  in df.columns: rename["bet_direction"]  = "dir"
                if result_col != "leg_result":     rename[result_col]       = "leg_result"
                df = df.rename(columns=rename)

                df["prop_norm"] = df["prop_norm"].str.lower().str.strip()
                df["actual"]    = pd.to_numeric(df.get("actual", float("nan")), errors="coerce")
                df["dir"]       = df.get("dir", "OVER")
                df["team"]      = df.get("team", "UNK")

                dfs.append(df[["date","player","team","sport","prop_norm",
                                "line","dir","actual","leg_result"]])

            else:
                skipped.append(f"{bn} (no recognised sheet)")

        except Exception as e:
            skipped.append(f"{bn} ({e})")

    if skipped:
        print(f"  Skipped {len(skipped)} files:")
        for s in skipped[:10]:
            print(f"    • {s}")
        if len(skipped) > 10:
            print(f"    … and {len(skipped)-10} more")

    if not dfs:
        raise RuntimeError("No graded data loaded — check GRADED_GLOB path")

    all_legs = pd.concat(dfs, ignore_index=True)
    graded   = all_legs[all_legs["leg_result"].isin(["HIT","MISS"])].copy()

    # Deduplicate — same player/prop/line/actual on same date = same event
    # (actual may be NaN for some formats — dedup on available cols)
    dedup_cols = ["date","player","prop_norm","line","dir"]
    if graded["actual"].notna().any():
        dedup_cols.append("actual")
    deduped = graded.drop_duplicates(subset=dedup_cols)

    print(f"  Loaded {len(deduped)} unique graded legs across "
          f"{deduped['date'].nunique()} days from {len(dfs)} files.")
    return deduped


# ── STEP 4: Compute actual value from boxscore for a prop ────────────────────

def compute_prop_value(row: dict, prop: str) -> float | None:
    mapping = PROP_MAP.get(prop)
    if mapping is None:
        return None
    if isinstance(mapping, list):
        return sum(row.get(k, 0) for k in mapping)
    return row.get(mapping)


# ── STEP 5: Cross-reference and compute hit rates ─────────────────────────────

def analyse(boxscore_df: pd.DataFrame, graded_df: pd.DataFrame) -> pd.DataFrame:
    """
    For every unique (player, prop_norm) combo in graded data,
    look up all their actual game values from boxscores,
    then compute hit rate against the lines that were offered.
    """
    results = []

    for (player, prop), g in graded_df[graded_df["sport"]=="NBA"].groupby(["player","prop_norm"]):
        # Get all season games for this player from boxscore
        player_games = boxscore_df[boxscore_df["player"] == player].copy()
        if player_games.empty:
            continue

        prop_values = player_games.apply(lambda r: compute_prop_value(r, prop), axis=1).dropna()
        if prop_values.empty:
            continue

        # Season stats
        season_n     = len(prop_values)
        season_avg   = prop_values.mean()
        season_std   = prop_values.std()

        # For each line offered in the tickets, compute hit rate across all season games
        lines = g["line"].unique()
        for line in lines:
            # OVER (most common direction in your data)
            hits_over  = (prop_values > line).sum()
            rate_over  = hits_over / season_n * 100

            # From tickets: what was the actual graded hit rate at this line?
            ticket_rows = g[g["line"] == line]
            ticket_n    = len(ticket_rows)
            ticket_hits = (ticket_rows["leg_result"] == "HIT").sum()
            ticket_rate = ticket_hits / ticket_n * 100 if ticket_n > 0 else None

            results.append({
                "player":        player,
                "team":          g["team"].iloc[0],
                "prop":          prop,
                "line":          line,
                "season_games":  season_n,
                "season_avg":    round(season_avg, 2),
                "season_std":    round(season_std, 2),
                "season_hit_rate_over": round(rate_over, 1),
                "cushion":       round(season_avg - line, 2),   # avg actual - line
                "ticket_n":      ticket_n,
                "ticket_hits":   int(ticket_hits),
                "ticket_hit_rate": round(ticket_rate, 1) if ticket_rate is not None else None,
            })

    df = pd.DataFrame(results)
    df = df.sort_values("season_hit_rate_over", ascending=False)
    return df


# ── STEP 6: Output ────────────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>PropOracle — Season Hit Rate Analysis</title>
<style>
body{{font-family:system-ui,sans-serif;background:#070a10;color:#e0e0e0;margin:0;padding:20px}}
h1{{color:#a78bfa;margin-bottom:4px}}
p{{color:#6b7280;margin-top:0}}
input{{background:#111827;border:1px solid #374151;color:#e0e0e0;padding:8px 12px;border-radius:6px;width:300px;font-size:14px;margin-bottom:12px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th{{background:#1f2937;color:#9ca3af;padding:8px 10px;text-align:left;border-bottom:2px solid #374151;cursor:pointer}}
td{{padding:7px 10px;border-bottom:1px solid #1f2937}}
tr:hover td{{background:#111827}}
.hot{{color:#34d399}}.warm{{color:#fbbf24}}.cold{{color:#f87171}}
.badge{{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;font-weight:600}}
</style>
</head>
<body>
<h1>PropOracle — Season Prop Hit Rate Analysis</h1>
<p>Season-long ESPN boxscore data cross-referenced with your graded PropOracle tickets</p>
<input type="text" id="search" placeholder="Filter by player..." oninput="filterTable()">
<table id="tbl">
<thead><tr>
<th onclick="sortTable(0)">Player</th>
<th onclick="sortTable(1)">Prop</th>
<th onclick="sortTable(2)">Line</th>
<th onclick="sortTable(3)">Season Games</th>
<th onclick="sortTable(4)">Season Avg</th>
<th onclick="sortTable(5)">Season Hit% (OVER)</th>
<th onclick="sortTable(6)">Cushion</th>
<th onclick="sortTable(7)">Ticket N</th>
<th onclick="sortTable(8)">Ticket Hit%</th>
</tr></thead>
<tbody id="tbody">{rows}</tbody>
</table>
<script>
function colorClass(v){{return v>=70?'hot':v>=45?'warm':'cold'}}
function filterTable(){{
  var q=document.getElementById('search').value.toLowerCase();
  document.querySelectorAll('#tbody tr').forEach(r=>{{
    r.style.display=r.cells[0].textContent.toLowerCase().includes(q)?'':'none';
  }});
}}
function sortTable(col){{
  var tbody=document.getElementById('tbody');
  var rows=Array.from(tbody.rows);
  var asc=tbody.dataset.lastCol==col&&tbody.dataset.dir=='asc';
  rows.sort((a,b)=>{{
    var av=parseFloat(a.cells[col].textContent)||a.cells[col].textContent;
    var bv=parseFloat(b.cells[col].textContent)||b.cells[col].textContent;
    return asc?(av>bv?1:-1):(av<bv?1:-1);
  }});
  rows.forEach(r=>tbody.appendChild(r));
  tbody.dataset.lastCol=col;
  tbody.dataset.dir=asc?'desc':'asc';
}}
</script>
</body></html>"""


def make_html_rows(df: pd.DataFrame) -> str:
    rows = []
    for _, r in df.iterrows():
        sr  = r["season_hit_rate_over"]
        tr  = r["ticket_hit_rate"]
        src = "hot" if sr>=70 else "warm" if sr>=45 else "cold"
        trc = ("hot" if tr>=70 else "warm" if tr>=45 else "cold") if tr is not None else "cold"
        rows.append(f"""<tr>
          <td><b>{r['player']}</b> <span style='color:#6b7280;font-size:11px'>{r['team']}</span></td>
          <td>{r['prop']}</td>
          <td>{r['line']}</td>
          <td>{r['season_games']}</td>
          <td>{r['season_avg']}</td>
          <td><span class='badge {src}'>{sr}%</span></td>
          <td style='color:{"#34d399" if r["cushion"]>0 else "#f87171"}'>{r["cushion"]:+.1f}</td>
          <td>{r['ticket_n']}</td>
          <td><span class='badge {trc}'>{tr if tr is not None else "—"}{"%" if tr is not None else ""}</span></td>
        </tr>""")
    return "\n".join(rows)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    # 1. Graded prop data
    graded_df = load_graded_data(GRADED_GLOB)

    # 2. Fetch all game IDs for the season
    game_ids = get_game_ids(SEASON_START, SEASON_END)

    # 3. Pull boxscores (with simple progress + caching)
    cache_path = os.path.join(GRADED_DIR, "NBA", "data", "cache", "espn_boxscores_cache.csv")
    if os.path.exists(cache_path):
        print(f"\nLoading cached boxscores from {cache_path}")
        boxscore_df = pd.read_csv(cache_path)
    else:
        all_rows = []
        print(f"\nFetching {len(game_ids)} boxscores (this takes ~10-15 min)...")
        for i, gid in enumerate(game_ids):
            rows = fetch_boxscore(gid)
            all_rows.extend(rows)
            if (i+1) % 50 == 0:
                print(f"  {i+1}/{len(game_ids)} games done ({len(all_rows)} player-game rows)")
            time.sleep(0.15)   # ~6-7 req/sec, well within ESPN limits

        boxscore_df = pd.DataFrame(all_rows)
        boxscore_df.to_csv(cache_path, index=False)
        print(f"  Cached to {cache_path}")

    print(f"\nBoxscore data: {len(boxscore_df)} player-game rows, "
          f"{boxscore_df['player'].nunique()} unique players, "
          f"{boxscore_df['date'].nunique()} game dates")

    # 4. Analyse
    print("\nCross-referencing with prop data...")
    results = analyse(boxscore_df, graded_df)
    print(f"  {len(results)} player-prop-line combos analysed")

    # 5. Save CSV
    csv_path = os.path.join(GRADED_DIR, "NBA", "data", "cache", "player_prop_season_hitrates.csv")
    results.to_csv(csv_path, index=False)
    print(f"  CSV saved → {csv_path}")

    # 6. Save HTML dashboard
    html_path = os.path.join(GRADED_DIR, "NBA", "data", "cache", "player_prop_hitrates.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(HTML_TEMPLATE.format(rows=make_html_rows(results)))
    print(f"  HTML dashboard saved → {html_path}")

    # 7. Print top 20
    print("\n=== TOP 20 — Season hit rate OVER (min 20 games) ===")
    top = results[results["season_games"] >= 20].head(20)
    for _, r in top.iterrows():
        print(f"  {r['season_hit_rate_over']:5.1f}%  {r['season_games']:3d}g  "
              f"{r['player']:<28} {r['prop']:<18} line={r['line']}  "
              f"avg={r['season_avg']}  cushion={r['cushion']:+.1f}")

    print("\n=== BOTTOM 20 — Season hit rate OVER (min 20 games) ===")
    bot = results[results["season_games"] >= 20].tail(20)
    for _, r in bot.iterrows():
        print(f"  {r['season_hit_rate_over']:5.1f}%  {r['season_games']:3d}g  "
              f"{r['player']:<28} {r['prop']:<18} line={r['line']}  "
              f"avg={r['season_avg']}  cushion={r['cushion']:+.1f}")


if __name__ == "__main__":
    main()
