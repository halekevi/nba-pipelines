"""
Step 5 — Compute Hit Rates for Each NHL Prop Line
Calculates Over/Under hit rates vs the PrizePicks line using:
  - L5, L10, L20 game windows
  - Season-long rate
  - Composite hit rate (weighted blend)

Usage:
    py step5_add_line_hit_rates_nhl.py --input step4_nhl_with_stats.csv \
        --output step5_nhl_hit_rates.csv
"""

import argparse
import csv
import json
import os
import time
import urllib.request
from datetime import datetime


def current_nhl_season() -> str:
    """Auto-detect current NHL season (e.g. 20252026)."""
    now = datetime.now()
    start_year = now.year if now.month >= 10 else now.year - 1
    return f"{start_year}{start_year + 1}"

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
NHL_WEB = "https://api-web.nhle.com/v1"

# Weighting for composite hit rate
WINDOW_WEIGHTS = {
    "L5": 0.40,
    "L10": 0.35,
    "L20": 0.15,
    "season": 0.10,
}


def fetch_json(url: str, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:
            if attempt == retries - 1:
                return {}
            time.sleep(1.5 ** attempt)


def get_game_log_values(nhl_id: str, stat_norm: str, role: str, season: str, max_games: int = 30) -> list[float]:
    """Fetch raw game-by-game values for a player+stat."""
    url = f"{NHL_WEB}/player/{nhl_id}/game-log/{season}/2"
    data = fetch_json(url)
    games = data.get("gameLog", [])[:max_games]

    def _parse_toi(g) -> float:
        """Parse TOI string '22:14' -> 22.233 minutes."""
        toi_str = g.get("toi", "") or ""
        try:
            parts = toi_str.split(":")
            if len(parts) == 2:
                return int(parts[0]) + int(parts[1]) / 60
        except Exception:
            pass
        return 0.0

    SKATER_MAP = {
        "goals": "goals",
        "assists": "assists",
        "points": lambda g: int(g.get("goals", 0) or 0) + int(g.get("assists", 0) or 0),
        "shots_on_goal": "shots",
        "hits": "hits",
        "blocked_shots": "blockedShots",
        "time_on_ice": _parse_toi,
        "plus/minus": lambda g: int(g.get("plusMinus", 0) or 0),
        "power_play_points": lambda g: int(g.get("powerPlayGoals", 0) or 0) + int(g.get("powerPlayAssists", 0) or 0),
        "faceoffs_won": lambda g: int(g.get("faceoffWinningPct", 0) or 0),
        "goalie_saves": "saves",
        "fantasy_score": lambda g: (
            int(g.get("goals", 0) or 0) * 8.0
            + int(g.get("assists", 0) or 0) * 5.0
            + int(g.get("shots", 0) or 0) * 1.5
            + int(g.get("hits", 0) or 0) * 1.3
            + int(g.get("blockedShots", 0) or 0) * 1.3
        ),
    }
    GOALIE_MAP = {
        "saves": "saves",
        "goals_allowed": "goalsAgainst",
        "fantasy_score": lambda g: (
            int(g.get("saves", 0) or 0) * 0.6
            + int(g.get("goalsAgainst", 0) or 0) * -3.0
            + (6.0 if str(g.get("decision", "")).upper() == "W" else 0.0)
        ),
    }

    field_map = GOALIE_MAP if role == "GOALIE" else SKATER_MAP
    extractor = field_map.get(stat_norm)

    values = []
    for g in games:
        try:
            if callable(extractor):
                val = extractor(g)
            elif extractor:
                val = float(g.get(extractor, 0) or 0)
            else:
                val = 0.0
            values.append(val)
        except Exception:
            values.append(0.0)

    return values


def compute_hit_rate(values: list[float], line: float, window: int | None = None) -> tuple[float, int]:
    """Returns (hit_rate_over, sample_size)."""
    subset = values[:window] if window else values
    if not subset:
        return 0.0, 0
    hits = sum(1 for v in subset if v > line)
    return round(hits / len(subset), 4), len(subset)


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


def load_game_log_cache(path: str = "nhl_gamelog_cache.json") -> dict:
    if os.path.exists(path):
        try:
            with open(path) as f:
                content = f.read().strip()
                if not content:
                    return {}
                return json.loads(content)
        except (json.JSONDecodeError, Exception):
            return {}
    return {}


def save_game_log_cache(cache: dict, path: str = "nhl_gamelog_cache.json"):
    with open(path, "w") as f:
        json.dump(cache, f)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="step4_nhl_with_stats.csv")
    parser.add_argument("--output", default="step5_nhl_hit_rates.csv")
    parser.add_argument("--season", default=current_nhl_season(),
                        help="NHL season string e.g. 20252026 (auto-detected by default)")
    parser.add_argument("--max-games", type=int, default=30)
    args = parser.parse_args()

    rows = read_csv(args.input)
    gamelog_cache = load_game_log_cache()

    results = []
    fetched = 0

    for i, row in enumerate(rows):
        nhl_id = row.get("nhl_player_id", "")
        stat_norm = row.get("stat_norm", "")
        role = row.get("player_role", "SKATER")

        try:
            line = float(row.get("line_score", 0) or 0)
        except Exception:
            line = 0.0

        if not nhl_id or not stat_norm or line == 0:
            for w in [5, 10, 20]:
                row[f"hit_rate_over_L{w}"] = ""
                row[f"sample_L{w}"] = ""
            row["hit_rate_over_season"] = ""
            row["composite_hit_rate"] = ""
            row["edge"] = ""
            results.append(row)
            continue

        cache_key = f"{nhl_id}:{stat_norm}:{args.season}"
        if cache_key in gamelog_cache:
            values = gamelog_cache[cache_key]
        else:
            print(f"  [{i+1}/{len(rows)}] {row.get('player_name','')} {stat_norm} ...", end=" ", flush=True)
            values = get_game_log_values(nhl_id, stat_norm, role, args.season, args.max_games)
            gamelog_cache[cache_key] = values
            fetched += 1
            print(f"{len(values)} games")
            time.sleep(0.2)

        hr_L5, s5 = compute_hit_rate(values, line, 5)
        hr_L10, s10 = compute_hit_rate(values, line, 10)
        hr_L20, s20 = compute_hit_rate(values, line, 20)
        hr_season, s_all = compute_hit_rate(values, line)

        # Weighted composite — only include windows with enough sample
        comp_num = 0.0
        comp_den = 0.0
        for (hr, sz, wkey) in [(hr_L5, s5, "L5"), (hr_L10, s10, "L10"), (hr_L20, s20, "L20"), (hr_season, s_all, "season")]:
            if sz >= 3:
                w = WINDOW_WEIGHTS[wkey]
                comp_num += hr * w
                comp_den += w
        composite = round(comp_num / comp_den, 4) if comp_den > 0 else 0.0

        # Edge = how far composite is from 50/50
        edge = round(abs(composite - 0.5), 4)

        row["hit_rate_over_L5"] = hr_L5
        row["sample_L5"] = s5
        row["hit_rate_over_L10"] = hr_L10
        row["sample_L10"] = s10
        row["hit_rate_over_L20"] = hr_L20
        row["sample_L20"] = s20
        row["hit_rate_over_season"] = hr_season
        row["sample_season"] = s_all
        row["composite_hit_rate"] = composite
        row["edge"] = edge
        row["recommended_side"] = "OVER" if composite >= 0.5 else "UNDER"

        results.append(row)

    save_game_log_cache(gamelog_cache)
    if fetched > 0:
        print(f"  Fetched {fetched} new game logs; cache updated.")

    write_csv(results, args.output)

    # Summary
    high_edge = [r for r in results if r.get("edge") and float(r.get("edge") or 0) >= 0.20]
    print(f"\nHigh-edge props (≥20% from 50/50): {len(high_edge)}")
    for r in sorted(high_edge, key=lambda x: -float(x.get("edge") or 0))[:10]:
        print(f"  {r['player_name']} {r['stat_norm']} {r['line_score']} "
              f"| composite={r['composite_hit_rate']} side={r['recommended_side']}")


if __name__ == "__main__":
    main()
