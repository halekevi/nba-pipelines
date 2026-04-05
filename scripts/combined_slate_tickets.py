#!/usr/bin/env python3
"""
combined_slate_tickets.py

Combined NBA + CBB + NHL + Soccer Slate & Ticket Generator
Merges NBA (step8_all_direction_clean.xlsx) and CBB (step6_ranked_cbb.xlsx ELIGIBLE)
Outputs:
  - combined_slate_tickets_YYYY-MM-DD.xlsx
  - tickets_latest.json (web; /tickets renders from this). Graded HTML: build_ticket_eval.py → ticket_eval_<date>.html

Cross-book lines (optional):
  Place CSVs next to the combined output, then pass --underdog-csv / --draftkings-csv (or use run_pipeline
  when files exist under outputs/<date>/):
    outputs/<date>/underdog_props.csv   ← fetch_underdog_pickem.py --output ...
    outputs/<date>/draftkings_props_nba.csv ← fetch_draftkings_player_props.py --league nba -o ...
  Join is on sport + team + normalized player + normalized prop label (best-effort; DK/UD naming differs).

Sheets: SUMMARY, Full Slate (reordered + STRONG/LEAN/RISK + pace beside Def Tier), NBA Slate, CBB Slate,
        2–6-Leg tickets per sport (Goblin / Standard / Std+Gob mix),
        cross-sport Standard / Std+Gob / Goblin mixes,
        plus up to three cross-pipeline slips (max 6 legs each): Standard-only, Goblin-only, Std+Gob mix

NEW (Web):
- Adds player headshot thumbnails when an ID is available:
    NBA: uses nba_player_id (if present) -> cdn.nba.com headshot
    CBB: uses espn_player_id (if present) -> espncdn headshot
  If no ID exists, it falls back to a simple initials avatar.
- JSON includes image_url per leg.
- More helpful file-path resolution (tries script dir + recursive search if file not found)

Ticket modes (defaults are strict; no extra flag needed):
- --high-conviction is ON by default (--no-high-conviction for legacy wider pools).
- With strict: pool hit rate floor >= 0.70, default tiers A,B,C collapse to A,B, max 4 legs on FINAL slips,
  stricter per-leg floors; structured 2–3 leg tickets use 0.70 leg floor unless --min-leg-hit-rate set.
- --min-leg-hit-rate / --max-ticket-legs: optional overrides (see argparse help).
- --prioritize-ticket-hit: optional; raises per-leg floors and drops slips below modeled P(payout).
  This maximizes *expected* ticket success — no generator can guarantee a literal 100% hit rate.
- --ticket-candidate-sort: how to rank props when *choosing* legs (default blend = ML prob + rank composite).
  ML already drives est_win_prob via _resolve_leg_prob; this aligns *selection* order with that signal.
- Improve ml_prob over time: run combined_ticket_grader.py with --export-graded-legs-csv (stack slates) and read ML_CALIBRATION in the graded workbook.
- --ticket-gen-starts (default 6): structured slips try K alternative first legs and keep the best modeled ticket payout (flex cash or all-hit prob).

HOTFIX:
- Fixes crash when CBB "direction" becomes a DataFrame due to duplicate columns.
  We de-duplicate columns BEFORE touching df["direction"].str.upper().
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from typing import List, Optional

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from usage_redistribution import apply_usage_redistribution

# Repo root = parent of scripts/ (this file lives in scripts/)
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
from utils.kelly_staking import fractional_kelly, leg_edge_pct_for_kelly
from utils.cbb_tourney_metadata import CBB_AP_TOP25_2026, CBB_TOURNEY_2026
from utils.goblin_demon_multiplier import (
    compute_ticket_ev as gd_compute_ticket_ev,
    leg_delta_pct as gd_leg_delta_pct,
    multiplier_summary as gd_multiplier_summary,
)

_log_slate = logging.getLogger("combined_slate_tickets")

DEFAULT_NBA_PATH = os.path.join(REPO_ROOT, "NBA", "data", "outputs", "step8_all_direction_clean.xlsx")
DEFAULT_CBB_PATH = os.path.join(REPO_ROOT, "CBB", "step6_ranked_cbb.xlsx")
DEFAULT_NBA1H_PATH = os.path.join(REPO_ROOT, "NBA", "step8_nba1h_direction_clean.xlsx")
DEFAULT_NBA1Q_PATH = os.path.join(REPO_ROOT, "NBA", "step8_nba1q_direction_clean.xlsx")
DEFAULT_WCBB_PATH = os.path.join(REPO_ROOT, "CBB", "step6_ranked_wcbb.xlsx")
DEFAULT_MLB_PATH = os.path.join(REPO_ROOT, "MLB", "step8_mlb_direction_clean.xlsx")
_soccer_root = os.path.join(REPO_ROOT, "Soccer", "step8_soccer_direction_clean.xlsx")
_soccer_outputs = os.path.join(REPO_ROOT, "Soccer", "outputs", "step8_soccer_direction_clean.xlsx")
if os.path.exists(_soccer_root) and os.path.exists(_soccer_outputs):
    DEFAULT_SOCCER_PATH = (
        _soccer_root
        if os.path.getsize(_soccer_root) >= os.path.getsize(_soccer_outputs)
        else _soccer_outputs
    )
elif os.path.exists(_soccer_root):
    DEFAULT_SOCCER_PATH = _soccer_root
elif os.path.exists(_soccer_outputs):
    DEFAULT_SOCCER_PATH = _soccer_outputs
else:
    DEFAULT_SOCCER_PATH = _soccer_root
DEFAULT_NHL_PATH = os.path.join(REPO_ROOT, "NHL", "outputs", "step8_nhl_direction_clean.xlsx")
DEFAULT_WEB_OUTDIR = os.path.join(REPO_ROOT, "ui_runner", "templates")


# ── Color palette ─────────────────────────────────────────────────────────────
C = {
    "hdr": "1C1C1C",
    "hdr_nba": "1A5276",
    "hdr_cbb": "1E8449",
    "hdr_mix": "6C3483",
    "hdr_sum": "117A65",
    "hit": "27AE60",
    "miss": "E74C3C",
    "push": "F39C12",
    "tier_a": "D5F5E3",
    "tier_b": "D6EAF8",
    "tier_c": "FEF9E7",
    "tier_d": "FDEDEC",
    "goblin": "E8D5F5",
    "demon": "FDEDEC",
    "standard": "F2F3F4",
    "over": "D6EAF8",
    "under": "FDEBD0",
    "alt": "F2F3F4",
    "white": "FFFFFF",
    "nba": "EBF5FB",
    "cbb": "EAFAF1",
    "nhl": "EBF4FD",
    "hdr_nhl": "1A3A5C",
    "hdr_soccer": "1A5C2E",
    "soccer": "EAFBF1",
    "hdr_wcbb": "4A235A",
    "hdr_mlb": "922B21",
    "hdr_nba1q": "1F618D",
    "hdr_nba1h": "117A65",
    "wcbb": "F5EEF8",
    "mlb": "FDEDEC",
    "nba1q": "D6EAF8",
    "nba1h": "D5F5E3",
    "mix": "F5EEF8",
    "gold": "F9E79F",
}

PAYOUT = {
    2: {"power": 3.0,  "flex": 3.0},
    3: {"power": 6.0,  "flex": 3.0},
    4: {"power": 10.0, "flex": 6.0},
    5: {"power": 20.0, "flex": 10.0},
    6: {"power": 37.5, "flex": 25.0},
}


# Cross-pipeline showcase slips: PrizePicks caps at 6 legs.
CROSS_PIPELINE_MAX_LEGS = 6


def power_flex_payout_for_n(n_legs: int) -> tuple[float, float]:
    """PrizePicks-style multipliers; extrapolate beyond 6 legs conservatively."""
    n = int(n_legs)
    if n < 2:
        return 0.0, 0.0
    if n in PAYOUT:
        p = PAYOUT[n]
        return float(p["power"]), float(p["flex"])
    base_n = max(PAYOUT.keys())
    base = PAYOUT[base_n]
    extra = max(0, n - base_n)
    power = float(base["power"]) * (1.10**extra)
    flex = float(base["flex"]) * (1.08**extra)
    return round(power, 2), round(flex, 2)

# Props excluded from ticket pools based on empirical hit rates below break-even
# Blocked Shots NBA: 41.9% overall, too low for any ticket
# Combo props: small sample, unreliable
# NHL OVER props: 21.5% hit rate — never use OVER direction in NHL tickets
# Fantasy Score is excluded from ticket generation pending data integrity
# validation. It remains in all grade/ranking outputs so hit rates can be
# monitored. Remove from this set once validated.
TICKET_EXCLUDED_PROPS = {
    "fantasy score", "fantasy_score", "fantasy",
    "fg attempted",
    "fg made",
    "personal fouls",
    "blks+stls",
    "two pointers attempted",
}

TIER1_PROPS = {"points", "pts+rebs+asts", "pts+rebs", "pts+asts", "rebounds"}
TIER2_PROPS = {"assists", "3-pt made", "rebs+asts"}
TIER3_PROPS = {"steals", "blocked shots", "turnovers", "free throws made"}

UNDER_ALLOWED_PROPS = {"free throws attempted", "turnovers"}

NBA_EXCLUDED_PROPS = {
    "blocked shots", "3-pt attempted", "fg attempted",
    "free throws attempted", "two pointers attempted",
    "defensive rebounds", "offensive rebounds",
    "dunks", "quarters with 5+ points", "quarters with 3+ points",
    "points - 1st 3 minutes", "assists - 1st 3 minutes", "rebounds - 1st 3 minutes",
    "assists (combo)", "points (combo)", "rebounds (combo)", "3-pt made (combo)",
}

CBB_EXCLUDED_PROPS = {
    "fantasy",
    "points (combo)",
}

NHL_EXCLUDED_PROPS = {
    "shots on goal",    # 23.7% hit rate — worst NHL prop
    "goals",            # 36.1% overall
    "assists",          # 36.8% overall
    "plus/minus",       # 39.9% overall
}

SOCCER_EXCLUDED_PROPS = {
    "passes attempted",  # 0% hit rate
    "assists",           # 5.6% hit rate
    "goals",             # 9.7% hit rate
}


def _norm_prop_label(v: object) -> str:
    s = str(v or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


# NBA abbreviations sometimes differ vs other books (align to slate/step8 style).
_CROSSBOOK_NBA_TEAM_ALIASES: dict[str, str] = {
    "BKN": "BRK",
    "CHA": "CHA",
    "PHX": "PHO",
    "GSW": "GS",
    "NOP": "NO",
    "NYK": "NY",
    "SAS": "SA",
    "UTH": "UTA",
    "UTAH": "UTA",
}


def _join_sport_key(sport: object) -> str:
    s = str(sport or "").strip().upper()
    if s in ("NBA1Q", "NBA1H"):
        return "NBA"
    if s == "SOCCER":
        return "SOCCER"
    return s


def _norm_player_join(v: object) -> str:
    s = str(v or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s).strip()
    return s


def _norm_team_join(team: object, sport: object) -> str:
    t = str(team or "").strip().upper()
    sp = str(sport or "").strip().upper()
    if sp in ("NBA", "NBA1Q", "NBA1H"):
        return _CROSSBOOK_NBA_TEAM_ALIASES.get(t, t)
    return t


def _ud_join_sport(ud_sid: object) -> str:
    u = str(ud_sid or "").strip().upper()
    m = {"FIFA": "SOCCER", "MASL": "SOCCER", "UFL": "SOCCER", "EPL": "SOCCER"}
    return m.get(u, u)


def _load_underdog_alt_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    if df.empty:
        return pd.DataFrame(columns=["_js", "_jt", "_jp", "_jpr", "line_underdog"])
    df["line"] = pd.to_numeric(df.get("line", np.nan), errors="coerce")
    df = df[df["line"].notna()].copy()
    df["_js"] = df["ud_sport_id"].map(_ud_join_sport)
    df["_jt"] = df["team"].map(lambda x: str(x).strip().upper())
    df["_jp"] = df["player"].map(_norm_player_join)
    df["_jpr"] = df["prop_type"].map(_norm_prop_label)
    df = df[(df["_jp"] != "") & (df["_jpr"] != "")].copy()
    g = (
        df.groupby(["_js", "_jt", "_jp", "_jpr"], as_index=False)["line"]
        .mean()
        .rename(columns={"line": "line_underdog"})
    )
    return g


def _load_draftkings_alt_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    if df.empty:
        return pd.DataFrame(columns=["_js", "_jt", "_jp", "_jpr", "line_draftkings"])
    df["line"] = pd.to_numeric(df.get("line", np.nan), errors="coerce")
    df = df[df["line"].notna()].copy()
    if "board_sport" in df.columns:
        df["_js"] = df["board_sport"].map(lambda x: _join_sport_key(str(x).strip()))
    else:
        df["_js"] = ""
    lbl = df["dk_market_label"] if "dk_market_label" in df.columns else df.get("prop_type", "")
    df["_jpr"] = lbl.map(_norm_prop_label)
    df["_jt"] = df["team"].map(lambda x: str(x).strip().upper())
    df["_jp"] = df["player"].map(_norm_player_join)
    df = df[(df["_js"] != "") & (df["_jp"] != "") & (df["_jpr"] != "")].copy()
    g = (
        df.groupby(["_js", "_jt", "_jp", "_jpr"], as_index=False)["line"]
        .median()
        .rename(columns={"line": "line_draftkings"})
    )
    return g


def attach_alt_book_lines(
    combined: pd.DataFrame,
    *,
    underdog_csv: str = "",
    draftkings_csv: str = "",
) -> pd.DataFrame:
    """Left-merge Underdog / DraftKings numeric lines onto the combined slate (PrizePicks line stays in `line`)."""
    out = combined.copy()
    out["_js"] = out["sport"].map(_join_sport_key)
    out["_jt"] = [_norm_team_join(t, s) for t, s in zip(out["team"], out["sport"])]
    out["_jp"] = out["player"].map(_norm_player_join)
    out["_jpr"] = out["prop_type"].map(_norm_prop_label)
    join_on = ["_js", "_jt", "_jp", "_jpr"]

    u_path = (underdog_csv or "").strip()
    if u_path and os.path.isfile(u_path):
        try:
            ud = _load_underdog_alt_csv(u_path)
            if not ud.empty:
                out = out.merge(ud, on=join_on, how="left")
                n = int(out["line_underdog"].notna().sum())
                print(f"  [alt-books] Underdog lines joined: {n} / {len(out)} rows ({u_path})")
        except Exception as e:
            print(f"  [alt-books] WARN Underdog merge skipped: {e}")
    if "line_underdog" not in out.columns:
        out["line_underdog"] = np.nan

    d_path = (draftkings_csv or "").strip()
    if d_path and os.path.isfile(d_path):
        try:
            dk = _load_draftkings_alt_csv(d_path)
            if not dk.empty:
                out = out.merge(dk, on=join_on, how="left")
                n = int(out["line_draftkings"].notna().sum())
                print(f"  [alt-books] DraftKings lines joined: {n} / {len(out)} rows ({d_path})")
        except Exception as e:
            print(f"  [alt-books] WARN DraftKings merge skipped: {e}")
    if "line_draftkings" not in out.columns:
        out["line_draftkings"] = np.nan

    out = out.drop(columns=join_on, errors="ignore")
    return out


def propagate_alt_book_lines_to_sport_frame(
    sport_df: pd.DataFrame | None,
    combined: pd.DataFrame,
    sport_labels: tuple[str, ...],
) -> pd.DataFrame | None:
    """
    Copy line_underdog / line_draftkings from combined onto a per-sport slate (NBA Slate, CBB Slate, ...).
    Join keys match attach_alt_book_lines (team + player + prop norms).
    """
    if sport_df is None or len(sport_df) == 0:
        return sport_df
    out = sport_df.copy()
    if "line_underdog" not in combined.columns:
        out["line_underdog"] = np.nan
        out["line_draftkings"] = np.nan
        return out
    labels = {s.upper() for s in sport_labels}
    sub = combined[combined["sport"].astype(str).str.upper().isin(labels)].copy()
    if sub.empty:
        out["line_underdog"] = np.nan
        out["line_draftkings"] = np.nan
        return out
    sub = sub.copy()
    sub["_jt"] = [_norm_team_join(t, s) for t, s in zip(sub["team"], sub["sport"])]
    sub["_jp"] = sub["player"].map(_norm_player_join)
    sub["_jpr"] = sub["prop_type"].map(_norm_prop_label)
    agg = sub.groupby(["_jt", "_jp", "_jpr"], as_index=False).agg(
        {"line_underdog": "first", "line_draftkings": "first"}
    )
    out["_jt"] = [_norm_team_join(t, s) for t, s in zip(out["team"], out["sport"])]
    out["_jp"] = out["player"].map(_norm_player_join)
    out["_jpr"] = out["prop_type"].map(_norm_prop_label)
    out = out.merge(agg, on=["_jt", "_jp", "_jpr"], how="left")
    return out.drop(columns=["_jt", "_jp", "_jpr"])


def _prop_priority_bonus(v: object) -> float:
    p = _norm_prop_label(v)
    if p in TIER1_PROPS:
        return 0.10
    if p in TIER3_PROPS:
        return -0.10
    return 0.0


# ── Goblin / Demon payout adjustment ─────────────────────────────────────────
# Goblin: line moved in your favor → REDUCES payout LINEARLY per unit of
#   line_discount_vs_standard.  Each 0.5pt of line movement cuts payout by
#   GOBLIN_REDUCTION_PER_UNIT * 0.5.  Tune this constant against real
#   PrizePicks payout tables once you have more graded data.
#   Current value: 0.18 per unit → 0.5pt goblin ≈ 9% payout reduction per leg.
GOBLIN_REDUCTION_PER_UNIT: float = 0.18   # ← tune me
GOBLIN_MAX_REDUCTION:      float = 0.60   # single-leg reduction cap (60%)

# Demon: line moved against you → BOOSTS payout MULTIPLICATIVELY per unit.
#   DEMON_BOOST_PER_UNIT * discount applied as a multiplier per leg.
#   Current value: 0.28 per unit → 0.5pt demon ≈ 14% payout boost per leg.
DEMON_BOOST_PER_UNIT: float = 0.28        # ← tune me
DEMON_MAX_BOOST:      float = 2.50        # single-leg multiplier cap (2.5×)

# Min EV threshold for a ticket to be included in output.
# ev = est_win_prob × adj_power_payout.  ev < 1.0 means expected loss.
# Shorter slips use a lower bar so the web/workbook are not empty when payouts are modest.
MIN_TICKET_EV_DEFAULT: float = 1.20
MIN_TICKET_EV_BY_LEGS: dict = {
    2: 1.05,
    3: 1.08,
    4: 1.10,
}

LEG_PROB_FLOOR = 0.35
# Source-aware caps (ML > hit-rate > rank-score fallback)
LEG_PROB_CAPS = {
    "ml_prob": 0.85,
    "rank_score": 0.72,
    "edge": 0.70,
    "hit_rate": 0.72,
    "fallback_const": 0.65,
}
TICKET_PROB_FLOOR = 1e-6
TICKET_PROB_CAP = 0.999
RANK_SCORE_SIGMOID_SCALE = 0.4
DEFAULT_LEG_PROB_FALLBACK = 0.50

# Limit how many distinct generated slips may include the same player (reduces single-leg cascade risk).
MAX_SLIPS_PER_PLAYER = 2


def _ticket_cap_players_from_rows(rows: list) -> set[str]:
    out: set[str] = set()
    for r in rows:
        p = _norm_player_join(r.get("player"))
        if p:
            out.add(p)
    return out


def _ticket_cap_can_add(rows: list, counts: dict[str, int] | None, cap: int = MAX_SLIPS_PER_PLAYER) -> bool:
    if counts is None or cap <= 0:
        return True
    for p in _ticket_cap_players_from_rows(rows):
        if int(counts.get(p, 0)) >= cap:
            return False
    return True


def _ticket_cap_register(rows: list, counts: dict[str, int] | None) -> None:
    if not counts:
        return
    for p in _ticket_cap_players_from_rows(rows):
        counts[p] = int(counts.get(p, 0)) + 1


def min_ev_for_ticket(n_legs: int) -> float:
    return MIN_TICKET_EV_BY_LEGS.get(int(n_legs), MIN_TICKET_EV_DEFAULT)

def _norm_team_abbr(v: object) -> str:
    return str(v or "").strip().upper()


def calc_adjusted_payout(base_payout: float, legs: list) -> float:
    """
    Adjusts base_payout for Goblin (linear reduction) and Demon
    (multiplicative boost) legs based on how far each line was moved
    from the Standard reference (line_discount_vs_standard).

    Goblin legs reduce payout linearly:
        reduction = min(discount * GOBLIN_REDUCTION_PER_UNIT, GOBLIN_MAX_REDUCTION)
        multiplier *= (1 - reduction)

    Demon legs boost payout multiplicatively:
        boost = min(1 + discount * DEMON_BOOST_PER_UNIT, DEMON_MAX_BOOST)
        multiplier *= boost

    Standard legs: no adjustment (multiplier unchanged).

    Returns adjusted payout rounded to 2 decimal places.
    """
    multiplier = 1.0
    for leg in legs:
        pt = str(leg.get("pick_type", "")).strip()
        raw_discount = leg.get("line_discount_vs_standard")
        try:
            discount = abs(float(raw_discount))
        except (TypeError, ValueError):
            discount = 0.0

        if pt == "Goblin":
            reduction = min(discount * GOBLIN_REDUCTION_PER_UNIT, GOBLIN_MAX_REDUCTION)
            multiplier *= max(0.0, 1.0 - reduction)
        elif pt == "Demon":
            boost = min(1.0 + discount * DEMON_BOOST_PER_UNIT, DEMON_MAX_BOOST)
            multiplier *= boost
        # Standard: no change

    return round(base_payout * multiplier, 2)


def enrich_ticket_curve_payouts(ticket: dict, stake_unit: float = 1.0) -> None:
    """
    Adds flat_multiplier, est_multiplier, mult_error, EV columns using
    utils.goblin_demon_multiplier (delta_pct from line / standard_line per leg).
    Mutates ticket dict in place.
    """
    rows = ticket.get("rows") or []
    n = int(ticket.get("n_legs", len(rows)) or 0) or len(rows)
    legs_payload: list[dict] = []
    using_fb = False
    for r in rows:
        rd = r if isinstance(r, dict) else dict(r)
        sl = rd.get("standard_line")
        ln = rd.get("line")
        dp = gd_leg_delta_pct(ln, sl)
        pt = str(rd.get("pick_type") or "Standard")
        pl = pt.lower()
        if ("goblin" in pl or "demon" in pl) and dp is None:
            using_fb = True
        pr = rd.get("leg_prob_used")
        if pr is None:
            pr = rd.get("ml_prob")
        legs_payload.append(
            {
                "pick_type": pt,
                "delta_pct": dp,
                "line": ln,
                "standard_line": sl,
                "prob": pr,
            }
        )
    summ = gd_multiplier_summary(legs_payload, mode="power", stake=float(stake_unit))
    flat_p = float(summ.get("flat_mult") or 0.0)
    est_p = float(summ.get("est_mult") or flat_p)
    mult_err = float(summ.get("mult_delta") or 0.0)
    ticket["flat_multiplier"] = round(flat_p, 4)
    ticket["est_multiplier"] = round(est_p, 4)
    ticket["mult_error"] = round(mult_err, 4)
    ticket["est_payout"] = round(est_p * float(stake_unit), 4)
    ticket["flat_payout"] = round(flat_p * float(stake_unit), 4)
    ticket["payout_delta"] = round(ticket["est_payout"] - ticket["flat_payout"], 4)
    cp = summ.get("combined_prob")
    ticket["combined_hit_prob_curve"] = cp
    if cp is not None:
        ticket["est_ev"] = round(gd_compute_ticket_ev(est_p, float(cp), float(stake_unit)), 4)
        ticket["flat_ev"] = round(gd_compute_ticket_ev(flat_p, float(cp), float(stake_unit)), 4)
    if n >= 3:
        summ_f = gd_multiplier_summary(legs_payload, mode="flex", hits=n, stake=float(stake_unit))
        ticket["est_multiplier_flex_nn"] = summ_f.get("est_mult")
        ticket["flat_multiplier_flex_nn"] = summ_f.get("flat_mult")
    ticket["using_flat_fallback"] = bool(using_fb)
    if abs(mult_err) > 1.0:
        _log_slate.warning(
            "Ticket curve mult_error %.4f (>1.0 vs flat base): %s-leg slip",
            mult_err,
            n,
        )


# ── Per-leg count quality thresholds (used by smart ticket builder) ───────────
# Min hit rate required per leg depending on ticket length
# Longer tickets need higher floor because win prob = product of all hit rates
LEG_MIN_HIT_RATE = {
    2: 0.60,
    3: 0.62,
    4: 0.64,
    5: 0.66,
    6: 0.68,
}

# When --high-conviction: per-leg hit_rate floors (merged with LEG_MIN_HIT_RATE via max())
HIGH_CONVICTION_LEG_MIN_HIT_RATE = {
    2: 0.70,
    3: 0.72,
    4: 0.74,
    5: 0.76,
    6: 0.78,
}

# With --prioritize-ticket-hit: extra leg floors (merged via max() on top of strict pools).
PRIORITIZE_TICKET_HIT_LEG_MIN = {
    2: 0.75,
    3: 0.77,
    4: 0.79,
    5: 0.81,
    6: 0.83,
}

# Modeled probability floors (after leg-prob caps + correlation penalty). Tune if too few tickets emit.
MIN_PRIORITIZE_MODELED_POWER_WIN_PROB = 0.38
MIN_PRIORITIZE_MODELED_FLEX_CASH_PROB = 0.52


def effective_leg_min_hit_rates(
    high_conviction: bool,
    override: dict[int, float] | None = None,
    prioritize_ticket_hit: bool = False,
) -> dict[int, float]:
    """Per ticket length: minimum hit_rate (0–1) for each leg in build_tickets / FINAL groups."""
    out: dict[int, float] = {}
    for n in TICKET_LEG_SIZES:
        base = float(LEG_MIN_HIT_RATE.get(n, 0.55))
        if high_conviction:
            base = max(base, float(HIGH_CONVICTION_LEG_MIN_HIT_RATE.get(n, 0.70)))
        if prioritize_ticket_hit:
            base = max(base, float(PRIORITIZE_TICKET_HIT_LEG_MIN.get(n, 0.75)))
        if override and n in override:
            base = max(base, float(override[n]))
        out[n] = base
    return out


def ticket_leg_sizes_for_max(max_legs: int) -> list:
    m = int(max_legs)
    if m < MIN_TICKET_POOL:
        m = MIN_TICKET_POOL
    return [n for n in TICKET_LEG_SIZES if n <= m]

# Min tier per leg count for Power mode tickets
POWER_MIN_TIER = {
    2: ["A", "B", "C"],
    3: ["A", "B", "C"],   # 3-leg power: Tier A/B/C ok
    4: ["A", "B"],
    5: ["A", "B"],         # 5-leg power: Tier A/B only
    6: ["A", "B"],         # 6-leg power: Tier A/B only
}

# Cap fantasy-score concentration per ticket so slips are more diversified.
MAX_FANTASY_LEGS = {
    2: 1,
    3: 1,
    4: 2,
    5: 2,
    6: 2,
}

# Ticket leg counts written to workbook + FINAL web payload
TICKET_LEG_SIZES = [2, 3, 4, 5, 6]
MIN_TICKET_POOL = min(TICKET_LEG_SIZES)


def _is_fantasy_prop(row: pd.Series) -> bool:
    return "fantasy" in str(row.get("prop_type", "")).strip().lower()

# Demon legs are only allowed in Flex-mode analysis (too low hit rate for Power)
# This is enforced in build_tickets_smart() below


# ── Excel style helpers ───────────────────────────────────────────────────────
def side(color: str = "CCCCCC") -> Border:
    s = Side(style="thin", color=color)
    return Border(left=s, right=s, top=s, bottom=s)


# ── Style object caches (avoid recreating identical objects per cell) ──────────
_font_cache: dict = {}
_fill_cache: dict = {}
_align_cache: dict = {}
_border_obj = None

def _side_obj():
    global _border_obj
    if _border_obj is None:
        _border_obj = side()
    return _border_obj

def _font(bold=False, color="000000", sz=9):
    key = (bold, color, sz)
    if key not in _font_cache:
        _font_cache[key] = Font(bold=bold, name="Arial", size=sz, color=color)
    return _font_cache[key]

def _fill(bg):
    if bg not in _fill_cache:
        _fill_cache[bg] = PatternFill("solid", start_color=bg)
    return _fill_cache[bg]

def _align(horizontal="center", wrap=False):
    key = (horizontal, wrap)
    if key not in _align_cache:
        _align_cache[key] = Alignment(horizontal=horizontal, vertical="center", wrap_text=wrap)
    return _align_cache[key]


def hc(ws, r, c, v, bg=None, fc="FFFFFF", bold=True, sz=9, align="center"):
    cell = ws.cell(row=r, column=c, value=v)
    cell.font = _font(bold=bold, color=fc, sz=sz)
    if bg:
        cell.fill = _fill(bg)
    cell.alignment = _align(horizontal=align, wrap=True)
    cell.border = _side_obj()
    return cell


def dc(ws, r, c, v, bg=None, bold=False, sz=9, align="center", fc="000000", fmt=None):
    if v is pd.NA or (isinstance(v, float) and np.isnan(v)) or v is None:
        v = ""
    cell = ws.cell(row=r, column=c, value=v)
    cell.font = _font(bold=bold, color=fc, sz=sz)
    cell.fill = _fill(bg or C["white"])
    cell.alignment = _align(horizontal=align)
    cell.border = _side_obj()
    if fmt:
        cell.number_format = fmt
    return cell


def sw(ws, widths: List[int]):
    for ci, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(ci)].width = w


def tier_bg(t) -> str:
    return {"A": C["tier_a"], "B": C["tier_b"], "C": C["tier_c"], "D": C["tier_d"]}.get(
        str(t).upper(), C["white"]
    )


def pt_bg(pt) -> str:
    return {"Goblin": C["goblin"], "Demon": C["demon"], "Standard": C["standard"]}.get(pt, C["white"])


def hr_bg(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "DDDDDD"
    if v >= 0.65:
        return C["hit"]
    if v >= 0.50:
        return C["push"]
    return C["miss"]


def pct_cell(ws, r, c, val):
    nan = val is None or (isinstance(val, float) and np.isnan(val))
    bg = hr_bg(val) if not nan else "DDDDDD"
    cell = dc(ws, r, c, val if not nan else "", bg=bg, bold=True)
    if not nan:
        cell.number_format = "0%"
        cell.font = _font(bold=True, color="FFFFFF", sz=9)
    return cell


def _signal_float(v):
    """Parse numeric for bet-signal / context scoring (shared HTML + Excel)."""
    try:
        if v is None or v == "":
            return None
        if isinstance(v, float) and np.isnan(v):
            return None
        return float(v)
    except Exception:
        return None


def _clip_prob(p: float, source: str = "hit_rate") -> float:
    """
    Clip probability to floor/cap bounds.
    Caps are source-aware so ML probabilities are not suppressed as aggressively as
    hit-rate/rank-score derived fallbacks.
    """
    if p is None or not np.isfinite(p):
        return DEFAULT_LEG_PROB_FALLBACK
    src = str(source or "").strip()
    cap = float(LEG_PROB_CAPS.get(src, LEG_PROB_CAPS["hit_rate"]))
    return float(np.clip(float(p), LEG_PROB_FLOOR, cap))


def _rank_score_to_prob(rank_score: float) -> float:
    if rank_score is None or not np.isfinite(rank_score):
        return DEFAULT_LEG_PROB_FALLBACK
    prob = 1.0 / (1.0 + math.exp(-float(rank_score) * RANK_SCORE_SIGMOID_SCALE))
    return float(prob)


def _scalar_rank_to_prob_for_sort(x: object) -> float:
    try:
        if x is None or pd.isna(x):
            return DEFAULT_LEG_PROB_FALLBACK
        xf = float(x)
        if not np.isfinite(xf):
            return DEFAULT_LEG_PROB_FALLBACK
        return _rank_score_to_prob(xf)
    except (TypeError, ValueError):
        return DEFAULT_LEG_PROB_FALLBACK


def _attach_ticket_pick_order(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    """
    Add __ts_pri / __ts_sec for descending sort when assembling tickets.
    rank: primary = rank_score; ml: primary = ml_prob (missing last); blend: 0.5*ml + 0.5*sigmoid(rank),
    with missing ml falling back to sigmoid(rank) only.
    """
    out = df.copy()
    if out.empty:
        out["__ts_pri"] = pd.Series(dtype=float)
        out["__ts_sec"] = pd.Series(dtype=float)
        return out
    rs = pd.to_numeric(out["rank_score"], errors="coerce") if "rank_score" in out.columns else pd.Series(np.nan, index=out.index)
    ml = pd.to_numeric(out["ml_prob"], errors="coerce") if "ml_prob" in out.columns else pd.Series(np.nan, index=out.index)
    rs_p = rs.map(_scalar_rank_to_prob_for_sort)
    m = (mode or "rank").strip().lower()
    if m not in ("rank", "ml", "blend"):
        m = "rank"
    if m == "ml":
        out["__ts_pri"] = ml.fillna(-1.0)
        out["__ts_sec"] = rs.fillna(-1e9)
    elif m == "blend":
        ml_b = ml.where(ml.notna(), rs_p)
        out["__ts_pri"] = 0.5 * ml_b + 0.5 * rs_p
        out["__ts_sec"] = rs.fillna(-1e9)
    else:
        out["__ts_pri"] = rs.fillna(-1e9)
        out["__ts_sec"] = ml.fillna(-1.0)
    return out


def get_edge_threshold(sport: str, prop_type: str, pick_type: str) -> float:
    """
    Edge threshold used to center raw edge -> probability conversion.
    Kept simple for now; can be expanded to sport/prop/pick specific thresholds.
    """
    _ = (sport, prop_type, pick_type)
    return 0.0


def _resolve_leg_prob(row: pd.Series) -> tuple[float, str]:
    # Priority 1: calibrated ML probability.
    mlp = pd.to_numeric(row.get("ml_prob"), errors="coerce")
    if pd.notna(mlp) and 0.0 < float(mlp) < 1.0:
        return _clip_prob(float(mlp), "ml_prob"), "ml_prob"

    # Priority 2: rank_score sigmoid (composite signal).
    rs = pd.to_numeric(row.get("rank_score"), errors="coerce")
    if pd.notna(rs):
        return _clip_prob(_rank_score_to_prob(float(rs)), "rank_score"), "rank_score"

    # Priority 3: edge-to-probability (magnitude — signed raw edge punishes UNDERs).
    ae = pd.to_numeric(row.get("abs_edge"), errors="coerce")
    edge_raw = pd.to_numeric(row.get("edge"), errors="coerce")
    edge_mag = ae if pd.notna(ae) else (abs(float(edge_raw)) if pd.notna(edge_raw) else float("nan"))
    thresh = get_edge_threshold(
        row.get("sport", ""), row.get("prop_type", ""), row.get("pick_type", "")
    )
    if pd.notna(edge_mag):
        shifted = float(edge_mag) - float(thresh)
        prob = 1.0 / (1.0 + math.exp(-shifted * 0.6))
        return _clip_prob(prob, "edge"), "edge"

    # Priority 4: shrunk hit rate.
    hr = pd.to_numeric(row.get("hit_rate"), errors="coerce")
    if pd.notna(hr):
        hr_val = float(hr)
        if 1.0 < hr_val <= 100.0:
            hr_val = hr_val / 100.0
        if 0.0 < hr_val < 1.0:
            n = pd.to_numeric(row.get("l5_games", row.get("sample_n", 5)), errors="coerce")
            if pd.isna(n) or float(n) <= 0:
                n = 5.0
            hit_rate_shrunk = (hr_val * float(n) + 0.55 * 5.0) / (float(n) + 5.0)
            return _clip_prob(float(hit_rate_shrunk), "hit_rate"), "hit_rate"

    return DEFAULT_LEG_PROB_FALLBACK, "fallback_const"


def win_prob(leg_probs_with_source, _n_legs: int) -> float:
    vals = []
    for p, src in leg_probs_with_source:
        try:
            if p is None:
                continue
            if isinstance(p, float) and np.isnan(p):
                continue
            vals.append(_clip_prob(float(p), src))
        except Exception:
            continue
    if not vals:
        return TICKET_PROB_FLOOR
    return float(np.clip(np.prod(vals), TICKET_PROB_FLOOR, TICKET_PROB_CAP))


def flex_cash_prob(leg_probs_with_source: list) -> float:
    """
    Modeled P(flex pays) for independent legs: all hit OR exactly one miss (PrizePicks-style flex, n≥3).
    For n<3, same as power (product of leg probs).
    """
    vals: list[float] = []
    for p, src in leg_probs_with_source:
        try:
            if p is None or (isinstance(p, float) and np.isnan(p)):
                continue
            vals.append(_clip_prob(float(p), str(src)))
        except Exception:
            continue
    n = len(vals)
    if n < 3:
        return float(np.clip(np.prod(vals), TICKET_PROB_FLOOR, TICKET_PROB_CAP)) if vals else TICKET_PROB_FLOOR
    prod_all = float(np.prod(vals))
    one_miss = 0.0
    for i in range(n):
        miss = 1.0 - vals[i]
        rest = 1.0
        for j in range(n):
            if j != i:
                rest *= vals[j]
        one_miss += miss * rest
    return float(np.clip(prod_all + one_miss, TICKET_PROB_FLOOR, TICKET_PROB_CAP))


def _greedy_ticket_with_first_leg(cand: pd.DataFrame, n_legs: int, first_idx: int) -> list[pd.Series] | None:
    """Greedy unique-player fill in cand row order; first leg locked to cand.iloc[first_idx]."""
    cand = cand.reset_index(drop=True)
    if first_idx < 0 or first_idx >= len(cand):
        return None
    first = cand.iloc[first_idx]
    p0 = str(first.get("player", "") or "").strip().lower()
    if not p0:
        return None
    chosen: list[pd.Series] = [first]
    used = {p0}
    for i in range(len(cand)):
        if len(chosen) >= n_legs:
            break
        if i == first_idx:
            continue
        r = cand.iloc[i]
        p = str(r.get("player", "") or "").strip().lower()
        if not p or p in used:
            continue
        chosen.append(r)
        used.add(p)
    if len(chosen) < n_legs:
        return None
    return chosen


def _modeled_ticket_paid_score(rows: list[dict], flow: str, n_legs: int) -> tuple[float, float, float]:
    """(objective_score, ep_with_penalty, flex_cash_with_penalty). Objective = flex cash for flex n≥3 else power."""
    leg_probs = [_resolve_leg_prob(pd.Series(r)) for r in rows]
    cmult, _ = _correlation_multiplier_and_audit(rows)
    ep = win_prob(leg_probs, n_legs) * cmult
    flex_cash = flex_cash_prob(leg_probs) * cmult if n_legs >= 3 else ep
    if flow == "flex" and n_legs >= 3:
        score = flex_cash
    else:
        score = ep
    return score, ep, flex_cash


def _pick_best_greedy_ticket_by_paid_metric(
    cand: pd.DataFrame,
    n_legs: int,
    flow: str,
    ticket_gen_starts: int,
) -> tuple[list[dict] | None, float, float, float]:
    """
    Use the first K eligible rows (in sorted-candidate order) as alternative first legs; keep the
    combination with highest modeled ticket payout (P(all hit) for power-style, P(flex cash) for flex 3+).
    """
    cand = cand.reset_index(drop=True)
    eligible_idx = [i for i in range(len(cand)) if str(cand.iloc[i].get("player", "") or "").strip()]
    if not eligible_idx:
        return None, 0.0, 0.0, 0.0
    n_starts = max(1, min(int(ticket_gen_starts), len(eligible_idx)))
    best_rows: list[dict] | None = None
    best_score = float("-inf")
    best_ep = 0.0
    best_flex = 0.0
    for s in range(n_starts):
        first_idx = eligible_idx[s]
        chosen = _greedy_ticket_with_first_leg(cand, n_legs, first_idx)
        if not chosen:
            continue
        rows = [x.to_dict() for x in chosen]
        score, ep, fc = _modeled_ticket_paid_score(rows, flow, n_legs)
        if score > best_score:
            best_score = score
            best_rows = rows
            best_ep = ep
            best_flex = fc
    return best_rows, best_score, best_ep, best_flex


def _same_game_density_multiplier(ticket_rows: list) -> float:
    """
    Same-game correlation discount (legacy).
    Any game with 2+ legs gets multiplied by 0.94 ** (n_same_game_legs - 1).
    """
    from collections import Counter

    keys = []
    for r in ticket_rows:
        team = str(r.get("team", "")).strip().upper()
        opp = str(r.get("opp", r.get("opp_team", ""))).strip().upper()
        if not team or not opp:
            continue
        keys.append("|".join(sorted([team, opp])))

    counts = Counter(keys)
    mult = 1.0
    for _, n in counts.items():
        if n >= 2:
            mult *= 0.94 ** (n - 1)
    return float(mult)


def _correlation_multiplier_and_audit(ticket_rows: list) -> tuple[float, list[str]]:
    """
    Ticket score multiplier + human-readable audit trail.
    - Same-game density (0.94^(n-1) per congested game), logged as same_game_density.
    - Same team, same game (2+ legs on one side): ×0.85 (−15%).
    - Same player correlated stack (e.g. PTS+AST or combo props): ×1.05 (+5%), at most once per ticket.
    """
    audit: list[str] = []
    mult = _same_game_density_multiplier(ticket_rows)
    audit.append(f"same_game_density×{mult:.4f}")

    from collections import defaultdict

    game_team_counts: dict[str, dict[str, int]] = {}
    for r in ticket_rows:
        team = str(r.get("team", "")).strip().upper()
        opp = str(r.get("opp", r.get("opp_team", ""))).strip().upper()
        if not team or not opp:
            continue
        gk = "|".join(sorted([team, opp]))
        if gk not in game_team_counts:
            game_team_counts[gk] = defaultdict(int)
        game_team_counts[gk][team] += 1
    if any(any(n >= 2 for n in tc.values()) for tc in game_team_counts.values()):
        mult *= 0.85
        audit.append("same_team_same_game:-15%")

    by_player: dict[str, set[str]] = {}
    for r in ticket_rows:
        pl = str(r.get("player", "")).strip().lower()
        if not pl:
            continue
        tok = _norm_prop_label(r.get("prop_type", ""))
        by_player.setdefault(pl, set()).add(tok)

    for pl, pset in by_player.items():
        if len(pset) < 2:
            continue
        has_pts = "points" in pset
        has_ast = "assists" in pset
        has_reb = "rebounds" in pset
        combo = bool(
            pset
            & {
                "pts+asts",
                "pts+rebs",
                "rebs+asts",
                "pts+rebs+asts",
            }
        )
        if combo or (has_pts and has_ast) or (has_pts and has_ast and has_reb):
            mult *= 1.05
            audit.append(f"player_correlated_stack:+5%:{pl[:28]}")
            break

    return float(mult), audit


def _correlation_penalty(ticket_rows: list) -> float:
    """Backward-compatible multiplier only (no audit). Used by combined_ticket_grader."""
    return _correlation_multiplier_and_audit(ticket_rows)[0]


def kelly_fraction(win_prob: float, payout_mult: float, fraction: float = 0.25) -> float:
    """
    Fractional Kelly sizing.

    payout_mult is the total return multiplier (e.g., 3.0 means win returns 3x stake).
    Returns recommended fraction of bankroll to wager (0..1).
    """
    try:
        p = float(win_prob)
        b = float(payout_mult) - 1.0
    except Exception:
        return 0.0
    if b <= 0.0 or p <= 0.0 or p >= 1.0:
        return 0.0
    k = (p * b - (1.0 - p)) / b
    k = max(0.0, float(k))
    return float(k * float(fraction))


# ──────────────────────────────────────────────────────────────────────────────
# Path resolution helpers (fixes the “file not found” headaches)
# ──────────────────────────────────────────────────────────────────────────────
def _norm_path(p: str) -> str:
    p = (p or "").strip().strip('"').strip("'")
    p = os.path.expanduser(p)
    return os.path.abspath(p)


def _find_most_recent_by_filename(root_dir: str, filename: str) -> Optional[str]:
    """
    Recursively search root_dir for files matching filename (case-insensitive).
    Returns the most recently modified match so stale archive copies never win.
    Skips archive and old_* directories entirely.
    """
    SKIP_DIRS = {"archive", "old_outputs", "old_csv", "old_runs", "old_scripts", "__pycache__"}
    matches = []
    try:
        for base, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
            for f in files:
                if f.lower() == filename.lower():
                    full = os.path.join(base, f)
                    try:
                        matches.append((os.path.getmtime(full), full))
                    except OSError:
                        pass
    except Exception:
        return None
    if not matches:
        return None
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]


def _is_plain_filename(raw: str) -> bool:
    """True if `raw` is a single filename with no directory or drive (safe for repo-wide search)."""
    r = os.path.expanduser(raw.strip().strip('"').strip("'"))
    return os.path.dirname(r) == ""


def resolve_input_path(path: str, fallback_filename: Optional[str] = None) -> str:
    """
    Tries:
    1) exact path as provided
    2) relative to repo root (parent of scripts/)
    3) relative to script directory
    4) recursive search — only if `path` is a plain filename (no folders); picks most recent
       under repo root then scripts/, skips archive/old_* dirs. Never substitutes a different
       basename when the user gave an explicit relative/absolute path that is missing.
    """
    if not path:
        raise FileNotFoundError("Empty input path.")

    raw = path.strip().strip('"').strip("'")
    p = _norm_path(raw)
    if os.path.exists(p):
        return p

    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(script_dir, ".."))

    p_repo = os.path.abspath(os.path.join(repo_root, raw))
    if os.path.exists(p_repo):
        return p_repo

    p2 = os.path.abspath(os.path.join(script_dir, raw))
    if os.path.exists(p2):
        return p2

    if not _is_plain_filename(raw):
        filename = os.path.basename(raw)
        raise FileNotFoundError(
            f"Could not find file: {path}\nTried:\n- {p}\n- {p_repo}\n- {p2}\n"
            f"(no recursive search — path includes a directory and file is missing)"
        )

    filename = fallback_filename or os.path.basename(raw)
    for root in (repo_root, script_dir):
        found = _find_most_recent_by_filename(root, filename)
        if found and os.path.exists(found):
            print(f"  [resolve] Fallback found (most recent under {root}): {found}")
            return os.path.abspath(found)

    raise FileNotFoundError(
        f"Could not find file: {path}\nTried:\n- {p}\n- {p_repo}\n- {p2}\n- recursive search for: {filename}"
    )


# Columns expected after each loader normalizes (shared slate / attach_standard_refs)
_SLATE_CORE_COLS = (
    "tier", "rank_score", "player", "team", "opp", "prop_type", "pick_type",
    "line", "direction", "hit_rate", "edge", "game_time",
)


def _load_audit_row(sport: str, path: str, df: pd.DataFrame) -> None:
    pe = os.path.isfile(_norm_path(path.strip())) if (path or "").strip() else False
    miss = [c for c in _SLATE_CORE_COLS if c not in df.columns]
    miss_s = ",".join(miss) if miss else "(none)"
    print(f"  [audit {sport}] file_exists={'Y' if pe else 'N'} rows={len(df)} missing_core_cols={miss_s}")


def _extract_game_dates(game_time: pd.Series, target_year: int) -> pd.Series:
    """
    Build canonical YYYY-MM-DD game dates from mixed game_time formats:
    - ISO timestamps (e.g. 2026-03-23T19:00:00-04:00)
    - MM/DD HH:MMAM/PM (e.g. 03/23 07:00PM)
    """
    s = game_time.astype(str).fillna("").str.strip()
    out = pd.Series(pd.NA, index=s.index, dtype="object")

    # ISO-like or full datetime strings that already include a year.
    iso_like = s.str.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", na=False)
    dt_iso = pd.to_datetime(s.where(iso_like, None), errors="coerce")
    iso_mask = iso_like & dt_iso.notna()
    if iso_mask.any():
        out.loc[iso_mask] = dt_iso.loc[iso_mask].dt.strftime("%Y-%m-%d")

    # MM/DD fallback strings used in slate sheets.
    mmdd = s.str.extract(r"^\s*(\d{1,2})/(\d{1,2})(?:\b|[\sT])")
    mm_mask = mmdd[0].notna() & out.isna()
    if mm_mask.any():
        m = pd.to_numeric(mmdd.loc[mm_mask, 0], errors="coerce")
        d = pd.to_numeric(mmdd.loc[mm_mask, 1], errors="coerce")
        built = pd.to_datetime(
            {"year": target_year, "month": m, "day": d},
            errors="coerce",
        )
        ok = built.notna()
        if ok.any():
            out.loc[mm_mask[mm_mask].index[ok]] = built.loc[ok].dt.strftime("%Y-%m-%d")

    return out


def enforce_target_date(
    df: pd.DataFrame,
    sport: str,
    target_date: str,
    allow_cross_date_fallback: bool = False,
) -> pd.DataFrame:
    """
    Strict date behavior by default:
    - keep only rows matching target_date
    - if none, return empty (sport skipped)
    Optional fallback mode:
    - choose largest upcoming date (>= target) and tie-break by nearest date.
    Soccer: always applies that fallback when the target date has zero rows (PP slate often
    rolls to the next ET calendar day before US sports).
    """
    if df is None or df.empty:
        print(f"  [{sport} date] no rows to filter")
        return df
    if "game_time" not in df.columns:
        if allow_cross_date_fallback:
            print(f"  [{sport} date] missing game_time column; fallback enabled -> keeping {len(df)} rows")
            return df
        print(f"  [{sport} date] missing game_time column; strict mode -> skipping {sport}")
        return df.iloc[0:0].copy()

    out = df.copy()
    target_year = int(str(target_date)[:4])
    out["game_date"] = _extract_game_dates(out["game_time"], target_year)

    counts = out["game_date"].value_counts(dropna=True)
    if not counts.empty:
        top = ", ".join([f"{str(k)}={int(v)}" for k, v in counts.head(5).items()])
        print(f"  [{sport} date] available: {top}")
    else:
        if allow_cross_date_fallback:
            print(f"  [{sport} date] no parseable game_date values; fallback enabled -> keeping {len(out)} rows")
            return out
        print(f"  [{sport} date] no parseable game_date values; strict mode -> skipping {sport}")
        return out.iloc[0:0].copy()

    keep_mask = out["game_date"].eq(target_date)
    kept = int(keep_mask.sum())
    total = len(out)

    use_date_fallback = allow_cross_date_fallback or (sport == "Soccer")
    if kept == 0 and use_date_fallback:
        avail = [str(d) for d in counts.index.tolist() if str(d)]
        if avail:
            target_dt = pd.to_datetime(target_date, errors="coerce")
            candidates = [d for d in avail if d >= target_date] or avail

            def _key(d: str):
                c = int(counts.get(d, 0))
                dd = pd.to_datetime(d, errors="coerce")
                dist = abs((dd - target_dt).days) if pd.notna(dd) and pd.notna(target_dt) else 999999
                return (-c, dist, d)

            chosen = sorted(candidates, key=_key)[0]
            keep_mask = out["game_date"].eq(chosen)
            kept = int(keep_mask.sum())
            print(
                f"  [{sport} date] no rows for {target_date}; "
                f"date fallback -> using {chosen} ({kept} rows)"
            )

    filtered = out.loc[keep_mask].copy()
    dropped = total - len(filtered)
    print(f"  [{sport} date] kept {len(filtered)}/{total}, dropped {dropped}")

    if filtered.empty:
        print(f"  [{sport} date] WARNING: sport skipped for target date {target_date}")

    return filtered


# ──────────────────────────────────────────────────────────────────────────────
# Web outputs (static HTML + JSON) + player images
# ──────────────────────────────────────────────────────────────────────────────
def _safe_float(x, default=None):
    try:
        if x is None:
            return default
        if isinstance(x, float) and np.isnan(x):
            return default
        f = float(x)
        return f if np.isfinite(f) else default
    except Exception:
        return default


def _clean_id(x) -> str:
    """Return a clean integer-like string for IDs, or ''."""
    if x is None:
        return ""
    s = str(x).strip()
    if s == "" or s.lower() == "nan":
        return ""
    # handle 1628368.0
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    if re.fullmatch(r"\d+", s):
        return s
    return ""

def attach_standard_refs(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds Standard sibling references to every row (Standard/Goblin/Demon):
      - standard_line
      - standard_edge
      - standard_projection
      - line_discount_vs_standard (direction-aware)

    Matching key uses: sport, player, team, opp, prop_type, game_time
    Bulletproof: supports 'Projection' vs 'projection' and missing cols.
    """
    if df is None or df.empty:
        return df

    out = df.copy()

    # --- unify projection column name (Projection -> projection) ---
    if "projection" not in out.columns and "Projection" in out.columns:
        out["projection"] = out["Projection"]

    # Ensure required columns exist
    for c in [
        "sport", "player", "team", "opp", "prop_type", "pick_type",
        "direction", "line", "edge", "projection", "game_time"
    ]:
        if c not in out.columns:
            out[c] = pd.NA

    key_cols = ["sport", "player", "team", "opp", "prop_type", "game_time"]

    # Build Standard reference table
    std = out[out["pick_type"].astype(str).str.lower() == "standard"].copy()
    if std.empty:
        out["standard_line"] = pd.NA
        out["standard_edge"] = pd.NA
        out["standard_projection"] = pd.NA
        out["line_discount_vs_standard"] = pd.NA
        return out

    std_ref = (
        std[key_cols + ["line", "edge", "projection"]]
        .rename(columns={
            "line": "standard_line",
            "edge": "standard_edge",
            "projection": "standard_projection",
        })
        .drop_duplicates(subset=key_cols, keep="first")
    )

    out = out.merge(std_ref, on=key_cols, how="left")

    # Direction-aware "discount vs standard"
    def _discount(row):
        try:
            s = row.get("standard_line", pd.NA)
            l = row.get("line", pd.NA)
            if pd.isna(s) or pd.isna(l):
                return pd.NA
            d = str(row.get("direction", "")).upper().strip()
            s = float(s)
            l = float(l)
            if d == "OVER":
                return s - l
            if d == "UNDER":
                return l - s
            return pd.NA
        except Exception:
            return pd.NA

    out["line_discount_vs_standard"] = out.apply(_discount, axis=1)
    return out

