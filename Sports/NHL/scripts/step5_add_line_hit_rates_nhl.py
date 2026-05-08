"""
Step 5 — Compute Hit Rates for Each NHL Prop Line
Calculates Over/Under hit rates vs the PrizePicks line using:
  - L5, L10, L20 game windows
  - Season-long rate
  - Composite hit rate (weighted blend)

Usage:
    py step5_add_line_hit_rates_nhl.py --input outputs/step4_nhl_with_stats.csv \
        --output outputs/step5_nhl_hit_rates.csv
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs
try:
    from tqdm import tqdm as _tqdm
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "tqdm", "--break-system-packages", "-q"])
    from tqdm import tqdm as _tqdm


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


def _get_game_log_values_one(nhl_id: str, stat_norm: str, role: str, season: str, max_games: int = 30) -> list[float]:
    """Fetch raw game-by-game values for a single player id + stat."""
    if not (nhl_id or "").strip().isdigit():
        return []
    url = f"{NHL_WEB}/player/{nhl_id.strip()}/game-log/{season}/2"
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
        "faceoffs_won": lambda g: int(g.get("faceoffWins", 0) or 0),
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


def _average_game_logs(series_list: list[list[float]], max_games: int) -> list[float]:
    """Pairwise average across players for aligned game indices (same index = recent games)."""
    nonempty = [s for s in series_list if s]
    if not nonempty:
        return []
    if len(nonempty) == 1:
        return nonempty[0][:max_games]
    n_games = min(len(s) for s in nonempty)
    n_games = min(n_games, max_games)
    return [
        sum(nonempty[j][i] for j in range(len(nonempty))) / len(nonempty)
        for i in range(n_games)
    ]


def get_game_log_values(nhl_id: str, stat_norm: str, role: str, season: str, max_games: int = 30) -> list[float]:
    """
    Fetch game-by-game values for this prop. Combo props use pipe-separated IDs;
    each player is fetched separately and values are averaged per game index.
    """
    raw = (nhl_id or "").strip()
    if "|" in raw:
        parts = [p.strip() for p in raw.split("|") if p.strip()]
        per_player: list[list[float]] = []
        for pid in parts:
            vals = _get_game_log_values_one(pid, stat_norm, role, season, max_games)
            if vals:
                per_player.append(vals)
        return _average_game_logs(per_player, max_games)
    return _get_game_log_values_one(raw, stat_norm, role, season, max_games)


def _row_stat_g_values(row: dict, max_games: int = 30) -> list[float]:
    """
    Read precomputed rolling game values from step4 (stat_g1..stat_gN).
    step4 is already stat-normalized per row and is the most reliable source
    for recent-window hit-rate consistency shown in downstream UI.
    """
    vals: list[float] = []
    for i in range(1, max_games + 1):
        k = f"stat_g{i}"
        if k not in row:
            break
        raw = row.get(k, "")
        if raw in ("", None):
            continue
        try:
            vals.append(float(raw))
        except Exception:
            continue
    return vals


def compute_hit_rate(values: list[float], line: float, window: int | None = None) -> tuple[float, int, int]:
    """Returns (hit_rate_over, sample_size, over_count)."""
    subset = values[:window] if window else values
    if not subset:
        return 0.0, 0, 0
    over_count = sum(1 for v in subset if v > line)
    return round(over_count / len(subset), 4), len(subset), over_count


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


DEFAULT_CACHE = str(Path(__file__).resolve().parent.parent / "cache" / "nhl_gamelog_cache.json")


def load_game_log_cache(path: str) -> dict:
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


def save_game_log_cache(cache: dict, path: str):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="outputs/step4_nhl_with_stats.csv")
    parser.add_argument("--output", default="outputs/step5_nhl_hit_rates.csv")
    parser.add_argument("--gamelog-cache", default=DEFAULT_CACHE,
                        help="Path to game log JSON cache (default: cache/nhl_gamelog_cache.json next to this script)")
    parser.add_argument("--season", default=current_nhl_season(),
                        help="NHL season string e.g. 20252026 (auto-detected by default)")
    parser.add_argument("--max-games", type=int, default=30)
    args = parser.parse_args()

    rows = read_csv(args.input)
    gamelog_cache = load_game_log_cache(args.gamelog_cache)
    print(f"  Game log cache: {args.gamelog_cache} ({len(gamelog_cache)} entries)")

    results = []
    fetched = 0

    bar = _tqdm(enumerate(rows), total=len(rows), desc="  Computing hit rates", unit="prop")
    for i, row in bar:
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

        # Prefer step4 in-row game values when present. This avoids NHL API
        # stat sparsity issues (e.g., hits returning all-zero logs for skaters).
        values = _row_stat_g_values(row, args.max_games)
        if len(values) >= 3:
            bar.set_postfix(source="step4", fetched=fetched)
        else:
            cache_key = f"{nhl_id}:{stat_norm}:{args.season}"
            if cache_key in gamelog_cache:
                values = gamelog_cache[cache_key]
                bar.set_postfix(cached=True, fetched=fetched)
            else:
                bar.set_postfix(fetching=row.get('player_name','')[:15], fetched=fetched)
                values = get_game_log_values(nhl_id, stat_norm, role, args.season, args.max_games)
                gamelog_cache[cache_key] = values
                fetched += 1
                time.sleep(0.2)

        hr_L5, s5, over_L5 = compute_hit_rate(values, line, 5)
        hr_L10, s10, over_L10 = compute_hit_rate(values, line, 10)
        hr_L20, s20, over_L20 = compute_hit_rate(values, line, 20)
        hr_season, s_all, over_season = compute_hit_rate(values, line)

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
        row["over_L5"] = over_L5
        row["under_L5"] = s5 - over_L5
        row["sample_L5"] = s5
        row["hit_rate_over_L10"] = hr_L10
        row["over_L10"] = over_L10
        row["under_L10"] = s10 - over_L10
        row["sample_L10"] = s10
        row["hit_rate_over_L20"] = hr_L20
        row["over_L20"] = over_L20
        row["under_L20"] = s20 - over_L20
        row["sample_L20"] = s20
        row["hit_rate_over_season"] = hr_season
        row["sample_season"] = s_all
        row["composite_hit_rate"] = composite

        # Demons and Goblins are always OVER-only picks — force direction regardless of hit rate
        pick_type_raw = str(row.get("pick_type", "")).strip().lower()
        if "dem" in pick_type_raw or "gob" in pick_type_raw:
            row["recommended_side"] = "OVER"
            row["edge"] = round(composite - 0.5, 4)  # edge relative to OVER direction
        else:
            row["recommended_side"] = "OVER" if composite >= 0.5 else "UNDER"
            row["edge"] = edge

        results.append(row)

    save_game_log_cache(gamelog_cache, args.gamelog_cache)
    if fetched > 0:
        print(f"  Fetched {fetched} new game logs; cache updated -> {args.gamelog_cache}")

    write_csv(results, args.output)
    df_out = pd.read_csv(args.output, low_memory=False, encoding="utf-8-sig")
    copy_pipeline_output_to_dated_dirs(
        output_path=args.output,
        df=df_out,
        sport_dir_name="NHL",
        repo_root=_REPO_ROOT,
    )

    # Summary
    high_edge = [r for r in results if r.get("edge") and float(r.get("edge") or 0) >= 0.20]
    print(f"\nHigh-edge props (≥20% from 50/50): {len(high_edge)}")
    for r in sorted(high_edge, key=lambda x: -float(x.get("edge") or 0))[:10]:
        print(f"  {r['player_name']} {r['stat_norm']} {r['line_score']} "
              f"| composite={r['composite_hit_rate']} side={r['recommended_side']}")


if __name__ == "__main__":
    main()
