"""
Soccer Goblin/Demon standard-line offsets (shared step2 backfill + step7 projections).

Goblin lines sit below the Standard line; Demon lines sit above it.
``estimate_*`` returns the implied Standard line from an alt line + deviation_level.
"""

from __future__ import annotations

import math

# Mirrors step7_rank_props_soccer._projection_from_row offset maps.
GOBLIN_STANDARD_LINE_OFFSET: dict[int, float] = {1: 1.0, 2: 1.5, 3: 2.0}
DEMON_STANDARD_LINE_OFFSET: dict[int, float] = {1: -1.0, 2: -2.0, 3: -3.0}


def _coerce_dev_level(deviation_level: object) -> int:
    try:
        dev = int(float(deviation_level))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        dev = 0
    return dev if dev > 0 else 1


def estimate_goblin_standard_line(line: object, deviation_level: object) -> float | None:
    """Infer Standard line from a Goblin alt line (sibling missing on slate)."""
    try:
        line_val = float(line)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(line_val):
        return None
    dev = _coerce_dev_level(deviation_level)
    offset = GOBLIN_STANDARD_LINE_OFFSET.get(dev, GOBLIN_STANDARD_LINE_OFFSET[1])
    return float(line_val + offset)


def estimate_demon_standard_line(line: object, deviation_level: object) -> float | None:
    """Infer Standard line from a Demon alt line (not used in step2 backfill today)."""
    try:
        line_val = float(line)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(line_val):
        return None
    dev = _coerce_dev_level(deviation_level)
    offset = DEMON_STANDARD_LINE_OFFSET.get(dev, DEMON_STANDARD_LINE_OFFSET[1])
    return float(line_val + offset)


def estimate_standard_line_from_pick_type(
    pick_type: str,
    line: object,
    deviation_level: object,
) -> float | None:
    """Pick-type aware Standard-line estimate (step7 projection parity)."""
    pt = str(pick_type or "").strip().lower()
    if "gob" in pt:
        return estimate_goblin_standard_line(line, deviation_level)
    if "dem" in pt:
        return estimate_demon_standard_line(line, deviation_level)
    try:
        return float(line)
    except (TypeError, ValueError):
        return None