def player_initials(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return "?"
    parts = [p for p in re.split(r"\s+", s) if p]
    if len(parts) == 1:
        return parts[0][:1].upper()
    return (parts[0][:1] + parts[-1][:1]).upper()


def compute_image_url(leg: dict) -> Optional[str]:
    """
    NBA:
      needs nba_player_id -> https://cdn.nba.com/headshots/nba/latest/1040x760/<id>.png
    CBB:
      needs espn_player_id -> https://a.espncdn.com/i/headshots/mens-college-basketball/players/full/<id>.png
    """
    sport = (leg.get("sport") or "").upper()
    if sport == "NBA":
        pid = _clean_id(leg.get("nba_player_id"))
        if pid:
            return f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png"
        return None
    if sport == "CBB":
        eid = _clean_id(leg.get("espn_player_id"))
        if eid:
            return f"https://a.espncdn.com/i/headshots/mens-college-basketball/players/full/{eid}.png"
        return None
    return None


def ticket_groups_to_payload(
    all_ticket_groups, date_str, thresholds, bankroll: float = 0.0, curve_stake_usd: float = 1.0
):
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "date": date_str,
        "filters": thresholds,
        "bankroll": float(bankroll) if bankroll and bankroll > 0 else None,
        "groups": [],
    }

    for group_name, tickets, _bg in all_ticket_groups:
        if not tickets:
            continue

        group = {
            "group_name": str(group_name),
            "n_legs": int(tickets[0].get("n_legs", 0) or 0),
            "power_payout": _safe_float(tickets[0].get("power_payout")),
            "flex_payout": _safe_float(tickets[0].get("flex_payout")),
            "tickets": [],
        }

        for ti, t in enumerate(tickets, start=1):
            enrich_ticket_curve_payouts(t, stake_unit=float(curve_stake_usd))
            rows = t.get("rows", [])
            slip = {
                "ticket_no": ti,
                "avg_hit_rate": _safe_float(t.get("avg_hit_rate")),
                "avg_rank_score": _safe_float(t.get("avg_rank_score")),
                "est_win_prob": _safe_float(t.get("est_win_prob")),
                "ticket_objective_score": _safe_float(t.get("ticket_objective_score")),
                "est_flex_cash_prob": _safe_float(t.get("est_flex_cash_prob")),
                "power_payout": _safe_float(t.get("power_payout")),
                "flex_payout": _safe_float(t.get("flex_payout")),
                "base_power_payout": _safe_float(t.get("base_power_payout")),
                "payout_multiplier": _safe_float(t.get("payout_multiplier")),
                "ev_power": _safe_float(t.get("ev_power")),
                "kelly_units": _safe_float(t.get("kelly_units")),
                "correlation_multiplier": _safe_float(t.get("correlation_multiplier")),
                "correlation_audit": list(t.get("correlation_audit") or []),
                "flat_multiplier": _safe_float(t.get("flat_multiplier")),
                "est_multiplier": _safe_float(t.get("est_multiplier")),
                "mult_error": _safe_float(t.get("mult_error")),
                "est_payout": _safe_float(t.get("est_payout")),
                "flat_payout": _safe_float(t.get("flat_payout")),
                "payout_delta": _safe_float(t.get("payout_delta")),
                "est_ev": _safe_float(t.get("est_ev")),
                "flat_ev": _safe_float(t.get("flat_ev")),
                "combined_hit_prob_curve": _safe_float(t.get("combined_hit_prob_curve")),
                "est_multiplier_flex_nn": _safe_float(t.get("est_multiplier_flex_nn")),
                "flat_multiplier_flex_nn": _safe_float(t.get("flat_multiplier_flex_nn")),
                "using_flat_fallback": bool(t.get("using_flat_fallback")),
                "has_data_warning": False,
                "legs": [],
            }

            for row in rows:

                def gv(field):
                    return row.get(field, "") if isinstance(row, dict) else getattr(row, field, "")

                _dpv = gd_leg_delta_pct(gv("line"), gv("standard_line"))
                leg = {
                    "sport": str(gv("sport") or ""),
                    "player": str(gv("player") or ""),
                    "team": str(gv("team") or ""),
                    "opp": str(gv("opp") or ""),
                    "prop_type": str(gv("prop_type") or ""),
                    "pick_type": str(gv("pick_type") or ""),
                    "direction": str(gv("direction") or ""),
                    "line": _safe_float(gv("line")),
                    "edge": _safe_float(gv("edge")),
                    "abs_edge": _safe_float(gv("abs_edge")),
                    "standard_line": _safe_float(gv("standard_line")),
                    "standard_edge": _safe_float(gv("standard_edge")),
                    "standard_projection": _safe_float(gv("standard_projection")),
                    "line_discount_vs_standard": _safe_float(gv("line_discount_vs_standard")),
                    "delta_pct": round(float(_dpv), 4) if _dpv is not None else None,
                    "hit_rate": _safe_float(gv("hit_rate")),
                    "ml_prob": _safe_float(gv("ml_prob")),
                    "rank_score": _safe_float(gv("rank_score")),
                    "game_time": str(gv("game_time") or ""),
                    "nba_player_id": gv("nba_player_id"),
                    "espn_player_id": gv("espn_player_id"),
                    "min_tier": str(gv("min_tier") or gv("minutes_tier") or gv("Min Tier") or ""),
                    "shot_role": str(gv("shot_role") or gv("Shot Role") or ""),
                    "usage_role": str(gv("usage_role") or gv("Usage Role") or ""),
                    "l5_avg": _safe_float(gv("l5_avg") or gv("Last 5 Avg") or gv("last_5_avg") or gv("intel_l5_avg")),
                    "season_avg": _safe_float(gv("season_avg") or gv("Season Avg") or gv("avg_season") or gv("intel_season_avg")),
                    "intel_season_hit_rate":   _safe_float(gv("intel_season_hit_rate")),
                    "intel_cushion":           _safe_float(gv("intel_cushion")),
                    "intel_cv_pct":            _safe_float(gv("intel_cv_pct")),
                    "intel_opp_vs_league_pct": _safe_float(gv("intel_opp_vs_league_pct")),
                    "intel_l5_vs_season":      _safe_float(gv("intel_l5_vs_season")),
                    "l5_over": _safe_float(gv("l5_over") or gv("L5 Over") or gv("line_hits_over_5")),
                    "l5_under": _safe_float(gv("l5_under") or gv("L5 Under") or gv("line_hits_under_5")),
                    "l5_side_hits": _safe_float(gv("l5_side_hits")),
                    "l5_consistency": _safe_float(gv("l5_consistency")),
                    "l10_over": _safe_float(gv("l10_over") or gv("L10 Over") or gv("hit_rate_over_L10") or gv("over_L10")),
                    "l10_under": _safe_float(gv("l10_under") or gv("L10 Under") or gv("hit_rate_under_L10") or gv("under_L10")),
                    "def_tier": str(gv("def_tier") or gv("Def Tier") or ""),
                    "pace_tier": str(gv("pace_tier") or gv("Pace Tier") or ""),
                    "context_score": _safe_float(gv("context_score")),
                    "usage_boost": _safe_float(gv("usage_boost")),
                    "usage_boost_reason": str(gv("usage_boost_reason") or ""),
                }
                leg["data_warning"] = "LIMITED_Q1_HISTORY" if str(leg.get("sport", "")).upper() == "NBA1Q" else None
                leg_prob_used, leg_prob_source = _resolve_leg_prob(pd.Series(leg))
                leg["leg_prob_used"] = _safe_float(leg_prob_used)
                leg["leg_prob_source"] = leg_prob_source
                leg["image_url"] = compute_image_url(leg)
                leg["initials"] = player_initials(leg.get("player", ""))
                br = float(bankroll) if bankroll and float(bankroll) > 0 else 0.0
                if br > 0:
                    p_raw = leg_prob_used if leg_prob_used is not None else _safe_float(gv("ml_prob"))
                    try:
                        p_f = float(p_raw) if p_raw is not None and not (isinstance(p_raw, float) and math.isnan(p_raw)) else 0.5
                    except (TypeError, ValueError):
                        p_f = 0.5
                    e_pct = leg_edge_pct_for_kelly(_safe_float(gv("ml_prob")), _safe_float(gv("edge")))
                    leg["recommended_stake_usd"] = fractional_kelly(e_pct, p_f, br)
                else:
                    leg["recommended_stake_usd"] = None

                slip["legs"].append(leg)
            slip["has_data_warning"] = any(bool(x.get("data_warning")) for x in slip["legs"])

            group["tickets"].append(slip)

        payload["groups"].append(group)

    return payload


def write_slate_json(nba, cbb, nhl, soccer, date_str, outdir,
                     wcbb=None, mlb=None, nba1q=None, nba1h=None):
    """Write full per-sport ranked slate to slate_latest.json for the web UI."""
    import math

    def safe(v):
        if v is None:
            return None
        try:
            if pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass
        try:
            if isinstance(v, float) and math.isnan(v):
                return None
        except Exception:
            pass
        if hasattr(v, 'item'):  # numpy scalar
            return v.item()
        return v

    def df_to_rows(df, sport_key):
        if df is None or len(df) == 0:
            return []
        col = lambda c: df[c] if c in df.columns else None
        rows = []
        for _, r in df.iterrows():
            def g(c):
                return safe(r[c]) if c in df.columns else None
            rows.append({
                "tier":       g("tier"),
                "rank_score": g("rank_score"),
                "player":     g("player") or "",
                "team":       g("team") or "",
                "opp":        g("opp") or "",
                "prop":       g("prop_type") or g("prop") or "",
                "pick_type":  g("pick_type") or "",
                "line":       g("line"),
                "dir":        g("direction") or g("dir") or "",
                "edge":       g("edge"),
                "hit_rate":   g("hit_rate"),
                "l5_avg":     g("l5_avg"),
                "l5_over":    g("l5_over"),
                "l5_under":   g("l5_under"),
                "l5_side_hits": g("l5_side_hits"),
                "l5_consistency": g("l5_consistency"),
                "game_time":  str(g("game_time") or ""),
            })
        return rows

    payload = {
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "sports": {
            "nba":    df_to_rows(nba,    "nba"),
            "cbb":    df_to_rows(cbb,    "cbb"),
            "nhl":    df_to_rows(nhl,    "nhl"),
            "soccer": df_to_rows(soccer, "soccer"),
            "wcbb":   df_to_rows(wcbb,   "wcbb"),
            "mlb":    df_to_rows(mlb,    "mlb"),
            "nba1q":  df_to_rows(nba1q,  "nba1q"),
            "nba1h":  df_to_rows(nba1h,  "nba1h"),
        }
    }

    os.makedirs(outdir, exist_ok=True)
    out_path = os.path.join(outdir, "slate_latest.json")
    with open(out_path, "w", encoding="utf-8") as f:
        import json as _json
        _json.dump(payload, f, ensure_ascii=False, default=str)
    print(f"  slate_latest.json -> {out_path}  ({sum(len(v) for v in payload['sports'].values())} props)")


