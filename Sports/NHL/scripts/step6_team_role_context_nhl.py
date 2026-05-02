"""
Step 6 — NHL Player Role & Line Context
Attaches:
- Player line context (PP unit, PP TOI share, line number)
- Position group (F/D/G)
- Scoring tier (ELITE / SECONDARY / DEPTH / SHUTDOWN / GOALIE)
- Power play context — using NHL Stats API skater splits

Usage:
    py step6_team_role_context_nhl.py --input outputs/step5_nhl_hit_rates.csv \
        --output outputs/step6_nhl_role_context.csv
"""

import argparse
import csv
import json
import time
import urllib.request
from datetime import datetime
try:
    from tqdm import tqdm as _tqdm
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "tqdm", "--break-system-packages", "-q"])
    from tqdm import tqdm as _tqdm

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
NHL_WEB = "https://api-web.nhle.com/v1"
NHL_API = "https://api.nhle.com/stats/rest/en"


def fetch_json(url: str) -> dict:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        return {}


def get_player_summary_single(nhl_id: str) -> dict:
    """Get player landing page summary for one numeric NHL player id."""
    pid = (nhl_id or "").strip()
    if not pid.isdigit():
        return {}
    url = f"{NHL_WEB}/player/{pid}/landing"
    data = fetch_json(url)
    if not data:
        return {}

    season_totals = data.get("seasonTotals", [])
    # Find most recent NHL regular season
    nhl_seasons = [s for s in season_totals if s.get("leagueAbbrev") == "NHL" and s.get("gameTypeId") == 2]
    current = nhl_seasons[-1] if nhl_seasons else {}

    pos = data.get("position", "")

    summary = {
        "position_code": pos,
        "current_team_id": data.get("currentTeamId", ""),
        "pp_goals": current.get("powerPlayGoals", 0) or 0,
        "pp_points": current.get("powerPlayPoints", 0) or 0,
        "es_goals": current.get("evGoals", 0) or 0,
        "es_points": current.get("evPoints", 0) or 0,
        "toi_per_game": current.get("avgToi", "") or "",
        "gp": current.get("gamesPlayed", 0) or 0,
        "goals_season": current.get("goals", 0) or 0,
        "assists_season": current.get("assists", 0) or 0,
        "points_season": current.get("points", 0) or 0,
        "plus_minus": current.get("plusMinus", 0) or 0,
    }
    return summary


def merge_player_summaries(summaries: list[dict]) -> dict:
    """Average numeric season fields; keep position/team from first successful summary."""
    if not summaries:
        return {}
    if len(summaries) == 1:
        return summaries[0]

    first = summaries[0]
    num_keys = [
        "pp_goals", "pp_points", "es_goals", "es_points", "gp",
        "goals_season", "assists_season", "points_season", "plus_minus",
    ]
    out: dict = {}
    for k in num_keys:
        vals = [int(s.get(k, 0) or 0) for s in summaries]
        out[k] = int(round(sum(vals) / len(vals)))

    mins = [toi_to_minutes(s.get("toi_per_game", "")) for s in summaries]
    mins = [m for m in mins if m > 0]
    if mins:
        am = sum(mins) / len(mins)
        mi = int(am)
        sec = int(round((am - mi) * 60))
        if sec >= 60:
            mi += sec // 60
            sec %= 60
        out["toi_per_game"] = f"{mi}:{sec:02d}"
    else:
        out["toi_per_game"] = first.get("toi_per_game", "") or ""

    out["position_code"] = next(
        (s.get("position_code", "") for s in summaries if s.get("position_code")),
        first.get("position_code", ""),
    )
    out["current_team_id"] = next(
        (s.get("current_team_id", "") for s in summaries if s.get("current_team_id")),
        first.get("current_team_id", ""),
    )
    return out


def get_player_summary(nhl_id: str) -> dict:
    """Landing summary; combo IDs fetch each player and merge."""
    raw = (nhl_id or "").strip()
    if "|" in raw:
        parts = [p.strip() for p in raw.split("|") if p.strip() and p.isdigit()]
        subs = [get_player_summary_single(pid) for pid in parts]
        subs = [s for s in subs if s]
        if not subs:
            return {}
        if len(subs) == 1:
            return subs[0]
        return merge_player_summaries(subs)
    return get_player_summary_single(raw)


