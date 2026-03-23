#!/usr/bin/env python3
"""
combined_slate_tickets.py

Combined NBA + CBB + NHL + Soccer Slate & Ticket Generator
Merges NBA (step8_all_direction_clean.xlsx) and CBB (step6_ranked_cbb.xlsx ELIGIBLE)
Outputs:
  - combined_slate_tickets_YYYY-MM-DD.xlsx
  - tickets_latest.json / tickets_latest.html (web-friendly, static)
  - docs/tickets_latest.json / docs/tickets_latest.html (for GitHub Pages /docs)

Sheets: SUMMARY, Full Slate (reordered + STRONG/LEAN/RISK + pace beside Def Tier), NBA Slate, CBB Slate,
        NBA 3/4/5/6-Leg tickets (Goblin/Standard/Demon/Mix),
        CBB 3/4/5/6-Leg tickets, Combined 3/4/5/6-Leg tickets,
        Cross-sport Standard Mix, Cross-sport Goblin Mix

NEW (Web):
- Adds player headshot thumbnails when an ID is available:
    NBA: uses nba_player_id (if present) -> cdn.nba.com headshot
    CBB: uses espn_player_id (if present) -> espncdn headshot
  If no ID exists, it falls back to a simple initials avatar.
- JSON includes image_url per leg.
- More helpful file-path resolution (tries script dir + recursive search if file not found)

HOTFIX:
- Fixes crash when CBB "direction" becomes a DataFrame due to duplicate columns.
  We de-duplicate columns BEFORE touching df["direction"].str.upper().
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from typing import Optional, List

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# Repo root = parent of scripts/ (this file lives in scripts/)
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
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
DEFAULT_NHL_PATH = os.path.join(REPO_ROOT, "NHL", "step8_nhl_direction_clean.xlsx")
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
    "mix": "F5EEF8",
    "gold": "F9E79F",
}

PAYOUT = {
    2: {"power": 3.0,  "flex": 3.0},
    3: {"power": 6.0,  "flex": 3.0},   # Updated: power=6x, flex=3x
    4: {"power": 10.0, "flex": 6.0},
    5: {"power": 20.0, "flex": 10.0},
    6: {"power": 37.5, "flex": 25.0},  # Updated: power=37.5x, flex=25x
}

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
# Set slightly above 1.0 for a small profit margin cushion.
MIN_TICKET_EV: float = 1.05              # ← tune me (1.0 = break-even)

# 2026 NCAA tournament + AP Top 25 metadata (CBB enrichment).
# Keys follow abbreviations used in our CBB files.
CBB_TOURNEY_2026 = {
    "DUKE": (1, "East"), "CONN": (2, "East"), "MSU": (3, "East"), "KU": (4, "East"),
    "SJU": (5, "East"), "LOU": (6, "East"), "UCLA": (7, "East"), "OSU": (8, "East"),
    "TCU": (9, "East"), "UCF": (10, "East"), "USF": (11, "East"), "UNI": (12, "East"),
    "CBU": (13, "East"), "NDSU": (14, "East"), "FUR": (15, "East"), "SIEN": (16, "East"),
    "ARIZ": (1, "West"), "PUR": (2, "West"), "GONZ": (3, "West"), "ARK": (4, "West"),
    "WIS": (5, "West"), "BYU": (6, "West"), "MIA": (7, "West"), "VILL": (8, "West"),
    "UST": (9, "West"), "MIZZ": (10, "West"), "TEX": (11, "West"), "NCSU": (11, "West"),
    "HP": (12, "West"), "HAW": (13, "West"), "KSU": (14, "West"), "QUC": (15, "West"),
    "LIU": (16, "West"),
    "MICH": (1, "Midwest"), "ISU": (2, "Midwest"), "UVA": (3, "Midwest"), "ALA": (4, "Midwest"),
    "TTU": (5, "Midwest"), "TENN": (6, "Midwest"), "UK": (7, "Midwest"), "UGA": (8, "Midwest"),
    "SLU": (9, "Midwest"), "SCU": (10, "Midwest"), "M-OH": (11, "Midwest"), "SMU": (11, "Midwest"),
    "AKR": (12, "Midwest"), "HOF": (13, "Midwest"), "WRST": (14, "Midwest"), "TNST": (15, "Midwest"),
    "HOW": (16, "Midwest"), "UMBC": (16, "Midwest"),
    "FLA": (1, "South"), "HOU": (2, "South"), "ILL": (3, "South"), "NEB": (4, "South"),
    "VAN": (5, "South"), "UNC": (6, "South"), "SMC": (7, "South"), "CLEM": (8, "South"),
    "IOWA": (9, "South"), "TA&M": (10, "South"), "VCU": (11, "South"), "MCN": (12, "South"),
    "TROY": (13, "South"), "PENN": (14, "South"), "IDA": (15, "South"),
    "PV": (16, "South"), "LEH": (16, "South"),
}

CBB_AP_TOP25_2026 = {
    "DUKE": 1, "ARIZ": 2, "MICH": 3, "FLA": 4, "HOU": 5, "ISU": 6, "CONN": 7,
    "PUR": 8, "UVA": 9, "SJU": 10, "MSU": 11, "GONZ": 12, "ILL": 13, "ARK": 14,
    "NEB": 15, "VAN": 16, "KU": 17, "ALA": 18, "WIS": 19, "TTU": 20, "UNC": 21,
    "SMC": 22, "LOU": 23, "MIA": 23, "TENN": 25,
}


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

# ── Per-leg count quality thresholds (used by smart ticket builder) ───────────
# Min hit rate required per leg depending on ticket length
# Longer tickets need higher floor because win prob = product of all hit rates
LEG_MIN_HIT_RATE = {
    3: 0.58,   # 3-leg: 0.58^3 = 19.5% win prob floor
    4: 0.62,   # 4-leg: 0.62^4 = 14.8% win prob floor
    5: 0.65,   # 5-leg: 0.65^5 = 11.6% win prob floor
    6: 0.68,   # 6-leg: 0.68^6 = 9.8% win prob floor
}

# Min tier per leg count for Power mode tickets
POWER_MIN_TIER = {
    3: ["A", "B", "C"],   # 3-leg power: Tier A/B/C ok
    4: ["A", "B", "C"],   # 4-leg power: Tier A/B/C ok
    5: ["A", "B"],         # 5-leg power: Tier A/B only
    6: ["A", "B"],         # 6-leg power: Tier A/B only
}

# Cap fantasy-score concentration per ticket so slips are more diversified.
MAX_FANTASY_LEGS = {
    3: 1,
    4: 2,
    5: 2,
    6: 2,
}


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


def win_prob(hit_rates, _n_legs: int) -> float:
    vals = []
    for h in hit_rates:
        try:
            if h is None:
                continue
            if isinstance(h, float) and np.isnan(h):
                continue
            vals.append(float(h))
        except Exception:
            continue
    if not vals:
        return 0.0
    return float(np.prod(vals))


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
    """
    if df is None or df.empty:
        print(f"  [{sport} date] no rows to filter")
        return df
    if "game_time" not in df.columns:
        print(f"  [{sport} date] missing game_time column; keeping {len(df)} rows")
        return df

    out = df.copy()
    target_year = int(str(target_date)[:4])
    out["game_date"] = _extract_game_dates(out["game_time"], target_year)

    counts = out["game_date"].value_counts(dropna=True)
    if not counts.empty:
        top = ", ".join([f"{str(k)}={int(v)}" for k, v in counts.head(5).items()])
        print(f"  [{sport} date] available: {top}")
    else:
        print(f"  [{sport} date] no parseable game_date values; keeping {len(out)} rows")
        return out

    keep_mask = out["game_date"].eq(target_date)
    kept = int(keep_mask.sum())
    total = len(out)

    if kept == 0 and allow_cross_date_fallback:
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
                f"fallback enabled -> using {chosen} ({kept} rows)"
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
def _safe_float(x):
    try:
        if x is None:
            return None
        if isinstance(x, float) and np.isnan(x):
            return None
        return float(x)
    except Exception:
        return None


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


