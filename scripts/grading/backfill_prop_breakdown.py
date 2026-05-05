from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GRADED_SLATE_DIR = ROOT / "ui_runner" / "graded_slate"
TEMPLATES_DIR = ROOT / "ui_runner" / "templates"
MOBILE_WWW_DIR = ROOT / "mobile" / "www"
BUILD_SCRIPT = ROOT / "scripts" / "grading" / "build_grades_html.py"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SENTINEL_DATE = "2098-12-31"


def is_date_folder(name: str) -> bool:
    return bool(DATE_RE.match(name)) and name != SENTINEL_DATE


def graded_props_path_for_date(date_str: str) -> Path:
    by_folder = GRADED_SLATE_DIR / date_str / f"graded_props_{date_str}.json"
    if by_folder.exists():
        return by_folder
    return TEMPLATES_DIR / f"graded_props_{date_str}.json"


def discover_dates() -> list[str]:
    dates: set[str] = set()

    if GRADED_SLATE_DIR.exists():
        for p in GRADED_SLATE_DIR.iterdir():
            if p.is_dir() and is_date_folder(p.name):
                gp = p / f"graded_props_{p.name}.json"
                if gp.exists():
                    dates.add(p.name)

    for p in TEMPLATES_DIR.glob("graded_props_*.json"):
        m = re.match(r"graded_props_(\d{4}-\d{2}-\d{2})\.json$", p.name)
        if not m:
            continue
        d = m.group(1)
        if is_date_folder(d):
            dates.add(d)

    return sorted(dates)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_def(raw: Any) -> str:
    s = str(raw or "").strip().lower().replace("🟢", "").replace("🟡", "").replace("🔴", "")
    s = s.replace(" ", "_")
    if s in {"elite"}:
        return "elite"
    if s in {"above_avg", "above_average"}:
        return "above_avg"
    if s in {"avg", "average"}:
        return "avg"
    if s in {"below_avg", "below_average"}:
        return "below_avg"
    if s in {"weak", "very_weak"}:
        return "weak"
    return "avg"


def normalize_tier(raw: Any) -> str:
    t = str(raw or "").strip().upper()
    return t if t in {"A", "B", "C", "D"} else ""


def normalize_pt(pick_type: Any, direction: Any, over_under: Any) -> str:
    pt = str(pick_type or "").strip().lower()
    side = str(direction or over_under or "").strip().lower()

    if "standard" in pt:
        if "under" in side:
            return "Standard Under"
        return "Standard Over"
    if "goblin" in pt:
        return "Goblin"
    if "demon" in pt:
        return "Demon"
    if "under" in side:
        return "Under"
    return "Over"


def is_hit(row: dict[str, Any]) -> bool:
    r = str(row.get("result", "") or row.get("Result", "")).strip().upper()
    return r in {"HIT", "WIN", "W", "1", "TRUE", "YES"}


def is_miss(row: dict[str, Any]) -> bool:
    r = str(row.get("result", "") or row.get("Result", "")).strip().upper()
    return r in {"MISS", "LOSS", "L", "0", "FALSE", "NO"}


def build_prop_breakdown_rows(cumulative_props: list[dict[str, Any]]) -> list[dict[str, Any]]:
    agg: dict[tuple[str, str, str, str], dict[str, int]] = defaultdict(lambda: {"decided": 0, "hits": 0})

    for row in cumulative_props:
        if not isinstance(row, dict):
            continue
        if not (is_hit(row) or is_miss(row)):
            continue

        prop = str(row.get("prop") or row.get("Prop") or "").strip()
        if not prop:
            continue

        pt = normalize_pt(row.get("pick_type"), row.get("direction"), row.get("over_under"))
        tier = normalize_tier(row.get("tier"))
        if not tier:
            continue
        dfn = normalize_def(row.get("def_tier"))

        key = (prop, pt, tier, dfn)
        agg[key]["decided"] += 1
        if is_hit(row):
            agg[key]["hits"] += 1

    rows: list[dict[str, Any]] = []
    for (prop, pt, tier, dfn), v in agg.items():
        rows.append(
            {
                "prop": prop,
                "pt": pt,
                "tier": tier,
                "def": dfn,
                "decided": int(v["decided"]),
                "hits": int(v["hits"]),
            }
        )
    rows.sort(key=lambda r: (r["prop"], r["pt"], r["tier"], r["def"]))
    return rows


