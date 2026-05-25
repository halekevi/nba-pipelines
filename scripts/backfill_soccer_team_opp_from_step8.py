#!/usr/bin/env python3
"""
backfill_soccer_team_opp_from_step8.py
---------------------------------------
Recovers blank team + UNKNOWN_OPP in graded_props_*.json for pre-May Soccer
dates using step8 xlsx archives — zero external API calls.

Join key:  player + prop + line  (step8 → graded JSON)
Pair-map:  Game Time + League → (TEAM_A, TEAM_B) within each xlsx
           → infers Opp for UNKNOWN rows from the other side of the same match

What gets patched in graded JSON:
  - team        (when "—" or blank)
  - opp_team    (when UNKNOWN_OPP or blank)
  - def_tier    (when missing, copied from step8 Def Tier / def_tier column)

Usage:
    # Dry-run (no files written)
    py -3.14 scripts/backfill_soccer_team_opp_from_step8.py --repo-root . --dry-run

    # All dates
    py -3.14 scripts/backfill_soccer_team_opp_from_step8.py --repo-root .

    # Single date
    py -3.14 scripts/backfill_soccer_team_opp_from_step8.py --repo-root . --date 2026-04-25

    # After running:
    py -3.14 scripts/build_retrain_dataset.py
    py -3.14 scripts/train_edge_model.py --input-csv data/retrain_dataset.csv --temporal-split --temporal-date-column file_date
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Optional

import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
_SOC_SCRIPTS = _REPO / "Sports" / "Soccer" / "scripts"
if str(_SOC_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SOC_SCRIPTS))

_UNKNOWN_OPP = frozenset({"", "nan", "none", "null", "unknown", "unknown_opp", "—", "-", "n/a", "na"})
_UNKNOWN_TEAM = frozenset({"", "nan", "none", "null", "—", "-", "n/a", "na", "tbd", "unknown"})
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_GRADED_JSON_RE = re.compile(r"^graded_props_(\d{4}-\d{2}-\d{2})\.json$")


def _is_unknown_opp(v: object) -> bool:
    return str(v or "").strip().lower() in _UNKNOWN_OPP


def _is_unknown_team(v: object) -> bool:
    return str(v or "").strip().lower() in _UNKNOWN_TEAM


def _norm_player(v: object) -> str:
    s = unicodedata.normalize("NFKC", str(v or ""))
    s = s.casefold().strip()
    return re.sub(r"\s+", " ", s)


def _norm_prop(v: object) -> str:
    s = unicodedata.normalize("NFKC", str(v or ""))
    return re.sub(r"[\s_]+", "", s.casefold().strip())


def _norm_line(v: object) -> str:
    try:
        return str(round(float(str(v).replace(",", "")), 4))
    except (TypeError, ValueError):
        return str(v or "").strip().casefold()


def _norm_slot(v: object) -> str:
    return str(v or "").strip().casefold()


def _ppl_key(player: object, prop: object, line: object) -> str:
    return f"{_norm_player(player)}||{_norm_prop(prop)}||{_norm_line(line)}"


# ── step8 discovery ───────────────────────────────────────────────────────────
def _find_step8(date_dir: Path, date: str) -> Optional[Path]:
    candidates = [
        date_dir / f"step8_soccer_direction_clean_{date}.xlsx",
        date_dir / "soccer" / f"step8_soccer_direction_clean_{date}.xlsx",
        date_dir / "soccer" / "step8_soccer_direction_clean.xlsx",
    ]
    for p in candidates:
        if p.is_file():
            return p
    for p in sorted(date_dir.rglob("step8_soccer*.xlsx")):
        return p
    return None


def _find_graded_json(templates_dir: Path, date: str) -> Optional[Path]:
    p = templates_dir / f"graded_props_{date}.json"
    return p if p.is_file() else None


def _col(df: pd.DataFrame, *names: str) -> Optional[str]:
    lmap = {c.strip().lower(): c for c in df.columns}
    for n in names:
        if n.lower() in lmap:
            return lmap[n.lower()]
    return None


def build_step8_lookup(step8: Path) -> tuple[dict[str, dict], dict[str, tuple[str, str]]]:
    """
    Load step8 xlsx and return:
      1. player+prop+line lookup → {team, opp, def_tier}
      2. pair-map size (diagnostic only)

    Uses soccer_opp_utils.fill_opp_team_column on a synthetic pp_game_id keyed by
    Game Time (League is often just "SOCCER" and mixes fixtures if used as pair key).
    """
    from soccer_opp_utils import fill_opp_team_column, game_pair_map  # type: ignore

    df = pd.read_excel(step8, engine="openpyxl", dtype=str).fillna("")

    c_player = _col(df, "player", "Player")
    c_team = _col(df, "team", "Team")
    c_opp = _col(df, "opp", "Opp", "opp_team")
    c_prop = _col(df, "prop", "Prop", "stat_type", "prop_type")
    c_line = _col(df, "line", "Line")
    c_gametime = _col(df, "game time", "game_time", "Game Time", "gametime")
    c_deftier = _col(df, "def tier", "def_tier", "Def Tier", "DEF_TIER")

    if not all([c_player, c_team, c_opp, c_prop, c_line]):
        print(
            f"  [warn] Missing required columns in {step8.name}: "
            f"player={c_player} team={c_team} opp={c_opp} prop={c_prop} line={c_line}"
        )
        return {}, {}

    work = df.copy()
    work["pp_game_id"] = work[c_gametime].astype(str).str.strip() if c_gametime else ""
    work["team"] = work[c_team]
    work["opp_team"] = work[c_opp]
    filled = fill_opp_team_column(work)

    pair_map = game_pair_map(filled) if "pp_game_id" in filled.columns else {}

    ppl_lookup: dict[str, dict] = {}
    for _, row in filled.iterrows():
        team = str(row["team"]).strip()
        opp = str(row["opp_team"]).strip()
        if _is_unknown_team(team) or _is_unknown_opp(opp):
            continue
        key = _ppl_key(row[c_player], row[c_prop], row[c_line])
        dt = str(row[c_deftier]).strip() if c_deftier else ""
        if dt.lower() in ("", "nan", "none"):
            dt = ""
        ppl_lookup.setdefault(
            key,
            {"team": team, "opp": opp, "def_tier": dt},
        )

    return ppl_lookup, pair_map


def _json_key(entry: dict) -> str:
    player = entry.get("player") or entry.get("player_name") or ""
    prop = entry.get("stat_type") or entry.get("prop") or entry.get("prop_type") or ""
    line = entry.get("line")
    if line is None:
        line = entry.get("line_score")
    return _ppl_key(player, prop, line)


def patch_graded_json(
    json_path: Path,
    ppl_lookup: dict[str, dict],
    dry_run: bool,
) -> tuple[int, int, int, int]:
    """Patch Soccer rows in graded_props JSON. Returns (checked, team, opp, def_tier)."""
    data = json.loads(json_path.read_text(encoding="utf-8"))

    if isinstance(data, list):
        entries = data
        wrap_list = True
        root = None
    else:
        root = data
        entries = data.get("props") or data.get("picks") or data.get("data") or []
        wrap_list = False

    n_checked = n_team = n_opp = n_dt = 0
    changed = False

    for entry in entries:
        sport = str(entry.get("sport") or "").strip().upper()
        if sport not in ("SOCCER", "SOC"):
            continue

        n_checked += 1
        match = ppl_lookup.get(_json_key(entry))
        if not match:
            continue

        cur_team = str(entry.get("team") or "").strip()
        if _is_unknown_team(cur_team) and not _is_unknown_team(match["team"]):
            entry["team"] = match["team"]
            n_team += 1
            changed = True

        cur_opp = str(entry.get("opp_team") or entry.get("opp") or "").strip()
        if _is_unknown_opp(cur_opp) and not _is_unknown_opp(match["opp"]):
            entry["opp_team"] = match["opp"]
            n_opp += 1
            changed = True

        if match.get("def_tier"):
            cur_dt = str(entry.get("def_tier") or "").strip()
            if cur_dt.lower() in ("", "nan", "none"):
                entry["def_tier"] = match["def_tier"]
                n_dt += 1
                changed = True

    if not dry_run and changed:
        if wrap_list:
            out = entries
        else:
            out = dict(root)
            for k in ("props", "picks", "data"):
                if k in out:
                    out[k] = entries
                    break
        json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    return n_checked, n_team, n_opp, n_dt


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Backfill Soccer team+opp_team in graded JSON from step8 xlsx"
    )
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--date", default="", help="Single YYYY-MM-DD (default: all)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--min-unknown",
        type=int,
        default=1,
        help="Skip dates with fewer than this many UNKNOWN_OPP rows in step8 (default 1)",
    )
    args = ap.parse_args()

    repo = Path(args.repo_root).resolve()
    outputs = repo / "outputs"
    templates = repo / "ui_runner" / "templates"

    if not outputs.is_dir():
        print(f"outputs/ not found at {outputs}")
        return 1
    if not templates.is_dir():
        print(f"ui_runner/templates/ not found at {templates}")
        return 1

    if args.date:
        dates = [args.date.strip()[:10]]
    else:
        dates = sorted(
            p.name for p in outputs.iterdir() if p.is_dir() and _DATE_RE.fullmatch(p.name)
        )

    total_checked = total_team = total_opp = total_dt = 0
    dates_improved = dates_skipped = 0

    for date in dates:
        date_dir = outputs / date
        step8 = _find_step8(date_dir, date)
        if not step8:
            dates_skipped += 1
            continue

        graded_json = _find_graded_json(templates, date)
        if not graded_json:
            print(f"  [{date}] no graded_props_{date}.json in templates")
            continue

        try:
            probe = pd.read_excel(step8, engine="openpyxl", dtype=str).fillna("")
        except Exception as e:
            print(f"  [{date}] xlsx read error: {e}")
            continue

        opp_col = _col(probe, "opp", "Opp", "opp_team")
        if not opp_col:
            continue
        n_unk = int(probe[opp_col].apply(_is_unknown_opp).sum())
        if n_unk < args.min_unknown:
            continue

        try:
            ppl_lookup, pair_map = build_step8_lookup(step8)
        except Exception as e:
            print(f"  [{date}] lookup build failed: {e}")
            continue

        if not ppl_lookup:
            print(f"  [{date}] empty lookup (no recoverable rows in step8)")
            continue

        try:
            chk, tm, op, dt = patch_graded_json(graded_json, ppl_lookup, args.dry_run)
        except Exception as e:
            print(f"  [{date}] JSON patch error: {e}")
            continue

        total_checked += chk
        total_team += tm
        total_opp += op
        total_dt += dt

        if tm > 0 or op > 0:
            dates_improved += 1

        dry = "(dry)" if args.dry_run else "ok"
        print(
            f"  [{date}] step8 unk={n_unk:,} | "
            f"lookup={len(ppl_lookup):,} pairs={len(pair_map):,} | "
            f"graded: checked={chk:,} team={tm:,} opp={op:,} def_tier={dt:,}  {dry}"
        )

    dry_note = " (DRY RUN)" if args.dry_run else ""
    print(
        f"\n{'=' * 60}\n"
        f"Dates improved:      {dates_improved}\n"
        f"Dates skipped:       {dates_skipped} (no step8)\n"
        f"Graded rows checked: {total_checked:,}\n"
        f"team patched:        {total_team:,}\n"
        f"opp_team patched:    {total_opp:,}\n"
        f"def_tier patched:    {total_dt:,}\n"
        f"{dry_note}"
    )

    if not args.dry_run and (total_team > 0 or total_opp > 0):
        print(
            "\nNext:\n"
            "  py -3.14 scripts/build_retrain_dataset.py\n"
            "  py -3.14 scripts/train_edge_model.py "
            "--input-csv data/retrain_dataset.csv "
            "--temporal-split --temporal-date-column file_date\n"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
