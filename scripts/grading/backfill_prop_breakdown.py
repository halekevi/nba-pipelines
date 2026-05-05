from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent.parent
TEMPLATES_DIR = ROOT_DIR / "ui_runner" / "templates"
GRADED_SLATE_DIR = ROOT_DIR / "ui_runner" / "graded_slate"
MOBILE_WWW_DATA_DIR = ROOT_DIR / "mobile" / "www" / "data"
ARCHIVE_DATES_PATH = ROOT_DIR / "mobile" / "www" / "grades_archive_dates.json"
BUILD_SCRIPT = SCRIPT_DIR / "build_grades_html.py"

sys.path.insert(0, str(SCRIPT_DIR))
import build_grades_html as bgh  # noqa: E402


@dataclass
class DateResult:
    date: str
    status: str
    note: str = ""


def read_archive_dates() -> list[str]:
    try:
        payload = json.loads(ARCHIVE_DATES_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Could not read archive dates: {ARCHIVE_DATES_PATH} ({exc})") from exc
    dates = payload.get("dates")
    if not isinstance(dates, list):
        raise RuntimeError("grades_archive_dates.json missing 'dates' list")
    out = [str(d).strip() for d in dates if str(d).strip()]
    out.sort()
    return out


def graded_props_name(date_str: str) -> str:
    return f"graded_props_{date_str}.json"


def slate_eval_name(date_str: str) -> str:
    return f"slate_eval_{date_str}.html"


def read_props_rows(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    props = payload.get("props")
    if not isinstance(props, list):
        return []
    return [p for p in props if isinstance(p, dict)]


def write_prop_breakdown_rows(path: Path, rows: list[dict[str, Any]], dry_run: bool) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["prop_breakdown_rows"] = rows
    if dry_run:
        return
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def candidate_json_paths(date_str: str) -> list[Path]:
    name = graded_props_name(date_str)
    paths: list[Path] = [TEMPLATES_DIR / name]
    if (GRADED_SLATE_DIR / date_str / name).exists():
        paths.append(GRADED_SLATE_DIR / date_str / name)
    if (MOBILE_WWW_DATA_DIR / name).exists():
        paths.append(MOBILE_WWW_DATA_DIR / name)
    # De-dup while preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        k = str(p.resolve()) if p.exists() else str(p)
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out


def run_build_for_date(date_str: str, dry_run: bool) -> tuple[bool, str]:
    cmd = [
        sys.executable,
        str(BUILD_SCRIPT),
        "--date",
        date_str,
        "--out",
        str(TEMPLATES_DIR),
        "--allow-empty",
    ]
    if dry_run:
        return True, "dry-run build skipped"
    proc = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return False, err[-4000:]
    return True, "ok"


def validate_outputs(date_str: str) -> tuple[bool, str]:
    gp = TEMPLATES_DIR / graded_props_name(date_str)
    html = TEMPLATES_DIR / slate_eval_name(date_str)
    if not gp.exists():
        return False, "missing graded props json"
    if not html.exists():
        return False, "missing slate eval html"
    try:
        payload = json.loads(gp.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"invalid json: {exc}"
    pbr = payload.get("prop_breakdown_rows")
    if not isinstance(pbr, list) or len(pbr) <= 0:
        return False, "prop_breakdown_rows missing or empty"
    text = html.read_text(encoding="utf-8", errors="ignore")
    if "Best / Worst" not in text or "Heatmap" not in text:
        return False, "widget tab markup missing"
    if "STANDARD — TOP PROP TYPES" in text or "OVERALL WORST PROP TYPES" in text:
        return False, "legacy card markup still present"
    return True, "ok"


def process_dates(dates: list[str], dry_run: bool) -> list[DateResult]:
    results: list[DateResult] = []
    cumulative_props: list[dict[str, Any]] = []

    for date_str in dates:
        ok, note = run_build_for_date(date_str, dry_run=dry_run)
        if not ok:
            results.append(DateResult(date=date_str, status="error", note=f"build failed: {note}"))
            continue

        t_path = TEMPLATES_DIR / graded_props_name(date_str)
        if not t_path.exists():
            results.append(DateResult(date=date_str, status="skip", note="no graded props file after build"))
            continue

        current_props = read_props_rows(t_path)
        if not current_props:
            results.append(DateResult(date=date_str, status="skip", note="graded props has no rows"))
            continue

        cumulative_props.extend(current_props)
        pbr = bgh.build_prop_breakdown_rows(cumulative_props)
        targets = [p for p in candidate_json_paths(date_str) if p.exists()]
        if not targets:
            results.append(DateResult(date=date_str, status="skip", note="no target json paths exist"))
            continue
        try:
            for p in targets:
                write_prop_breakdown_rows(p, pbr, dry_run=dry_run)
        except Exception as exc:
            results.append(DateResult(date=date_str, status="error", note=f"write failed: {exc}"))
            continue

        if dry_run:
            results.append(DateResult(date=date_str, status="success", note=f"dry-run; rows={len(pbr)}"))
            continue

        valid_ok, valid_note = validate_outputs(date_str)
        if not valid_ok:
            results.append(DateResult(date=date_str, status="error", note=f"validation failed: {valid_note}"))
            continue
        results.append(DateResult(date=date_str, status="success", note=f"rows={len(pbr)}"))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill prop_breakdown_rows and widget HTML across graded dates.")
    parser.add_argument("--date", type=str, default="", help="Optional single date YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print actions without writing")
    args = parser.parse_args()

    all_dates = read_archive_dates()
    if args.date:
        dates = [args.date.strip()]
    else:
        dates = all_dates

    results = process_dates(dates, dry_run=args.dry_run)
    counts = {"success": 0, "skip": 0, "error": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
        print(f"[{r.status.upper():7}] {r.date} :: {r.note}")

    print("")
    print(
        f"Summary: success={counts.get('success', 0)} "
        f"skip={counts.get('skip', 0)} error={counts.get('error', 0)} total={len(results)}"
    )
    if counts.get("error", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

