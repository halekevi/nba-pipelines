"""Writable data root for grade_history.json, payout logs, and other durable artifacts."""
from __future__ import annotations

import os
from pathlib import Path


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