def ticket_groups_to_payload(all_ticket_groups, date_str, thresholds):
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "date": date_str,
        "filters": thresholds,
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
            rows = t.get("rows", [])
            slip = {
                "ticket_no": ti,
                "avg_hit_rate": _safe_float(t.get("avg_hit_rate")),
                "avg_rank_score": _safe_float(t.get("avg_rank_score")),
                "est_win_prob": _safe_float(t.get("est_win_prob")),
                "power_payout": _safe_float(t.get("power_payout")),
                "flex_payout": _safe_float(t.get("flex_payout")),
                "base_power_payout": _safe_float(t.get("base_power_payout")),
                "payout_multiplier": _safe_float(t.get("payout_multiplier")),
                "ev_power": _safe_float(t.get("ev_power")),
                "legs": [],
            }

            for row in rows:

                def gv(field):
                    return row.get(field, "") if isinstance(row, dict) else getattr(row, field, "")

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
                    "standard_line": _safe_float(gv("standard_line")),
                    "standard_edge": _safe_float(gv("standard_edge")),
                    "standard_projection": _safe_float(gv("standard_projection")),
                    "line_discount_vs_standard": _safe_float(gv("line_discount_vs_standard")),
                    "hit_rate": _safe_float(gv("hit_rate")),
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
                    "l10_over": _safe_float(gv("l10_over") or gv("L10 Over") or gv("hit_rate_over_L10") or gv("over_L10")),
                    "l10_under": _safe_float(gv("l10_under") or gv("L10 Under") or gv("hit_rate_under_L10") or gv("under_L10")),
                    "def_tier": str(gv("def_tier") or gv("Def Tier") or ""),
                    "pace_tier": str(gv("pace_tier") or gv("Pace Tier") or ""),
                    "context_score": _safe_float(gv("context_score")),
                }
                leg["image_url"] = compute_image_url(leg)
                leg["initials"] = player_initials(leg.get("player", ""))

                slip["legs"].append(leg)

            group["tickets"].append(slip)

        payload["groups"].append(group)

    return payload


def write_slate_json(nba, cbb, nhl, soccer, date_str, outdir: str):
    """Write full per-sport ranked slate to slate_latest.json for the web UI."""
    import math

    def safe(v):
        if v is None:
            return None
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
                "game_time":  str(g("game_time") or ""),
            })
        return rows

    payload = {
        "date": date_str,
        "sports": {
            "nba":    df_to_rows(nba,    "nba"),
            "cbb":    df_to_rows(cbb,    "cbb"),
            "nhl":    df_to_rows(nhl,    "nhl"),
            "soccer": df_to_rows(soccer, "soccer"),
        }
    }

    os.makedirs(outdir, exist_ok=True)
    out_path = os.path.join(outdir, "slate_latest.json")
    with open(out_path, "w", encoding="utf-8") as f:
        import json as _json
        _json.dump(payload, f, ensure_ascii=False, default=str)
    print(f"  slate_latest.json -> {out_path}  ({sum(len(v) for v in payload['sports'].values())} props)")


def write_web_outputs(payload, outdir: str):
    os.makedirs(outdir, exist_ok=True)
    json_path = os.path.join(outdir, "tickets_latest.json")
    html_path = os.path.join(outdir, "tickets_latest.html")

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

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

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
    date    = payload.get("date", "")

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
<title>PropOracle — Tickets</title>
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
    <div class="meta">Generated: {gen_at} &nbsp;|&nbsp; Date: {date}</div>
  </div>
</div>

<div class="filter-pill">
  Filters &rarr;
  <strong>tiers:</strong> {filters.get('tiers','ALL')} &nbsp;
  <strong>min_hit_rate:</strong> {filters.get('min_hit_rate',0)} &nbsp;
  <strong>min_edge:</strong> {filters.get('min_edge',0)} &nbsp;
  <strong>min_rank:</strong> {filters.get('min_rank','None')} &nbsp;
  <strong>pick_types:</strong> {filters.get('pick_types','ALL')}
  &nbsp;&nbsp;<a href="tickets_latest.json" style="color:var(--cyan);">⬇ JSON</a>
</div>
<div class="filter-pill" style="margin-top:-12px;">
  Quick read: <strong>STRONG</strong> means direction aligns with context (defense + pace + sample), <strong>LEAN</strong> means partial alignment, <strong>RISKY</strong> means weak context support.
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
          </div>
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
                    f"<td>{leg.get('pick_type','')}</td>"
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

    html_str = "\n".join(html_parts)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_str)

    print(f"[OK] Web JSON  -> {json_path}")
    print(f"[OK] Web HTML  -> {html_path}")


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
            "Projection": "projection",
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

    # Drop voids if present — BUT keep NO_PROJECTION_OR_LINE rows so that
    # shooting-split props (3-PT Made, Two Pointers Made, FT Made/Att, etc.)
    # appear in slate sheets for historical hit-rate tracking.
    # filter_eligible will still exclude them from tickets via tier/hit_rate filters.
    if "void_reason" in df.columns:
        if isinstance(df["void_reason"], pd.DataFrame):
            df["void_reason"] = df["void_reason"].iloc[:, 0]
        void_str = df["void_reason"].astype(str).str.strip()
        keep_mask = (
            df["void_reason"].isna()
            | (void_str == "")
            | (void_str == "NO_PROJECTION_OR_LINE")
        )
        df = df[keep_mask]

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
    df = df.astype(object).where(df.notna(), other=None)
    return df


