"""OOS metrics for model promotion (Phase 2): Brier, log loss, bucket ECE."""

from __future__ import annotations

import math
from typing import Iterable


def brier_score(y: Iterable[int], p: Iterable[float]) -> float:
    ys = [int(x) for x in y]
    ps = [float(x) for x in p]
    if len(ys) != len(ps) or not ys:
        raise ValueError("y and p same non-empty length")
    return sum((pi - yi) ** 2 for yi, pi in zip(ys, ps, strict=True)) / len(ys)


def log_loss(y: Iterable[int], p: Iterable[float], eps: float = 1e-15) -> float:
    ys = [int(x) for x in y]
    ps = [min(1 - eps, max(eps, float(x))) for x in p]
    if len(ys) != len(ps) or not ys:
        raise ValueError("y and p same non-empty length")
    return -sum(
        yi * math.log(pi) + (1 - yi) * math.log(1 - pi) for yi, pi in zip(ys, ps, strict=True)
    ) / len(ys)


def ece_bins(y: Iterable[int], p: Iterable[float], n_bins: int = 10) -> float:
    """Expected calibration error: mean |accuracy - confidence| per bin."""
    pairs = list(zip([int(x) for x in y], [float(x) for x in p], strict=True))
    if not pairs:
        return 0.0
    bins: dict[int, list[tuple[int, float]]] = {i: [] for i in range(n_bins)}
    for yi, pi in pairs:
        b = min(n_bins - 1, int(pi * n_bins))
        bins[b].append((yi, pi))
    ece = 0.0
    n = len(pairs)
    for _, items in bins.items():
        if not items:
            continue
        acc = sum(yi for yi, _ in items) / len(items)
        conf = sum(pi for _, pi in items) / len(items)
        ece += abs(acc - conf) * (len(items) / n)
    return ece
