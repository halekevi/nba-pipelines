#!/usr/bin/env python3
"""
Backtest daily cross-sport parlays from matchup-aligned Standard legs.

Categories (theory-aligned):
  A  top-3 producer × Weak/Below Avg DEF × OVER
  B  bottom-3 producer × Elite/Above Avg DEF × UNDER
  C  OVER × Weak/Below Avg DEF (def-only; soccer / untagged)
  D  UNDER × Elite/Above Avg DEF (def-only)

Parlay mode: best N legs per slate (default 6).

Bracket mode (--bracket): best OVER (Cat A) + best UNDER (Cat D) combinations:
  OVER pool — NBA/NBA1H/NBA1Q/MLB, player_tier=top, soft DEF, Standard OVER
  UNDER pool — SOCCER/WNBA/CBB/NBA, tough DEF, Standard UNDER
  Builds 2-leg (1+1), 3-leg (2+1 / 1+2), 4-leg (2+2); unique players, ≥2 sports.

Usage:
  py -3 scripts/backtest_matchup_parlay.py
  py -3 scripts/backtest_matchup_parlay.py --from 2026-05-06 --legs 6
  py -3 scripts/backtest_matchup_parlay.py --bracket --all-dates
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import backtest_player_tier_vs_defense as bt  # noqa: E402
from analyze_graded_history import (  # noqa: E402
    _GRADED_DIR,
    _norm_dir,
    _norm_pick,
    _norm_prop_type,
    _norm_sport,
    _parse_hit,
)
from utils.defense_tiers import normalize_def_tier_label  # noqa: E402

TOUGH = frozenset({"Elite", "Above Avg"})
SOFT = frozenset({"Weak", "Below Avg"})

BRACKET_OVER_SPORTS = frozenset({"NBA", "NBA1H", "NBA1Q", "MLB"})
BRACKET_UNDER_SPORTS = frozenset({"SOCCER", "WNBA", "CBB", "NBA"})

BRACKET_MODES: tuple[tuple[str, int, int], ...] = (
    ("bracket_2_1o1u", 1, 1),
    ("bracket_3_2o1u", 2, 1),
    ("bracket_3_1o2u", 1, 2),
    ("bracket_4_2o2u", 2, 2),
)

CAT_WEIGHT = {"A": 100.0, "B": 95.0, "C": 55.0, "D": 50.0}
TIER_BONUS = {"A": 8.0, "B": 6.0, "C": 3.0, "D": 0.0, "—": 0.0, "": 0.0}


def _rank_by_ml_prob(pool: pd.DataFrame) -> pd.DataFrame:
    if pool.empty:
        return pool
    out = pool.copy()
    out["_ml"] = pd.to_numeric(out["ml_prob"], errors="coerce").fillna(0.0)
    out["_ae"] = pd.to_numeric(out["abs_edge"], errors="coerce").fillna(0.0)
    return out.sort_values(["_ml", "_ae"], ascending=[False, False]).reset_index(drop=True)


def bracket_over_pool(day: pd.DataFrame) -> pd.DataFrame:
    """Cat A: top producer × soft DEF × OVER in bracket OVER sports."""
    mask = (
        day["pick_type"].eq("standard")
        & day["direction"].eq("OVER")
        & day["sport"].isin(BRACKET_OVER_SPORTS)
        & day["player_tier"].eq("top")
        & day["def_tier_norm"].isin(SOFT)
    )
    return _rank_by_ml_prob(day.loc[mask].copy())


def bracket_under_pool(day: pd.DataFrame) -> pd.DataFrame:
    """Cat D: UNDER × tough DEF in bracket UNDER sports."""
    mask = (
        day["pick_type"].eq("standard")
        & day["direction"].eq("UNDER")
        & day["sport"].isin(BRACKET_UNDER_SPORTS)
        & day["def_tier_norm"].isin(TOUGH)
    )
    return _rank_by_ml_prob(day.loc[mask].copy())


def _pick_unique_from_pool(pool: pd.DataFrame, n: int, used_players: set[str]) -> list[dict]:
    chosen: list[dict] = []
    if pool.empty or n <= 0:
        return chosen
    for _, row in pool.iterrows():
        pk = str(row.get("player_key", "")).strip()
        if not pk or pk in used_players:
            continue
        chosen.append(row.to_dict())
        used_players.add(pk)
        if len(chosen) >= n:
            break
    return chosen


def _sports_in_legs(legs: list[dict]) -> set[str]:
    return {str(l.get("sport", "")).strip().upper() for l in legs if str(l.get("sport", "")).strip()}


def select_bracket_legs(
    over_pool: pd.DataFrame,
    under_pool: pd.DataFrame,
    *,
    n_over: int,
    n_under: int,
    min_sports: int = 2,
) -> list[dict]:
    """Pick n_over + n_under legs; unique players; require ≥2 sports."""
    if over_pool.empty or under_pool.empty:
        return []

    over_rows = [row.to_dict() for _, row in over_pool.head(20).iterrows()]
    under_rows = [row.to_dict() for _, row in under_pool.head(20).iterrows()]

    def _combos(rows: list[dict], n: int) -> list[list[dict]]:
        if n <= 0:
            return [[]]
        out: list[list[dict]] = []
        for i in range(len(rows)):
            pk = str(rows[i].get("player_key", "")).strip()
            if not pk:
                continue
            for rest in _combos(rows[i + 1 :], n - 1):
                if all(str(x.get("player_key", "")).strip() != pk for x in rest):
                    out.append([rows[i], *rest])
        return out

    over_combos = _combos(over_rows, n_over)
    under_combos = _combos(under_rows, n_under)
    if not over_combos or not under_combos:
        return []

    best: list[dict] = []
    best_score = -1.0

    for oc in over_combos:
        for uc in under_combos:
            legs = oc + uc
            players = {str(l.get("player_key", "")).strip() for l in legs}
            if len(players) != len(legs):
                continue
            sports = _sports_in_legs(legs)
            if len(sports) < min_sports:
                continue
            score = sum(float(l.get("ml_prob") or 0) for l in legs)
            if score > best_score:
                best_score = score
                best = legs

    return best


def format_leg_detail(legs: list[dict]) -> str:
    parts: list[str] = []
    for leg in legs:
        hit = "W" if int(leg.get("hit", 0)) else "L"
        mlp = float(leg.get("ml_prob") or 0)
        parts.append(
            f"{leg.get('player', '')}|{leg.get('sport', '')}|{leg.get('direction', '')}"
            f"|{leg.get('prop_type', '')}|{hit}|ml={mlp:.2f}"
        )
    return " ; ".join(parts)


def run_bracket_backtest(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for date, day in df.groupby("file_date", sort=True):
        over_p = bracket_over_pool(day)
        under_p = bracket_under_pool(day)
        for mode, n_over, n_under in BRACKET_MODES:
            n_legs = n_over + n_under
            legs = select_bracket_legs(over_p, under_p, n_over=n_over, n_under=n_under)
            if len(legs) < n_legs:
                rows.append(
                    {
                        "date": date,
                        "mode": mode,
                        "legs_built": 0,
                        "legs_hit": 0,
                        "all_hit": 0,
                        "sports": "",
                        "leg_detail": "",
                        "reason": "insufficient_legs",
                    }
                )
                continue
            st = parlay_stats(legs)
            rows.append(
                {
                    "date": date,
                    "mode": mode,
                    "legs_built": n_legs,
                    "legs_hit": st["legs_hit"],
                    "all_hit": st["all_hit"],
                    "sports": ",".join(st["sports"]),
                    "leg_detail": format_leg_detail(legs),
                    "leg_hr": st["leg_hr"],
                    "reason": "",
                }
            )
    return pd.DataFrame(rows)


def summarize_bracket(results: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("  MATCHUP BRACKET BACKTEST (best OVER + best UNDER)")
    print("=" * 72)
    for mode, _, _ in BRACKET_MODES:
        sub = results[results["mode"] == mode]
        built = sub[sub["legs_built"] > 0]
        n_days = len(sub)
        n_built = len(built)
        n_legs = int(built["legs_built"].iloc[0]) if n_built else 0
        print(f"\n--- {mode} ---")
        print(f"  Slates: {n_days}  |  Brackets built: {n_built} ({100*n_built/max(n_days,1):.0f}%)")
        if built.empty:
            print("  (no brackets could be built)")
            continue
        all_hit = int(built["all_hit"].sum())
        avg_legs = float(built["legs_hit"].mean())
        leg_hr = float(built["leg_hr"].mean())
        print(f"  All-{n_legs} hit: {all_hit}/{n_built} = {100*all_hit/n_built:.1f}%")
        print(f"  Avg legs hit: {avg_legs:.2f} / {n_legs}")
        print(f"  Avg leg HR:   {100*leg_hr:.1f}%")
        p = leg_hr
        implied = p ** n_legs
        print(f"  Implied all-hit (indep.): {100*implied:.2f}%")

    built_any = results[results["legs_built"] > 0]
    if not built_any.empty:
        print("\n--- All-hit bracket days (any mode) ---")
        winners = built_any[built_any["all_hit"] == 1].sort_values(["date", "mode"])
        if winners.empty:
            print("  (none)")
        else:
            for _, r in winners.iterrows():
                detail = str(r["leg_detail"])
                suffix = "…" if len(detail) > 120 else ""
                print(
                    f"  {r['date']}  {r['mode']}  sports={r['sports']}  "
                    f"detail={detail[:120]}{suffix}"
                )


def print_bracket_pool_stats(df: pd.DataFrame) -> None:
    print("\n--- Bracket pool leg HR (Standard, all slates pooled) ---")
    parts: list[pd.DataFrame] = []
    for _, day in df.groupby("file_date", sort=True):
        parts.append(bracket_over_pool(day).assign(_side="OVER"))
        parts.append(bracket_under_pool(day).assign(_side="UNDER"))
    if not parts:
        print("  (empty)")
        return
    pool = pd.concat(parts, ignore_index=True)
    for side in ("OVER", "UNDER"):
        sub = pool[pool["_side"] == side]
        if sub.empty:
            print(f"  {side}: no rows")
            continue
        print(f"  {side} pool: HR={100*sub['hit'].mean():.1f}%  n={len(sub):,}")
        for sp, g in sub.groupby("sport"):
            if len(g) >= 5:
                print(f"    {sp:<8} HR={100*g['hit'].mean():.1f}%  n={len(g)}")


def load_graded_rich(*, min_date: str | None) -> pd.DataFrame:
    paths = sorted(p for p in _GRADED_DIR.glob("graded_props_*.json") if ".bak_" not in p.name)
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
            hit = _parse_hit(r.get("result"))
            if hit is None:
                continue
            edge = pd.to_numeric(r.get("edge"), errors="coerce")
            rows.append(
                {
                    "player": str(r.get("player", "")).strip(),
                    "player_key": str(r.get("player", "")).strip().lower(),
                    "team": str(r.get("team", "")).strip().upper(),
                    "sport": _norm_sport(r.get("sport")),
                    "prop_type": _norm_prop_type(r.get("prop")),
                    "prop_raw": str(r.get("prop", "")),
                    "pick_type": _norm_pick(r.get("pick_type")),
                    "direction": _norm_dir(r.get("direction") or r.get("over_under")),
                    "def_tier": normalize_def_tier_label(r.get("def_tier") or r.get("DEF_TIER") or "") or "",
                    "line": pd.to_numeric(r.get("line"), errors="coerce"),
                    "actual_value": pd.to_numeric(r.get("actual_value"), errors="coerce"),
                    "hit": int(hit),
                    "file_date": file_date,
                    "edge": edge,
                    "abs_edge": float(abs(edge)) if pd.notna(edge) else np.nan,
                    "ml_prob": pd.to_numeric(r.get("ml_prob"), errors="coerce"),
                    "tier": str(r.get("tier", "")).strip().upper() or "—",
                }
            )
    return pd.DataFrame(rows)


def tag_category(row: pd.Series) -> str:
    d = str(row.get("direction", "")).upper()
    dt = str(row.get("def_tier_norm", ""))
    pt = str(row.get("player_tier", ""))
    if d == "OVER" and pt == "top" and dt in SOFT:
        return "A"
    if d == "UNDER" and pt == "bottom" and dt in TOUGH:
        return "B"
    if d == "OVER" and dt in SOFT:
        return "C"
    if d == "UNDER" and dt in TOUGH:
        return "D"
    return ""


def leg_score(row: pd.Series) -> float:
    cat = str(row.get("matchup_cat", ""))
    if not cat:
        return -1e9
    w = CAT_WEIGHT.get(cat, 0.0)
    mlp = row.get("ml_prob")
    mlp_v = float(mlp) if pd.notna(mlp) else 0.52
    ae = row.get("abs_edge")
    ae_v = float(ae) if pd.notna(ae) else 0.0
    tier = str(row.get("tier", "—"))
    tb = TIER_BONUS.get(tier, 0.0)
    return w + 40.0 * mlp_v + 5.0 * ae_v + tb


def select_parlay_legs(pool: pd.DataFrame, n_legs: int, *, min_sports: int = 2) -> list[dict]:
    if pool.empty:
        return []
    work = pool.sort_values("_score", ascending=False).reset_index(drop=True)
    chosen: list[dict] = []
    players: set[str] = set()
    sports: set[str] = set()

    def _try_add(row: pd.Series) -> bool:
        pk = str(row.get("player_key", "")).strip()
        sp = str(row.get("sport", "")).strip().upper()
        if not pk or pk in players:
            return False
        chosen.append(row.to_dict())
        players.add(pk)
        if sp:
            sports.add(sp)
        return True

    # Pass 1: fill with cross-sport diversity — reserve slots for other sports when possible.
    for _, row in work.iterrows():
        if len(chosen) >= n_legs:
            break
        _try_add(row)

    if len(chosen) < n_legs:
        return []

    if len(sports) < min_sports:
        # Swap last leg for best unused leg from a different sport.
        base_sport = str(chosen[0].get("sport", "")).upper()
        for j, row in work.iterrows():
            sp = str(row.get("sport", "")).upper()
            pk = str(row.get("player_key", "")).strip()
            if not sp or sp == base_sport or pk in players:
                continue
            chosen[-1] = row.to_dict()
            sports = {str(r.get("sport", "")).upper() for r in chosen if r.get("sport")}
            break
        if len(sports) < min_sports:
            return []

    return chosen[:n_legs]


def parlay_stats(legs: list[dict]) -> dict:
    if not legs:
        return {}
    hits = [int(l["hit"]) for l in legs]
    n = len(legs)
    return {
        "n_legs": n,
        "legs_hit": sum(hits),
        "all_hit": int(all(hits)),
        "leg_hr": sum(hits) / n,
        "sports": sorted({str(l.get("sport", "")).upper() for l in legs if l.get("sport")}),
        "cats": [l.get("matchup_cat", "") for l in legs],
    }


def run_daily_backtest(
    df: pd.DataFrame,
    *,
    n_legs: int,
    modes: tuple[str, ...],
) -> pd.DataFrame:
    rows: list[dict] = []
    for date, day in df.groupby("file_date", sort=True):
        for mode in modes:
            if mode == "theory_ab":
                pool = day[day["matchup_cat"].isin(["A", "B"])].copy()
            elif mode == "theory_all":
                pool = day[day["matchup_cat"].isin(["A", "B", "C", "D"])].copy()
            elif mode == "theory_a_only":
                pool = day[day["matchup_cat"] == "A"].copy()
            elif mode == "premium_ad":
                pool = day[day["matchup_cat"].isin(["A", "D"])].copy()
            elif mode == "control_std":
                pool = day[day["pick_type"] == "standard"].copy()
                pool["matchup_cat"] = "CTL"
            else:
                continue
            pool = pool[pool["pick_type"] == "standard"].copy()
            if pool.empty:
                rows.append({"file_date": date, "mode": mode, "built": 0, "reason": "no_pool"})
                continue
            if mode == "control_std":
                pool["_score"] = pool["ml_prob"].fillna(0.5) * 100 + pool["abs_edge"].fillna(0) * 5
            else:
                pool["_score"] = pool.apply(leg_score, axis=1)
            legs = select_parlay_legs(pool, n_legs)
            if len(legs) < n_legs:
                rows.append({"file_date": date, "mode": mode, "built": 0, "reason": "insufficient_legs"})
                continue
            st = parlay_stats(legs)
            rows.append(
                {
                    "file_date": date,
                    "mode": mode,
                    "built": 1,
                    "all_hit": st["all_hit"],
                    "legs_hit": st["legs_hit"],
                    "leg_hr": st["leg_hr"],
                    "sports": ",".join(st["sports"]),
                    "cats": ",".join(st["cats"]),
                    "reason": "",
                }
            )
    return pd.DataFrame(rows)


def summarize(results: pd.DataFrame, *, n_legs: int) -> None:
    print("\n" + "=" * 72)
    print(f"  6-LEG CROSS-SPORT PARLAY BACKTEST (n_legs={n_legs})")
    print("=" * 72)
    for mode in results["mode"].unique():
        sub = results[results["mode"] == mode]
        built = sub[sub["built"] == 1]
        n_days = len(sub)
        n_built = len(built)
        print(f"\n--- {mode} ---")
        print(f"  Slates: {n_days}  |  Parlays built: {n_built} ({100*n_built/max(n_days,1):.0f}%)")
        if built.empty:
            print("  (no parlays could be built)")
            continue
        all_hit = int(built["all_hit"].sum())
        avg_legs = float(built["legs_hit"].mean())
        leg_hr = float(built["leg_hr"].mean())
        print(f"  All-{n_legs} hit: {all_hit}/{n_built} = {100*all_hit/n_built:.1f}%")
        print(f"  Avg legs hit: {avg_legs:.2f} / {n_legs}")
        print(f"  Avg leg HR:   {100*leg_hr:.1f}%")
        # implied if independent
        p = leg_hr
        implied = p ** n_legs
        print(f"  Implied all-hit (indep.): {100*implied:.2f}%")
        by_cat = built["cats"].str.split(",").explode().value_counts()
        print(f"  Leg category mix: {dict(by_cat)}")


def category_leg_hr(df: pd.DataFrame) -> None:
    print("\n--- Per-category Standard leg HR (all slates pooled) ---")
    std = df[df["pick_type"] == "standard"].copy()
    for cat, label in [
        ("A", "top×soft OVER"),
        ("B", "bottom×tough UNDER"),
        ("C", "OVER×soft (def-only)"),
        ("D", "UNDER×tough (def-only)"),
    ]:
        sub = std[std["matchup_cat"] == cat]
        if sub.empty:
            print(f"  {cat} {label}: no rows")
            continue
        print(f"  {cat} {label}: HR={100*sub['hit'].mean():.1f}%  n={len(sub):,}")


def build_lookups(raw: pd.DataFrame) -> dict[str, dict]:
    slate_dates = raw["file_date"].astype(str).str[:10].tolist()
    lookups: dict[str, dict] = {}
    nba_logs = bt._load_nba_logs()
    if not nba_logs.empty:
        cats = ("pts", "reb", "ast", "stl", "blk", "fg3m", "pra")
        lu = bt._build_pit_lookup_bball(
            nba_logs,
            slate_dates=slate_dates,
            categories=cats,
            team_key_fn=lambda t: bt._team_lookup_key("NBA", t),
        )
        for sp in ("NBA", "NBA1H", "NBA1Q"):
            lookups[sp] = lu
    wnba_logs = bt._load_wnba_logs()
    if not wnba_logs.empty:
        lookups["WNBA"] = bt._build_pit_lookup_bball(
            wnba_logs,
            slate_dates=slate_dates,
            categories=("pts", "reb", "ast", "stl", "blk", "fg3m", "pra"),
            team_key_fn=lambda t: bt._team_lookup_key("WNBA", t),
        )
    nhl_logs = bt._load_nhl_logs()
    if not nhl_logs.empty:
        lookups["NHL"] = bt._build_pit_lookup_nhl(nhl_logs, slate_dates=slate_dates)
    mlb_lu = bt._load_mlb_pit_lookup(slate_dates=slate_dates)
    if mlb_lu:
        lookups["MLB"] = mlb_lu
    return lookups


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="min_date", default="2026-05-06", metavar="DATE")
    ap.add_argument("--legs", type=int, default=6)
    ap.add_argument("--all-dates", action="store_true", help="Use full graded archive")
    ap.add_argument(
        "--bracket",
        action="store_true",
        help="Run matchup bracket backtest (best OVER + UNDER) only",
    )
    args = ap.parse_args()

    min_date = None if args.all_dates else str(args.min_date).strip()[:10]

    print("Loading graded props…")
    raw = load_graded_rich(min_date=min_date)
    if raw.empty:
        print("No rows.", file=sys.stderr)
        return 1

    print("Building PIT player tiers…")
    lookups = build_lookups(raw)
    df = bt.attach_player_tier(raw, lookups)
    df["def_tier_norm"] = df["def_tier"].map(lambda x: x or "(missing)")
    df = df[df["def_tier_norm"] != "(missing)"].copy()
    df["matchup_cat"] = df.apply(tag_category, axis=1)

    print(f"Rows with def_tier: {len(df):,}  dates: {df['file_date'].min()} → {df['file_date'].max()}")

    if args.bracket:
        print_bracket_pool_stats(df)
        bracket_results = run_bracket_backtest(df)
        summarize_bracket(bracket_results)
        out = _REPO / "data" / "reports" / "matchup_bracket_backtest.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        bracket_results.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\nWrote {out}")
        return 0

    category_leg_hr(df)

    modes = ("theory_ab", "theory_all", "theory_a_only", "premium_ad", "control_std")
    results = run_daily_backtest(df, n_legs=int(args.legs), modes=modes)
    summarize(results, n_legs=int(args.legs))

    # Show best and worst parlay days for theory_all
    built = results[(results["mode"] == "theory_all") & (results["built"] == 1)].copy()
    if not built.empty:
        print("\n--- Sample theory_all parlay days ---")
        print("Best (most legs hit):")
        for _, r in built.sort_values("legs_hit", ascending=False).head(5).iterrows():
            print(
                f"  {r['file_date']}  {int(r['legs_hit'])}/{args.legs} hit  "
                f"sports={r['sports']}  cats={r['cats']}"
            )
        print("All-hit days:")
        winners = built[built["all_hit"] == 1]
        if winners.empty:
            print("  (none)")
        else:
            for _, r in winners.iterrows():
                print(
                    f"  {r['file_date']}  sports={r['sports']}  cats={r['cats']}"
                )

    out = _REPO / "data" / "reports" / "matchup_parlay_backtest.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