def toi_to_minutes(toi_str: str) -> float:
    """Convert 'mm:ss' string to float minutes. Returns 0.0 on failure."""
    try:
        if not toi_str:
            return 0.0
        parts = str(toi_str).split(":")
        if len(parts) == 2:
            return round(int(parts[0]) + int(parts[1]) / 60, 2)
        return float(toi_str)
    except Exception:
        return 0.0


def classify_position(pos_code: str) -> str:
    pos = pos_code.upper()
    if pos in ("C", "L", "R", "LW", "RW"):
        return "F"  # Forward
    elif pos in ("D", "LD", "RD"):
        return "D"  # Defense
    elif pos == "G":
        return "G"  # Goalie
    else:
        return "F"  # default


def classify_scoring_tier(gp: int, points: int, goals: int, pp_points: int, role: str) -> str:
    if role == "GOALIE":
        return "GOALIE"
    if gp == 0:
        return "UNKNOWN"
    pts_per_gp = points / gp
    if pts_per_gp >= 0.90:
        return "ELITE"
    elif pts_per_gp >= 0.55:
        return "SECONDARY"
    elif pts_per_gp >= 0.25:
        return "DEPTH"
    else:
        return "SHUTDOWN"


def classify_pp_tier(pp_points: int, gp: int) -> str:
    if gp == 0:
        return "N/A"
    pp_per_gp = pp_points / gp
    if pp_per_gp >= 0.40:
        return "PP1_STAR"
    elif pp_per_gp >= 0.20:
        return "PP_REGULAR"
    elif pp_per_gp >= 0.05:
        return "PP_OCC"
    else:
        return "NO_PP"


def read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(rows: list[dict], path: str):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} rows -> {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="outputs/step5_nhl_hit_rates.csv")
    parser.add_argument("--output", default="outputs/step6_nhl_role_context.csv")
    parser.add_argument("--skip-api", action="store_true",
                        help="Skip NHL API calls and just use position from PP board")
    args = parser.parse_args()

    rows = read_csv(args.input)

    # Dedupe by player ID
    player_summaries = {}
    unique_ids = list({r.get("nhl_player_id", "") for r in rows if r.get("nhl_player_id")})
    print(f"Fetching player summaries for {len(unique_ids)} unique players...")

    if not args.skip_api:
        for i, nhl_id in enumerate(_tqdm(unique_ids, desc="  Fetching player summaries", unit="player")):
            summary = get_player_summary(nhl_id)
            player_summaries[nhl_id] = summary
            time.sleep(0.2)

    results = []
    for row in _tqdm(rows, desc="  Attaching role context", unit="prop"):
        nhl_id = row.get("nhl_player_id", "")
        role = row.get("player_role", "SKATER")
        pp_pos = row.get("position", "")

        summary = player_summaries.get(nhl_id, {})

        pos_code = summary.get("position_code", pp_pos) or pp_pos
        pos_group = classify_position(pos_code)

        gp = int(summary.get("gp", 0) or 0)
        points = int(summary.get("points_season", 0) or 0)
        goals = int(summary.get("goals_season", 0) or 0)
        pp_pts = int(summary.get("pp_points", 0) or 0)

        scoring_tier = classify_scoring_tier(gp, points, goals, pp_pts, role)
        pp_tier = classify_pp_tier(pp_pts, gp) if role != "GOALIE" else "N/A"

        row["position_code"] = pos_code
        row["position_group"] = pos_group
        row["scoring_tier"] = scoring_tier
        row["pp_tier"] = pp_tier
        row["pts_per_game"] = round(points / max(gp, 1), 3)
        row["goals_per_game"] = round(goals / max(gp, 1), 3)
        row["pp_pts_per_game"] = round(pp_pts / max(gp, 1), 3)
        row["toi_per_game_api"] = toi_to_minutes(summary.get("toi_per_game", "")) or ""

        results.append(row)

    write_csv(results, args.output)

    # Print tier summary
    tiers = {}
    for r in results:
        t = r.get("scoring_tier", "?")
        tiers[t] = tiers.get(t, 0) + 1
    print(f"Scoring tier breakdown: {tiers}")

    pp_tiers = {}
    for r in results:
        t = r.get("pp_tier", "?")
        pp_tiers[t] = pp_tiers.get(t, 0) + 1
    print(f"PP tier breakdown: {pp_tiers}")


if __name__ == "__main__":
    main()
