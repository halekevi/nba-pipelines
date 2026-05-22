"""Precomputed sport breakdown for /income (avoids scanning all graded_props on every request)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.proporacle_data_root import persistent_data_dir

SPORT_BREAKDOWN_ORDER = ("NBA", "CBB", "CFB", "WNBA", "MLB", "SOCCER", "TENNIS", "NHL", "NFL")

_SPORT_ALIASES = {
    "NCAAB": "CBB",
    "WCBB": "CBB",
    "NCAAF": "CFB",
    "NBA1Q": "NBA",
    "NBA1H": "NBA",
}


def normalize_sport_label(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    if not s:
        return ""
    return _SPORT_ALIASES.get(s, s)


def graded_props_signature(templates_dir: Path) -> str:
    files = sorted(templates_dir.glob("graded_props_*.json"))
    if not files:
        return "empty"
    mt = max(f.stat().st_mtime_ns for f in files)
    return f"n={len(files)}:mt={mt}"


def build_from_graded_props(
    templates_dir: Path,
    *,
    stake_per_pick: float = 10.0,
) -> list[dict[str, Any]]:
    stats: dict[str, dict[str, float]] = {
        s: {"decided": 0.0, "paid": 0.0, "net": 0.0} for s in SPORT_BREAKDOWN_ORDER
    }
    for fp in sorted(templates_dir.glob("graded_props_*.json")):
        try:
            payload = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        props = payload.get("props") if isinstance(payload, dict) else None
        if not isinstance(props, list):
            continue
        for row in props:
            if not isinstance(row, dict):
                continue
            sp = normalize_sport_label(row.get("sport"))
            if sp not in stats:
                continue
            result = str(row.get("result") or "").strip().upper()
            if result in {"", "NO_ACTUAL", "PENDING", "VOID", "PUSH"}:
                continue
            is_hit = result == "HIT"
            is_miss = result == "MISS"
            if not is_hit and not is_miss:
                continue
            stats[sp]["decided"] += 1.0
            if is_hit:
                stats[sp]["paid"] += 1.0
                stats[sp]["net"] += stake_per_pick
            else:
                stats[sp]["net"] -= stake_per_pick

    out: list[dict[str, Any]] = []
    for sp in SPORT_BREAKDOWN_ORDER:
        decided = int(stats[sp]["decided"])
        paid = int(stats[sp]["paid"])
        win_rate = (paid / decided) if decided > 0 else None
        out.append(
            {
                "sport": sp,
                "decided": decided,
                "paid": paid,
                "win_rate": win_rate,
                "net_dollars": round(float(stats[sp]["net"]), 2),
            }
        )
    return out


def cache_paths(repo_root: Path, templates_dir: Path) -> list[Path]:
    return [
        persistent_data_dir(repo_root) / "sport_breakdown.json",
        templates_dir / "sport_breakdown.json",
    ]


def write_cache(
    repo_root: Path,
    templates_dir: Path,
    rows: list[dict[str, Any]],
    *,
    source: str = "graded_props_json",
) -> Path | None:
    payload = {
        "ok": True,
        "rows": rows,
        "source": source,
        "signature": graded_props_signature(templates_dir),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    written: Path | None = None
    for path in cache_paths(repo_root, templates_dir):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            written = path
        except OSError:
            continue
    return written


def read_cached_rows(
    repo_root: Path,
    templates_dir: Path,
    *,
    expected_signature: str | None = None,
) -> list[dict[str, Any]] | None:
    for path in cache_paths(repo_root, templates_dir):
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict) or not isinstance(raw.get("rows"), list):
            continue
        sig = str(raw.get("signature") or "")
        if expected_signature and sig and sig != expected_signature:
            continue
        return list(raw["rows"])
    return None


def refresh_cache(
    repo_root: Path,
    templates_dir: Path,
    *,
    stake_per_pick: float = 10.0,
) -> list[dict[str, Any]]:
    rows = build_from_graded_props(templates_dir, stake_per_pick=stake_per_pick)
    write_cache(repo_root, templates_dir, rows)
    return rows