def render_tickets_html(payload: dict) -> str:
    """Build full tickets page HTML from the same structure as tickets_latest.json."""

    def fmt_pct(x) -> str:
        try:
            if x is None:
                return ""
            return f"{float(x) * 100:.2f}%"
        except Exception:
            return ""

    def fmt_2(x) -> str:
        try:
            if x is None:
                return ""
            return f"{float(x):.2f}"
        except Exception:
            return ""

    def fmt_line(x) -> str:
        # keep lines readable (avoid 5.5000000003)
        try:
            if x is None:
                return ""
            xf = float(x)
            if abs(xf - round(xf)) < 1e-9:
                return str(int(round(xf)))
            return f"{xf:.2f}".rstrip("0").rstrip(".")
        except Exception:
            return str(x) if x is not None else ""

    # ── helpers ────────────────────────────────────────────────────────────────
    def hit_color(x) -> str:
        try:
            v = float(x)
            if v >= 0.65:
                return "#00F2FF"
            if v >= 0.50:
                return "#f0a500"
            return "#c96a74"
        except Exception:
            return "#c96a74"

    def sport_badge(sport: str) -> str:
        s = (sport or "").upper()
        if "NBA" in s:
            return "<span style='background:#c8ff00;color:#000;font-size:11px;font-weight:700;padding:2px 7px;border-radius:4px;letter-spacing:.04em;'>NBA</span>"
        if "CBB" in s or "NCAA" in s:
            return "<span style='background:#00e5ff;color:#000;font-size:11px;font-weight:700;padding:2px 7px;border-radius:4px;letter-spacing:.04em;'>CBB</span>"
        if "NHL" in s:
            return "<span style='background:#5bc4f5;color:#000;font-size:11px;font-weight:700;padding:2px 7px;border-radius:4px;letter-spacing:.04em;'>NHL</span>"
        if "SOCCER" in s:
            return "<span style='background:#57e87d;color:#000;font-size:11px;font-weight:700;padding:2px 7px;border-radius:4px;letter-spacing:.04em;'>SOC</span>"
        return f"<span style='background:#333;color:#ccc;font-size:11px;padding:2px 7px;border-radius:4px;'>{sport or ''}</span>"

    def badge(val, color="#00F2FF") -> str:
        if not val:
            return "<span style='color:#555;font-size:12px;'>—</span>"
        return f"<span style='background:rgba(0,0,0,.35);color:{color};font-size:12px;padding:2px 8px;border-radius:4px;border:1px solid {color}33;'>{val}</span>"

    def wp_bar(wp) -> str:
        try:
            pct = float(wp) * 100
            w = max(2, min(100, pct))
            col = "#00F2FF" if pct >= 50 else "#f0a500"
            return (
                f"<div style='display:flex;align-items:center;gap:8px;'>"
                f"<div style='flex:1;height:6px;background:#1a1a2e;border-radius:3px;overflow:hidden;'>"
                f"<div style='width:{w:.1f}%;height:100%;background:{col};border-radius:3px;'></div></div>"
                f"<span style='color:{col};font-family:\"Bebas Neue\",sans-serif;font-size:15px;letter-spacing:.05em;'>{pct:.1f}%</span>"
                f"</div>"
            )
        except Exception:
            return ""

    def direction_signal(leg: dict):
        """
        User-facing decision helper.
        Returns (signal_html, reason_text) based on available context.
        """
        score, reasons = compute_bet_signal_core(leg)
        joined = " + ".join(reasons) if reasons else ""
        if score >= 3:
            return "<span class='sig-strong'>STRONG</span>", joined or "aligned context"
        if score >= 2:
            return "<span class='sig-lean'>LEAN</span>", joined or "partial context"
        return "<span class='sig-risk'>RISKY</span>", joined or "limited context support"

    # ── HTML ───────────────────────────────────────────────────────────────────
    filters = payload.get("filters", {})
    gen_at  = payload.get("generated_at", "")
    date_declared_raw = (payload.get("date") or "").strip()
    date_declared = date_declared_raw[:10] if len(date_declared_raw) >= 10 else date_declared_raw

    def fmt_slate_date_pretty(iso: str) -> str:
        """M/D/YYYY from YYYY-MM-DD (no ambiguous 04-04 style)."""
        s = (iso or "").strip()[:10]
        if len(s) != 10 or s[4] != "-" or s[7] != "-":
            return (iso or "").strip() or "—"
        try:
            y, m, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
            if not (1 <= m <= 12 and 1 <= d <= 31):
                return iso
            return f"{m}/{d}/{y}"
        except (TypeError, ValueError):
            return iso or "—"

    def _calendar_date_from_game_time(gs: str) -> str | None:
        """Calendar YYYY-MM-DD in the prop's local offset (or parsed instant)."""
        s = (gs or "").strip()
        if not s:
            return None
        candidates = [s]
        if " " in s and "T" not in s.split(" ", 1)[0]:
            candidates.append(s.replace(" ", "T", 1))
        for cand in candidates:
            try:
                c2 = cand.replace("Z", "+00:00") if cand.endswith("Z") else cand
                dt = datetime.fromisoformat(c2)
                return dt.date().isoformat()
            except ValueError:
                continue
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            head = s[:10]
            if head[0:4].isdigit() and head[5:7].isdigit() and head[8:10].isdigit():
                return head
        return None

    def _modal_slate_date_from_legs(p: dict) -> str | None:
        counts: dict[str, int] = {}
        for g in p.get("groups") or []:
            for t in g.get("tickets") or []:
                for leg in t.get("legs") or []:
                    cd = _calendar_date_from_game_time(str(leg.get("game_time") or ""))
                    if cd:
                        counts[cd] = counts.get(cd, 0) + 1
        if not counts:
            return None
        return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]

    date_from_legs = _modal_slate_date_from_legs(payload)
    date_eff = date_from_legs or date_declared or ""
    if len(date_eff) > 10:
        date_eff = date_eff[:10]

    date_mismatch_html = ""
    if date_from_legs and date_declared and date_from_legs != date_declared:
        date_mismatch_html = (
            f' <span style="opacity:.65;font-size:11px;">file date {fmt_slate_date_pretty(date_declared)}</span>'
        )

    date_pretty = fmt_slate_date_pretty(date_eff)

    CSS = """
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Share+Tech+Mono&family=Inter:wght@600;700;800&display=swap');
*{box-sizing:border-box;margin:0;padding:0;}
:root{
  --bg:#050505;--surface:rgba(20,20,20,0.60);--card:rgba(20,20,20,0.60);--border:rgba(212,175,55,0.15);
  --accent:#d4af37;--cyan:#00F2FF;--muted:#999;--text:#e8e8f0;
}
body{background:var(--bg);color:var(--text);font-family:'Share Tech Mono',monospace;min-height:100vh;overflow-x:hidden;}

body::before{
  content:'';position:fixed;inset:0;
  background:
    radial-gradient(1200px 760px at -8% -18%, rgba(0,242,255,.12) 0%, transparent 56%),
    radial-gradient(980px 620px at 108% -8%, rgba(212,175,55,.18) 0%, transparent 54%),
    linear-gradient(180deg,#050505 0%,#080808 52%,#0f0f0f 100%);
  pointer-events:none;z-index:0;
}

/* scanlines */
body::after{content:'';position:fixed;inset:0;pointer-events:none;z-index:0;}

#app{position:relative;z-index:1;max-width:1400px;margin:0 auto;padding:0 20px 24px;}

/* nav */
nav{display:flex;align-items:center;gap:16px;padding:10px 0 12px;border-bottom:1px solid rgba(196,166,107,.22);flex-wrap:wrap;position:sticky;top:0;z-index:220;background:rgba(7,10,19,0.90);backdrop-filter:blur(22px) saturate(180%);}
.nav-logo{display:flex;align-items:center;gap:12px;text-decoration:none;}
.brain-wrap{display:none;}
.nav-logo::before{
  content:"";width:64px;height:40px;flex:0 0 64px;border-radius:10px;
  background:url('/static/hybrid-logo.png?v=20260320a') center/contain no-repeat;
  filter:drop-shadow(0 6px 14px rgba(0,0,0,.35));
}
.brain-slate{position:absolute;inset:0;border-radius:7px;background:linear-gradient(145deg,#12122a 0%,#080818 100%);border:1px solid #252545;animation:slateBreak 3.5s ease-in-out infinite;}
.brain-slate::before{content:'';position:absolute;inset:0;background:linear-gradient(to bottom right,transparent 47%,#c8ff0044 49%,transparent 51%),linear-gradient(to bottom left,transparent 44%,#c8ff0022 46%,transparent 48%),linear-gradient(to right,transparent 30%,#00e5ff22 31%,transparent 33%);border-radius:7px;animation:crackGlow 3.5s ease-in-out infinite;}
@keyframes slateBreak{0%,100%{transform:scale(1);box-shadow:0 0 0px #c8ff0000;}48%{transform:scale(1.06) rotate(-0.5deg);box-shadow:0 0 24px #c8ff0055;}50%{transform:scale(1.10) rotate(0.5deg);box-shadow:0 0 40px #c8ff0088;}52%{transform:scale(1.06) rotate(-0.3deg);box-shadow:0 0 24px #c8ff0055;}}
@keyframes crackGlow{0%,100%{opacity:0.2;}50%{opacity:1;}}
.brain-svg{position:absolute;inset:3px;animation:brainBreakthrough 3.5s ease-in-out infinite;transform-origin:center bottom;}
@keyframes brainBreakthrough{0%,100%{transform:scale(1) translateY(0px);filter:drop-shadow(0 0 5px #c8ff0099) drop-shadow(0 0 2px #00e5ff66);}48%{transform:scale(1.07) translateY(-1px);filter:drop-shadow(0 0 12px #c8ff00cc) drop-shadow(0 0 8px #00e5ffaa);}50%{transform:scale(1.18) translateY(-3px);filter:drop-shadow(0 0 20px #c8ff00ff) drop-shadow(0 0 14px #00e5ffcc) drop-shadow(0 0 40px #c8ff0044);}52%{transform:scale(1.07) translateY(-1px);filter:drop-shadow(0 0 12px #c8ff00cc) drop-shadow(0 0 8px #00e5ffaa);}}
.brain-pulse-ring{position:absolute;border-radius:9px;border:1.5px solid #c8ff00;opacity:0;animation:brainRingExpand 3.5s ease-out infinite;inset:-3px;}
.brain-pulse-ring:nth-child(2){border-color:#00e5ff;animation-delay:0.15s;}
.brain-pulse-ring:nth-child(3){border-color:#c8ff0088;animation-delay:0.3s;}
.brain-pulse-ring:nth-child(4){border-color:#00e5ff66;animation-delay:0.45s;}
@keyframes brainRingExpand{0%,48%{transform:scale(1);opacity:0;}50%{transform:scale(1);opacity:0.9;}85%{transform:scale(2.4);opacity:0;}100%{transform:scale(2.4);opacity:0;}}
.bspark{position:absolute;border-radius:50%;opacity:0;animation:bsparkFly 3.5s ease-out infinite;}
.bspark.lg{width:4px;height:4px;background:#c8ff00;box-shadow:0 0 6px #c8ff00;}
.bspark.md{width:3px;height:3px;background:#00e5ff;box-shadow:0 0 5px #00e5ff;}
.bspark.sm{width:2px;height:2px;background:#c8ff00cc;}
.bspark.cy{width:2px;height:2px;background:#00e5ffcc;}
.bspark.wh{width:2px;height:2px;background:#ffffffaa;}
.bspark:nth-child(5) {top:10%;left:5%; --tx:-18px;--ty:-16px;animation-delay:0.50s;}
.bspark:nth-child(6) {top:5%; left:40%;--tx:2px;  --ty:-22px;animation-delay:0.52s;}
.bspark:nth-child(7) {top:8%; left:75%;--tx:16px; --ty:-18px;animation-delay:0.54s;}
.bspark:nth-child(8) {top:30%;left:96%;--tx:22px; --ty:-8px; animation-delay:0.51s;}
.bspark:nth-child(9) {top:55%;left:96%;--tx:20px; --ty:8px;  animation-delay:0.53s;}
.bspark:nth-child(10){top:80%;left:86%;--tx:14px; --ty:16px; animation-delay:0.55s;}
.bspark:nth-child(11){top:92%;left:55%;--tx:4px;  --ty:22px; animation-delay:0.50s;}
.bspark:nth-child(12){top:90%;left:25%;--tx:-10px;--ty:20px; animation-delay:0.52s;}
.bspark:nth-child(13){top:72%;left:2%; --tx:-20px;--ty:12px; animation-delay:0.54s;}
.bspark:nth-child(14){top:45%;left:0%; --tx:-22px;--ty:0px;  animation-delay:0.51s;}
.bspark:nth-child(15){top:20%;left:2%; --tx:-18px;--ty:-12px;animation-delay:0.56s;}
.bspark:nth-child(16){top:15%;left:60%;--tx:10px; --ty:-20px;animation-delay:0.53s;}
.bspark:nth-child(17){top:18%;left:20%;--tx:-14px;--ty:-18px;animation-delay:0.65s;}
.bspark:nth-child(18){top:12%;left:55%;--tx:6px;  --ty:-20px;animation-delay:0.67s;}
.bspark:nth-child(19){top:25%;left:88%;--tx:18px; --ty:-14px;animation-delay:0.66s;}
.bspark:nth-child(20){top:60%;left:93%;--tx:18px; --ty:10px; animation-delay:0.68s;}
.bspark:nth-child(21){top:82%;left:70%;--tx:10px; --ty:18px; animation-delay:0.65s;}
.bspark:nth-child(22){top:80%;left:10%;--tx:-16px;--ty:14px; animation-delay:0.67s;}
.bspark:nth-child(23){top:40%;left:2%; --tx:-20px;--ty:4px;  animation-delay:0.69s;}
.bspark:nth-child(24){top:35%;left:93%;--tx:20px; --ty:-4px; animation-delay:0.66s;}
.bspark:nth-child(25){top:3%; left:30%;--tx:-6px; --ty:-24px;animation-delay:0.72s;}
.bspark:nth-child(26){top:3%; left:65%;--tx:8px;  --ty:-24px;animation-delay:0.70s;}
.bspark:nth-child(27){top:50%;left:98%;--tx:24px; --ty:2px;  animation-delay:0.73s;}
.bspark:nth-child(28){top:50%;left:0%; --tx:-24px;--ty:2px;  animation-delay:0.71s;}
@keyframes bsparkFly{0%,47%{opacity:0;transform:translate(0,0) scale(0);}50%{opacity:1;transform:translate(0,0) scale(1);}75%{opacity:0.5;}95%{opacity:0;transform:translate(var(--tx),var(--ty)) scale(0.2);}100%{opacity:0;transform:translate(var(--tx),var(--ty)) scale(0);}}
.brand{font-family:'Inter',sans-serif;font-size:34px;font-weight:700;letter-spacing:-0.5px;color:#ffffff;line-height:1;text-shadow:0 1px 10px rgba(0,0,0,.35);}
.brand span{color:var(--accent);font-weight:800;}
.nav-links{display:flex;gap:8px;margin-left:auto;flex-wrap:wrap;}
.nav-links a{color:#aaa;text-decoration:none;font-size:13px;padding:6px 14px;border-radius:6px;border:1px solid transparent;transition:all .2s;}
.nav-links a:hover{color:var(--text);border-color:var(--border);}
.nav-links a.active{color:var(--accent);border-color:var(--accent);background:rgba(225,188,101,.10);}
/* player graph expand */
.leg-row{cursor:pointer;transition:background .15s;}
.leg-row:hover{background:rgba(200,255,0,.04);}
.leg-graph-row{display:none;}
.leg-graph-row.open{display:table-row;}
.leg-graph-cell{padding:12px 16px 16px;background:#0d1117;border-bottom:1px solid var(--border);}
.graph-wrap{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap;}
.graph-stats{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:10px;}
.gstat{background:#1a1f2e;border:1px solid var(--border);border-radius:6px;padding:6px 12px;min-width:80px;text-align:center;}
.gstat-label{font-size:10px;color:#888;text-transform:uppercase;letter-spacing:.5px;}
.gstat-val{font-size:15px;font-weight:700;color:var(--accent);margin-top:2px;}
.graph-canvas-wrap{flex:1;min-width:260px;max-width:480px;}
canvas.leg-chart{width:100%!important;height:140px!important;}

/* hero */
.hero{margin:28px 0 20px;display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;}
.hero h1{font-family:'Bebas Neue',sans-serif;font-size:clamp(32px,5vw,52px);letter-spacing:.08em;line-height:1;color:var(--accent);}
.hero h1 span{color:var(--cyan);}
.meta{color:var(--muted);font-size:12px;margin-top:4px;}

/* filter pill */
.filter-pill{background:rgba(14,18,34,.72);border:1px solid rgba(196,166,107,.20);border-radius:12px;padding:10px 16px;font-size:12px;color:#9aa4b2;margin-bottom:24px;backdrop-filter:blur(10px);}
.filter-pill strong{color:var(--cyan);}

/* group */
.group{background:linear-gradient(160deg,rgba(22,27,47,.74) 0%,rgba(12,16,32,.70) 100%);border:1px solid rgba(196,166,107,.24);border-radius:16px;padding:20px;margin-bottom:24px;box-shadow:0 14px 32px rgba(0,0,0,.30),0 0 0 1px rgba(255,255,255,.03) inset;backdrop-filter:blur(12px);}
.group-hdr{display:flex;align-items:center;gap:12px;margin-bottom:16px;}
.group-title{font-family:'Bebas Neue',sans-serif;font-size:22px;letter-spacing:.08em;color:var(--accent);}
.group-meta{color:var(--muted);font-size:12px;}

/* ticket card */
.ticket{background:linear-gradient(160deg,rgba(24,30,52,.72) 0%,rgba(13,18,35,.68) 100%);border:1px solid rgba(196,166,107,.22);border-radius:14px;margin-bottom:16px;overflow:hidden;transition:transform .2s,box-shadow .2s;backdrop-filter:blur(10px);}
.ticket:hover{transform:translateY(-2px);box-shadow:0 10px 28px rgba(0,0,0,.35),0 0 0 1px rgba(196,166,107,.20) inset;}
.ticket-accent{width:5px;background:linear-gradient(180deg,var(--accent),var(--cyan));flex-shrink:0;}
.ticket-inner{display:flex;}
.ticket-body{flex:1;padding:14px 16px;}
.ticket-hdr{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap;}
.ticket-no{font-family:'Bebas Neue',sans-serif;font-size:18px;letter-spacing:.08em;color:var(--text);}
.kpi-row{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px;}
.kpi{display:flex;flex-direction:column;gap:2px;}
.kpi-label{font-size:10px;color:var(--muted);letter-spacing:.08em;text-transform:uppercase;}
.kpi-val{font-family:'Bebas Neue',sans-serif;font-size:20px;letter-spacing:.05em;}

/* table */
table{width:100%;border-collapse:collapse;}
th{background:rgba(225,188,101,.10);color:var(--accent);font-family:'Bebas Neue',sans-serif;font-size:13px;letter-spacing:.08em;padding:8px 10px;text-align:left;border-bottom:1px solid rgba(196,166,107,.28);}
td{padding:8px 10px;border-bottom:1px solid rgba(255,255,255,.06);font-size:12px;vertical-align:middle;}
tr:last-child td{border-bottom:none;}
tr:hover td{background:rgba(225,188,101,.06);}

html[data-theme="light"] body{
  background:
    radial-gradient(1200px 760px at -12% -22%, rgba(213,225,255,.76) 0%, transparent 56%),
    radial-gradient(980px 640px at 108% -8%, rgba(255,227,190,.72) 0%, transparent 54%),
    linear-gradient(180deg,#fcfdff 0%,#f6f8ff 45%,#f8f2e8 100%);
  color:#1f2430;
}
html[data-theme="light"] body::before{
  background:
    radial-gradient(1200px 760px at -12% -22%, rgba(213,225,255,.76) 0%, transparent 56%),
    radial-gradient(980px 640px at 108% -8%, rgba(255,227,190,.72) 0%, transparent 54%);
}
html[data-theme="light"] body::after{display:none;}
html[data-theme="light"] nav{
  background:rgba(255,255,255,.84);
  border-bottom:1px solid rgba(196,166,107,.24);
}
html[data-theme="light"] .filter-pill,
html[data-theme="light"] .group,
html[data-theme="light"] .ticket{
  background:rgba(255,255,255,.74);
  border:1px solid rgba(196,166,107,.22);
}

/* player cell */
.pwrap{display:flex;gap:8px;align-items:center;}
.avatar{width:30px;height:30px;border-radius:50%;overflow:hidden;border:1px solid var(--border);flex-shrink:0;background:#1a1a2e;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:var(--accent);}
.avatar img{width:100%;height:100%;object-fit:cover;}

/* dir badges */
.dir-over{background:rgba(0,242,255,.15);color:#00F2FF;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;}
.dir-under{background:rgba(240,165,0,.15);color:#f0a500;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;}
.delta-badge{font-family:'Share Tech Mono',monospace;font-size:10px;padding:2px 6px;border-radius:6px;border:1px solid;margin-left:6px;vertical-align:middle;white-space:nowrap;}
.sig-strong{background:rgba(0,242,255,.16);color:#00F2FF;border:1px solid rgba(0,242,255,.35);padding:3px 8px;border-radius:5px;font-size:11px;font-weight:700;display:inline-block;}
.sig-lean{background:rgba(240,165,0,.16);color:#f0a500;border:1px solid rgba(240,165,0,.35);padding:3px 8px;border-radius:5px;font-size:11px;font-weight:700;display:inline-block;}
.sig-risk{background:rgba(201,106,116,.16);color:#c96a74;border:1px solid rgba(201,106,116,.35);padding:3px 8px;border-radius:5px;font-size:11px;font-weight:700;display:inline-block;}
.why-note{color:#bfc5d4;font-size:11px;line-height:1.25;}

.ca-border{position:relative;isolation:isolate;}
.ca-border::before{
  content:"";position:absolute;inset:0;padding:1px;border-radius:inherit;pointer-events:none;
  background:linear-gradient(120deg, rgba(212,175,55,.35), rgba(0,242,255,.20), rgba(212,175,55,.35));
  -webkit-mask:linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite:xor;mask-composite:exclude;opacity:.45;
}
.mouse-glow{position:relative;overflow:hidden;}
.mouse-glow::after{
  content:"";position:absolute;left:var(--mx,50%);top:var(--my,50%);width:340px;height:340px;pointer-events:none;
  transform:translate(-50%,-50%);background:radial-gradient(circle, rgba(212,175,55,.18) 0%, rgba(0,242,255,.08) 28%, transparent 65%);
  opacity:0;transition:opacity .25s;
}
.mouse-glow:hover::after{opacity:1;}

/* responsive */
@media(max-width:640px){
  .kpi-row{gap:10px;}
  th,td{padding:6px 6px;font-size:11px;}
}
"""

    html_parts = []
    html_parts.append(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>PropOracle — Tickets · {date_pretty}</title>
<style>{CSS}</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/chartjs-plugin-annotation/3.0.1/chartjs-plugin-annotation.min.js"></script>
</head>
<body>
<div id="app">

<nav>
  <a class="nav-logo" href="/">
    <div class="brain-wrap">
      <div class="brain-slate"></div>
      <div class="brain-pulse-ring"></div>
      <div class="brain-pulse-ring"></div>
      <div class="brain-pulse-ring"></div>
      <div class="brain-pulse-ring"></div>
      <div class="bspark lg"></div><div class="bspark md"></div>
      <div class="bspark sm"></div><div class="bspark lg"></div>
      <div class="bspark cy"></div><div class="bspark md"></div>
      <div class="bspark sm"></div><div class="bspark lg"></div>
      <div class="bspark cy"></div><div class="bspark md"></div>
      <div class="bspark lg"></div><div class="bspark sm"></div>
      <div class="bspark cy"></div><div class="bspark sm"></div>
      <div class="bspark md"></div><div class="bspark cy"></div>
      <div class="bspark sm"></div><div class="bspark md"></div>
      <div class="bspark lg"></div><div class="bspark wh"></div>
      <svg class="brain-svg" viewBox="0 0 50 50" fill="none" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="lgL" x1="6" y1="6" x2="25" y2="44" gradientUnits="userSpaceOnUse">
            <stop offset="0%" stop-color="#c8ff00" stop-opacity="0.35"/>
            <stop offset="60%" stop-color="#c8ff00" stop-opacity="0.12"/>
            <stop offset="100%" stop-color="#c8ff00" stop-opacity="0.06"/>
          </linearGradient>
          <linearGradient id="lgR" x1="44" y1="6" x2="25" y2="44" gradientUnits="userSpaceOnUse">
            <stop offset="0%" stop-color="#00e5ff" stop-opacity="0.35"/>
            <stop offset="60%" stop-color="#00e5ff" stop-opacity="0.12"/>
            <stop offset="100%" stop-color="#00e5ff" stop-opacity="0.06"/>
          </linearGradient>
          <filter id="nglow"><feGaussianBlur stdDeviation="0.8" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
        </defs>
        <path d="M25 7 C22 7 18 8 15 10 C12 12 10 15 9 18 C8 21 8.5 24 9 26 C7.5 27.5 7 30 7.5 32.5 C8 35 10 37.5 13 39 C15 40 17 40 19 39.5 C20.5 39 22 38 23 37 L23 9 C23.5 8 24 7.5 25 7Z" fill="url(#lgL)" stroke="#c8ff00" stroke-width="0.9"/>
        <path d="M15 10 C13 11 11 13 11 15 C11 17 12.5 18.5 14 18" stroke="#c8ff00" stroke-width="0.7" stroke-linecap="round" fill="none" opacity="0.6"/>
        <path d="M9 19 C10.5 18 12 19 13.5 18"    stroke="#c8ff00" stroke-width="0.75" stroke-linecap="round" fill="none" opacity="0.8"/>
        <path d="M8.5 23 C10 22 12 23 13.5 22"    stroke="#c8ff00" stroke-width="0.75" stroke-linecap="round" fill="none" opacity="0.8"/>
        <path d="M8 27 C9.5 26 11.5 27 13 26.5"   stroke="#c8ff00" stroke-width="0.7"  stroke-linecap="round" fill="none" opacity="0.7"/>
        <path d="M8.5 31 C10 30.5 12 31 13.5 30.5" stroke="#c8ff00" stroke-width="0.7" stroke-linecap="round" fill="none" opacity="0.7"/>
        <path d="M10 35 C11.5 34.5 13.5 35 15 34.5" stroke="#c8ff00" stroke-width="0.65" stroke-linecap="round" fill="none" opacity="0.55"/>
        <path d="M16 14 C17 13.5 18.5 14 19.5 13.5"  stroke="#c8ff00" stroke-width="0.6" stroke-linecap="round" fill="none" opacity="0.5"/>
        <path d="M15.5 20 C17 19.5 18.5 20 20 19.5"  stroke="#c8ff00" stroke-width="0.6" stroke-linecap="round" fill="none" opacity="0.5"/>
        <path d="M15 27 C16.5 26.5 18 27 19.5 26.5"  stroke="#c8ff00" stroke-width="0.6" stroke-linecap="round" fill="none" opacity="0.5"/>
        <path d="M15 33 C16.5 32.5 18.5 33 20 32.5"  stroke="#c8ff00" stroke-width="0.6" stroke-linecap="round" fill="none" opacity="0.5"/>
        <path d="M25 7 C28 7 32 8 35 10 C38 12 40 15 41 18 C42 21 41.5 24 41 26 C42.5 27.5 43 30 42.5 32.5 C42 35 40 37.5 37 39 C35 40 33 40 31 39.5 C29.5 39 28 38 27 37 L27 9 C26.5 8 26 7.5 25 7Z" fill="url(#lgR)" stroke="#00e5ff" stroke-width="0.9"/>
        <path d="M35 10 C37 11 39 13 39 15 C39 17 37.5 18.5 36 18" stroke="#00e5ff" stroke-width="0.7" stroke-linecap="round" fill="none" opacity="0.6"/>
        <path d="M41 19 C39.5 18 38 19 36.5 18"      stroke="#00e5ff" stroke-width="0.75" stroke-linecap="round" fill="none" opacity="0.8"/>
        <path d="M41.5 23 C40 22 38 23 36.5 22"      stroke="#00e5ff" stroke-width="0.75" stroke-linecap="round" fill="none" opacity="0.8"/>
        <path d="M42 27 C40.5 26 38.5 27 37 26.5"    stroke="#00e5ff" stroke-width="0.7"  stroke-linecap="round" fill="none" opacity="0.7"/>
        <path d="M41.5 31 C40 30.5 38 31 36.5 30.5"  stroke="#00e5ff" stroke-width="0.7"  stroke-linecap="round" fill="none" opacity="0.7"/>
        <path d="M40 35 C38.5 34.5 36.5 35 35 34.5"  stroke="#00e5ff" stroke-width="0.65" stroke-linecap="round" fill="none" opacity="0.55"/>
        <path d="M34 14 C33 13.5 31.5 14 30.5 13.5"  stroke="#00e5ff" stroke-width="0.6" stroke-linecap="round" fill="none" opacity="0.5"/>
        <path d="M34.5 20 C33 19.5 31.5 20 30 19.5"  stroke="#00e5ff" stroke-width="0.6" stroke-linecap="round" fill="none" opacity="0.5"/>
        <path d="M35 27 C33.5 26.5 32 27 30.5 26.5"  stroke="#00e5ff" stroke-width="0.6" stroke-linecap="round" fill="none" opacity="0.5"/>
        <path d="M35 33 C33.5 32.5 31.5 33 30 32.5"  stroke="#00e5ff" stroke-width="0.6" stroke-linecap="round" fill="none" opacity="0.5"/>
        <line x1="25" y1="8" x2="25" y2="38" stroke="#ffffff22" stroke-width="0.6" stroke-dasharray="2.5,2"/>
        <circle cx="13" cy="16" r="1.4" fill="#c8ff00" filter="url(#nglow)"><animate attributeName="opacity" values="1;0.15;1" dur="1.7s" repeatCount="indefinite"/><animate attributeName="r" values="1.4;0.9;1.4" dur="1.7s" repeatCount="indefinite"/></circle>
        <circle cx="11" cy="22" r="1.2" fill="#c8ff00" filter="url(#nglow)"><animate attributeName="opacity" values="0.8;0.1;0.8" dur="2.2s" repeatCount="indefinite" begin="0.3s"/></circle>
        <circle cx="12" cy="28.5" r="1.3" fill="#c8ff00" filter="url(#nglow)"><animate attributeName="opacity" values="0.9;0.2;0.9" dur="1.9s" repeatCount="indefinite" begin="0.6s"/></circle>
        <circle cx="15" cy="34.5" r="1.1" fill="#c8ff00" filter="url(#nglow)"><animate attributeName="opacity" values="0.7;0.1;0.7" dur="2.4s" repeatCount="indefinite" begin="0.9s"/></circle>
        <circle cx="19" cy="18" r="1.0" fill="#c8ff00" filter="url(#nglow)"><animate attributeName="opacity" values="0.6;0.1;0.6" dur="2.0s" repeatCount="indefinite" begin="1.1s"/></circle>
        <circle cx="18" cy="30" r="1.0" fill="#c8ff00" filter="url(#nglow)"><animate attributeName="opacity" values="0.7;0.15;0.7" dur="1.6s" repeatCount="indefinite" begin="0.5s"/></circle>
        <circle cx="37" cy="16" r="1.4" fill="#00e5ff" filter="url(#nglow)"><animate attributeName="opacity" values="1;0.15;1" dur="2.0s" repeatCount="indefinite" begin="0.2s"/><animate attributeName="r" values="1.4;0.9;1.4" dur="2.0s" repeatCount="indefinite" begin="0.2s"/></circle>
        <circle cx="39" cy="22" r="1.2" fill="#00e5ff" filter="url(#nglow)"><animate attributeName="opacity" values="0.8;0.1;0.8" dur="1.8s" repeatCount="indefinite" begin="0.5s"/></circle>
        <circle cx="38" cy="28.5" r="1.3" fill="#00e5ff" filter="url(#nglow)"><animate attributeName="opacity" values="0.9;0.2;0.9" dur="2.3s" repeatCount="indefinite" begin="0.8s"/></circle>
        <circle cx="35" cy="34.5" r="1.1" fill="#00e5ff" filter="url(#nglow)"><animate attributeName="opacity" values="0.7;0.1;0.7" dur="1.7s" repeatCount="indefinite" begin="1.0s"/></circle>
        <circle cx="31" cy="18" r="1.0" fill="#00e5ff" filter="url(#nglow)"><animate attributeName="opacity" values="0.6;0.1;0.6" dur="2.1s" repeatCount="indefinite" begin="1.2s"/></circle>
        <circle cx="32" cy="30" r="1.0" fill="#00e5ff" filter="url(#nglow)"><animate attributeName="opacity" values="0.7;0.15;0.7" dur="1.5s" repeatCount="indefinite" begin="0.4s"/></circle>
        <line x1="13" y1="16" x2="37" y2="16" stroke="#c8ff0030" stroke-width="0.6"><animate attributeName="opacity" values="0.2;0.9;0.2" dur="1.7s" repeatCount="indefinite"/></line>
        <line x1="11" y1="22" x2="39" y2="22" stroke="#00e5ff30" stroke-width="0.6"><animate attributeName="opacity" values="0.2;0.9;0.2" dur="2.2s" repeatCount="indefinite" begin="0.4s"/></line>
        <line x1="12" y1="28.5" x2="38" y2="28.5" stroke="#c8ff0030" stroke-width="0.6"><animate attributeName="opacity" values="0.2;0.8;0.2" dur="1.9s" repeatCount="indefinite" begin="0.7s"/></line>
        <line x1="15" y1="34.5" x2="35" y2="34.5" stroke="#00e5ff30" stroke-width="0.6"><animate attributeName="opacity" values="0.2;0.8;0.2" dur="2.4s" repeatCount="indefinite" begin="1.0s"/></line>
        <line x1="13" y1="16" x2="39" y2="22" stroke="#c8ff0018" stroke-width="0.5"><animate attributeName="opacity" values="0;0.6;0" dur="2.5s" repeatCount="indefinite" begin="0.3s"/></line>
        <line x1="11" y1="22" x2="38" y2="28.5" stroke="#00e5ff18" stroke-width="0.5"><animate attributeName="opacity" values="0;0.6;0" dur="2.1s" repeatCount="indefinite" begin="0.8s"/></line>
        <line x1="19" y1="18" x2="31" y2="18" stroke="#ffffff18" stroke-width="0.5"><animate attributeName="opacity" values="0;0.7;0" dur="1.6s" repeatCount="indefinite" begin="1.1s"/></line>
        <line x1="18" y1="30" x2="32" y2="30" stroke="#ffffff18" stroke-width="0.5"><animate attributeName="opacity" values="0;0.7;0" dur="2.0s" repeatCount="indefinite" begin="0.5s"/></line>
        <path d="M22 38 C22 40.5 23 43 25 43 C27 43 28 40.5 28 38" stroke="#c8ff0066" stroke-width="0.9" fill="none" stroke-linecap="round"/>
        <line x1="25" y1="38" x2="25" y2="43" stroke="#00e5ff55" stroke-width="0.7" stroke-dasharray="1.5,1.5"/>
      </svg>
    </div>
    <div><div class="brand">Prop<span>ORACLE</span></div></div>
  </a>
  <div class="nav-links">
    <a href="/">Home</a>
    <a href="/tickets" class="active">Tickets</a>
    <a href="/grades">Grades</a>
    <a href="/payout">Payouts</a>
  </div>
</nav>

<div class="hero">
  <div>
    <h1>🎟 Latest <span>Tickets</span></h1>
    <div class="meta">Generated: {gen_at} &nbsp;|&nbsp; Slate date: <strong>{date_pretty}</strong> <span style="opacity:.72">({date_eff})</span>{date_mismatch_html}</div>
  </div>
</div>

<div class="filter-pill">
  Filters &rarr;
  <strong>tiers:</strong> {filters.get('tiers','ALL')} &nbsp;
  <strong>min_hit_rate:</strong> {filters.get('min_hit_rate',0)} &nbsp;
  <strong>min_edge:</strong> {filters.get('min_edge',0)} &nbsp;
  <strong>min_rank:</strong> {filters.get('min_rank','None')} &nbsp;
  <strong>pick_types:</strong> {filters.get('pick_types','ALL')}
  &nbsp;&nbsp;<a href="/tickets_latest.json" style="color:var(--cyan);">⬇ JSON</a>
</div>
<div class="filter-pill" style="margin-top:-12px;">
  Quick read: <strong>STRONG</strong> means direction aligns with context (defense + pace + sample), <strong>LEAN</strong> means partial alignment, <strong>RISKY</strong> means weak context support.
</div>
""")

    if not payload.get("groups"):
        html_parts.append("""
<div class="filter-pill" style="margin-top:-12px;border-color:rgba(201,106,116,.35);color:#d4b5b8;">
  <strong>No tickets in this JSON.</strong> Run the combined slate ticket script with <code>--write-web</code> after building slates, or relax filters. Download JSON (link above) to confirm <code>groups</code> is non-empty.
</div>
""")

    for g in payload.get("groups", []):
        html_parts.append(f"""
<div class="group">
  <div class="group-hdr">
    <div class="group-title">{g.get('group_name','Group')}</div>
    <div class="group-meta">Legs: {g.get('n_legs','')} &nbsp;|&nbsp; Power: {g.get('power_payout','')}x &nbsp;|&nbsp; Flex: {g.get('flex_payout','')}x</div>
  </div>
""")
        for t in g.get("tickets", []):
            avg_hr = t.get("avg_hit_rate")
            avg_rs = t.get("avg_rank_score")
            wp     = t.get("est_win_prob")

            try:
                hr_disp = f"{float(avg_hr)*100:.1f}%"
                hr_col  = hit_color(avg_hr)
            except Exception:
                hr_disp, hr_col = "—", "#aaa"

            try:
                rs_disp = f"{float(avg_rs):.2f}"
            except Exception:
                rs_disp = "—"

            em = t.get("est_multiplier")
            fm = t.get("flat_multiplier")
            mult_kpi = ""
            if em is not None and fm is not None:
                try:
                    mult_kpi = f"""
          <div class="kpi">
            <div class="kpi-label">Curve est</div>
            <div class="kpi-val" style="color:var(--accent);">{float(em):.2f}x</div>
            <div class="kpi-label" style="margin-top:4px">Flat PP base</div>
            <div style="font-family:'Bebas Neue',sans-serif;font-size:15px;color:var(--muted);">{float(fm):.2f}x</div>
          </div>"""
                except (TypeError, ValueError):
                    mult_kpi = ""

            html_parts.append(f"""
  <div class="ticket">
    <div class="ticket-inner">
      <div class="ticket-accent"></div>
      <div class="ticket-body">
        <div class="ticket-hdr">
          <div class="ticket-no">Ticket #{t.get('ticket_no','')}</div>
        </div>
        <div class="kpi-row">
          <div class="kpi">
            <div class="kpi-label">Hit Rate</div>
            <div class="kpi-val" style="color:{hr_col};">{hr_disp}</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">Avg Rank</div>
            <div class="kpi-val" style="color:var(--cyan);">{rs_disp}</div>
          </div>
          <div class="kpi" style="flex:1;min-width:140px;">
            <div class="kpi-label">Win Prob</div>
            {wp_bar(wp)}
          </div>{mult_kpi}
        </div>
        <table>
          <thead><tr>
            <th>#</th><th>Sport</th><th>Player</th><th>Prop</th><th>Line</th>
            <th>Pick</th><th>Min</th><th>Shot</th><th>Usage</th>
            <th>Dir</th><th>Signal</th><th>Why</th><th>Hit%</th><th>Edge</th><th>Rank</th>
          </tr></thead>
          <tbody>
""")
            for i, leg in enumerate(t.get("legs", []), start=1):
                dirv = (leg.get("direction") or "").upper()
                dir_span = (
                    "<span class='dir-over'>OVER</span>"
                    if dirv == "OVER"
                    else f"<span class='dir-under'>{dirv or '—'}</span>"
                )
                img      = leg.get("image_url")
                initials = leg.get("initials") or "?"
                if img:
                    avatar = f"<div class='avatar'><img src='{img}' alt='{initials}' onerror=\"this.style.display='none'\"></div>"
                else:
                    avatar = f"<div class='avatar'>{initials}</div>"

                player_cell = f"<div class='pwrap'>{avatar}<div>{leg.get('player','')}</div></div>"

                hr_val = leg.get("hit_rate")
                hr_fmt = fmt_pct(hr_val) if hr_val is not None else "—"
                hr_c   = hit_color(hr_val) if hr_val is not None else "#aaa"

                min_tier  = badge(leg.get("min_tier") or leg.get("minutes_tier"), "#39ff6e")
                shot_role = badge(leg.get("shot_role"), "#00e5ff")
                usg_role  = badge(leg.get("usage_role"), "#888")

                # build graph data for expand panel
                l5_avg     = leg.get("l5_avg")
                season_avg = leg.get("season_avg")
                l5_over    = leg.get("l5_over")
                l5_under   = leg.get("l5_under")
                l10_over   = leg.get("l10_over")
                l10_under  = leg.get("l10_under")
                line_val   = leg.get("line")
                dir_txt    = str(leg.get("direction") or "").upper()
                row_id     = f"lgr-{id(leg)}-{i}"
                sig_html, sig_reason = direction_signal(leg)

                dp = leg.get("delta_pct")
                ptl = str(leg.get("pick_type") or "").lower()
                delta_badge = ""
                if dp is not None:
                    try:
                        dpf = float(dp)
                        bc = "#888888"
                        if "goblin" in ptl:
                            if dpf >= 0.9:
                                bc = "#7dcf9a"
                            elif dpf >= 0.7:
                                bc = "#c89a4a"
                            else:
                                bc = "#e67e22"
                        elif "demon" in ptl:
                            if dpf <= 1.15:
                                bc = "#e8a0a0"
                            else:
                                bc = "#ff3333"
                        delta_badge = (
                            f" <span class='delta-badge' style='border-color:{bc};color:{bc}' "
                            f"title='played ÷ standard'>{dpf * 100:.1f}%</span>"
                        )
                    except (TypeError, ValueError):
                        pass
                pick_cell = f"{leg.get('pick_type', '')}{delta_badge}"

                # stat pills
                def _pill(label, val, fmt=None):
                    if val is None: return ""
                    v = fmt(val) if fmt else str(val)
                    return f'<div class="gstat"><div class="gstat-label">{label}</div><div class="gstat-val">{v}</div></div>'

                pills = "".join([
                    _pill("L5 Avg",     l5_avg,     lambda x: f"{x:.1f}"),
                    _pill("Season Avg", season_avg, lambda x: f"{x:.1f}"),
                    _pill("L5 Over",    l5_over,    lambda x: f"{int(round(x*5)) if x<=1 else int(x)}/5"),
                    _pill("L5 Under",   l5_under,   lambda x: f"{int(round(x*5)) if x<=1 else int(x)}/5"),
                    _pill("L10 Over",   l10_over,   lambda x: f"{int(round(x*10)) if x<=1 else int(x)}/10"),
                    _pill("L10 Under",  l10_under,  lambda x: f"{int(round(x*10)) if x<=1 else int(x)}/10"),
                    _pill("Hit Rate",   hr_val,     lambda x: f"{x*100:.0f}%"),
                ])

                # chart data — reconstruct bar-level data from l5 over/under counts
                def _hits(over_rate, n):
                    if over_rate is None: return "null"
                    cnt = int(round(over_rate * n)) if over_rate <= 1 else int(over_rate)
                    cnt = min(cnt, n)
                    vals = [1]*cnt + [0]*(n-cnt)
                    return str(vals)

                chart_data = f"""{{
                  line: {line_val if line_val is not None else 'null'},
                  l5hits: {_hits(l5_under, 5) if dir_txt == "UNDER" else _hits(l5_over, 5)},
                  l10hits: {_hits(l10_under, 10) if dir_txt == "UNDER" else _hits(l10_over, 10)},
                  l5avg: {l5_avg if l5_avg is not None else 'null'},
                  seasonAvg: {season_avg if season_avg is not None else 'null'},
                  player: {repr(leg.get('player',''))},
                  prop: {repr(leg.get('prop_type',''))},
                  direction: {repr(leg.get('direction',''))}
                }}"""

                graph_row = f"""
<tr class="leg-graph-row" id="{row_id}">
  <td class="leg-graph-cell" colspan="15">
    <div class="graph-wrap">
      <div style="flex:1;min-width:200px;">
        <div style="font-size:11px;color:#888;margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px;">{leg.get('player','')} · {leg.get('prop_type','')} · Line {fmt_line(line_val)}</div>
        <div class="graph-stats">{pills}</div>
      </div>
      <div class="graph-canvas-wrap">
        <canvas class="leg-chart" id="c-{row_id}"></canvas>
      </div>
    </div>
    <script>
    (function(){{
      var d = {chart_data};
      var ctx = document.getElementById('c-{row_id}');
      if(!ctx||!window.Chart) return;
      var hits10 = d.l10hits || d.l5hits || [];
      var labels = hits10.map((_,i)=>'G'+(i+1));
      var vals = hits10.map(()=>null); // placeholder — show avg lines only
      // IMPORTANT: do not fabricate stat heights from line values.
      // We only have hit/miss counts here, so render a truthful binary timeline.
      var barVals = hits10.map(h => h ? 1 : 0);
      var colors = hits10.map(h=> h ? '#00F2FF' : '#c96a74');
      new Chart(ctx, {{
        type:'bar',
        data:{{
          labels: labels,
          datasets:[{{
            label:'Hit Timeline',
            data: barVals,
            backgroundColor: colors,
            borderRadius:3,
            borderSkipped:false
          }}]
        }},
        options:{{
          responsive:true,
          maintainAspectRatio:false,
          plugins:{{
            legend:{{display:false}},
            tooltip:{{callbacks:{{label:function(c){{return hits10[c.dataIndex] ? 'Hit' : 'Miss';}}}}}}
          }},
          scales:{{
            x:{{ticks:{{color:'#888',font:{{size:10}}}},grid:{{color:'#1a1f2e'}}}},
            y:{{
              min: 0,
              max: 1,
              ticks:{{
                stepSize: 1,
                color:'#888',
                font:{{size:10}},
                callback: function(v){{ return v === 1 ? 'Hit' : 'Miss'; }}
              }},
              grid:{{color:'#1a1f2e'}},
            }}
          }},
          annotation:{{annotations:{{}}}}
        }}
      }});
    }})();
    </script>
  </td>
</tr>"""

                html_parts.append(
                    f"<tr class='leg-row' onclick=\"var r=document.getElementById('{row_id}');r.classList.toggle('open');\">"
                    f"<td>{i}</td>"
                    f"<td>{sport_badge(leg.get('sport',''))}</td>"
                    f"<td>{player_cell}</td>"
                    f"<td>{leg.get('prop_type','')}</td>"
                    f"<td style='color:var(--text);'>{fmt_line(leg.get('line'))}</td>"
                    f"<td>{pick_cell}</td>"
                    f"<td>{min_tier}</td>"
                    f"<td>{shot_role}</td>"
                    f"<td>{usg_role}</td>"
                    f"<td>{dir_span}</td>"
                    f"<td>{sig_html}</td>"
                    f"<td class='why-note'>{sig_reason}</td>"
                    f"<td style='color:{hr_c};font-weight:600;'>{hr_fmt}</td>"
                    f"<td>{fmt_2(leg.get('edge')) if leg.get('edge') is not None else '—'}</td>"
                    f"<td>{fmt_2(leg.get('rank_score')) if leg.get('rank_score') is not None else '—'}</td>"
                    f"</tr>"
                )
                html_parts.append(graph_row)

            html_parts.append("""
          </tbody>
        </table>
      </div>
    </div>
  </div>
""")
        html_parts.append("</div>")  # group

    html_parts.append("""
</div><!-- #app -->
<script>
(() => {
  document.querySelectorAll('.group,.ticket,.filter-pill,.kpi').forEach(el => {
    el.classList.add('ca-border');
    el.classList.add('mouse-glow');
    el.addEventListener('mousemove', e => {
      const r = el.getBoundingClientRect();
      el.style.setProperty('--mx', (e.clientX - r.left) + 'px');
      el.style.setProperty('--my', (e.clientY - r.top) + 'px');
    });
  });
})();
</script>
</body>
</html>""")

    return "\n".join(html_parts)


def write_web_outputs(payload, outdir: str):
    """Write tickets_latest.json for /tickets; graded HTML is build_ticket_eval.py → ticket_eval_<date>.html."""
    os.makedirs(outdir, exist_ok=True)
    json_path = os.path.join(outdir, "tickets_latest.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[OK] Web JSON  -> {json_path}")
    print("  (Graded eval HTML) Run: py -3.14 scripts/build_ticket_eval.py --date <YYYY-MM-DD>")


def _apply_l5_truth_from_stat_games(df: pd.DataFrame, sport_label: str) -> pd.DataFrame:
    """
    Source-of-truth guardrail:
    when stat_g1..stat_g5 are present, derive L5 Over/Under, L5 Avg, and HIT%
    directly from those raw values so downstream UI cannot drift from game logs.
    """
    if df is None or df.empty:
        return df

    stat_cols = [c for c in [f"stat_g{i}" for i in range(1, 6)] if c in df.columns]
    if not stat_cols or "line" not in df.columns:
        return df

    vals = df[stat_cols].apply(pd.to_numeric, errors="coerce")
    line = pd.to_numeric(df["line"], errors="coerce")
    valid_n = vals.notna().sum(axis=1)
    use_mask = valid_n >= 3
    if not bool(use_mask.any()):
        return df

    over = vals.gt(line, axis=0).sum(axis=1).astype(float)
    under = vals.lt(line, axis=0).sum(axis=1).astype(float)
    l5_avg = vals.mean(axis=1)
    total_ou = over + under

    direction = (
        df.get("direction", pd.Series(["OVER"] * len(df), index=df.index))
        .astype(str)
        .str.strip()
        .str.upper()
    )
    hit_over = over.divide(total_ou.where(total_ou > 0))
    hit_under = under.divide(total_ou.where(total_ou > 0))
    hit_dir = hit_over.where(direction.ne("UNDER"), hit_under)

    if "l5_over" not in df.columns:
        df["l5_over"] = np.nan
    if "l5_under" not in df.columns:
        df["l5_under"] = np.nan
    if "l5_avg" not in df.columns:
        df["l5_avg"] = np.nan
    if "hit_rate" not in df.columns:
        df["hit_rate"] = np.nan

    df.loc[use_mask, "l5_over"] = over[use_mask]
    df.loc[use_mask, "l5_under"] = under[use_mask]
    df.loc[use_mask, "l5_avg"] = l5_avg[use_mask]
    df.loc[use_mask, "hit_rate"] = hit_dir[use_mask]
    return df


# ── Load & normalize NBA ───────────────────────────────────────────────────────
def load_nba(path: str) -> pd.DataFrame:
    path = resolve_input_path(path, fallback_filename="step8_all_direction_clean.xlsx")

    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet = "ALL" if "ALL" in xl.sheet_names else xl.sheet_names[0]
    df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")

    df = df.rename(
        columns={
            "Tier": "tier",
            "Rank Score": "rank_score",
            "Player": "player",
            "Pos": "pos",
            "Team": "team",
            "Opp": "opp",
            "Game Time": "game_time",
            "Prop": "prop_type",
            "Pick Type": "pick_type",
            "Line": "line",
            "Direction": "direction",
            "Edge": "edge",
            "Abs Edge": "abs_edge",
            "Projection": "projection",
            "ML Prob": "ml_prob",
            "Hit Rate (5g)": "hit_rate",
            "Last 5 Avg": "l5_avg",
            "Season Avg": "season_avg",
            "L5 Over": "l5_over",
            "L5 Under": "l5_under",
            "Def Rank": "def_rank",
            "Def Tier": "def_tier",
            "Pace Tier": "pace_tier",
            "pace_tier": "pace_tier",
            "Context Score": "context_score",
            "context_score": "context_score",
            "Min Tier": "min_tier",
            "Shot Role": "shot_role",
            "Usage Role": "usage_role",
            "Void Reason": "void_reason",
            # OPTIONAL if your NBA file has it:
            "nba_player_id": "nba_player_id",
            "NBA Player ID": "nba_player_id",
            "player_id": "nba_player_id",
            "Player ID": "nba_player_id",
            # H2H / B2B / CV / Opp vs Avg
            "H2H Avg":      "h2h_avg",
            "H2H Over%":    "h2h_over_rate",
            "H2H Games":    "h2h_games",
            "H2H Last":     "h2h_last",
            "B2B":          "b2b_flag",
            "CV%":          "cv_pct",
            "Opp vs Avg%":  "opp_vs_avg_pct",
            "Game Script Mult": "game_script_mult",
            "Game Script Note": "game_script_note",
            "game_script_mult": "game_script_mult",
            "game_script_note": "game_script_note",
        }
    )

    # ✅ IMPORTANT: de-dupe before using any column as Series
    df = df.loc[:, ~df.columns.duplicated()].copy()

    df["sport"] = "NBA"

    if "direction" in df.columns:
        if isinstance(df["direction"], pd.DataFrame):
            df["direction"] = df["direction"].iloc[:, 0]
        df["direction"] = df["direction"].astype(str).str.upper()

    if "tier" in df.columns:
        if isinstance(df["tier"], pd.DataFrame):
            df["tier"] = df["tier"].iloc[:, 0]
        df["tier"] = df["tier"].astype(str).str.upper()

    # Keep void_reason metadata but do not prune NBA board rows here.
    # The product requirement is to show board parity with available PP lines.
    if "void_reason" in df.columns:
        if isinstance(df["void_reason"], pd.DataFrame):
            df["void_reason"] = df["void_reason"].iloc[:, 0]

    # Drop "1st 3 Minutes" props — no historical data, not bettable on PrizePicks standard
    if "prop_type" in df.columns:
        before = len(df)
        df = df[~df["prop_type"].astype(str).str.contains("1st 3 Min", case=False, na=False)].copy()
        dropped = before - len(df)
        if dropped:
            print(f"  [load_nba] Dropped {dropped} '1st 3 Min' props")

    # Clean ID if present
    if "nba_player_id" in df.columns:
        df["nba_player_id"] = df["nba_player_id"].apply(_clean_id)

    df = _apply_l5_truth_from_stat_games(df, "NBA")
    if "abs_edge" not in df.columns and "edge" in df.columns:
        df["abs_edge"] = pd.to_numeric(df["edge"], errors="coerce").abs()
    elif "abs_edge" in df.columns:
        df["abs_edge"] = pd.to_numeric(df["abs_edge"], errors="coerce")
    return df


# ── Load & normalize CBB ───────────────────────────────────────────────────────
def load_cbb(path: str) -> pd.DataFrame:
    path = resolve_input_path(path, fallback_filename="step6_ranked_cbb.xlsx")

    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet = (
        "ELIGIBLE"
        if "ELIGIBLE" in xl.sheet_names
        else ("ALL" if "ALL" in xl.sheet_names else xl.sheet_names[0])
    )
    df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")

    df = df.rename(
        columns={
            "final_bet_direction": "direction",
            "bet_direction": "direction",
            "opp_team_abbr": "opp",
            "start_time": "game_time",
            "line_hit_rate": "hit_rate",
            "stat_last5_avg": "l5_avg",
            "stat_season_avg": "season_avg",
            "line_hits_over_5": "l5_over",
            # Intel layer columns (step6e)
            "intel_season_avg":        "intel_season_avg",
            "intel_l5_avg":            "intel_l5_avg",
            "intel_l10_avg":           "intel_l10_avg",
            "intel_season_hit_rate":   "intel_season_hit_rate",
            "intel_cushion":           "intel_cushion",
            "intel_cv_pct":            "intel_cv_pct",
            "intel_opp_vs_league_pct": "intel_opp_vs_league_pct",
            "intel_l5_vs_season":      "intel_l5_vs_season",
            "line_hits_under_5": "l5_under",
            "Def Tier": "def_tier",
            "DEF_TIER": "def_tier",
            "Defense Tier": "def_tier",
            "minutes_tier": "min_tier",
            "Min Tier": "min_tier",
            "shot_role": "shot_role",
            "Shot Role": "shot_role",
            "usage_role": "usage_role",
            "Usage Role": "usage_role",
            # OPTIONAL IDs
            "espn_player_id": "espn_player_id",
            "ESPN Player ID": "espn_player_id",
            "player_id": "espn_player_id",
            # Optional NCAA ranking fields (when present in CBB pipeline output)
            "NCAA Rank": "ncaa_rank",
            "ncaa_rank": "ncaa_rank",
            "OVERALL_DEF_RANK": "ncaa_rank",
            "opp_def_rank": "ncaa_rank",
        }
    )

    # ✅ CRITICAL HOTFIX: de-duplicate columns BEFORE df["direction"].str.upper()
    df = df.loc[:, ~df.columns.duplicated()].copy()

    # ✅ If direction is still a DataFrame for any reason, take the first column.
    if "direction" in df.columns and isinstance(df["direction"], pd.DataFrame):
        df["direction"] = df["direction"].iloc[:, 0]

    df["sport"] = "CBB"

    if "direction" in df.columns:
        df["direction"] = df["direction"].astype(str).str.upper()

    if "tier" in df.columns:
        if isinstance(df["tier"], pd.DataFrame):
            df["tier"] = df["tier"].iloc[:, 0]
        df["tier"] = df["tier"].astype(str).str.upper()

    if "void_reason" in df.columns:
        if isinstance(df["void_reason"], pd.DataFrame):
            df["void_reason"] = df["void_reason"].iloc[:, 0]
        df = df[df["void_reason"].isna() | (df["void_reason"].astype(str).str.strip() == "")]

    if "espn_player_id" in df.columns:
        df["espn_player_id"] = df["espn_player_id"].apply(_clean_id)

    # Enrich CBB rows with tournament + AP metadata for team and opponent.
    team_src = "team" if "team" in df.columns else ("pp_team" if "pp_team" in df.columns else "")
    opp_src = "opp" if "opp" in df.columns else ("opp_team_abbr" if "opp_team_abbr" in df.columns else "")
    if team_src:
        t_abbr = df[team_src].map(_norm_team_abbr)
        df["team_seed"] = t_abbr.map(lambda a: CBB_TOURNEY_2026.get(a, ("", ""))[0])
        df["team_region"] = t_abbr.map(lambda a: CBB_TOURNEY_2026.get(a, ("", ""))[1])
        df["team_ap_rank"] = t_abbr.map(lambda a: CBB_AP_TOP25_2026.get(a, ""))
    if opp_src:
        o_abbr = df[opp_src].map(_norm_team_abbr)
        df["opp_seed"] = o_abbr.map(lambda a: CBB_TOURNEY_2026.get(a, ("", ""))[0])
        df["opp_region"] = o_abbr.map(lambda a: CBB_TOURNEY_2026.get(a, ("", ""))[1])
        df["opp_ap_rank"] = o_abbr.map(lambda a: CBB_AP_TOP25_2026.get(a, ""))

    # Ensure NCAA rank is numeric when available.
    if "ncaa_rank" in df.columns:
        df["ncaa_rank"] = pd.to_numeric(df["ncaa_rank"], errors="coerce")

    if "team_seed" in df.columns and "opp_seed" in df.columns:
        _ts = pd.to_numeric(df["team_seed"], errors="coerce")
        _os = pd.to_numeric(df["opp_seed"], errors="coerce")
        df["is_tournament_game"] = ((_ts.notna()) & (_ts > 0)) | ((_os.notna()) & (_os > 0))
    else:
        df["is_tournament_game"] = False

    return df


# ── Load & normalize NHL ──────────────────────────────────────────────────────
def load_nhl(path: str) -> pd.DataFrame:
    raw = (path or "").strip()
    if not raw:
        return pd.DataFrame()

    try:
        path = resolve_input_path(raw, fallback_filename="step8_nhl_direction_clean.xlsx")
    except FileNotFoundError:
        print("  [load_nhl] NHL file not found — skipping NHL")
        return pd.DataFrame()

    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet = "NHL" if "NHL" in xl.sheet_names else ("ALL" if "ALL" in xl.sheet_names else xl.sheet_names[0])
    df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")

    df = df.rename(columns={
        "Game Script Mult": "game_script_mult",
        "Game Script Note": "game_script_note",
        "player_name":        "player",
        "position":           "pos",
        "stat_type":          "prop_type",
        "line_score":         "line",
        "recommended_side":   "direction",
        "composite_hit_rate": "hit_rate",
        "Composite Hit Rate": "hit_rate",
        "composite_hr":       "hit_rate",
        "hr_L10":             "hit_rate_over_L10",
        "avg_L5":             "l5_avg",
        "avg_season":         "season_avg",
        "def_tier":           "def_tier",
        "def_rank":           "def_rank",
        "prop_score":         "rank_score",
        "game_start":         "game_time",
    })
    # Deduplicate columns immediately after rename — multiple source cols may map to same target
    df = df.loc[:, ~df.columns.duplicated()].copy()

    # Fallback: derive hit_rate from hit_rate_over_L10 if still missing
    if "hit_rate" not in df.columns or df["hit_rate"].isna().all():
        for fallback_col in ("hit_rate_over_L10", "hit_rate_over_L5", "hit_rate_over_L20",
                             "over_L10", "over_L5"):
            if fallback_col in df.columns:
                df["hit_rate"] = pd.to_numeric(df[fallback_col], errors="coerce")
                print(f"  [load_nhl] hit_rate sourced from '{fallback_col}'")
                break

    # Normalize hit_rate to 0-1 — handle "94.0%", "0.94", or 94.0
    if "hit_rate" in df.columns:
        hr = df["hit_rate"].astype(str).str.replace("%", "", regex=False).str.strip()
        hr = pd.to_numeric(hr, errors="coerce")
        if hr.dropna().max() > 1.5:   # clearly a percentage value (e.g. 94.0)
            hr = hr / 100.0
        df["hit_rate"] = hr

    # opponent is stored in 'description' column
    if "opp" not in df.columns:
        if "description" in df.columns:
            df["opp"] = df["description"]
        else:
            df["opp"] = ""

    df = df.loc[:, ~df.columns.duplicated()].copy()
    df["sport"] = "NHL"

    def _norm_pick(x):
        t = str(x).strip().lower() if x else ""
        if "gob" in t: return "Goblin"
        if "dem" in t: return "Demon"
        return "Standard"

    if "pick_type" not in df.columns:
        df["pick_type"] = "Standard"
    df["pick_type"] = df["pick_type"].apply(_norm_pick)
    forced = df["pick_type"].isin(["Goblin", "Demon"])

    if "direction" in df.columns:
        df["direction"] = df["direction"].astype(str).str.upper()
        df.loc[forced, "direction"] = "OVER"
    else:
        df["direction"] = "OVER"

    if "tier" in df.columns:
        df["tier"] = df["tier"].astype(str).str.upper()
    else:
        df["tier"] = "C"

    # Extra fallback: NHL step8 may still have line_score if rename didn't catch it
    if "line" not in df.columns:
        for alt in ("line_score", "Line", "line_value", "prop_line"):
            if alt in df.columns:
                df["line"] = df[alt]
                break
    if "line" not in df.columns:
        df["line"] = np.nan

    for col in ["rank_score", "hit_rate", "line"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "edge" not in df.columns:
        df["edge"] = 0.0

    # Faceoff wins are currently not sourced with reliable per-player counts
    # in our actuals pipeline; exclude until a stable source is added.
    if "prop_type" in df.columns:
        before = len(df)
        p = df["prop_type"].astype(str).str.lower()
        df = df[~p.str.contains(r"faceoff", regex=True, na=False)].copy()
        dropped = before - len(df)
        if dropped > 0:
            print(f"  [load_nhl] Dropped {dropped} unreliable faceoff props")

    # Last-5 game counts vs line (step8 raw cols) → slate L5 Over / L5 Under
    if "over_L5_raw" in df.columns:
        df["l5_over"] = pd.to_numeric(df["over_L5_raw"], errors="coerce")
    elif "l5_over" not in df.columns:
        df["l5_over"] = np.nan
    if "under_L5_raw" in df.columns:
        df["l5_under"] = pd.to_numeric(df["under_L5_raw"], errors="coerce")
    elif "l5_under" not in df.columns:
        df["l5_under"] = np.nan

    df = add_l5_play_side_columns(df)

    df = df[df["line"].notna() & (df["line"] > 0)]
    # Convert all pandas NA/NaT to None so openpyxl can handle them
    df = df.astype(object).where(df.notna(), other=None)
    return df



# ── Load & normalize Soccer ───────────────────────────────────────────────────
def load_soccer(path: str) -> pd.DataFrame:
    path = resolve_input_path(path, fallback_filename="step8_soccer_direction_clean.xlsx")

    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet = "Soccer" if "Soccer" in xl.sheet_names else (
        "ALL" if "ALL" in xl.sheet_names else xl.sheet_names[0])
    df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")

    df = df.rename(columns={
        # title-case (from step8 clean xlsx)
        "Player":           "player",
        "Tier":             "tier",
        "Rank Score":       "rank_score",
        "Pos":              "pos",
        "Team":             "team",
        "Opp":              "opp",
        "Game Time":        "game_time",
        "Prop":             "prop_type",
        "Pick Type":        "pick_type",
        "Line":             "line",
        "Direction":        "direction",
        "Edge":             "edge",
        "Projection":       "projection",
        "ESPN ID":          "espn_player_id",
        "Hit Rate (5g)":    "hit_rate",
        # Kept separate so we can coalesce into hit_rate when 5g is blank (common when
        # Soccer step5/7 line-hit columns aren't populated yet).
        "Hit Rate (10g)":   "_soccer_hit10",
        "Last 5 Avg":       "l5_avg",
        "Season Avg":       "season_avg",
        "L5 Over":          "l5_over",
        "L5 Under":         "l5_under",
        "L10 Over":         "l10_over",
        "L10 Under":        "l10_under",
        "Def Rank":         "def_rank",
        "Def Tier":         "def_tier",
        "Min Tier":         "min_tier",
        "Shot Role":        "shot_role",
        "Usage Role":       "usage_role",
        "League":           "league",
        "Pos Group":        "position_group",
        "Void Reason":      "void_reason",
        # snake_case fallbacks
        "player_name":        "player",
        "stat_type":          "prop_type",
        "stat_norm":          "prop_type",
        "line_score":         "line",
        "recommended_side":   "direction",
        "composite_hit_rate": "hit_rate",
        "avg_L5":             "l5_avg",
        "avg_season":         "season_avg",
        "def_tier":           "def_tier",
        "def_rank":           "def_rank",
        "prop_score":         "rank_score",
        "game_start":         "game_time",
        "opponent":           "opp",
        "line_hit_rate_over_ou_5":  "hit_rate",
        "line_hit_rate_over_ou_10": "_soccer_hit10",
        "hit_rate_over_L10": "l10_over",
        "hit_rate_under_L10": "l10_under",
        "over_L10": "l10_over",
        "under_L10": "l10_under",
        "Last 10 Avg": "season_avg",
        "Game Script Mult": "game_script_mult",
        "Game Script Note": "game_script_note",
        "game_script_mult": "game_script_mult",
        "game_script_note": "game_script_note",
    })

    if "opp" not in df.columns:
        df["opp"] = ""

    df = df.loc[:, ~df.columns.duplicated()].copy()
    df["sport"] = "Soccer"

    def _norm_pick(x):
        t = str(x).strip().lower() if x else ""
        if "gob" in t: return "Goblin"
        if "dem" in t: return "Demon"
        return "Standard"

    if "pick_type" not in df.columns:
        df["pick_type"] = "Standard"
    df["pick_type"] = df["pick_type"].apply(_norm_pick)

    if "direction" in df.columns:
        df["direction"] = df["direction"].astype(str).str.upper()
    else:
        df["direction"] = "OVER"

    if "tier" in df.columns:
        df["tier"] = df["tier"].astype(str).str.upper()
    else:
        df["tier"] = "C"

    if "hit_rate" not in df.columns:
        df["hit_rate"] = np.nan

    for col in [
        "rank_score",
        "hit_rate",
        "line",
        "_soccer_hit10",
        "l5_avg",
        "season_avg",
        "l5_over",
        "l5_under",
        "l10_over",
        "l10_under",
        "projection",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Prefer L5 hit rate, then L10, when either is present.
    if "_soccer_hit10" in df.columns:
        df["hit_rate"] = df["hit_rate"].combine_first(df["_soccer_hit10"])
        df.drop(columns=["_soccer_hit10"], inplace=True)

    # Normalize hit_rate to 0–1 (handles "62%" or 62.0 from spreadsheets)
    if "hit_rate" in df.columns and df["hit_rate"].notna().any():
        hr = df["hit_rate"]
        if hr.dtype == object:
            hr = hr.astype(str).str.replace("%", "", regex=False).str.strip()
            hr = pd.to_numeric(hr, errors="coerce")
        else:
            hr = pd.to_numeric(hr, errors="coerce")
        if hr.dropna().max() is not None and hr.dropna().max() > 1.5:
            hr = hr / 100.0
        df["hit_rate"] = hr

    # Still no usable hit rate (common on current Soccer pipeline when game logs are empty).
    # Use a mild rank_score-based proxy so tier/rank ticket gates still run; re-run step5 when HRs exist.
    hr_series = pd.to_numeric(df["hit_rate"], errors="coerce")
    if hr_series.notna().sum() == 0:
        rs = pd.to_numeric(df.get("rank_score", 0), errors="coerce").fillna(0.0)
        q25, q75 = float(rs.quantile(0.25)), float(rs.quantile(0.75))
        span = (q75 - q25) + 1e-6
        proxy = 0.54 + ((rs - q25) / span).clip(lower=0.0, upper=1.0) * 0.12
        df["hit_rate"] = proxy.clip(0.50, 0.68)
        print(
            "  [load_soccer] NOTE: Hit Rate (5g)/(10g) empty - using rank_score proxy for ticket eligibility. "
            "Fix Soccer step5 line-hit output when possible."
        )

    # Backfill sparse soccer stats so slate columns are populated.
    hr_for_counts = pd.to_numeric(df.get("hit_rate", np.nan), errors="coerce").clip(lower=0.0, upper=1.0)
    if "l5_over" not in df.columns:
        df["l5_over"] = np.nan
    if "l5_under" not in df.columns:
        df["l5_under"] = np.nan
    if "l10_over" not in df.columns:
        df["l10_over"] = np.nan
    if "l10_under" not in df.columns:
        df["l10_under"] = np.nan
    if "l5_avg" not in df.columns:
        df["l5_avg"] = np.nan
    if "season_avg" not in df.columns:
        df["season_avg"] = np.nan

    # IMPORTANT:
    # For Soccer, upstream "Hit Rate (5g)/(10g)" is direction-aware in many cases
    # (e.g. when stat_g* are missing, step7 fills line_hit_rate into the over_* columns).
    # If hit_rate represents the chosen bet side, we must derive L5/L10 counts
    # directionally so UNDER rows don't get reversed.
    dirv = df.get("direction", pd.Series(["OVER"] * len(df), index=df.index)).astype(str).str.upper().fillna("OVER")

    l5_hit_as_over = (hr_for_counts * 5.0).round()
    l5_over_fill = l5_hit_as_over.where(dirv.ne("UNDER"), 5.0 - l5_hit_as_over)
    l5_under_fill = 5.0 - l5_over_fill

    l10_hit_as_over = (hr_for_counts * 10.0).round()
    l10_over_fill = l10_hit_as_over.where(dirv.ne("UNDER"), 10.0 - l10_hit_as_over)
    l10_under_fill = 10.0 - l10_over_fill

    df["l5_over"] = pd.to_numeric(df["l5_over"], errors="coerce").combine_first(l5_over_fill)
    df["l5_under"] = pd.to_numeric(df["l5_under"], errors="coerce").combine_first(l5_under_fill)
    df["l10_over"] = pd.to_numeric(df["l10_over"], errors="coerce").combine_first(l10_over_fill)
    df["l10_under"] = pd.to_numeric(df["l10_under"], errors="coerce").combine_first(l10_under_fill)

    proj = pd.to_numeric(df.get("projection", np.nan), errors="coerce")
    df["l5_avg"] = pd.to_numeric(df["l5_avg"], errors="coerce").combine_first(proj)
    df["season_avg"] = pd.to_numeric(df["season_avg"], errors="coerce").combine_first(df["l5_avg"]).combine_first(proj)

    if "edge" not in df.columns:
        df["edge"] = 0.0

    if "espn_player_id" in df.columns:
        df["espn_player_id"] = df["espn_player_id"].apply(_clean_id)

    df = df[df["line"].notna() & (df["line"] >= 0)]
    df = _apply_l5_truth_from_stat_games(df, "NBA1Q")
    df = df.astype(object).where(df.notna(), other=None)
    return df


def load_wcbb(path: str) -> pd.DataFrame:
    path = resolve_input_path(path, fallback_filename="step8_wcbb_direction_clean.xlsx")

    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet = "Soccer" if "Soccer" in xl.sheet_names else (
        "ALL" if "ALL" in xl.sheet_names else xl.sheet_names[0])
    df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")

    df = df.rename(columns={
        # title-case (from step8 clean xlsx)
        "Player":           "player",
        "Tier":             "tier",
        "Rank Score":       "rank_score",
        "Pos":              "pos",
        "Team":             "team",
        "Opp":              "opp",
        "Game Time":        "game_time",
        "Prop":             "prop_type",
        "Pick Type":        "pick_type",
        "Line":             "line",
        "Direction":        "direction",
        "Edge":             "edge",
        "Projection":       "projection",
        "ESPN ID":          "espn_player_id",
        "Hit Rate (5g)":    "hit_rate",
        # Kept separate so we can coalesce into hit_rate when 5g is blank (common when
        # Soccer step5/7 line-hit columns aren't populated yet).
        "Hit Rate (10g)":   "_soccer_hit10",
        "Last 5 Avg":       "l5_avg",
        "Season Avg":       "season_avg",
        "L5 Over":          "l5_over",
        "L5 Under":         "l5_under",
        "Def Rank":         "def_rank",
        "Def Tier":         "def_tier",
        "Min Tier":         "min_tier",
        "Shot Role":        "shot_role",
        "Usage Role":       "usage_role",
        "League":           "league",
        "Pos Group":        "position_group",
        "Void Reason":      "void_reason",
        # snake_case fallbacks
        "player_name":        "player",
        "stat_type":          "prop_type",
        "stat_norm":          "prop_type",
        "line_score":         "line",
        "recommended_side":   "direction",
        "composite_hit_rate": "hit_rate",
        "avg_L5":             "l5_avg",
        "avg_season":         "season_avg",
        "def_tier":           "def_tier",
        "def_rank":           "def_rank",
        "prop_score":         "rank_score",
        "game_start":         "game_time",
        "opponent":           "opp",
        "line_hit_rate_over_ou_5":  "hit_rate",
        "line_hit_rate_over_ou_10": "_soccer_hit10",
        "Game Script Mult": "game_script_mult",
        "Game Script Note": "game_script_note",
        "game_script_mult": "game_script_mult",
        "game_script_note": "game_script_note",
    })

    if "opp" not in df.columns:
        df["opp"] = ""

    df = df.loc[:, ~df.columns.duplicated()].copy()
    df["sport"] = "WCBB"

    def _norm_pick(x):
        t = str(x).strip().lower() if x else ""
        if "gob" in t: return "Goblin"
        if "dem" in t: return "Demon"
        return "Standard"

    if "pick_type" not in df.columns:
        df["pick_type"] = "Standard"
    df["pick_type"] = df["pick_type"].apply(_norm_pick)

    if "direction" in df.columns:
        df["direction"] = df["direction"].astype(str).str.upper()
    else:
        df["direction"] = "OVER"

    if "tier" in df.columns:
        df["tier"] = df["tier"].astype(str).str.upper()
    else:
        df["tier"] = "C"

    if "hit_rate" not in df.columns:
        df["hit_rate"] = np.nan

    for col in ["rank_score", "hit_rate", "line", "_soccer_hit10"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Prefer L5 hit rate, then L10, when either is present.
    if "_soccer_hit10" in df.columns:
        df["hit_rate"] = df["hit_rate"].combine_first(df["_soccer_hit10"])
        df.drop(columns=["_soccer_hit10"], inplace=True)

    # Normalize hit_rate to 0–1 (handles "62%" or 62.0 from spreadsheets)
    if "hit_rate" in df.columns and df["hit_rate"].notna().any():
        hr = df["hit_rate"]
        if hr.dtype == object:
            hr = hr.astype(str).str.replace("%", "", regex=False).str.strip()
            hr = pd.to_numeric(hr, errors="coerce")
        else:
            hr = pd.to_numeric(hr, errors="coerce")
        if hr.dropna().max() is not None and hr.dropna().max() > 1.5:
            hr = hr / 100.0
        df["hit_rate"] = hr

    # Still no usable hit rate (common on current Soccer pipeline when game logs are empty).
    # Use a mild rank_score-based proxy so tier/rank ticket gates still run; re-run step5 when HRs exist.
    hr_series = pd.to_numeric(df["hit_rate"], errors="coerce")
    if hr_series.notna().sum() == 0:
        rs = pd.to_numeric(df.get("rank_score", 0), errors="coerce").fillna(0.0)
        q25, q75 = float(rs.quantile(0.25)), float(rs.quantile(0.75))
        span = (q75 - q25) + 1e-6
        proxy = 0.54 + ((rs - q25) / span).clip(lower=0.0, upper=1.0) * 0.12
        df["hit_rate"] = proxy.clip(0.50, 0.68)
        print(
            "  [load_soccer] NOTE: Hit Rate (5g)/(10g) empty - using rank_score proxy for ticket eligibility. "
            "Fix Soccer step5 line-hit output when possible."
        )

    if "edge" not in df.columns:
        df["edge"] = 0.0

    if "espn_player_id" in df.columns:
        df["espn_player_id"] = df["espn_player_id"].apply(_clean_id)

    df = df[df["line"].notna() & (df["line"] >= 0)]
    df = _apply_l5_truth_from_stat_games(df, "NBA1H")
    df = df.astype(object).where(df.notna(), other=None)
    return df


def load_mlb(path: str) -> pd.DataFrame:
    path = resolve_input_path(path, fallback_filename="step8_mlb_direction_clean.xlsx")

    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet = "MLB" if "MLB" in xl.sheet_names else (
        "ALL" if "ALL" in xl.sheet_names else xl.sheet_names[0])
    df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")

    df = df.rename(columns={
        # title-case (from step8 clean xlsx)
        "Player":           "player",
        "Tier":             "tier",
        "Rank Score":       "rank_score",
        "Pos":              "pos",
        "Team":             "team",
        "Opp":              "opp",
        "Game Time":        "game_time",
        "Prop":             "prop_type",
        "Pick Type":        "pick_type",
        "Line":             "line",
        "Direction":        "direction",
        "Edge":             "edge",
        "Projection":       "projection",
        "ESPN ID":          "espn_player_id",
        "Hit Rate (5g)":    "hit_rate",
        # Kept separate so we can coalesce into hit_rate when 5g is blank (common when
        # Soccer step5/7 line-hit columns aren't populated yet).
        "Hit Rate (10g)":   "_soccer_hit10",
        "Last 5 Avg":       "l5_avg",
        "Season Avg":       "season_avg",
        "L5 Over":          "l5_over",
        "L5 Under":         "l5_under",
        "Def Rank":         "def_rank",
        "Def Tier":         "def_tier",
        "Min Tier":         "min_tier",
        "Shot Role":        "shot_role",
        "Usage Role":       "usage_role",
        "League":           "league",
        "Pos Group":        "position_group",
        "Void Reason":      "void_reason",
        # snake_case fallbacks
        "player_name":        "player",
        "stat_type":          "prop_type",
        "stat_norm":          "prop_type",
        "line_score":         "line",
        "recommended_side":   "direction",
        "composite_hit_rate": "hit_rate",
        "avg_L5":             "l5_avg",
        "avg_season":         "season_avg",
        "def_tier":           "def_tier",
        "def_rank":           "def_rank",
        "prop_score":         "rank_score",
        "game_start":         "game_time",
        "opponent":           "opp",
        "line_hit_rate_over_ou_5":  "hit_rate",
        "line_hit_rate_over_ou_10": "_soccer_hit10",
        "Game Script Mult": "game_script_mult",
        "Game Script Note": "game_script_note",
        "game_script_mult": "game_script_mult",
        "game_script_note": "game_script_note",
    })

    if "opp" not in df.columns:
        df["opp"] = ""

    df = df.loc[:, ~df.columns.duplicated()].copy()
    df["sport"] = "MLB"

    # Ensure ml_prob is numeric and always present for downstream leg scoring.
    if "ml_prob" not in df.columns:
        df["ml_prob"] = np.nan
    df["ml_prob"] = pd.to_numeric(df["ml_prob"], errors="coerce")

    # Derive L5 game sample size from over+under counts when available.
    if "l5_games" not in df.columns:
        l5o = pd.to_numeric(df.get("l5_over", np.nan), errors="coerce")
        l5u = pd.to_numeric(df.get("l5_under", np.nan), errors="coerce")
        derived = l5o.add(l5u, fill_value=0)
        df["l5_games"] = derived.where(derived > 0, np.nan)

    def _norm_pick(x):
        t = str(x).strip().lower() if x else ""
        if "gob" in t: return "Goblin"
        if "dem" in t: return "Demon"
        return "Standard"

    if "pick_type" not in df.columns:
        df["pick_type"] = "Standard"
    df["pick_type"] = df["pick_type"].apply(_norm_pick)

    if "direction" in df.columns:
        df["direction"] = df["direction"].astype(str).str.upper()
    else:
        df["direction"] = "OVER"

    if "tier" in df.columns:
        df["tier"] = df["tier"].astype(str).str.upper()
    else:
        df["tier"] = "C"

    if "hit_rate" not in df.columns:
        df["hit_rate"] = np.nan

    for col in ["rank_score", "hit_rate", "line", "_soccer_hit10"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Prefer L5 hit rate, then L10, when either is present.
    if "_soccer_hit10" in df.columns:
        df["hit_rate"] = df["hit_rate"].combine_first(df["_soccer_hit10"])
        df.drop(columns=["_soccer_hit10"], inplace=True)

    # Normalize hit_rate to 0–1 (handles "62%" or 62.0 from spreadsheets)
    if "hit_rate" in df.columns and df["hit_rate"].notna().any():
        hr = df["hit_rate"]
        if hr.dtype == object:
            hr = hr.astype(str).str.replace("%", "", regex=False).str.strip()
            hr = pd.to_numeric(hr, errors="coerce")
        else:
            hr = pd.to_numeric(hr, errors="coerce")
        if hr.dropna().max() is not None and hr.dropna().max() > 1.5:
            hr = hr / 100.0
        df["hit_rate"] = hr

    # Still no usable hit rate (common on current Soccer pipeline when game logs are empty).
    # Use a mild rank_score-based proxy so tier/rank ticket gates still run; re-run step5 when HRs exist.
    hr_series = pd.to_numeric(df["hit_rate"], errors="coerce")
    if hr_series.notna().sum() == 0:
        rs = pd.to_numeric(df.get("rank_score", 0), errors="coerce").fillna(0.0)
        q25, q75 = float(rs.quantile(0.25)), float(rs.quantile(0.75))
        span = (q75 - q25) + 1e-6
        proxy = 0.54 + ((rs - q25) / span).clip(lower=0.0, upper=1.0) * 0.12
        df["hit_rate"] = proxy.clip(0.50, 0.68)
        print(
            "  [load_mlb] NOTE: Hit Rate (5g)/(10g) empty - using rank_score proxy for ticket eligibility. "
            "Fix Soccer step5 line-hit output when possible."
        )

    if "edge" not in df.columns:
        df["edge"] = 0.0

    if "espn_player_id" in df.columns:
        df["espn_player_id"] = df["espn_player_id"].apply(_clean_id)

    df = df[df["line"].notna() & (df["line"] >= 0)]
    df = df.astype(object).where(df.notna(), other=None)
    return df


def load_nba1q(path: str) -> pd.DataFrame:
    path = resolve_input_path(path, fallback_filename="step8_nba1q_direction_clean.xlsx")

    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet = "NBA1Q" if "NBA1Q" in xl.sheet_names else (
        "ALL" if "ALL" in xl.sheet_names else xl.sheet_names[0])
    df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")

    df = df.rename(columns={
        # title-case (from step8 clean xlsx)
        "Player":           "player",
        "Tier":             "tier",
        "Rank Score":       "rank_score",
        "Pos":              "pos",
        "Team":             "team",
        "Opp":              "opp",
        "Game Time":        "game_time",
        "Prop":             "prop_type",
        "Pick Type":        "pick_type",
        "Line":             "line",
        "Direction":        "direction",
        "Edge":             "edge",
        "Projection":       "projection",
        "ESPN ID":          "espn_player_id",
        "ML Prob":          "ml_prob",
        "Hit Rate (5g)":    "hit_rate",
        # Kept separate so we can coalesce into hit_rate when 5g is blank (common when
        # Soccer step5/7 line-hit columns aren't populated yet).
        "Hit Rate (10g)":   "_soccer_hit10",
        "Last 5 Avg":       "l5_avg",
        "Season Avg":       "season_avg",
        "L5 Over":          "l5_over",
        "L5 Under":         "l5_under",
        "Def Rank":         "def_rank",
        "Def Tier":         "def_tier",
        "Min Tier":         "min_tier",
        "Shot Role":        "shot_role",
        "Usage Role":       "usage_role",
        "League":           "league",
        "Pos Group":        "position_group",
        "Void Reason":      "void_reason",
        # snake_case fallbacks
        "player_name":        "player",
        "stat_type":          "prop_type",
        "stat_norm":          "prop_type",
        "line_score":         "line",
        "recommended_side":   "direction",
        "composite_hit_rate": "hit_rate",
        "avg_L5":             "l5_avg",
        "avg_season":         "season_avg",
        "def_tier":           "def_tier",
        "def_rank":           "def_rank",
        "prop_score":         "rank_score",
        "game_start":         "game_time",
        "opponent":           "opp",
        "line_hit_rate_over_ou_5":  "hit_rate",
        "line_hit_rate_over_ou_10": "_soccer_hit10",
        "Game Script Mult": "game_script_mult",
        "Game Script Note": "game_script_note",
        "game_script_mult": "game_script_mult",
        "game_script_note": "game_script_note",
    })

    if "opp" not in df.columns:
        df["opp"] = ""

    df = df.loc[:, ~df.columns.duplicated()].copy()
    df["sport"] = "NBA1Q"

    # Ensure ml_prob is numeric and always present for downstream leg scoring.
    if "ml_prob" not in df.columns:
        df["ml_prob"] = np.nan
    df["ml_prob"] = pd.to_numeric(df["ml_prob"], errors="coerce")

    # Derive L5 game sample size from over+under counts when available.
    if "l5_games" not in df.columns:
        l5o = pd.to_numeric(df.get("l5_over", np.nan), errors="coerce")
        l5u = pd.to_numeric(df.get("l5_under", np.nan), errors="coerce")
        derived = l5o.add(l5u, fill_value=0)
        df["l5_games"] = derived.where(derived > 0, np.nan)

    def _norm_pick(x):
        t = str(x).strip().lower() if x else ""
        if "gob" in t: return "Goblin"
        if "dem" in t: return "Demon"
        return "Standard"

    if "pick_type" not in df.columns:
        df["pick_type"] = "Standard"
    df["pick_type"] = df["pick_type"].apply(_norm_pick)

    if "direction" in df.columns:
        df["direction"] = df["direction"].astype(str).str.upper()
    else:
        df["direction"] = "OVER"

    if "tier" in df.columns:
        df["tier"] = df["tier"].astype(str).str.upper()
    else:
        df["tier"] = "C"

    if "hit_rate" not in df.columns:
        df["hit_rate"] = np.nan

    for col in ["rank_score", "hit_rate", "line", "_soccer_hit10"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Prefer L5 hit rate, then L10, when either is present.
    if "_soccer_hit10" in df.columns:
        df["hit_rate"] = df["hit_rate"].combine_first(df["_soccer_hit10"])
        df.drop(columns=["_soccer_hit10"], inplace=True)

    # Normalize hit_rate to 0–1 (handles "62%" or 62.0 from spreadsheets)
    if "hit_rate" in df.columns and df["hit_rate"].notna().any():
        hr = df["hit_rate"]
        if hr.dtype == object:
            hr = hr.astype(str).str.replace("%", "", regex=False).str.strip()
            hr = pd.to_numeric(hr, errors="coerce")
        else:
            hr = pd.to_numeric(hr, errors="coerce")
        if hr.dropna().max() is not None and hr.dropna().max() > 1.5:
            hr = hr / 100.0
        df["hit_rate"] = hr

    # Still no usable hit rate (common on current Soccer pipeline when game logs are empty).
    # Use a mild rank_score-based proxy so tier/rank ticket gates still run; re-run step5 when HRs exist.
    hr_series = pd.to_numeric(df["hit_rate"], errors="coerce")
    if hr_series.notna().sum() == 0:
        rs = pd.to_numeric(df.get("rank_score", 0), errors="coerce").fillna(0.0)
        q25, q75 = float(rs.quantile(0.25)), float(rs.quantile(0.75))
        span = (q75 - q25) + 1e-6
        proxy = 0.54 + ((rs - q25) / span).clip(lower=0.0, upper=1.0) * 0.12
        df["hit_rate"] = proxy.clip(0.50, 0.68)
        print(
            "  [load_nba1q] NOTE: Hit Rate (5g)/(10g) empty - using rank_score proxy for ticket eligibility. "
            "Fix Soccer step5 line-hit output when possible."
        )

    if "edge" not in df.columns:
        df["edge"] = 0.0

    if "espn_player_id" in df.columns:
        df["espn_player_id"] = df["espn_player_id"].apply(_clean_id)

    df = df[df["line"].notna() & (df["line"] >= 0)]
    df = df.astype(object).where(df.notna(), other=None)
    return df


def load_nba1h(path: str) -> pd.DataFrame:
    path = resolve_input_path(path, fallback_filename="step8_nba1h_direction_clean.xlsx")

    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet = "NBA1H" if "NBA1H" in xl.sheet_names else (
        "ALL" if "ALL" in xl.sheet_names else xl.sheet_names[0])
    df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")

    df = df.rename(columns={
        # title-case (from step8 clean xlsx)
        "Player":           "player",
        "Tier":             "tier",
        "Rank Score":       "rank_score",
        "Pos":              "pos",
        "Team":             "team",
        "Opp":              "opp",
        "Game Time":        "game_time",
        "Prop":             "prop_type",
        "Pick Type":        "pick_type",
        "Line":             "line",
        "Direction":        "direction",
        "Edge":             "edge",
        "Projection":       "projection",
        "ESPN ID":          "espn_player_id",
        "ML Prob":          "ml_prob",
        "Hit Rate (5g)":    "hit_rate",
        # Kept separate so we can coalesce into hit_rate when 5g is blank (common when
        # Soccer step5/7 line-hit columns aren't populated yet).
        "Hit Rate (10g)":   "_soccer_hit10",
        "Last 5 Avg":       "l5_avg",
        "Season Avg":       "season_avg",
        "L5 Over":          "l5_over",
        "L5 Under":         "l5_under",
        "Def Rank":         "def_rank",
        "Def Tier":         "def_tier",
        "Min Tier":         "min_tier",
        "Shot Role":        "shot_role",
        "Usage Role":       "usage_role",
        "League":           "league",
        "Pos Group":        "position_group",
        "Void Reason":      "void_reason",
        # snake_case fallbacks
        "player_name":        "player",
        "stat_type":          "prop_type",
        "stat_norm":          "prop_type",
        "line_score":         "line",
        "recommended_side":   "direction",
        "composite_hit_rate": "hit_rate",
        "avg_L5":             "l5_avg",
        "avg_season":         "season_avg",
        "def_tier":           "def_tier",
        "def_rank":           "def_rank",
        "prop_score":         "rank_score",
        "game_start":         "game_time",
        "opponent":           "opp",
        "line_hit_rate_over_ou_5":  "hit_rate",
        "line_hit_rate_over_ou_10": "_soccer_hit10",
        "Game Script Mult": "game_script_mult",
        "Game Script Note": "game_script_note",
        "game_script_mult": "game_script_mult",
        "game_script_note": "game_script_note",
    })

    if "opp" not in df.columns:
        df["opp"] = ""

    df = df.loc[:, ~df.columns.duplicated()].copy()
    df["sport"] = "NBA1H"

    # Ensure ml_prob is numeric and always present for downstream leg scoring.
    if "ml_prob" not in df.columns:
        df["ml_prob"] = np.nan
    df["ml_prob"] = pd.to_numeric(df["ml_prob"], errors="coerce")

    # Derive L5 game sample size from over+under counts when available.
    if "l5_games" not in df.columns:
        l5o = pd.to_numeric(df.get("l5_over", np.nan), errors="coerce")
        l5u = pd.to_numeric(df.get("l5_under", np.nan), errors="coerce")
        derived = l5o.add(l5u, fill_value=0)
        df["l5_games"] = derived.where(derived > 0, np.nan)

    def _norm_pick(x):
        t = str(x).strip().lower() if x else ""
        if "gob" in t: return "Goblin"
        if "dem" in t: return "Demon"
        return "Standard"

    if "pick_type" not in df.columns:
        df["pick_type"] = "Standard"
    df["pick_type"] = df["pick_type"].apply(_norm_pick)

    if "direction" in df.columns:
        df["direction"] = df["direction"].astype(str).str.upper()
    else:
        df["direction"] = "OVER"

    if "tier" in df.columns:
        df["tier"] = df["tier"].astype(str).str.upper()
    else:
        df["tier"] = "C"

    if "hit_rate" not in df.columns:
        df["hit_rate"] = np.nan

    for col in ["rank_score", "hit_rate", "line", "_soccer_hit10"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Prefer L5 hit rate, then L10, when either is present.
    if "_soccer_hit10" in df.columns:
        df["hit_rate"] = df["hit_rate"].combine_first(df["_soccer_hit10"])
        df.drop(columns=["_soccer_hit10"], inplace=True)

    # Normalize hit_rate to 0–1 (handles "62%" or 62.0 from spreadsheets)
    if "hit_rate" in df.columns and df["hit_rate"].notna().any():
        hr = df["hit_rate"]
        if hr.dtype == object:
            hr = hr.astype(str).str.replace("%", "", regex=False).str.strip()
            hr = pd.to_numeric(hr, errors="coerce")
        else:
            hr = pd.to_numeric(hr, errors="coerce")
        if hr.dropna().max() is not None and hr.dropna().max() > 1.5:
            hr = hr / 100.0
        df["hit_rate"] = hr

    # Still no usable hit rate (common on current Soccer pipeline when game logs are empty).
    # Use a mild rank_score-based proxy so tier/rank ticket gates still run; re-run step5 when HRs exist.
    hr_series = pd.to_numeric(df["hit_rate"], errors="coerce")
    if hr_series.notna().sum() == 0:
        rs = pd.to_numeric(df.get("rank_score", 0), errors="coerce").fillna(0.0)
        q25, q75 = float(rs.quantile(0.25)), float(rs.quantile(0.75))
        span = (q75 - q25) + 1e-6
        proxy = 0.54 + ((rs - q25) / span).clip(lower=0.0, upper=1.0) * 0.12
        df["hit_rate"] = proxy.clip(0.50, 0.68)
        print(
            "  [load_nba1h] NOTE: Hit Rate (5g)/(10g) empty - using rank_score proxy for ticket eligibility. "
            "Fix Soccer step5 line-hit output when possible."
        )

    if "edge" not in df.columns:
        df["edge"] = 0.0

    if "espn_player_id" in df.columns:
        df["espn_player_id"] = df["espn_player_id"].apply(_clean_id)

    df = df[df["line"].notna() & (df["line"] >= 0)]
    df = df.astype(object).where(df.notna(), other=None)
    return df


def add_l5_play_side_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each row: L5 hits on the recommended side (over vs under vs line) and
    hits / (l5_over + l5_under) when that sample size is known (>0).
    """
    if df is None or df.empty:
        return df
    out = df.copy()
    lo = pd.to_numeric(out.get("l5_over"), errors="coerce")
    lu = pd.to_numeric(out.get("l5_under"), errors="coerce")
    if "direction" not in out.columns:
        out["l5_side_hits"] = np.nan
        out["l5_consistency"] = np.nan
        return out
    d = out["direction"].astype(str).str.strip().str.upper()
    lo_a = lo.to_numpy(dtype=float, copy=True)
    lu_a = lu.to_numpy(dtype=float, copy=True)
    hits = np.select(
        [d.eq("OVER").to_numpy(), d.eq("UNDER").to_numpy()],
        [lo_a, lu_a],
        default=np.nan,
    )
    out["l5_side_hits"] = hits
    denom = lo_a + lu_a
    denom = np.where(denom > 0, denom, np.nan)
    out["l5_consistency"] = hits / denom
    return out


# ── Merge to full slate ────────────────────────────────────────────────────────
def build_combined_slate(
    nba: pd.DataFrame,
    cbb: pd.DataFrame,
    nhl: pd.DataFrame = None,
    soccer: pd.DataFrame = None,
    wcbb: pd.DataFrame = None,
    mlb: pd.DataFrame = None,
    nba1q: pd.DataFrame = None,
    nba1h: pd.DataFrame = None,
) -> pd.DataFrame:
    keep = [
        "sport",
        "tier",
        "rank_score",
        "player",
        "team",
        "opp",
        "team_seed",
        "team_region",
        "team_ap_rank",
        "opp_seed",
        "opp_region",
        "opp_ap_rank",
        "ncaa_rank",
        "game_time",
        "game_date",
        "prop_type",
        "pick_type",
        "line",
        "direction",
        "edge",
        "abs_edge",
        "projection",
        "hit_rate",
        "ml_prob",
        "l5_avg",
        "season_avg",
        "l5_over",
        "l5_under",
        "l10_over",
        "l10_under",
        "def_tier",
        "pace_tier",
        "context_score",
        "min_tier",
        "shot_role",
        "usage_role",
        "nba_player_id",
        "espn_player_id",
        "league",
        "position_group",
    ]

    def safe_keep(df, cols):
        df = df.loc[:, ~df.columns.duplicated()].copy()
        return df.reindex(columns=cols).copy()

    frames = [safe_keep(nba, keep), safe_keep(cbb, keep)]
    if nhl is not None and len(nhl) > 0:
        frames.append(safe_keep(nhl, keep))
    if soccer is not None and len(soccer) > 0:
        frames.append(safe_keep(soccer, keep))
    if wcbb is not None and len(wcbb) > 0:
        frames.append(safe_keep(wcbb, keep))
    if mlb is not None and len(mlb) > 0:
        frames.append(safe_keep(mlb, keep))
    if nba1q is not None and len(nba1q) > 0:
        frames.append(safe_keep(nba1q, keep))
    if nba1h is not None and len(nba1h) > 0:
        frames.append(safe_keep(nba1h, keep))
    combined = pd.concat(frames, ignore_index=True)

    if "rank_score" in combined.columns:
        combined["rank_score"] = pd.to_numeric(combined["rank_score"], errors="coerce")
    if "hit_rate" in combined.columns:
        combined["hit_rate"] = pd.to_numeric(combined["hit_rate"], errors="coerce")
    if "ml_prob" in combined.columns:
        combined["ml_prob"] = pd.to_numeric(combined["ml_prob"], errors="coerce")
    if "edge" in combined.columns:
        combined["edge"] = pd.to_numeric(combined["edge"], errors="coerce")
    if "abs_edge" in combined.columns:
        combined["abs_edge"] = pd.to_numeric(combined["abs_edge"], errors="coerce")

    combined = add_l5_play_side_columns(combined)

    combined = combined.sort_values("rank_score", ascending=False, na_position="last").reset_index(drop=True)
    return combined


def _edge_magnitude_series(df: pd.DataFrame) -> pd.Series:
    """Use abs_edge when present so UNDER legs are not dropped by min_edge filters."""
    if df is None or len(df) == 0:
        return pd.Series(dtype=float)
    if "abs_edge" in df.columns:
        ae = pd.to_numeric(df["abs_edge"], errors="coerce")
        if ae.notna().any():
            return ae
    return pd.to_numeric(df.get("edge", np.nan), errors="coerce").abs()


# ── Filter eligible props for tickets ─────────────────────────────────────────
def filter_eligible(df: pd.DataFrame, min_hit_rate=0.55, min_edge=0.0, min_rank=None, tiers=None, pick_types=None):
    mask = pd.Series([True] * len(df), index=df.index)
    MIN_SAMPLE_FOR_TICKET = 4
    if "l5_games" in df.columns:
        mask &= pd.to_numeric(df["l5_games"], errors="coerce").fillna(0) >= MIN_SAMPLE_FOR_TICKET
    elif "sample_n" in df.columns:
        mask &= pd.to_numeric(df["sample_n"], errors="coerce").fillna(0) >= MIN_SAMPLE_FOR_TICKET
    if "prop_type" in df.columns:
        prop_norm = df["prop_type"].apply(_norm_prop_label)
        mask &= ~prop_norm.isin(TICKET_EXCLUDED_PROPS)
    # Only hard-exclude rows that truly cannot be ticketed.
    if "void_reason" in df.columns:
        vs = df["void_reason"]
        void_str = vs.astype(str).str.strip()
        mask &= ~void_str.eq("NO_PROJECTION_OR_LINE")
    l5_o = pd.to_numeric(df.get("l5_over"), errors="coerce").fillna(0)
    l5_u = pd.to_numeric(df.get("l5_under"), errors="coerce").fillna(0)
    strong_l5 = (l5_o >= 4) | (l5_u >= 4)
    if min_hit_rate > 0 and "hit_rate" in df.columns:
        mask &= (df["hit_rate"].fillna(0) >= min_hit_rate) | strong_l5
    if min_edge > 0:
        mask &= _edge_magnitude_series(df).fillna(0) >= min_edge
    if min_rank is not None and "rank_score" in df.columns:
        mask &= df["rank_score"].fillna(-99) >= min_rank
    if tiers and "tier" in df.columns:
        mask &= df["tier"].isin([t.upper() for t in tiers])
    if pick_types and "pick_type" in df.columns:
        mask &= df["pick_type"].isin(pick_types)
    return df[mask].copy()


# Per-sport ticket structures: n_legs, pick pool (goblin vs standard), and
# direction/sort flow (power vs flex vs standard 2-leg).
_STRUCTURE_SPECS: dict[str, dict[str, object]] = {
    "power": {"n_legs": 2, "pool": "goblin", "flow": "power"},
    "flex": {"n_legs": 3, "pool": "goblin", "flow": "flex"},
    "standard": {"n_legs": 2, "pool": "standard", "flow": "standard"},
    "power_std3": {"n_legs": 3, "pool": "standard", "flow": "power"},
    "goblin3": {"n_legs": 3, "pool": "goblin", "flow": "power"},
}


def build_single_structure_ticket(
    pool_df: pd.DataFrame,
    sport_label: str,
    structure: str,
    counters: dict | None = None,
    relaxed: bool = False,
    min_leg_hit_rate: float | None = None,
    prioritize_ticket_hit: bool = False,
    ticket_sort_mode: str = "rank",
    ticket_gen_starts: int = 6,
) -> dict | None:
    """
    Build exactly one best ticket for a sport+structure.
    With ticket_gen_starts>1, tries several first-leg seeds and keeps the combo that maximizes
    modeled ticket payout (flex cash for flex 3+, else all-hit prob).
    """
    if pool_df is None or pool_df.empty:
        return None

    spec = _STRUCTURE_SPECS.get(structure)
    if not spec:
        return None

    n_legs = int(spec["n_legs"])
    pool_kind = str(spec["pool"])
    flow = str(spec["flow"])
    allowed_tiers = {"A", "B", "C", "D"}
    q = 0.70 if flow in ("power", "standard") else 0.50  # top 30% / top 50%

    # NHL and Soccer don't use Goblin/Standard split — all props are Standard.
    # Skip pick_type filtering for these sports so Power/Flex can use Standard props.
    sport_up = sport_label.upper()
    skip_picktype_filter = sport_up in ("NHL", "SOCCER", "SOCCER")

    df = pool_df.copy()
    if "pick_type" in df.columns and not skip_picktype_filter:
        pt = df["pick_type"].astype(str).str.strip().str.lower()
        if pool_kind == "standard":
            df = df[pt == "standard"]
        else:
            df = df[pt == "goblin"]
    if "tier" in df.columns and not (relaxed and structure == "standard"):
        df = df[df["tier"].astype(str).str.upper().isin(allowed_tiers)]

    prop_norm = df["prop_type"].apply(_norm_prop_label) if "prop_type" in df.columns else pd.Series([""] * len(df))
    excl_mask = prop_norm.isin(TICKET_EXCLUDED_PROPS)
    if counters is not None:
        fantasy_mask = prop_norm.str.contains("fantasy", na=False)
        counters["fantasy_excluded_count"] += int((excl_mask & fantasy_mask).sum())
        counters["ban_list_filtered_count"] += int((excl_mask & ~fantasy_mask).sum())
    df = df[~excl_mask].copy()

    if flow in ("power", "standard"):
        df_prop_norm = df["prop_type"].apply(_norm_prop_label) if "prop_type" in df.columns else pd.Series([""] * len(df))
        df = df[~df_prop_norm.isin(TIER3_PROPS)].copy()
        # Explicitly block steals from Power Play and Standard 2-leg tickets.
        df_prop_norm = df["prop_type"].apply(_norm_prop_label) if "prop_type" in df.columns else pd.Series([""] * len(df))
        df = df[~df_prop_norm.eq("steals")].copy()

    # Keep generator aligned with ticket_eval render filters so tickets do not
    # collapse into partials during HTML rendering.
    if "direction" in df.columns and "line" in df.columns and "prop_type" in df.columns:
        ddir = df["direction"].astype(str).str.strip().str.upper()
        dprop = df["prop_type"].apply(_norm_prop_label)
        dline = pd.to_numeric(df["line"], errors="coerce")
        line_ok = pd.Series([True] * len(df), index=df.index)
        line_ok &= ~((dprop == "points") & (ddir == "OVER") & (dline < 8.0))
        line_ok &= ~((dprop == "rebounds") & (ddir == "OVER") & (dline < 2.5))
        df = df[line_ok].copy()

    if "rank_score" in df.columns and len(df) > 0 and not (relaxed and structure == "standard"):
        rs = pd.to_numeric(df["rank_score"], errors="coerce")
        cutoff = float(rs.quantile(q))
        df = df[rs >= cutoff].copy()
    if df.empty:
        return None

    # Direction rules
    dirs = df.get("direction", pd.Series([""] * len(df), index=df.index)).astype(str).str.upper().str.strip()
    over_df = df[dirs == "OVER"].copy()
    under_df = df[dirs == "UNDER"].copy()

    if flow == "standard":
        # Standard: OVER only.
        cand = over_df.copy()
    elif flow == "power":
        # Power: OVER only unless not enough OVER legs.
        cand = over_df if len(over_df) >= n_legs else pd.concat([over_df, under_df], ignore_index=True)
    else:
        # Flex: UNDER only for explicitly allowed props and strong hit-rate history.
        if not under_df.empty:
            up = under_df["prop_type"].apply(_norm_prop_label)
            uhr = pd.to_numeric(under_df.get("hit_rate", 0), errors="coerce").fillna(0.0)
            under_df = under_df[(up.isin(UNDER_ALLOWED_PROPS)) & (uhr >= 0.65)].copy()
        cand = pd.concat([over_df, under_df], ignore_index=True)

    if cand.empty:
        return None

    if min_leg_hit_rate is not None and float(min_leg_hit_rate) > 0 and "hit_rate" in cand.columns:
        thr = float(min_leg_hit_rate)
        cand = cand[pd.to_numeric(cand["hit_rate"], errors="coerce").fillna(0) >= thr].copy()
    if cand.empty:
        return None

    if counters is not None:
        pct_cap = counters.get("player_ticket_counts")
        if pct_cap is not None and len(cand) > 0 and "player" in cand.columns:
            pn = cand["player"].map(_norm_player_join)
            cap_ok = pn.eq("") | pn.map(lambda p: int(pct_cap.get(p, 0)) < MAX_SLIPS_PER_PLAYER)
            cand = cand[cap_ok].copy()
    if cand.empty:
        return None

    if counters is not None:
        counters["total_eligible_count"] += int(len(cand))

    cand = _attach_ticket_pick_order(cand, ticket_sort_mode)
    cand["__over_pref"] = cand.get("direction", "").astype(str).str.upper().eq("OVER").astype(int)
    bonus = cand["prop_type"].apply(_prop_priority_bonus) if "prop_type" in cand.columns else 0.0
    cand["__score_adj"] = cand["__ts_pri"] + bonus
    if flow == "standard":
        cand = cand.sort_values(
            ["__over_pref", "__ts_pri", "__ts_sec"], ascending=[False, False, False], na_position="last"
        )
    else:
        cand = cand.sort_values(
            ["__over_pref", "__score_adj", "__ts_sec"], ascending=[False, False, False], na_position="last"
        )

    tg_starts = max(1, int(ticket_gen_starts))
    if tg_starts <= 1:
        chosen: list[pd.Series] = []
        used_players: set[str] = set()
        for _, r in cand.iterrows():
            p = str(r.get("player", "")).strip().lower()
            if not p or p in used_players:
                continue
            chosen.append(r)
            used_players.add(p)
            if len(chosen) == n_legs:
                break
        if len(chosen) < n_legs:
            return None
        rows = [x.to_dict() for x in chosen]
        leg_probs = []
        for r in rows:
            _p, _src = _resolve_leg_prob(r)
            leg_probs.append((_p, _src))
        cmult, caudit = _correlation_multiplier_and_audit(rows)
        ep = win_prob(leg_probs, n_legs) * cmult
        flex_cash = flex_cash_prob(leg_probs) * cmult if n_legs >= 3 else ep
        obj_score = flex_cash if flow == "flex" and n_legs >= 3 else ep
    else:
        rows, obj_score, ep, flex_cash = _pick_best_greedy_ticket_by_paid_metric(
            cand, n_legs, flow, tg_starts
        )
        if not rows:
            return None
        leg_probs = [_resolve_leg_prob(pd.Series(r)) for r in rows]
        cmult, caudit = _correlation_multiplier_and_audit(rows)

    hrs = [float(r.get("hit_rate", 0.5) or 0.5) for r in rows]
    rss = [float(r.get("rank_score", 0.0) or 0.0) for r in rows]

    if prioritize_ticket_hit:
        if flow == "flex" and n_legs >= 3:
            if flex_cash < float(MIN_PRIORITIZE_MODELED_FLEX_CASH_PROB):
                return None
        elif ep < float(MIN_PRIORITIZE_MODELED_POWER_WIN_PROB):
            return None

    payout = PAYOUT.get(n_legs, {"power": 0, "flex": 0})
    pwr = payout["power"]
    flx = payout["flex"]
    adj_power = calc_adjusted_payout(pwr, rows)
    adj_flex = calc_adjusted_payout(flx, rows)

    # Requested expected-win metadata
    tiers = [str(r.get("tier", "")).upper() for r in rows]
    defs = [_norm_prop_label(r.get("def_tier", r.get("opp_def_tier", ""))) for r in rows]
    if all(t in {"A", "B"} for t in tiers) and all(d in {"avg", "weak"} for d in defs):
        expected_win_rate = 0.78
    else:
        expected_win_rate = 0.68

    pct_out = counters.get("player_ticket_counts") if counters else None
    if pct_out is not None and not _ticket_cap_can_add(rows, pct_out):
        return None
    _ticket_cap_register(rows, pct_out)

    return {
        "key": frozenset((str(r.get("player", "")) + "|" + str(r.get("prop_type", ""))).strip() for r in rows),
        "rows": rows,
        "avg_hit_rate": float(np.mean(hrs)) if hrs else 0.0,
        "avg_rank_score": float(np.mean(rss)) if rss else 0.0,
        "est_win_prob": ep,
        "ticket_objective_score": round(float(obj_score), 4),
        "est_flex_cash_prob": round(float(flex_cash), 4) if n_legs >= 3 else None,
        "power_payout": adj_power,
        "flex_payout": adj_flex,
        "base_power_payout": pwr,
        "payout_multiplier": round(adj_power / pwr, 4) if pwr else 1.0,
        "ev_power": round(ep * adj_power, 4),
        "kelly_units": round(kelly_fraction(ep, adj_power, fraction=0.25), 2),
        "n_legs": n_legs,
        "expected_win_rate": expected_win_rate,
        "correlation_multiplier": cmult,
        "correlation_audit": caudit,
        "ticket_type": (
            "Standard"
            if structure == "standard"
            else (
                "Flex"
                if structure == "flex"
                else (
                    "Power Standard 3"
                    if structure == "power_std3"
                    else "Goblin 3" if structure == "goblin3" else "Power Play"
                )
            )
        ),
        "sport": sport_label,
    }


def apply_nba_context_confidence_filter(
    df: pd.DataFrame,
    enabled: bool = True,
    min_context_score: int = 2,
    min_l5_sample: int = 5,
) -> pd.DataFrame:
    """
    Context-aware filter calibrated from NBA backtest tendencies:
    - OVER performs better vs weaker defenses and faster pace
    - UNDER performs better vs stronger defenses and normal/slower pace
    - Require minimal recent sample size (L5 over+under count)

    Applies only to NBA Standard picks. Other sports/pick-types pass through unchanged.
    """
    if not enabled or df.empty:
        return df

    out = df.copy()
    if "sport" not in out.columns:
        return out

    sport = out["sport"].astype(str).str.upper()
    if "pick_type" in out.columns:
        pick = out["pick_type"].astype(str).str.title()
    else:
        pick = pd.Series(["Standard"] * len(out), index=out.index)

    is_nba_standard = (sport == "NBA") & (pick == "Standard")
    if not is_nba_standard.any():
        return out

    direction = out.get("direction", pd.Series("", index=out.index)).astype(str).str.upper()
    def_tier = out.get("def_tier", pd.Series("", index=out.index)).astype(str).str.upper().str.strip()
    pace_tier = out.get("pace_tier", pd.Series("", index=out.index)).astype(str).str.upper().str.strip()

    l5_over = pd.to_numeric(out.get("l5_over", 0), errors="coerce").fillna(0)
    l5_under = pd.to_numeric(out.get("l5_under", 0), errors="coerce").fillna(0)
    l5_sample = l5_over + l5_under

    def_over_good = def_tier.isin(["WEAK", "AVG", "ABOVE AVG", "AVERAGE"])
    def_under_good = def_tier.isin(["ELITE", "SOLID"])
    pace_over_good = pace_tier.eq("FAST")
    pace_under_good = pace_tier.isin(["NORMAL", "SLOW"])

    score = pd.Series(0, index=out.index, dtype="int64")
    score += (l5_sample >= min_l5_sample).astype(int)
    score += (((direction == "OVER") & def_over_good) | ((direction == "UNDER") & def_under_good)).astype(int)
    score += (((direction == "OVER") & pace_over_good) | ((direction == "UNDER") & pace_under_good)).astype(int)

    keep = (~is_nba_standard) | (score >= int(min_context_score))

    kept_before = len(out)
    out = out[keep].copy()
    dropped = kept_before - len(out)
    if dropped > 0:
        print(
            f"  [context_filter] Dropped {dropped} NBA standard rows "
            f"(score < {min_context_score}, min_l5_sample={min_l5_sample})"
        )
    return out


def compute_bet_signal_core(leg: dict) -> tuple[int, list[str]]:
    """
    Context score 0–3+ and human reasons; same rules as web direction_signal().
    """
    sport = str(leg.get("sport", "") or "").upper()
    direction = str(leg.get("direction", "") or "").upper()
    def_tier = str(leg.get("def_tier", "") or "").upper().strip()
    pace_tier = str(leg.get("pace_tier", "") or "").upper().strip()
    l5o = _signal_float(leg.get("l5_over"))
    l5u = _signal_float(leg.get("l5_under"))
    l5_sample = int(round((l5o or 0) + (l5u or 0)))

    explicit_score = _signal_float(leg.get("context_score"))
    score = int(round(explicit_score)) if explicit_score is not None else 0
    reasons: list[str] = []

    if explicit_score is None:
        if l5_sample >= 5:
            score += 1
            reasons.append("enough recent sample")

        over_def_good = def_tier in {"WEAK", "AVG", "ABOVE AVG", "AVERAGE"}
        under_def_good = def_tier in {"ELITE", "SOLID"}
        if (direction == "OVER" and over_def_good) or (direction == "UNDER" and under_def_good):
            score += 1
            reasons.append(f"defense supports {direction}")

        over_pace_good = pace_tier == "FAST"
        under_pace_good = pace_tier in {"NORMAL", "SLOW"}
        if (direction == "OVER" and over_pace_good) or (direction == "UNDER" and under_pace_good):
            score += 1
            reasons.append(f"pace supports {direction}")
    else:
        reasons.append(f"context score {int(round(explicit_score))}")
        if l5_sample > 0:
            reasons.append(f"L5 sample {l5_sample}")

    if sport != "NBA" and not reasons:
        hr = _signal_float(leg.get("hit_rate")) or 0.0
        ae = _signal_float(leg.get("abs_edge"))
        edge = ae if ae is not None else abs(_signal_float(leg.get("edge")) or 0.0)
        if hr >= 0.62 and edge > 0:
            score = 2
            reasons.append("strong model profile")
        elif hr >= 0.55:
            score = 1
            reasons.append("model lean")
        else:
            reasons.append("model-only read")

    return score, reasons


def excel_signal_columns_from_leg(leg: dict) -> dict[str, str]:
    score, _ = compute_bet_signal_core(leg)
    return {
        "bet_strong": "Y" if score >= 3 else "",
        "bet_lean": "Y" if score == 2 else "",
        "bet_risk": "LOW" if score >= 3 else ("MED" if score == 2 else "HIGH"),
    }


def apply_full_slate_signal_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df.copy()

    def _one(row: pd.Series) -> pd.Series:
        return pd.Series(excel_signal_columns_from_leg(row.to_dict()))

    sig = out.apply(_one, axis=1)
    return pd.concat([out, sig], axis=1)


# ── Build tickets ──────────────────────────────────────────────────────────────
def build_tickets(
    pool: pd.DataFrame,
    n_legs: int,
    max_tickets=20,
    require_mix=False,
    leg_min_hit_by_n: dict[int, float] | None = None,
    prioritize_ticket_hit: bool = False,
    ticket_sort_mode: str = "rank",
    player_ticket_counts: dict[str, int] | None = None,
) -> list:
    """
    Smart ticket builder with quality filters per leg count.

    Key improvements vs original:
    - Per-leg min hit rate floor (longer tickets require higher floor)
    - Tier floor per leg count for longer tickets (5/6-leg = Tier A/B only)
    - Tickets sorted by est_win_prob DESC then avg_rank_score (optimises for actual wins)
    - require_mix still enforced for cross-sport sheets
    """
    pool = pool.copy().reset_index(drop=True)
    tickets = []

    # ── Per-leg-count quality filters ─────────────────────────────────────────
    _lim = leg_min_hit_by_n or LEG_MIN_HIT_RATE
    min_hr = float(_lim.get(n_legs, LEG_MIN_HIT_RATE.get(n_legs, 0.55)))
    ok_tiers = POWER_MIN_TIER.get(n_legs, ["A", "B", "C", "D"])

    # Apply hit rate floor to this pool
    if "hit_rate" in pool.columns:
        pool = pool[pool["hit_rate"].fillna(0) >= min_hr].copy()

    # Apply tier floor for 5/6-leg tickets
    if n_legs >= 5 and "tier" in pool.columns:
        pool = pool[pool["tier"].isin(ok_tiers)].copy()

    pool = pool.reset_index(drop=True)

    has_sport_col = "sport" in pool.columns
    sports_available = pool["sport"].dropna().unique().tolist() if has_sport_col else []
    can_mix = require_mix and has_sport_col and len(sports_available) >= 2

    eligible = (
        _attach_ticket_pick_order(pool, ticket_sort_mode)
        .sort_values(["__ts_pri", "__ts_sec"], ascending=[False, False], na_position="last")
        .reset_index(drop=True)
    )
    max_fantasy = MAX_FANTASY_LEGS.get(n_legs, n_legs)

    for _ in range(max_tickets * 5):
        if len(tickets) >= max_tickets:
            break

        ticket_rows = []
        ticket_players = set()
        sports_in_ticket = set()
        fantasy_count = 0

        if can_mix:
            for sport in sports_available:
                sport_pool = eligible[eligible["sport"] == sport]
                for _, row in sport_pool.iterrows():
                    player = str(row.get("player", "")).strip().lower()
                    if player and player not in ticket_players:
                        ticket_rows.append(row)
                        ticket_players.add(player)
                        sports_in_ticket.add(sport)
                        break

            for _, row in eligible.iterrows():
                if len(ticket_rows) == n_legs:
                    break
                player = str(row.get("player", "")).strip().lower()
                if player and player not in ticket_players:
                    if _is_fantasy_prop(row) and fantasy_count >= max_fantasy:
                        continue
                    ticket_rows.append(row)
                    ticket_players.add(player)
                    sports_in_ticket.add(row.get("sport", ""))
                    if _is_fantasy_prop(row):
                        fantasy_count += 1
        else:
            for _, row in eligible.iterrows():
                if len(ticket_rows) == n_legs:
                    break
                player = str(row.get("player", "")).strip().lower()
                if player and player not in ticket_players:

                    if _is_fantasy_prop(row) and fantasy_count >= max_fantasy:
                        continue

                    ticket_rows.append(row)
                    ticket_players.add(player)
                    if _is_fantasy_prop(row):
                        fantasy_count += 1

        # If diversity cap was too strict to fill a ticket, backfill best remaining legs.
        if len(ticket_rows) < n_legs:
            for _, row in eligible.iterrows():
                if len(ticket_rows) == n_legs:
                    break
                player = str(row.get("player", "")).strip().lower()
                if player and player not in ticket_players:
                    ticket_rows.append(row)
                    ticket_players.add(player)
                    sports_in_ticket.add(row.get("sport", ""))

        if len(ticket_rows) == n_legs:
            if can_mix and len(sports_in_ticket) < 2:
                if len(eligible) > 1:
                    eligible = eligible.iloc[1:].reset_index(drop=True)
                continue

            if can_mix:
                ticket_rows = sorted(
                    ticket_rows,
                    key=lambda r: (str(r.get("sport", "")), -float(r.get("rank_score", 0) or 0)),
                )

            key = frozenset(
                (str(r.get("player", "")) + "|" + str(r.get("prop_type", ""))).strip() for r in ticket_rows
            )

            if key not in [t["key"] for t in tickets]:
                hrs = []
                rss = []
                prob_srcs = []
                for r in ticket_rows:
                    hrs.append(float(r.get("hit_rate", 0.5) or 0.5))
                    rss.append(float(r.get("rank_score", 0) or 0))
                    _p, _src = _resolve_leg_prob(r)
                    prob_srcs.append(_src)
                avg_hr = float(np.mean(hrs)) if hrs else 0.0
                avg_rs = float(np.mean(rss)) if rss else 0.0
                leg_probs = [_resolve_leg_prob(r) for r in ticket_rows]  # [(p, src), ...]
                cmult, caudit = _correlation_multiplier_and_audit(ticket_rows)
                ep = win_prob(leg_probs, n_legs) * cmult
                pout = PAYOUT.get(n_legs, {"power": 0, "flex": 0})

                # Adjust payouts for Goblin (reduces) and Demon (boosts)
                adj_power = calc_adjusted_payout(pout["power"], ticket_rows)
                adj_flex  = calc_adjusted_payout(pout["flex"],  ticket_rows)

                # EV gate: skip tickets with negative expected value
                ev_power = ep * adj_power
                if ev_power < min_ev_for_ticket(n_legs):
                    continue
                if prioritize_ticket_hit and ep < float(MIN_PRIORITIZE_MODELED_POWER_WIN_PROB):
                    continue
                if not _ticket_cap_can_add(ticket_rows, player_ticket_counts):
                    continue

                tickets.append(
                    {
                        "key": key,
                        "rows": ticket_rows,
                        "avg_hit_rate": avg_hr,
                        "avg_rank_score": avg_rs,
                        "est_win_prob": ep,
                        "power_payout": adj_power,
                        "flex_payout":  adj_flex,
                        "base_power_payout": pout["power"],  # kept for reference
                        "payout_multiplier": round(adj_power / pout["power"], 4) if pout["power"] else 1.0,
                        "ev_power": round(ev_power, 4),
                        "kelly_units": round(kelly_fraction(ep, adj_power, fraction=0.25), 2),
                        "n_legs": n_legs,
                        "leg_prob_sources": ",".join(sorted(set(prob_srcs))),
                        "correlation_multiplier": cmult,
                        "correlation_audit": caudit,
                    }
                )
                _ticket_cap_register(ticket_rows, player_ticket_counts)

        if len(eligible) > n_legs:
            eligible = eligible.iloc[1:].reset_index(drop=True)
        else:
            break

    # Sort by win probability first, then rank score — optimises for actual wins
    tickets.sort(key=lambda x: (-x["est_win_prob"], -x["avg_rank_score"]))
    return tickets[:max_tickets]


# ──────────────────────────────────────────────────────────────────────────────
# FINAL web groups (ONLY the ticket sets you want) + ENFORCED Std/Gob mix
# ──────────────────────────────────────────────────────────────────────────────
def build_mixed_picktype_tickets(
    pool_df: pd.DataFrame,
    n_legs: int,
    max_tickets: int,
    min_standard: int,
    min_leg_hit_rate: float | None = None,
    prioritize_ticket_hit: bool = False,
    ticket_sort_mode: str = "rank",
    player_ticket_counts: dict[str, int] | None = None,
) -> list:
    """
    Deterministic ticket builder that enforces a minimum number of Standard legs,
    while allowing remaining legs from Standard+Goblin pool.

    - Avoids duplicate players
    - Uses rank_score descending
    - Generates variety by sliding a start offset window
    """
    pool_df = pool_df.copy()
    if "rank_score" not in pool_df.columns or "pick_type" not in pool_df.columns:
        return []

    std_raw = pool_df[pool_df["pick_type"] == "Standard"]
    gob_raw = pool_df[pool_df["pick_type"] == "Goblin"]
    std = (
        _attach_ticket_pick_order(std_raw, ticket_sort_mode)
        .sort_values(["__ts_pri", "__ts_sec"], ascending=[False, False], na_position="last")
    )
    gob = (
        _attach_ticket_pick_order(gob_raw, ticket_sort_mode)
        .sort_values(["__ts_pri", "__ts_sec"], ascending=[False, False], na_position="last")
    )

    if min_leg_hit_rate is not None and min_leg_hit_rate > 0 and "hit_rate" in std.columns:
        thr = float(min_leg_hit_rate)
        std = std[pd.to_numeric(std["hit_rate"], errors="coerce").fillna(0) >= thr].copy()
        gob = gob[pd.to_numeric(gob["hit_rate"], errors="coerce").fillna(0) >= thr].copy()

    if len(std) < min_standard:
        return []

    tickets = []
    std_start = 0
    gob_start = 0
    max_fantasy = MAX_FANTASY_LEGS.get(n_legs, n_legs)
    attempts = 0
    max_attempts = max_tickets * 50

    while len(tickets) < max_tickets and attempts < max_attempts:
        attempts += 1
        legs = []
        used_players = set()
        fantasy_count = 0

        # 1) Required Standards first
        for _, r in std.iloc[std_start:].iterrows():
            if sum(1 for x in legs if str(x.get("pick_type", "")) == "Standard") >= min_standard:
                break
            p = str(r.get("player", "")).strip().lower()
            if p and p not in used_players:
                if _is_fantasy_prop(r) and fantasy_count >= max_fantasy:
                    continue
                legs.append(r)
                used_players.add(p)
                if _is_fantasy_prop(r):
                    fantasy_count += 1

        # 2) Fill remaining legs by best rank_score from (gob slice + std slice)
        combined_ranked = pd.concat([gob.iloc[gob_start:], std.iloc[std_start:]], ignore_index=True)
        combined_ranked = (
            _attach_ticket_pick_order(combined_ranked, ticket_sort_mode)
            .sort_values(["__ts_pri", "__ts_sec"], ascending=[False, False], na_position="last")
        )

        for _, r in combined_ranked.iterrows():
            if len(legs) >= n_legs:
                break
            p = str(r.get("player", "")).strip().lower()
            if p and p not in used_players:
                if _is_fantasy_prop(r) and fantasy_count >= max_fantasy:
                    continue
                legs.append(r)
                used_players.add(p)
                if _is_fantasy_prop(r):
                    fantasy_count += 1

        # Backfill if diversity cap prevented filling all required legs.
        if len(legs) < n_legs:
            for _, r in combined_ranked.iterrows():
                if len(legs) >= n_legs:
                    break
                p = str(r.get("player", "")).strip().lower()
                if p and p not in used_players:
                    legs.append(r)
                    used_players.add(p)

        if len(legs) == n_legs:
            std_count = sum(1 for x in legs if str(x.get("pick_type", "")) == "Standard")
            if std_count >= min_standard:
                hrs = [float(x.get("hit_rate", 0.5) or 0.5) for x in legs]
                rss = [float(x.get("rank_score", 0) or 0) for x in legs]
                leg_probs = []
                prob_srcs = []
                for x in legs:
                    _p, _src = _resolve_leg_prob(x)
                    leg_probs.append((_p, _src))
                    prob_srcs.append(_src)
                avg_hr = float(np.mean(hrs)) if hrs else 0.0
                avg_rs = float(np.mean(rss)) if rss else 0.0
                cmult, caudit = _correlation_multiplier_and_audit(legs)
                ep = win_prob(leg_probs, n_legs) * cmult
                pout = PAYOUT.get(n_legs, {"power": 0, "flex": 0})

                # Adjust payouts for Goblin/Demon legs
                adj_power = calc_adjusted_payout(pout["power"], legs)
                adj_flex  = calc_adjusted_payout(pout["flex"],  legs)

                # EV gate
                ev_power = ep * adj_power
                if ev_power < min_ev_for_ticket(n_legs):
                    std_start = min(std_start + 1, max(len(std) - 1, 0))
                    if len(gob) > 0:
                        gob_start = min(gob_start + 1, max(len(gob) - 1, 0))
                    continue
                if prioritize_ticket_hit and ep < float(MIN_PRIORITIZE_MODELED_POWER_WIN_PROB):
                    std_start = min(std_start + 1, max(len(std) - 1, 0))
                    if len(gob) > 0:
                        gob_start = min(gob_start + 1, max(len(gob) - 1, 0))
                    continue

                key = frozenset((str(x.get("player", "")) + "|" + str(x.get("prop_type", ""))).strip() for x in legs)
                if key not in [t["key"] for t in tickets]:
                    if not _ticket_cap_can_add(legs, player_ticket_counts):
                        std_start = min(std_start + 1, max(len(std) - 1, 0))
                        if len(gob) > 0:
                            gob_start = min(gob_start + 1, max(len(gob) - 1, 0))
                        continue
                    tickets.append(
                        {
                            "key": key,
                            "rows": legs,
                            "avg_hit_rate": avg_hr,
                            "avg_rank_score": avg_rs,
                            "est_win_prob": ep,
                            "power_payout": adj_power,
                            "flex_payout":  adj_flex,
                            "base_power_payout": pout["power"],
                            "payout_multiplier": round(adj_power / pout["power"], 4) if pout["power"] else 1.0,
                            "ev_power": round(ev_power, 4),
                            "kelly_units": round(kelly_fraction(ep, adj_power, fraction=0.25), 2),
                            "n_legs": n_legs,
                            "leg_prob_sources": ",".join(sorted(set(prob_srcs))),
                            "correlation_multiplier": cmult,
                            "correlation_audit": caudit,
                        }
                    )
                    _ticket_cap_register(legs, player_ticket_counts)

        # Slide window to create different combos
        if len(std) > 0:
            std_start = min(std_start + 1, max(len(std) - 1, 0))
        if len(gob) > 0:
            gob_start = min(gob_start + 1, max(len(gob) - 1, 0))

    tickets.sort(key=lambda x: (-x["avg_rank_score"], -x["avg_hit_rate"]))
    return tickets[:max_tickets]


def build_final_web_ticket_groups(
    nba_pool: pd.DataFrame,
    cbb_pool: pd.DataFrame,
    nhl_pool: pd.DataFrame = None,
    soccer_pool: pd.DataFrame = None,
    mlb_pool: pd.DataFrame = None,
    min_hit_rate=0.70,
    min_edge=2.0,
    min_rank=5.0,
    ticket_leg_sizes: list | None = None,
    leg_min_hit_by_n: dict[int, float] | None = None,
    prioritize_ticket_hit: bool = False,
    ticket_sort_mode: str = "rank",
    player_ticket_counts: dict[str, int] | None = None,
):
    def apply_filters(df):
        mask = pd.Series(True, index=df.index)
        if min_hit_rate > 0 and "hit_rate" in df.columns:
            mask &= df["hit_rate"].fillna(0) >= min_hit_rate
        if min_edge > 0:
            mask &= _edge_magnitude_series(df).fillna(0) >= min_edge
        if min_rank is not None and "rank_score" in df.columns:
            mask &= df["rank_score"].fillna(-99) >= min_rank
        return df[mask].copy()

    def _split_sg(df_f: pd.DataFrame):
        if df_f is None or len(df_f) == 0:
            empty = pd.DataFrame()
            return empty, empty, empty
        if "pick_type" not in df_f.columns:
            return df_f.copy(), df_f.copy(), df_f.iloc[0:0].copy()
        mix = df_f[df_f["pick_type"].isin(["Standard", "Goblin"])].copy()
        std = df_f[df_f["pick_type"] == "Standard"].copy()
        gob = df_f[df_f["pick_type"] == "Goblin"].copy()
        return mix, std, gob

    def _min_std_mixed(n: int) -> int:
        return 2 if n >= 4 else 1

    def _sort_rank(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or len(df) == 0 or "rank_score" not in df.columns:
            return df
        return df.sort_values("rank_score", ascending=False, na_position="last")

    _pct: dict[str, int] = player_ticket_counts if player_ticket_counts is not None else defaultdict(int)

    nba_filtered = apply_filters(nba_pool)
    nba_mix, nba_std, nba_gob = _split_sg(nba_filtered)

    groups = []
    leg_sizes = ticket_leg_sizes if ticket_leg_sizes is not None else TICKET_LEG_SIZES

    def _min_hr_for_n(n: int) -> float | None:
        if not leg_min_hit_by_n:
            return None
        return float(leg_min_hit_by_n.get(int(n), LEG_MIN_HIT_RATE.get(int(n), 0.55)))

    def _add_mixed_std_gob(sub: pd.DataFrame, label: str):
        for n in leg_sizes:
            if len(sub) < n:
                continue
            mt = 2 if n == 3 else 1
            tix = build_mixed_picktype_tickets(
                sub,
                n,
                max_tickets=mt,
                min_standard=_min_std_mixed(n),
                min_leg_hit_rate=_min_hr_for_n(n),
                prioritize_ticket_hit=prioritize_ticket_hit,
                ticket_sort_mode=ticket_sort_mode,
                player_ticket_counts=_pct,
            )
            if tix:
                groups.append((f"FINAL {n}-Leg (Std+Gob {label})", tix, None))

    def _add_std_only(sub: pd.DataFrame, label: str):
        for n in leg_sizes:
            if len(sub) < n:
                continue
            tix = build_tickets(
                sub,
                n,
                max_tickets=1,
                leg_min_hit_by_n=leg_min_hit_by_n,
                prioritize_ticket_hit=prioritize_ticket_hit,
                ticket_sort_mode=ticket_sort_mode,
                player_ticket_counts=_pct,
            )
            if tix:
                groups.append((f"FINAL {n}-Leg STANDARD ONLY ({label})", tix, None))

    _add_mixed_std_gob(nba_mix, "NBA")
    _add_std_only(nba_std, "NBA")

    cbb_mix = cbb_std = cbb_gob = pd.DataFrame()
    if cbb_pool is not None and len(cbb_pool):
        cbb_f = apply_filters(cbb_pool)
        cbb_mix, cbb_std, cbb_gob = _split_sg(cbb_f)
        _add_mixed_std_gob(cbb_mix, "CBB")
        _add_std_only(cbb_std, "CBB")
        combo_ncaa = pd.concat([nba_mix, cbb_mix], ignore_index=True)
        _add_mixed_std_gob(combo_ncaa, "NBA+CBB")
        _add_std_only(pd.concat([nba_std, cbb_std], ignore_index=True), "NBA+CBB")

    nhl_mix = nhl_std = nhl_gob = pd.DataFrame()
    if nhl_pool is not None and len(nhl_pool):
        nhl_f = apply_filters(nhl_pool)
        nhl_mix, nhl_std, nhl_gob = _split_sg(nhl_f)
        _add_mixed_std_gob(nhl_mix, "NHL")
        _add_std_only(nhl_std, "NHL")

    soc_mix = soc_std = soc_gob = pd.DataFrame()
    if soccer_pool is not None and len(soccer_pool):
        soc_f = apply_filters(soccer_pool)
        soc_mix, soc_std, soc_gob = _split_sg(soc_f)
        _add_mixed_std_gob(soc_mix, "Soccer")
        _add_std_only(soc_std, "Soccer")

    mlb_mix = mlb_std = mlb_gob = pd.DataFrame()
    if mlb_pool is not None and len(mlb_pool):
        mlb_f = apply_filters(mlb_pool)
        mlb_mix, mlb_std, mlb_gob = _split_sg(mlb_f)
        _add_mixed_std_gob(mlb_mix, "MLB")
        _add_std_only(mlb_std, "MLB")

    mix_frames = [f for f in (nba_mix, cbb_mix, nhl_mix, soc_mix, mlb_mix) if len(f) > 0]
    if mix_frames:
        all_sg = _sort_rank(pd.concat(mix_frames, ignore_index=True))
        if "sport" in all_sg.columns and all_sg["sport"].nunique() >= 2:
            for n in leg_sizes:
                if len(all_sg) < n:
                    continue
                tix = build_tickets(
                    all_sg,
                    n,
                    max_tickets=2 if n == 3 else 1,
                    require_mix=True,
                    leg_min_hit_by_n=leg_min_hit_by_n,
                    prioritize_ticket_hit=prioritize_ticket_hit,
                    ticket_sort_mode=ticket_sort_mode,
                    player_ticket_counts=_pct,
                )
                if tix:
                    groups.append((f"FINAL {n}-Leg CROSS-SPORT (Std+Gob best)", tix, None))

    std_frames = [f for f in (nba_std, cbb_std, nhl_std, soc_std, mlb_std) if len(f) > 0]
    if std_frames:
        all_std = _sort_rank(pd.concat(std_frames, ignore_index=True))
        if "sport" in all_std.columns and all_std["sport"].nunique() >= 2:
            for n in leg_sizes:
                if len(all_std) < n:
                    continue
                tix = build_tickets(
                    all_std,
                    n,
                    max_tickets=2 if n == 3 else 1,
                    require_mix=True,
                    leg_min_hit_by_n=leg_min_hit_by_n,
                    prioritize_ticket_hit=prioritize_ticket_hit,
                    ticket_sort_mode=ticket_sort_mode,
                    player_ticket_counts=_pct,
                )
                if tix:
                    groups.append((f"FINAL {n}-Leg CROSS-SPORT (Standard best)", tix, None))

    return groups


def _filter_pool_cross_pick_mode(pool_df: pd.DataFrame | None, sport_label: str, mode: str) -> pd.DataFrame:
    """
    mode: standard | goblin | mix (Standard+Goblin only; Demon excluded when pick_type present).
    NHL/Soccer pools have no Goblin split — all rows count as Standard for standard/mix; goblin-only skips them.
    """
    if pool_df is None or len(pool_df) == 0:
        return pd.DataFrame()
    df = pool_df.copy()
    su = str(sport_label).upper()
    skip_pt = su in ("NHL", "SOCCER", "SOC")
    if "pick_type" in df.columns and not skip_pt:
        pt = df["pick_type"].astype(str).str.strip().str.lower()
        if mode == "standard":
            df = df[pt == "standard"].copy()
        elif mode == "goblin":
            df = df[pt == "goblin"].copy()
        elif mode == "mix":
            df = df[pt.isin(["standard", "goblin"])].copy()
    elif mode == "goblin" and skip_pt:
        return pd.DataFrame()
    return df


def _pick_top_row_from_eligible_pool(pool_df: pd.DataFrame, sort_mode: str = "rank") -> dict | None:
    """
    Best single prop in a sport's eligible pool for cross-pipeline ticket.
    With sort_mode rank/ml/blend, uses _attach_ticket_pick_order (ML-aware when not rank).
    Fallback: rank_score, blended_score, ml distance, confidence_score.
    Applies global TICKET_EXCLUDED_PROPS + fantasy ban (same family as structured tickets).
    """
    if pool_df is None or pool_df.empty:
        return None
    df = pool_df.copy()
    if "prop_type" in df.columns:
        pn = df["prop_type"].apply(_norm_prop_label)
        df = df[~pn.isin(TICKET_EXCLUDED_PROPS) & ~pn.str.contains("fantasy", na=False)].copy()
    if df.empty:
        return None
    work = df.copy()
    sm = (sort_mode or "rank").strip().lower()
    if sm in ("rank", "ml", "blend"):
        work = _attach_ticket_pick_order(work, sm)
        work["_hr"] = pd.to_numeric(work.get("hit_rate"), errors="coerce").fillna(0)
        work = work.sort_values(["__ts_pri", "__ts_sec", "_hr"], ascending=[False, False, False], na_position="last")
        return work.iloc[0].to_dict()
    if "rank_score" in work.columns and pd.to_numeric(work["rank_score"], errors="coerce").notna().any():
        work["_k"] = pd.to_numeric(work["rank_score"], errors="coerce")
    elif "blended_score" in work.columns and pd.to_numeric(work["blended_score"], errors="coerce").notna().any():
        work["_k"] = pd.to_numeric(work["blended_score"], errors="coerce")
    elif "ml_prob" in work.columns and pd.to_numeric(work["ml_prob"], errors="coerce").notna().any():
        work["_k"] = (pd.to_numeric(work["ml_prob"], errors="coerce") - 0.5).abs()
    elif "confidence_score" in work.columns and pd.to_numeric(
        work["confidence_score"], errors="coerce"
    ).notna().any():
        work["_k"] = pd.to_numeric(work["confidence_score"], errors="coerce")
    else:
        return work.iloc[0].to_dict()
    work["_hr"] = pd.to_numeric(work.get("hit_rate"), errors="coerce").fillna(0)
    work = work.sort_values(["_k", "_hr"], ascending=[False, False], na_position="last")
    return work.iloc[0].to_dict()


def _collect_cross_pipeline_rows(
    sport_pools: list[tuple[str, pd.DataFrame | None]],
    mode: str,
    max_legs: int,
    ticket_sort_mode: str = "rank",
) -> list[dict]:
    """Up to max_legs legs, one per pipeline in order, after pick-mode filter."""
    rows: list[dict] = []
    for sport_label, pool_df in sport_pools:
        if len(rows) >= max_legs:
            break
        sub = _filter_pool_cross_pick_mode(pool_df, sport_label, mode)
        picked = _pick_top_row_from_eligible_pool(sub, sort_mode=ticket_sort_mode)
        if not picked:
            continue
        d = dict(picked)
        d["sport"] = str(sport_label).upper()
        rows.append(d)
    return rows


def _finalize_cross_pipeline_ticket(rows: list[dict], ticket_type: str) -> dict | None:
    """Build ticket dict from leg rows (min 2 legs). No extra EV gate."""
    if len(rows) < 2:
        return None
    n_legs = len(rows)
    leg_probs: list = []
    prob_srcs: list[str] = []
    hrs: list[float] = []
    rss: list[float] = []
    for r in rows:
        _p, _src = _resolve_leg_prob(pd.Series(r))
        leg_probs.append((_p, _src))
        prob_srcs.append(_src)
        hrs.append(float(r.get("hit_rate", 0.5) or 0.5))
        rss.append(float(r.get("rank_score", 0.0) or 0.0))
    cmult, caudit = _correlation_multiplier_and_audit(rows)
    ep = win_prob(leg_probs, n_legs) * cmult
    pwr, flx = power_flex_payout_for_n(n_legs)
    adj_power = calc_adjusted_payout(pwr, rows)
    adj_flex = calc_adjusted_payout(flx, rows)

    tiers = [str(r.get("tier", "")).upper() for r in rows]
    defs = [_norm_prop_label(r.get("def_tier", r.get("opp_def_tier", ""))) for r in rows]
    if all(t in {"A", "B"} for t in tiers) and all(d in {"avg", "weak"} for d in defs):
        expected_win_rate = 0.78
    else:
        expected_win_rate = 0.68

    return {
        "key": frozenset(
            (str(r.get("player", "")) + "|" + str(r.get("prop_type", ""))).strip() for r in rows
        ),
        "rows": rows,
        "avg_hit_rate": float(np.mean(hrs)) if hrs else 0.0,
        "avg_rank_score": float(np.mean(rss)) if rss else 0.0,
        "est_win_prob": ep,
        "power_payout": adj_power,
        "flex_payout": adj_flex,
        "base_power_payout": pwr,
        "payout_multiplier": round(adj_power / pwr, 4) if pwr else 1.0,
        "ev_power": round(ep * adj_power, 4),
        "kelly_units": round(kelly_fraction(ep, adj_power, fraction=0.25), 2),
        "n_legs": n_legs,
        "expected_win_rate": expected_win_rate,
        "ticket_type": ticket_type,
        "sport": "MIX",
        "leg_prob_sources": ",".join(sorted(set(prob_srcs))),
        "correlation_multiplier": cmult,
        "correlation_audit": caudit,
    }


def build_cross_pipeline_ticket_bundle(
    sport_pools: list[tuple[str, pd.DataFrame | None]],
    max_legs: int = CROSS_PIPELINE_MAX_LEGS,
    ticket_sort_mode: str = "rank",
    player_ticket_counts: dict[str, int] | None = None,
) -> list[tuple[str, dict]]:
    """
    Up to three tickets (each ≤ max_legs, default 6):
      Standard-only, Goblin-only, Std+Gob mix — best eligible prop per pipeline.
    Returns [(excel_sheet_name, ticket_dict), ...].
    """
    specs = [
        ("standard", "Cross-Pipeline Standard", "Cross Std All Pipe"),
        ("goblin", "Cross-Pipeline Goblin", "Cross Gob All Pipe"),
        ("mix", "Cross-Pipeline Mix", "Cross Mix All Pipe"),
    ]
    out: list[tuple[str, dict]] = []
    for mode, ttype, sheet in specs:
        legs = _collect_cross_pipeline_rows(sport_pools, mode, max_legs, ticket_sort_mode=ticket_sort_mode)
        tix = _finalize_cross_pipeline_ticket(legs, ttype)
        if tix is not None:
            if not _ticket_cap_can_add(tix["rows"], player_ticket_counts):
                continue
            _ticket_cap_register(tix["rows"], player_ticket_counts)
            out.append((sheet[:31], tix))
    return out


# ── Write slate sheet ──────────────────────────────────────────────────────────
SLATE_COLS = [
    "sport",
    "tier",
    "rank_score",
    "player",
    "team",
    "opp",
    "team_seed",
    "team_region",
    "team_ap_rank",
    "opp_seed",
    "opp_region",
    "opp_ap_rank",
    "ncaa_rank",
    "prop_type",
    "pick_type",
    "line",
    "line_underdog",
    "line_draftkings",
    "direction",
    "edge",
    "projection",
    "hit_rate",
    "l5_avg",
    "season_avg",
    "l5_over",
    "l5_under",
    "l5_side_hits",
    "l5_consistency",
    "l10_over",
    "l10_under",
    "def_tier",
    "min_tier",
    "shot_role",
    "usage_role",
    "h2h_avg",
    "h2h_over_rate",
    "h2h_games",
    "h2h_last",
    "b2b_flag",
    "cv_pct",
    "opp_vs_avg_pct",
    "game_time",
]
SLATE_WIDTHS = [6, 5, 10, 20, 6, 6, 7, 10, 8, 7, 10, 8, 10, 18, 10, 6, 11, 11, 8, 7, 10, 10, 8, 10, 7, 7, 9, 10, 8, 8, 10, 9, 10, 10, 8, 9, 8, 10, 7, 8, 10, 16]
SLATE_HDRS = [
    "Sport",
    "Tier",
    "Rank Score",
    "Player",
    "Team",
    "Opp",
    "Team Seed",
    "Team Region",
    "Team AP",
    "Opp Seed",
    "Opp Region",
    "Opp AP",
    "NCAA Rank",
    "Prop",
    "Pick Type",
    "Line",
    "Line UD",
    "Line DK",
    "Dir",
    "Edge",
    "Proj",
    "Hit Rate",
    "L5 Avg",
    "Szn Avg",
    "L5 Over",
    "L5 Under",
    "L5 Side Hits",
    "L5 Match %",
    "L10 Over",
    "L10 Under",
    "Def Tier",
    "Min Tier",
    "Shot Role",
    "Usage Role",
    "H2H Avg",
    "H2H Over%",
    "H2H GP",
    "H2H Last",
    "B2B",
    "CV%",
    "Opp vs Avg%",
    "Game Time",
]

_SLATE_HDR_BY_COL = dict(zip(SLATE_COLS, SLATE_HDRS))
_SLATE_WIDTH_BY_COL = dict(zip(SLATE_COLS, SLATE_WIDTHS))

# Full Slate only: scan order, pace + STRONG/LEAN/RISK beside Def Tier (per-sport sheets keep SLATE_COLS).
FULL_SLATE_EXTRA_HDRS = {
    "pace_tier": "Pace Tier",
    "bet_strong": "STRONG",
    "bet_lean": "LEAN",
    "bet_risk": "RISK",
    "game_script_mult": "Game Script",
    "game_script_note": "Script Note",
    "l5_side_hits": "L5 Side Hits",
    "l5_consistency": "L5 Match %",
    "line_underdog": "Line (UD)",
    "line_draftkings": "Line (DK)",
}
FULL_SLATE_EXTRA_WIDTHS = {
    "pace_tier": 10,
    "bet_strong": 9,
    "bet_lean": 7,
    "bet_risk": 9,
    "game_script_mult": 12,
    "game_script_note": 42,
    "l5_side_hits": 9,
    "l5_consistency": 10,
    "line_underdog": 11,
    "line_draftkings": 11,
}

FULL_SLATE_COLS = [
    "sport",
    "tier",
    "rank_score",
    "player",
    "team",
    "opp",
    "game_time",
    "team_seed",
    "team_region",
    "team_ap_rank",
    "opp_seed",
    "opp_region",
    "opp_ap_rank",
    "ncaa_rank",
    "prop_type",
    "pick_type",
    "line",
    "line_underdog",
    "line_draftkings",
    "direction",
    "edge",
    "projection",
    "hit_rate",
    "l5_avg",
    "season_avg",
    "l5_over",
    "l5_under",
    "l5_side_hits",
    "l5_consistency",
    "l10_over",
    "l10_under",
    "def_tier",
    "pace_tier",
    "bet_strong",
    "bet_lean",
    "bet_risk",
    "min_tier",
    "shot_role",
    "usage_role",
    "h2h_avg",
    "h2h_over_rate",
    "h2h_games",
    "h2h_last",
    "b2b_flag",
    "cv_pct",
    "opp_vs_avg_pct",
    "game_script_mult",
    "game_script_note",
]


def _slate_hdr_for(col: str) -> str:
    if col in FULL_SLATE_EXTRA_HDRS:
        return FULL_SLATE_EXTRA_HDRS[col]
    return _SLATE_HDR_BY_COL.get(col, col.replace("_", " ").title())


def _slate_width_for(col: str) -> int:
    if col in FULL_SLATE_EXTRA_WIDTHS:
        return FULL_SLATE_EXTRA_WIDTHS[col]
    return _SLATE_WIDTH_BY_COL.get(col, 11)


def full_slate_column_order(df: pd.DataFrame) -> List[str]:
    """Preferred Full Slate order first, then legacy slate columns, then any extras."""
    seen: set[str] = set()
    out: List[str] = []
    for c in FULL_SLATE_COLS:
        if c in df.columns and c not in seen:
            out.append(c)
            seen.add(c)
    for c in SLATE_COLS:
        if c in df.columns and c not in seen:
            out.append(c)
            seen.add(c)
    for c in df.columns:
        if c not in seen:
            out.append(c)
            seen.add(c)
    return out


def write_slate_sheet(
    wb,
    df,
    sheet_name,
    bg_hdr,
    sport_label="",
    *,
    column_order: Optional[List[str]] = None,
    full_slate_visual: bool = False,
):
    ws = wb.create_sheet(sheet_name)
    if column_order is not None:
        cols = [c for c in column_order if c in df.columns]
    else:
        cols = [c for c in SLATE_COLS if c in df.columns]
    hdrs = [_slate_hdr_for(c) for c in cols]
    widths = [_slate_width_for(c) for c in cols]
    sw(ws, widths)
    hdr_h = 28 if full_slate_visual else 22
    hdr_sz = 10 if full_slate_visual else 9
    ws.row_dimensions[1].height = hdr_h
    for ci, h in enumerate(hdrs, 1):
        hc(ws, 1, ci, h, bg=bg_hdr, sz=hdr_sz)
    ws.freeze_panes = "A2"

    for ri, row in enumerate(df[cols].itertuples(index=False), 2):
        if full_slate_visual:
            ws.row_dimensions[ri].height = 19
        bg = C["alt"] if ri % 2 == 0 else C["white"]
        sp = getattr(row, "sport", "")
        spu = str(sp).upper() if sp else ""
        if spu == "NBA":
            bg_row = C["nba"] if ri % 2 == 0 else C["white"]
        elif spu == "CBB":
            bg_row = C["cbb"] if ri % 2 == 0 else C["white"]
        elif spu == "NHL":
            bg_row = C["nhl"] if ri % 2 == 0 else C["white"]
        elif spu == "SOCCER":
            bg_row = C["soccer"] if ri % 2 == 0 else C["white"]
        else:
            bg_row = bg

        for ci, col in enumerate(cols, 1):
            val = getattr(row, col, "")
            if val is None or (isinstance(val, float) and np.isnan(val)):
                val = ""
            if col == "tier":
                dc(ws, ri, ci, val, bg=tier_bg(val), bold=True, align="center")
            elif col == "pick_type":
                dc(ws, ri, ci, val, bg=pt_bg(val), align="center")
            elif col == "hit_rate":
                pct_cell(ws, ri, ci, val if val != "" else np.nan)
                continue
            elif col == "rank_score":
                dc(ws, ri, ci, round(val, 2) if val != "" else "", bg=bg_row, bold=True, fmt="0.00")
            elif col == "direction":
                dbg = C["over"] if str(val).upper() == "OVER" else C["under"]
                dc(ws, ri, ci, val, bg=dbg, bold=True)
            elif col == "sport":
                vu = str(val).upper() if val else ""
                if vu == "NBA":
                    sbg = C["hdr_nba"]
                elif vu == "CBB":
                    sbg = C["hdr_cbb"]
                elif vu == "NHL":
                    sbg = C["hdr_nhl"]
                elif vu == "SOCCER":
                    sbg = C["hdr_soccer"]
                else:
                    sbg = C["hdr"]
                dc(ws, ri, ci, val, bg=sbg, bold=True, fc="FFFFFF")
            elif col == "player":
                dc(ws, ri, ci, val, bg=bg_row, align="left", bold=True)
            elif col == "def_tier" and full_slate_visual:
                dc(ws, ri, ci, val, bg=bg_row, align="center", bold=True)
            elif col == "pace_tier" and full_slate_visual:
                dc(ws, ri, ci, val, bg=bg_row, align="center", bold=(val != ""))
            elif col == "bet_strong" and str(val).upper() == "Y":
                dc(ws, ri, ci, "Y", bg=C["hit"], bold=True, fc="FFFFFF", align="center")
            elif col == "bet_lean" and str(val).upper() == "Y":
                dc(ws, ri, ci, "Y", bg=C["gold"], bold=True, fc="000000", align="center")
            elif col == "bet_risk":
                vs = str(val).upper()
                if vs == "LOW":
                    dc(ws, ri, ci, val, bg=C["hit"], bold=True, fc="FFFFFF", align="center")
                elif vs == "MED":
                    dc(ws, ri, ci, val, bg=C["push"], bold=True, fc="000000", align="center")
                elif vs == "HIGH":
                    dc(ws, ri, ci, val, bg=C["miss"], bold=True, fc="FFFFFF", align="center")
                else:
                    dc(ws, ri, ci, val, bg=bg_row, align="center")
            elif col == "l5_side_hits" and val != "":
                try:
                    dc(ws, ri, ci, int(round(float(val))), bg=bg_row, align="center", fmt="0")
                except (TypeError, ValueError):
                    dc(ws, ri, ci, val, bg=bg_row, align="center")
            elif col == "l5_consistency":
                pct_cell(ws, ri, ci, val if val != "" else np.nan)
                continue
            elif col in ("h2h_over_rate", "opp_vs_avg_pct"):
                cell = dc(ws, ri, ci, val if val != "" else "", bg=bg_row, align="center")
                if val != "":
                    try:
                        cell.number_format = "0.0%"
                    except Exception:
                        pass
            elif col == "cv_pct":
                cell = dc(ws, ri, ci, val if val != "" else "", bg=bg_row, align="center")
                if val != "":
                    try:
                        cell.number_format = "0.0"
                    except Exception:
                        pass
            elif col == "b2b_flag":
                b2b_str = "YES" if str(val).lower() in ("true", "1", "yes") else ("NO" if val != "" else "")
                b2b_bg = C["miss"] if b2b_str == "YES" else bg_row
                dc(ws, ri, ci, b2b_str, bg=b2b_bg, bold=(b2b_str == "YES"), align="center",
                   fc="FFFFFF" if b2b_str == "YES" else "000000")
                continue
            elif col == "game_time":
                try:
                    if val and val != "":
                        dt = pd.to_datetime(val)
                        dc(ws, ri, ci, dt.strftime("%m/%d %I:%M%p"), bg=bg_row, align="center")
                    else:
                        dc(ws, ri, ci, "", bg=bg_row)
                except Exception:
                    dc(ws, ri, ci, str(val)[:16], bg=bg_row)
                continue
            elif col == "edge" and full_slate_visual and val != "":
                try:
                    ev = float(val)
                    dc(ws, ri, ci, round(ev, 2), bg=bg_row, align="center", bold=True, fmt="0.00")
                except Exception:
                    dc(ws, ri, ci, val, bg=bg_row, align="center")
            elif col == "game_script_mult" and full_slate_visual:
                fv = None
                try:
                    if val != "" and val is not None:
                        fv = float(val)
                except (TypeError, ValueError):
                    fv = None
                fill_gs = bg_row
                fc_gs = "000000"
                if fv is not None:
                    if fv >= 1.03:
                        fill_gs = C["hit"]
                        fc_gs = "FFFFFF"
                    elif fv < 0.90:
                        fill_gs = C["miss"]
                        fc_gs = "FFFFFF"
                    elif 0.90 <= fv <= 0.96:
                        fill_gs = C["push"]
                        fc_gs = "000000"
                dc(
                    ws,
                    ri,
                    ci,
                    round(fv, 3) if fv is not None else "",
                    bg=fill_gs,
                    align="center",
                    bold=(fv is not None),
                    fc=fc_gs,
                    fmt="0.000" if fv is not None else None,
                )
            elif col == "game_script_note" and full_slate_visual:
                dc(ws, ri, ci, val, bg=bg_row, align="left")
            else:
                dc(ws, ri, ci, val, bg=bg_row, align="center")

    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}1"


# ── Write ticket sheet ─────────────────────────────────────────────────────────
TICKET_COLS = [
    "#",
    "player",
    "team",
    "opp",
    "prop_type",
    "pick_type",
    "line",
    "direction",
    "edge",
    "hit_rate",
    "l5_avg",
    "season_avg",
    "l5_over",
    "l5_under",
    "l10_over",
    "l10_under",
    "rank_score",
    "ml_prob",
    "def_tier",
    "h2h_avg",
    "h2h_over_rate",
    "h2h_games",
    "b2b_flag",
    "cv_pct",
    "opp_vs_avg_pct",
    "standard_line",
    "delta_pct",
    "sport",
]
TICKET_HDRS = [
    "#",
    "Player",
    "Team",
    "Opp",
    "Prop",
    "Pick Type",
    "Line",
    "Dir",
    "Edge",
    "Hit Rate",
    "L5 Avg",
    "Szn Avg",
    "L5 Over",
    "L5 Under",
    "L10 Over",
    "L10 Under",
    "Rank Score",
    "ML Prob",
    "Def Tier",
    "H2H Avg",
    "H2H Over%",
    "H2H GP",
    "B2B",
    "CV%",
    "Opp vs Avg%",
    "Std Line",
    "Delta %",
    "Sport",
]
TICKET_W = [4, 20, 6, 6, 18, 10, 6, 6, 7, 9, 8, 9, 7, 8, 8, 9, 11, 8, 10, 8, 9, 7, 7, 8, 10, 8, 8, 6]


def write_ticket_sheet(wb, tickets, sheet_name, bg_hdr, label=""):
    if not tickets:
        return
    ws = wb.create_sheet(sheet_name)
    sw(ws, TICKET_W)
    ws.freeze_panes = "A2"

    ri = 1
    for ti, ticket in enumerate(tickets, 1):
        n = ticket["n_legs"]
        pout = ticket["power_payout"]
        fout = ticket["flex_payout"]
        cost = round(100 / pout, 0) if pout else 0
        avg_hr = ticket["avg_hit_rate"]
        ep = ticket["est_win_prob"]
        avg_rs = ticket["avg_rank_score"]

        base_pout  = ticket.get("base_power_payout", pout)
        pay_mult   = ticket.get("payout_multiplier", 1.0)
        ev_pow     = ticket.get("ev_power", round(ep * pout, 4))
        exp_wr     = ticket.get("expected_win_rate", None)
        ttype      = ticket.get("ticket_type", "")
        em_curve = ticket.get("est_multiplier")
        fm_curve = ticket.get("flat_multiplier")
        curve_lbl = ""
        if em_curve is not None and fm_curve is not None:
            try:
                curve_lbl = f"  ·  Curve est: {float(em_curve):.2f}x vs flat PP base {float(fm_curve):.2f}x"
            except (TypeError, ValueError):
                curve_lbl = ""
        _pl_parts: list[str] = []
        if abs(pay_mult - 1.0) > 0.001:
            _pl_parts.append(f"Payout Mult: {pay_mult:.2f}x (base {base_pout}x → adj {pout}x)")
        if curve_lbl:
            _pl_parts.append(curve_lbl.strip())
        mult_label = ("  ·  " + "  ·  ".join(_pl_parts)) if _pl_parts else ""
        wr_label = f"  ·  Expected Win Rate: {float(exp_wr):.0%}" if exp_wr is not None else ""
        banner = (
            f"  Ticket #{ti}  ·  {n}-Leg {label} {ttype}  ·  "
            f"Power: {pout}x (${cost:.0f} to win $100)  ·  Flex: {fout}x  ·  "
            f"Avg Hit Rate: {avg_hr:.0%}  ·  Est Win Prob: {ep:.0%}  ·  EV: {ev_pow:.2f}  ·  "
            f"Avg Rank Score: {avg_rs:.2f}{wr_label}{mult_label}"
        )
        ws.merge_cells(start_row=ri, start_column=1, end_row=ri, end_column=len(TICKET_COLS))
        hc(ws, ri, 1, banner, bg=bg_hdr, sz=9, align="left")
        ws.row_dimensions[ri].height = 16
        ri += 1

        for ci, h in enumerate(TICKET_HDRS, 1):
            hc(ws, ri, ci, h, bg=C["hdr"], sz=8)
        ws.row_dimensions[ri].height = 14
        ri += 1

        for leg_i, row in enumerate(ticket["rows"], 1):
            bg = C["alt"] if leg_i % 2 == 0 else C["white"]
            sp = row.get("sport", "")
            if sp == "NBA":
                bg = C["nba"]
            elif sp == "CBB":
                bg = C["cbb"]
            elif sp == "MLB":
                bg = C["mlb"]
            elif sp == "NHL":
                bg = C["nhl"]
            elif sp in ("SOCCER", "SOC"):
                bg = C["soccer"]
            elif sp == "WCBB":
                bg = C["wcbb"]
            elif sp == "NBA1Q":
                bg = C["nba1q"]
            elif sp == "NBA1H":
                bg = C["nba1h"]

            def gv(field):
                return row.get(field, "")

            def _fmt_team_with_meta(team_val, seed_val, region_val, ap_val):
                t = str(team_val or "").strip()
                if not t:
                    return ""
                tags = []
                ap_missing = ap_val in ("", None) or (isinstance(ap_val, float) and np.isnan(ap_val))
                seed_missing = seed_val in ("", None) or (isinstance(seed_val, float) and np.isnan(seed_val))
                region_missing = region_val in ("", None) or (isinstance(region_val, float) and np.isnan(region_val))
                if not ap_missing:
                    tags.append(f"AP#{int(ap_val)}")
                if not seed_missing:
                    try:
                        s = int(seed_val)
                    except Exception:
                        s = seed_val
                    tags.append(f"S{s}")
                if not region_missing:
                    tags.append(str(region_val))
                return f"{t} ({' | '.join(tags)})" if tags else t

            dc(ws, ri, 1, leg_i, bg=bg, bold=True, align="center")
            dc(ws, ri, 2, gv("player"), bg=bg, align="left", bold=True)
            dc(ws, ri, 3, _fmt_team_with_meta(gv("team"), gv("team_seed"), gv("team_region"), gv("team_ap_rank")), bg=bg)
            dc(ws, ri, 4, _fmt_team_with_meta(gv("opp"), gv("opp_seed"), gv("opp_region"), gv("opp_ap_rank")), bg=bg)
            dc(ws, ri, 5, gv("prop_type"), bg=bg, align="left")
            ptv = gv("pick_type")
            dc(ws, ri, 6, ptv, bg=pt_bg(str(ptv)), align="center")
            dc(ws, ri, 7, gv("line"), bg=bg)
            dirv = str(gv("direction")).upper()
            dc(ws, ri, 8, dirv, bg=C["over"] if dirv == "OVER" else C["under"], bold=True)
            dc(ws, ri, 9, gv("edge"), bg=bg)
            pct_cell(ws, ri, 10, gv("hit_rate") if gv("hit_rate") != "" else np.nan)
            dc(ws, ri, 11, gv("l5_avg"), bg=bg)
            dc(ws, ri, 12, gv("season_avg"), bg=bg)
            dc(ws, ri, 13, gv("l5_over"), bg=bg)
            dc(ws, ri, 14, gv("l5_under"), bg=bg)
            dc(ws, ri, 15, gv("l10_over"), bg=bg)
            dc(ws, ri, 16, gv("l10_under"), bg=bg)
            rs = gv("rank_score")
            try:
                rs_out = round(float(rs), 2) if rs != "" and rs is not None else ""
            except Exception:
                rs_out = ""
            dc(ws, ri, 17, rs_out, bg=bg, bold=True)
            # ML prob (if present); keep formatting consistent with other probability fields
            mp = gv("ml_prob")
            try:
                mp_out = round(float(mp), 4) if mp != "" and mp is not None else ""
            except Exception:
                mp_out = ""
            dc(ws, ri, 18, mp_out, bg=bg, align="center", bold=(mp_out != ""), fmt="0.0000" if mp_out != "" else None)
            dc(ws, ri, 19, gv("def_tier"), bg=bg)
            # H2H Avg
            dc(ws, ri, 20, gv("h2h_avg"), bg=bg, align="center")
            # H2H Over%
            h2h_or = gv("h2h_over_rate")
            cell_h2h = dc(ws, ri, 21, h2h_or if h2h_or != "" else "", bg=bg, align="center")
            if h2h_or != "":
                try:
                    cell_h2h.number_format = "0.0%"
                except Exception:
                    pass
            # H2H GP
            dc(ws, ri, 22, gv("h2h_games"), bg=bg, align="center")
            # B2B
            b2b_raw = gv("b2b_flag")
            b2b_str = "YES" if str(b2b_raw).lower() in ("true", "1", "yes") else ("NO" if b2b_raw != "" else "")
            b2b_bg = C["miss"] if b2b_str == "YES" else bg
            dc(ws, ri, 23, b2b_str, bg=b2b_bg, bold=(b2b_str == "YES"), align="center",
               fc="FFFFFF" if b2b_str == "YES" else "000000")
            # CV%
            cv_val = gv("cv_pct")
            cell_cv = dc(ws, ri, 24, cv_val if cv_val != "" else "", bg=bg, align="center")
            if cv_val != "":
                try:
                    cell_cv.number_format = "0.0"
                except Exception:
                    pass
            # Opp vs Avg%
            opp_val = gv("opp_vs_avg_pct")
            cell_opp = dc(ws, ri, 25, opp_val if opp_val != "" else "", bg=bg, align="center")
            if opp_val != "":
                try:
                    cell_opp.number_format = "0.0%"
                except Exception:
                    pass
            std_v = gv("standard_line")
            try:
                std_out = round(float(std_v), 2) if std_v != "" and std_v is not None else ""
            except (TypeError, ValueError):
                std_out = ""
            dc(ws, ri, 26, std_out, bg=bg, align="center")
            _dpx = gd_leg_delta_pct(gv("line"), gv("standard_line"))
            dp_out = round(float(_dpx), 4) if _dpx is not None else ""
            dc(ws, ri, 27, dp_out, bg=bg, align="center", fmt="0.0000" if dp_out != "" else None)
            # Sport
            sv = gv("sport")
            sbg = C["hdr_nba"] if sv == "NBA" else (C["hdr_cbb"] if sv == "CBB" else C["hdr"])
            dc(ws, ri, 28, sv, bg=sbg, bold=True, fc="FFFFFF")
            ws.row_dimensions[ri].height = 14
            ri += 1

        ws.row_dimensions[ri].height = 6
        ri += 1


# ── Write SUMMARY sheet ───────────────────────────────────────────────────────
def write_summary(wb, nba, cbb, combined, all_ticket_groups, date_str, thresholds,
                  nhl=None, soccer=None, wcbb=None, mlb=None, nba1q=None, nba1h=None):
    ws = wb.create_sheet("SUMMARY", 0)
    sw(ws, [28, 14, 10, 10, 10, 10, 10, 12, 18])

    ws.merge_cells("A1:I1")
    c = ws["A1"]
    c.value = f"COMBINED NBA + CBB SLATE  |  {date_str}  |  Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    c.font = Font(bold=True, name="Arial", size=13, color="FFFFFF")
    c.fill = PatternFill("solid", start_color=C["hdr"])
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30

    ws.merge_cells("A2:I2")
    c2 = ws["A2"]
    c2.value = (
        f"Filters: Tier {thresholds.get('tiers','ALL')} | "
        f"Min Hit Rate: {thresholds.get('min_hit_rate',0):.0%} | "
        f"Min Edge: {thresholds.get('min_edge',0)} | "
        f"Min Rank Score: {thresholds.get('min_rank','None')} | "
        f"Pick Types: {thresholds.get('pick_types','ALL')} | "
        f"Context Filter: {thresholds.get('context_filter', False)} "
        f"(score>={thresholds.get('context_min_score', 2)}, "
        f"L5 sample>={thresholds.get('context_min_l5_sample', 5)})"
    )
    c2.font = Font(bold=False, name="Arial", size=9, color="000000")
    c2.fill = PatternFill("solid", start_color=C["gold"])
    c2.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 16

    row = 4

    def sec(r, label, bg):
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=9)
        hc(ws, r, 1, label, bg=bg, sz=10, align="left")
        ws.row_dimensions[r].height = 20
        return r + 1

    def stat_row(r, label, total, elig, bg=None):
        bg = bg or (C["alt"] if r % 2 == 0 else C["white"])
        dc(ws, r, 1, label, bg=bg, align="left", bold=True)
        dc(ws, r, 2, total, bg=bg)
        dc(ws, r, 3, elig, bg=bg)
        for ci in range(4, 10):
            dc(ws, r, ci, "", bg=bg)
        return r + 1

    row = sec(row, "📊 SLATE OVERVIEW", C["hdr_sum"])
    for ci, h in enumerate(["Category", "Total Props", "Eligible", "", "", "", "", "", ""], 1):
        hc(ws, row, ci, h, bg=C["hdr"], sz=8)
    ws.row_dimensions[row].height = 14
    row += 1

    elig_nba = len(nba[nba.get("tier", "").isin(["A", "B"])]) if "tier" in nba.columns else 0
    elig_cbb = len(cbb[cbb.get("tier", "").isin(["A", "B"])]) if "tier" in cbb.columns else 0
    elig_all = len(combined[combined.get("tier", "").isin(["A", "B"])]) if "tier" in combined.columns else 0
    row = stat_row(row, "NBA Props", len(nba), elig_nba, C["nba"])
    row = stat_row(row, "CBB Props", len(cbb), elig_cbb, C["cbb"])
    if nhl is not None and len(nhl) > 0:
        elig_nhl = len(nhl[nhl.get("tier", "").isin(["A", "B"])]) if "tier" in nhl.columns else 0
        row = stat_row(row, "NHL Props", len(nhl), elig_nhl, C["nhl"])
    if soccer is not None and len(soccer) > 0:
        elig_soc = len(soccer[soccer.get("tier", "").isin(["A", "B"])]) if "tier" in soccer.columns else 0
        row = stat_row(row, "Soccer Props", len(soccer), elig_soc, C["soccer"])
    if wcbb is not None and len(wcbb) > 0:
        elig_wcbb = len(wcbb[wcbb["tier"].isin(["A", "B"])]) if "tier" in wcbb.columns else 0
        row = stat_row(row, "WCBB Props", len(wcbb), elig_wcbb, C["wcbb"])
    if mlb is not None and len(mlb) > 0:
        elig_mlb = len(mlb[mlb["tier"].isin(["A", "B"])]) if "tier" in mlb.columns else 0
        row = stat_row(row, "MLB Props", len(mlb), elig_mlb, C["mlb"])
    if nba1q is not None and len(nba1q) > 0:
        elig_nba1q = len(nba1q[nba1q["tier"].isin(["A", "B"])]) if "tier" in nba1q.columns else 0
        row = stat_row(row, "NBA1Q Props", len(nba1q), elig_nba1q, C["nba1q"])
    if nba1h is not None and len(nba1h) > 0:
        elig_nba1h = len(nba1h[nba1h["tier"].isin(["A", "B"])]) if "tier" in nba1h.columns else 0
        row = stat_row(row, "NBA1H Props", len(nba1h), elig_nba1h, C["nba1h"])
    row = stat_row(row, "Combined Slate", len(combined), elig_all)
    row += 1

    row = sec(row, "🎟️ TICKET SUMMARY", C["hdr_mix"])
    for ci, h in enumerate(
        ["Sheet", "Legs", "Type", "# Tickets", "Avg Hit Rate", "Avg Win Prob", "Avg Rank Score", "Adj Power Payout", "Avg EV", "Payout Mult", "Players"],
        1,
    ):
        hc(ws, row, ci, h, bg=C["hdr"], sz=8)
    ws.row_dimensions[row].height = 14
    row += 1

    sw(ws, [28, 14, 10, 10, 10, 10, 10, 12, 10, 11, 18])

    for group_name, tickets, bg_row in all_ticket_groups:
        if not tickets:
            continue
        avg_hr  = np.mean([t["avg_hit_rate"] for t in tickets])
        avg_wp  = np.mean([t["est_win_prob"] for t in tickets])
        avg_rs  = np.mean([t["avg_rank_score"] for t in tickets])
        avg_ev  = np.mean([t.get("ev_power", t["est_win_prob"] * t["power_payout"]) for t in tickets])
        avg_pm  = np.mean([t.get("payout_multiplier", 1.0) for t in tickets])
        n    = tickets[0]["n_legs"]
        pout = tickets[0]["power_payout"]
        bg   = bg_row if bg_row else (C["alt"] if row % 2 == 0 else C["white"])
        # colour the EV cell: green ≥ 1.2, amber 1.0–1.2, red < 1.0
        ev_bg = C["hit"] if avg_ev >= 1.2 else (C["push"] if avg_ev >= 1.0 else C["miss"])
        dc(ws, row, 1, group_name, bg=bg, align="left", bold=True)
        dc(ws, row, 2, n, bg=bg)
        lbl = group_name.split(" ")[0] if group_name else ""
        dc(ws, row, 3, lbl, bg=bg)
        dc(ws, row, 4, len(tickets), bg=bg)
        pct_cell(ws, row, 5, avg_hr)
        pct_cell(ws, row, 6, avg_wp)
        dc(ws, row, 7, round(avg_rs, 2), bg=bg)
        dc(ws, row, 8, f"{pout}x", bg=bg)
        dc(ws, row, 9, round(avg_ev, 2), bg=ev_bg, bold=True, fc="FFFFFF")
        dc(ws, row, 10, f"{avg_pm:.2f}x", bg=bg)
        sample = " | ".join(f"{r.get('player','')}" for r in tickets[0]["rows"][:3]) + ("..." if n > 3 else "")
        dc(ws, row, 11, sample, bg=bg, align="left", sz=8)
        row += 1


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--nba",
        default="",
        help=f"NBA step8_all_direction_clean.xlsx (default: {DEFAULT_NBA_PATH})",
    )
    ap.add_argument(
        "--cbb",
        default="",
        help=f"CBB step6_ranked_cbb.xlsx (default: {DEFAULT_CBB_PATH})",
    )
    ap.add_argument(
        "--nhl",
        default=DEFAULT_NHL_PATH,
        help=f"NHL step8 (default: {DEFAULT_NHL_PATH})",
    )
    ap.add_argument(
        "--soccer",
        default="",
        help=f"Soccer step8 (default: {DEFAULT_SOCCER_PATH})",
    )
    ap.add_argument("--wcbb", default="", help="WCBB step8 direction clean xlsx (optional)")
    ap.add_argument("--mlb", default="", help="MLB step8 direction clean xlsx (optional)")
    ap.add_argument("--nba1q", default="", help="NBA 1st Quarter step8 direction clean xlsx (optional)")
    ap.add_argument("--nba1h", default="", help="NBA 1st Half step8 direction clean xlsx (optional)")
    ap.add_argument("--output", default="")
    ap.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="Slate date YYYY-MM-DD, or 'today' / 'now'",
    )
    ap.add_argument("--tiers", default="A,B,C", help="Comma-separated tiers e.g. A,B")
    ap.add_argument(
        "--high-conviction",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Strict ticket pool (default: on): min pool hit rate >= 0.70; if tiers are default A,B,C, use A,B; "
            "cap FINAL slips at 4 legs; higher per-leg floors. --no-high-conviction restores legacy behavior."
        ),
    )
    ap.add_argument(
        "--prioritize-ticket-hit",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Bias generation toward modeled payout probability (not a real-world 100%% guarantee): "
            "higher per-leg hit_rate floors, min modeled P(all legs hit) for power-style slips, "
            "min modeled P(flex cash) for Flex 3+ sheets; FINAL builders use the power threshold. "
            "May yield fewer or empty slips — loosen with --no-prioritize-ticket-hit (default)."
        ),
    )
    ap.add_argument(
        "--ticket-candidate-sort",
        choices=("rank", "ml", "blend"),
        default="blend",
        dest="ticket_candidate_sort",
        help=(
            "Order slate rows when choosing ticket legs. rank=rank_score only; ml=ml_prob first (NaN last); "
            "blend=avg(ml_prob, sigmoid(rank_score)) with missing ml using sigmoid(rank) only. "
            "Default blend uses your step8 ML Prob column when present (same signal as _resolve_leg_prob priority)."
        ),
    )
    ap.add_argument(
        "--ticket-gen-starts",
        type=int,
        default=6,
        dest="ticket_gen_starts",
        help=(
            "Structured tickets only: try the first K eligible rows as the first leg (after sort) and keep the slip "
            "with highest modeled ticket payout (flex cash for Flex 3+, else P(all hit)). Use 1 for legacy single-pass."
        ),
    )
    ap.add_argument(
        "--min-leg-hit-rate",
        type=float,
        default=None,
        dest="min_leg_hit_rate",
        help="Every ticket leg must have hit_rate >= this (0-1). When strict mode is on, defaults to 0.70 if omitted.",
    )
    ap.add_argument(
        "--max-ticket-legs",
        type=int,
        default=6,
        dest="max_ticket_legs",
        help="FINAL / long-slip builders: max leg count (2-6). In strict mode (default), capped at 4 unless already lower.",
    )
    ap.add_argument("--min-hit-rate", type=float, default=0.55, dest="min_hit_rate")
    ap.add_argument("--min-edge", type=float, default=0.0, dest="min_edge")
    ap.add_argument("--min-rank", type=float, default=None, dest="min_rank")
    ap.add_argument(
        "--pick-types",
        default="Goblin,Standard",
        dest="pick_types",
        help="Comma-separated pick types for ticket eligibility (Demon excluded from tickets).",
    )
    ap.add_argument("--max-tickets", type=int, default=3, dest="max_tickets")
    ap.add_argument("--use-context-filter", action="store_true", dest="use_context_filter", default=True,
                    help="Apply NBA direction+defense+pace context confidence filter")
    ap.add_argument("--no-context-filter", action="store_false", dest="use_context_filter",
                    help="Disable NBA direction+defense+pace context confidence filter")
    ap.add_argument("--context-min-score", type=int, default=2, dest="context_min_score",
                    help="Minimum NBA context score for Standard picks")
    ap.add_argument("--context-min-l5-sample", type=int, default=5, dest="context_min_l5_sample",
                    help="Minimum (L5 over+under) sample size for NBA context filter")
    ap.add_argument(
        "--allow-cross-date-fallback",
        action="store_true",
        help="Allow non-target game dates when target date has zero rows (default: strict target-date only).",
    )
    ap.add_argument(
        "--underdog-csv",
        default="",
        help="Optional Underdog fetch CSV (PP-shaped). If omitted and outputs/<date>/underdog_props.csv exists, it is used.",
    )
    ap.add_argument(
        "--draftkings-csv",
        default="",
        help="Optional DraftKings sportsbook CSV with board_sport column. "
        "If omitted and outputs/<date>/draftkings_props_nba.csv exists, it is used.",
    )

    # Web outputs
    ap.add_argument(
        "--write-web",
        action="store_true",
        help="Write tickets_latest.json for web/Railway (graded HTML via build_ticket_eval.py)",
    )
    ap.add_argument(
        "--web-outdir",
        default=DEFAULT_WEB_OUTDIR,
        help="Folder to write tickets_latest.json (+ slate_latest.json)",
    )
    ap.add_argument(
        "--also-root",
        action="store_true",
        help="Also write tickets_latest.json in repo root (HTML only from build_ticket_eval.py)",
    )
    ap.add_argument(
        "--bankroll",
        type=float,
        default=0.0,
        help="Optional bankroll (USD). When > 0, tickets_latest.json legs include recommended_stake_usd (fractional Kelly, utils/kelly_staking).",
    )
    ap.add_argument(
        "--curve-stake-usd",
        type=float,
        default=1.0,
        dest="curve_stake_usd",
        help="Stake (USD) for est_payout / est_ev / flat_ev columns (Goblin-Demon curve); does not change Kelly stakes.",
    )

    args = ap.parse_args()

    ds = str(args.date).strip().lower()
    if ds in ("today", "now"):
        args.date = datetime.now().strftime("%Y-%m-%d")

    args.max_ticket_legs = max(2, min(6, int(args.max_ticket_legs)))
    args.ticket_gen_starts = max(1, min(24, int(args.ticket_gen_starts)))
    if args.high_conviction:
        args.min_hit_rate = max(float(args.min_hit_rate), 0.70)
        if str(args.tiers).strip() == "A,B,C":
            args.tiers = "A,B"
        args.max_ticket_legs = min(args.max_ticket_legs, 4)
        print(
            "[tickets] strict pool: min hit rate >= 0.70, tiers A,B when default A,B,C, "
            f"max FINAL legs={args.max_ticket_legs} (use --no-high-conviction for legacy)"
        )
    if args.prioritize_ticket_hit:
        args.min_hit_rate = max(float(args.min_hit_rate), 0.72)
        print(
            "[tickets] prioritize-ticket-hit: pool min hit rate >= 0.72, raised per-leg floors, "
            "modeled payout probability gates on structured + FINAL tickets"
        )

    effective_max_legs = int(args.max_ticket_legs)
    leg_sizes_runtime = ticket_leg_sizes_for_max(effective_max_legs)
    leg_min_override = None
    if args.min_leg_hit_rate is not None:
        leg_min_override = {n: float(args.min_leg_hit_rate) for n in TICKET_LEG_SIZES}
    leg_min_hit_by_n = effective_leg_min_hit_rates(
        bool(args.high_conviction),
        leg_min_override,
        prioritize_ticket_hit=bool(args.prioritize_ticket_hit),
    )

    structured_min_leg_hr = args.min_leg_hit_rate
    if args.high_conviction and structured_min_leg_hr is None:
        structured_min_leg_hr = 0.70

    if not str(args.nba).strip():
        args.nba = DEFAULT_NBA_PATH
    if not str(args.cbb).strip():
        args.cbb = DEFAULT_CBB_PATH
    if not str(args.nhl).strip():
        args.nhl = DEFAULT_NHL_PATH
    if not str(args.soccer).strip():
        args.soccer = DEFAULT_SOCCER_PATH

    if not args.output:
        args.output = f"combined_slate_tickets_{args.date}.xlsx"

    _repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    _auto_ud = os.path.join(_repo_root, "outputs", args.date, "underdog_props.csv")
    _auto_dk = os.path.join(_repo_root, "outputs", args.date, "draftkings_props_nba.csv")
    if not str(args.underdog_csv).strip() and os.path.isfile(_auto_ud):
        args.underdog_csv = _auto_ud
        print(f"  [alt-books] Using Underdog CSV: {_auto_ud}")
    if not str(args.draftkings_csv).strip() and os.path.isfile(_auto_dk):
        args.draftkings_csv = _auto_dk
        print(f"  [alt-books] Using DraftKings CSV: {_auto_dk}")

    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()]
    pick_types = [p.strip() for p in args.pick_types.split(",") if p.strip()]
    ticket_pick_types = [p for p in pick_types if p != "Demon"]
    thresholds = {
        "tiers": args.tiers,
        "min_hit_rate": args.min_hit_rate,
        "min_edge": args.min_edge,
        "min_rank": args.min_rank,
        # What actually feeds ticket builders (Demon never on tickets)
        "pick_types": ",".join(ticket_pick_types) if ticket_pick_types else "Goblin,Standard",
        "context_filter": args.use_context_filter,
        "context_min_score": args.context_min_score,
        "context_min_l5_sample": args.context_min_l5_sample,
        "high_conviction": bool(args.high_conviction),
        "prioritize_ticket_hit": bool(args.prioritize_ticket_hit),
        "ticket_candidate_sort": str(args.ticket_candidate_sort),
        "ticket_gen_starts": int(args.ticket_gen_starts),
        "min_leg_hit_rate": args.min_leg_hit_rate,
        "structured_min_leg_hit_rate": structured_min_leg_hr,
        "max_ticket_legs": effective_max_legs,
        "leg_min_hit_by_n": {str(k): round(v, 4) for k, v in leg_min_hit_by_n.items()},
    }

    print(f"Loading NBA slate from {args.nba}...")
    nba = load_nba(args.nba)
    nba = enforce_target_date(
        nba, "NBA", args.date, allow_cross_date_fallback=args.allow_cross_date_fallback
    )
    print(f"  {len(nba)} NBA props loaded")
    _load_audit_row("NBA", args.nba, nba)

    print(f"Loading CBB slate from {args.cbb}...")
    cbb = load_cbb(args.cbb)
    cbb = enforce_target_date(
        cbb, "CBB", args.date, allow_cross_date_fallback=args.allow_cross_date_fallback
    )
    print(f"  {len(cbb)} CBB props loaded")
    _load_audit_row("CBB", args.cbb, cbb)

    nhl = None
    if str(args.nhl).strip():
        try:
            nhl = load_nhl(args.nhl)
            if nhl is not None and not nhl.empty:
                nhl = enforce_target_date(
                    nhl, "NHL", args.date, allow_cross_date_fallback=args.allow_cross_date_fallback
                )
                nhl = attach_standard_refs(nhl)
                print(f"  {len(nhl)} NHL props loaded")
                _load_audit_row("NHL", args.nhl, nhl)
        except Exception as e:
            print(f"  WARNING: Could not load NHL file: {e}")
            nhl = None

    soccer = None
    if str(args.soccer).strip():
        try:
            soccer = load_soccer(args.soccer)
            soccer = enforce_target_date(
                soccer, "Soccer", args.date, allow_cross_date_fallback=args.allow_cross_date_fallback
            )
            soccer = attach_standard_refs(soccer)
            print(f"  {len(soccer)} Soccer props loaded")
            _load_audit_row("Soccer", args.soccer, soccer)
        except Exception as e:
            print(f"  WARNING: Could not load Soccer file: {e}")
            soccer = None
    else:
        print("  [Soccer] skipped (empty --soccer)")

    wcbb = None
    if args.wcbb:
        try:
            wcbb = load_wcbb(args.wcbb)
            wcbb = attach_standard_refs(wcbb)
            print(f"  {len(wcbb)} WCBB props loaded")
        except Exception as e:
            print(f"  WARNING: Could not load WCBB file: {e}")
            wcbb = None

    mlb = None
    if args.mlb:
        try:
            mlb = load_mlb(args.mlb)
            mlb = attach_standard_refs(mlb)
            print(f"  {len(mlb)} MLB props loaded")
        except Exception as e:
            print(f"  WARNING: Could not load MLB file: {e}")
            mlb = None

    nba1q = None
    if args.nba1q:
        try:
            nba1q = load_nba1q(args.nba1q)
            nba1q = attach_standard_refs(nba1q)
            print(f"  {len(nba1q)} NBA1Q props loaded")
        except Exception as e:
            print(f"  WARNING: Could not load NBA1Q file: {e}")
            nba1q = None

    nba1h = None
    if args.nba1h:
        try:
            nba1h = load_nba1h(args.nba1h)
            nba1h = attach_standard_refs(nba1h)
            print(f"  {len(nba1h)} NBA1H props loaded")
        except Exception as e:
            print(f"  WARNING: Could not load NBA1H file: {e}")
            nba1h = None

    # ✅ Attach Standard sibling refs AFTER normalized columns exist
    nba = attach_standard_refs(nba)
    cbb = attach_standard_refs(cbb)

    def drop_stale_rows(df, target_date, sport_label):
        if df is None or df.empty:
            return df
        if "game_date" not in df.columns:
            return df
        dated = df["game_date"].notna()
        gd_str = df["game_date"].astype(str).str[:10]
        td = str(target_date).strip()[:10]
        # Soccer boards often span several upcoming ET days; drop only rows clearly before the pipeline target.
        if sport_label == "Soccer":
            stale = dated & (gd_str < td)
        elif sport_label == "Combined" and "sport" in df.columns:
            # Same rule as strict date check: soccer allows future ET days; other sports must match target.
            su = df["sport"].astype(str).str.upper()
            is_soc = su == "SOCCER"
            stale = dated & ((gd_str < td) | (~is_soc & (gd_str != td)))
        else:
            stale = dated & (gd_str != td)
        n_stale = int(stale.sum())
        if n_stale > 0:
            print(f"  [date-filter] {sport_label}: dropped {n_stale} stale-dated rows (target slate {td})")
        return df[~stale].copy()

    nba = drop_stale_rows(nba, args.date, "NBA")
    cbb = drop_stale_rows(cbb, args.date, "CBB")
    nhl = drop_stale_rows(nhl, args.date, "NHL")
    soccer = drop_stale_rows(soccer, args.date, "Soccer")
    wcbb = drop_stale_rows(wcbb, args.date, "WCBB")
    mlb = drop_stale_rows(mlb, args.date, "MLB")
    nba1q = drop_stale_rows(nba1q, args.date, "NBA1Q")
    nba1h = drop_stale_rows(nba1h, args.date, "NBA1H")

    # Apply teammate-absence usage redistribution before ticket eligibility filtering.
    nba = apply_usage_redistribution(nba, "NBA", args.date, REPO_ROOT)
    cbb = apply_usage_redistribution(cbb, "CBB", args.date, REPO_ROOT)
    wcbb = apply_usage_redistribution(wcbb, "WCBB", args.date, REPO_ROOT) if wcbb is not None else wcbb
    nhl = apply_usage_redistribution(nhl, "NHL", args.date, REPO_ROOT) if nhl is not None else nhl
    soccer = apply_usage_redistribution(soccer, "Soccer", args.date, REPO_ROOT) if soccer is not None else soccer
    mlb = apply_usage_redistribution(mlb, "MLB", args.date, REPO_ROOT) if mlb is not None else mlb
    nba1q = apply_usage_redistribution(nba1q, "NBA1Q", args.date, REPO_ROOT) if nba1q is not None else nba1q
    nba1h = apply_usage_redistribution(nba1h, "NBA1H", args.date, REPO_ROOT) if nba1h is not None else nba1h

    print("Building combined slate...")
    combined = build_combined_slate(nba, cbb, nhl, soccer,
                                    wcbb=wcbb, mlb=mlb, nba1q=nba1q, nba1h=nba1h)

    # ✅ Attach Standard refs for combined too
    combined = attach_standard_refs(combined)

    combined = attach_alt_book_lines(
        combined,
        underdog_csv=str(args.underdog_csv or ""),
        draftkings_csv=str(args.draftkings_csv or ""),
    )

    combined = drop_stale_rows(combined, args.date, "Combined")

    # Per-sport Excel sheets use SLATE_COLS — propagate UD/DK lines from combined onto each.
    nba = propagate_alt_book_lines_to_sport_frame(nba, combined, ("NBA",))
    cbb = propagate_alt_book_lines_to_sport_frame(cbb, combined, ("CBB",))
    nhl = propagate_alt_book_lines_to_sport_frame(nhl, combined, ("NHL",))
    soccer = propagate_alt_book_lines_to_sport_frame(soccer, combined, ("Soccer",))
    wcbb = propagate_alt_book_lines_to_sport_frame(wcbb, combined, ("WCBB",))
    mlb = propagate_alt_book_lines_to_sport_frame(mlb, combined, ("MLB",))
    nba1q = propagate_alt_book_lines_to_sport_frame(nba1q, combined, ("NBA1Q",))
    nba1h = propagate_alt_book_lines_to_sport_frame(nba1h, combined, ("NBA1H",))

    _n_ud = int(combined["line_underdog"].notna().sum()) if "line_underdog" in combined.columns else 0
    _n_dk = int(combined["line_draftkings"].notna().sum()) if "line_draftkings" in combined.columns else 0
    if _n_ud == 0 and _n_dk == 0:
        print(
            "  [alt-books] No Underdog/DraftKings lines merged (all blank). "
            f"Fetch into outputs/{args.date}/underdog_props.csv and "
            f"outputs/{args.date}/draftkings_props_nba.csv, then re-run this script."
        )

    print(f"  {len(combined)} total props")
    for s in ("NBA", "CBB", "NHL", "Soccer", "MLB", "NBA1H", "NBA1Q", "WCBB"):
        n_s = int((combined["sport"] == s).sum()) if "sport" in combined.columns else 0
        if n_s > 0:
            print(f"  Full Slate rows — {s}: {n_s}")

    # ── CBB Goblin rank floor ─────────────────────────────────────────────────
    # CBB Goblin hits at ~55-58% vs NBA Goblin at ~67%.
    # We raise the minimum rank score for CBB Goblin-only pools so only
    # the model's highest-confidence CBB Goblin props enter tickets.
    CBB_GOBLIN_MIN_RANK = 5.0   # tune this up/down based on graded results

    def pool(df, pt=None):
        if df is None or len(df) == 0:
            return df

        sport = str(df["sport"].iloc[0]).upper() if "sport" in df.columns and len(df) > 0 else ""

        # Sport-specific prop exclusions
        excluded = set()
        if sport == "NBA":
            excluded = NBA_EXCLUDED_PROPS
        elif sport == "CBB":
            excluded = CBB_EXCLUDED_PROPS
        elif sport == "NHL":
            excluded = NHL_EXCLUDED_PROPS
        elif sport == "SOCCER":
            excluded = SOCCER_EXCLUDED_PROPS

        filtered_df = df.copy()
        if excluded and "prop_type" in filtered_df.columns:
            filtered_df = filtered_df[
                ~filtered_df["prop_type"].astype(str).str.lower().isin(excluded)
            ]

        # Sport-specific hit rate floors based on empirical data
        effective_min_hit = args.min_hit_rate

        if pt == ["Goblin"]:
            if sport == "NBA":
                effective_min_hit = max(args.min_hit_rate, 0.62)   # NBA Goblin: 64.3% overall
            elif sport == "CBB":
                effective_min_hit = max(args.min_hit_rate, 0.58)   # CBB Goblin: 61.9%
            elif sport == "NHL":
                effective_min_hit = max(args.min_hit_rate, 0.38)   # NHL Goblin is weak (40%)
            else:
                effective_min_hit = max(args.min_hit_rate, 0.55)

        elif pt == ["Standard"]:
            if sport == "NBA":
                effective_min_hit = max(args.min_hit_rate, 0.50)   # NBA Standard: 50.7% — only Tier A viable
            elif sport == "CBB":
                effective_min_hit = max(args.min_hit_rate, 0.50)   # CBB Standard: 51.6%
            elif sport == "NHL":
                effective_min_hit = max(args.min_hit_rate, 0.65)   # NHL Standard: 67.9% — very strong
            else:
                effective_min_hit = max(args.min_hit_rate, 0.50)

        # Direction filter: NHL OVER props are only 21.5% — exclude from NHL pools
        if sport == "NHL" and "direction" in filtered_df.columns:
            filtered_df = filtered_df[
                filtered_df["direction"].astype(str).str.upper() != "OVER"
            ]

        # Tier floor: exclude Tier D from all pools
        effective_tiers = [t for t in (tiers if tiers else ["A", "B", "C", "D"]) if t != "D"]

        # CBB Goblin rank floor
        effective_min_rank = args.min_rank
        if sport == "CBB" and pt is not None and pt == ["Goblin"]:
            effective_min_rank = max(args.min_rank or 0, CBB_GOBLIN_MIN_RANK)

        # Exclude Demon from all pools
        effective_pick_types = pt if pt is not None else [
            p for p in (pick_types if pick_types else ["Goblin", "Standard"]) if p != "Demon"
        ]

        return filter_eligible(
            filtered_df,
            effective_min_hit,
            args.min_edge,
            effective_min_rank,
            effective_tiers,
            effective_pick_types,
        )

    nba_pool = pool(nba)
    cbb_pool = pool(cbb)
    mlb_pool = pool(mlb)
    combo_pool = pool(combined)
    mlb_elig = len(mlb_pool) if mlb_pool is not None else 0
    print(f"  NBA eligible: {len(nba_pool)} | CBB eligible: {len(cbb_pool)} | MLB eligible: {mlb_elig} | Combined: {len(combo_pool)}")
    print(f"  CBB Goblin rank floor: {CBB_GOBLIN_MIN_RANK} (NBA uses global floor: {args.min_rank})")

    print("Generating tickets + workbook...")
    wb = Workbook()
    wb.remove(wb.active)

    all_ticket_groups = []
    fantasy_excluded_count = 0
    def_tier_filtered_count = 0
    ban_list_filtered_count = 0
    total_eligible_count = 0
    generated_tickets: dict = {}
    counters = {
        "fantasy_excluded_count": fantasy_excluded_count,
        "def_tier_filtered_count": def_tier_filtered_count,
        "ban_list_filtered_count": ban_list_filtered_count,
        "total_eligible_count": total_eligible_count,
        "player_ticket_counts": defaultdict(int),
    }

    def add_structured_sport_tickets(
        sport_df: pd.DataFrame,
        sport_label: str,
        bg_hdr: str,
        prefix: str,
        min_leg_hit_rate: float | None = None,
        prioritize_ticket_hit: bool = False,
        ticket_sort_mode: str = "rank",
        ticket_gen_starts: int = 6,
    ):
        if sport_df is None or sport_df.empty:
            print(f"  WARNING: {sport_label} skipped (empty pool).")
            return

        p_ticket = build_single_structure_ticket(
            sport_df,
            sport_label,
            "power",
            counters=counters,
            min_leg_hit_rate=min_leg_hit_rate,
            prioritize_ticket_hit=prioritize_ticket_hit,
            ticket_sort_mode=ticket_sort_mode,
            ticket_gen_starts=ticket_gen_starts,
        )
        f_ticket = build_single_structure_ticket(
            sport_df,
            sport_label,
            "flex",
            counters=counters,
            min_leg_hit_rate=min_leg_hit_rate,
            prioritize_ticket_hit=prioritize_ticket_hit,
            ticket_sort_mode=ticket_sort_mode,
            ticket_gen_starts=ticket_gen_starts,
        )
        s_ticket = build_single_structure_ticket(
            sport_df,
            sport_label,
            "standard",
            counters=counters,
            min_leg_hit_rate=min_leg_hit_rate,
            prioritize_ticket_hit=prioritize_ticket_hit,
            ticket_sort_mode=ticket_sort_mode,
            ticket_gen_starts=ticket_gen_starts,
        )
        if s_ticket is None:
            s_ticket = build_single_structure_ticket(
                sport_df,
                sport_label,
                "standard",
                counters=counters,
                relaxed=True,
                min_leg_hit_rate=min_leg_hit_rate,
                prioritize_ticket_hit=prioritize_ticket_hit,
                ticket_sort_mode=ticket_sort_mode,
                ticket_gen_starts=ticket_gen_starts,
            )
        if s_ticket is None and p_ticket is not None:
            # Ensure every sport can publish a Standard ticket when possible.
            s_ticket = dict(p_ticket)
            s_ticket["ticket_type"] = "Standard"
            s_ticket["sport"] = sport_label

        ps3_ticket = build_single_structure_ticket(
            sport_df,
            sport_label,
            "power_std3",
            counters=counters,
            min_leg_hit_rate=min_leg_hit_rate,
            prioritize_ticket_hit=prioritize_ticket_hit,
            ticket_sort_mode=ticket_sort_mode,
            ticket_gen_starts=ticket_gen_starts,
        )
        g3_ticket = build_single_structure_ticket(
            sport_df,
            sport_label,
            "goblin3",
            counters=counters,
            min_leg_hit_rate=min_leg_hit_rate,
            prioritize_ticket_hit=prioritize_ticket_hit,
            ticket_sort_mode=ticket_sort_mode,
            ticket_gen_starts=ticket_gen_starts,
        )

        if (
            p_ticket is None
            and f_ticket is None
            and s_ticket is None
            and ps3_ticket is None
            and g3_ticket is None
        ):
            print(f"  WARNING: {sport_label} skipped (<2 eligible legs after strict filters).")
            return

        if p_ticket is not None:
            sname = f"{prefix} Power Play 2-Leg"[:31]
            write_ticket_sheet(wb, [p_ticket], sname, bg_hdr, label=f"{sport_label} Power Play")
            all_ticket_groups.append((sname, [p_ticket], None))
            print(f"  {sname}: 1 ticket")
            generated_tickets.setdefault(sport_label, {})["power_play"] = {
                "legs": [str(x.get("prop_type", "")) for x in p_ticket.get("rows", [])]
            }
        else:
            print(f"  WARNING: {sport_label} Power Play 2-Leg unavailable (strict filters).")
            generated_tickets.setdefault(sport_label, {})["power_play"] = None

        if f_ticket is not None:
            sname = f"{prefix} Flex 3-Leg"[:31]
            write_ticket_sheet(wb, [f_ticket], sname, bg_hdr, label=f"{sport_label} Flex")
            all_ticket_groups.append((sname, [f_ticket], None))
            print(f"  {sname}: 1 ticket")
            generated_tickets.setdefault(sport_label, {})["flex"] = {
                "legs": [str(x.get("prop_type", "")) for x in f_ticket.get("rows", [])]
            }
        else:
            print(f"  WARNING: {sport_label} Flex 3-Leg unavailable (strict filters).")
            generated_tickets.setdefault(sport_label, {})["flex"] = None

        if s_ticket is not None:
            sname = f"{prefix} Standard 2-Leg"[:31]
            write_ticket_sheet(wb, [s_ticket], sname, bg_hdr, label=f"{sport_label} Standard")
            all_ticket_groups.append((sname, [s_ticket], None))
            print(f"  {sname}: 1 ticket")
            generated_tickets.setdefault(sport_label, {})["standard"] = {
                "legs": [str(x.get("prop_type", "")) for x in s_ticket.get("rows", [])]
            }
        else:
            print(f"  WARNING: {sport_label} Standard 2-Leg unavailable (strict filters).")
            generated_tickets.setdefault(sport_label, {})["standard"] = None

        if ps3_ticket is not None:
            sname = f"{prefix} Pwr Std 3-Leg"[:31]
            write_ticket_sheet(wb, [ps3_ticket], sname, bg_hdr, label=f"{sport_label} Power Std 3")
            all_ticket_groups.append((sname, [ps3_ticket], None))
            print(f"  {sname}: 1 ticket")
            generated_tickets.setdefault(sport_label, {})["power_std3"] = {
                "legs": [str(x.get("prop_type", "")) for x in ps3_ticket.get("rows", [])]
            }
        else:
            print(f"  WARNING: {sport_label} Power Standard 3-Leg unavailable (strict filters).")
            generated_tickets.setdefault(sport_label, {})["power_std3"] = None

        if g3_ticket is not None:
            sname = f"{prefix} Goblin 3-Leg"[:31]
            write_ticket_sheet(wb, [g3_ticket], sname, bg_hdr, label=f"{sport_label} Goblin 3")
            all_ticket_groups.append((sname, [g3_ticket], None))
            print(f"  {sname}: 1 ticket")
            generated_tickets.setdefault(sport_label, {})["goblin3"] = {
                "legs": [str(x.get("prop_type", "")) for x in g3_ticket.get("rows", [])]
            }
        else:
            print(f"  WARNING: {sport_label} Goblin 3-Leg unavailable (strict filters).")
            generated_tickets.setdefault(sport_label, {})["goblin3"] = None

    _prio_hit = bool(args.prioritize_ticket_hit)
    _ticket_sort = str(args.ticket_candidate_sort)
    _tg_starts = int(args.ticket_gen_starts)
    add_structured_sport_tickets(
        pool(nba),
        "NBA",
        C["hdr_nba"],
        "NBA",
        min_leg_hit_rate=structured_min_leg_hr,
        prioritize_ticket_hit=_prio_hit,
        ticket_sort_mode=_ticket_sort,
        ticket_gen_starts=_tg_starts,
    )
    add_structured_sport_tickets(
        pool(cbb),
        "CBB",
        C["hdr_cbb"],
        "CBB",
        min_leg_hit_rate=structured_min_leg_hr,
        prioritize_ticket_hit=_prio_hit,
        ticket_sort_mode=_ticket_sort,
        ticket_gen_starts=_tg_starts,
    )
    if nhl is not None and len(nhl) > 0:
        add_structured_sport_tickets(
            pool(nhl),
            "NHL",
            C["hdr_nhl"],
            "NHL",
            min_leg_hit_rate=structured_min_leg_hr,
            prioritize_ticket_hit=_prio_hit,
            ticket_sort_mode=_ticket_sort,
            ticket_gen_starts=_tg_starts,
        )
    if soccer is not None and len(soccer) > 0:
        add_structured_sport_tickets(
            pool(soccer),
            "Soccer",
            C["hdr_soccer"],
            "Soccer",
            min_leg_hit_rate=structured_min_leg_hr,
            prioritize_ticket_hit=_prio_hit,
            ticket_sort_mode=_ticket_sort,
            ticket_gen_starts=_tg_starts,
        )
    if mlb is not None and len(mlb) > 0:
        add_structured_sport_tickets(
            mlb_pool,
            "MLB",
            C["hdr_mlb"],
            "MLB",
            min_leg_hit_rate=structured_min_leg_hr,
            prioritize_ticket_hit=_prio_hit,
            ticket_sort_mode=_ticket_sort,
            ticket_gen_starts=_tg_starts,
        )
    if nba1q is not None and len(nba1q) > 0:
        add_structured_sport_tickets(
            pool(nba1q),
            "NBA1Q",
            C["hdr_nba1q"],
            "NBA1Q",
            min_leg_hit_rate=structured_min_leg_hr,
            prioritize_ticket_hit=_prio_hit,
            ticket_sort_mode=_ticket_sort,
            ticket_gen_starts=_tg_starts,
        )
    if nba1h is not None and len(nba1h) > 0:
        add_structured_sport_tickets(
            pool(nba1h),
            "NBA1H",
            C["hdr_nba1h"],
            "NBA1H",
            min_leg_hit_rate=structured_min_leg_hr,
            prioritize_ticket_hit=_prio_hit,
            ticket_sort_mode=_ticket_sort,
            ticket_gen_starts=_tg_starts,
        )

    _cross_pools = [
        ("NBA", nba_pool),
        ("CBB", cbb_pool),
        ("WCBB", pool(wcbb) if wcbb is not None and len(wcbb) > 0 else None),
        ("NHL", pool(nhl) if nhl is not None and len(nhl) > 0 else None),
        ("Soccer", pool(soccer) if soccer is not None and len(soccer) > 0 else None),
        ("MLB", mlb_pool),
        ("NBA1Q", pool(nba1q) if nba1q is not None and len(nba1q) > 0 else None),
        ("NBA1H", pool(nba1h) if nba1h is not None and len(nba1h) > 0 else None),
    ]
    cross_bundle = build_cross_pipeline_ticket_bundle(
        _cross_pools,
        max_legs=CROSS_PIPELINE_MAX_LEGS,
        ticket_sort_mode=_ticket_sort,
        player_ticket_counts=counters["player_ticket_counts"],
    )
    mix_keys = ("cross_pipeline_standard", "cross_pipeline_goblin", "cross_pipeline_mix")
    generated_tickets.setdefault("MIX", {})
    for k in mix_keys:
        generated_tickets["MIX"][k] = None

    def _mix_key_for_cross_ticket(ticket: dict) -> str:
        ttype = str(ticket.get("ticket_type", ""))
        if ttype == "Cross-Pipeline Standard":
            return "cross_pipeline_standard"
        if ttype == "Cross-Pipeline Goblin":
            return "cross_pipeline_goblin"
        return "cross_pipeline_mix"

    if cross_bundle:
        for xs, cross_ticket in cross_bundle:
            write_ticket_sheet(
                wb,
                [cross_ticket],
                xs,
                C["hdr_mix"],
                label=str(cross_ticket.get("ticket_type", "Cross-Pipeline")),
            )
            all_ticket_groups.append((xs, [cross_ticket], None))
            print(
                f"  {xs}: 1 ticket ({cross_ticket['n_legs']} legs, max {CROSS_PIPELINE_MAX_LEGS})"
            )
            gk = _mix_key_for_cross_ticket(cross_ticket)
            generated_tickets["MIX"][gk] = {
                "legs": [
                    f"{x.get('sport', '')}:{x.get('player', '')} {x.get('prop_type', '')}"
                    for x in cross_ticket.get("rows", [])
                ]
            }
    else:
        print(
            "  WARNING: Cross-pipeline tickets skipped (need ≥2 pipelines with eligible props per slip)."
        )

    print("Writing slate sheets...")
    # Strict-mode guardrail: fail if mixed dates survived filtering.
    if not args.allow_cross_date_fallback:
        td = str(args.date).strip()[:10]
        to_check = [
            ("NBA", nba),
            ("CBB", cbb),
            ("NHL", nhl),
            ("Soccer", soccer),
            ("MLB", mlb),
            ("Combined", combined),
        ]
        mixed = []
        for label, sdf in to_check:
            if sdf is None or len(sdf) == 0 or "game_date" not in sdf.columns:
                continue
            dated = sdf["game_date"].notna()
            gd = sdf["game_date"].astype(str).str[:10]
            if label == "Soccer":
                bad = sdf[dated & (gd < td)]
            elif label == "Combined" and "sport" in sdf.columns:
                su = sdf["sport"].astype(str).str.upper()
                is_soc = su == "SOCCER"
                bad = sdf[dated & ((gd < td) | (~is_soc & (gd != td)))]
            else:
                bad = sdf[dated & (gd != td)]
            if len(bad) > 0:
                cts = bad["game_date"].astype(str).str[:10].value_counts().to_dict()
                mixed.append((label, cts))
        if mixed:
            msg = "; ".join([f"{lbl}: {cts}" for lbl, cts in mixed])
            raise ValueError(f"Strict date mode violation for target {args.date}: {msg}")

    full_slate_df = apply_full_slate_signal_columns(combined.copy())
    write_slate_sheet(
        wb,
        full_slate_df,
        "Full Slate",
        C["hdr"],
        "ALL",
        column_order=full_slate_column_order(full_slate_df),
        full_slate_visual=True,
    )
    write_slate_sheet(wb, nba, "NBA Slate", C["hdr_nba"], "NBA")
    write_slate_sheet(wb, cbb, "CBB Slate", C["hdr_cbb"], "CBB")
    if nhl is not None and len(nhl) > 0:
        write_slate_sheet(wb, nhl, "NHL Slate", C["hdr_nhl"], "NHL")
    if soccer is not None and len(soccer) > 0:
        write_slate_sheet(wb, soccer, "Soccer Slate", C["hdr_soccer"], "Soccer")
    if wcbb is not None and len(wcbb) > 0:
        write_slate_sheet(wb, wcbb, "WCBB Slate", C["hdr_wcbb"], "WCBB")
    if mlb is not None and len(mlb) > 0:
        write_slate_sheet(wb, mlb, "MLB Slate", C["hdr_mlb"], "MLB")
    if nba1q is not None and len(nba1q) > 0:
        write_slate_sheet(wb, nba1q, "NBA1Q Slate", C["hdr_nba1q"], "NBA1Q")
    if nba1h is not None and len(nba1h) > 0:
        write_slate_sheet(wb, nba1h, "NBA1H Slate", C["hdr_nba1h"], "NBA1H")

    for _gn, _tickets, _bg in all_ticket_groups:
        for _ti in _tickets:
            enrich_ticket_curve_payouts(_ti, stake_unit=float(args.curve_stake_usd))

    write_summary(wb, nba, cbb, combined, all_ticket_groups, args.date, thresholds,
                  nhl=nhl, soccer=soccer, wcbb=wcbb, mlb=mlb, nba1q=nba1q, nba1h=nba1h)

    # Reorder: put SUMMARY + slate sheets at the front
    desired_first = [
        "SUMMARY", "Full Slate", "NBA Slate", "CBB Slate", "NHL Slate", "Soccer Slate",
        "WCBB Slate", "MLB Slate", "NBA1Q Slate", "NBA1H Slate",
    ]
    for sname in reversed(desired_first):
        if sname in wb.sheetnames:
            wb.move_sheet(wb[sname], offset=-(len(wb.sheetnames) - 1))

    wb.save(args.output)
    print(f"\n[OK] Saved -> {args.output}")
    print(f"   Sheets ({len(wb.sheetnames)}): {wb.sheetnames}")

    if args.write_web:
        print("\nWriting web outputs...")
        if all_ticket_groups:
            payload = ticket_groups_to_payload(
                all_ticket_groups,
                args.date,
                thresholds,
                bankroll=max(0.0, float(args.bankroll)),
                curve_stake_usd=float(args.curve_stake_usd),
            )
            n_groups = len(payload["groups"])
            n_slips = sum(len(g["tickets"]) for g in payload["groups"])
            print(f"  Web payload: {n_groups} groups, {n_slips} slips (workbook — all sports).")
        else:
            print("  WARNING: workbook produced 0 groups — falling back to FINAL builder.")
            nhl_pool_web = pool(nhl) if nhl is not None and len(nhl) > 0 else None
            soccer_pool_web = pool(soccer) if soccer is not None and len(soccer) > 0 else None
            mlb_pool_web = mlb_pool if mlb_pool is not None and len(mlb_pool) > 0 else None
            final_groups = build_final_web_ticket_groups(
                nba_pool,
                cbb_pool,
                nhl_pool=nhl_pool_web,
                soccer_pool=soccer_pool_web,
                mlb_pool=mlb_pool_web,
                min_hit_rate=thresholds.get("min_hit_rate", 0.70),
                min_edge=thresholds.get("min_edge", 2.0),
                min_rank=thresholds.get("min_rank", 5.0),
                ticket_leg_sizes=leg_sizes_runtime,
                leg_min_hit_by_n=leg_min_hit_by_n,
                prioritize_ticket_hit=bool(args.prioritize_ticket_hit),
                ticket_sort_mode=str(args.ticket_candidate_sort),
            )
            payload = ticket_groups_to_payload(
                final_groups,
                args.date,
                thresholds,
                bankroll=max(0.0, float(args.bankroll)),
                curve_stake_usd=float(args.curve_stake_usd),
            )
            n_groups = len(payload["groups"])
            n_slips = sum(len(g["tickets"]) for g in payload["groups"])
            print(f"  Web payload: {n_groups} groups, {n_slips} slips (FINAL fallback).")
        write_web_outputs(payload, args.web_outdir)
        write_slate_json(nba, cbb, nhl, soccer, args.date, args.web_outdir,
                         wcbb=wcbb, mlb=mlb, nba1q=nba1q, nba1h=nba1h)
        if args.also_root:
            write_web_outputs(payload, outdir=".")
        # Avoid Windows console codepage issues with unicode checkmarks.
        print("[OK] Web outputs complete.")

    print("\n[TICKETS] -- SUMMARY -----------------------------------------")
    for sport, tickets in generated_tickets.items():
        pp = tickets.get("power_play")
        fl = tickets.get("flex")
        st = tickets.get("standard")
        ps3 = tickets.get("power_std3")
        g3 = tickets.get("goblin3")
        pp_legs = " + ".join(pp["legs"]) if pp else "SKIPPED"
        fl_legs = " + ".join(fl["legs"]) if fl else "SKIPPED"
        st_legs = " + ".join(st["legs"]) if st else "SKIPPED"
        ps3_legs = " + ".join(ps3["legs"]) if ps3 else "SKIPPED"
        g3_legs = " + ".join(g3["legs"]) if g3 else "SKIPPED"
        print(
            f"[TICKETS] {sport}: Power Play ({pp_legs}) | Flex ({fl_legs}) | Standard ({st_legs}) | "
            f"Pwr Std 3 ({ps3_legs}) | Goblin 3 ({g3_legs})"
        )

    print(f"[TICKETS] Fantasy Score excluded : {int(counters['fantasy_excluded_count'])} props removed")
    print(f"[TICKETS] Def tier filtered      : {int(counters['def_tier_filtered_count'])} props removed")
    print(f"[TICKETS] Prop ban list filtered : {int(counters['ban_list_filtered_count'])} props removed")
    print(f"[TICKETS] Total eligible props   : {int(counters['total_eligible_count'])} props used")
    print("[TICKETS] ----------------------------------------------------")


