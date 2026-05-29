#!/usr/bin/env python3
"""
Backtest graded props: top/bottom team producers × opponent defense tier.

Hypotheses tested (per prop type):
  - Bottom-3 producers vs Elite + Above Avg defense → higher hit rate (often UNDER lean)
  - Top-3 producers vs Weak + Below Avg defense → higher hit rate (often OVER lean)

Uses graded_props JSON (def_tier on each row) and point-in-time team rankings from
box-score caches (games strictly before slate file_date).

Grading semantics (matches leg_grade_utils.py):
  OVER HIT  = actual > line
  UNDER HIT = actual < line  (strictly under; equal = PUSH, excluded from HR)
  Reported hit_rate uses the bet direction on each graded row.

Also reports counterfactual HR (what if every leg in a slice were OVER or UNDER)
and theory-aligned slices (bottom×tough→UNDER, top×soft→OVER).

Usage (repo root):
  py -3 scripts/backtest_player_tier_vs_defense.py
  py -3 scripts/backtest_player_tier_vs_defense.py --sport NBA --min-n 15
  py -3 scripts/backtest_player_tier_vs_defense.py --from 2026-05-06

NHL point-in-time tags require data/cache/proporacle_ref.db (gitignored). Build once:
  py -3 scripts/build_boxscore_ref.py --sports nhl --backfill --days 75

MLB fade sanity check (use step8 CSV — clean xlsx drops audit columns):
  py -3 -c "import pandas as pd; from pathlib import Path; p=next(Path('outputs').rglob('step8_mlb_direction.csv'), None); s=pd.read_csv(p); o=(s['direction_override'].astype(str).str.strip()=='BOTTOM3_TOUGH_UNDER').sum(); r=(s['final_dir_reason'].astype(str).str.strip()=='BOTTOM3_TOUGH_UNDER').sum(); print(p); print('override',o,'reason',r)"
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import json

from analyze_graded_history import (  # noqa: E402
    _GRADED_DIR,
    _norm_dir,
    _norm_pick,
    _norm_prop_type,
    _norm_sport,
    _parse_hit,
)
from utils.defense_tiers import normalize_def_tier_label  # noqa: E402

TOUGH_DEF = frozenset({"Elite", "Above Avg"})
SOFT_DEF = frozenset({"Weak", "Below Avg"})
TOP_N = 3
BOTTOM_N = 3
MIN_GAMES_PIT = 8
PUSH_EPS = 1e-9

# Graded prop label / norm → analyze_top_players category id
NBA_PROP_CAT: dict[str, str] = {
    "points": "pts",
    "pts": "pts",
    "rebounds": "reb",
    "reb": "reb",
    "offensiverebounds": "reb",
    "defensiverebounds": "reb",
    "assists": "ast",
    "ast": "ast",
    "steals": "stl",
    "stl": "stl",
    "blocks": "blk",
    "blk": "blk",
    "3ptmade": "fg3m",
    "3ptmmade": "fg3m",
    "3pt": "fg3m",
    "fg3m": "fg3m",
    "threes": "fg3m",
    "pts+rebs+asts": "pra",
    "pts+rebs": "pra",
    "pts+asts": "pra",
    "rebs+asts": "pra",
    "fantasyscore": "pra",
}

WNBA_PROP_CAT = dict(NBA_PROP_CAT)

NHL_PROP_CAT: dict[str, str] = {
    "goals": "goals",
    "goal": "goals",
    "assists": "assists",
    "assist": "assists",
    "points": "points",
    "pts": "points",
    "powerplaypoints": "points",
    "shots": "shots",
    "shotsongoal": "shots",
    "sog": "shots",
    "shots_on_goal": "shots",
}

MLB_PROP_CAT: dict[str, str] = {
    "hits": "hits",
    "hit": "hits",
    "runs": "hits",
    "run": "hits",
    "total bases": "total_bases",
    "total_bases": "total_bases",
    "home runs": "home_runs",
    "home_runs": "home_runs",
    "hr": "home_runs",
    "rbi": "rbi",
    "rbis": "rbi",
}

SOCCER_PROP_CAT: dict[str, str] = {
    "goals": "goals",
    "goal": "goals",
    "assists": "assists",
    "assist": "assists",
    "shots": "shots",
    "shot": "shots",
    "shotsongoal": "shots",
    "sog": "shots",
    "passes": "passes",
    "pass": "passes",
    "tackles": "tackles",
    "tackle": "tackles",
}

# Pipeline sports with graded archives (see mobile/www/graded_props_*.json)
PIPELINE_SPORTS: tuple[str, ...] = (
    "NBA",
    "NBA1H",
    "NBA1Q",
    "WNBA",
    "NHL",
    "MLB",
    "SOCCER",
    "TENNIS",
)

# Sports where we build point-in-time top/bottom-3 from box-score caches
PIT_SPORTS: frozenset[str] = frozenset({"NBA", "NBA1H", "NBA1Q", "WNBA", "NHL", "MLB"})


def _norm_name(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _norm_prop_key(prop: object) -> str:
    s = str(prop or "").strip().lower()
    s = re.sub(r"[^a-z0-9+]+", " ", s).strip()
    s = s.replace(" ", "")
    s = s.replace("threepoint", "3pt").replace("3ptmade", "3pt")
    return s


def prop_to_category(sport: str, prop: object, *, prop_raw: object = "") -> str:
    sp = _norm_sport(sport)
    key = _norm_prop_key(prop_raw or prop)
    # combo props with + in key
    if "+" in key:
        for part in key.split("+"):
            if part in ("pts", "rebs", "asts", "reb", "ast"):
                return "pra"
    maps = {
        "NBA": NBA_PROP_CAT,
        "NBA1H": NBA_PROP_CAT,
        "NBA1Q": NBA_PROP_CAT,
        "WNBA": WNBA_PROP_CAT,
        "NHL": NHL_PROP_CAT,
        "MLB": MLB_PROP_CAT,
        "SOCCER": SOCCER_PROP_CAT,
        "SOC": SOCCER_PROP_CAT,
    }
    m = maps.get(sp, {})
    if key in m:
        return m[key]
    # fuzzy
    for pat, cat in m.items():
        if pat in key or key in pat:
            return cat
    # display labels from _norm_prop_type (e.g. "Shots on Goal", "3-PT Made")
    label = _norm_prop_type(prop).casefold()
    label_key = _norm_prop_key(label)
    if label_key in m:
        return m[label_key]
    return m.get(label_key, "")


def _def_tier_series(raw: pd.Series) -> pd.Series:
    return raw.map(lambda x: normalize_def_tier_label(x) or "(missing)")


def _hr_table(df: pd.DataFrame, group_cols: list[str], min_n: int) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    g = (
        df.groupby(group_cols, dropna=False)
        .agg(n=("hit", "count"), hits=("hit", "sum"))
        .reset_index()
    )
    g["hit_rate"] = g["hits"] / g["n"]
    g = g[g["n"] >= min_n].sort_values("hit_rate", ascending=False)
    return g


def _print_compare(
    label: str,
    target: pd.DataFrame,
    baseline: pd.DataFrame,
    *,
    min_n: int,
) -> None:
    if target.empty:
        print(f"\n{label}: no rows")
        return
    tn, th = len(target), float(target["hit"].mean())
    bn = len(baseline)
    bh = float(baseline["hit"].mean()) if bn else float("nan")
    lift = (th - bh) * 100 if bn else float("nan")
    flag = "✓" if lift > 2.0 and tn >= min_n else ("✗" if lift < -2.0 and tn >= min_n else "·")
    print(
        f"\n{label} {flag}\n"
        f"  Target:    {100*th:.1f}%  (n={tn:,})\n"
        f"  Baseline:  {100*bh:.1f}%  (n={bn:,})  "
        f"lift {lift:+.1f} pp"
    )


# --- point-in-time rankings from game logs ---


def _derive_bball_stat(df: pd.DataFrame, cat: str) -> pd.Series:
    pts = pd.to_numeric(df.get("PTS"), errors="coerce")
    reb = pd.to_numeric(df.get("REB"), errors="coerce")
    ast = pd.to_numeric(df.get("AST"), errors="coerce")
    stl = pd.to_numeric(df.get("STL"), errors="coerce")
    blk = pd.to_numeric(df.get("BLK"), errors="coerce")
    fg3m = pd.to_numeric(df.get("FG3M"), errors="coerce")
    if cat == "pts":
        return pts
    if cat == "reb":
        return reb
    if cat == "ast":
        return ast
    if cat == "stl":
        return stl
    if cat == "blk":
        return blk
    if cat == "fg3m":
        return fg3m
    if cat == "stocks":
        return stl.fillna(0) + blk.fillna(0)
    if cat == "pra":
        return pts.fillna(0) + reb.fillna(0) + ast.fillna(0)
    return pd.Series(np.nan, index=df.index)


def _build_pit_lookup_bball(
    logs: pd.DataFrame,
    *,
    slate_dates: list[str],
    team_col: str = "TEAM",
    categories: tuple[str, ...],
    team_key_fn=None,
) -> dict[tuple[str, str, str, str], str]:
    """(file_date, team_key, player_norm, category) -> 'top'|'bottom'|''."""
    if logs.empty or not slate_dates:
        return {}
    team_key_fn = team_key_fn or (lambda x: str(x or "").strip().upper())
    out = logs.copy()
    out["game_date"] = pd.to_datetime(out["game_date"], errors="coerce")
    out = out[out["game_date"].notna()].sort_values("game_date")
    out["PLAYER_NORM"] = out["PLAYER_NAME"].map(_norm_name)
    out["team_key"] = out[team_col].map(team_key_fn)
    for cat in categories:
        out[f"_stat_{cat}"] = _derive_bball_stat(out, cat)
    lookup: dict[tuple[str, str, str, str], str] = {}
    for d_str in sorted(set(str(x)[:10] for x in slate_dates if x)):
        d = pd.Timestamp(d_str)
        prior = out[out["game_date"] < d]
        if prior.empty:
            continue
        for cat in categories:
            col = f"_stat_{cat}"
            agg = (
                prior.groupby(["team_key", "PLAYER_NORM"], as_index=False)
                .agg(season_avg=(col, "mean"), games=(col, "count"))
            )
            agg = agg[agg["games"] >= MIN_GAMES_PIT]
            for team_key, grp in agg.groupby("team_key", sort=False):
                top = set(grp.nlargest(TOP_N, "season_avg")["PLAYER_NORM"])
                bot = set(grp.nsmallest(BOTTOM_N, "season_avg")["PLAYER_NORM"])
                for pn in top:
                    lookup[(d_str, str(team_key), pn, cat)] = "top"
                for pn in bot:
                    if pn not in top:
                        lookup[(d_str, str(team_key), pn, cat)] = "bottom"
    return lookup


def _load_nba_logs() -> pd.DataFrame:
    p = _REPO / "Sports" / "NBA" / "data" / "cache" / "espn_boxscores_cache.csv"
    if not p.is_file():
        return pd.DataFrame()
    df = pd.read_csv(p, low_memory=False, encoding="utf-8-sig")
    rename = {
        "player": "PLAYER_NAME",
        "team": "TEAM",
        "date": "game_date",
        "points": "PTS",
        "totalRebounds": "REB",
        "assists": "AST",
        "steals": "STL",
        "blocks": "BLK",
        "threePointFieldGoalsMade": "FG3M",
    }
    for src, dst in rename.items():
        if src in df.columns and dst not in df.columns:
            df[dst] = df[src]
    return df


def _load_wnba_logs() -> pd.DataFrame:
    for p in (
        _REPO / "Sports" / "WNBA" / "wnba_espn_cache.csv",
        _REPO / "Sports" / "WNBA" / "data" / "cache" / "wnba_boxscores_cache.csv",
    ):
        if not p.is_file():
            continue
        df = pd.read_csv(p, low_memory=False, encoding="utf-8-sig")
        if "PLAYER_NAME" not in df.columns and "player" in df.columns:
            df["PLAYER_NAME"] = df["player"]
        if "TEAM" not in df.columns and "team" in df.columns:
            df["TEAM"] = df["team"]
        if "game_date" not in df.columns and "date" in df.columns:
            df["game_date"] = df["date"]
        return df
    return pd.DataFrame()


def _load_nhl_logs() -> pd.DataFrame:
    """Skater game logs from proporacle_ref.db (same schema as analyze_top_players_vs_defense)."""
    db = _REPO / "data" / "cache" / "proporacle_ref.db"
    if not db.is_file():
        raise FileNotFoundError(
            f"NHL boxscore DB missing: {db}\n"
            "Run: py -3 scripts/build_boxscore_ref.py --sports nhl --backfill --days 75"
        )
    import sqlite3

    conn = sqlite3.connect(str(db))
    try:
        df = pd.read_sql_query(
            """
            SELECT player, team, game_date, position,
                   goals, assists, points, shots_on_goal
            FROM nhl
            ORDER BY game_date
            """,
            conn,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to read NHL logs from {db} (is the nhl table populated? "
            "Run build_boxscore_ref.py --sports nhl --backfill)."
        ) from exc
    finally:
        conn.close()
    if df.empty:
        return df
    df["PLAYER_NAME"] = df["player"].astype(str)
    df["TEAM"] = df["team"].astype(str).str.upper().str.strip()
    pos = df["position"].astype(str).str.upper()
    df = df[~pos.eq("G")].copy()
    df["GOALS"] = pd.to_numeric(df["goals"], errors="coerce")
    df["AST"] = pd.to_numeric(df["assists"], errors="coerce")
    df["PTS"] = pd.to_numeric(df["points"], errors="coerce")
    df["SHOTS"] = pd.to_numeric(df["shots_on_goal"], errors="coerce")
    return df


def _derive_nhl_stat(df: pd.DataFrame, cat: str) -> pd.Series:
    if cat == "goals":
        return pd.to_numeric(df.get("GOALS"), errors="coerce")
    if cat == "assists":
        return pd.to_numeric(df.get("AST"), errors="coerce")
    if cat == "points":
        return pd.to_numeric(df.get("PTS"), errors="coerce")
    if cat == "shots":
        return pd.to_numeric(df.get("SHOTS"), errors="coerce")
    return pd.Series(np.nan, index=df.index)


def _nhl_defense_team_key(team: object) -> str:
    s = str(team or "").strip().upper()
    return {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL", "CLB": "CBJ", "ARZ": "UTA"}.get(s, s)


def _build_pit_lookup_nhl(
    logs: pd.DataFrame, *, slate_dates: list[str]
) -> dict[tuple[str, str, str, str], str]:
    if logs.empty or not slate_dates:
        return {}
    categories = ("goals", "assists", "points", "shots")
    out = logs.copy()
    out["game_date"] = pd.to_datetime(out["game_date"], errors="coerce")
    out = out[out["game_date"].notna()].sort_values("game_date")
    out["PLAYER_NORM"] = out["PLAYER_NAME"].map(_norm_name)
    out["team_key"] = out["TEAM"].astype(str).str.upper().str.strip()
    lookup: dict[tuple[str, str, str, str], str] = {}
    for cat in categories:
        out[f"_stat_{cat}"] = _derive_nhl_stat(out, cat)
    for d_str in sorted(set(str(x)[:10] for x in slate_dates if x)):
        d = pd.Timestamp(d_str)
        prior = out[out["game_date"] < d]
        for cat in categories:
            col = f"_stat_{cat}"
            agg = (
                prior.groupby(["team_key", "PLAYER_NORM"], as_index=False)
                .agg(season_avg=(col, "mean"), games=(col, "count"))
            )
            agg = agg[agg["games"] >= MIN_GAMES_PIT]
            for team_key, grp in agg.groupby("team_key", sort=False):
                top = set(grp.nlargest(TOP_N, "season_avg")["PLAYER_NORM"])
                bot = set(grp.nsmallest(BOTTOM_N, "season_avg")["PLAYER_NORM"])
                for pn in top:
                    lookup[(d_str, str(team_key), pn, cat)] = "top"
                for pn in bot:
                    if pn not in top:
                        lookup[(d_str, str(team_key), pn, cat)] = "bottom"
    return lookup


def _load_mlb_pit_lookup(*, slate_dates: list[str]) -> dict[tuple[str, str, str, str], str]:
    """Build PIT top/bottom lookup from MLB long-format stats cache + id cache."""
    cache = _REPO / "Sports" / "MLB" / "mlb_stats_cache.csv"
    id_cache = _REPO / "Sports" / "MLB" / "mlb_id_cache.csv"
    if not cache.is_file():
        return {}
    try:
        from Sports.MLB.scripts.analyze_top_hitters_vs_defense import (  # noqa: WPS433
            CATEGORIES,
            _attach_player_names,
            _id_to_abbrev,
            _load_game_logs,
            _load_player_names,
            _norm_team_id,
        )
    except Exception:
        return {}

    wide, _ = _load_game_logs(cache, season=None)
    if wide.empty:
        return {}
    names = _load_player_names(id_cache, [])
    wide = _attach_player_names(wide, names)
    id_map = _id_to_abbrev()
    categories = tuple(CATEGORIES)
    out = wide.copy()
    out["game_date"] = pd.to_datetime(out["game_date"], errors="coerce")
    out = out[out["game_date"].notna()].sort_values("game_date")
    out["team_key"] = out["TEAM"].astype(str).str.upper()
    lookup: dict[tuple[str, str, str, str], str] = {}
    for d_str in sorted(set(str(x)[:10] for x in slate_dates if x)):
        d = pd.Timestamp(d_str)
        prior = out[out["game_date"] < d]
        for cat in categories:
            if cat not in prior.columns:
                continue
            sub = prior.copy()
            sub["_val"] = pd.to_numeric(sub[cat], errors="coerce")
            agg = (
                sub.groupby(["team_key", "PLAYER_NORM"], as_index=False)
                .agg(season_avg=("_val", "mean"), games=("_val", "count"))
            )
            agg = agg[agg["games"] >= MIN_GAMES_PIT]
            for team_key, grp in agg.groupby("team_key", sort=False):
                top = set(grp.nlargest(TOP_N, "season_avg")["PLAYER_NORM"])
                bot = set(grp.nsmallest(BOTTOM_N, "season_avg")["PLAYER_NORM"])
                for pn in top:
                    lookup[(d_str, str(team_key), pn, cat)] = "top"
                for pn in bot:
                    if pn not in top:
                        lookup[(d_str, str(team_key), pn, cat)] = "bottom"
    return lookup


def load_graded_with_defense(
    *,
    sport: str | None = None,
    days: int | None = None,
    min_date: str | None = None,
) -> pd.DataFrame:
    paths = sorted(p for p in _GRADED_DIR.glob("graded_props_*.json") if ".bak_" not in p.name)
    if days and days > 0:
        paths = paths[-days:]
    min_d = str(min_date or "").strip()[:10]
    rows: list[dict] = []
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        file_date = str(raw.get("date") or path.stem.replace("graded_props_", ""))[:10]
        if min_d and len(min_d) == 10 and file_date < min_d:
            continue
        chunk = raw.get("props", raw.get("rows", []))
        if not isinstance(chunk, list):
            continue
        for r in chunk:
            if not isinstance(r, dict):
                continue
            sp = _norm_sport(r.get("sport"))
            if sport and sp != _norm_sport(sport):
                continue
            hit = _parse_hit(r.get("result"))
            if hit is None:
                continue
            line = pd.to_numeric(r.get("line"), errors="coerce")
            actual = pd.to_numeric(r.get("actual_value"), errors="coerce")
            rows.append(
                {
                    "player": str(r.get("player", "")).strip(),
                    "team": str(r.get("team", "")).strip().upper(),
                    "opp_team": str(r.get("opp_team", "")).strip().upper(),
                    "sport": sp,
                    "prop_type": _norm_prop_type(r.get("prop")),
                    "prop_raw": str(r.get("prop", "")),
                    "pick_type": _norm_pick(r.get("pick_type")),
                    "direction": _norm_dir(r.get("direction") or r.get("over_under")),
                    "def_tier": r.get("def_tier") or r.get("DEF_TIER") or "",
                    "line": line,
                    "actual_value": actual,
                    "hit": int(hit),
                    "file_date": file_date,
                    "on_ticket": bool(r.get("on_ticket")),
                }
            )
    return pd.DataFrame(rows)


def _add_counterfactual_hits(df: pd.DataFrame) -> pd.DataFrame:
    """hit_if_over / hit_if_under from actual vs line (push → NaN)."""
    out = df.copy()
    line = pd.to_numeric(out["line"], errors="coerce")
    actual = pd.to_numeric(out["actual_value"], errors="coerce")
    margin = actual - line
    push = margin.abs() <= PUSH_EPS
    out["hit_if_over"] = np.where(push, np.nan, np.where(actual > line, 1.0, 0.0))
    out["hit_if_under"] = np.where(push, np.nan, np.where(actual < line, 1.0, 0.0))
    return out


def _hr_series(s: pd.Series) -> float:
    v = pd.to_numeric(s, errors="coerce").dropna()
    return float(v.mean()) if len(v) else float("nan")


def _direction_slice_report(
    sub: pd.DataFrame,
    *,
    sport: str,
    slice_name: str,
    min_n: int,
) -> list[dict]:
    """Per-sport direction + counterfactual metrics for one matchup slice."""
    rows: list[dict] = []
    if sub.empty:
        return rows

    def _row(metric: str, n: int, hr: float, note: str = "") -> dict:
        return {
            "sport": sport,
            "matchup_slice": slice_name,
            "metric": metric,
            "n": n,
            "hit_rate": round(hr, 4) if pd.notna(hr) else None,
            "note": note,
        }

    n = len(sub)
    rows.append(_row("bet_actual_direction", n, _hr_series(sub["hit"])))

    over_bet = sub[sub["direction"] == "OVER"]
    under_bet = sub[sub["direction"] == "UNDER"]
    if len(over_bet) >= min_n:
        rows.append(_row("bet_OVER_only", len(over_bet), _hr_series(over_bet["hit"])))
    if len(under_bet) >= min_n:
        rows.append(_row("bet_UNDER_only", len(under_bet), _hr_series(under_bet["hit"])))

    cf_over = sub["hit_if_over"].dropna()
    cf_under = sub["hit_if_under"].dropna()
    if len(cf_over) >= min_n:
        rows.append(
            _row(
                "counterfactual_all_OVER",
                len(cf_over),
                _hr_series(cf_over),
                "HR if every leg in slice were OVER",
            )
        )
    if len(cf_under) >= min_n:
        rows.append(
            _row(
                "counterfactual_all_UNDER",
                len(cf_under),
                _hr_series(cf_under),
                "HR if every leg in slice were UNDER",
            )
        )

    # Theory-aligned: bottom×tough should be UNDER; top×soft should be OVER
    if "bottom" in slice_name:
        if len(cf_under) >= min_n:
            rows.append(_row("theory_UNDER_cf", len(cf_under), _hr_series(cf_under)))
    if "top" in slice_name:
        if len(cf_over) >= min_n:
            rows.append(_row("theory_OVER_cf", len(cf_over), _hr_series(cf_over)))

    return rows


def _team_lookup_key(sport: str, team: str) -> str:
    """Map graded/slate team abbrev → box-score cache abbrev for PIT lookup."""
    t = str(team or "").strip().upper()
    sp = _norm_sport(sport)
    if sp in ("NBA", "NBA1H", "NBA1Q"):
        nba_map = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "PHO": "PHX", "WSH": "WAS", "UTAH": "UTA", "BRK": "BKN"}
        return nba_map.get(t, t)
    if sp == "NHL":
        # proporacle_ref.nhl uses ESPN abbrevs (LA, NJ, SJ) — not defense-summary LAK/NJD
        return t
    if sp == "WNBA":
        # Slate/PrizePicks → ESPN cache (wnba_espn_cache.csv TEAM column)
        wnba_map = {
            "LVA": "LV",
            "LAS": "LA",
            "NYL": "NY",
            "GSV": "GS",
            "PHX": "PHO",
            "CON": "CONN",
            "WSH": "WSH",
            "DAL": "DAL",
            "IND": "IND",
            "ATL": "ATL",
            "CHI": "CHI",
            "MIN": "MIN",
            "SEA": "SEA",
            "POR": "POR",
            "TOR": "TOR",
        }
        return wnba_map.get(t, t)
    return t


def attach_player_tier(df: pd.DataFrame, lookups: dict[str, dict]) -> pd.DataFrame:
    out = df.copy()
    out["player_norm"] = out["player"].map(_norm_name)
    pr_raw = out["prop_raw"] if "prop_raw" in out.columns else out["prop_type"]
    out["prop_category"] = [
        prop_to_category(sp, pr, prop_raw=raw)
        for sp, pr, raw in zip(out["sport"], out["prop_type"], pr_raw)
    ]
    out["player_tier"] = ""
    for sp, lu in lookups.items():
        if not lu:
            continue
        mask = out["sport"].eq(sp)
        teams = out.loc[mask, "team"].map(lambda t: _team_lookup_key(sp, t))
        dates = out.loc[mask, "file_date"].astype(str).str[:10]
        pnorm = out.loc[mask, "player_norm"]
        cats = out.loc[mask, "prop_category"]
        tiers = []
        for d, t, p, c in zip(dates, teams, pnorm, cats):
            if not c:
                tiers.append("")
                continue
            tiers.append(lu.get((d, t, p, c), ""))
        out.loc[mask, "player_tier"] = tiers
    return out


def _slice_metrics(
    target: pd.DataFrame,
    baseline: pd.DataFrame,
    *,
    sport: str,
    slice_name: str,
    min_n: int,
) -> dict | None:
    if target.empty:
        return None
    n = len(target)
    hr = float(target["hit"].mean())
    base_hr = float(baseline["hit"].mean()) if len(baseline) else float("nan")
    lift = (hr - base_hr) * 100 if len(baseline) else float("nan")
    flag = ""
    if n >= min_n:
        if lift > 2.0:
            flag = "yes"
        elif lift < -2.0:
            flag = "no"
        else:
            flag = "neutral"
    return {
        "sport": sport,
        "slice": slice_name,
        "n": n,
        "hit_rate": round(hr, 4),
        "baseline_hr": round(base_hr, 4) if pd.notna(base_hr) else None,
        "lift_pp": round(lift, 2) if pd.notna(lift) else None,
        "validated": flag,
    }


def _defense_only_slices(sub: pd.DataFrame, sport: str, min_n: int) -> list[dict]:
    """When player tier unavailable: OVER×soft def, UNDER×tough def (all players)."""
    rows: list[dict] = []
    over = sub[sub["direction"] == "OVER"]
    under = sub[sub["direction"] == "UNDER"]
    over_soft = over[over["def_tier_norm"].isin(SOFT_DEF)]
    under_tough = under[under["def_tier_norm"].isin(TOUGH_DEF)]
    over_other = over[~over["def_tier_norm"].isin(SOFT_DEF)]
    under_other = under[~under["def_tier_norm"].isin(TOUGH_DEF)]
    for name, tgt, base in (
        ("all_OVER×soft_DEF", over_soft, over),
        ("all_OVER×other_DEF", over_other, over),
        ("all_UNDER×tough_DEF", under_tough, under),
        ("all_UNDER×other_DEF", under_other, under),
    ):
        m = _slice_metrics(tgt, base, sport=sport, slice_name=name, min_n=min_n)
        if m:
            rows.append(m)
    return rows


def run_backtest(df: pd.DataFrame, *, min_n: int, sport_filter: str | None) -> None:
    df = df.copy()
    if "line" in df.columns and "actual_value" in df.columns:
        df = _add_counterfactual_hits(df)
    df["def_tier_norm"] = _def_tier_series(df.get("def_tier", pd.Series("", index=df.index)))

    print("=" * 72)
    print("  PLAYER TIER × DEFENSE TIER BACKTEST (all pipeline sports)")
    print("  Coverage (before def_tier filter):")
    for sp in sorted(df["sport"].unique()):
        sub = df[df["sport"] == sp]
        has_def = (sub["def_tier_norm"] != "(missing)").sum()
        print(f"    {sp:<8} {len(sub):>8,} rows  |  def_tier filled: {has_def:,} ({100*has_def/len(sub):.1f}%)")

    excluded = df[df["def_tier_norm"] == "(missing)"]
    if not excluded.empty:
        ex_sp = excluded.groupby("sport").size().sort_values(ascending=False)
        print(f"  Excluded (no def_tier): {len(excluded):,} rows — {', '.join(f'{k} {v:,}' for k,v in ex_sp.items())}")

    df = df[df["def_tier_norm"] != "(missing)"].copy()
    if sport_filter:
        df = df[df["sport"].eq(sport_filter)].copy()

    overall = float(df["hit"].mean())
    print(f"\n  Analyzed rows: {len(df):,}  |  Overall HR: {100*overall:.1f}%")
    if not df["file_date"].empty:
        print(f"  Dates: {df['file_date'].min()[:10]} → {df['file_date'].max()[:10]}")
    print("=" * 72)

    has_tier = df["player_tier"].isin(["top", "bottom"])
    print(f"\nRows with point-in-time top/bottom-3 tag: {has_tier.sum():,} / {len(df):,}")

    # --- global slices ---
    bottom_tough = df[(df["player_tier"] == "bottom") & df["def_tier_norm"].isin(TOUGH_DEF)]
    top_soft = df[(df["player_tier"] == "top") & df["def_tier_norm"].isin(SOFT_DEF)]
    bottom_other = df[(df["player_tier"] == "bottom") & ~df["def_tier_norm"].isin(TOUGH_DEF)]
    top_other = df[(df["player_tier"] == "top") & ~df["def_tier_norm"].isin(SOFT_DEF)]

    _print_compare(
        "BOTTOM-3 vs ELITE + ABOVE AVG (all props)",
        bottom_tough,
        df,
        min_n=min_n,
    )
    _print_compare(
        "BOTTOM-3 vs other defenses (control)",
        bottom_other,
        df,
        min_n=min_n,
    )
    _print_compare(
        "TOP-3 vs WEAK + BELOW AVG (all props)",
        top_soft,
        df,
        min_n=min_n,
    )
    _print_compare(
        "TOP-3 vs other defenses (control)",
        top_other,
        df,
        min_n=min_n,
    )

    # Direction-aligned (theory: bottom+tough → UNDER, top+soft → OVER)
    bottom_tough_under = bottom_tough[bottom_tough["direction"] == "UNDER"]
    top_soft_over = top_soft[top_soft["direction"] == "OVER"]
    _print_compare(
        "BOTTOM-3 + tough DEF + UNDER direction",
        bottom_tough_under,
        df[df["direction"] == "UNDER"],
        min_n=min_n,
    )
    _print_compare(
        "TOP-3 + soft DEF + OVER direction",
        top_soft_over,
        df[df["direction"] == "OVER"],
        min_n=min_n,
    )

    sport_summary_rows: list[dict] = []
    direction_rows: list[dict] = []

    # --- per sport: direction + counterfactual (theory-aligned) ---
    print("\n--- Direction & counterfactual HR by sport (all pipeline sports) ---")
    print("  UNDER HIT = actual < line | OVER HIT = actual > line | PUSH excluded")
    for sp in sorted(df["sport"].unique()):
        sub = df[df["sport"] == sp]
        bt = sub[(sub["player_tier"] == "bottom") & sub["def_tier_norm"].isin(TOUGH_DEF)]
        ts = sub[(sub["player_tier"] == "top") & sub["def_tier_norm"].isin(SOFT_DEF)]
        for slice_name, sl in (("bottom×tough", bt), ("top×soft", ts)):
            if sl.empty:
                continue
            direction_rows.extend(
                _direction_slice_report(sl, sport=sp, slice_name=slice_name, min_n=min_n)
            )
            bet_hr = _hr_series(sl["hit"])
            cf_under = _hr_series(sl["hit_if_under"]) if "hit_if_under" in sl.columns else float("nan")
            cf_over = _hr_series(sl["hit_if_over"]) if "hit_if_over" in sl.columns else float("nan")
            over_pct = 100.0 * (sl["direction"] == "OVER").mean()
            print(
                f"  {sp:<8} {slice_name:<14} n={len(sl):>5,}  "
                f"bet={100*bet_hr:.1f}%  cf_UNDER={100*cf_under:.1f}%  cf_OVER={100*cf_over:.1f}%  "
                f"({over_pct:.0f}% bet OVER)"
            )

    # --- per sport (full pipeline table) ---
    print("\n--- By sport (all pipelines) ---")
    print(
        f"{'Sport':<8} {'Rows':>8} {'Def%':>6} {'Tag%':>6} "
        f"{'Bot×Tgh':>8} {'Top×Sft':>8} {'Ovr×Sft':>8} {'Und×Tgh':>8}"
    )
    for sp in sorted(df["sport"].unique()):
        sub = df[df["sport"] == sp]
        n_all = len(sub)
        def_pct = 100.0 * n_all / max(n_all, 1)
        tagged = sub["player_tier"].isin(["top", "bottom"])
        tag_pct = 100.0 * tagged.sum() / max(n_all, 1)
        base_hr = float(sub["hit"].mean())

        bt = sub[(sub["player_tier"] == "bottom") & sub["def_tier_norm"].isin(TOUGH_DEF)]
        ts = sub[(sub["player_tier"] == "top") & sub["def_tier_norm"].isin(SOFT_DEF)]
        bt_hr = f"{100*bt['hit'].mean():.1f}%" if len(bt) else "—"
        ts_hr = f"{100*ts['hit'].mean():.1f}%" if len(ts) else "—"

        over = sub[sub["direction"] == "OVER"]
        under = sub[sub["direction"] == "UNDER"]
        os = over[over["def_tier_norm"].isin(SOFT_DEF)]
        ut = under[under["def_tier_norm"].isin(TOUGH_DEF)]
        os_hr = f"{100*os['hit'].mean():.1f}%" if len(os) else "—"
        ut_hr = f"{100*ut['hit'].mean():.1f}%" if len(ut) else "—"

        print(
            f"  {sp:<8} {n_all:>8,} {def_pct:>5.0f}% {tag_pct:>5.1f}% "
            f"{bt_hr:>8} {ts_hr:>8} {os_hr:>8} {ut_hr:>8}  (base {100*base_hr:.1f}%)"
        )

        sport_summary_rows.append(
            {
                "sport": sp,
                "n_with_def_tier": n_all,
                "baseline_hr": round(base_hr, 4),
                "pct_tagged_top_bottom": round(tag_pct, 2),
            }
        )
        for slice_name, tgt in (
            ("bottom×tough", bt),
            ("top×soft", ts),
            ("bottom×tough_UNDER", bt[bt["direction"] == "UNDER"]),
            ("top×soft_OVER", ts[ts["direction"] == "OVER"]),
        ):
            m = _slice_metrics(tgt, sub, sport=sp, slice_name=slice_name, min_n=min_n)
            if m:
                sport_summary_rows.append(m)
        if sp not in PIT_SPORTS or tagged.sum() < min_n:
            sport_summary_rows.extend(_defense_only_slices(sub, sp, min_n))

    # --- per prop type (within tagged rows) ---
    print(f"\n--- Per prop type (min n={min_n}) ---")
    tagged = df[has_tier & df["prop_category"].astype(str).str.len().gt(0)]
    if tagged.empty:
        print("  (no prop_category matches)")
        return

    rows_out = []
    for (sp, prop_label, cat), grp in tagged.groupby(
        ["sport", "prop_type", "prop_category"], sort=False
    ):
        base = grp
        bt = grp[(grp["player_tier"] == "bottom") & grp["def_tier_norm"].isin(TOUGH_DEF)]
        ts = grp[(grp["player_tier"] == "top") & grp["def_tier_norm"].isin(SOFT_DEF)]
        if len(bt) >= min_n:
            rows_out.append(
                {
                    "slice": "bottom×tough",
                    "sport": sp,
                    "prop": prop_label,
                    "category": cat,
                    "n": len(bt),
                    "hr": bt["hit"].mean(),
                    "baseline_hr": base["hit"].mean(),
                    "lift_pp": (bt["hit"].mean() - base["hit"].mean()) * 100,
                }
            )
        if len(ts) >= min_n:
            rows_out.append(
                {
                    "slice": "top×soft",
                    "sport": sp,
                    "prop": prop_label,
                    "category": cat,
                    "n": len(ts),
                    "hr": ts["hit"].mean(),
                    "baseline_hr": base["hit"].mean(),
                    "lift_pp": (ts["hit"].mean() - base["hit"].mean()) * 100,
                }
            )

    if not rows_out:
        print("  (no prop slices met min-n)")
        return

    res = pd.DataFrame(rows_out).sort_values("lift_pp", ascending=False)
    print("\nBest lifts (bottom×tough or top×soft vs same prop baseline):")
    for _, r in res.head(15).iterrows():
        print(
            f"  {r['slice']:<14} {r['sport']:<6} {str(r['prop'])[:28]:<28} "
            f"HR={100*r['hr']:.1f}% base={100*r['baseline_hr']:.1f}% "
            f"lift {r['lift_pp']:+.1f}pp n={int(r['n'])}"
        )
    print("\nWorst lifts:")
    for _, r in res.tail(10).iterrows():
        print(
            f"  {r['slice']:<14} {r['sport']:<6} {str(r['prop'])[:28]:<28} "
            f"HR={100*r['hr']:.1f}% base={100*r['baseline_hr']:.1f}% "
            f"lift {r['lift_pp']:+.1f}pp n={int(r['n'])}"
        )

    report_dir = _REPO / "data" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    out_csv = report_dir / "player_tier_defense_backtest_per_prop.csv"
    res.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\nWrote {out_csv}")

    if sport_summary_rows:
        by_sport = pd.DataFrame(sport_summary_rows)
        sport_path = report_dir / "player_tier_defense_backtest_by_sport.csv"
        by_sport.to_csv(sport_path, index=False, encoding="utf-8-sig")
        print(f"Wrote {sport_path}")

    if direction_rows:
        dir_path = report_dir / "player_tier_defense_backtest_direction.csv"
        pd.DataFrame(direction_rows).to_csv(dir_path, index=False, encoding="utf-8-sig")
        print(f"Wrote {dir_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sport", default="", help="Filter one sport (NBA, WNBA, …)")
    ap.add_argument(
        "--from",
        dest="min_date",
        default="2026-05-06",
        metavar="DATE",
        help="Only graded files on/after this date (empty = all archives)",
    )
    ap.add_argument("--min-n", type=int, default=20)
    ap.add_argument("--days", type=int, default=0, help="Only last N graded files (0=all)")
    args = ap.parse_args()

    sport_f = args.sport.strip().upper() if args.sport else None
    days = args.days if args.days > 0 else None
    min_date_raw = str(args.min_date or "").strip()[:10]
    min_date = min_date_raw if min_date_raw else None

    print("Loading graded props…")
    raw = load_graded_with_defense(sport=sport_f, days=days, min_date=min_date)
    if raw.empty:
        print("No graded rows.", file=sys.stderr)
        return 1

    raw["sport"] = raw["sport"].map(_norm_sport)
    raw["direction"] = raw["direction"].map(_norm_dir)
    raw["pick_type"] = raw["pick_type"].map(_norm_pick)
    raw["prop_type"] = raw["prop_type"].map(_norm_prop_type)
    slate_dates = raw["file_date"].astype(str).str[:10].tolist()

    # Build PIT lookups
    print("Building point-in-time top/bottom rankings…")
    lookups: dict[str, dict] = {}
    nba_logs = _load_nba_logs()
    if not nba_logs.empty:
        cats = ("pts", "reb", "ast", "stl", "blk", "fg3m", "pra")
        nba_team_fn = lambda t: _team_lookup_key("NBA", t)
        lu = _build_pit_lookup_bball(
            nba_logs, slate_dates=slate_dates, categories=cats, team_key_fn=nba_team_fn
        )
        for sp in ("NBA", "NBA1H", "NBA1Q"):
            lookups[sp] = lu
        print(f"  NBA logs: {len(nba_logs):,} rows → {len(lu):,} lookup keys")
    wnba_logs = _load_wnba_logs()
    if not wnba_logs.empty:
        cats = ("pts", "reb", "ast", "stl", "blk", "fg3m", "pra")
        wnba_team_fn = lambda t: _team_lookup_key("WNBA", t)
        lu_w = _build_pit_lookup_bball(
            wnba_logs,
            slate_dates=slate_dates,
            categories=cats,
            team_key_fn=wnba_team_fn,
        )
        lookups["WNBA"] = lu_w
        print(f"  WNBA logs: {len(wnba_logs):,} rows → {len(lu_w):,} lookup keys")
    need_nhl = (not sport_f) or sport_f == "NHL"
    if need_nhl:
        nhl_logs = _load_nhl_logs()
        if nhl_logs.empty:
            print("  NHL: proporacle_ref.db has no skater rows; tags will be 0%", file=sys.stderr)
        else:
            lookups["NHL"] = _build_pit_lookup_nhl(nhl_logs, slate_dates=slate_dates)
            print(f"  NHL logs: {len(nhl_logs):,} rows → {len(lookups['NHL']):,} lookup keys")
    mlb_lu = _load_mlb_pit_lookup(slate_dates=slate_dates)
    if mlb_lu:
        lookups["MLB"] = mlb_lu
        print(f"  MLB lookup keys: {len(mlb_lu):,}")

    df = attach_player_tier(raw, lookups)
    run_backtest(df, min_n=max(5, int(args.min_n)), sport_filter=sport_f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
