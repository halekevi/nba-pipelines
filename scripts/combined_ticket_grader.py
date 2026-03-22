#!/usr/bin/env python3
r"""
combined_ticket_grader_UPDATED.py
================================
Full analytics grader for the output of combined_slate_tickets.py, with **dynamic payout modifiers**
for Goblin/Demon legs based on "distance from line" buckets (dev levels).

Key upgrade vs v1:
- Supports leg types like:
    Standard
    Goblin -1 / -2 / -3   (more discounted => lower payout)
    Demon  +1 / +2 / +3   (more juiced     => higher payout)
- If your ticket workbook only has pick_type = Goblin/Demon (no dev), we default to:
    Goblin -> Goblin -1
    Demon  -> Demon +1
- Payout multipliers are computed like your payout_calculator.jsx:
    Power: POWER_BASE[n] * Π(leg_modifier_power)
    Flex : FLEX_BASE[n][hits] * Π(leg_modifier_flex)

Outputs:
- SUMMARY (key metrics)
- ANALYSIS_INSIGHTS (readable takeaways from the slate)
- TICKET_RESULTS (one row per ticket per mode)
- LEG_RESULTS (one row per leg, with HIT/MISS/PUSH/NO_ACTUAL)
- LEG_BY_* breakdowns (prop, sport, direction, pick type, sheet)
- TICKET_DEEP_DIVE (per-ticket leg mix + outcomes)
- Analytics tabs per mode (ROI by sheet/legs/sports/pick_types)
- ML_* (optional): RandomForest leg hit model, feature importance, filter simulation
  (exploratory — trained on the same graded slate; use for patterns, not live edge claims)

Usage (PowerShell):
  py -3.14 .\\combined_ticket_grader_UPDATED.py `
    --tickets .\combined_slate_tickets_2026-02-21.xlsx `
    --nba_actuals ".\grades\actuals_nba_2026-02-21.csv" `
    --cbb_actuals ".\grades\actuals_cbb_2026-02-21.csv" `
    --mode both --stake 20

Optional config override:
  py -3.14 .\\combined_ticket_grader_UPDATED.py ... --payouts_json .\grades\payouts_2026-02-21.json

JSON schema (example):
{
  "power_base": { "2":3.0, "3":6.0, "4":10.0, "5":20.0, "6":37.5 },
  "flex_base": {
    "2": { "2":3.0 },
    "3": { "3":3.0, "2":1.0 },
    "4": { "4":6.0, "3":1.5 },
    "5": { "5":10.0, "4":2.0, "3":0.4 },
    "6": { "6":25.0, "5":2.0, "4":0.4 }
  },
  "mods": {
    "goblin_power": { "1":0.84, "2":0.747, "3":0.707 },
    "goblin_flex":  { "1":0.80, "2":0.720, "3":0.600 },
    "demon_power":  { "1":1.627,"2":2.40,  "3":2.72  },
    "demon_flex":   { "1":1.60, "2":1.520, "3":1.560 }
  }
}
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


# -----------------------------
# Defaults
# -----------------------------
POWER_BASE = {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0, 6: 37.5}
FLEX_BASE = {
    2: {2: 3.0},
    3: {3: 3.0, 2: 1.0},
    4: {4: 6.0, 3: 1.5},
    5: {5: 10.0, 4: 2.0, 3: 0.4},
    6: {6: 25.0, 5: 2.0, 4: 0.4},
}

# Modifiers by deviation bucket (dev=1 closest to standard, dev=3 furthest)
GOBLIN_POWER = {1: 0.840, 2: 0.747, 3: 0.707}
GOBLIN_FLEX  = {1: 0.800, 2: 0.720, 3: 0.600}
DEMON_POWER  = {1: 1.627, 2: 2.400, 3: 2.720}
DEMON_FLEX   = {1: 1.600, 2: 1.520, 3: 1.560}


# -----------------------------
# Normalization helpers
# -----------------------------
def strip_norm(s: str) -> str:
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def pick_category_from_cell(pick_type: str) -> str:
    s = strip_norm(pick_type or "")
    if "goblin" in s:
        return "Goblin"
    if "demon" in s:
        return "Demon"
    return "Standard"


def nhl_player_aliases(player: str) -> List[str]:
    """
    Generate NHL-friendly name aliases for matching ticket names against
    API sources that often abbreviate first names (e.g., "Timo Meier" vs "T. Meier").
    """
    p = strip_norm(player)
    if not p:
        return []
    parts = [x for x in p.split(" ") if x]
    aliases = [p]
    if len(parts) >= 2:
        first = parts[0]
        last = " ".join(parts[1:])
        aliases.append(f"{first[:1]}. {last}".strip())
        aliases.append(f"{first[:1]} {last}".strip())
    # preserve order, unique only
    seen = set()
    out = []
    for a in aliases:
        if a and a not in seen:
            seen.add(a)
            out.append(a)
    return out


def prop_norm_from_label(prop: str) -> str:
    p = strip_norm(prop)
    # NHL normalization (ticket labels vs actuals CSV wording differ)
    if "shots on goal" in p or p == "shots_on_goal":
        return "shots_on_goal"
    if "faceoff" in p and "won" in p:
        return "faceoffs_won"

    # Soccer normalization (ticket tokens vs actuals CSV wording differ)
    if "goalie saves" in p or "goalkeeper saves" in p:
        return "goalie saves"
    if "shots on target (combo)" in p:
        return "shots on target"
    if "shots on target" in p:
        return "shots on target"
    if p == "shots":
        # Soccer actuals appear to use a generic "shots" label.
        return "shots on target"

    if "pts+reb+ast" in p or p == "pra":
        return "pra"
    if "pts+reb" in p or p == "pr":
        return "pr"
    if "pts+ast" in p or p == "pa":
        return "pa"
    if "reb+ast" in p or p == "ra":
        return "ra"
    if "points" in p or p == "pts":
        return "points"
    if "rebounds" in p or p == "reb":
        return "rebounds"
    if "assists" in p or p == "ast":
        return "assists"
    if "turnover" in p or p == "tov":
        return "turnovers"
    if "blocked" in p or p == "blk":
        return "blocks"
    if "steal" in p or p == "stl":
        return "steals"
    if "fantasy" in p:
        return "fantasy"
    if any(x in p for x in ["3pm", "3-pointers made", "3 pointers made", "3pt made", "threes made"]):
        return "3pm"
    if any(x in p for x in ["3pa", "3-point attempts", "3 pointers attempted", "3pt att", "threes attempted"]):
        return "3pa"
    if "field goal attempts" in p or p == "fga":
        return "fga"
    if "field goals made" in p or p == "fgm":
        return "fgm"
    if "free throw attempts" in p or p == "fta":
        return "fta"
    if "free throws made" in p or p == "ftm":
        return "ftm"
    return p


def prop_norm_from_actual(prop_type: str) -> str:
    return prop_norm_from_label(prop_type)


# -----------------------------
# Ticket parsing
# -----------------------------
TICKET_RE = re.compile(r"ticket\s*#\s*(\d+)", re.IGNORECASE)

def derive_leg_type(pick_type_cell: str) -> str:
    """
    Convert workbook pick_type into a dev-bucketed leg type.
    Accepts:
      - "Standard"
      - "Goblin" -> "Goblin -1" (default)
      - "Demon"  -> "Demon +1"  (default)
      - "Goblin -2" / "Demon +3" (pass through)
    """
    s = (pick_type_cell or "").strip()
    if not s:
        return "Standard"
    s_norm = strip_norm(s)
    if "goblin" in s_norm:
        # if dev already included like "-2"
        m = re.search(r"-(\d+)", s_norm)
        dev = int(m.group(1)) if m else 1
        dev = max(1, min(3, dev))
        return f"Goblin -{dev}"
    if "demon" in s_norm:
        m = re.search(r"\+(\d+)", s_norm)
        dev = int(m.group(1)) if m else 1
        dev = max(1, min(3, dev))
        return f"Demon +{dev}"
    return "Standard"


def parse_ticket_sheet(tickets_xlsx: Path, sheet_name: str) -> pd.DataFrame:
    df = pd.read_excel(tickets_xlsx, sheet_name=sheet_name, dtype=object)
    if df.empty:
        return pd.DataFrame()

    first_col = df.columns[0]
    df = df.copy()
    df.rename(columns={first_col: "ticket_header"}, inplace=True)

    # Column positions in Excel can change across writers/exports, so we key off
    # header names instead of hard-coded indices. This fixes missing `sport`
    # (and therefore missing NBA/Soccer aggregates).
    def _norm_col(c) -> str:
        return re.sub(r"\s+", " ", str(c)).strip().lower()

    col_idx = {_norm_col(c): i for i, c in enumerate(df.columns)}

    # Expected headers from combined_slate_tickets.py
    idx_player = col_idx.get("player", 1)
    idx_team = col_idx.get("team", 2)
    idx_prop = col_idx.get("prop", 4)
    idx_line = col_idx.get("line", 6)
    idx_dir = col_idx.get("dir", 7)
    idx_pick_type = col_idx.get("pick type", 5)
    idx_def_tier = col_idx.get("def tier", 15)
    idx_sport = col_idx.get("sport", 22)

    rows = []
    i = 0
    while i < len(df):
        hdr = df.at[i, "ticket_header"]
        ticket_no = None
        if isinstance(hdr, str):
            m = TICKET_RE.search(hdr)
            if m:
                ticket_no = int(m.group(1))
        if ticket_no is None:
            i += 1
            continue

        # Find header row (2nd column == "Player")
        j = i + 1
        while j < len(df):
            c1 = df.iloc[j, idx_player] if df.shape[1] > idx_player else None
            if isinstance(c1, str) and strip_norm(c1) == "player":
                break
            nxt = df.at[j, "ticket_header"]
            if isinstance(nxt, str) and TICKET_RE.search(nxt):
                break
            j += 1

        if j >= len(df) or not (
            isinstance(df.iloc[j, idx_player], str) and strip_norm(df.iloc[j, idx_player]) == "player"
        ):
            i += 1
            continue

        k = j + 1
        leg_no = 0
        while k < len(df):
            player = df.iloc[k, idx_player] if df.shape[1] > idx_player else None
            if pd.isna(player) or strip_norm(player) == "":
                break
            if isinstance(player, str) and strip_norm(player) == "player":
                k += 1
                continue

            team = df.iloc[k, idx_team] if df.shape[1] > idx_team else ""
            prop = df.iloc[k, idx_prop] if df.shape[1] > idx_prop else ""
            line = df.iloc[k, idx_line] if df.shape[1] > idx_line else np.nan
            direction = df.iloc[k, idx_dir] if df.shape[1] > idx_dir else ""
            pick_type = df.iloc[k, idx_pick_type] if df.shape[1] > idx_pick_type else ""
            tier = df.iloc[k, idx_def_tier] if df.shape[1] > idx_def_tier else ""
            sport = df.iloc[k, idx_sport] if df.shape[1] > idx_sport else ""

            line_num = pd.to_numeric(line, errors="coerce")
            if pd.isna(line_num):
                k += 1
                continue

            leg_no += 1
            leg_type = derive_leg_type("" if pd.isna(pick_type) else str(pick_type))

            rows.append({
                "sheet": sheet_name,
                "ticket_no": ticket_no,
                "leg_no": leg_no,
                "player": str(player),
                "team": str(team) if not pd.isna(team) else "",
                "prop": str(prop) if not pd.isna(prop) else "",
                "prop_norm": prop_norm_from_label(prop),
                "line": float(line_num),
                "dir": str(direction).strip().upper() if not pd.isna(direction) else "",
                "sport": str(sport).strip().upper() if not pd.isna(sport) else "",
                "pick_type": str(pick_type) if not pd.isna(pick_type) else "",
                "leg_type": leg_type,
                "tier": str(tier) if not pd.isna(tier) else "",
            })
            k += 1

        i = k + 1

    return pd.DataFrame(rows)


# -----------------------------
# Actuals loading + lookup
# -----------------------------
def prep_actuals(csv_path: Path, sport_label: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    required = {"player", "team", "prop_type", "actual"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{sport_label} actuals missing columns: {sorted(missing)}. Found: {list(df.columns)}")

    df["actual"] = pd.to_numeric(df["actual"], errors="coerce")
    df = df.dropna(subset=["actual"]).copy()

    df["player_norm"] = df["player"].map(strip_norm)
    df["team_norm"] = df["team"].map(strip_norm)
    df["prop_norm"] = df["prop_type"].map(prop_norm_from_actual)
    return df


def build_lookup(act: pd.DataFrame):
    by_player_prop: Dict[Tuple[str, str], List[dict]] = {}
    by_player_team_prop: Dict[Tuple[str, str, str], List[dict]] = {}
    for _, r in act.iterrows():
        key1 = (r["player_norm"], r["prop_norm"])
        key2 = (r["player_norm"], r["team_norm"], r["prop_norm"])
        by_player_prop.setdefault(key1, []).append(r.to_dict())
        by_player_team_prop.setdefault(key2, []).append(r.to_dict())
    return by_player_prop, by_player_team_prop


def lookup_actual(sport: str, player: str, team: str, prop_norm: str,
                  nba_lpt, nba_lp,
                  cbb_lpt, cbb_lp,
                  nhl_lpt=None, nhl_lp=None,
                  soccer_lpt=None, soccer_lp=None) -> float:
    sport = (sport or "").upper()
    player_n = strip_norm(player)
    team_n = strip_norm(team)
    if sport == "NBA":
        key2 = (player_n, team_n, prop_norm)
        if key2 in nba_lpt:
            return float(nba_lpt[key2][0]["actual"])
        key1 = (player_n, prop_norm)
        if key1 in nba_lp:
            return float(nba_lp[key1][0]["actual"])
        return np.nan

    if sport == "CBB":
        # FIX 5: For CBB, team in ticket is an abbreviation (e.g. "COLO") but actuals
        # may use a different format. Try player+prop first (most reliable), then
        # team-keyed as secondary. This is the reverse of NBA priority for CBB.
        key1 = (player_n, prop_norm)
        if key1 in cbb_lp:
            return float(cbb_lp[key1][0]["actual"])
        key2 = (player_n, team_n, prop_norm)
        if key2 in cbb_lpt:
            return float(cbb_lpt[key2][0]["actual"])
        return np.nan

    if sport == "NHL":
        if nhl_lpt is None or nhl_lp is None:
            return np.nan
        # NHL feeds often abbreviate first names in actuals (e.g. "T. Meier"),
        # while tickets contain full names (e.g. "Timo Meier"), so try aliases.
        for pn in nhl_player_aliases(player):
            key2 = (pn, team_n, prop_norm)
            if key2 in nhl_lpt:
                return float(nhl_lpt[key2][0]["actual"])
            key1 = (pn, prop_norm)
            if key1 in nhl_lp:
                return float(nhl_lp[key1][0]["actual"])
        return np.nan

    if sport == "SOCCER":
        if soccer_lpt is None or soccer_lp is None:
            return np.nan
        key2 = (player_n, team_n, prop_norm)
        if key2 in soccer_lpt:
            return float(soccer_lpt[key2][0]["actual"])
        key1 = (player_n, prop_norm)
        if key1 in soccer_lp:
            return float(soccer_lp[key1][0]["actual"])
        return np.nan

    return np.nan


# -----------------------------
# Grading + payout modifiers
# -----------------------------
def grade_leg(dir_: str, line: float, actual: float) -> str:
    if pd.isna(actual):
        return "NO_ACTUAL"
    if abs(actual - line) < 1e-9:
        return "PUSH"
    d = (dir_ or "").upper()
    if d == "OVER":
        return "HIT" if actual > line else "MISS"
    if d == "UNDER":
        return "HIT" if actual < line else "MISS"
    return "UNKNOWN_DIR"


def leg_modifiers(leg_types: List[str]) -> Tuple[float, float]:
    """
    Returns (power_mod, flex_mod) computed as product of per-leg modifiers.
    """
    power_mod = 1.0
    flex_mod = 1.0
    for lt in leg_types:
        s = strip_norm(lt)
        if s.startswith("goblin"):
            m = re.search(r"-(\d+)", s)
            dev = int(m.group(1)) if m else 1
            dev = max(1, min(3, dev))
            power_mod *= float(GOBLIN_POWER.get(dev, 0.84))
            flex_mod  *= float(GOBLIN_FLEX.get(dev, 0.80))
        elif s.startswith("demon"):
            m = re.search(r"\+(\d+)", s)
            dev = int(m.group(1)) if m else 1
            dev = max(1, min(3, dev))
            power_mod *= float(DEMON_POWER.get(dev, 1.627))
            flex_mod  *= float(DEMON_FLEX.get(dev, 1.60))
        else:
            # Standard
            pass
    return power_mod, flex_mod


def compute_ticket_payout(stake: float, mode: str,
                          legs: int, hits: int, misses: int, pushes: int, no_actual: int,
                          power_mod: float, flex_mod: float) -> Tuple[float, str, float]:
    """
    Returns (payout_amount, payout_status, applied_multiplier).
    payout_amount includes stake (total returned). profit = payout - stake.
    """
    if no_actual > 0:
        return np.nan, "NO_ACTUAL", np.nan

    effective_legs = legs - pushes
    if effective_legs <= 1:
        return stake, "REFUND", 1.0

    if mode == "power":
        if misses == 0:
            base = float(POWER_BASE.get(effective_legs, 0.0))
            mult = float(round(base * power_mod, 4))
            payout = stake * mult
            return payout, "WIN" if mult > 0 else "WIN_NO_MULT", mult
        return 0.0, "LOSE", 0.0

    if mode == "flex":
        base_table = FLEX_BASE.get(effective_legs, {})
        base = float(base_table.get(hits, 0.0))
        mult = float(round(base * flex_mod, 4)) if base > 0 else 0.0
        if mult == 0.0:
            return 0.0, "LOSE", 0.0
        return stake * mult, "CASH", mult

    raise ValueError(f"Unknown mode: {mode}")


# -----------------------------
# Analytics helpers
# -----------------------------
def pivot_roi(df: pd.DataFrame, group_cols: List[str], prefix: str) -> pd.DataFrame:
    g = (df
         .groupby(group_cols, dropna=False, as_index=False)
         .agg(
            tickets=("ticket_id", "nunique"),
            staked=("stake", "sum"),
            payout=("payout", "sum"),
            profit=("profit", "sum"),
            win_rate=("is_win", "mean"),
            cash_rate=("is_cash", "mean"),
            no_actual=("no_actual", "sum"),
         ))
    g["roi"] = np.where(g["staked"] > 0, g["profit"] / g["staked"], np.nan)
    g["win_rate"] = g["win_rate"].round(4)
    g["cash_rate"] = g["cash_rate"].round(4)
    g["roi"] = g["roi"].round(4)
    g = g.sort_values(["profit", "roi"], ascending=False).reset_index(drop=True)
    g.insert(0, "view", prefix)
    return g


def build_summary_kv(overall: dict) -> pd.DataFrame:
    return pd.DataFrame([{"metric": k, "value": v} for k, v in overall.items()])


# --- Extra breakdown tables -------------------------------------------------

def _leg_segment_hit_rate(legs_df: pd.DataFrame, group_cols: List[str]) -> pd.DataFrame:
    b = legs_df[legs_df["leg_result"].isin(["HIT", "MISS"])].copy()
    if b.empty:
        return pd.DataFrame(columns=group_cols + ["graded_legs", "hits", "leg_hit_rate"])
    b["is_hit"] = (b["leg_result"] == "HIT").astype(int)
    g = (
        b.groupby(group_cols, dropna=False, as_index=False)
        .agg(graded_legs=("is_hit", "count"), hits=("is_hit", "sum"), leg_hit_rate=("is_hit", "mean"))
    )
    g["leg_hit_rate"] = g["leg_hit_rate"].round(4)
    g["misses"] = g["graded_legs"] - g["hits"]
    return g.sort_values("graded_legs", ascending=False).reset_index(drop=True)


def build_leg_breakdown_tables(legs_df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    df = legs_df.copy()
    df["pick_cat"] = df["pick_type"].astype(str).map(pick_category_from_cell)
    out: Dict[str, pd.DataFrame] = {
        "LEG_BY_PROP_SPORT": _leg_segment_hit_rate(df, ["sport", "prop_norm"]),
        "LEG_BY_PROP": _leg_segment_hit_rate(df, ["prop_norm"]),
        "LEG_BY_SPORT": _leg_segment_hit_rate(df, ["sport"]),
        "LEG_BY_DIR": _leg_segment_hit_rate(df, ["dir"]),
        "LEG_BY_PICK_CAT": _leg_segment_hit_rate(df, ["pick_cat"]),
        "LEG_BY_SHEET": _leg_segment_hit_rate(df, ["sheet"]),
        "LEG_BY_SPORT_PICK": _leg_segment_hit_rate(df, ["sport", "pick_cat"]),
    }
    noa = legs_df["leg_result"].eq("NO_ACTUAL").groupby(legs_df["sport"]).sum().reset_index(name="no_actual_legs")
    noa = noa.sort_values("no_actual_legs", ascending=False)
    out["LEG_NO_ACTUAL_BY_SPORT"] = noa
    return out


def build_ticket_deep_dive(ticket_results: pd.DataFrame, legs_df: pd.DataFrame) -> pd.DataFrame:
    """One row per ticket_id + mode with compact leg outcome string and mix stats."""
    leg_order = legs_df.sort_values(["ticket_id", "leg_no"])
    pieces = []
    for tid, g in leg_order.groupby("ticket_id"):
        props = "|".join(g["prop_norm"].astype(str).head(6).tolist())
        if len(g) > 6:
            props += "|..."
        res = "".join(
            {"HIT": "W", "MISS": "L", "PUSH": "P", "NO_ACTUAL": "?"}.get(x, "?")
            for x in g["leg_result"].tolist()
        )
        pieces.append(
            {
                "ticket_id": tid,
                "sheet": g["sheet"].iloc[0],
                "ticket_no": int(g["ticket_no"].iloc[0]) if pd.notna(g["ticket_no"].iloc[0]) else "",
                "n_legs": len(g),
                "n_hit": int((g["leg_result"] == "HIT").sum()),
                "n_miss": int((g["leg_result"] == "MISS").sum()),
                "n_push": int((g["leg_result"] == "PUSH").sum()),
                "n_no_actual": int((g["leg_result"] == "NO_ACTUAL").sum()),
                "sports_mix": ",".join(sorted({str(s) for s in g["sport"].tolist() if str(s).strip()})),
                "pick_mix": ",".join(sorted({pick_category_from_cell(x) for x in g["pick_type"].tolist()})),
                "result_chain": res,
                "props_sample": props,
            }
        )
    deep = pd.DataFrame(pieces)
    if deep.empty:
        return deep
    tr = ticket_results.copy()
    deep_m = deep.drop(columns=[c for c in ("sheet", "ticket_no") if c in deep.columns], errors="ignore")
    return tr.merge(deep_m, on="ticket_id", how="left")


def build_analysis_insights(
    overall: dict,
    ticket_results: pd.DataFrame,
    leg_breakdowns: Dict[str, pd.DataFrame],
) -> pd.DataFrame:
    rows: List[Dict[str, str]] = []
    for mode in ("power", "flex"):
        roi_k = f"{mode}_roi"
        if roi_k not in overall:
            continue
        rows.append(
            {
                "topic": f"{mode.upper()} summary",
                "detail": (
                    f"ROI {overall.get(roi_k, '')} | profit ${overall.get(mode + '_profit', '')} "
                    f"on ${overall.get(mode + '_staked', '')} staked | "
                    f"win/cash rate {overall.get(mode + '_win_rate', '')} / {overall.get(mode + '_cash_rate', '')}"
                ),
            }
        )

    sub = ticket_results[ticket_results["payout_status"] != "NO_ACTUAL"].copy()
    if not sub.empty:
        best = (
            sub.groupby(["mode", "sheet"], as_index=False)["profit"]
            .sum()
            .sort_values(["mode", "profit"], ascending=[True, False])
        )
        for mode in best["mode"].unique():
            top = best[best["mode"] == mode].head(3)
            for _, r in top.iterrows():
                rows.append(
                    {
                        "topic": f"Top ticket sheets ({mode})",
                        "detail": f"{r['sheet']}: total profit ${float(r['profit']):.2f}",
                    }
                )

    by_prop = leg_breakdowns.get("LEG_BY_PROP_SPORT", pd.DataFrame())
    if by_prop is not None and not by_prop.empty and "graded_legs" in by_prop.columns:
        enough = by_prop[by_prop["graded_legs"] >= 8].copy()
        if not enough.empty:
            worst = enough.nsmallest(3, "leg_hit_rate")
            best = enough.nlargest(3, "leg_hit_rate")
            for _, r in worst.iterrows():
                rows.append(
                    {
                        "topic": "Leg segments to review (low hit%, n>=8)",
                        "detail": f"{r['sport']} {r['prop_norm']}: hit rate {float(r['leg_hit_rate']):.1%} "
                        f"({int(r['hits'])}/{int(r['graded_legs'])})",
                    }
                )
            for _, r in best.iterrows():
                rows.append(
                    {
                        "topic": "Leg segments that hit (n>=8)",
                        "detail": f"{r['sport']} {r['prop_norm']}: hit rate {float(r['leg_hit_rate']):.1%}",
                    }
                )

    rows.append(
        {
            "topic": "How to use ML tabs",
            "detail": (
                "ML_* uses RandomForest on graded legs (HIT vs MISS) for sport/prop/direction/pick mix. "
                "ML_FILTER_SIM_* compares ROI if you only kept tickets with above-median model leg strength — "
                "same-day only; combine with historical exports before changing ticket rules."
            ),
        }
    )
    return pd.DataFrame(rows)


# --- Excel styling ----------------------------------------------------------

_HDR_FILL = PatternFill(start_color="1C2833", end_color="1C2833", fill_type="solid")
_HDR_FONT = Font(color="FFFFFF", bold=True, size=10)
_THIN = Side(style="thin", color="CCCCCC")
_CELL_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_HIT_FILL = PatternFill(start_color="27AE60", end_color="27AE60", fill_type="solid")
_MISS_FILL = PatternFill(start_color="C0392B", end_color="C0392B", fill_type="solid")
_PUSH_FILL = PatternFill(start_color="F39C12", end_color="F39C12", fill_type="solid")
_NA_FILL = PatternFill(start_color="BDC3C7", end_color="BDC3C7", fill_type="solid")
_PROFIT_POS = PatternFill(start_color="D5F5E3", end_color="D5F5E3", fill_type="solid")
_PROFIT_NEG = PatternFill(start_color="FADBD8", end_color="FADBD8", fill_type="solid")


def _header_cell(ws, row: int, col: int):
    cell = ws.cell(row=row, column=col)
    cell.fill = _HDR_FILL
    cell.font = _HDR_FONT
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = _CELL_BORDER


def _col_index_by_header(ws, name: str, header_row: int = 1) -> Optional[int]:
    for c in range(1, ws.max_column + 1):
        v = ws.cell(row=header_row, column=c).value
        if v is not None and str(v).strip() == str(name).strip():
            return c
    return None


def apply_graded_workbook_styles(wb) -> None:
    """Bold headers, freeze panes, borders, column widths, conditional leg/profit colors."""
    for ws in wb.worksheets:
        if ws.max_row < 1 or ws.max_column < 1:
            continue
        ws.freeze_panes = "A2"
        ws.row_dimensions[1].height = 28

        for c in range(1, ws.max_column + 1):
            _header_cell(ws, 1, c)
            letter = get_column_letter(c)
            maxlen = len(str(ws.cell(1, c).value or ""))
            for r in range(2, min(ws.max_row + 1, 500)):
                maxlen = max(maxlen, len(str(ws.cell(r, c).value or "")))
            ws.column_dimensions[letter].width = float(min(max(maxlen + 2, 9), 52))

        for r in range(2, ws.max_row + 1):
            for c in range(1, ws.max_column + 1):
                ws.cell(r, c).border = _CELL_BORDER
                ws.cell(r, c).alignment = Alignment(vertical="center", wrap_text=False)

        title = ws.title
        if title == "TICKET_RESULTS":
            pc = _col_index_by_header(ws, "profit")
            if pc:
                for r in range(2, ws.max_row + 1):
                    cell = ws.cell(r, pc)
                    try:
                        v = float(cell.value)
                    except (TypeError, ValueError):
                        continue
                    cell.number_format = "#,##0.00"
                    if v > 0:
                        cell.fill = _PROFIT_POS
                    elif v < 0:
                        cell.fill = _PROFIT_NEG
        elif title == "LEG_RESULTS":
            lc = _col_index_by_header(ws, "leg_result")
            if lc:
                for r in range(2, ws.max_row + 1):
                    cell = ws.cell(r, lc)
                    lr = str(cell.value or "").upper().strip()
                    if lr == "HIT":
                        cell.fill = _HIT_FILL
                        cell.font = Font(color="FFFFFF", bold=True, size=10)
                    elif lr == "MISS":
                        cell.fill = _MISS_FILL
                        cell.font = Font(color="FFFFFF", bold=True, size=10)
                    elif lr == "PUSH":
                        cell.fill = _PUSH_FILL
                        cell.font = Font(color="000000", bold=True, size=10)
                    elif lr == "NO_ACTUAL":
                        cell.fill = _NA_FILL
        elif title == "ANALYSIS_INSIGHTS":
            for r in range(2, ws.max_row + 1):
                ws.cell(r, 1).alignment = Alignment(wrap_text=True, vertical="top")
                ws.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="top")
            ws.column_dimensions["A"].width = 28
            ws.column_dimensions["B"].width = 90


# --- ML (exploratory) -------------------------------------------------------

def run_ml_profit_layers(
    legs_df: pd.DataFrame,
    ticket_results: pd.DataFrame,
    min_graded_legs: int = 40,
    random_state: int = 42,
) -> Dict[str, Any]:
    """
    Train a leg-level hit classifier and simulate ticket filtering by mean predicted leg hit prob.
    In-sample on this workbook only — for discovery, not production edge.
    """
    out: Dict[str, Any] = {"sheets": {}, "meta": []}
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import cross_val_score
    except ImportError:
        out["sheets"]["ML_SKIPPED"] = pd.DataFrame(
            [{"reason": "scikit-learn not installed (pip install scikit-learn)"}]
        )
        return out

    train = legs_df[legs_df["leg_result"].isin(["HIT", "MISS"])].copy()
    if len(train) < min_graded_legs:
        out["sheets"]["ML_SKIPPED"] = pd.DataFrame(
            [{"reason": f"Only {len(train)} graded legs (need {min_graded_legs}+ for stable ML)"}]
        )
        return out

    train["pick_cat"] = train["pick_type"].astype(str).map(pick_category_from_cell)
    top_props = train["prop_norm"].value_counts().head(14).index.tolist()
    train["prop_bucket"] = train["prop_norm"].where(train["prop_norm"].isin(top_props), "other")

    feat_cols = ["sport", "prop_bucket", "dir", "pick_cat"]
    X = pd.get_dummies(train[feat_cols].fillna(""), drop_first=False)
    y = (train["leg_result"] == "HIT").astype(int).values

    n_splits = min(5, max(2, len(train) // 25))
    rf = RandomForestClassifier(
        n_estimators=120,
        max_depth=10,
        min_samples_leaf=4,
        random_state=random_state,
        class_weight="balanced_subsample",
        n_jobs=1,
    )
    rf.fit(X.values, y)
    cv_acc = cross_val_score(rf, X.values, y, cv=n_splits, scoring="accuracy")
    try:
        cv_auc = cross_val_score(rf, X.values, y, cv=n_splits, scoring="roc_auc")
    except ValueError:
        cv_auc = np.array([np.nan])

    imp = pd.DataFrame({"feature": X.columns, "importance": rf.feature_importances_})
    imp = imp.sort_values("importance", ascending=False).reset_index(drop=True)
    imp["importance_pct"] = (imp["importance"] / imp["importance"].sum() * 100).round(2)
    out["sheets"]["ML_FEATURE_IMPORTANCE"] = imp

    out["sheets"]["ML_MODEL_META"] = pd.DataFrame(
        [
            {"metric": "cv_accuracy_mean", "value": round(float(np.mean(cv_acc)), 4)},
            {"metric": "cv_accuracy_std", "value": round(float(np.std(cv_acc)), 4)},
            {"metric": "cv_roc_auc_mean", "value": round(float(np.mean(cv_auc)), 4)},
            {"metric": "graded_legs", "value": len(train)},
            {"metric": "n_splits", "value": n_splits},
            {
                "metric": "note",
                "value": "Trained on this slate only; use rolling history for real policy changes.",
            },
        ]
    )

    # Predict per leg, aggregate to ticket
    full = legs_df.copy()
    full["pick_cat"] = full["pick_type"].astype(str).map(pick_category_from_cell)
    full["prop_bucket"] = full["prop_norm"].where(full["prop_norm"].isin(top_props), "other")
    X_full = pd.get_dummies(full[feat_cols].fillna(""), drop_first=False)
    X_full = X_full.reindex(columns=X.columns, fill_value=0)
    full["ml_p_hit"] = rf.predict_proba(X_full.values)[:, 1]

    ticket_strength = full.groupby("ticket_id", as_index=False).agg(
        ml_avg_leg_hit_prob=("ml_p_hit", "mean"),
        ml_min_leg_hit_prob=("ml_p_hit", "min"),
    )
    tr_enriched = ticket_results.merge(ticket_strength, on="ticket_id", how="left")

    sim_rows = []
    for mode in tr_enriched["mode"].dropna().unique():
        m = tr_enriched[tr_enriched["mode"] == mode].copy()
        el = m[m["payout_status"] != "NO_ACTUAL"]
        if el.empty or el["ml_avg_leg_hit_prob"].isna().all():
            continue
        med = el["ml_avg_leg_hit_prob"].median()
        base_profit = float(el["profit"].sum())
        base_stake = float(el["stake"].sum())
        filt = el[el["ml_avg_leg_hit_prob"] >= med]
        sim_rows.append(
            {
                "mode": mode,
                "filter": f"ml_avg_leg_hit_prob >= median ({med:.3f})",
                "tickets_all": int(len(el)),
                "tickets_kept": int(len(filt)),
                "staked_all": base_stake,
                "staked_filtered": float(filt["stake"].sum()),
                "profit_all": base_profit,
                "profit_filtered": float(filt["profit"].sum()),
                "roi_all": round(base_profit / base_stake, 4) if base_stake > 0 else 0.0,
                "roi_filtered": round(float(filt["profit"].sum()) / float(filt["stake"].sum()), 4)
                if float(filt["stake"].sum()) > 0
                else 0.0,
            }
        )
    out["sheets"]["ML_FILTER_SIM"] = pd.DataFrame(sim_rows)

    # Actionable-ish: which one-hot features associate with higher hit rate in raw data
    lift_rows = []
    base_rate = float(y.mean())
    for col in X.columns:
        mask = X[col].values.astype(bool)
        if mask.sum() < 8:
            continue
        hr = float(y[mask].mean())
        lift_rows.append(
            {
                "segment": col,
                "n_legs": int(mask.sum()),
                "hit_rate": round(hr, 4),
                "lift_vs_baseline": round((hr - base_rate) / max(base_rate, 0.01), 4),
            }
        )
    lift_df = pd.DataFrame(lift_rows).sort_values("lift_vs_baseline", ascending=False)
    out["sheets"]["ML_SEGMENT_LIFT"] = lift_df.head(40).reset_index(drop=True)

    return out


# -----------------------------
# Main
# -----------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickets", required=True, help="combined_slate_tickets_YYYY-MM-DD.xlsx")
    ap.add_argument("--nba_actuals", required=True, help="actuals_nba_YYYY-MM-DD.csv")
    ap.add_argument("--cbb_actuals", required=True, help="actuals_cbb_YYYY-MM-DD.csv")
    ap.add_argument("--nhl_actuals", default="", help="actuals_nhl_YYYY-MM-DD.csv (optional, for NHL legs)")
    ap.add_argument("--soccer_actuals", default="", help="actuals_soccer_YYYY-MM-DD.csv (optional, for Soccer legs)")
    ap.add_argument("--out", default="", help="Output graded workbook (default: <tickets>_GRADED.xlsx)")
    ap.add_argument("--mode", choices=["power", "flex", "both"], default="both")
    ap.add_argument("--stake", type=float, default=20.0)
    ap.add_argument("--payouts_json", default="", help="Optional JSON override for base payouts + modifiers")
    ap.add_argument("--no-ml", action="store_true", help="Skip ML analysis sheets (faster)")
    ap.add_argument("--ml-min-legs", type=int, default=40, help="Min graded legs to run ML (default 40)")
    args = ap.parse_args()

    global POWER_BASE, FLEX_BASE, GOBLIN_POWER, GOBLIN_FLEX, DEMON_POWER, DEMON_FLEX

    if args.payouts_json:
        cfg = json.loads(Path(args.payouts_json).read_text(encoding="utf-8"))
        if "power_base" in cfg:
            POWER_BASE = {int(k): float(v) for k, v in cfg["power_base"].items()}
        if "flex_base" in cfg:
            FLEX_BASE = {int(n): {int(k): float(v) for k, v in tab.items()} for n, tab in cfg["flex_base"].items()}
        mods = cfg.get("mods", {})
        if "goblin_power" in mods:
            GOBLIN_POWER = {int(k): float(v) for k, v in mods["goblin_power"].items()}
        if "goblin_flex" in mods:
            GOBLIN_FLEX = {int(k): float(v) for k, v in mods["goblin_flex"].items()}
        if "demon_power" in mods:
            DEMON_POWER = {int(k): float(v) for k, v in mods["demon_power"].items()}
        if "demon_flex" in mods:
            DEMON_FLEX = {int(k): float(v) for k, v in mods["demon_flex"].items()}

    tickets_xlsx = Path(args.tickets)
    nba_csv = Path(args.nba_actuals)
    cbb_csv = Path(args.cbb_actuals)
    out_xlsx = Path(args.out) if args.out else tickets_xlsx.with_name(tickets_xlsx.stem + "_GRADED.xlsx")

    # actuals + lookups
    nba_act = prep_actuals(nba_csv, "NBA")
    cbb_act = prep_actuals(cbb_csv, "CBB")
    nba_lp, nba_lpt = build_lookup(nba_act)
    cbb_lp, cbb_lpt = build_lookup(cbb_act)

    nhl_lp = nhl_lpt = None
    soccer_lp = soccer_lpt = None
    if args.nhl_actuals:
        nhl_csv = Path(args.nhl_actuals)
        nhl_act = prep_actuals(nhl_csv, "NHL")
        nhl_lp, nhl_lpt = build_lookup(nhl_act)
    if args.soccer_actuals:
        soccer_csv = Path(args.soccer_actuals)
        soccer_act = prep_actuals(soccer_csv, "SOCCER")
        soccer_lp, soccer_lpt = build_lookup(soccer_act)

    # ticket sheets
    xls = pd.ExcelFile(tickets_xlsx)
    ticket_sheets = [s for s in xls.sheet_names if re.search(r"\b\d-?Leg\b", s, re.IGNORECASE)]

    leg_frames = []
    for s in ticket_sheets:
        legs = parse_ticket_sheet(tickets_xlsx, s)
        if not legs.empty:
            leg_frames.append(legs)
    if not leg_frames:
        raise RuntimeError("No ticket legs parsed. Check sheet format.")

    legs_df = pd.concat(leg_frames, ignore_index=True)
    legs_df["ticket_id"] = legs_df["sheet"].astype(str) + " | " + legs_df["ticket_no"].astype(str)

    # grade legs
    legs_df["actual"] = legs_df.apply(
        lambda r: lookup_actual(
            r["sport"], r["player"], r["team"], r["prop_norm"],
            nba_lpt, nba_lp,
            cbb_lpt, cbb_lp,
            nhl_lpt=nhl_lpt, nhl_lp=nhl_lp,
            soccer_lpt=soccer_lpt, soccer_lp=soccer_lp
        ),
        axis=1,
    )
    legs_df["leg_result"] = legs_df.apply(lambda r: grade_leg(r["dir"], r["line"], r["actual"]), axis=1)

    # per-ticket modifiers
    mods_df = (legs_df.groupby("ticket_id", as_index=False)
               .agg(
                    leg_types=("leg_type", lambda s: list(s)),
                    sports=("sport", lambda s: ",".join(sorted(set([x for x in s if str(x).strip()])))),
                    pick_types=("pick_type", lambda s: ",".join(sorted(set([x for x in s if str(x).strip()])))),
                    tiers=("tier", lambda s: ",".join(sorted(set([x for x in s if str(x).strip()])))),
                ))
    mods_df[["power_mod", "flex_mod"]] = mods_df["leg_types"].apply(lambda L: pd.Series(leg_modifiers(L)))

    # ticket base stats
    ticket_base = (legs_df
        .groupby(["sheet", "ticket_no", "ticket_id"], as_index=False)
        .agg(
            legs=("leg_no", "max"),
            hits=("leg_result", lambda s: int((s == "HIT").sum())),
            misses=("leg_result", lambda s: int((s == "MISS").sum())),
            pushes=("leg_result", lambda s: int((s == "PUSH").sum())),
            no_actual=("leg_result", lambda s: int((s == "NO_ACTUAL").sum())),
        ))
    ticket_base["effective_legs"] = ticket_base["legs"] - ticket_base["pushes"]
    ticket_base["stake"] = float(args.stake)
    ticket_base = ticket_base.merge(mods_df[["ticket_id", "sports", "pick_types", "tiers", "power_mod", "flex_mod"]], on="ticket_id", how="left")

    modes = ["power", "flex"] if args.mode == "both" else [args.mode]
    ticket_rows = []
    for mode in modes:
        t = ticket_base.copy()
        payouts_out = []
        statuses = []
        mults = []
        for _, r in t.iterrows():
            payout_amt, status, mult = compute_ticket_payout(
                stake=float(r["stake"]),
                mode=mode,
                legs=int(r["legs"]),
                hits=int(r["hits"]),
                misses=int(r["misses"]),
                pushes=int(r["pushes"]),
                no_actual=int(r["no_actual"]),
                power_mod=float(r["power_mod"]),
                flex_mod=float(r["flex_mod"]),
            )
            payouts_out.append(payout_amt)
            statuses.append(status)
            mults.append(mult)
        t["mode"] = mode
        t["payout_status"] = statuses
        t["applied_mult"] = mults
        t["payout"] = payouts_out
        t["profit"] = t["payout"] - t["stake"]
        t["is_win"] = ((t["payout_status"] == "WIN") | (t["payout_status"] == "WIN_NO_MULT")).astype(int)
        t["is_cash"] = ((t["payout_status"] == "WIN") | (t["payout_status"] == "WIN_NO_MULT") | (t["payout_status"] == "CASH")).astype(int)
        ticket_rows.append(t)

    ticket_results = pd.concat(ticket_rows, ignore_index=True)

    # overall stats
    overall = {}
    for mode in modes:
        sub = ticket_results[ticket_results["mode"] == mode].copy()
        eligible = sub[sub["payout_status"] != "NO_ACTUAL"]
        overall[f"{mode}_tickets"] = int(sub["ticket_id"].nunique())
        overall[f"{mode}_eligible_tickets"] = int(eligible["ticket_id"].nunique())
        overall[f"{mode}_no_actual_tickets"] = int((sub["payout_status"] == "NO_ACTUAL").sum())
        overall[f"{mode}_staked"] = float(eligible["stake"].sum())
        overall[f"{mode}_payout"] = float(eligible["payout"].sum())
        overall[f"{mode}_profit"] = float(eligible["profit"].sum())
        overall[f"{mode}_roi"] = round(float(eligible["profit"].sum() / eligible["stake"].sum()) if eligible["stake"].sum() > 0 else 0.0, 4)
        overall[f"{mode}_win_rate"] = round(float(eligible["is_win"].mean()) if len(eligible) else 0.0, 4)
        overall[f"{mode}_cash_rate"] = round(float(eligible["is_cash"].mean()) if len(eligible) else 0.0, 4)

    tables = {}
    for mode in modes:
        sub = ticket_results[ticket_results["mode"] == mode].copy()
        eligible = sub[sub["payout_status"] != "NO_ACTUAL"].copy()
        tables[f"{mode}_BY_SHEET"] = pivot_roi(eligible, ["sheet"], f"{mode}_BY_SHEET")
        tables[f"{mode}_BY_LEGS"] = pivot_roi(eligible, ["effective_legs"], f"{mode}_BY_LEGS")
        tables[f"{mode}_BY_SPORTS"] = pivot_roi(eligible, ["sports"], f"{mode}_BY_SPORTS")
        tables[f"{mode}_BY_PICK_TYPES"] = pivot_roi(eligible, ["pick_types"], f"{mode}_BY_PICK_TYPES")

    summary_kv = build_summary_kv(overall)

    leg_breakdowns = build_leg_breakdown_tables(legs_df)
    insights_df = build_analysis_insights(overall, ticket_results, leg_breakdowns)
    deep_df = build_ticket_deep_dive(ticket_results, legs_df)

    ml_pack: Dict[str, Any] = {}
    if not args.no_ml:
        ml_pack = run_ml_profit_layers(
            legs_df,
            ticket_results,
            min_graded_legs=int(args.ml_min_legs),
        )

    with pd.ExcelWriter(out_xlsx, engine="openpyxl") as xw:
        summary_kv.to_excel(xw, index=False, sheet_name="SUMMARY")
        insights_df.to_excel(xw, index=False, sheet_name="ANALYSIS_INSIGHTS")
        ticket_results.to_excel(xw, index=False, sheet_name="TICKET_RESULTS")
        legs_df.to_excel(xw, index=False, sheet_name="LEG_RESULTS")
        if deep_df is not None and not deep_df.empty:
            deep_df.to_excel(xw, index=False, sheet_name="TICKET_DEEP_DIVE")
        for name, tab in leg_breakdowns.items():
            if tab is not None and not tab.empty:
                tab.to_excel(xw, index=False, sheet_name=str(name)[:31])
        if not args.no_ml:
            for name, tab in ml_pack.get("sheets", {}).items():
                if tab is not None and not tab.empty:
                    tab.to_excel(xw, index=False, sheet_name=str(name)[:31])
        for name, tab in tables.items():
            tab.to_excel(xw, index=False, sheet_name=str(name)[:31])
        apply_graded_workbook_styles(xw.book)

    # Keep this ASCII-only so the script runs in non-UTF8 consoles.
    print(f"Wrote graded workbook -> {out_xlsx}")


if __name__ == "__main__":
    main()
