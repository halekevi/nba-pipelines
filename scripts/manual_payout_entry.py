#!/usr/bin/env python3
"""
Manual payout data entry: suggest legs from NBA step8, print 15 ticket configs,
prompt for PrizePicks slip values, save CSV for fit_payout_formula.py.
No browser/CDP — terminal only.
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
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
    # De-dupe, newest first
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


def load_filtered_candidates(
    path: Path,
) -> tuple[pd.DataFrame, dict[tuple[str, str], float]]:
    xls = pd.ExcelFile(path)
    sh = "ALL" if "ALL" in xls.sheet_names else xls.sheet_names[0]
    df = pd.read_excel(path, sheet_name=sh, engine="openpyxl")

    pcol = _pick_col(df, ["player"])
    prcol = _pick_col(df, ["prop_type", "prop"])
    lcol = _pick_col(df, ["line"])
    dircol = _pick_col(df, ["direction", "final_bet_direction"])
    tiercol = _pick_col(df, ["tier"])
    blendcol = _pick_col(df, ["blended_score", "blended score"])
    pickcol = _pick_col(df, ["pick_type", "pick type"])

    req = [pcol, prcol, lcol, dircol, tiercol, blendcol, pickcol]
    if any(c is None for c in req):
        raise RuntimeError(
            f"step8 sheet missing required columns. Have: {list(df.columns)}"
        )

    work = df.copy()
    work["__pick"] = work[pickcol].map(_norm_pick_type)
    tier = work[tiercol].astype(str).str.upper().str.strip()
    direction = work[dircol].astype(str).str.strip()
    linev = pd.to_numeric(work[lcol], errors="coerce")
    blend = pd.to_numeric(work[blendcol], errors="coerce")

    mask = (
        tier.isin(["A", "B", "C"])
        & direction.ne("")
        & direction.notna()
        & work["__pick"].isin(["standard", "goblin", "demon"])
        & linev.notna()
        & (linev > 0.5)
        & blend.notna()
    )
    work = work.loc[mask].copy()
    work["__line"] = linev.loc[work.index]
    work["__blend"] = blend.loc[work.index]

    # Standard line per (player, prop): line from standard row with highest blend
    std_map: dict[tuple[str, str], float] = {}
    std_rows = work[work["__pick"] == "standard"]
    for _, r in std_rows.sort_values("__blend", ascending=False).iterrows():
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

    return work, std_map


def top_by_pick(
    work: pd.DataFrame,
    pcol: str,
    prcol: str,
    pick: str,
    n: int,
) -> pd.DataFrame:
    sub = work[work["__pick"] == pick].sort_values("__blend", ascending=False)
    return sub.head(n).copy()


def row_to_leg_dict(
    r: pd.Series,
    pcol: str,
    prcol: str,
    lcol: str,
    dircol: str,
) -> dict[str, Any]:
    dist = r.get("__distance")
    return {
        "player": str(r[pcol] or "").strip(),
        "prop_type": str(r[prcol] or "").strip(),
        "line": float(r["__line"]),
        "pick_type": str(r["__pick"]),
        "direction": str(r[dircol] or "over").lower(),
        "line_distance": float(dist) if dist is not None and pd.notna(dist) else None,
    }


def leg_label(leg: dict[str, Any]) -> str:
    return (
        f"{leg['player']} {leg['line']} {leg['prop_type']} "
        f"({leg['pick_type']}"
        + (
            f", dist={leg['line_distance']:.2f}"
            if leg.get("line_distance") is not None
            else ""
        )
        + ")"
    )


def pick_goblin_by_rank(
    goblins: pd.DataFrame, pcol: str, prcol: str, lcol: str, dircol: str, rank: str
) -> dict[str, Any] | None:
    """rank: 'smallest', 'largest', 'second_largest', 'medium'."""
    g = goblins[goblins["__distance"].notna()].copy()
    if g.empty:
        g = goblins.copy()
        if g.empty:
            return None
        r0 = g.iloc[0]
        return row_to_leg_dict(r0, pcol, prcol, lcol, dircol)
    g = g.sort_values("__distance", ascending=True)
    if rank == "smallest":
        return row_to_leg_dict(g.iloc[0], pcol, prcol, lcol, dircol)
    if rank == "largest":
        return row_to_leg_dict(g.iloc[-1], pcol, prcol, lcol, dircol)
    if rank == "second_largest":
        if len(g) < 2:
            return row_to_leg_dict(g.iloc[-1], pcol, prcol, lcol, dircol)
        return row_to_leg_dict(g.iloc[-2], pcol, prcol, lcol, dircol)
    if rank == "medium":
        mid = len(g) // 2
        return row_to_leg_dict(g.iloc[mid], pcol, prcol, lcol, dircol)
    return None


def build_fifteen_tickets(
    standards: pd.DataFrame,
    goblins: pd.DataFrame,
    demons: pd.DataFrame,
    pcol: str,
    prcol: str,
    lcol: str,
    dircol: str,
) -> list[dict[str, Any]]:
    def std_i(i: int) -> dict[str, Any]:
        r = standards.iloc[i % len(standards)]
        return row_to_leg_dict(r, pcol, prcol, lcol, dircol)

    tickets: list[dict[str, Any]] = []

    # 1: 2 power 2 std
    tickets.append(
        {
            "num": 1,
            "n_legs": 2,
            "ticket_type": "power",
            "n_goblins": 0,
            "n_demons": 0,
            "n_standard": 2,
            "legs": [std_i(0), std_i(1)],
            "title": "2-leg Power, 2 Standard",
        }
    )
    # 2: small gob + std (prefer smallest distance > 0 vs standard line)
    g_pos = goblins[
        goblins["__distance"].notna() & (pd.to_numeric(goblins["__distance"], errors="coerce") > 1e-6)
    ]
    g1 = pick_goblin_by_rank(
        g_pos if not g_pos.empty else goblins, pcol, prcol, lcol, dircol, "smallest"
    )
    if g1 is None:
        g1 = std_i(0)  # fallback
        g1["pick_type"] = "goblin"
    tickets.append(
        {
            "num": 2,
            "n_legs": 2,
            "ticket_type": "power",
            "n_goblins": 1,
            "n_demons": 0,
            "n_standard": 1,
            "legs": [g1, std_i(0)],
            "title": "2-leg Power, 1 Goblin (small distance) + 1 Standard",
        }
    )
    # 3: large gob + std (prefer distance >= 5 vs standard when available)
    g_far = goblins[goblins["__distance"].notna() & (goblins["__distance"] >= 5.0)]
    g2 = pick_goblin_by_rank(
        g_far if not g_far.empty else goblins, pcol, prcol, lcol, dircol, "largest"
    )
    if g2 is None:
        g2 = g1
    tickets.append(
        {
            "num": 3,
            "n_legs": 2,
            "ticket_type": "power",
            "n_goblins": 1,
            "n_demons": 0,
            "n_standard": 1,
            "legs": [g2, std_i(1)],
            "title": "2-leg Power, 1 Goblin (large distance) + 1 Standard",
        }
    )
    # 4: 2 goblins
    ga = pick_goblin_by_rank(goblins, pcol, prcol, lcol, dircol, "largest")
    gb = pick_goblin_by_rank(goblins, pcol, prcol, lcol, dircol, "second_largest")
    if ga is None or gb is None or ga["player"] == gb["player"]:
        # use top two distinct goblin rows by blend
        gob2 = goblins.drop_duplicates(subset=[pcol]).head(2)
        legs_g = [
            row_to_leg_dict(gob2.iloc[i], pcol, prcol, lcol, dircol)
            for i in range(min(2, len(gob2)))
        ]
        while len(legs_g) < 2:
            legs_g.append(std_i(2))
            legs_g[-1]["pick_type"] = "goblin"
    else:
        legs_g = [ga, gb]
    tickets.append(
        {
            "num": 4,
            "n_legs": 2,
            "ticket_type": "power",
            "n_goblins": 2,
            "n_demons": 0,
            "n_standard": 0,
            "legs": legs_g,
            "title": "2-leg Power, 2 Goblins",
        }
    )

    # 5–8: 3-leg power
    tickets.append(
        {
            "num": 5,
            "n_legs": 3,
            "ticket_type": "power",
            "n_goblins": 0,
            "n_demons": 0,
            "n_standard": 3,
            "legs": [std_i(0), std_i(1), std_i(2)],
            "title": "3-leg Power, 3 Standard",
        }
    )
    gm = pick_goblin_by_rank(goblins, pcol, prcol, lcol, dircol, "medium")
    if gm is None:
        gm = g1
    tickets.append(
        {
            "num": 6,
            "n_legs": 3,
            "ticket_type": "power",
            "n_goblins": 1,
            "n_demons": 0,
            "n_standard": 2,
            "legs": [gm, std_i(0), std_i(1)],
            "title": "3-leg Power, 1 Goblin + 2 Standard",
        }
    )
    ga3 = pick_goblin_by_rank(goblins, pcol, prcol, lcol, dircol, "largest")
    gb3 = pick_goblin_by_rank(goblins, pcol, prcol, lcol, dircol, "second_largest")
    if ga3 is None:
        ga3 = gm
    if gb3 is None or (
        ga3.get("player") == gb3.get("player") and ga3.get("prop_type") == gb3.get("prop_type")
    ):
        gb3 = pick_goblin_by_rank(goblins, pcol, prcol, lcol, dircol, "smallest") or ga3
    tickets.append(
        {
            "num": 7,
            "n_legs": 3,
            "ticket_type": "power",
            "n_goblins": 2,
            "n_demons": 0,
            "n_standard": 1,
            "legs": [ga3, gb3, std_i(2)],
            "title": "3-leg Power, 2 Goblins + 1 Standard",
        }
    )
    gob3 = goblins.drop_duplicates(subset=[pcol]).head(3)
    legs_3g = [
        row_to_leg_dict(gob3.iloc[i], pcol, prcol, lcol, dircol)
        for i in range(min(3, len(gob3)))
    ]
    while len(legs_3g) < 3:
        legs_3g.append(legs_3g[-1] if legs_3g else std_i(0))
    tickets.append(
        {
            "num": 8,
            "n_legs": 3,
            "ticket_type": "power",
            "n_goblins": 3,
            "n_demons": 0,
            "n_standard": 0,
            "legs": legs_3g[:3],
            "title": "3-leg Power, 3 Goblins",
        }
    )

    # 9–11: 4-leg power
    tickets.append(
        {
            "num": 9,
            "n_legs": 4,
            "ticket_type": "power",
            "n_goblins": 0,
            "n_demons": 0,
            "n_standard": 4,
            "legs": [std_i(0), std_i(1), std_i(2), std_i(3)],
            "title": "4-leg Power, 4 Standard",
        }
    )
    g4 = pick_goblin_by_rank(goblins, pcol, prcol, lcol, dircol, "medium") or gm
    tickets.append(
        {
            "num": 10,
            "n_legs": 4,
            "ticket_type": "power",
            "n_goblins": 1,
            "n_demons": 0,
            "n_standard": 3,
            "legs": [g4, std_i(0), std_i(1), std_i(2)],
            "title": "4-leg Power, 1 Goblin + 3 Standard",
        }
    )
    ga4 = pick_goblin_by_rank(goblins, pcol, prcol, lcol, dircol, "largest") or g4
    gb4 = pick_goblin_by_rank(goblins, pcol, prcol, lcol, dircol, "second_largest") or ga4
    tickets.append(
        {
            "num": 11,
            "n_legs": 4,
            "ticket_type": "power",
            "n_goblins": 2,
            "n_demons": 0,
            "n_standard": 2,
            "legs": [ga4, gb4, std_i(0), std_i(1)],
            "title": "4-leg Power, 2 Goblins + 2 Standard",
        }
    )

    # 12–15 flex
    tickets.append(
        {
            "num": 12,
            "n_legs": 3,
            "ticket_type": "flex",
            "n_goblins": 0,
            "n_demons": 0,
            "n_standard": 3,
            "legs": [std_i(0), std_i(1), std_i(2)],
            "title": "3-leg Flex, 3 Standard",
        }
    )
    flex_gob = pick_goblin_by_rank(goblins, pcol, prcol, lcol, dircol, "medium") or gm
    tickets.append(
        {
            "num": 13,
            "n_legs": 3,
            "ticket_type": "flex",
            "n_goblins": 1,
            "n_demons": 0,
            "n_standard": 2,
            "legs": [flex_gob, std_i(0), std_i(1)],
            "title": "3-leg Flex, 1 Goblin + 2 Standard",
        }
    )
    tickets.append(
        {
            "num": 14,
            "n_legs": 4,
            "ticket_type": "flex",
            "n_goblins": 0,
            "n_demons": 0,
            "n_standard": 4,
            "legs": [std_i(0), std_i(1), std_i(2), std_i(3)],
            "title": "4-leg Flex, 4 Standard",
        }
    )
    flex_gob_lg = pick_goblin_by_rank(goblins, pcol, prcol, lcol, dircol, "largest") or g4
    tickets.append(
        {
            "num": 15,
            "n_legs": 4,
            "ticket_type": "flex",
            "n_goblins": 1,
            "n_demons": 0,
            "n_standard": 3,
            "legs": [flex_gob_lg, std_i(0), std_i(1), std_i(2)],
            "title": "4-leg Flex, 1 Goblin + 3 Standard",
        }
    )

    _ = demons  # reserved if demon tickets added later
    return tickets


def print_candidates(
    work: pd.DataFrame,
    standards: pd.DataFrame,
    goblins: pd.DataFrame,
    demons: pd.DataFrame,
    pcol: str,
    prcol: str,
) -> None:
    print("\n=== CANDIDATE LEGS (from step8) ===\n")

    tier_col = _pick_col(work, ["tier"])
    if tier_col is None:
        tier_col = work.columns[0]

    def dump(name: str, df: pd.DataFrame) -> None:
        print(f"--- {name} ---")
        for _, r in df.iterrows():
            std_ln = r.get("__std_line")
            dist = r.get("__distance")
            std_s = f"{float(std_ln):.1f}" if pd.notna(std_ln) else ""
            dist_s = f"{float(dist):.2f}" if pd.notna(dist) else ""
            tier_val = r[tier_col] if tier_col in r.index else ""
            print(
                f"  {r[pcol]} | {r[prcol]} | {float(r['__line']):.1f} | "
                f"{r['__pick']} | {std_s} | {dist_s} | {tier_val}"
            )
        print()

    dump("Standard (top 20)", standards)
    dump("Goblin (top 20)", goblins)
    if len(demons) > 0:
        dump("Demon (top 10)", demons)
    else:
        print("--- Demon (top 10) ---\n  (none in filtered data)\n")


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

    work, _std_map = load_filtered_candidates(step8_path)
    pcol = _pick_col(work, ["player"])
    prcol = _pick_col(work, ["prop_type", "prop"])
    lcol = _pick_col(work, ["line"])
    dircol = _pick_col(work, ["direction", "final_bet_direction"])
    assert pcol and prcol and lcol and dircol

    standards = top_by_pick(work, pcol, prcol, "standard", 20)
    goblins = top_by_pick(work, pcol, prcol, "goblin", 20)
    demons = top_by_pick(work, pcol, prcol, "demon", 10)

    if _pick_col(work, ["tier"]) is None:
        raise RuntimeError("tier column missing")

    print_candidates(work, standards, goblins, demons, pcol, prcol)

    if len(standards) < 4:
        print(
            f"[WARN] Only {len(standards)} standard rows; ticket suggestions may repeat players."
        )
    if len(goblins) < 2:
        print("[WARN] Few goblin rows; goblin tickets may be weak suggestions.")

    tickets = build_fifteen_tickets(
        standards, goblins, demons, pcol, prcol, lcol, dircol
    )

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
