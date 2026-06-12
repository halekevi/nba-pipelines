#!/usr/bin/env python3
"""
Top-N players per prop category per team, with historical performance vs opponent
defensive rank (league-scoped team defense from soccer_defense_summary.csv).

Run (from repo root):
  py -3.14 Sports/Soccer/scripts/analyze_top_players_vs_defense.py
  py -3.14 Sports/Soccer/scripts/analyze_top_players_vs_defense.py --slate-date 2026-06-12
  py -3.14 Sports/Soccer/scripts/analyze_top_players_vs_defense.py --min-games 5 --top-n 3
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[3]
_SOC = Path(__file__).resolve().parents[1]
_SCRIPTS = Path(__file__).resolve().parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from enrich_soccer_step8_defense import normalize_opp  # noqa: E402

DB_PATH = _REPO / "data" / "cache" / "proporacle_ref.db"
ABBR_CACHE_PATH = _SOC / "data" / "espn_abbr_to_pp.json"
ESPN_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/teams?limit=200"
STATS_CACHE = _SOC / "cache" / "soccer_stats_cache.csv"

CATEGORIES: tuple[str, ...] = (
    "shots_on_target",
    "shots",
    "assists",
    "goals",
    "saves",
    "tackles",
    "passes",
)

STAT_COL: dict[str, str] = {
    "goals": "g",
    "shots": "sh",
    "shots_on_target": "sog",
    "assists": "a",
    "saves": "sv",
    "tackles": "tk",
    "passes": "pa",
}

SLUG_TO_DEF_LEAGUE: dict[str, str] = {
    "eng.1": "EPL",
    "eng.2": "Championship",
    "uefa.champions": "UCL",
    "usa.1": "MLS",
    "esp.1": "La Liga",
    "ger.1": "Bundesliga",
    "ita.1": "Serie A",
    "fra.1": "Ligue 1",
    "arg.1": "Argentina",
    "bra.1": "Brazil",
    "mex.1": "Liga MX",
    "ned.1": "Eredivisie",
    "por.1": "Primeira Liga",
    "sco.1": "Scottish Prem",
    "usa.nwsl": "NWSL",
    "aus.1": "A-League",
    "eng.w.1": "WSL",
    "tur.1": "Süper Lig",
    "gre.1": "Super League Greece",
    "uefa.nations": "UEFA Nations",
}

try:
    from soccer_defense_report import LEAGUES, _pp_name  # noqa: E402
except Exception:
    LEAGUES = tuple(SLUG_TO_DEF_LEAGUE.items())  # type: ignore[misc]

    def _pp_name(display_name: str) -> str:  # type: ignore[misc]
        return str(display_name or "").upper().strip()

TOP_N_DEFAULT = 3
BOTTOM_N_DEFAULT = 3
MIN_GAMES_DEFAULT = 5


def _norm_name(s: object) -> str:
    t = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _def_league_label(league_slug: object) -> str:
    raw = str(league_slug or "").strip()
    if not raw:
        return ""
    return SLUG_TO_DEF_LEAGUE.get(raw.lower(), raw)


def _def_buckets(rank: float, n_teams: int) -> str:
    if pd.isna(rank) or n_teams < 3:
        return "unknown"
    r = int(rank)
    elite_cut = max(1, int(np.ceil(n_teams * 0.33)))
    weak_cut = max(elite_cut + 1, int(np.floor(n_teams * 0.67)))
    if r <= elite_cut:
        return "elite"
    if r >= weak_cut:
        return "weak"
    return "mid"


def _load_defense(path: Path) -> pd.DataFrame:
    d = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    d["pp_name_key"] = d["pp_name"].astype(str).str.upper().str.strip()
    d["team_name_key"] = d.get("team_name", d["pp_name"]).astype(str).map(
        lambda x: normalize_opp(x).upper() if x else ""
    )
    d["OVERALL_DEF_RANK"] = pd.to_numeric(d["OVERALL_DEF_RANK"], errors="coerce")
    d["league"] = d["league"].astype(str).str.strip()
    return d


def _build_pp_lookup(defense: pd.DataFrame) -> dict[tuple[str, str], str]:
    """Map (def_league, normalized team token) -> pp_name_key."""
    out: dict[tuple[str, str], str] = {}
    for row in defense.itertuples(index=False):
        league = str(getattr(row, "league", "") or "").strip()
        pp = str(getattr(row, "pp_name_key", "") or "").strip()
        if not league or not pp:
            continue
        out[(league, pp)] = pp
        tn = str(getattr(row, "team_name_key", "") or "").strip()
        if tn:
            out[(league, tn)] = pp
            out[(league, normalize_opp(tn).upper())] = pp
    out.update(_load_espn_abbr_lookup(defense))
    return out


def _load_espn_abbr_lookup(defense: pd.DataFrame) -> dict[tuple[str, str], str]:
    """ESPN 3-letter team code -> pp_name within each defense league."""
    if ABBR_CACHE_PATH.is_file():
        try:
            raw = json.loads(ABBR_CACHE_PATH.read_text(encoding="utf-8"))
            out: dict[tuple[str, str], str] = {}
            for key, pp in raw.items():
                if "|" in str(key):
                    lg, ab = str(key).split("|", 1)
                    out[(lg, ab)] = str(pp)
            if out:
                return out
        except Exception:
            pass
    return _fetch_espn_abbr_lookup(defense)


def _fetch_espn_abbr_lookup(defense: pd.DataFrame) -> dict[tuple[str, str], str]:
    import requests

    pp_by_league: dict[str, set[str]] = {}
    for row in defense.itertuples(index=False):
        lg = str(getattr(row, "league", "") or "").strip()
        pp = str(getattr(row, "pp_name_key", "") or "").strip()
        if lg and pp:
            pp_by_league.setdefault(lg, set()).add(pp)

    lookup: dict[tuple[str, str], str] = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (PropORACLE)",
        "Accept": "application/json",
    }
    for slug, label in LEAGUES:
        known = pp_by_league.get(label, set())
        if not known:
            continue
        try:
            time.sleep(0.25)
            r = requests.get(ESPN_TEAMS_URL.format(slug=slug), headers=headers, timeout=25)
            if r.status_code != 200:
                continue
            data = r.json()
        except Exception:
            continue
        team_nodes = (data.get("sports") or [{}])[0].get("leagues", [{}])[0].get("teams", [])
        if not team_nodes:
            team_nodes = [{"team": t} for t in (data.get("teams") or [])]
        for node in team_nodes:
            team = node.get("team", node)
            abbr = str(team.get("abbreviation", "") or "").upper().strip()
            display = str(team.get("displayName", team.get("name", "")) or "").strip()
            if not abbr or not display:
                continue
            pp = _pp_name(display)
            if pp in known:
                lookup[(label, abbr)] = pp
    if lookup:
        ABBR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        serial = {f"{lg}|{ab}": pp for (lg, ab), pp in lookup.items()}
        ABBR_CACHE_PATH.write_text(json.dumps(serial, indent=2), encoding="utf-8")
    return lookup


def _resolve_pp_name(team_raw: object, league_slug: object, lookup: dict[tuple[str, str], str]) -> str:
    league = _def_league_label(league_slug)
    if not league:
        return str(team_raw or "").upper().strip()
    candidates = [
        str(team_raw or "").upper().strip(),
        normalize_opp(str(team_raw or "")).upper(),
    ]
    for cand in candidates:
        if not cand:
            continue
        hit = lookup.get((league, cand))
        if hit:
            return hit
    return candidates[0] if candidates[0] else ""


def _load_game_logs(db_path: Path, stats_cache: Path, min_games: int) -> pd.DataFrame:
    if db_path.is_file():
        con = sqlite3.connect(db_path)
        try:
            logs = pd.read_sql(
                """
                SELECT game_date, event_id, league, home_team, away_team,
                       player, team, espn_player_id,
                       g, sh, sog, a, sv, pa, tk
                FROM soccer
                WHERE player IS NOT NULL AND TRIM(player) != ''
                """,
                con,
            )
        finally:
            con.close()
        if not logs.empty:
            for col in STAT_COL.values():
                if col in logs.columns:
                    logs[col] = pd.to_numeric(logs[col], errors="coerce")
            logs["PLAYER_NORM"] = logs["player"].map(_norm_name)
            logs["game_date"] = pd.to_datetime(logs["game_date"], errors="coerce")
            logs["team"] = logs["team"].astype(str).str.upper().str.strip()
            logs["home_team"] = logs["home_team"].astype(str).str.upper().str.strip()
            logs["away_team"] = logs["away_team"].astype(str).str.upper().str.strip()
            logs["opp_team"] = np.where(
                logs["team"] == logs["home_team"],
                logs["away_team"],
                np.where(logs["team"] == logs["away_team"], logs["home_team"], ""),
            )
            return logs

    if not stats_cache.is_file():
        return pd.DataFrame()

    cache = pd.read_csv(stats_cache, low_memory=False)
    cache.columns = [c.strip() for c in cache.columns]
    if cache.empty:
        return pd.DataFrame()
    # Long cache without team — cannot build opponent splits without DB.
    print(f"WARN: {db_path} missing; {stats_cache} has no team/opp columns — need DB.")
    return pd.DataFrame()


def _attach_defense_context(logs: pd.DataFrame, defense: pd.DataFrame) -> pd.DataFrame:
    lookup = _build_pp_lookup(defense)
    out = logs.copy()
    out["def_league"] = out["league"].map(_def_league_label)
    out["team_pp"] = [
        _resolve_pp_name(t, lg, lookup) for t, lg in zip(out["team"], out["league"])
    ]
    out["opp_pp"] = [
        _resolve_pp_name(t, lg, lookup) for t, lg in zip(out["opp_team"], out["league"])
    ]

    def _rank_row(row) -> float:
        lg = str(row.get("def_league") or "")
        opp = str(row.get("opp_pp") or "")
        if not lg or not opp:
            return np.nan
        sub = defense[(defense["league"] == lg) & (defense["pp_name_key"] == opp)]
        if sub.empty:
            sub = defense[defense["pp_name_key"] == opp]
        if sub.empty:
            return np.nan
        return float(pd.to_numeric(sub.iloc[0]["OVERALL_DEF_RANK"], errors="coerce"))

    out["OVERALL_DEF_RANK"] = out.apply(_rank_row, axis=1)
    league_counts = defense.groupby("league")["pp_name_key"].nunique().to_dict()

    def _bucket_row(row) -> str:
        lg = str(row.get("def_league") or "")
        n = int(league_counts.get(lg, 0) or 0)
        if n < 3:
            n = int(defense["OVERALL_DEF_RANK"].notna().sum()) or 15
        return _def_buckets(row.get("OVERALL_DEF_RANK"), n)

    out["def_bucket"] = out.apply(_bucket_row, axis=1)
    out.loc[out["OVERALL_DEF_RANK"].isna(), "def_bucket"] = "unknown"
    tier_map = defense.set_index("pp_name_key")["DEF_TIER"].to_dict() if "DEF_TIER" in defense.columns else {}
    out["DEF_TIER"] = out["opp_pp"].map(lambda k: tier_map.get(k, ""))
    return out


def _player_baselines(logs: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for cat in CATEGORIES:
        col = STAT_COL.get(cat)
        if not col or col not in logs.columns:
            continue
        for pnorm, grp in logs.groupby("PLAYER_NORM", sort=False):
            g = grp.sort_values("game_date")
            vals = pd.to_numeric(g[col], errors="coerce")
            baseline = vals.expanding(min_periods=3).mean().shift(1)
            for idx, base in zip(g.index, baseline):
                actual = vals.loc[idx]
                delta = actual - base if pd.notna(base) and pd.notna(actual) else np.nan
                rows.append({"idx": idx, "category": cat, "baseline": base, "actual": actual, "delta": delta})
    if not rows:
        return logs
    wide = pd.DataFrame(rows)
    pivot = wide.pivot_table(index="idx", columns="category", values="delta", aggfunc="first")
    pivot.columns = [f"delta_{c}" for c in pivot.columns]
    return logs.join(pivot, how="left")


def build_leaderboard_and_splits(
    logs: pd.DataFrame,
    *,
    top_n: int,
    min_games: int,
) -> pd.DataFrame:
    if logs.empty:
        return logs

    season_avgs: list[pd.DataFrame] = []
    for cat in CATEGORIES:
        col = STAT_COL.get(cat)
        if not col or col not in logs.columns:
            continue
        agg = (
            logs.groupby(["PLAYER_NAME", "PLAYER_NORM", "team_pp", "def_league"], as_index=False)
            .agg(season_avg=(col, "mean"), games=(col, "count"))
        )
        agg["category"] = cat
        agg["prop_norm"] = cat
        season_avgs.append(agg)
    if not season_avgs:
        return pd.DataFrame()
    avgs = pd.concat(season_avgs, ignore_index=True)
    avgs["season_avg"] = pd.to_numeric(avgs["season_avg"], errors="coerce")
    avgs["games"] = pd.to_numeric(avgs["games"], errors="coerce").fillna(0)
    avgs = avgs[avgs["games"] >= min_games]
    avgs["team_slate"] = avgs["team_pp"]
    avgs["TEAM"] = avgs["team_pp"]

    top_rows: list[pd.DataFrame] = []
    bottom_rows: list[pd.DataFrame] = []
    for (team_key, league, cat), grp in avgs.groupby(["team_pp", "def_league", "category"], sort=False):
        if not team_key:
            continue
        top = grp.nlargest(top_n, "season_avg").copy()
        top["rank_on_team"] = range(1, len(top) + 1)
        top["leader_side"] = "top"
        top_rows.append(top)
        bottom_n = min(BOTTOM_N_DEFAULT, len(grp))
        if bottom_n > 0:
            bottom = grp.nsmallest(bottom_n, "season_avg").copy()
            bottom["rank_on_team"] = range(1, len(bottom) + 1)
            bottom["leader_side"] = "bottom"
            bottom_rows.append(bottom)
    leaders = pd.concat(top_rows + bottom_rows, ignore_index=True) if top_rows else pd.DataFrame()

    split_rows: list[dict] = []
    logs_delta = _player_baselines(logs)
    for cat in CATEGORIES:
        dcol = f"delta_{cat}"
        if dcol not in logs_delta.columns:
            continue
        name_col = "PLAYER_NAME" if "PLAYER_NAME" in logs_delta.columns else "player"
        sub = logs_delta[
            [
                "PLAYER_NORM",
                name_col,
                "team_pp",
                "def_league",
                "game_date",
                "opp_pp",
                "OVERALL_DEF_RANK",
                "DEF_TIER",
                "def_bucket",
                dcol,
            ]
        ].copy()
        sub = sub.rename(columns={name_col: "PLAYER_NAME", dcol: "delta"})
        sub["category"] = cat
        sub["prop_norm"] = cat
        sub = sub.dropna(subset=["delta"])
        for (pnorm, team), g in sub.groupby(["PLAYER_NORM", "team_pp"], sort=False):
            elite = g[g["def_bucket"] == "elite"]["delta"]
            weak = g[g["def_bucket"] == "weak"]["delta"]
            mid = g[g["def_bucket"] == "mid"]["delta"]
            all_d = g["delta"]
            rank_corr = np.nan
            if len(g) >= 5 and g["OVERALL_DEF_RANK"].notna().sum() >= 5:
                rank_corr = float(g[["OVERALL_DEF_RANK", "delta"]].corr().iloc[0, 1])
            split_rows.append(
                {
                    "PLAYER_NORM": pnorm,
                    "PLAYER_NAME": g["PLAYER_NAME"].iloc[0],
                    "TEAM": team,
                    "league": g["def_league"].iloc[0] if "def_league" in g.columns else "",
                    "category": cat,
                    "prop_norm": cat,
                    "def_bucket": g["def_bucket"].mode().iloc[0] if not g["def_bucket"].mode().empty else "",
                    "n_games": len(g),
                    "n": len(g),
                    "n_elite": len(elite),
                    "n_weak": len(weak),
                    "over_rate": float((all_d > 0).mean()) if len(all_d) else np.nan,
                    "avg_delta_all": float(all_d.mean()) if len(all_d) else np.nan,
                    "avg_delta_vs_elite": float(elite.mean()) if len(elite) else np.nan,
                    "avg_delta_vs_weak": float(weak.mean()) if len(weak) else np.nan,
                    "avg_delta_vs_mid": float(mid.mean()) if len(mid) else np.nan,
                    "def_rank_corr": rank_corr,
                    "weak_over_rate": float((weak > 0).mean()) if len(weak) else np.nan,
                    "elite_over_rate": float((elite > 0).mean()) if len(elite) else np.nan,
                }
            )

    splits = pd.DataFrame(split_rows)
    if leaders.empty or splits.empty:
        return leaders

    out = leaders.merge(
        splits,
        on=["PLAYER_NORM", "TEAM", "category", "prop_norm"],
        how="left",
        suffixes=("", "_split"),
    )
    out["def_boost"] = out["avg_delta_vs_weak"] - out["avg_delta_vs_elite"]
    out["def_boost_hist"] = out["def_boost"]
    out["overperform_vs_weak"] = (
        (out["def_boost"] > 0.5)
        & (out["n_weak"].fillna(0) >= 2)
        & (out["weak_over_rate"].fillna(0) >= 0.55)
    )
    out["fades_vs_elite"] = (
        (out["avg_delta_vs_elite"].fillna(0) < -0.3)
        & (out["n_elite"].fillna(0) >= 2)
    )
    out["player"] = out.get("PLAYER_NAME", out.get("PLAYER_NAME_split", ""))
    return out.sort_values(["team_slate", "category", "leader_side", "rank_on_team"])


def _resolve_slate_path(repo: Path, slate_date: str) -> Path | None:
    if not slate_date:
        return None
    candidates = [
        repo / "outputs" / slate_date / "soccer" / "step6_soccer_role_context.csv",
        repo / "outputs" / slate_date / "soccer" / "step5_soccer_hit_rates.csv",
        repo / "outputs" / slate_date / "soccer" / "step8_soccer_direction_clean.csv",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _col_series(df: pd.DataFrame, *names: str, default: str = "") -> pd.Series:
    for name in names:
        if name in df.columns:
            return df[name]
    return pd.Series(default, index=df.index)


def attach_slate(leaders: pd.DataFrame, slate_path: Path) -> pd.DataFrame:
    if leaders.empty or not slate_path or not slate_path.is_file():
        return leaders
    if slate_path.suffix.lower() in (".xlsx", ".xls"):
        slate = pd.read_excel(slate_path, dtype=str)
    else:
        slate = pd.read_csv(slate_path, dtype=str, encoding="utf-8-sig")
    slate = slate.copy()
    slate["player_norm"] = slate.get("player", "").map(_norm_name)
    if "prop_norm" not in slate.columns and "prop_type" in slate.columns:
        slate["prop_norm"] = slate["prop_type"].astype(str).str.lower()
    slate["prop_norm"] = slate.get("prop_norm", "").astype(str).str.lower().str.strip()

    pick_cols = [
        c
        for c in (
            "player",
            "team",
            "opp_team",
            "prop_norm",
            "line",
            "bet_direction",
            "final_bet_direction",
            "rank_score",
            "edge",
            "tier",
            "OVERALL_DEF_RANK",
            "DEF_TIER",
            "line_hit_rate_over_ou_5",
            "stat_last5_avg",
        )
        if c in slate.columns
    ]
    slate_sub = slate[pick_cols + ["player_norm"]].copy()
    slate_sub = slate_sub.rename(
        columns={"OVERALL_DEF_RANK": "slate_opp_def_rank", "DEF_TIER": "slate_opp_def_tier"}
    )

    merged = leaders.merge(
        slate_sub,
        left_on=["PLAYER_NORM", "category"],
        right_on=["player_norm", "prop_norm"],
        how="left",
        suffixes=("", "_slate"),
    )
    merged["on_slate"] = _col_series(merged, "player").notna()
    merged["slate_edge_signal"] = False

    opp_rank = pd.to_numeric(merged.get("slate_opp_def_rank"), errors="coerce")
    direction = _col_series(merged, "final_bet_direction", "bet_direction").astype(str).str.upper()
    boost = merged["overperform_vs_weak"].fillna(False)
    weak_opp = opp_rank >= np.nanpercentile(opp_rank.dropna(), 67) if opp_rank.notna().any() else False
    tier = merged.get("slate_opp_def_tier", pd.Series("", index=merged.index)).astype(str)
    weak_tier = tier.isin(["Weak", "Below Avg", "WEAK", "BELOW AVG"])
    merged["slate_edge_signal"] = (
        boost & merged["on_slate"] & direction.eq("OVER") & (weak_opp | weak_tier)
    )
    return merged


def _print_summary(df: pd.DataFrame) -> None:
    if df.empty:
        print("No rows produced.")
        return
    prop_col = "prop_norm" if "prop_norm" in df.columns else "category"
    print(f"Teams: {df['team_slate'].nunique()} | Props: {df[prop_col].nunique()} | Rows: {len(df)}")
    if "def_bucket" in df.columns:
        print("Def buckets:", df["def_bucket"].value_counts().to_dict())
    boosters = df[df["overperform_vs_weak"].fillna(False)]
    print(f"Historical weak-D boosters (top-{TOP_N_DEFAULT} per team×prop): {len(boosters)}")
    if "slate_edge_signal" in df.columns:
        sig = df[df["slate_edge_signal"].fillna(False)]
        print(f"Slate OVER + weak opp + def boost: {len(sig)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Soccer top-N per team vs opponent defense ranks")
    ap.add_argument("--defense", default=str(_SOC / "cache" / "soccer_defense_summary.csv"))
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--stats-cache", default=str(STATS_CACHE))
    ap.add_argument("--top-n", type=int, default=TOP_N_DEFAULT)
    ap.add_argument("--min-games", type=int, default=MIN_GAMES_DEFAULT)
    ap.add_argument("--slate-date", default="", help="YYYY-MM-DD; writes _slate.csv when step6 exists")
    ap.add_argument("--slate", default="", help="Optional explicit slate CSV/XLSX")
    ap.add_argument("--out", default=str(_SOC / "data" / "soccer_top3_vs_defense.csv"))
    args = ap.parse_args()

    defense = _load_defense(Path(args.defense))
    logs = _load_game_logs(Path(args.db), Path(args.stats_cache), args.min_games)
    if logs.empty:
        print("No soccer game logs available.")
        return

    logs = logs.rename(columns={"player": "PLAYER_NAME"})
    logs = _attach_defense_context(logs, defense)
    logs = logs[logs["opp_pp"].astype(str).str.len() > 0].copy()

    result = build_leaderboard_and_splits(logs, top_n=args.top_n, min_games=args.min_games)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"Wrote {out} ({len(result)} rows)")

    slate_path = Path(args.slate) if args.slate else None
    if slate_path is None and args.slate_date:
        slate_path = _resolve_slate_path(_REPO, args.slate_date.strip()[:10])
    if slate_path and slate_path.is_file():
        slate_view = attach_slate(result, slate_path)
        slate_out = out.with_name(out.stem + "_slate" + out.suffix)
        slate_view.to_csv(slate_out, index=False, encoding="utf-8-sig")
        print(f"Wrote {slate_out} ({len(slate_view)} rows)")
        _print_summary(slate_view)
    else:
        if args.slate_date:
            print(f"No slate file for {args.slate_date} — skipped _slate.csv")
        _print_summary(result)


if __name__ == "__main__":
    main()
