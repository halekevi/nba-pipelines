"""Load per-sport ML blend weight from models/prop_model_{sport}_blend_weight.json."""
from __future__ import annotations

import json
from pathlib import Path


def load_ml_blend_weight(repo_root: Path, sport: str, default: float = 0.30) -> float:
    p = repo_root / "models" / f"prop_model_{sport}_blend_weight.json"
    if not p.is_file():
        return default
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        w = data.get("blend_weight", default)
        return float(w)
    except Exception:
        return default