# ── Web render helper ─────────────────────────────────────────────────────────

_SPORT_ACCENT: dict[str, str] = {
    "NBA":    "#1A5276",
    "CBB":    "#1E8449",
    "NHL":    "#1A3A5C",
    "SOCCER": "#1A5C2E",
    "MLB":    "#922B21",
    "WCBB":   "#4A235A",
    "NBA1Q":  "#1F618D",
    "NBA1H":  "#117A65",
    "CROSS":  "#6C3483",
    "MIX":    "#6C3483",
}

_PICK_COLOR: dict[str, str] = {
    "goblin":   "#39ff6e",
    "demon":    "#ff4d4d",
    "standard": "#00e5ff",
}


def _h(v) -> str:
    """HTML-escape a value."""
    import html as _html
    return _html.escape(str(v)) if v is not None else ""


def _pct(v, decimals: int = 0) -> str:
    try:
        return f"{float(v) * 100:.{decimals}f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt(v, decimals: int = 2, suffix: str = "") -> str:
    try:
        return f"{float(v):.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _sport_accent(sport: str) -> str:
    key = (sport or "").upper().split()[0]
    return _SPORT_ACCENT.get(key, "#6C3483")


def _group_sport(group_name: str) -> str:
    """Infer sport from group name for accent colouring."""
    name = (group_name or "").upper()
    if name.startswith("CROSS") or name.startswith("MIX"):
        return "CROSS"
    for sp in ("NBA1Q", "NBA1H", "WCBB", "SOCCER", "NHL", "MLB", "CBB", "NBA"):
        if sp in name:
            return sp
    return "NBA"