# ── Merge to full slate ────────────────────────────────────────────────────────
def build_combined_slate(
    nba: pd.DataFrame,
    cbb: pd.DataFrame,
    nhl: pd.DataFrame = None,
    soccer: pd.DataFrame = None,
    nba1h: pd.DataFrame = None,
    nba1q: pd.DataFrame = None,
    wcbb: pd.DataFrame = None,
    mlb: pd.DataFrame = None,
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
        "projection",
        "hit_rate",
        "l5_avg",
        "season_avg",
        "l5_over",
        "l5_under",
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
        return df[[c for c in cols if c in df.columns]].copy()

    frames = [safe_keep(nba, keep), safe_keep(cbb, keep)]
    if nhl is not None and len(nhl) > 0:
        frames.append(safe_keep(nhl, keep))
    if soccer is not None and len(soccer) > 0:
        frames.append(safe_keep(soccer, keep))
    if nba1h is not None and len(nba1h) > 0:
        frames.append(safe_keep(nba1h, keep))
    if nba1q is not None and len(nba1q) > 0:
        frames.append(safe_keep(nba1q, keep))
    if wcbb is not None and len(wcbb) > 0:
        frames.append(safe_keep(wcbb, keep))
    if mlb is not None and len(mlb) > 0:
        frames.append(safe_keep(mlb, keep))
    combined = pd.concat(frames, ignore_index=True)

    if "rank_score" in combined.columns:
        combined["rank_score"] = pd.to_numeric(combined["rank_score"], errors="coerce")
    if "hit_rate" in combined.columns:
        combined["hit_rate"] = pd.to_numeric(combined["hit_rate"], errors="coerce")
    if "edge" in combined.columns:
        combined["edge"] = pd.to_numeric(combined["edge"], errors="coerce")

    combined = combined.sort_values("rank_score", ascending=False, na_position="last").reset_index(drop=True)
    return combined


# ── Filter eligible props for tickets ─────────────────────────────────────────
def filter_eligible(df: pd.DataFrame, min_hit_rate=0.55, min_edge=0.0, min_rank=None, tiers=None, pick_types=None):
    mask = pd.Series([True] * len(df), index=df.index)
    if "prop_type" in df.columns:
        # Temporarily exclude all fantasy-score props from tickets.
        mask &= ~df["prop_type"].astype(str).str.contains("fantasy", case=False, na=False)
    # Always exclude NO_PROJECTION_OR_LINE rows from tickets (no line = can't bet)
    if "void_reason" in df.columns:
        void_str = df["void_reason"].astype(str).str.strip()
        mask &= ~(void_str == "NO_PROJECTION_OR_LINE")
    if min_hit_rate > 0 and "hit_rate" in df.columns:
        mask &= df["hit_rate"].fillna(0) >= min_hit_rate
    if min_edge > 0 and "edge" in df.columns:
        mask &= df["edge"].fillna(0) >= min_edge
    if min_rank is not None and "rank_score" in df.columns:
        mask &= df["rank_score"].fillna(-99) >= min_rank
    if tiers and "tier" in df.columns:
        mask &= df["tier"].isin([t.upper() for t in tiers])
    if pick_types and "pick_type" in df.columns:
        mask &= df["pick_type"].isin(pick_types)
    return df[mask].copy()


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
        edge = _signal_float(leg.get("edge")) or 0.0
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
def build_tickets(pool: pd.DataFrame, n_legs: int, max_tickets=20, require_mix=False) -> list:
    """
    Smart ticket builder with quality filters per leg count.

    Key improvements vs original:
    - Per-leg min hit rate floor (longer tickets require higher floor)
    - Tier floor per leg count for longer tickets (5/6-leg = Tier A/B only)
    - Demon legs soft-filtered: excluded from 5/6-leg tickets, capped at 1 in 3/4-leg
    - Tickets sorted by est_win_prob DESC then avg_rank_score (optimises for actual wins)
    - require_mix still enforced for cross-sport sheets
    """
    pool = pool.copy().reset_index(drop=True)
    tickets = []

    # ── Per-leg-count quality filters ─────────────────────────────────────────
    min_hr   = LEG_MIN_HIT_RATE.get(n_legs, 0.55)
    ok_tiers = POWER_MIN_TIER.get(n_legs, ["A", "B", "C", "D"])

    # Apply hit rate floor to this pool
    if "hit_rate" in pool.columns:
        pool = pool[pool["hit_rate"].fillna(0) >= min_hr].copy()

    # Apply tier floor for 5/6-leg tickets
    if n_legs >= 5 and "tier" in pool.columns:
        pool = pool[pool["tier"].isin(ok_tiers)].copy()

    # For 5/6-leg tickets: remove Demon legs entirely (38% hit rate kills these)
    if n_legs >= 5 and "pick_type" in pool.columns:
        pool = pool[pool["pick_type"] != "Demon"].copy()

    pool = pool.reset_index(drop=True)

    has_sport_col = "sport" in pool.columns
    sports_available = pool["sport"].dropna().unique().tolist() if has_sport_col else []
    can_mix = require_mix and has_sport_col and len(sports_available) >= 2

    eligible = pool.sort_values("rank_score", ascending=False, na_position="last").reset_index(drop=True)
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

                    # Cap Demon legs at 1 per 3/4-leg ticket
                    if n_legs <= 4 and "pick_type" in row.index:
                        demon_count = sum(1 for r in ticket_rows
                                          if str(r.get("pick_type", "")) == "Demon")
                        if str(row.get("pick_type", "")) == "Demon" and demon_count >= 1:
                            continue
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
                for r in ticket_rows:
                    hrs.append(float(r.get("hit_rate", 0.5) or 0.5))
                    rss.append(float(r.get("rank_score", 0) or 0))
                avg_hr = float(np.mean(hrs)) if hrs else 0.0
                avg_rs = float(np.mean(rss)) if rss else 0.0
                ep = win_prob(hrs, n_legs)
                pout = PAYOUT.get(n_legs, {"power": 0, "flex": 0})

                # Adjust payouts for Goblin (reduces) and Demon (boosts)
                adj_power = calc_adjusted_payout(pout["power"], ticket_rows)
                adj_flex  = calc_adjusted_payout(pout["flex"],  ticket_rows)

                # EV gate: skip tickets with negative expected value
                ev_power = ep * adj_power
                if ev_power < MIN_TICKET_EV:
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
                        "n_legs": n_legs,
                    }
                )

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
def build_mixed_picktype_tickets(pool_df: pd.DataFrame, n_legs: int, max_tickets: int, min_standard: int) -> list:
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

    std = pool_df[pool_df["pick_type"] == "Standard"].sort_values("rank_score", ascending=False, na_position="last")
    gob = pool_df[pool_df["pick_type"] == "Goblin"].sort_values("rank_score", ascending=False, na_position="last")

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
        combined_ranked = combined_ranked.sort_values("rank_score", ascending=False, na_position="last")

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
                avg_hr = float(np.mean(hrs)) if hrs else 0.0
                avg_rs = float(np.mean(rss)) if rss else 0.0
                ep = win_prob(hrs, n_legs)
                pout = PAYOUT.get(n_legs, {"power": 0, "flex": 0})

                # Adjust payouts for Goblin/Demon legs
                adj_power = calc_adjusted_payout(pout["power"], legs)
                adj_flex  = calc_adjusted_payout(pout["flex"],  legs)

                # EV gate
                ev_power = ep * adj_power
                if ev_power < MIN_TICKET_EV:
                    std_start = min(std_start + 1, max(len(std) - 1, 0))
                    if len(gob) > 0:
                        gob_start = min(gob_start + 1, max(len(gob) - 1, 0))
                    continue

                key = frozenset((str(x.get("player", "")) + "|" + str(x.get("prop_type", ""))).strip() for x in legs)
                if key not in [t["key"] for t in tickets]:
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
                            "n_legs": n_legs,
                        }
                    )

        # Slide window to create different combos
        if len(std) > 0:
            std_start = min(std_start + 1, max(len(std) - 1, 0))
        if len(gob) > 0:
            gob_start = min(gob_start + 1, max(len(gob) - 1, 0))

    tickets.sort(key=lambda x: (-x["avg_rank_score"], -x["avg_hit_rate"]))
    return tickets[:max_tickets]


