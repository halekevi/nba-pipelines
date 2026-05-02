"""
Fill missing MLB opp_team in ui_runner/templates/graded_props_*.json using step8 CSV
rows matched by game_date + player + team + prop + line.

Looks for step8 in order:
  1) --step8-csv (default: MLB/step8_mlb_direction.csv)
  2) outputs/<date>/step8_mlb_direction_clean.csv
  3) outputs/<date>/step8_mlb_direction.csv

Usage:
  py -3 scripts/enrich_graded_props_mlb_opp.py
  py -3 scripts/enrich_graded_props_mlb_opp.py --date 2026-05-01
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "ui_runner" / "templates"

DATE_RE = re.compile(r"^graded_props_(\d{4}-\d{2}-\d{2})\.json$")

PLACEHOLDER_OPP = frozenset(
    {"", "—", "-", "–", "—", "n/a", "na", "tbd", "unknown"}
)


def _norm_line(v: object) -> str:
    s = str(v).strip() if v is not None else ""
    if not s:
        return ""
    try:
        f = float(s.replace(",", ""))
        if abs(f - round(f)) < 1e-9:
            return str(int(round(f)))
        return str(f)
    except ValueError:
        return s.casefold()


def _norm_player(v: object) -> str:
    s = str(v or "").strip().casefold()
    s = re.sub(r"\s+", " ", s)
    return s


def _norm_team(v: object) -> str:
    return str(v or "").strip().upper()


def _norm_prop(v: object) -> str:
    return str(v or "").strip().casefold()


def _row_game_date(row: dict[str, str]) -> str:
    raw = row.get("game_date") or row.get("Game Date") or ""
    s = str(raw).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return ""


def _is_placeholder_opp(s: str) -> bool:
    t = str(s).strip().replace("\u2013", "-").replace("\u2014", "-").casefold()
    return t in PLACEHOLDER_OPP or t == "-"


def load_step8_maps(
    paths: list[Path],
) -> dict[str, dict[tuple[str, str, str, str], str]]:
    """date_str -> {(player, team, prop, line): opp_team}."""
    by_date: dict[str, dict[tuple[str, str, str, str], str]] = defaultdict(dict)
    for path in paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row:
                    continue
                d = _row_game_date(row)
                if not d:
                    continue
                opp = _norm_team(row.get("opp_team") or row.get("Opp") or "")
                if not opp:
                    continue
                player = _norm_player(row.get("player") or row.get("Player") or "")
                team = _norm_team(row.get("team") or row.get("Team") or "")
                prop = _norm_prop(
                    row.get("prop_type") or row.get("Prop Type") or row.get("prop") or ""
                )
                line = _norm_line(row.get("line") or row.get("Line") or "")
                if not player or not team or not prop:
                    continue
                key = (player, team, prop, line)
                by_date[d][key] = opp
    return dict(by_date)


def enrich_file(
    json_path: Path,
    opp_by_date: dict[str, dict[tuple[str, str, str, str], str]],
) -> tuple[int, int]:
    """Returns (mlb_rows_updated, mlb_rows_considered)."""
    m = DATE_RE.match(json_path.name)
    if not m:
        return 0, 0
    date_str = m.group(1)
    lookup = opp_by_date.get(date_str, {})
    if not lookup:
        return 0, 0

    data = json.loads(json_path.read_text(encoding="utf-8"))
    props = data.get("props") or []
    updated = 0
    mlb_count = 0
    for r in props:
        if str(r.get("sport", "")).strip().upper() != "MLB":
            continue
        mlb_count += 1
        cur = str(r.get("opp_team", "") or "")
        if not _is_placeholder_opp(cur):
            continue
        key = (
            _norm_player(r.get("player")),
            _norm_team(r.get("team")),
            _norm_prop(r.get("prop")),
            _norm_line(r.get("line")),
        )
        new_opp = lookup.get(key)
        if not new_opp:
            continue
        r["opp_team"] = new_opp
        updated += 1
    if updated:
        data["count"] = len(props)
        json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return updated, mlb_count


def main() -> None:
    ap = argparse.ArgumentParser(description="Enrich MLB opp_team in graded_props JSON")
    ap.add_argument(
        "--date",
        default="",
        help="Only process graded_props_YYYY-MM-DD.json for this date (default: all)",
    )
    ap.add_argument(
        "--step8-csv",
        default="",
        help="Primary step8 CSV (default: MLB/step8_mlb_direction.csv)",
    )
    args = ap.parse_args()

    primary = Path(args.step8_csv) if args.step8_csv.strip() else ROOT / "MLB" / "step8_mlb_direction.csv"
    extra_paths: list[Path] = [primary]

    json_files: list[Path]
    if args.date.strip():
        p = TEMPLATES / f"graded_props_{args.date.strip()}.json"
        if not p.exists():
            print(f"ERROR: missing {p}", file=sys.stderr)
            sys.exit(1)
        json_files = [p]
        ds = args.date.strip()
        extra_paths.extend(
            [
                ROOT / "outputs" / ds / "step8_mlb_direction_clean.csv",
                ROOT / "outputs" / ds / "step8_mlb_direction.csv",
            ]
        )
    else:
        json_files = sorted(TEMPLATES.glob("graded_props_*.json"))
        for p in json_files:
            m = DATE_RE.match(p.name)
            if not m:
                continue
            ds = m.group(1)
            extra_paths.extend(
                [
                    ROOT / "outputs" / ds / "step8_mlb_direction_clean.csv",
                    ROOT / "outputs" / ds / "step8_mlb_direction.csv",
                ]
            )

    # Dedupe paths while preserving order
    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in extra_paths:
        rp = path.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        ordered.append(path)

    opp_by_date = load_step8_maps(ordered)
    if not opp_by_date:
        print(
            "WARN: No step8 rows loaded (check --step8-csv and outputs/<date>/ step8 files).",
            file=sys.stderr,
        )

    total_up = 0
    for jp in json_files:
        if not DATE_RE.match(jp.name):
            continue
        up, mlb_n = enrich_file(jp, opp_by_date)
        if up:
            print(f"OK {jp.name}: updated {up} MLB opp fields ({mlb_n} MLB rows)")
            total_up += up
    print(f"Done. Total MLB opp fields set: {total_up}")


if __name__ == "__main__":
    main()
