#!/usr/bin/env python3
"""
Cross-sport ranked ticket generator (2–4 legs) using empirical payout EV from combined_slate_tickets.

  py -3.14 scripts/build_ultimate_tickets.py --date 2026-04-11 --mode balanced

score_to_hit_prob matches fetch_prizepicks_payouts.py (no import — avoids playwright dependency).
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from combined_slate_tickets import compute_ticket_ev  # noqa: E402

# ── Mirrors fetch_prizepicks_payouts.score_to_hit_prob (avoid importing playwright stack) ──
MIN_REALISTIC_HIT_PROB = 0.50


def score_to_hit_prob(score: Any, pick_type: str) -> float:
    pt = str(pick_type or "").strip().lower()
    if pt == "goblin":
        ceiling = 0.82
    elif pt == "demon":
        ceiling = 0.65
    else:
        ceiling = 0.72
    try:
        s = float(score)
    except Exception:
        s = 0.0
    s = max(0.0, min(1.0, s))
    prob = MIN_REALISTIC_HIT_PROB + (s * (ceiling - MIN_REALISTIC_HIT_PROB))
    return round(prob, 4)


def find_col(df: pd.DataFrame, names: list[str]) -> str | None:
    for c in names:
        if c in df.columns:
            return c
        matches = [col for col in df.columns if str(col).strip().lower() == str(c).strip().lower()]
        if matches:
            return matches[0]
    return None


def _norm_text(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower().replace("_", " "))


def _line_key(v: Any) -> str:
    try:
        return f"{float(v):.3f}"
    except Exception:
        return ""


def _to_sheet_df(path: Path) -> pd.DataFrame:
    xls = pd.ExcelFile(path)
    sheet = "ALL" if "ALL" in xls.sheet_names else xls.sheet_names[0]
    return pd.read_excel(path, sheet_name=sheet)


def norm_pick_type(pt: str) -> str:
    s = str(pt or "").lower()
    if "goblin" in s:
        return "goblin"
    if "demon" in s:
        return "demon"
    return "standard"


def step8_path_for_sport(sport: str, date_str: str) -> Path | None:
    od = ROOT / "outputs" / date_str
    candidates: list[Path] = {
        "NBA": [
            od / f"step8_all_direction_clean_{date_str}.xlsx",
            ROOT / "NBA" / "data" / "outputs" / "step8_all_direction_clean.xlsx",
        ],
        "NHL": [
            od / f"step8_nhl_direction_clean_{date_str}.xlsx",
            ROOT / "NHL" / "outputs" / "step8_nhl_direction_clean.xlsx",
        ],
        "Soccer": [
            od / f"step8_soccer_direction_clean_{date_str}.xlsx",
            ROOT / "Soccer" / "outputs" / "step8_soccer_direction_clean.xlsx",
        ],
        "MLB": [
            od / f"step8_mlb_direction_clean_{date_str}.xlsx",
            ROOT / "MLB" / "step8_mlb_direction_clean.xlsx",
            ROOT / "MLB" / "outputs" / "step8_mlb_direction_clean.xlsx",
        ],
    }.get(sport, [])
    for p in candidates:
        if p.is_file():
            return p
    return None


def step1_path_for_sport(sport: str) -> Path | None:
    p = {
        "NBA": ROOT / "NBA" / "data" / "outputs" / "step1_pp_props_today.csv",
        "NHL": ROOT / "NHL" / "outputs" / "step1_nhl_props.csv",
        "Soccer": ROOT / "Soccer" / "outputs" / "step1_soccer_props.csv",
        "MLB": ROOT / "MLB" / "step1_mlb_props.csv",
    }.get(sport)
    return p if p and p.is_file() else None


def load_sport_legs(
    sport: str,
    date_str: str,
    max_candidates: int,
) -> list[dict[str, Any]]:
    step8 = step8_path_for_sport(sport, date_str)
    step1 = step1_path_for_sport(sport)
    if not step8:
        return []
    df8 = _to_sheet_df(step8)
    tier_expected = ["Tier", "tier", "TIER"]
    score_expected = ["blended_score", "Blended Score", "Rank Score", "rank_score", "score"]
    dir_expected = ["Direction", "direction", "final_bet_direction", "Bet Direction", "Dir"]
    player_expected = ["Player", "player_name", "player", "Name"]
    prop_expected = ["Prop", "prop_type", "Prop Type", "prop"]
    line_expected = ["Line", "line_score", "Line Score", "line"]
    picktype_expected = ["Pick Type", "pick_type", "PickType"]
    team_expected = ["team", "Team"]

    p_col8 = find_col(df8, player_expected)
    t_col8 = find_col(df8, team_expected)
    prop_col8 = find_col(df8, prop_expected)
    line_col8 = find_col(df8, line_expected)
    dir_col8 = find_col(df8, dir_expected)
    tier_col8 = find_col(df8, tier_expected)
    blend_col8 = find_col(df8, score_expected)
    pick_type8 = find_col(df8, picktype_expected)

    if not all([p_col8, prop_col8, line_col8, dir_col8, tier_col8, blend_col8]):
        return []

    idx: dict[tuple[str, str, str, str], dict] = {}
    if step1:
        df1 = pd.read_csv(step1, low_memory=False)
        p_col1 = find_col(df1, ["player", "player_name", "name", "Player"])
        t_col1 = find_col(df1, ["team", "Team"])
        prop_col1 = find_col(df1, ["prop_type", "prop", "Prop", "Prop Type", "stat_type", "Stat Type"])
        line_col1 = find_col(df1, ["line", "line_score", "Line"])
        pick_col1 = find_col(df1, ["pick_type", "Pick Type", "PickType"])
        proj_col1 = find_col(df1, ["projection_id", "pp_projection_id", "pp_id", "Projection ID"])
        std_col1 = find_col(df1, ["standard_line", "Standard Line", "baseline", "standard_score"])
        if all([p_col1, prop_col1, line_col1, proj_col1]):
            for _, r in df1.iterrows():
                key = (
                    _norm_text(r.get(p_col1)),
                    _norm_text(r.get(prop_col1)),
                    _line_key(r.get(line_col1)),
                    _norm_text(r.get(t_col1)) if t_col1 else "",
                )
                raw_std = r.get(std_col1) if std_col1 else None
                std_parsed: float | None = None
                if raw_std is not None and str(raw_std).strip() != "":
                    try:
                        std_parsed = float(raw_std)
                    except (TypeError, ValueError):
                        std_parsed = None
                idx[key] = {
                    "pick_type": str(r.get(pick_col1, "Standard") or "Standard"),
                    "standard_line": std_parsed,
                }

    df8f = df8.copy()
    tier = df8f[tier_col8].astype(str).str.upper().str.strip()
    ddir = df8f[dir_col8].astype(str).str.upper().str.strip()
    bs = pd.to_numeric(df8f[blend_col8], errors="coerce")
    ln = pd.to_numeric(df8f[line_col8], errors="coerce")
    mask = tier.isin(["A", "B", "C"]) & ddir.ne("") & bs.notna() & ln.gt(0.5)
    df8f = df8f.loc[mask].copy()
    df8f["__blend"] = bs.loc[df8f.index]
    df8f = df8f.sort_values("__blend", ascending=False).head(int(max_candidates))

    out: list[dict[str, Any]] = []
    for _, r in df8f.iterrows():
        player = str(r.get(p_col8, "") or "").strip()
        prop = str(r.get(prop_col8, "") or "").strip()
        line_raw = r.get(line_col8, "")
        team = str(r.get(t_col8, "") or "").strip() if t_col8 else ""
        direction = str(r.get(dir_col8, "") or "").strip().upper()
        if direction not in ("OVER", "UNDER"):
            continue
        try:
            leg_line = float(line_raw)
        except (TypeError, ValueError):
            continue
        if leg_line <= 0.5:
            continue

        ptype_raw = str(r.get(pick_type8, "") or "").strip() if pick_type8 else ""
        key_full = (_norm_text(player), _norm_text(prop), _line_key(line_raw), _norm_text(team))
        key_noteam = (_norm_text(player), _norm_text(prop), _line_key(line_raw), "")
        match = idx.get(key_full) or idx.get(key_noteam)
        if match:
            ptype_raw = ptype_raw or str(match.get("pick_type", "Standard"))
        pt = norm_pick_type(ptype_raw)

        raw_blend = float(r.get("__blend") or 0.0)
        hit_prob = score_to_hit_prob(raw_blend, pt)

        std_line_val: float | None = None
        if match:
            sv = match.get("standard_line")
            if sv is not None:
                try:
                    std_line_val = float(sv)
                except (TypeError, ValueError):
                    std_line_val = None
        if std_line_val is None and pt == "standard":
            std_line_val = leg_line

        line_distance = 0.0
        if std_line_val is not None:
            line_distance = abs(float(std_line_val) - float(leg_line))

        out.append(
            {
                "player": player,
                "sport": sport,
                "prop_type": prop,
                "line": leg_line,
                "direction": direction,
                "pick_type": pt,
                "line_distance": line_distance,
                "hit_prob": hit_prob,
                "tier": str(r.get(tier_col8, "") or "").strip().upper(),
                "blended_score": raw_blend,
                "team": team,
            }
        )
    return out


def estimate_raw_combo_count(n_legs: int, pool: int) -> int:
    if pool < n_legs:
        return 0
    return math.comb(pool, n_legs)


def get_top_n(
    results: list[dict[str, Any]],
    n: int,
    mode: str,
    max_player: int = 3,
    max_sport: int = 8,
) -> list[dict[str, Any]]:
    mode = str(mode or "balanced").strip().lower()
    if mode == "pure_ev":
        results = sorted(results, key=lambda r: r.get("score_pure_ev", 0.0), reverse=True)
    elif mode == "safe":
        results = sorted(
            results,
            key=lambda r: (r.get("score_safe", 0.0), r.get("p_win", 0.0)),
            reverse=True,
        )
    else:
        results = sorted(results, key=lambda r: r.get("score_balanced", 0.0), reverse=True)

    player_exposure: dict[str, int] = {}
    sport_exposure: dict[str, int] = {}
    top_n: list[dict[str, Any]] = []

    for r in results:
        detail = r.get("legs_detail") or []
        players = [str(x.get("player", "")) for x in detail]
        sports = list(r.get("sports") or [])

        if any(player_exposure.get(p, 0) >= max_player for p in players if p):
            continue
        if any(sport_exposure.get(s, 0) >= max_sport for s in sports if s):
            continue

        for p in players:
            if p:
                player_exposure[p] = player_exposure.get(p, 0) + 1
        for s in sports:
            if s:
                sport_exposure[s] = sport_exposure.get(s, 0) + 1

        r["rank"] = len(top_n) + 1
        top_n.append(r)
        if len(top_n) >= n:
            break

    return top_n


def leg_detail_to_jsonable(legs: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for x in legs:
        out.append(
            {
                "player": x.get("player"),
                "sport": x.get("sport"),
                "prop_type": x.get("prop_type"),
                "line": x.get("line"),
                "direction": x.get("direction"),
                "pick_type": x.get("pick_type"),
                "tier": x.get("tier"),
                "blended_score": x.get("blended_score"),
                "hit_prob": x.get("hit_prob"),
                "line_distance": x.get("line_distance"),
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Cross-sport ultimate ticket ranker")
    ap.add_argument("--date", default="", help="Slate date YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--mode", choices=("pure_ev", "safe", "balanced"), default="balanced")
    ap.add_argument("--max-candidates", type=int, default=20)
    ap.add_argument("--max-combos", type=int, default=50_000)
    ap.add_argument("--min-legs", type=int, default=2)
    ap.add_argument("--max-legs", type=int, default=4)
    ap.add_argument("--min-ev", type=float, default=0.80)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true", help="Print summary only; do not write files")
    args = ap.parse_args()

    date_str = str(args.date or "").strip()[:10] or date.today().strftime("%Y-%m-%d")

    all_legs: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for sport in ("NBA", "NHL", "Soccer", "MLB"):
        legs = load_sport_legs(sport, date_str, args.max_candidates)
        counts[sport] = len(legs)
        all_legs.extend(legs)

    print(
        f"[ULTIMATE] Loaded legs: NBA={counts.get('NBA', 0)}, NHL={counts.get('NHL', 0)}, "
        f"Soccer={counts.get('Soccer', 0)}, MLB={counts.get('MLB', 0)}"
    )
    print(f"[ULTIMATE] Total candidates: {len(all_legs)}")
    if len(all_legs) < 2:
        print("[ULTIMATE] ERROR: need at least 2 legs across sports — build step8 outputs first.")
        return 1

    pool = len(all_legs)
    raw_total = sum(
        estimate_raw_combo_count(n, pool) for n in range(int(args.min_legs), int(args.max_legs) + 1)
    )
    prefilter_ev = float(args.min_ev)
    if raw_total > 500_000:
        prefilter_ev = max(prefilter_ev, 1.0)
        print(
            f"[ULTIMATE] WARN: raw combo count ~{raw_total:,} > 500k — "
            f"tightening est EV prefilter to {prefilter_ev}"
        )

    BASE_PAYOUT = {2: 3.0, 3: 6.0, 4: 10.0}
    all_results: list[dict[str, Any]] = []
    total_combos_evaluated = 0

    for n_legs in range(int(args.min_legs), int(args.max_legs) + 1):
        combos_tested = 0
        combos_kept = 0
        for combo in itertools.combinations(all_legs, n_legs):
            players = [l["player"] for l in combo]
            if len(players) != len(set(players)):
                continue

            sport_counts: dict[str, int] = {}
            for l in combo:
                sp = str(l["sport"])
                sport_counts[sp] = sport_counts.get(sp, 0) + 1
            if max(sport_counts.values()) > 2:
                continue

            p_win_est = 1.0
            for l in combo:
                p_win_est *= float(l["hit_prob"])
            base = float(BASE_PAYOUT.get(n_legs, 6.0))
            est_ev = p_win_est * base - (1.0 - p_win_est)
            if est_ev < prefilter_ev:
                continue

            combos_tested += 1
            if combos_tested > int(args.max_combos):
                break

            legs_for_ev = [
                {
                    "pick_type": l["pick_type"],
                    "line_distance": float(l.get("line_distance") or 0.0),
                    "hit_prob": float(l["hit_prob"]),
                }
                for l in combo
            ]
            ev_result = compute_ticket_ev(legs_for_ev, "power", n_legs)

            p_win = float(ev_result["p_all_win"])
            payout = float(ev_result["first_place_payout"])
            ev = float(ev_result["ev"])
            min_g = float(ev_result["min_guarantee"])
            adj = float(ev_result["min_guarantee_adjustment"])

            score_pure_ev = ev
            score_safe = p_win if ev > 1.0 else 0.0
            score_balanced = p_win * payout

            w1, w2, w3 = 0.4, 0.4, 0.2
            score_composite = (
                w1 * p_win
                + w2 * min(ev / 5.0, 1.0)
                + w3 * min(adj, 3.0) / 3.0
            )

            leg_strs = [
                f"{l['player']} {l['prop_type']} {l['line']} {l['direction']}" for l in combo
            ]
            all_results.append(
                {
                    "rank": 0,
                    "n_legs": n_legs,
                    "legs": leg_strs,
                    "legs_detail": leg_detail_to_jsonable(combo),
                    "sports": sorted({str(l["sport"]) for l in combo}),
                    "n_sports": len({l["sport"] for l in combo}),
                    "pick_types": [l["pick_type"] for l in combo],
                    "n_goblins": sum(1 for l in combo if l["pick_type"] == "goblin"),
                    "n_demons": sum(1 for l in combo if l["pick_type"] == "demon"),
                    "p_win": round(p_win, 4),
                    "p_win_pct": round(p_win * 100, 1),
                    "payout": round(payout, 2),
                    "min_guarantee": round(min_g, 2),
                    "payout_adjustment": round(adj, 4),
                    "ev": round(ev, 4),
                    "score_pure_ev": round(score_pure_ev, 4),
                    "score_safe": round(score_safe, 4),
                    "score_balanced": round(score_balanced, 4),
                    "score_composite": round(score_composite, 4),
                    "recommendation": ev_result["recommendation"],
                    "tiers": [l["tier"] for l in combo],
                }
            )
            combos_kept += 1

        total_combos_evaluated += combos_tested
        print(f"[ULTIMATE] {n_legs}-leg: tested={combos_tested} kept={combos_kept}")

    top = get_top_n(all_results, int(args.top_n), args.mode)
    mode_label = str(args.mode).upper()

    print(f"\n=== ULTIMATE TICKETS — {mode_label} MODE ===")
    print(f"{'Rank':<5} | {'Legs':<4} | {'Sports':<18} | {'P(Win)':<8} | {'Pay':<6} | {'EV':<6} | Rec")
    for t in top[:10]:
        sp = "+".join(t["sports"])
        print(
            f"{t['rank']:<5} | {t['n_legs']:<4} | {sp:<18} | "
            f"{t['p_win_pct']:<7.1f}% | {t['payout']:<5.1f}x | {t['ev']:<6.2f} | {t['recommendation']}"
        )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ui_payload = {
        "generated_at": generated_at,
        "date": date_str,
        "mode": args.mode,
        "total_combos_evaluated": total_combos_evaluated,
        "tickets": [
            {
                "rank": t["rank"],
                "legs": t["legs"],
                "sports": t["sports"],
                "n_legs": t["n_legs"],
                "p_win_pct": t["p_win_pct"],
                "payout": t["payout"],
                "min_guarantee": t["min_guarantee"],
                "ev": t["ev"],
                "recommendation": t["recommendation"],
                "n_goblins": t["n_goblins"],
                "n_demons": t["n_demons"],
                "pick_types": t["pick_types"],
            }
            for t in top
        ],
    }

    if args.dry_run:
        print("\n[ULTIMATE] dry-run: no files written.")
        return 0

    out_dir = ROOT / "outputs" / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    mode_fn = str(args.mode)
    csv_path = out_dir / f"ultimate_tickets_{date_str}_{mode_fn}.csv"
    json_path = out_dir / f"ultimate_tickets_{date_str}_{mode_fn}.json"

    rows = []
    for t in all_results:
        row = {k: v for k, v in t.items() if k != "legs_detail"}
        row["legs_detail"] = json.dumps(t.get("legs_detail") or [])
        rows.append(row)
    if rows:
        pd.DataFrame(rows).to_csv(csv_path, index=False)
    else:
        pd.DataFrame(
            columns=[
                "rank",
                "n_legs",
                "legs",
                "p_win",
                "ev",
                "recommendation",
                "legs_detail",
            ]
        ).to_csv(csv_path, index=False)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at": generated_at,
                "date": date_str,
                "mode": args.mode,
                "total_combos_evaluated": total_combos_evaluated,
                "results": all_results,
            },
            f,
            indent=2,
            default=str,
        )

    ui_path = ROOT / "ui_runner" / "templates" / "ev_top20_latest.json"
    ui_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ui_path, "w", encoding="utf-8") as f:
        json.dump(ui_payload, f, indent=2)

    print(f"\n[ULTIMATE] Wrote {csv_path.relative_to(ROOT)}")
    print(f"[ULTIMATE] Wrote {json_path.relative_to(ROOT)}")
    print(f"[ULTIMATE] Wrote {ui_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