def _tickets_fmt_line_plain(x) -> str:
    try:
        if x is None:
            return "—"
        xf = float(x)
        if abs(xf - round(xf)) < 1e-9:
            return str(int(round(xf)))
        return f"{xf:.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(x) if x is not None else "—"


def _tickets_hits_js_array(over_rate, n: int) -> str:
    """JS array literal [1,0,...] or null — same reconstruction as render_tickets_html."""
    if over_rate is None:
        return "null"
    try:
        x = float(over_rate)
    except (TypeError, ValueError):
        return "null"
    cnt = int(round(x * n)) if x <= 1.0 else int(round(x))
    cnt = max(0, min(n, cnt))
    vals = [1] * cnt + [0] * (n - cnt)
    return str(vals)


def _tickets_leg_graph_row_html(leg: dict, row_id: str, table_cols: int) -> str:
    """Expandable Chart.js row for /tickets (tickets_built.html loads Chart.js)."""
    l5_avg = leg.get("l5_avg")
    season_avg = leg.get("season_avg")
    l5_over = leg.get("l5_over")
    l5_under = leg.get("l5_under")
    l10_over = leg.get("l10_over")
    l10_under = leg.get("l10_under")
    line_val = leg.get("line")
    dir_txt = str(leg.get("direction") or "").upper()
    hr_val = leg.get("hit_rate")

    def _pill(label: str, val, fmt=None) -> str:
        if val is None:
            return ""
        if fmt:
            try:
                v = fmt(val)
            except Exception:
                v = str(val)
        else:
            v = str(val)
        return f'<div class="gstat"><div class="gstat-label">{_h(label)}</div><div class="gstat-val">{_h(v)}</div></div>'

    def _n_over(n_games: int, raw):
        try:
            x = float(raw)
            k = int(round(x * n_games)) if x <= 1.0 else int(round(x))
            k = max(0, min(n_games, k))
            return f"{k}/{n_games}"
        except (TypeError, ValueError):
            return str(raw)

    pills = "".join(
        [
            _pill("L5 Avg", l5_avg, lambda x: f"{float(x):.1f}"),
            _pill("Season Avg", season_avg, lambda x: f"{float(x):.1f}"),
            _pill("L5 Over", l5_over, lambda x: _n_over(5, x)),
            _pill("L5 Under", l5_under, lambda x: _n_over(5, x)),
            _pill("L10 Over", l10_over, lambda x: _n_over(10, x)),
            _pill("L10 Under", l10_under, lambda x: _n_over(10, x)),
            _pill("Hit Rate", hr_val, lambda x: f"{float(x) * 100:.0f}%"),
        ]
    )

    l5hits = _tickets_hits_js_array(l5_under if dir_txt == "UNDER" else l5_over, 5)
    l10hits = _tickets_hits_js_array(l10_under if dir_txt == "UNDER" else l10_over, 10)
    chart_data = (
        "{\n"
        f"  line: {line_val if line_val is not None else 'null'},\n"
        f"  l5hits: {l5hits},\n"
        f"  l10hits: {l10hits},\n"
        f"  l5avg: {l5_avg if l5_avg is not None else 'null'},\n"
        f"  seasonAvg: {season_avg if season_avg is not None else 'null'},\n"
        f"  player: {repr(leg.get('player', ''))},\n"
        f"  prop: {repr(leg.get('prop_type', ''))},\n"
        f"  direction: {repr(leg.get('direction', ''))}\n"
        "}"
    )

    sub = f"{leg.get('player', '')} · {leg.get('prop_type', '')} · Line {_tickets_fmt_line_plain(line_val)}"
    cid = "c-" + row_id
    return f"""
<tr class="leg-graph-row" id="{_h(row_id)}">
  <td class="leg-graph-cell" colspan="{table_cols}">
    <div class="graph-wrap">
      <div style="flex:1;min-width:200px;">
        <div style="font-size:11px;color:var(--muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px;">{_h(sub)}</div>
        <div class="graph-stats">{pills}</div>
      </div>
      <div class="graph-canvas-wrap">
        <canvas class="leg-chart" id="{_h(cid)}"></canvas>
      </div>
    </div>
    <script>
    (function(){{
      var d = {chart_data};
      var ctx = document.getElementById({repr(cid)});
      if(!ctx||!window.Chart) return;
      var hits10 = d.l10hits || d.l5hits || [];
      if (!hits10 || !hits10.length) return;
      var labels = hits10.map((_,i)=>'G'+(i+1));
      var barVals = hits10.map(h => h ? 1 : 0);
      var colors = hits10.map(h=> h ? '#00F2FF' : '#c96a74');
      new Chart(ctx, {{
        type:'bar',
        data:{{
          labels: labels,
          datasets:[{{
            label:'Hit Timeline',
            data: barVals,
            backgroundColor: colors,
            borderRadius:3,
            borderSkipped:false
          }}]
        }},
        options:{{
          responsive:true,
          maintainAspectRatio:false,
          plugins:{{
            legend:{{display:false}},
            tooltip:{{callbacks:{{label:function(c){{return hits10[c.dataIndex] ? 'Hit' : 'Miss';}}}}}}
          }},
          scales:{{
            x:{{ticks:{{color:'#888',font:{{size:10}}}},grid:{{color:'#1a1f2e'}}}},
            y:{{
              min: 0,
              max: 1,
              ticks:{{
                stepSize: 1,
                color:'#888',
                font:{{size:10}},
                callback: function(v){{ return v === 1 ? 'Hit' : 'Miss'; }}
              }},
              grid:{{color:'#1a1f2e'}},
            }}
          }}
        }}
      }});
    }})();
    </script>
  </td>
</tr>"""