def build_final_web_ticket_groups(nba_pool: pd.DataFrame, cbb_pool: pd.DataFrame,
                                   nhl_pool: pd.DataFrame = None, soccer_pool: pd.DataFrame = None,
                                   min_hit_rate=0.70, min_edge=2.0, min_rank=5.0):
    def apply_filters(df):
        mask = pd.Series(True, index=df.index)
        if min_hit_rate > 0 and "hit_rate" in df.columns:
            mask &= df["hit_rate"].fillna(0) >= min_hit_rate
        if min_edge > 0 and "edge" in df.columns:
            mask &= df["edge"].fillna(0) >= min_edge
        if min_rank is not None and "rank_score" in df.columns:
            mask &= df["rank_score"].fillna(-99) >= min_rank
        return df[mask].copy()

    # ── NBA groups ─────────────────────────────────────────────────────────────
    nba_filtered = apply_filters(nba_pool)
    nba_mix = nba_filtered[nba_filtered["pick_type"].isin(["Standard", "Goblin"])].copy()
    nba_std = nba_filtered[nba_filtered["pick_type"].isin(["Standard"])].copy()

    groups = []

    if len(nba_mix) >= 6:
        t6 = build_mixed_picktype_tickets(nba_mix, 6, max_tickets=1, min_standard=2)
        if t6:
            groups.append(("FINAL 6-Leg (NBA Std+Gob)", t6, None))

    if len(nba_mix) >= 5:
        t5 = build_mixed_picktype_tickets(nba_mix, 5, max_tickets=1, min_standard=2)
        if t5:
            groups.append(("FINAL 5-Leg (NBA Std+Gob)", t5, None))

    if len(nba_mix) >= 4:
        t4 = build_mixed_picktype_tickets(nba_mix, 4, max_tickets=1, min_standard=2)
        if t4:
            groups.append(("FINAL 4-Leg (NBA Std+Gob)", t4, None))

    if len(nba_mix) >= 3:
        t3 = build_mixed_picktype_tickets(nba_mix, 3, max_tickets=2, min_standard=1)
        if t3:
            groups.append(("FINAL 3-Leg MIX (NBA Std+Gob)", t3, None))

    if len(nba_std) >= 3:
        groups.append(("FINAL 3-Leg STANDARD ONLY (NBA)", build_tickets(nba_std, 3, max_tickets=1), None))

    # ── CBB groups ─────────────────────────────────────────────────────────────
    if cbb_pool is not None and len(cbb_pool):
        cbb_filtered = apply_filters(cbb_pool)
        cbb_mix = cbb_filtered[cbb_filtered["pick_type"].isin(["Standard", "Goblin"])].copy() \
            if "pick_type" in cbb_filtered.columns else cbb_filtered.copy()
        cbb_std = cbb_filtered[cbb_filtered["pick_type"].isin(["Standard"])].copy() \
            if "pick_type" in cbb_filtered.columns else cbb_filtered.copy()

        if len(cbb_mix) >= 6:
            t6 = build_mixed_picktype_tickets(cbb_mix, 6, max_tickets=1, min_standard=2)
            if t6:
                groups.append(("FINAL 6-Leg (CBB Std+Gob)", t6, None))

        if len(cbb_mix) >= 5:
            t5 = build_mixed_picktype_tickets(cbb_mix, 5, max_tickets=1, min_standard=2)
            if t5:
                groups.append(("FINAL 5-Leg (CBB Std+Gob)", t5, None))

        if len(cbb_mix) >= 4:
            t4 = build_mixed_picktype_tickets(cbb_mix, 4, max_tickets=1, min_standard=2)
            if t4:
                groups.append(("FINAL 4-Leg (CBB Std+Gob)", t4, None))

        if len(cbb_mix) >= 3:
            t3 = build_mixed_picktype_tickets(cbb_mix, 3, max_tickets=2, min_standard=1)
            if t3:
                groups.append(("FINAL 3-Leg MIX (CBB Std+Gob)", t3, None))

        if len(cbb_std) >= 3:
            groups.append(("FINAL 3-Leg STANDARD ONLY (CBB)", build_tickets(cbb_std, 3, max_tickets=1), None))

    # ── NBA + CBB SPORT MIX groups ─────────────────────────────────────────────
    if cbb_pool is not None and len(cbb_pool):
        cbb_filtered = apply_filters(cbb_pool)
        cbb_mix_combo = cbb_filtered[cbb_filtered["pick_type"].isin(["Standard", "Goblin"])].copy() \
            if "pick_type" in cbb_filtered.columns else cbb_filtered.copy()
        combo = pd.concat([nba_mix, cbb_mix_combo], ignore_index=True)

        if len(combo) >= 6:
            t6 = build_mixed_picktype_tickets(combo, 6, max_tickets=1, min_standard=2)
            if t6:
                groups.append(("FINAL 6-Leg SPORT MIX (NBA+CBB)", t6, None))

        if len(combo) >= 5:
            t5 = build_mixed_picktype_tickets(combo, 5, max_tickets=1, min_standard=2)
            if t5:
                groups.append(("FINAL 5-Leg SPORT MIX (NBA+CBB)", t5, None))

        if len(combo) >= 4:
            t4 = build_mixed_picktype_tickets(combo, 4, max_tickets=1, min_standard=2)
            if t4:
                groups.append(("FINAL 4-Leg SPORT MIX (NBA+CBB)", t4, None))

        if len(combo) >= 3:
            t3 = build_mixed_picktype_tickets(combo, 3, max_tickets=2, min_standard=1)
            if t3:
                groups.append(("FINAL 3-Leg SPORT MIX (NBA+CBB)", t3, None))

    # ── NHL groups ─────────────────────────────────────────────────────────────
    if nhl_pool is not None and len(nhl_pool):
        nhl_f = apply_filters(nhl_pool)
        nhl_mix = nhl_f[nhl_f["pick_type"].isin(["Standard", "Goblin"])].copy() \
            if "pick_type" in nhl_f.columns else nhl_f.copy()
        nhl_std = nhl_f[nhl_f["pick_type"] == "Standard"].copy() \
            if "pick_type" in nhl_f.columns else nhl_f.copy()

        if len(nhl_mix) >= 6:
            t6 = build_mixed_picktype_tickets(nhl_mix, 6, max_tickets=1, min_standard=2)
            if t6:
                groups.append(("FINAL 6-Leg (NHL Std+Gob)", t6, None))
        if len(nhl_mix) >= 5:
            t5 = build_mixed_picktype_tickets(nhl_mix, 5, max_tickets=1, min_standard=2)
            if t5:
                groups.append(("FINAL 5-Leg (NHL Std+Gob)", t5, None))
        if len(nhl_mix) >= 4:
            t4 = build_mixed_picktype_tickets(nhl_mix, 4, max_tickets=1, min_standard=2)
            if t4:
                groups.append(("FINAL 4-Leg (NHL Std+Gob)", t4, None))
        if len(nhl_mix) >= 3:
            t3 = build_mixed_picktype_tickets(nhl_mix, 3, max_tickets=2, min_standard=1)
            if t3:
                groups.append(("FINAL 3-Leg MIX (NHL Std+Gob)", t3, None))
        if len(nhl_std) >= 3:
            groups.append(("FINAL 3-Leg STANDARD ONLY (NHL)", build_tickets(nhl_std, 3, max_tickets=1), None))

    # ── Soccer groups ──────────────────────────────────────────────────────────
    if soccer_pool is not None and len(soccer_pool):
        soc_f = apply_filters(soccer_pool)
        soc_mix = soc_f[soc_f["pick_type"].isin(["Standard", "Goblin"])].copy() \
            if "pick_type" in soc_f.columns else soc_f.copy()
        soc_std = soc_f[soc_f["pick_type"] == "Standard"].copy() \
            if "pick_type" in soc_f.columns else soc_f.copy()

        if len(soc_mix) >= 6:
            t6 = build_mixed_picktype_tickets(soc_mix, 6, max_tickets=1, min_standard=2)
            if t6:
                groups.append(("FINAL 6-Leg (Soccer Std+Gob)", t6, None))
        if len(soc_mix) >= 5:
            t5 = build_mixed_picktype_tickets(soc_mix, 5, max_tickets=1, min_standard=2)
            if t5:
                groups.append(("FINAL 5-Leg (Soccer Std+Gob)", t5, None))
        if len(soc_mix) >= 4:
            t4 = build_mixed_picktype_tickets(soc_mix, 4, max_tickets=1, min_standard=2)
            if t4:
                groups.append(("FINAL 4-Leg (Soccer Std+Gob)", t4, None))
        if len(soc_mix) >= 3:
            t3 = build_mixed_picktype_tickets(soc_mix, 3, max_tickets=2, min_standard=1)
            if t3:
                groups.append(("FINAL 3-Leg MIX (Soccer Std+Gob)", t3, None))
        if len(soc_std) >= 3:
            groups.append(("FINAL 3-Leg STANDARD ONLY (Soccer)", build_tickets(soc_std, 3, max_tickets=1), None))

    # ── All-sport cross-sport MIX ──────────────────────────────────────────────
    extra_frames = []
    if nhl_pool is not None and len(nhl_pool):
        nhl_f2 = apply_filters(nhl_pool)
        extra_frames.append(nhl_f2[nhl_f2["pick_type"].isin(["Standard", "Goblin"])].copy()
                            if "pick_type" in nhl_f2.columns else nhl_f2.copy())
    if soccer_pool is not None and len(soccer_pool):
        soc_f2 = apply_filters(soccer_pool)
        extra_frames.append(soc_f2[soc_f2["pick_type"].isin(["Standard", "Goblin"])].copy()
                            if "pick_type" in soc_f2.columns else soc_f2.copy())

    if extra_frames and cbb_pool is not None and len(cbb_pool):
        cbb_f3 = apply_filters(cbb_pool)
        cbb_m3 = cbb_f3[cbb_f3["pick_type"].isin(["Standard", "Goblin"])].copy() \
            if "pick_type" in cbb_f3.columns else cbb_f3.copy()
        all_sport_combo = pd.concat([nba_mix, cbb_m3] + extra_frames, ignore_index=True)

        if len(all_sport_combo) >= 6:
            t6 = build_mixed_picktype_tickets(all_sport_combo, 6, max_tickets=1, min_standard=2)
            if t6:
                groups.append(("FINAL 6-Leg ALL-SPORT MIX", t6, None))
        if len(all_sport_combo) >= 5:
            t5 = build_mixed_picktype_tickets(all_sport_combo, 5, max_tickets=1, min_standard=2)
            if t5:
                groups.append(("FINAL 5-Leg ALL-SPORT MIX", t5, None))
        if len(all_sport_combo) >= 4:
            t4 = build_mixed_picktype_tickets(all_sport_combo, 4, max_tickets=1, min_standard=2)
            if t4:
                groups.append(("FINAL 4-Leg ALL-SPORT MIX", t4, None))
        if len(all_sport_combo) >= 3:
            t3 = build_mixed_picktype_tickets(all_sport_combo, 3, max_tickets=2, min_standard=1)
            if t3:
                groups.append(("FINAL 3-Leg ALL-SPORT MIX", t3, None))

    return groups


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
    "direction",
    "edge",
    "projection",
    "hit_rate",
    "l5_avg",
    "season_avg",
    "l5_over",
    "l5_under",
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
SLATE_WIDTHS = [6, 5, 10, 20, 6, 6, 7, 10, 8, 7, 10, 8, 10, 18, 10, 6, 8, 7, 10, 10, 8, 10, 7, 7, 10, 9, 10, 10, 8, 9, 8, 10, 7, 8, 10, 16]
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
    "Dir",
    "Edge",
    "Proj",
    "Hit Rate",
    "L5 Avg",
    "Szn Avg",
    "L5 Over",
    "L5 Under",
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
}
FULL_SLATE_EXTRA_WIDTHS = {
    "pace_tier": 10,
    "bet_strong": 9,
    "bet_lean": 7,
    "bet_risk": 9,
    "game_script_mult": 12,
    "game_script_note": 42,
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
    "direction",
    "edge",
    "projection",
    "hit_rate",
    "l5_avg",
    "season_avg",
    "l5_over",
    "l5_under",
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
    "rank_score",
    "def_tier",
    "h2h_avg",
    "h2h_over_rate",
    "h2h_games",
    "b2b_flag",
    "cv_pct",
    "opp_vs_avg_pct",
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
    "Rank Score",
    "Def Tier",
    "H2H Avg",
    "H2H Over%",
    "H2H GP",
    "B2B",
    "CV%",
    "Opp vs Avg%",
    "Sport",
]
TICKET_W = [4, 20, 6, 6, 18, 10, 6, 6, 7, 9, 8, 9, 7, 8, 11, 10, 8, 9, 7, 7, 8, 10, 6]


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
        mult_label = (
            f"  ·  Payout Mult: {pay_mult:.2f}x (base {base_pout}x → adj {pout}x)"
            if abs(pay_mult - 1.0) > 0.001 else ""
        )
        banner = (
            f"  Ticket #{ti}  ·  {n}-Leg {label}  ·  "
            f"Power: {pout}x (${cost:.0f} to win $100)  ·  Flex: {fout}x  ·  "
            f"Avg Hit Rate: {avg_hr:.0%}  ·  Est Win Prob: {ep:.0%}  ·  EV: {ev_pow:.2f}  ·  "
            f"Avg Rank Score: {avg_rs:.2f}{mult_label}"
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
            rs = gv("rank_score")
            try:
                rs_out = round(float(rs), 2) if rs != "" and rs is not None else ""
            except Exception:
                rs_out = ""
            dc(ws, ri, 15, rs_out, bg=bg, bold=True)
            dc(ws, ri, 16, gv("def_tier"), bg=bg)
            # H2H Avg
            dc(ws, ri, 17, gv("h2h_avg"), bg=bg, align="center")
            # H2H Over%
            h2h_or = gv("h2h_over_rate")
            cell_h2h = dc(ws, ri, 18, h2h_or if h2h_or != "" else "", bg=bg, align="center")
            if h2h_or != "":
                try:
                    cell_h2h.number_format = "0.0%"
                except Exception:
                    pass
            # H2H GP
            dc(ws, ri, 19, gv("h2h_games"), bg=bg, align="center")
            # B2B
            b2b_raw = gv("b2b_flag")
            b2b_str = "YES" if str(b2b_raw).lower() in ("true", "1", "yes") else ("NO" if b2b_raw != "" else "")
            b2b_bg = C["miss"] if b2b_str == "YES" else bg
            dc(ws, ri, 20, b2b_str, bg=b2b_bg, bold=(b2b_str == "YES"), align="center",
               fc="FFFFFF" if b2b_str == "YES" else "000000")
            # CV%
            cv_val = gv("cv_pct")
            cell_cv = dc(ws, ri, 21, cv_val if cv_val != "" else "", bg=bg, align="center")
            if cv_val != "":
                try:
                    cell_cv.number_format = "0.0"
                except Exception:
                    pass
            # Opp vs Avg%
            opp_val = gv("opp_vs_avg_pct")
            cell_opp = dc(ws, ri, 22, opp_val if opp_val != "" else "", bg=bg, align="center")
            if opp_val != "":
                try:
                    cell_opp.number_format = "0.0%"
                except Exception:
                    pass
            # Sport (shifted to col 23)
            sv = gv("sport")
            sbg = C["hdr_nba"] if sv == "NBA" else (C["hdr_cbb"] if sv == "CBB" else C["hdr"])
            dc(ws, ri, 23, sv, bg=sbg, bold=True, fc="FFFFFF")
            ws.row_dimensions[ri].height = 14
            ri += 1

        ws.row_dimensions[ri].height = 6
        ri += 1


# ── Write SUMMARY sheet ───────────────────────────────────────────────────────
def write_summary(wb, nba, cbb, combined, all_ticket_groups, date_str, thresholds, nhl=None, soccer=None):
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
    ap.add_argument(
        "--mlb",
        default="",
        help=f"MLB step8 (default: {DEFAULT_MLB_PATH})",
    )
    ap.add_argument(
        "--nba1h",
        default="",
        help=f"NBA1H step8 (default: {DEFAULT_NBA1H_PATH})",
    )
    ap.add_argument(
        "--nba1q",
        default="",
        help=f"NBA1Q step8 (default: {DEFAULT_NBA1Q_PATH})",
    )
    ap.add_argument(
        "--wcbb",
        default="",
        help=f"WCBB step6 (default: {DEFAULT_WCBB_PATH})",
    )
    ap.add_argument("--output", default="")
    ap.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="Slate date YYYY-MM-DD, or 'today' / 'now'",
    )
    ap.add_argument("--tiers", default="A,B,C", help="Comma-separated tiers e.g. A,B")
    ap.add_argument("--min-hit-rate", type=float, default=0.55, dest="min_hit_rate")
    ap.add_argument("--min-edge", type=float, default=0.0, dest="min_edge")
    ap.add_argument("--min-rank", type=float, default=None, dest="min_rank")
    ap.add_argument("--pick-types", default="Goblin,Standard,Demon", dest="pick_types")  # Demon kept for Flex sheets; filtered out of 5/6-leg Power by build_tickets
    ap.add_argument("--max-tickets", type=int, default=20, dest="max_tickets")
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

    # Web outputs
    ap.add_argument("--write-web", action="store_true", help="Write tickets_latest.html/json for GitHub Pages")
    ap.add_argument(
        "--web-outdir",
        default=DEFAULT_WEB_OUTDIR,
        help="Folder to write tickets_latest.html/json",
    )
    ap.add_argument("--also-root", action="store_true", help="Also write tickets_latest.* in repo root")

    args = ap.parse_args()

    ds = str(args.date).strip().lower()
    if ds in ("today", "now"):
        args.date = datetime.now().strftime("%Y-%m-%d")

    if not str(args.nba).strip():
        args.nba = DEFAULT_NBA_PATH
    if not str(args.cbb).strip():
        args.cbb = DEFAULT_CBB_PATH
    if not str(args.nhl).strip():
        args.nhl = DEFAULT_NHL_PATH
    if not str(args.soccer).strip():
        args.soccer = DEFAULT_SOCCER_PATH
    if not str(args.mlb).strip():
        args.mlb = DEFAULT_MLB_PATH
    if not str(args.nba1h).strip():
        args.nba1h = DEFAULT_NBA1H_PATH
    if not str(args.nba1q).strip():
        args.nba1q = DEFAULT_NBA1Q_PATH
    if not str(args.wcbb).strip():
        args.wcbb = DEFAULT_WCBB_PATH

    if not args.output:
        args.output = f"combined_slate_tickets_{args.date}.xlsx"

    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()]
    pick_types = [p.strip() for p in args.pick_types.split(",") if p.strip()]
    thresholds = {
        "tiers": args.tiers,
        "min_hit_rate": args.min_hit_rate,
        "min_edge": args.min_edge,
        "min_rank": args.min_rank,
        "pick_types": args.pick_types,
        "context_filter": args.use_context_filter,
        "context_min_score": args.context_min_score,
        "context_min_l5_sample": args.context_min_l5_sample,
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

    mlb = None
    if str(args.mlb).strip():
        try:
            mlb = load_nba(args.mlb)
            mlb["sport"] = "MLB"
            mlb = enforce_target_date(
                mlb, "MLB", args.date, allow_cross_date_fallback=args.allow_cross_date_fallback
            )
            mlb = attach_standard_refs(mlb)
            print(f"  {len(mlb)} MLB props loaded")
            _load_audit_row("MLB", args.mlb, mlb)
        except Exception as e:
            print(f"  WARNING: Could not load MLB file: {e}")
            mlb = None
    else:
        print("  [MLB] skipped (empty --mlb)")

    nba1h = None
    if str(args.nba1h).strip():
        try:
            nba1h = load_nba(args.nba1h)
            nba1h["sport"] = "NBA1H"
            nba1h = enforce_target_date(
                nba1h, "NBA1H", args.date, allow_cross_date_fallback=args.allow_cross_date_fallback
            )
            nba1h = attach_standard_refs(nba1h)
            print(f"  {len(nba1h)} NBA1H props loaded")
            _load_audit_row("NBA1H", args.nba1h, nba1h)
        except Exception as e:
            print(f"  WARNING: Could not load NBA1H file: {e}")
            nba1h = None
    else:
        print("  [NBA1H] skipped (empty --nba1h)")

    nba1q = None
    if str(args.nba1q).strip():
        try:
            nba1q = load_nba(args.nba1q)
            nba1q["sport"] = "NBA1Q"
            nba1q = enforce_target_date(
                nba1q, "NBA1Q", args.date, allow_cross_date_fallback=args.allow_cross_date_fallback
            )
            nba1q = attach_standard_refs(nba1q)
            print(f"  {len(nba1q)} NBA1Q props loaded")
            _load_audit_row("NBA1Q", args.nba1q, nba1q)
        except Exception as e:
            print(f"  WARNING: Could not load NBA1Q file: {e}")
            nba1q = None
    else:
        print("  [NBA1Q] skipped (empty --nba1q)")

    wcbb = None
    if str(args.wcbb).strip():
        try:
            wcbb = load_cbb(args.wcbb)
            wcbb["sport"] = "WCBB"
            wcbb = enforce_target_date(
                wcbb, "WCBB", args.date, allow_cross_date_fallback=args.allow_cross_date_fallback
            )
            wcbb = attach_standard_refs(wcbb)
            print(f"  {len(wcbb)} WCBB props loaded")
            _load_audit_row("WCBB", args.wcbb, wcbb)
        except Exception as e:
            print(f"  WARNING: Could not load WCBB file: {e}")
            wcbb = None
    else:
        print("  [WCBB] skipped (empty --wcbb)")

    # ✅ Attach Standard sibling refs AFTER normalized columns exist
    nba = attach_standard_refs(nba)
    cbb = attach_standard_refs(cbb)

    print("Building combined slate...")
    combined = build_combined_slate(nba, cbb, nhl, soccer, nba1h, nba1q, wcbb, mlb)

    # ✅ Attach Standard refs for combined too
    combined = attach_standard_refs(combined)

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
        sport = str(df["sport"].iloc[0]).upper() if "sport" in df.columns and len(df) > 0 else ""
        # Apply tighter rank floor specifically for CBB Goblin
        effective_min_rank = args.min_rank
        if sport in ("CBB", "WCBB") and pt is not None and pt == ["Goblin"]:
            effective_min_rank = max(args.min_rank or 0, CBB_GOBLIN_MIN_RANK)
        base = filter_eligible(
            df,
            args.min_hit_rate,
            args.min_edge,
            effective_min_rank,
            tiers if tiers else None,
            pt if pt is not None else pick_types,
        )
        return apply_nba_context_confidence_filter(
            base,
            enabled=args.use_context_filter,
            min_context_score=args.context_min_score,
            min_l5_sample=args.context_min_l5_sample,
        )

    nba_pool = pool(nba)
    cbb_pool = pool(cbb)
    combo_pool = pool(combined)
    print(f"  NBA eligible: {len(nba_pool)} | CBB eligible: {len(cbb_pool)} | Combined: {len(combo_pool)}")
    print(f"  CBB Goblin rank floor: {CBB_GOBLIN_MIN_RANK} (NBA uses global floor: {args.min_rank})")

    print("Generating tickets + workbook...")
    wb = Workbook()
    wb.remove(wb.active)

    all_ticket_groups = []
    leg_sizes = [3, 4, 5, 6]

    def gen_tickets(pool_df, sport_label, bg_hdr, sport_prefix, pick_type_filter=None):
        rows_out = []
        for n in leg_sizes:
            sub_pool = pool_df if pick_type_filter is None else pool_df[pool_df["pick_type"].isin([pick_type_filter])]
            tickets = build_tickets(sub_pool, n, args.max_tickets)
            if tickets:
                pt_label = pick_type_filter or "Mix"
                sheet_name = f"{sport_prefix} {pt_label} {n}-Leg"[:31] if pick_type_filter else f"{sport_prefix} Mix {n}-Leg"[:31]
                write_ticket_sheet(wb, tickets, sheet_name, bg_hdr, label=f"{sport_label} {pt_label}")
                rows_out.append((sheet_name, tickets, None))
                print(f"  {sheet_name}: {len(tickets)} tickets")
        return rows_out

    # NBA tickets by pick type
    for pt in ["Goblin", "Standard", "Demon"]:
        pt_pool = pool(nba, [pt])
        if len(pt_pool) >= 3:
            all_ticket_groups += gen_tickets(pt_pool, "NBA", C["hdr_nba"], "NBA", pt)

    # NBA Mix
    if len(nba_pool) >= 3:
        all_ticket_groups += gen_tickets(nba_pool, "NBA", C["hdr_nba"], "NBA Mix")

    # CBB tickets by pick type
    for pt in ["Goblin", "Standard", "Demon"]:
        pt_pool = pool(cbb, [pt])
        if len(pt_pool) >= 3:
            all_ticket_groups += gen_tickets(pt_pool, "CBB", C["hdr_cbb"], "CBB", pt)

    # CBB Mix
    if len(cbb_pool) >= 3:
        all_ticket_groups += gen_tickets(cbb_pool, "CBB", C["hdr_cbb"], "CBB Mix")


    # NHL tickets
    if nhl is not None and len(nhl) > 0:
        nhl_pool = pool(nhl)
        if len(nhl_pool) >= 3:
            for pt in ["Goblin", "Standard", "Demon"]:
                pt_pool = pool(nhl, [pt])
                if len(pt_pool) >= 3:
                    all_ticket_groups += gen_tickets(pt_pool, "NHL", C["hdr_nhl"], "NHL", pt)
            all_ticket_groups += gen_tickets(nhl_pool, "NHL", C["hdr_nhl"], "NHL Mix")

    # Soccer tickets
    if soccer is not None and len(soccer) > 0:
        soccer_pool = pool(soccer)
        if len(soccer_pool) >= 3:
            for pt in ["Goblin", "Standard", "Demon"]:
                pt_pool = pool(soccer, [pt])
                if len(pt_pool) >= 3:
                    all_ticket_groups += gen_tickets(pt_pool, "Soccer", C["hdr_soccer"], "Soccer", pt)
            all_ticket_groups += gen_tickets(soccer_pool, "Soccer", C["hdr_soccer"], "Soccer Mix")

    # Combined NBA+CBB tickets (all pick types mixed)
    if len(combo_pool) >= 3:
        all_ticket_groups += gen_tickets(combo_pool, "COMBO", C["hdr_mix"], "COMBO")

    # Cross-sport Standard Mix (enforce mix) — NBA + CBB + Soccer when available
    nba_std = pool(nba, ["Standard"])
    cbb_std = pool(cbb, ["Standard"])
    mix_parts = [nba_std, cbb_std]
    if soccer is not None and len(soccer) > 0:
        mix_parts.append(pool(soccer, ["Standard"]))
    std_mix_pool = pd.concat(mix_parts, ignore_index=True).sort_values("rank_score", ascending=False)
    if len(std_mix_pool) >= 3:
        print("Generating cross-sport Standard Mix tickets...")
        for n in leg_sizes:
            tickets = build_tickets(std_mix_pool, n, args.max_tickets, require_mix=True)
            if tickets:
                sheet_name = f"MIX Standard {n}-Leg"[:31]
                mix_lbl = "NBA+CBB+Soccer Standard" if (soccer is not None and len(soccer) > 0) else "NBA+CBB Standard"
                write_ticket_sheet(wb, tickets, sheet_name, C["hdr_mix"], label=mix_lbl)
                all_ticket_groups.append((sheet_name, tickets, C["mix"]))
                print(f"  {sheet_name}: {len(tickets)} tickets")

    # Cross-sport Goblin Mix — RETIRED
    # Data showed 0% win rate across all dates. NBA Goblin (67%) and CBB Goblin (55%)
    # dilute each other in multi-leg tickets. Pure NBA Goblin sheets outperform.
    # MIX Goblin sheets are no longer generated.

    print("Writing slate sheets...")
    # Strict-mode guardrail: fail if mixed dates survived filtering.
    if not args.allow_cross_date_fallback:
        to_check = [
            ("NBA", nba),
            ("CBB", cbb),
            ("NHL", nhl),
            ("Soccer", soccer),
            ("Combined", combined),
        ]
        mixed = []
        for label, sdf in to_check:
            if sdf is None or len(sdf) == 0 or "game_date" not in sdf.columns:
                continue
            bad = sdf[sdf["game_date"].astype(str) != args.date]
            if len(bad) > 0:
                cts = bad["game_date"].astype(str).value_counts().to_dict()
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

    write_summary(wb, nba, cbb, combined, all_ticket_groups, args.date, thresholds, nhl=nhl, soccer=soccer)

    # Reorder: put SUMMARY + slate sheets at the front
    desired_first = ["SUMMARY", "Full Slate", "NBA Slate", "CBB Slate", "NHL Slate", "Soccer Slate"]
    for sname in reversed(desired_first):
        if sname in wb.sheetnames:
            wb.move_sheet(wb[sname], offset=-(len(wb.sheetnames) - 1))

    wb.save(args.output)
    print(f"\n[OK] Saved -> {args.output}")
    print(f"   Sheets ({len(wb.sheetnames)}): {wb.sheetnames}")

    # Web output (FINAL only)
    if args.write_web:
        print("\nWriting GitHub Pages web outputs (FINAL tickets only)...")
        nhl_pool_web   = pool(nhl)    if nhl    is not None and len(nhl)    > 0 else None
        soccer_pool_web= pool(soccer) if soccer is not None and len(soccer) > 0 else None
        final_groups = build_final_web_ticket_groups(
            nba_pool, cbb_pool,
            nhl_pool=nhl_pool_web,
            soccer_pool=soccer_pool_web,
            min_hit_rate=thresholds.get("min_hit_rate", 0.70),
            min_edge=thresholds.get("min_edge", 2.0),
            min_rank=thresholds.get("min_rank", 5.0),
        )
        payload = ticket_groups_to_payload(final_groups, args.date, thresholds)
        write_web_outputs(payload, args.web_outdir)
        write_slate_json(nba, cbb, nhl, soccer, args.date, args.web_outdir)
        if args.also_root:
            write_web_outputs(payload, outdir=".")
        print("[OK] Web outputs complete (FINAL only).")


if __name__ == "__main__":
    main()