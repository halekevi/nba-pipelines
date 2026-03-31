"""
proporacle_intelligence.py
=======================
Builds a full player intelligence layer from the ESPN boxscore cache.

Produces four analyses:
  1. Player consistency  — season avg, L5, L10, std dev, hit rates at common lines
  2. Team defense profile — which props spike/drop vs each opponent
  3. H2H history         — player stats specifically against upcoming opponent
  4. Intelligence CSV    — one row per player/prop with all signals merged

Usage:
    python proporacle_intelligence.py

Outputs (in your PropOracle directory):
    intel_player_consistency.csv
    intel_team_defense.csv
    intel_h2h.csv
    intel_dashboard.html   ← open this in Chrome
"""

import pandas as pd
import numpy as np
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
CACHE_PATH = str(REPO_ROOT / "NBA" / "data" / "cache" / "espn_boxscores_cache.csv")
OUT_DIR = str(REPO_ROOT / "NBA" / "data" / "cache")

# Props we care about and their display names
PROPS = {
    "points":                   "Points",
    "totalRebounds":            "Rebounds",
    "assists":                  "Assists",
    "steals":                   "Steals",
    "blocks":                   "Blocks",
    "threePointFieldGoalsMade": "3PM",
    "freeThrowsMade":           "FTM",
    "espnFPS":                  "Fantasy",
}

# Derived combo props
COMBOS = {
    "pts+rebs":      ["points", "totalRebounds"],
    "pts+asts":      ["points", "assists"],
    "rebs+asts":     ["totalRebounds", "assists"],
    "pts+rebs+asts": ["points", "totalRebounds", "assists"],
    "blks+stls":     ["blocks", "steals"],
    "pr":            ["points", "totalRebounds"],
    "pa":            ["points", "assists"],
}


# ── LOAD & ENRICH ─────────────────────────────────────────────────────────────

def load_and_enrich(path: str) -> pd.DataFrame:
    print(f"Loading cache: {path}")
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])

    # Derive opponent: for each game_id, find the other team
    game_teams = df.groupby("game_id")["team"].unique().reset_index()
    game_teams.columns = ["game_id", "teams_in_game"]

    team_map = {}
    for _, row in game_teams.iterrows():
        teams = list(row["teams_in_game"])
        if len(teams) == 2:
            team_map[(row["game_id"], teams[0])] = teams[1]
            team_map[(row["game_id"], teams[1])] = teams[0]

    df["opp_team"] = df.apply(
        lambda r: team_map.get((r["game_id"], r["team"]), "UNK"), axis=1
    )

    # Add all combo props as columns
    for combo, parts in COMBOS.items():
        df[combo] = df[parts].sum(axis=1)

    # Sort by player + date
    df = df.sort_values(["player", "date"]).reset_index(drop=True)

    print(f"  {len(df):,} player-game rows | {df['player'].nunique()} players | "
          f"{df['date'].nunique()} game dates | {df['opp_team'].nunique()} teams seen as opponents")
    return df


# ── 1. PLAYER CONSISTENCY ────────────────────────────────────────────────────

def build_player_consistency(df: pd.DataFrame) -> pd.DataFrame:
    all_props = list(PROPS.keys()) + list(COMBOS.keys())
    rows = []

    for player, pg in df.groupby("player"):
        pg = pg.sort_values("date")
        team = pg["team"].iloc[-1]   # most recent team
        gp   = len(pg)
        if gp < 3:
            continue

        for prop in all_props:
            if prop not in pg.columns:
                continue
            vals = pg[prop].dropna()
            if len(vals) < 3:
                continue

            season_avg = vals.mean()
            season_std = vals.std()
            season_med = vals.median()

            # L5 and L10 (most recent games)
            l5  = vals.tail(5).mean()  if len(vals) >= 5  else vals.mean()
            l10 = vals.tail(10).mean() if len(vals) >= 10 else vals.mean()

            # Consistency score: lower CV = more consistent
            cv = (season_std / season_avg * 100) if season_avg > 0 else 999

            # Hit rates at common lines around the season avg
            base = round(season_avg * 2) / 2   # round to nearest 0.5
            hit_rates = {}
            for offset in [-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0]:
                line = max(base + offset, 0.5)
                line = round(line * 2) / 2
                hr = (vals > line).mean() * 100
                hit_rates[f"hit_rate_at_{line}"] = round(hr, 1)

            rows.append({
                "player":     player,
                "team":       team,
                "prop":       prop,
                "prop_label": PROPS.get(prop, prop),
                "gp":         gp,
                "season_avg": round(season_avg, 2),
                "season_std": round(season_std, 2),
                "season_med": round(season_med, 2),
                "cv_pct":     round(cv, 1),       # coefficient of variation — lower = more consistent
                "l5_avg":     round(l5, 2),
                "l10_avg":    round(l10, 2),
                "l5_vs_season": round(l5 - season_avg, 2),    # positive = trending up
                "l10_vs_season": round(l10 - season_avg, 2),
                **hit_rates,
            })

    out = pd.DataFrame(rows)
    out = out.sort_values(["player", "prop"])
    print(f"  Player consistency: {len(out):,} player-prop rows")
    return out