def _tickets_generator_filter_html(filters: dict) -> str:
    """Human-readable ticket-builder settings (parity with legacy render_tickets_html)."""
    if not filters:
        return ""
    lm = filters.get("leg_min_hit_by_n")
    if isinstance(lm, dict) and lm:
        try:
            lm_s = ", ".join(
                f"{k}:{v}" for k, v in sorted(lm.items(), key=lambda kv: int(str(kv[0])) if str(kv[0]).isdigit() else 0)
            )
        except Exception:
            lm_s = str(lm)
    else:
        lm_s = "—"

    def _disp(k: str, default: str = "—"):
        v = filters.get(k, default)
        if v is None:
            return "None"
        return v

    return f'''<div class="filter-pill" style="margin-top:0;">
  <div style="font-size:10px;letter-spacing:2px;color:var(--muted);text-transform:uppercase;margin-bottom:10px;">Ticket generator</div>
  Filters &rarr;
  <strong>tiers:</strong> {_h(_disp("tiers", "ALL"))} &nbsp;
  <strong>min_hit_rate:</strong> {_h(_disp("min_hit_rate", 0))} &nbsp;
  <strong>min_edge:</strong> {_h(_disp("min_edge", 0))} &nbsp;
  <strong>min_rank:</strong> {_h(_disp("min_rank", "None"))} &nbsp;
  <strong>pick_types:</strong> {_h(_disp("pick_types", "ALL"))} &nbsp;
  <strong>high_conviction:</strong> {_h(_disp("high_conviction", False))} &nbsp;
  <strong>ticket_gen_starts:</strong> {_h(_disp("ticket_gen_starts", "—"))} &nbsp;
  <strong>structured_min_leg_hit:</strong> {_h(_disp("structured_min_leg_hit_rate", "—"))} &nbsp;
  <strong>leg_min_hit_by_n:</strong> {_h(lm_s)}
  &nbsp;&nbsp;<a href="/tickets_latest.json" style="color:var(--cyan);">⬇ JSON</a>
</div>
<div class="filter-pill" style="margin-top:-12px;">
  Slip tags use modeled <strong>EV</strong> (Power payout &times; win prob): <strong>STRONG</strong> &ge;1.40&times;, <strong>LEAN</strong> 1.15&ndash;1.40&times;, <strong>RISKY</strong> &lt;1.15&times;.
  Tap a player row to expand the L5 / L10 hit timeline when counts exist in JSON.
</div>'''


