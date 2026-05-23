"""Writable data root for grade_history.json, payout logs, and other durable artifacts."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def grade_history_read_paths(repo_root: Path, *, templates_dir: Path | None = None) -> list[Path]:
    """
    Resolution order for Income /api and grade-history consumers (matches Flask page_income).
    """
    seen: set[str] = set()
    out: list[Path] = []

    def _add(p: Path) -> None:
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)

    _add(persistent_data_dir(repo_root) / "grade_history.json")
    _add(repo_root / "data" / "grade_history.json")
    if templates_dir is not None:
        _add(templates_dir / "grade_history.json")
    return out


def persistent_data_dir(repo_root: Path) -> Path:
    """
    Prefer PROPORACLE_PERSISTENT_DATA_DIR / RAILWAY_VOLUME_MOUNT_PATH, then /app/data on Railway,
    else ``<repo_root>/data``.
    """
    for key in ("PROPORACLE_PERSISTENT_DATA_DIR", "RAILWAY_VOLUME_MOUNT_PATH"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
    if (os.environ.get("RAILWAY_ENVIRONMENT") or "").strip() and Path("/app/data").is_dir():
        return Path("/app/data").resolve()
    return (repo_root / "data").resolve()


def _parse_grade_history_runs(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict) and isinstance(raw.get("runs"), list):
        return [x for x in (raw.get("runs") or []) if isinstance(x, dict)]
    return []


def _grade_history_last_date(runs: list[dict[str, Any]]) -> str:
    dates = [str(r.get("date") or "").strip()[:10] for r in runs]
    dates = [d for d in dates if len(d) == 10 and d[4] == "-" and d[7] == "-"]
    return max(dates) if dates else ""


def load_best_grade_history_runs(
    repo_root: Path, *, templates_dir: Path | None = None
) -> list[dict[str, Any]]:
    """
    Load grade_history from all candidate paths and return the copy whose latest
    ``date`` is newest. Avoids a stale Railway volume masking a fresher bundled
    ``ui_runner/templates/grade_history.json``.
    """
    best_runs: list[dict[str, Any]] = []
    best_last = ""
    for path in grade_history_read_paths(repo_root, templates_dir=templates_dir):
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        runs = _parse_grade_history_runs(raw)
        last = _grade_history_last_date(runs)
        if last > best_last:
            best_last = last
            best_runs = runs
    return best_runs