# ── 2. TEAM DEFENSE PROFILE ──────────────────────────────────────────────────

def build_team_defense(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each opponent team, compute avg stats allowed per player.
    This tells you which teams give up more/less of each prop.
    """
    all_props = list(PROPS.keys()) + list(COMBOS.keys())
    rows = []

    # League-wide avg per player-game for normalisation
    league_avgs = {p: df[p].mean() for p in all_props if p in df.columns}

    for opp_team, og in df.groupby("opp_team"):
        if opp_team == "UNK":
            continue
        games_vs = og["game_id"].nunique()
        if games_vs < 5:
            continue

        for prop in all_props:
            if prop not in og.columns:
                continue
            vals = og[prop].dropna()
            if len(vals) < 10:
                continue

            avg_allowed   = vals.mean()
            league_avg    = league_avgs.get(prop, 1)
            vs_league     = avg_allowed - league_avg          # raw diff vs league avg
            vs_league_pct = (avg_allowed / league_avg - 1) * 100 if league_avg > 0 else 0

            rows.append({
                "opp_team":      opp_team,
                "prop":          prop,
                "prop_label":    PROPS.get(prop, prop),
                "games_sampled": games_vs,
                "avg_allowed":   round(avg_allowed, 2),
                "league_avg":    round(league_avg, 2),
                "vs_league":     round(vs_league, 2),
                "vs_league_pct": round(vs_league_pct, 1),
            })

    out = pd.DataFrame(rows)
    # Sort by vs_league_pct descending — most generous defenses first
    out = out.sort_values(["prop", "vs_league_pct"], ascending=[True, False])
    print(f"  Team defense: {len(out):,} team-prop rows")
    return out


# ── 3. H2H ───────────────────────────────────────────────────────────────────

def build_h2h(df: pd.DataFrame) -> pd.DataFrame:
    """
    For every player-opponent combo, compute their historical stats
    in those specific matchups.
    """
    all_props = list(PROPS.keys()) + list(COMBOS.keys())
    rows = []

    for (player, opp), pg in df.groupby(["player", "opp_team"]):
        if opp == "UNK":
            continue
        if len(pg) < 1:
            continue

        team = pg["team"].iloc[-1]

        for prop in all_props:
            if prop not in pg.columns:
                continue
            vals = pg[prop].dropna()
            if len(vals) < 1:
                continue

            rows.append({
                "player":    player,
                "team":      team,
                "opp_team":  opp,
                "prop":      prop,
                "prop_label": PROPS.get(prop, prop),
                "h2h_games": len(vals),
                "h2h_avg":   round(vals.mean(), 2),
                "h2h_std":   round(vals.std(), 2) if len(vals) > 1 else 0.0,
                "h2h_min":   round(vals.min(), 2),
                "h2h_max":   round(vals.max(), 2),
                "h2h_last":  round(vals.iloc[-1], 2),   # most recent game vs this opponent
            })

    out = pd.DataFrame(rows)
    out = out.sort_values(["player", "opp_team", "prop"])
    print(f"  H2H: {len(out):,} player-opponent-prop rows")
    return out


# ── 4. COMBINED LOOKUP TABLE ─────────────────────────────────────────────────

def build_combined(consistency: pd.DataFrame, defense: pd.DataFrame,
                   h2h: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge all signals into one flat lookup table.
    One row per player + prop + opponent — the full picture for any matchup.
    """
    # Start with consistency base
    base = consistency[["player","team","prop","prop_label","gp",
                         "season_avg","season_std","cv_pct",
                         "l5_avg","l10_avg","l5_vs_season","l10_vs_season"]].copy()

    # Get each player's upcoming opponents from last game dates
    # (just use all unique opponents they've faced for the lookup)
    player_opps = df[["player","opp_team"]].drop_duplicates()
    player_opps = player_opps[player_opps["opp_team"] != "UNK"]

    # Cross join player-prop with opponents
    combined = base.merge(player_opps, on="player", how="left")

    # Merge H2H
    h2h_slim = h2h[["player","opp_team","prop","h2h_games","h2h_avg","h2h_last"]].copy()
    combined = combined.merge(h2h_slim, on=["player","opp_team","prop"], how="left")

    # Merge defense profile
    def_slim = defense[["opp_team","prop","avg_allowed","vs_league_pct"]].copy()
    def_slim = def_slim.rename(columns={
        "avg_allowed":   "opp_avg_allowed",
        "vs_league_pct": "opp_vs_league_pct",
    })
    combined = combined.merge(def_slim, on=["opp_team","prop"], how="left")

    # Edge score: combines season hit rate trend + opp generosity
    combined["matchup_edge"] = (
        combined["l5_vs_season"].fillna(0) * 0.4 +   # recent form vs season
        combined["opp_vs_league_pct"].fillna(0) * 0.6  # opponent generosity
    ).round(2)

    combined = combined.sort_values("matchup_edge", ascending=False)
    print(f"  Combined: {len(combined):,} rows")
    return combined


# ── HTML DASHBOARD ────────────────────────────────────────────────────────────

def build_dashboard(consistency: pd.DataFrame, defense: pd.DataFrame,
                    h2h: pd.DataFrame) -> str:

    # Top consistent players per prop (min 20 games, lowest CV)
    top_consistent = (
        consistency[consistency["gp"] >= 20]
        .sort_values("cv_pct")
        .groupby("prop").head(10)
        .sort_values(["prop","cv_pct"])
    )

    # Top generous defenses per prop
    top_generous = (
        defense[defense["vs_league_pct"] > 0]
        .sort_values("vs_league_pct", ascending=False)
        .groupby("prop").head(5)
    )

    # Trending up (L5 > season avg by most)
    trending_up = (
        consistency[consistency["gp"] >= 15]
        .sort_values("l5_vs_season", ascending=False)
        .head(30)
    )

    trending_down = (
        consistency[consistency["gp"] >= 15]
        .sort_values("l5_vs_season")
        .head(30)
    )

    def df_to_table(df, cols, col_labels=None):
        if col_labels is None:
            col_labels = cols
        th = "".join(f"<th>{l}</th>" for l in col_labels)
        rows = []
        for _, r in df.iterrows():
            tds = []
            for c in cols:
                v = r.get(c, "")
                if isinstance(v, float):
                    v = f"{v:.2f}" if abs(v) < 1000 else f"{v:.0f}"
                tds.append(f"<td>{v}</td>")
            rows.append(f"<tr>{''.join(tds)}</tr>")
        return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(rows)}</tbody></table>"

    css = """
body{font-family:system-ui,sans-serif;background:#070a10;color:#e2e8f0;margin:0;padding:20px}
h1{color:#a78bfa;margin-bottom:4px;font-size:22px}
h2{color:#7c3aed;font-size:16px;margin:24px 0 8px}
h3{color:#8b5cf6;font-size:14px;margin:16px 0 6px}
p{color:#64748b;margin:0 0 12px;font-size:13px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px}
.card{background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:16px}
.search-row{display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap}
input{background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:7px 12px;
      border-radius:6px;font-size:13px;outline:none;min-width:180px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:#1e293b;color:#94a3b8;padding:7px 8px;text-align:left;
   border-bottom:2px solid #334155;white-space:nowrap}
td{padding:6px 8px;border-bottom:1px solid #1e293b;white-space:nowrap}
tr:hover td{background:#1e293b}
.up{color:#34d399}.down{color:#f87171}.neu{color:#94a3b8}
.tag{display:inline-block;padding:1px 6px;border-radius:8px;font-size:11px;font-weight:600}
.g{background:#064e3b;color:#34d399}.r{background:#450a0a;color:#f87171}
"""

    # Prop selector tabs
    props_for_tabs = ["points","totalRebounds","assists","steals",
                      "threePointFieldGoalsMade","espnFPS","pts+rebs+asts"]
    prop_labels    = ["Points","Rebounds","Assists","Steals","3PM","Fantasy","PRA"]

    # Build defense table HTML per prop
    def_tabs = ""
    for prop, label in zip(props_for_tabs, prop_labels):
        sub = defense[defense["prop"]==prop].head(30)
        if sub.empty:
            continue
        rows_html = ""
        for _, r in sub.iterrows():
            pct = r["vs_league_pct"]
            cls = "g" if pct > 5 else "r" if pct < -5 else ""
            badge = f'<span class="tag {cls}">{pct:+.1f}%</span>' if cls else f"{pct:+.1f}%"
            rows_html += (f"<tr><td>{r['opp_team']}</td>"
                          f"<td>{r['avg_allowed']:.2f}</td>"
                          f"<td>{r['league_avg']:.2f}</td>"
                          f"<td>{badge}</td>"
                          f"<td>{r['games_sampled']}</td></tr>")
        def_tabs += f"""
<div class="def-tab" id="def-{prop}" style="display:none">
<table><thead><tr><th>Opp Team</th><th>Avg Allowed</th><th>League Avg</th>
<th>vs League</th><th>Games</th></tr></thead>
<tbody>{rows_html}</tbody></table></div>"""

    # Build consistency table
    cons_rows = ""
    for _, r in top_consistent.iterrows():
        l5d = r["l5_vs_season"]
        cls = "up" if l5d > 1 else "down" if l5d < -1 else "neu"
        cons_rows += (f"<tr><td>{r['player']}</td><td>{r['prop_label']}</td>"
                      f"<td>{r['gp']}</td><td>{r['season_avg']:.2f}</td>"
                      f"<td>{r['l5_avg']:.2f}</td><td>{r['l10_avg']:.2f}</td>"
                      f"<td class='{cls}'>{l5d:+.2f}</td>"
                      f"<td>{r['cv_pct']:.1f}%</td></tr>")

    # Trending up rows
    trend_up_rows = ""
    for _, r in trending_up.iterrows():
        trend_up_rows += (f"<tr><td>{r['player']}</td><td>{r['prop_label']}</td>"
                          f"<td>{r['season_avg']:.2f}</td><td>{r['l5_avg']:.2f}</td>"
                          f"<td class='up'>+{r['l5_vs_season']:.2f}</td>"
                          f"<td>{r['l10_avg']:.2f}</td></tr>")

    trend_dn_rows = ""
    for _, r in trending_down.iterrows():
        trend_dn_rows += (f"<tr><td>{r['player']}</td><td>{r['prop_label']}</td>"
                          f"<td>{r['season_avg']:.2f}</td><td>{r['l5_avg']:.2f}</td>"
                          f"<td class='down'>{r['l5_vs_season']:.2f}</td>"
                          f"<td>{r['l10_avg']:.2f}</td></tr>")

    tab_btns = "".join(
        f'<button onclick="showDef(\'{p}\')" id="btn-{p}" '
        f'class="tab-btn">{l}</button>'
        for p, l in zip(props_for_tabs, prop_labels)
    )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>PropOracle Intelligence Dashboard</title>