def render_tickets_body_html(payload: dict) -> tuple[str, str]:
    """
    Render today's ticket slips from the tickets_latest.json payload.
    Returns (body_html, page_title) for injection into tickets_built.html.
    """
    date_str = payload.get("date") or "Today"
    generated_at = payload.get("generated_at") or ""
    groups = payload.get("groups") or []
    n_slips = sum(len(g.get("tickets") or []) for g in groups)
    n_groups = len(groups)

    page_title = f"PropOracle Tickets — {date_str}"

    parts: list[str] = []
    parts.append('<div class="tickets-built shell">')

    # ── Hero ──────────────────────────────────────────────────────────────────
    built_html = (
        f'<span class="hero-meta-built">{_h(generated_at)}</span>' if generated_at else ""
    )
    parts.append(f'''
<div class="hero tickets-hero" style="margin-bottom:24px;">
  <div class="hero-copy">
    <div class="hero-eyebrow" style="font-size:11px;letter-spacing:2px;color:var(--muted);text-transform:uppercase;margin-bottom:8px;">Today&rsquo;s Picks</div>
    <h1 class="hero-title" style="font-family:'Bebas Neue',sans-serif;font-size:clamp(32px,5vw,56px);letter-spacing:0.06em;line-height:1.05;color:var(--text);margin:0;">
      PROP<span class="hero-oracle-em">ORACLE</span>&nbsp;TICKETS
    </h1>
  </div>
  <div class="hero-meta-row" role="group" aria-label="Slate summary">
    <span class="hero-meta-date">{_h(date_str)}</span>
    <span class="hero-meta-counts">{n_groups} groups &nbsp;·&nbsp; {n_slips} slips</span>
    {built_html}
  </div>
</div>''')

    filters = payload.get("filters") or {}
    if filters:
        parts.append(_tickets_generator_filter_html(filters))

    if not groups:
        parts.append('<div class="filter-pill">No tickets generated for this date.</div>')
        parts.append('</div>')
        return "".join(parts), page_title

    # ── Groups ────────────────────────────────────────────────────────────────
    leg_graph_uid = 0
    table_cols = 10
    for group in groups:
        group_name = group.get("group_name") or "Tickets"
        n_legs = group.get("n_legs") or 0
        power_pay = group.get("power_payout")
        flex_pay = group.get("flex_payout")
        tickets = group.get("tickets") or []
        if not tickets:
            continue

        sport_key = _group_sport(group_name)
        accent = _sport_accent(sport_key)

        pay_label = ""
        if power_pay and flex_pay and abs(float(power_pay) - float(flex_pay)) > 0.01:
            pay_label = f"Power {_fmt(power_pay, 1)}× &nbsp;·&nbsp; Flex {_fmt(flex_pay, 1)}×"
        elif power_pay:
            pay_label = f"{_fmt(power_pay, 1)}×"

        parts.append(f'''
<div class="group" style="border-left:4px solid {accent};">
  <div class="group-hdr">
    <span class="group-title" style="color:{accent};">{_h(group_name)}</span>
    <span class="group-meta">{n_legs}-leg{(" &nbsp;·&nbsp; " + pay_label) if pay_label else ""}</span>
  </div>''')

        for ticket in tickets:
            ticket_no = ticket.get("ticket_no") or ""
            win_prob = ticket.get("est_win_prob")
            avg_hr = ticket.get("avg_hit_rate")
            ev = ticket.get("ev_power")
            t_power_pay = ticket.get("power_payout") or ticket.get("base_power_payout")
            has_warn = ticket.get("has_data_warning", False)
            legs = ticket.get("legs") or []

            # Signal badge
            if ev is not None:
                try:
                    ev_f = float(ev)
                    if ev_f >= 1.40:
                        sig_cls, sig_lbl = "sig-strong", "STRONG"
                    elif ev_f >= 1.15:
                        sig_cls, sig_lbl = "sig-lean", "LEAN"
                    else:
                        sig_cls, sig_lbl = "sig-risk", "RISKY"
                except (TypeError, ValueError):
                    sig_cls, sig_lbl = "sig-lean", "—"
            else:
                sig_cls, sig_lbl = "sig-lean", "—"

            warn_html = ('<span style="font-size:10px;color:var(--amber);margin-left:auto;">⚠ data warning</span>'
                         if has_warn else "")

            parts.append(f'''
<div class="ticket">
  <div class="ticket-inner">
    <div class="ticket-accent" style="background:linear-gradient(180deg,{accent},{accent}88);"></div>
    <div class="ticket-body">
      <div class="ticket-hdr">
        <span class="ticket-no">#{_h(ticket_no)}</span>
        <span class="{sig_cls}">{sig_lbl}</span>
        {warn_html}
      </div>
      <div class="kpi-row">
        <div class="kpi">
          <div class="kpi-label">Win Prob</div>
          <div class="kpi-val" style="color:var(--green);">{_pct(win_prob)}</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">Avg HR</div>
          <div class="kpi-val" style="color:var(--cyan);">{_pct(avg_hr)}</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">EV</div>
          <div class="kpi-val" style="color:var(--accent);">{_fmt(ev, 2)}×</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">Payout</div>
          <div class="kpi-val">{_fmt(t_power_pay, 1)}×</div>
        </div>
      </div>
      <table>
        <thead>
          <tr>
            <th>Player</th>
            <th>Sport</th>
            <th>Prop</th>
            <th>Line</th>
            <th>Dir</th>
            <th>Pick</th>
            <th>HR</th>
            <th>ML</th>
            <th>Edge</th>
            <th>Vs Def</th>
          </tr>
        </thead>
        <tbody>''')

            for leg in legs:
                player = leg.get("player") or ""
                sport = leg.get("sport") or ""
                prop_type = leg.get("prop_type") or ""
                line = leg.get("line")
                std_line = leg.get("standard_line")
                direction = (leg.get("direction") or "").upper()
                pick_type = (leg.get("pick_type") or "").strip()
                hit_rate = leg.get("hit_rate")
                ml_prob = leg.get("ml_prob")
                edge = leg.get("edge")
                def_tier = leg.get("def_tier") or ""
                team = leg.get("team") or ""
                opp = leg.get("opp") or ""
                initials = leg.get("initials") or player[:2].upper()

                # Direction badge
                dir_cls = "dir-over" if direction == "OVER" else "dir-under"
                dir_html = f'<span class="{dir_cls}">{_h(direction)}</span>'

                # Pick type badge
                pk_lower = pick_type.lower()
                pk_color = _PICK_COLOR.get(pk_lower, "#aaa")
                pick_html = f'<span style="font-size:11px;font-weight:700;color:{pk_color};">{_h(pick_type)}</span>'

                # Line display (show goblin discount if applicable)
                if std_line and line and abs(float(std_line) - float(line)) >= 0.1:
                    line_html = f'{_fmt(line, 1)} <span style="font-size:10px;color:var(--muted);text-decoration:line-through;">{_fmt(std_line, 1)}</span>'
                else:
                    line_html = _fmt(line, 1)

                # Sport accent chip
                s_accent = _sport_accent(sport)
                sport_html = f'<span style="font-size:10px;font-weight:700;color:{s_accent};background:{s_accent}22;padding:2px 6px;border-radius:4px;border:1px solid {s_accent}44;">{_h(sport)}</span>'

                # Avatar
                av_html = f'<div class="avatar">{_h(initials)}</div>'

                # Matchup sub-label
                matchup = f"{team} vs {opp}" if team and opp else (team or opp)

                parts.append(f'''
          <tr class="leg-row">
            <td>
              <div class="pwrap">
                {av_html}
                <div>
                  <div style="font-weight:600;font-size:12px;">{_h(player)}</div>
                  <div style="font-size:10px;color:var(--muted);">{_h(matchup)}</div>
                </div>
              </div>
            </td>
            <td>{sport_html}</td>
            <td style="color:var(--text);font-weight:500;">{_h(prop_type)}</td>
            <td style="font-family:'Share Tech Mono',monospace;">{line_html}</td>
            <td>{dir_html}</td>
            <td>{pick_html}</td>
            <td style="font-family:'Share Tech Mono',monospace;color:var(--green);">{_pct(hit_rate)}</td>
            <td style="font-family:'Share Tech Mono',monospace;color:var(--cyan);">{_pct(ml_prob)}</td>
            <td style="font-family:'Share Tech Mono',monospace;color:var(--accent);">{_fmt(edge, 2)}</td>
            <td style="font-size:11px;color:var(--muted);">{_h(def_tier)}</td>
          </tr>''')
                leg_graph_uid += 1
                parts.append(_tickets_leg_graph_row_html(leg, f"lgr-{leg_graph_uid}", table_cols))

            parts.append('''
        </tbody>
      </table>
    </div>
  </div>
</div>''')

        parts.append('</div>')  # end .group

    parts.append('</div>')  # end .tickets-built.shell

    # Inline JS: expand/collapse leg graph rows on click
    parts.append('''
<script>
(function(){
  document.querySelectorAll('.tickets-built .leg-row').forEach(function(row){
    row.addEventListener('click', function(){
      var next = row.nextElementSibling;
      if(next && next.classList.contains('leg-graph-row')){
        next.classList.toggle('open');
      }
    });
  });
})();
</script>''')

    return "".join(parts), page_title


if __name__ == "__main__":
    main()