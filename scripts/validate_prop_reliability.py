#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _norm(x: Any) -> str:
    return str(x or "").strip()


def _up(x: Any) -> str:
    return _norm(x).upper()


def _norm_prop(x: Any) -> str:
    s = _norm(x).lower().replace("_", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def _line_bucket(v: Any) -> str:
    try:
        x = abs(float(v))
    except Exception:
        return "(missing)"
    if x < 1.5:
        return "micro"
    if x < 5:
        return "low"
    if x < 15:
        return "mid"
    if x < 30:
        return "high"
    return "xl"


def _iter_graded_props(template_dir: Path) -> list[dict[str, Any]]:
    pat = re.compile(r"^graded_props_\d{4}-\d{2}-\d{2}\.json$")
    rows: list[dict[str, Any]] = []
    for p in sorted(template_dir.glob("graded_props_*.json")):
        if not pat.match(p.name):
            continue
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for r in payload.get("props", []):
            if isinstance(r, dict):
                rows.append(r)
    return rows


def _load_overrides(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = payload.get("overrides", []) if isinstance(payload, dict) else []
    return [r for r in rows if isinstance(r, dict)]


def _override_key(r: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        _up(r.get("sport")),
        _norm_prop(r.get("prop_type")),
        _up(r.get("direction")),
        _norm(r.get("line_bucket")).lower(),
    )


def _reliability_from_stats(decided_n: int, hit_rate: float, zero_rate: float, void_rate: float) -> tuple[float, str]:
    # Base confidence increases with sample and closeness to fair range.
    sample = min(1.0, decided_n / 200.0)
    hr_pen = min(1.0, abs(hit_rate - 0.5) / 0.5)
    # Penalize extreme labels + low coverage artifacts.
    score = 0.55 * sample + 0.30 * (1.0 - hr_pen) + 0.15 * max(0.0, 1.0 - void_rate)
    if decided_n >= 40 and (hit_rate <= 0.03 or hit_rate >= 0.97):
        score -= 0.35
    if decided_n >= 40 and zero_rate >= 0.90:
        score -= 0.25
    score = max(0.0, min(1.0, score))
    status = "RELIABLE" if score >= 0.60 else ("WATCHLIST" if score >= 0.35 else "UNRELIABLE")
    return score, status


def build_report(repo_root: Path, min_n: int, overrides_path: Path) -> dict[str, Any]:
    tpl = repo_root / "ui_runner" / "templates"
    rows = _iter_graded_props(tpl)
    agg: dict[tuple[str, str, str, str], dict[str, float]] = defaultdict(lambda: {"dec": 0.0, "hit": 0.0, "void": 0.0, "zero": 0.0, "actual_n": 0.0})

    for r in rows:
        sport = _up(r.get("sport")) or "UNKNOWN"
        prop = _norm_prop(r.get("prop"))
        direction = _up(r.get("direction")) or "UNKNOWN"
        bucket = _line_bucket(r.get("line"))
        k = (sport, prop, direction, bucket)
        res = _up(r.get("result"))
        if res in {"HIT", "MISS"}:
            agg[k]["dec"] += 1
            if res == "HIT":
                agg[k]["hit"] += 1
            try:
                actual = float(r.get("actual_value"))
                agg[k]["actual_n"] += 1
                if abs(actual) < 1e-9:
                    agg[k]["zero"] += 1
            except Exception:
                pass
        elif res in {"VOID", "PUSH", "NO_DATA", "DNP"}:
            agg[k]["void"] += 1

    out_rows: list[dict[str, Any]] = []
    for (sport, prop, direction, bucket), v in agg.items():
        dec = int(v["dec"])
        if dec < min_n:
            continue
        hit_rate = float(v["hit"] / dec) if dec else 0.0
        actual_n = int(v["actual_n"])
        zero_rate = float(v["zero"] / actual_n) if actual_n else 0.0
        void_rate = float(v["void"] / max(1.0, (v["void"] + v["dec"])))
        score, status = _reliability_from_stats(dec, hit_rate, zero_rate, void_rate)
        out_rows.append(
            {
                "sport": sport,
                "prop_type": prop,
                "direction": direction,
                "line_bucket": bucket,
                "decided_n": dec,
                "hit_rate": round(hit_rate, 4),
                "zero_rate": round(zero_rate, 4),
                "void_rate": round(void_rate, 4),
                "reliability_score": round(score, 4),
                "status": status,
            }
        )

    out_rows.sort(key=lambda x: (x["sport"], x["prop_type"], x["direction"], x["line_bucket"]))
    overrides = _load_overrides(overrides_path)
    override_map = {_override_key(o): o for o in overrides}
    overrides_applied = 0
    if override_map:
        for r in out_rows:
            ok = _override_key(r)
            ov = override_map.get(ok)
            if not ov:
                continue
            forced_status = _up(ov.get("status"))
            if forced_status in {"RELIABLE", "WATCHLIST", "UNRELIABLE"}:
                r["status"] = forced_status
                if forced_status == "UNRELIABLE":
                    r["reliability_score"] = min(float(r.get("reliability_score", 0.0)), 0.10)
                elif forced_status == "RELIABLE":
                    r["reliability_score"] = max(float(r.get("reliability_score", 0.0)), 0.70)
                r["override_note"] = _norm(ov.get("note"))
                overrides_applied += 1

    flagged = [r for r in out_rows if r["status"] == "UNRELIABLE"]
    return {
        "rows": out_rows,
        "summary": {
            "rows_total": len(out_rows),
            "unreliable_count": len(flagged),
            "watchlist_count": sum(1 for r in out_rows if r["status"] == "WATCHLIST"),
            "reliable_count": sum(1 for r in out_rows if r["status"] == "RELIABLE"),
            "min_n": min_n,
            "overrides_path": str(overrides_path),
            "overrides_defined": len(overrides),
            "overrides_applied": overrides_applied,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build prop reliability index from historical graded_props JSON files.")
    ap.add_argument("--min-n", type=int, default=40, help="Minimum decided sample per bucket.")
    ap.add_argument("--out-json", default="", help="Optional path for JSON output.")
    ap.add_argument(
        "--overrides-json",
        default="",
        help="Optional override JSON (default: config/prop_reliability_overrides.json)",
    )
    args = ap.parse_args()

    root = _repo_root()
    overrides_path = Path(args.overrides_json) if args.overrides_json else (root / "config" / "prop_reliability_overrides.json")
    report = build_report(root, int(args.min_n), overrides_path)
    out = Path(args.out_json) if args.out_json else (root / "data" / "reports" / "prop_reliability_latest.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    s = report["summary"]
    print(
        f"Wrote {out} | rows={s['rows_total']} reliable={s['reliable_count']} "
        f"watchlist={s['watchlist_count']} unreliable={s['unreliable_count']}"
    )


if __name__ == "__main__":
    main()

