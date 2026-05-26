#!/usr/bin/env python3
"""
step4_attach_player_stats_nhl.py  (DB version)

Replaces api-web.nhle.com live fetching with indexed reads from proporacle_ref.db.
The DB is populated nightly by build_boxscore_ref.py.

Usage:
    py step4_attach_player_stats_nhl.py \
        --input  outputs/step3_nhl_with_defense.csv \
        --output outputs/step4_nhl_with_stats.csv

NHL lookup is by player name (no ESPN ID in the NHL pipeline).
The name match is exact first, then case-insensitive fallback.
If names still miss, run with --show-misses to see which players need
name normalization in the DB.
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from datetime import datetime, date

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from utils.pipeline_dated_outputs import copy_pipeline_output_to_dated_dirs
try:
    from tqdm import tqdm as _tqdm
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "tqdm", "--break-system-packages", "-q"])
    from tqdm import tqdm as _tqdm

import pandas as pd

# Walk up from this file to find scripts/step4_db_reader.py
_here = Path(__file__).resolve().parent
for _ in range(6):
    if (_here / "scripts" / "step4_db_reader.py").exists():
        sys.path.insert(0, str(_here / "scripts"))
        break
    _here = _here.parent
from step4_db_reader import open_db, attach_stats, db_summary, DB_PATH


HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
NHL_WEB = "https://api-web.nhle.com/v1"


def fetch_json(url: str) -> dict:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}


def _team_schedule_dates(team: str, cache_dir: Path) -> list[str]:
    """
    Return sorted game dates for team from club-schedule-season endpoint.
    Uses daily cache at cache/schedule_<TEAM>.json.
    """
    team = str(team or "").strip().upper()
    if not team:
        return []
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"schedule_{team}.json"
    today_key = date.today().isoformat()

    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if str(cached.get("fetched_on", "")) == today_key:
                return list(cached.get("game_dates", []) or [])
        except Exception:
            pass

    data = fetch_json(f"{NHL_WEB}/club-schedule-season/{team}/now")
    games = data.get("games", []) if isinstance(data, dict) else []
    dates = []
    for g in games:
        gd = str(g.get("gameDate", "") or "").strip()
        if len(gd) >= 10 and gd[4] == "-" and gd[7] == "-":
            dates.append(gd[:10])
    dates = sorted(set(dates))

    payload = {"fetched_on": today_key, "team": team, "game_dates": dates}
    cache_path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    return dates


def _fallback_prev_game_date(team: str, game_date: str, cache_dir: Path) -> str | None:
    dates = _team_schedule_dates(team, cache_dir)
    prior = [d for d in dates if d < game_date]
    return prior[-1] if prior else None


def _parse_slate_game_date(row: pd.Series) -> str:
    for col in ("game_date", "game_start", "fetched_at"):
        raw = str(row.get(col, "") or "").strip()
        if not raw:
            continue
        ts = pd.to_datetime(raw, utc=True, errors="coerce")
        if pd.notna(ts):
            return ts.strftime("%Y-%m-%d")
        if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
            return raw[:10]
    return ""


def compute_rest_days(df: pd.DataFrame, con) -> pd.DataFrame:
    """
    Attach rest_days and back_to_back by looking up prior team game_date in nhl table.
    rest_days: integer day delta, or empty when no prior game exists.
    back_to_back: 1 when rest_days == 1 else 0.
    """
    out = df.copy()
    out["game_date"] = out.apply(_parse_slate_game_date, axis=1)
    out["rest_days"] = ""
    out["back_to_back"] = 0

    if "team" not in out.columns:
        return out
    cache_dir = Path(__file__).resolve().parents[1] / "cache"

    unique_games = (
        out.loc[out["game_date"].astype(str).str.len() >= 10, ["team", "game_date"]]
        .dropna()
        .drop_duplicates()
    )

    rest_map: dict[tuple[str, str], tuple[str, int]] = {}
    for _, rec in unique_games.iterrows():
        team = str(rec.get("team", "") or "").strip().upper()
        game_date = str(rec.get("game_date", "") or "").strip()
        if not team or not game_date:
            continue
        prev = con.execute(
            "SELECT MAX(game_date) FROM nhl WHERE team = ? AND game_date < ?",
            (team, game_date),
        ).fetchone()
        prev_date = prev[0] if prev and prev[0] else None
        if not prev_date:
            prev_date = _fallback_prev_game_date(team, game_date, cache_dir)
        if not prev_date:
            rest_map[(team, game_date)] = ("", 0)
            continue
        try:
            days = (
                datetime.strptime(game_date, "%Y-%m-%d")
                - datetime.strptime(str(prev_date), "%Y-%m-%d")
            ).days
        except Exception:
            days = None
        if days is None:
            rest_map[(team, game_date)] = ("", 0)
        else:
            rest_map[(team, game_date)] = (str(int(days)), 1 if int(days) == 1 else 0)

    def _rest_key(row: pd.Series) -> tuple[str, str]:
        return (str(row.get("team", "") or "").strip().upper(), str(row.get("game_date", "") or "").strip())

    keys = out.apply(_rest_key, axis=1)
    out["rest_days"] = keys.map(lambda k: rest_map.get(k, ("", 0))[0])
    out["back_to_back"] = keys.map(lambda k: rest_map.get(k, ("", 0))[1]).astype(int)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",       default="outputs/step3_nhl_with_defense.csv")
    ap.add_argument("--output",      default="outputs/step4_nhl_with_stats.csv")
    ap.add_argument("--cache",       default="",
                    help="Legacy arg — ignored (DB is the cache now)")
    ap.add_argument("--season",      default="",
                    help="Legacy arg — ignored (DB holds all seasons)")
    ap.add_argument("--n",           type=int, default=10)
    ap.add_argument("--db",          default="", help="Override DB path")
    ap.add_argument("--show-misses", action="store_true",
                    help="Print players with NO_DATA to help diagnose name mismatches")
    ap.add_argument("--summary",     action="store_true")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else DB_PATH
    con = open_db(db_path)

    if args.summary:
        db_summary(con)
        return

    print(f"→ Loading slate: {args.input}")
    slate = pd.read_csv(args.input, dtype=str, encoding="utf-8-sig").fillna("")
    print(f"  {len(slate)} rows")

    # NHL pipeline uses player_name column
    id_col = next(
        (c for c in ["player_name", "player", "Player"] if c in slate.columns),
        None
    )
    if not id_col:
        raise SystemExit(f"No player name column found. Columns: {list(slate.columns)}")

    # prop column name varies across NHL pipeline versions
    prop_col = next(
        (c for c in ["stat_norm", "prop_norm", "prop_type"] if c in slate.columns),
        "prop_norm"
    )

    print(f"\n→ Attaching NHL stats from DB (id_col={id_col}, prop_col={prop_col}, n={args.n})...")
    with _tqdm(total=len(slate), desc="  Attaching stats", unit="row") as pbar:
        slate, counts = attach_stats(
            slate, "nhl", con,
            id_col=id_col,
            prop_col=prop_col,
            n=args.n
        )
        pbar.update(len(slate))

    slate = compute_rest_days(slate, con)

    if args.show_misses:
        misses = slate[slate["stat_status"] == "NO_DATA"][
            [id_col, prop_col, "line"]
        ].drop_duplicates()
        if not misses.empty:
            print(f"\n⚠️  NO_DATA players ({len(misses)} unique):")
            print(misses.to_string(index=False))

    slate.to_csv(args.output, index=False, encoding="utf-8-sig")
    copy_pipeline_output_to_dated_dirs(
        output_path=args.output,
        df=slate,
        sport_dir_name="NHL",
        repo_root=_REPO_ROOT,
    )
    print(f"\n✅ Saved → {args.output}  ({len(slate)} rows)")
    print("\nstat_status breakdown:")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        if v > 0:
            print(f"  {k:25s} {v:>5}")


if __name__ == "__main__":
    main()
