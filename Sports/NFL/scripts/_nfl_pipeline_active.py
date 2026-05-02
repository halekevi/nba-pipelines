"""NFL scaffold gate: downstream steps stay off until explicitly enabled."""

from __future__ import annotations

import os
import sys


def require_nfl_pipeline_active_or_exit() -> None:
    """If NFL_PIPELINE_ACTIVE is not 1, print a message and exit 0 (safe for cron)."""
    if os.environ.get("NFL_PIPELINE_ACTIVE", "").strip() == "1":
        return
    print("[NFL] Pipeline not yet active")
    sys.exit(0)
