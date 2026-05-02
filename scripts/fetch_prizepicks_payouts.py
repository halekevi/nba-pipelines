#!/usr/bin/env python3
"""
Read-only PrizePicks payout EV engine via existing logged-in Chrome session.

Requires Chrome started with remote debugging:
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir=C:\\chrome_debug
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
_scripts = str(ROOT / "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)
import combined_slate_tickets as _cst  # noqa: E402

SPORT_CFG = {
    "NBA": {
        "step8": ROOT / "Sports" / "NBA" / "data" / "outputs" / "step8_all_direction_clean.xlsx",
        "step1": ROOT / "Sports" / "NBA" / "data" / "outputs" / "step1_pp_props_today.csv",
        "top_n": 15,
    },
    "NHL": {
        "step8": ROOT / "Sports" / "NHL" / "outputs" / "step8_nhl_direction_clean.xlsx",
        "step1": ROOT / "Sports" / "NHL" / "outputs" / "step1_nhl_props.csv",
        "top_n": 10,
    },
    "Soccer": {
        "step8": ROOT / "Sports" / "Soccer" / "outputs" / "step8_soccer_direction_clean.xlsx",
        "step1": ROOT / "Sports" / "Soccer" / "outputs" / "step1_soccer_props.csv",
        "top_n": 10,
    },
    "Tennis": {
        "step8": ROOT / "Sports" / "Tennis" / "outputs" / "step8_tennis_direction_clean.xlsx",
        "step1": ROOT / "Sports" / "Tennis" / "outputs" / "step1_tennis_props.csv",
        "top_n": 10,
    },
    "MLB": {
        "step8": ROOT / "Sports" / "MLB" / "step8_mlb_direction_clean.xlsx",
        "step1": ROOT / "Sports" / "MLB" / "step1_mlb_props.csv",
        "top_n": 10,
    },
}

MIN_REALISTIC_HIT_PROB = 0.50
MAX_PLAYER_EXPOSURE_IN_TOP_N = 3
MAX_SPORT_EXPOSURE_IN_TOP_N = 8


def find_col(df: pd.DataFrame, names: list[str]) -> str | None:
    for c in names:
        if c in df.columns:
            return c
        matches = [col for col in df.columns if str(col).strip().lower() == str(c).strip().lower()]
        if matches:
            return matches[0]
    return None


def _pick_col(df: pd.DataFrame, names: list[str]) -> str | None:
    m = {str(c).strip().lower(): c for c in df.columns}
    for n in names:
        if n.lower() in m:
            return m[n.lower()]
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


def combo_to_ev_legs(combo: tuple[dict, ...]) -> list[dict]:
    """Leg payloads for combined_slate_tickets.compute_ticket_ev (power path)."""
    out: list[dict] = []
    for leg in combo:
        pt = str(leg.get("pick_type") or "standard").lower()
        if "goblin" in pt:
            pe = "goblin"
        elif "demon" in pt:
            pe = "demon"
        else:
            pe = "standard"
        sl = leg.get("standard_line")
        ln = leg.get("line")
        dist = 0.0
        try:
            if sl is not None and ln is not None:
                dist = abs(float(sl) - float(ln))
        except (TypeError, ValueError):
            dist = 0.0
        out.append(
            {
                "pick_type": pe,
                "line_distance": dist,
                "hit_prob": float(leg["hit_prob"]),
            }
        )
    return out


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


def load_candidate_legs(sport_filter: str | None = None) -> tuple[list[dict], dict[str, int]]:
    legs: list[dict] = []
    by_sport: dict[str, int] = {}
    sf = str(sport_filter or "").strip().upper()
    for sport, cfg in SPORT_CFG.items():
        if sf and sport.upper() != sf:
            continue
        step8 = cfg["step8"]
        step1 = cfg["step1"]
        print(f"[PAYOUT] {sport} step8 path: {step8}")
        print(f"[PAYOUT] {sport} step1 path: {step1}")
        if not step8.exists() or not step1.exists():
            print(f"[PAYOUT] WARN: missing inputs for {sport} (step8/step1) — skip")
            continue

        df8 = _to_sheet_df(step8)
        df1 = pd.read_csv(step1, low_memory=False)

        if sport in ("NHL", "MLB"):
            print(f"[PAYOUT] {sport} step8 columns: {list(df8.columns)}")

        tier_expected = ["Tier", "tier", "TIER"]
        score_expected = ["blended_score", "Blended Score", "Rank Score", "rank_score", "score"]
        dir_expected = ["Direction", "direction", "final_bet_direction", "Bet Direction", "Dir"]
        player_expected = ["Player", "player_name", "player", "Name"]
        prop_expected = ["Prop", "prop_type", "Prop Type", "prop"]
        line_expected = ["Line", "line_score", "Line Score", "line"]
        picktype_expected = ["Pick Type", "pick_type", "PickType"]
        proj_expected = ["projection_id", "pp_projection_id", "pp_id", "Projection ID"]
        team_expected = ["team", "Team"]

        p_col8 = find_col(df8, player_expected)
        t_col8 = find_col(df8, team_expected)
        prop_col8 = find_col(df8, prop_expected)
        line_col8 = find_col(df8, line_expected)
        dir_col8 = find_col(df8, dir_expected)
        tier_col8 = find_col(df8, tier_expected)
        blend_col8 = find_col(df8, score_expected)
        pick_type8 = find_col(df8, picktype_expected)
        proj_col8 = find_col(df8, proj_expected)

        if sport in ("NHL", "MLB"):
            print(
                f"[PAYOUT] {sport} expected step8 mappings -> "
                f"tier={tier_col8}, score={blend_col8}, direction={dir_col8}, "
                f"player={p_col8}, prop={prop_col8}, line={line_col8}, pick_type={pick_type8}, proj={proj_col8}"
            )

        if not all([p_col8, prop_col8, line_col8, dir_col8, tier_col8, blend_col8]):
            print(f"[PAYOUT] WARN: required step8 cols missing for {sport} — skip")
            continue

        p_col1 = find_col(df1, ["player", "player_name", "name", "Player"])
        t_col1 = find_col(df1, ["team", "Team"])
        prop_col1 = find_col(df1, ["prop_type", "prop", "Prop", "Prop Type", "stat_type", "Stat Type"])
        line_col1 = find_col(df1, ["line", "line_score", "Line"])
        pick_col1 = find_col(df1, ["pick_type", "Pick Type", "PickType"])
        proj_col1 = find_col(df1, ["projection_id", "pp_projection_id", "pp_id", "Projection ID"])
        std_col1 = find_col(df1, ["standard_line", "Standard Line", "baseline", "standard_score"])

        if sport in ("NHL", "MLB"):
            print(f"[PAYOUT] {sport} step1 columns: {list(df1.columns)}")
            print(
                f"[PAYOUT] {sport} expected step1 mappings -> "
                f"player={p_col1}, team={t_col1}, prop={prop_col1}, line={line_col1}, pick_type={pick_col1}, proj={proj_col1}"
            )
        if not all([p_col1, prop_col1, line_col1, proj_col1]):
            print(f"[PAYOUT] WARN: required step1 cols missing for {sport} — skip")
            continue

        # Map step1 rows to projection IDs by normalized keys.
        idx: dict[tuple[str, str, str, str], dict] = {}
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
                "projection_id": str(r.get(proj_col1, "") or "").strip(),
                "pick_type": str(r.get(pick_col1, "Standard") or "Standard"),
                "standard_line": std_parsed,
            }

        df8f = df8.copy()
        tier = df8f[tier_col8].astype(str).str.upper().str.strip()
        ddir = df8f[dir_col8].astype(str).str.upper().str.strip()
        bs = pd.to_numeric(df8f[blend_col8], errors="coerce")
        mask = tier.isin(["A", "B", "C"]) & ddir.ne("") & bs.notna()
        df8f = df8f.loc[mask].copy()
        df8f["__blend"] = bs.loc[df8f.index]
        df8f = df8f.sort_values("__blend", ascending=False).head(int(cfg["top_n"]))

        added = 0
        local_probs: list[tuple[str, float, float, str]] = []
        for _, r in df8f.iterrows():
            player = str(r.get(p_col8, "") or "").strip()
            prop = str(r.get(prop_col8, "") or "").strip()
            line = r.get(line_col8, "")
            team = str(r.get(t_col8, "") or "").strip() if t_col8 else ""
            direction = str(r.get(dir_col8, "") or "").strip().upper()
            proj = str(r.get(proj_col8, "") or "").strip() if proj_col8 else ""
            ptype = str(r.get(pick_type8, "") or "").strip()
            key_full = (_norm_text(player), _norm_text(prop), _line_key(line), _norm_text(team))
            key_noteam = (_norm_text(player), _norm_text(prop), _line_key(line), "")
            match = idx.get(key_full) or idx.get(key_noteam)
            if not proj:
                if match:
                    proj = str(match.get("projection_id", "") or "").strip()
                    if not ptype:
                        ptype = str(match.get("pick_type", "Standard"))
            if not proj:
                continue

            raw_blend = float(r.get("__blend") or 0.0)
            hit_prob = score_to_hit_prob(raw_blend, ptype.lower() if ptype else "standard")
            std_line_val: float | None = None
            if match:
                sv = match.get("standard_line")
                if sv is not None:
                    try:
                        std_line_val = float(sv)
                    except (TypeError, ValueError):
                        std_line_val = None
            leg_line = float(line) if str(line).strip() != "" else None
            if std_line_val is None and leg_line is not None:
                pl = (ptype.lower() if ptype else "standard")
                if "standard" in pl and "goblin" not in pl and "demon" not in pl:
                    std_line_val = leg_line
            legs.append(
                {
                    "player": player,
                    "sport": sport,
                    "prop_type": prop,
                    "line": leg_line,
                    "direction": direction if direction in ("OVER", "UNDER") else "OVER",
                    "pick_type": ptype.lower() if ptype else "standard",
                    "hit_prob": hit_prob,
                    "pp_id": proj,
                    "team": team,
                    "standard_line": std_line_val,
                }
            )
            local_probs.append((player, raw_blend, hit_prob, ptype.lower() if ptype else "standard"))
            added += 1
        by_sport[sport] = added
        print(f"[PAYOUT] {sport} candidate legs loaded: {added}")
        if local_probs:
            print(f"[PAYOUT] {sport} top 5 score->hit_prob:")
            for i, (pl, raw_b, hp, pt) in enumerate(local_probs[:5], start=1):
                print(f"  {i}. {pl} [{pt}] score={raw_b:.4f} -> hit_prob={hp:.4f}")
    return legs, by_sport


def extract_multiplier(value: Any) -> float | None:
    if isinstance(value, dict):
        for k, v in value.items():
            kl = str(k).lower()
            if any(x in kl for x in ("payout_multiplier", "winning_multiplier", "multiplier", "payout", "odds")):
                m = extract_multiplier(v)
                if m is not None:
                    return m
            m = extract_multiplier(v)
            if m is not None:
                return m
    elif isinstance(value, list):
        for v in value:
            m = extract_multiplier(v)
            if m is not None:
                return m
    elif isinstance(value, (int, float)):
        vf = float(value)
        if vf > 1.0:
            return vf
    elif isinstance(value, str):
        mt = re.search(r"(\d+(?:\.\d+)?)\s*x", value.lower())
        if mt:
            return float(mt.group(1))
        if re.fullmatch(r"\d+(?:\.\d+)?", value.strip()):
            vf = float(value.strip())
            if vf > 1.0:
                return vf
    return None


def connect_existing_browser(cdp_url: str):
    try:
        p = sync_playwright().start()
        browser = p.chromium.connect_over_cdp(cdp_url)
        if not browser.contexts:
            raise RuntimeError("No Chrome contexts found on CDP endpoint.")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        for pg in context.pages:
            if "prizepicks" in (pg.url or "").lower():
                page = pg
                break
        return p, browser, context, page
    except Exception as e:
        print("[PAYOUT] Could not connect to existing Chrome session.")
        print("Close Chrome completely, then run:")
        print("Windows: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' --remote-debugging-port=9222 --user-data-dir=C:\\chrome_debug")
        print("Then navigate to prizepicks.com and log in.")
        print("Then run this script again.")
        raise RuntimeError(str(e))


def navigate_sport_tab(page, sport: str):
    try:
        page.get_by_text(sport, exact=False).first.click(timeout=1500)
        page.wait_for_timeout(350)
    except Exception:
        return


def add_leg_to_slip(page, leg: dict) -> bool:
    player = leg["player"]
    direction = leg["direction"]
    prop = leg["prop_type"]
    try:
        search_selectors = [
            "input[placeholder*='Search']",
            "input[aria-label*='Search']",
            "input[type='search']",
        ]
        for sel in search_selectors:
            box = page.locator(sel).first
            if box.count() > 0:
                try:
                    box.click(timeout=600)
                    box.fill(player, timeout=1200)
                    page.wait_for_timeout(300)
                    break
                except Exception:
                    continue
        # Pick by card text context.
        card = page.locator(f"text={player}").first
        if card.count() == 0:
            print(f"[PAYOUT] SKIP: {player} not found on board")
            return False
        try:
            # Prefer prop text block near player.
            region = page.locator("body")
            if prop:
                region = page.locator(f"text={prop}").first
            region.get_by_text(direction, exact=False).first.click(timeout=1500)
        except Exception:
            # fallback try clicking direction text globally
            page.get_by_text(direction, exact=False).first.click(timeout=1200)
        page.wait_for_timeout(250)
        return True
    except Exception:
        print(f"[PAYOUT] SKIP: {player} not found on board")
        return False


def read_dom_multiplier(page) -> float | None:
    selectors = [
        "[data-testid='payout-multiplier']",
        ".payout-multiplier",
        ".multiplier",
    ]
    for sel in selectors:
        try:
            txt = page.locator(sel).first.inner_text(timeout=800)
            m = extract_multiplier(txt)
            if m is not None:
                return m
        except Exception:
            continue
    try:
        mt = page.locator("text=/\\d+\\.?\\d*x/i").first
        if mt.count() > 0:
            return extract_multiplier(mt.inner_text(timeout=600))
    except Exception:
        pass
    return None


def clear_slip(page):
    # Never click submit actions; only close/remove/clear.
    try:
        for txt in ["Clear All", "Clear", "Remove All"]:
            loc = page.get_by_text(txt, exact=False).first
            if loc.count() > 0:
                try:
                    loc.click(timeout=500)
                    page.wait_for_timeout(200)
                except Exception:
                    pass
        # Click close/delete icons in slip panel.
        for sel in ["[aria-label*='Remove']", "[aria-label*='Close']", "[data-testid*='remove']"]:
            btns = page.locator(sel)
            n = min(btns.count(), 12)
            for i in range(n):
                try:
                    btns.nth(0).click(timeout=300)
                    page.wait_for_timeout(120)
                except Exception:
                    break
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        page.wait_for_timeout(180)
    except Exception:
        pass


def combo_key(combo: tuple[dict, ...]) -> str:
    return "|".join(sorted(str(x["pp_id"]) for x in combo))


def correlation_flag(combo: tuple[dict, ...]) -> str:
    counts: dict[str, int] = {}
    for leg in combo:
        k = str(leg.get("prop_type", "")).strip().lower()
        if not k:
            continue
        counts[k] = counts.get(k, 0) + 1
    return "HIGH" if any(v >= 3 for v in counts.values()) else "OK"


def passes_diversity_constraints(combo: tuple[dict, ...]) -> bool:
    players = [str(x.get("player", "")).strip().lower() for x in combo]
    if len(set(players)) < len(players):
        return False
    sports = [str(x.get("sport", "")).strip().upper() for x in combo]
    # Force cross-sport diversity to avoid mono-sport saturation in top tickets.
    if len(set(sports)) < 2:
        return False
    per_sport: dict[str, int] = {}
    for s in sports:
        per_sport[s] = per_sport.get(s, 0) + 1
    # No more than 2 legs from one sport for 3+ leg tickets.
    if len(combo) >= 3 and any(v > 2 for v in per_sport.values()):
        return False
    prop_counts: dict[str, int] = {}
    for leg in combo:
        p = str(leg.get("prop_type", "")).strip().lower()
        prop_counts[p] = prop_counts.get(p, 0) + 1
    # Avoid heavily correlated same-prop stacks.
    if any(v >= 3 for v in prop_counts.values()):
        return False
    return True


def _build_exposure_capped_top(df_all: pd.DataFrame, top_n: int = 20) -> tuple[pd.DataFrame, dict[str, int], dict[str, int]]:
    if df_all.empty:
        return df_all.head(0), {}, {}
    player_exposure: dict[str, int] = {}
    sport_exposure: dict[str, int] = {}
    keep_rows = []
    for _, r in df_all.iterrows():
        players = [str(x).strip() for x in (r.get("players") or []) if str(x).strip()]
        sports = [str(x).strip() for x in (r.get("sports") or []) if str(x).strip()]
        if any(player_exposure.get(p, 0) >= MAX_PLAYER_EXPOSURE_IN_TOP_N for p in players):
            continue
        if any(sport_exposure.get(s, 0) >= MAX_SPORT_EXPOSURE_IN_TOP_N for s in sports):
            continue
        for p in players:
            player_exposure[p] = player_exposure.get(p, 0) + 1
        for s in sports:
            sport_exposure[s] = sport_exposure.get(s, 0) + 1
        keep_rows.append(r.to_dict())
        if len(keep_rows) >= int(top_n):
            break
    out = pd.DataFrame(keep_rows) if keep_rows else df_all.head(0)
    return out, player_exposure, sport_exposure


def write_outputs(results: list[dict], date_str: str):
    out_dir = ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = out_dir / f"ticket_ev_{date_str}.xlsx"
    json_path = out_dir / "ticket_ev_latest.json"

    df = pd.DataFrame(results)
    if df.empty:
        df = pd.DataFrame(
            columns=[
                "legs", "sports", "n_legs", "n_goblins", "n_demons", "p_win",
                "base_payout", "exact_multiplier", "true_ev", "recommendation",
                "payout_source", "correlation_flag",
            ]
        )
    df_all = df.sort_values("true_ev", ascending=False) if not df.empty else df
    df_strong = df_all[df_all["true_ev"] > 1.5] if not df.empty else df_all
    df_ok = df_all[df_all["true_ev"] > 1.0] if not df.empty else df_all
    df_top, player_exposure, sport_exposure = _build_exposure_capped_top(df_all, top_n=20)

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as xw:
        df_all.to_excel(xw, sheet_name="ALL", index=False)
        df_strong.to_excel(xw, sheet_name="STRONG", index=False)
        df_ok.to_excel(xw, sheet_name="OK", index=False)
        df_top.to_excel(xw, sheet_name="TOP20", index=False)

    groups = []
    if not df_top.empty:
        tickets = []
        for i, r in df_top.reset_index(drop=True).iterrows():
            tickets.append(
                {
                    "ticket_no": int(i + 1),
                    "n_legs": int(r.get("n_legs", 0)),
                    "est_win_prob": float(r.get("p_win", 0.0)),
                    "power_payout": float(r.get("exact_multiplier", 0.0)),
                    "ev_power": float(r.get("true_ev", 0.0)),
                    "legs": [{"label": x} for x in (r.get("legs") or [])],
                    "sports": r.get("sports") or [],
                    "recommendation": r.get("recommendation", ""),
                    "payout_source": r.get("payout_source", ""),
                    "correlation_flag": r.get("correlation_flag", "OK"),
                }
            )
        groups.append({"group_name": "TOP20 Exact EV", "tickets": tickets})

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "date": date_str,
        "groups": groups,
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return xlsx_path, json_path, df_top, player_exposure, sport_exposure


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp-url", default="http://localhost:9222")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-ui-combos", type=int, default=2000)
    ap.add_argument("--max-scan", type=int, default=150000)
    ap.add_argument("--min-est-ev", type=float, default=0.80, help=argparse.SUPPRESS)
    ap.add_argument("--min-ev", type=float, default=None, help="Estimated EV prefilter threshold (default: 0.80)")
    ap.add_argument(
        "--sport",
        choices=["NBA", "NHL", "Soccer", "Tennis", "MLB"],
        default="",
        help="Optional sport-only run",
    )
    ap.add_argument("--delay-sec", type=float, default=0.5)
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = ap.parse_args()

    min_ev = float(args.min_ev) if args.min_ev is not None else float(args.min_est_ev)
    sport_filter = str(args.sport or "").strip()
    all_candidate_legs, sport_counts = load_candidate_legs(sport_filter=sport_filter)
    if len(all_candidate_legs) < 2:
        print("[PAYOUT] Not enough candidate legs to build combos.")
        return

    p = browser = context = page = None
    payouts_captured: dict[str, float] = {}
    current_combo_key = {"value": ""}
    if not args.dry_run:
        p, browser, context, page = connect_existing_browser(args.cdp_url)
        page.wait_for_timeout(500)

        def handle_response(response):
            if "entries" in response.url or "entry" in response.url:
                try:
                    body = response.json()
                    m = extract_multiplier(body)
                    if m is not None and current_combo_key["value"]:
                        payouts_captured[current_combo_key["value"]] = m
                except Exception:
                    return

        page.on("response", handle_response)

    tested = 0
    skipped_low_ev = 0
    skipped_lookup = 0
    scanned = 0
    raw_combo_total = 0
    skipped_diversity = 0
    results: list[dict] = []
    strong_est = 0
    ok_est = 0

    for n_legs in [2, 3, 4, 5]:
        raw_combo_total += math.comb(len(all_candidate_legs), n_legs) if len(all_candidate_legs) >= n_legs else 0

    try:
        for n_legs in [2, 3, 4, 5]:
            for combo in itertools.combinations(all_candidate_legs, n_legs):
                scanned += 1
                if scanned > int(args.max_scan):
                    print(f"[PAYOUT] scan cap reached ({args.max_scan})")
                    break
                if tested >= int(args.max_ui_combos):
                    break

                if not passes_diversity_constraints(combo):
                    skipped_diversity += 1
                    continue

                p_win_est = 1.0
                for leg in combo:
                    p_win_est *= float(leg["hit_prob"])
                ev_legs = combo_to_ev_legs(combo)
                ev_pack = _cst.compute_ticket_ev(ev_legs, "power", n_legs)
                base_payout = float(ev_pack["first_place_payout"])
                est_ev = float(ev_pack["ev"])
                if est_ev < min_ev:
                    skipped_low_ev += 1
                    continue

                ckey = combo_key(combo)
                current_combo_key["value"] = ckey
                corr = correlation_flag(combo)
                if est_ev > 1.0:
                    ok_est += 1
                if est_ev > 1.5:
                    strong_est += 1

                if args.dry_run:
                    tested += 1
                    results.append(
                        {
                            "legs": [f'{l["player"]} {l["prop_type"]}' for l in combo],
                            "players": [l["player"] for l in combo],
                            "sports": sorted(list({l["sport"] for l in combo})),
                            "n_legs": n_legs,
                            "n_goblins": sum(1 for l in combo if "goblin" in str(l["pick_type"]).lower()),
                            "n_demons": sum(1 for l in combo if "demon" in str(l["pick_type"]).lower()),
                            "p_win": round(p_win_est, 4),
                            "base_payout": base_payout,
                            "empirical_min_g": float(ev_pack["min_guarantee"]),
                            "empirical_adj": float(ev_pack["min_guarantee_adjustment"]),
                            "exact_multiplier": float(base_payout),
                            "true_ev": round(est_ev, 4),
                            "recommendation": ev_pack.get("recommendation", "SKIP"),
                            "payout_source": "estimated",
                            "correlation_flag": corr,
                        }
                    )
                    continue

                combo_ok = True
                clear_slip(page)
                for leg in combo:
                    navigate_sport_tab(page, leg["sport"])
                    ok = add_leg_to_slip(page, leg)
                    if not ok:
                        combo_ok = False
                        skipped_lookup += 1
                        break
                if not combo_ok:
                    clear_slip(page)
                    time.sleep(max(float(args.delay_sec), 0.5))
                    continue

                dom_mult = read_dom_multiplier(page)
                net_mult = payouts_captured.get(ckey)
                exact_multiplier = dom_mult if dom_mult is not None else net_mult
                if exact_multiplier is None:
                    exact_multiplier = base_payout

                true_ev = p_win_est * float(exact_multiplier) - (1 - p_win_est)
                results.append(
                    {
                        "legs": [f'{l["player"]} {l["prop_type"]}' for l in combo],
                        "players": [l["player"] for l in combo],
                        "sports": sorted(list({l["sport"] for l in combo})),
                        "n_legs": n_legs,
                        "n_goblins": sum(1 for l in combo if "goblin" in str(l["pick_type"]).lower()),
                        "n_demons": sum(1 for l in combo if "demon" in str(l["pick_type"]).lower()),
                        "p_win": round(p_win_est, 4),
                        "base_payout": base_payout,
                        "empirical_min_g": float(ev_pack["min_guarantee"]),
                        "empirical_adj": float(ev_pack["min_guarantee_adjustment"]),
                        "exact_multiplier": float(exact_multiplier),
                        "true_ev": round(true_ev, 4),
                        "empirical_ev_prefilter": round(est_ev, 4),
                        "recommendation": (
                            "STRONG" if true_ev > 1.5 else
                            "OK" if true_ev > 1.0 else
                            "MARGINAL" if true_ev > 0.8 else "SKIP"
                        ),
                        "payout_source": "exact" if (dom_mult is not None or net_mult is not None) else "estimated",
                        "correlation_flag": corr,
                    }
                )
                tested += 1
                clear_slip(page)
                time.sleep(max(float(args.delay_sec), 0.5))
            if scanned > int(args.max_scan) or tested >= int(args.max_ui_combos):
                break
    finally:
        if not args.dry_run:
            try:
                browser.close()
            except Exception:
                pass
            try:
                p.stop()
            except Exception:
                pass

    xlsx_path, json_path, df_top_capped, player_exp_top, sport_exp_top = write_outputs(results, args.date)
    df = pd.DataFrame(results)
    strong_n = int((df["true_ev"] > 1.5).sum()) if not df.empty else 0
    ok_n = int((df["true_ev"] > 1.0).sum()) if not df.empty else 0
    best = df.sort_values("true_ev", ascending=False).head(1) if not df.empty else pd.DataFrame()

    if args.dry_run:
        total_loaded = sum(sport_counts.values())
        print(
            f"[DRY RUN] Candidate legs loaded: {total_loaded} "
            f"(NBA: {sport_counts.get('NBA',0)}, NHL: {sport_counts.get('NHL',0)}, "
            f"Soccer: {sport_counts.get('Soccer',0)}, Tennis: {sport_counts.get('Tennis',0)}, "
            f"MLB: {sport_counts.get('MLB',0)})"
        )
        print(f"[DRY RUN] Total raw combos (2-5 legs): {raw_combo_total}")
        print(f"[DRY RUN] Combos skipped by diversity constraints: {skipped_diversity}")
        print(f"[DRY RUN] Combos passing est_ev >= {min_ev:.2f}: {tested}")
        print(f"[DRY RUN] Combos passing est_ev >= 1.0: {ok_est}")
        print(f"[DRY RUN] Combos passing est_ev >= 1.5: {strong_est}")
        print(f"[DRY RUN] Estimated UI calls needed for live run: {min(tested, int(args.max_ui_combos))}")
        print("[DRY RUN] Top 5 combos by estimated EV:")
        top5 = df_top_capped.head(5) if not df_top_capped.empty else pd.DataFrame()
        if top5.empty:
            print("  (none)")
        else:
            for i, (_, r) in enumerate(top5.iterrows(), start=1):
                rec = "STRONG" if float(r["true_ev"]) > 1.5 else ("OK" if float(r["true_ev"]) > 1.0 else "MARGINAL")
                print(
                    f"  {i}. {r['legs']} | P(win)={round(float(r['p_win'])*100,2)}% | "
                    f"Est Payout={r['exact_multiplier']}x | Est EV={r['true_ev']} | {rec}"
                )
        print("[DRY RUN] Player exposure in TOP20:")
        if player_exp_top:
            for p, n in sorted(player_exp_top.items(), key=lambda kv: (-kv[1], kv[0])):
                print(f"  {p}: {n}")
        else:
            print("  (none)")
        print("[DRY RUN] Sport exposure in TOP20:")
        if sport_exp_top:
            ordered = ", ".join(
                f"{k}: {sport_exp_top.get(k, 0)}" for k in ["NBA", "NHL", "Soccer", "Tennis", "MLB"]
            )
            print(f"  {ordered}")
        else:
            print("  (none)")
    else:
        print(f"Total combos tested: {tested}")
        print(f"Total combos skipped (diversity constraints): {skipped_diversity}")
        print(f"Total combos skipped (est EV < {min_ev:.2f}): {skipped_low_ev}")
        print(f"Total combos skipped (leg lookup failed): {skipped_lookup}")
        print(f"STRONG tickets: {strong_n}")
        print(f"OK tickets: {ok_n}")
    if not best.empty:
        r = best.iloc[0]
        print(
            f"Best ticket: {r['legs']} | P(win)={round(float(r['p_win'])*100,2)}% | "
            f"Payout={r['exact_multiplier']}x | EV={r['true_ev']}"
        )
    print(f"[PAYOUT] Saved workbook -> {xlsx_path}")
    print(f"[PAYOUT] Saved json     -> {json_path}")


if __name__ == "__main__":
    main()

