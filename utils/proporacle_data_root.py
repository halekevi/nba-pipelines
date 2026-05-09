"""Writable data root for grade_history.json, payout logs, and other durable artifacts."""
from __future__ import annotations

import os
from pathlib import Path


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