<style>{css}
.tab-btn{{background:#1e293b;border:1px solid #334155;color:#94a3b8;
          padding:5px 12px;border-radius:6px;cursor:pointer;font-size:12px;margin-right:4px}}
.tab-btn.active{{background:#4c1d95;color:#ddd6fe;border-color:#7c3aed}}
.section{{margin-bottom:28px}}
</style></head>
<body>
<h1>PropOracle Intelligence Dashboard</h1>
<p>Season-long ESPN boxscore data · L5/L10/Season/H2H · Team defense profiles</p>

<div class="section">
<h2>Team Defense — Props Allowed vs League Average</h2>
<p>Green = gives up more than average (good for OVER). Red = gives up less (avoid or fade).</p>
<div style="margin-bottom:10px">{tab_btns}</div>
{def_tabs}
</div>

<div class="grid">
<div class="card section">
<h3>Most Consistent Players (lowest variance, min 20 GP)</h3>
<table><thead><tr><th>Player</th><th>Prop</th><th>GP</th><th>Season</th>
<th>L5</th><th>L10</th><th>L5 vs Season</th><th>CV%</th></tr></thead>
<tbody>{cons_rows}</tbody></table>
</div>

<div class="card section">
<h3>Trending Up — L5 avg above season avg</h3>
<table><thead><tr><th>Player</th><th>Prop</th><th>Season</th>
<th>L5</th><th>Δ</th><th>L10</th></tr></thead>
<tbody>{trend_up_rows}</tbody></table>
</div>
</div>

<div class="card section">
<h3>Trending Down — L5 avg below season avg</h3>
<table><thead><tr><th>Player</th><th>Prop</th><th>Season</th>
<th>L5</th><th>Δ</th><th>L10</th></tr></thead>
<tbody>{trend_dn_rows}</tbody></table>
</div>

<script>
function showDef(prop){{
  document.querySelectorAll('.def-tab').forEach(d=>d.style.display='none');
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.getElementById('def-'+prop).style.display='block';
  document.getElementById('btn-'+prop).classList.add('active');
}}
showDef('{props_for_tabs[0]}');
document.getElementById('btn-{props_for_tabs[0]}').classList.add('active');
</script>
</body></html>"""

    return html


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    df = load_and_enrich(CACHE_PATH)

    print("\nBuilding player consistency...")
    consistency = build_player_consistency(df)
    consistency.to_csv(os.path.join(OUT_DIR, "intel_player_consistency.csv"), index=False)

    print("Building team defense profiles...")
    defense = build_team_defense(df)
    defense.to_csv(os.path.join(OUT_DIR, "intel_team_defense.csv"), index=False)

    print("Building H2H history...")
    h2h = build_h2h(df)
    h2h.to_csv(os.path.join(OUT_DIR, "intel_h2h.csv"), index=False)

    print("Building combined lookup...")
    combined = build_combined(consistency, defense, h2h, df)
    combined.to_csv(os.path.join(OUT_DIR, "intel_combined.csv"), index=False)

    print("Building dashboard...")
    html = build_dashboard(consistency, defense, h2h)
    with open(os.path.join(OUT_DIR, "intel_dashboard.html"), "w", encoding="utf-8") as f:
        f.write(html)

    print("\n✓ Done. Files saved:")
    for fname in ["intel_player_consistency.csv","intel_team_defense.csv",
                  "intel_h2h.csv","intel_combined.csv","intel_dashboard.html"]:
        print(f"  {os.path.join(OUT_DIR, fname)}")

    # Print quick summary
    print("\n=== TOP 20 MOST CONSISTENT PLAYERS (points, min 20 GP) ===")
    pts = consistency[(consistency["prop"]=="points") & (consistency["gp"]>=20)]
    pts = pts.sort_values("cv_pct").head(20)
    for _, r in pts.iterrows():
        trend = f"L5={r['l5_avg']:.1f} ({r['l5_vs_season']:+.1f})"
        print(f"  CV={r['cv_pct']:5.1f}%  {r['player']:<28} avg={r['season_avg']:.1f}  {trend}")

    print("\n=== TOP 10 MOST GENEROUS DEFENSES vs POINTS ===")
    pts_def = defense[defense["prop"]=="points"].head(10)
    for _, r in pts_def.iterrows():
        print(f"  {r['opp_team']:6s}  avg allowed={r['avg_allowed']:.2f}  "
              f"vs league={r['vs_league_pct']:+.1f}%  ({r['games_sampled']}g sampled)")

    print("\n=== TOP 10 TIGHTEST DEFENSES vs POINTS ===")
    pts_def_tight = defense[defense["prop"]=="points"].tail(10).iloc[::-1]
    for _, r in pts_def_tight.iterrows():
        print(f"  {r['opp_team']:6s}  avg allowed={r['avg_allowed']:.2f}  "
              f"vs league={r['vs_league_pct']:+.1f}%  ({r['games_sampled']}g sampled)")


if __name__ == "__main__":
    main()
