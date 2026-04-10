#!/usr/bin/env python3
"""
Manual payout data entry: suggest legs from NBA step8, print 15 ticket configs,
prompt for PrizePicks slip values, save CSV for fit_payout_formula.py.
No browser/CDP — terminal only.

Leg selection is intentionally simple: valid board rows only (no scores).
Each player appears in at most one suggested ticket.
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = ROOT / "data" / "payout_samples"


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _pick_col(df: pd.DataFrame, names: list[str]) -> str | None:
    m = {str(c).strip().lower(): c for c in df.columns}
    for n in names:
        if n.lower() in m:
            return m[n.lower()]
    return None


def _norm_pick_type(raw: Any) -> str | None:
    s = str(raw or "").strip().lower()
    if not s:
        return None
    if "goblin" in s:
        return "goblin"
    if "demon" in s:
        return "demon"
    if "standard" in s or s == "std":
        return "standard"
    return None


def find_nba_step8_excel() -> Path:
    """Prefer NBA/outputs/step8_nba_direction_clean*.xlsx; fall back to data/outputs."""
    candidates: list[Path] = []
    for sub in (
        ROOT / "NBA" / "outputs",
        ROOT / "NBA" / "data" / "outputs",
    ):
        if not sub.is_dir():
            continue
        candidates.extend(sub.glob("step8_nba_direction_clean*.xlsx"))
        candidates.extend(sub.glob("step8*direction*clean*.xlsx"))
        candidates.extend(sub.glob("step8_all_direction_clean*.xlsx"))
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in sorted(candidates, key=lambda x: x.stat().st_mtime, reverse=True):
        k = str(p.resolve())
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    if not uniq:
        print(
            "[ERROR] No NBA step8 Excel found.\n"
            "  Looked under:\n"
            "    NBA/outputs/step8_nba_direction_clean*.xlsx\n"
            "    NBA/data/outputs/step8*direction*clean*.xlsx\n"
            "  Generate step8 output or copy the latest workbook into NBA/outputs/."
        )
        sys.exit(1)
    return uniq[0]


def load_board(path: Path) -> tuple[pd.DataFrame, str, str, str, str | None, str | None]:
    """Rows on the board: line > 0.5, player set, pick_type standard/goblin/demon. No score filters."""
    xls = pd.ExcelFile(path)
    sh = "ALL" if "ALL" in xls.sheet_names else xls.sheet_names[0]
    df = pd.read_excel(path, sheet_name=sh, engine="openpyxl")

    pcol = _pick_col(df, ["player"])
    prcol = _pick_col(df, ["prop_type", "prop"])
    lcol = _pick_col(df, ["line"])
    pickcol = _pick_col(df, ["pick_type", "pick type"])
    dircol = _pick_col(df, ["direction", "final_bet_direction"])
    teamcol = _pick_col(df, ["team"])

    req = [pcol, prcol, lcol, pickcol]
    if any(c is None for c in req):
        raise RuntimeError(
            f"step8 sheet missing required columns. Have: {list(df.columns)}"
        )

    work = df.copy()
    work["__pick"] = work[pickcol].map(_norm_pick_type)
    linev = pd.to_numeric(work[lcol], errors="coerce")
    player_ok = work[pcol].astype(str).str.strip().ne("") & work[pcol].notna()

    mask = (
        work["__pick"].isin(["standard", "goblin", "demon"])
        & linev.notna()
        & (linev > 0.5)
        & player_ok
    )
    work = work.loc[mask].copy()
    work["__line"] = linev.loc[work.index]

    # Standard line per (player, prop): first row in stable name order (no scoring)
    std_map: dict[tuple[str, str], float] = {}
    std_rows = work[work["__pick"] == "standard"].copy()
    if not std_rows.empty:
        std_rows = std_rows.sort_values([pcol, prcol], kind="mergesort")
        for _, r in std_rows.iterrows():
            key = (_norm(r[pcol]), _norm(r[prcol]))
            if key not in std_map:
                std_map[key] = float(r["__line"])

    def row_std_line(row: pd.Series) -> float | None:
        key = (_norm(row[pcol]), _norm(row[prcol]))
        v = std_map.get(key)
        return float(v) if v is not None else None

    work["__std_line"] = work.apply(row_std_line, axis=1)

    def row_distance(row: pd.Series) -> float | None:
        std_ln = row["__std_line"]
        if std_ln is None or pd.isna(std_ln):
            return None
        return abs(float(row["__line"]) - float(std_ln))

    work["__distance"] = work.apply(row_distance, axis=1)

    return work, pcol, prcol, lcol, dircol, teamcol


def build_pools(
    work: pd.DataFrame,
    pcol: str,
    prcol: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    std = work[work["__pick"] == "standard"].copy()
    std = std.sort_values(pcol, kind="mergesort")
    std = std.drop_duplicates(subset=[pcol], keep="first")

    gob = work[work["__pick"] == "goblin"].copy()
    gob = gob.sort_values("__distance", ascending=False, na_position="last", kind="mergesort")
    gob = gob.drop_duplicates(subset=[pcol], keep="first")

    dem = work[work["__pick"] == "demon"].copy()
    dem = dem.sort_values(pcol, kind="mergesort")
    dem = dem.drop_duplicates(subset=[pcol], keep="first")

    return std, gob, dem


def row_to_leg_dict(
    r: pd.Series,
    pcol: str,
    prcol: str,
    lcol: str,
    dircol: str | None,
    teamcol: str | None,
) -> dict[str, Any]:
    dist = r.get("__distance")
    direction = "over"
    if dircol and dircol in r.index:
        direction = str(r[dircol] or "over").strip().lower() or "over"
    leg: dict[str, Any] = {
        "player": str(r[pcol] or "").strip(),
        "prop_type": str(r[prcol] or "").strip(),
        "line": float(r["__line"]),
        "pick_type": str(r["__pick"]),
        "direction": direction,
        "line_distance": float(dist) if dist is not None and pd.notna(dist) else None,
    }
    if teamcol and teamcol in r.index:
        leg["team"] = str(r[teamcol] or "").strip()
    return leg


def leg_label(leg: dict[str, Any]) -> str:
    dist = leg.get("line_distance")
    dist_part = (
        f", dist={float(dist):.2f}"
        if dist is not None
        else ", dist=n/a"
    )
    return (
        f"{leg['player']} {leg['line']} {leg['prop_type']} "
        f"({leg['pick_type']}{dist_part})"
    )


def avoid_team_frozenset(team: str | None) -> frozenset[str] | None:
    if not team or not str(team).strip():
        return None
    return frozenset({str(team).strip()})


@dataclass
class DiversePicker:
    """Picks legs without reusing players across tickets; optional same-team avoidance."""

    standard_pool: pd.DataFrame
    goblin_pool: pd.DataFrame
    pcol: str
    prcol: str
    lcol: str
    dircol: str | None
    teamcol: str | None
    used_players: set[str] = field(default_factory=set)
    std_taken: set[int] = field(default_factory=set)
    gob_taken: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.standard_pool = self.standard_pool.reset_index(drop=True)
        self.goblin_pool = self.goblin_pool.reset_index(drop=True)

    def _take(self, leg: dict[str, Any]) -> None:
        self.used_players.add(_norm(leg["player"]))

    def _team(self, r: pd.Series) -> str:
        if not self.teamcol or self.teamcol not in r.index:
            return ""
        return str(r[self.teamcol] or "").strip()

    def pop_standard(self, avoid_teams: frozenset[str] | None = None) -> dict[str, Any] | None:
        for k in range(len(self.standard_pool)):
            if k in self.std_taken:
                continue
            r = self.standard_pool.iloc[k]
            key = _norm(r[self.pcol])
            if key in self.used_players:
                continue
            t = self._team(r)
            if avoid_teams and t and t in avoid_teams:
                continue
            self.std_taken.add(k)
            leg = row_to_leg_dict(r, self.pcol, self.prcol, self.lcol, self.dircol, self.teamcol)
            self._take(leg)
            return leg
        return None

    def pop_goblin_large(self, avoid_teams: frozenset[str] | None = None) -> dict[str, Any] | None:
        for k in range(len(self.goblin_pool)):
            if k in self.gob_taken:
                continue
            r = self.goblin_pool.iloc[k]
            key = _norm(r[self.pcol])
            if key in self.used_players:
                continue
            t = self._team(r)
            if avoid_teams and t and t in avoid_teams:
                continue
            self.gob_taken.add(k)
            leg = row_to_leg_dict(r, self.pcol, self.prcol, self.lcol, self.dircol, self.teamcol)
            self._take(leg)
            return leg
        return None

    def pop_goblin_small(self, avoid_teams: frozenset[str] | None = None) -> dict[str, Any] | None:
        eps = 1e-6
        for k in range(len(self.goblin_pool) - 1, -1, -1):
            if k in self.gob_taken:
                continue
            r = self.goblin_pool.iloc[k]
            d = r.get("__distance")
            if d is None or pd.isna(d) or float(d) <= eps:
                continue
            key = _norm(r[self.pcol])
            if key in self.used_players:
                continue
            t = self._team(r)
            if avoid_teams and t and t in avoid_teams:
                continue
            self.gob_taken.add(k)
            leg = row_to_leg_dict(r, self.pcol, self.prcol, self.lcol, self.dircol, self.teamcol)
            self._take(leg)
            return leg
        return None


def _fill_short(
    legs: list[dict[str, Any] | None],
    picker: DiversePicker,
    need: int,
    kind: str,
) -> list[dict[str, Any]]:
    """Replace Nones by pulling more legs from pools (goblin-preferring or standard-preferring)."""
    out: list[dict[str, Any]] = [L for L in legs if L is not None]
    while len(out) < need:
        if kind == "goblin":
            g = picker.pop_goblin_large()
            if g is None:
                g = picker.pop_standard()
        else:
            g = picker.pop_standard()
            if g is None:
                g = picker.pop_goblin_large()
        if g is None:
            break
        out.append(g)
    return out


def build_fifteen_tickets(picker: DiversePicker) -> list[dict[str, Any]]:
    p = picker
    tickets: list[dict[str, Any]] = []

    def count_types(legs: list[dict[str, Any]]) -> tuple[int, int, int]:
        ng = sum(1 for x in legs if x.get("pick_type") == "goblin")
        nd = sum(1 for x in legs if x.get("pick_type") == "demon")
        ns = sum(1 for x in legs if x.get("pick_type") == "standard")
        return ng, nd, ns

    # NOTE: PrizePicks may block same-team 2-leg entries; we prefer different teams when Team exists.
    print(
        "\n[NOTE] Calibration slips: different players per leg. "
        "If PrizePicks blocks same-team 2-leg picks, skip that build and use the next "
        "standard/goblin from the printed pools (different Team) instead.\n"
    )

    # 1: 2 std — prefer different teams
    a = p.pop_standard()
    b = p.pop_standard(avoid_team_frozenset(a.get("team") if a else None))
    legs = _fill_short([a, b], p, 2, "standard")
    ng, nd, ns = count_types(legs)
    tickets.append(
        {
            "num": 1,
            "n_legs": 2,
            "ticket_type": "power",
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
            "legs": legs,
            "title": "2-leg Power, 2 Standard",
        }
    )

    # 2: small goblin + std
    g1 = p.pop_goblin_small()
    s1 = p.pop_standard(avoid_team_frozenset(g1.get("team") if g1 else None))
    legs = _fill_short([g1, s1], p, 2, "standard")
    ng, nd, ns = count_types(legs)
    tickets.append(
        {
            "num": 2,
            "n_legs": 2,
            "ticket_type": "power",
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
            "legs": legs,
            "title": "2-leg Power, 1 Goblin (small distance) + 1 Standard",
        }
    )

    # 3: large goblin + std
    g2 = p.pop_goblin_large()
    s2 = p.pop_standard(avoid_team_frozenset(g2.get("team") if g2 else None))
    legs = _fill_short([g2, s2], p, 2, "standard")
    ng, nd, ns = count_types(legs)
    tickets.append(
        {
            "num": 3,
            "n_legs": 2,
            "ticket_type": "power",
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
            "legs": legs,
            "title": "2-leg Power, 1 Goblin (large distance) + 1 Standard",
        }
    )

    # 4: 2 goblins (large, then large w/ team avoid)
    ga = p.pop_goblin_large()
    gb = p.pop_goblin_large(avoid_team_frozenset(ga.get("team") if ga else None))
    legs = _fill_short([ga, gb], p, 2, "goblin")
    ng, nd, ns = count_types(legs)
    tickets.append(
        {
            "num": 4,
            "n_legs": 2,
            "ticket_type": "power",
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
            "legs": legs,
            "title": "2-leg Power, 2 Goblins",
        }
    )

    # 5: 3 std
    legs = _fill_short(
        [p.pop_standard(), p.pop_standard(), p.pop_standard()],
        p,
        3,
        "standard",
    )
    ng, nd, ns = count_types(legs)
    tickets.append(
        {
            "num": 5,
            "n_legs": 3,
            "ticket_type": "power",
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
            "legs": legs,
            "title": "3-leg Power, 3 Standard",
        }
    )

    # 6: 1 goblin (large queue) + 2 std
    gm = p.pop_goblin_large()
    legs = _fill_short(
        [gm, p.pop_standard(), p.pop_standard()],
        p,
        3,
        "standard",
    )
    ng, nd, ns = count_types(legs)
    tickets.append(
        {
            "num": 6,
            "n_legs": 3,
            "ticket_type": "power",
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
            "legs": legs,
            "title": "3-leg Power, 1 Goblin + 2 Standard",
        }
    )

    # 7: 2 goblins + 1 std
    ga3 = p.pop_goblin_large()
    gb3 = p.pop_goblin_large(avoid_team_frozenset(ga3.get("team") if ga3 else None))
    legs = _fill_short([ga3, gb3, p.pop_standard()], p, 3, "standard")
    ng, nd, ns = count_types(legs)
    tickets.append(
        {
            "num": 7,
            "n_legs": 3,
            "ticket_type": "power",
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
            "legs": legs,
            "title": "3-leg Power, 2 Goblins + 1 Standard",
        }
    )

    # 8: 3 goblins
    legs = _fill_short(
        [
            p.pop_goblin_large(),
            p.pop_goblin_large(),
            p.pop_goblin_large(),
        ],
        p,
        3,
        "goblin",
    )
    ng, nd, ns = count_types(legs)
    tickets.append(
        {
            "num": 8,
            "n_legs": 3,
            "ticket_type": "power",
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
            "legs": legs,
            "title": "3-leg Power, 3 Goblins",
        }
    )

    # 9: 4 std
    legs = _fill_short(
        [
            p.pop_standard(),
            p.pop_standard(),
            p.pop_standard(),
            p.pop_standard(),
        ],
        p,
        4,
        "standard",
    )
    ng, nd, ns = count_types(legs)
    tickets.append(
        {
            "num": 9,
            "n_legs": 4,
            "ticket_type": "power",
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
            "legs": legs,
            "title": "4-leg Power, 4 Standard",
        }
    )

    # 10: 1 gob + 3 std
    g4 = p.pop_goblin_large()
    legs = _fill_short(
        [g4, p.pop_standard(), p.pop_standard(), p.pop_standard()],
        p,
        4,
        "standard",
    )
    ng, nd, ns = count_types(legs)
    tickets.append(
        {
            "num": 10,
            "n_legs": 4,
            "ticket_type": "power",
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
            "legs": legs,
            "title": "4-leg Power, 1 Goblin + 3 Standard",
        }
    )

    # 11: 2 gob + 2 std
    ga4 = p.pop_goblin_large()
    gb4 = p.pop_goblin_large(avoid_team_frozenset(ga4.get("team") if ga4 else None))
    legs = _fill_short(
        [ga4, gb4, p.pop_standard(), p.pop_standard()],
        p,
        4,
        "standard",
    )
    ng, nd, ns = count_types(legs)
    tickets.append(
        {
            "num": 11,
            "n_legs": 4,
            "ticket_type": "power",
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
            "legs": legs,
            "title": "4-leg Power, 2 Goblins + 2 Standard",
        }
    )

    # 12–15 flex (same leg patterns)
    legs = _fill_short(
        [p.pop_standard(), p.pop_standard(), p.pop_standard()],
        p,
        3,
        "standard",
    )
    ng, nd, ns = count_types(legs)
    tickets.append(
        {
            "num": 12,
            "n_legs": 3,
            "ticket_type": "flex",
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
            "legs": legs,
            "title": "3-leg Flex, 3 Standard",
        }
    )

    flex_g = p.pop_goblin_large()
    legs = _fill_short(
        [flex_g, p.pop_standard(), p.pop_standard()],
        p,
        3,
        "standard",
    )
    ng, nd, ns = count_types(legs)
    tickets.append(
        {
            "num": 13,
            "n_legs": 3,
            "ticket_type": "flex",
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
            "legs": legs,
            "title": "3-leg Flex, 1 Goblin + 2 Standard",
        }
    )

    legs = _fill_short(
        [
            p.pop_standard(),
            p.pop_standard(),
            p.pop_standard(),
            p.pop_standard(),
        ],
        p,
        4,
        "standard",
    )
    ng, nd, ns = count_types(legs)
    tickets.append(
        {
            "num": 14,
            "n_legs": 4,
            "ticket_type": "flex",
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
            "legs": legs,
            "title": "4-leg Flex, 4 Standard",
        }
    )

    flex_gl = p.pop_goblin_large()
    legs = _fill_short(
        [
            flex_gl,
            p.pop_standard(),
            p.pop_standard(),
            p.pop_standard(),
        ],
        p,
        4,
        "standard",
    )
    ng, nd, ns = count_types(legs)
    tickets.append(
        {
            "num": 15,
            "n_legs": 4,
            "ticket_type": "flex",
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
            "legs": legs,
            "title": "4-leg Flex, 1 Goblin + 3 Standard",
        }
    )

    return tickets


def print_pools(
    standard_pool: pd.DataFrame,
    goblin_pool: pd.DataFrame,
    demon_pool: pd.DataFrame,
    pcol: str,
    prcol: str,
) -> None:
    print("\n=== POOLS (deduped by player; no score filtering) ===\n")

    def dump(name: str, df: pd.DataFrame) -> None:
        print(f"--- {name} ({len(df)} rows) ---")
        for _, r in df.iterrows():
            std_ln = r.get("__std_line")
            dist = r.get("__distance")
            std_s = f"{float(std_ln):.1f}" if pd.notna(std_ln) else ""
            dist_s = f"{float(dist):.2f}" if pd.notna(dist) else ""
            print(
                f"  {r[pcol]} | {r[prcol]} | {float(r['__line']):.1f} | "
                f"{r['__pick']} | std={std_s} | dist={dist_s}"
            )
        print()

    dump("Standard pool (A→Z by player)", standard_pool)
    dump("Goblin pool (distance ↓, then deduped)", goblin_pool)
    if len(demon_pool) > 0:
        dump("Demon pool (A→Z by player)", demon_pool)
    else:
        print("--- Demon pool ---\n  (none)\n")


def save_suggested_tickets(path: Path, tickets: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for t in tickets:
        lines.append(f"TICKET #{t['num']} — {t['title']}")
        lines.append(f"  Type: {t['ticket_type']} | Legs: {t['n_legs']}")
        for i, leg in enumerate(t["legs"], 1):
            lines.append(f"  Leg {i}: {leg_label(leg)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[SAVE] Suggested tickets -> {path}")


def avg_goblin_distance(legs: list[dict[str, Any]]) -> str:
    ds = [
        float(leg["line_distance"])
        for leg in legs
        if str(leg.get("pick_type", "")).lower() == "goblin"
        and leg.get("line_distance") is not None
    ]
    if not ds:
        return ""
    return f"{sum(ds) / len(ds):.4f}"


def main() -> None:
    step8_path = find_nba_step8_excel()
    print(f"[LOAD] {step8_path}")

    work, pcol, prcol, lcol, dircol, teamcol = load_board(step8_path)
    standard_pool, goblin_pool, demon_pool = build_pools(work, pcol, prcol)

    print_pools(standard_pool, goblin_pool, demon_pool, pcol, prcol)

    if len(standard_pool) + len(goblin_pool) < 25:
        print(
            "[WARN] Few deduped standard+goblin rows; tickets may repeat pick types "
            "or fall short until pools exhaust.\n"
        )

    picker = DiversePicker(standard_pool, goblin_pool, pcol, prcol, lcol, dircol, teamcol)
    tickets = build_fifteen_tickets(picker)

    today = date.today().isoformat()
    suggested_path = SAMPLES_DIR / f"suggested_tickets_{today}.txt"
    save_suggested_tickets(suggested_path, tickets)

    print("\n=== SUGGESTED 15 TICKETS ===\n")
    for t in tickets:
        print(f"TICKET #{t['num']} — {t['title']}")
        print(f"  {t['ticket_type'].upper()} | n_legs={t['n_legs']}")
        for i, leg in enumerate(t["legs"], 1):
            print(f"  Leg {i}: {leg_label(leg)}")
        print()

    out_csv = SAMPLES_DIR / f"payout_log_manual_{today}.csv"
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "ticket_num",
        "n_legs",
        "ticket_type",
        "n_goblins",
        "n_demons",
        "n_standard",
        "legs_detail",
        "legs",
        "avg_goblin_distance",
        "first_place_payout",
        "min_guarantee_payout",
        "displayed_multiplier",
        "entry_amount",
        "to_win_amount",
        "computed_multiplier",
        "source",
    ]

    saved: list[dict[str, Any]] = []

    print(
        "\nEnter payouts after building each slip in PrizePicks.\n"
        "  • First prompt: type Q or quit to exit and save.\n"
        "  • Press ENTER on '1st place pays' to skip that ticket.\n"
    )

    for t in tickets:
        print("=" * 60)
        print(f"TICKET #{t['num']} — {t['title']}")
        print(f"  Mode: {t['ticket_type'].upper()} | Legs: {t['n_legs']}")
        for i, leg in enumerate(t["legs"], 1):
            print(f"  Leg {i}: {leg_label(leg)}")
        print("\nBuild this slip in PrizePicks, then enter values:\n")

        fp = input("1st place pays (e.g. 6.0) [Enter=skip ticket, Q=quit]: ").strip()
        if fp.lower() in ("q", "quit"):
            print("[EXIT] Quit requested.")
            break
        if not fp:
            print("  (skipped)\n")
            continue
        try:
            first_place = float(fp)
        except ValueError:
            print("  Invalid number; skipping ticket.\n")
            continue

        mg_raw = input("N correct pays / min guarantee (e.g. 1.25): ").strip()
        if mg_raw.lower() in ("q", "quit"):
            print("[EXIT] Quit requested.")
            break
        min_g = float(mg_raw) if mg_raw else None

        ent_raw = input("Entry amount (e.g. 10.00): ").strip()
        if ent_raw.lower() in ("q", "quit"):
            print("[EXIT] Quit requested.")
            break
        entry = float(ent_raw) if ent_raw else None

        tw_raw = input("To Win amount (e.g. 75.00): ").strip()
        if tw_raw.lower() in ("q", "quit"):
            print("[EXIT] Quit requested.")
            break
        to_win = float(tw_raw) if tw_raw else None

        computed = ""
        if entry and to_win and entry > 0:
            computed = f"{float(to_win) / float(entry):.4f}"

        legs_detail = " | ".join(leg_label(L) for L in t["legs"])
        legs_json = json.dumps(t["legs"], ensure_ascii=False)

        row = {
            "ticket_num": t["num"],
            "n_legs": t["n_legs"],
            "ticket_type": t["ticket_type"],
            "n_goblins": t["n_goblins"],
            "n_demons": t["n_demons"],
            "n_standard": t["n_standard"],
            "legs_detail": legs_detail,
            "legs": legs_json,
            "avg_goblin_distance": avg_goblin_distance(t["legs"]),
            "first_place_payout": first_place,
            "min_guarantee_payout": min_g if min_g is not None else "",
            "displayed_multiplier": first_place,
            "entry_amount": entry if entry is not None else "",
            "to_win_amount": to_win if to_win is not None else "",
            "computed_multiplier": computed,
            "source": "manual",
        }
        saved.append(row)

        write_header = not out_csv.exists()
        with out_csv.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                w.writeheader()
            w.writerow(row)
        print(f"  [SAVED] -> {out_csv.name}\n")

    if saved:
        print("Running formula fitter on collected data...")
        r = subprocess.run(
            ["py", "-3.14", str(ROOT / "scripts" / "fit_payout_formula.py")],
            cwd=str(ROOT),
        )
        if r.returncode != 0:
            print(f"[WARN] fit_payout_formula.py exited with code {r.returncode}")
    else:
        print("No rows saved; skipping fitter.")


if __name__ == "__main__":
    main()
