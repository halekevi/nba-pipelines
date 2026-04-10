#!/usr/bin/env python3
"""
Generate six PrizePicks Power calibration tickets (A–F) from the latest NBA *exported*
step8 workbook, then prompt for observed payouts and append CSV for fit_payout_formula.py.

This script does **not** modify any step8 pipeline code — it only **reads** the Excel
file your pipeline already produced (e.g. step8_all_direction_clean.xlsx).

Published baselines (reference only):
  Legs   Power   Flex 1st   Flex miss 1
  2      3x      3x         -
  3      6x      3x         1.25x
  4      10x     5x         1.5x
  5      20x     10x        2x
  6      37.5x   25x        2x
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


def is_combo_player_or_prop(player: Any, prop: Any) -> bool:
    """Exclude PrizePicks combo markets (multi-player or '(Combo)' props)."""
    p = str(player or "").strip()
    pr = str(prop or "").strip()
    if " + " in p:
        return True
    if "combo" in pr.lower():
        return True
    return False


def find_nba_step8_excel() -> Path:
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
            "[ERROR] No NBA step8 Excel found to read.\n"
            "  Expected under NBA/outputs/ or NBA/data/outputs/ (e.g. step8_*clean*.xlsx).\n"
            "  Run your normal NBA pipeline to export step8 — this tool does not change those scripts."
        )
        sys.exit(1)
    return uniq[0]


def load_singles_board(path: Path) -> tuple[pd.DataFrame, str, str, str, str | None, str | None]:
    """Single-player rows only: line > 0.5, pick type set, no combo player/prop."""
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
        raise RuntimeError(f"step8 sheet missing columns. Have: {list(df.columns)}")

    work = df.copy()
    work["__pick"] = work[pickcol].map(_norm_pick_type)
    linev = pd.to_numeric(work[lcol], errors="coerce")
    player_ok = work[pcol].astype(str).str.strip().ne("") & work[pcol].notna()
    combo_mask = work.apply(
        lambda r: is_combo_player_or_prop(r[pcol], r[prcol]), axis=1
    )

    mask = (
        work["__pick"].isin(["standard", "goblin"])
        & linev.notna()
        & (linev > 0.5)
        & player_ok
        & ~combo_mask
    )
    work = work.loc[mask].copy()
    work["__line"] = linev.loc[work.index]

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
) -> tuple[pd.DataFrame, pd.DataFrame]:
    std = work[work["__pick"] == "standard"].copy()
    std = std.sort_values(pcol, kind="mergesort")
    std = std.drop_duplicates(subset=[pcol], keep="first")

    gob = work[work["__pick"] == "goblin"].copy()
    gob = gob.sort_values("__distance", ascending=False, na_position="last", kind="mergesort")
    gob = gob.drop_duplicates(subset=[pcol], keep="first")

    return std, gob


def row_to_leg_dict(
    r: pd.Series,
    pcol: str,
    prcol: str,
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
    }
    if dist is not None and pd.notna(dist):
        leg["line_distance"] = float(dist)
    if teamcol and teamcol in r.index:
        t = str(r[teamcol] or "").strip()
        if t:
            leg["team"] = t
    return leg


def leg_label(leg: dict[str, Any]) -> str:
    dist = leg.get("line_distance")
    dist_part = f", dist={float(dist):.2f}" if dist is not None else ", dist=n/a"
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
    standard_pool: pd.DataFrame
    goblin_pool: pd.DataFrame
    pcol: str
    prcol: str
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
            leg = row_to_leg_dict(r, self.pcol, self.prcol, self.dircol, self.teamcol)
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
            leg = row_to_leg_dict(r, self.pcol, self.prcol, self.dircol, self.teamcol)
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
            leg = row_to_leg_dict(r, self.pcol, self.prcol, self.dircol, self.teamcol)
            self._take(leg)
            return leg
        return None


def _fill_short(
    legs: list[dict[str, Any] | None],
    picker: DiversePicker,
    need: int,
    prefer: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [L for L in legs if L is not None]
    while len(out) < need:
        if prefer == "goblin":
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


def _count_types(legs: list[dict[str, Any]]) -> tuple[int, int, int]:
    ng = sum(1 for x in legs if str(x.get("pick_type", "")).lower() == "goblin")
    nd = sum(1 for x in legs if str(x.get("pick_type", "")).lower() == "demon")
    ns = sum(1 for x in legs if str(x.get("pick_type", "")).lower() == "standard")
    return ng, nd, ns


def build_six_tickets(picker: DiversePicker) -> list[dict[str, Any]]:
    p = picker
    specs: list[dict[str, Any]] = []

    # A: small goblin + standard
    g = p.pop_goblin_small()
    s = p.pop_standard(avoid_team_frozenset(g.get("team") if g else None))
    legs = _fill_short([g, s], p, 2, "standard")
    ng, nd, ns = _count_types(legs)
    specs.append(
        {
            "ticket_id": "A",
            "ticket_num": 1,
            "n_legs": 2,
            "ticket_type": "power",
            "title": "2-leg Power, 1 small goblin + 1 standard (singles only)",
            "legs": legs,
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
        }
    )

    # B: large goblin + standard
    g2 = p.pop_goblin_large()
    s2 = p.pop_standard(avoid_team_frozenset(g2.get("team") if g2 else None))
    legs = _fill_short([g2, s2], p, 2, "standard")
    ng, nd, ns = _count_types(legs)
    specs.append(
        {
            "ticket_id": "B",
            "ticket_num": 2,
            "n_legs": 2,
            "ticket_type": "power",
            "title": "2-leg Power, 1 large goblin + 1 standard (singles only)",
            "legs": legs,
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
        }
    )

    # C: 2 goblins (largest + next, avoid same team)
    ga = p.pop_goblin_large()
    gb = p.pop_goblin_large(avoid_team_frozenset(ga.get("team") if ga else None))
    legs = _fill_short([ga, gb], p, 2, "goblin")
    ng, nd, ns = _count_types(legs)
    specs.append(
        {
            "ticket_id": "C",
            "ticket_num": 3,
            "n_legs": 2,
            "ticket_type": "power",
            "title": "2-leg Power, 2 goblins (singles only)",
            "legs": legs,
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
        }
    )

    # D: 1 large goblin + 2 standard
    gd = p.pop_goblin_large()
    legs = _fill_short(
        [gd, p.pop_standard(), p.pop_standard()],
        p,
        3,
        "standard",
    )
    ng, nd, ns = _count_types(legs)
    specs.append(
        {
            "ticket_id": "D",
            "ticket_num": 4,
            "n_legs": 3,
            "ticket_type": "power",
            "title": "3-leg Power, 1 goblin + 2 standard (singles only)",
            "legs": legs,
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
        }
    )

    # E: 2 goblins + 1 standard
    gea = p.pop_goblin_large()
    geb = p.pop_goblin_large(avoid_team_frozenset(gea.get("team") if gea else None))
    legs = _fill_short(
        [gea, geb, p.pop_standard()],
        p,
        3,
        "standard",
    )
    ng, nd, ns = _count_types(legs)
    specs.append(
        {
            "ticket_id": "E",
            "ticket_num": 5,
            "n_legs": 3,
            "ticket_type": "power",
            "title": "3-leg Power, 2 goblins + 1 standard (singles only)",
            "legs": legs,
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
        }
    )

    # F: 3 goblins
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
    ng, nd, ns = _count_types(legs)
    specs.append(
        {
            "ticket_id": "F",
            "ticket_num": 6,
            "n_legs": 3,
            "ticket_type": "power",
            "title": "3-leg Power, 3 goblins (singles only)",
            "legs": legs,
            "n_goblins": ng,
            "n_demons": nd,
            "n_standard": ns,
        }
    )

    return specs


def save_suggested_tickets(path: Path, tickets: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for t in tickets:
        lines.append(f"TICKET {t['ticket_id']} — {t['title']}")
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
    print(
        "\nTicket generator: reads latest NBA step8 **export** only (does not edit step8 scripts).\n"
        "Singles only: no combo players ('A + B') and no props with 'combo' in the name.\n"
    )
    print(
        "Published Power / Flex baselines (reference):\n"
        "  Legs   Power   Flex 1st   Flex miss 1\n"
        "  2      3x      3x         -\n"
        "  3      6x      3x         1.25x\n"
        "  4      10x     5x         1.5x\n"
        "  5      20x     10x        2x\n"
        "  6      37.5x   25x        2x\n"
    )

    step8_path = find_nba_step8_excel()
    print(f"[READ] {step8_path}\n")

    work, pcol, prcol, _lcol, dircol, teamcol = load_singles_board(step8_path)
    std_pool, gob_pool = build_pools(work, pcol, prcol)

    n_std, n_gob = len(std_pool), len(gob_pool)
    print(
        f"Singles board: {len(work)} rows -> {n_std} standard / {n_gob} goblin "
        f"(one row per player per side after dedupe).\n"
    )
    if n_std < 6 or n_gob < 6:
        print(
            "[WARN] Few singles after filters; tickets A–F may be incomplete or repeat types.\n"
        )

    picker = DiversePicker(std_pool, gob_pool, pcol, prcol, dircol, teamcol)
    tickets = build_six_tickets(picker)

    today = date.today().isoformat()
    suggested_path = SAMPLES_DIR / f"suggested_tickets_{today}.txt"
    save_suggested_tickets(suggested_path, tickets)

    print("=== SIX TICKETS (A–F) — build in PrizePicks ===\n")
    for t in tickets:
        print(f"TICKET {t['ticket_id']} — {t['title']}")
        print(f"  POWER | n_legs={t['n_legs']}")
        for i, leg in enumerate(t["legs"], 1):
            print(f"  Leg {i}: {leg_label(leg)}")
        print()

    out_csv = SAMPLES_DIR / f"payout_log_manual_{today}.csv"
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "ticket_id",
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
        "Enter payouts after each slip (6 tickets).\n"
        "  • Q or quit: exit early.\n"
        "  • ENTER on first prompt: skip that ticket.\n"
    )

    for t in tickets:
        print("=" * 60)
        print(f"TICKET {t['ticket_id']} — {t['title']}")
        print(f"  Mode: POWER | Legs: {t['n_legs']}")
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
            "ticket_id": t["ticket_id"],
            "ticket_num": t["ticket_num"],
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