def write_json(path: Path, payload: dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        return
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_build_html(date_str: str, dry_run: bool, no_html: bool) -> tuple[bool, str]:
    if dry_run or no_html:
        return True, "skipped"
    cmd = [sys.executable, str(BUILD_SCRIPT), "--date", date_str]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        return False, msg[-2000:]
    return True, "ok"


def remove_if_exists(path: Path, dry_run: bool) -> bool:
    if not path.exists():
        return False
    if dry_run:
        return True
    if path.is_dir():
        for sub in sorted(path.rglob("*"), reverse=True):
            if sub.is_file() or sub.is_symlink():
                sub.unlink(missing_ok=True)
            elif sub.is_dir():
                sub.rmdir()
        path.rmdir()
    else:
        path.unlink(missing_ok=True)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill prop_breakdown_rows across historical graded dates.")
    parser.add_argument("--date", default="", help="Optional single date YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing")
    parser.add_argument("--no-html", action="store_true", help="Skip build_grades_html regeneration")
    args = parser.parse_args()

    # Cleanup sentinels / accidental duplicate path.
    removed_sentinel_dir = remove_if_exists(GRADED_SLATE_DIR / SENTINEL_DATE, args.dry_run)
    removed_nested_templates = remove_if_exists(ROOT / "ui_runner" / "ui_runner", args.dry_run)

    # Optional sentinel files in active outputs.
    remove_if_exists(TEMPLATES_DIR / f"graded_props_{SENTINEL_DATE}.json", args.dry_run)
    remove_if_exists(TEMPLATES_DIR / f"slate_eval_{SENTINEL_DATE}.html", args.dry_run)
    remove_if_exists(MOBILE_WWW_DIR / f"graded_props_{SENTINEL_DATE}.json", args.dry_run)
    remove_if_exists(MOBILE_WWW_DIR / f"slate_eval_{SENTINEL_DATE}.html", args.dry_run)

    if args.date:
        dates = [args.date.strip()]
    else:
        dates = discover_dates()

    if not dates:
        print("No dates discovered.")
        return

    cumulative: list[dict[str, Any]] = []
    for d in dates:
        path = graded_props_path_for_date(d)
        if not path.exists():
            print(f"{d} | rows_added=0 | status=missing_json ({path})")
            continue

        payload = load_json(path)
        props = payload.get("props")
        if not isinstance(props, list):
            print(f"{d} | rows_added=0 | status=invalid_props")
            continue

        cumulative.extend([p for p in props if isinstance(p, dict)])
        pbr = build_prop_breakdown_rows(cumulative)

        payload["prop_breakdown_rows"] = pbr
        write_json(path, payload, args.dry_run)

        # Mirror to mobile/www if present there.
        mobile_path = MOBILE_WWW_DIR / path.name
        if mobile_path.exists():
            mobile_payload = load_json(mobile_path)
            mobile_payload["prop_breakdown_rows"] = pbr
            write_json(mobile_path, mobile_payload, args.dry_run)

        ok, note = run_build_html(d, args.dry_run, args.no_html)
        status = "ok" if ok else f"html_error: {note}"
        print(f"{d} | rows_added={len(pbr)} | status={status}")

    print(
        "cleanup | sentinel_dir_removed={} | nested_ui_runner_removed={}".format(
            removed_sentinel_dir,
            removed_nested_templates,
        )
    )


if __name__ == "__main__":
    main()
