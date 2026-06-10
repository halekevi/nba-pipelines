#!/usr/bin/env python3
"""Sport-scoped ticket ML registry: training filters, model paths, runtime sport inference."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"

# Training + inference sport keys (combined = all tickets; mixed = multi-sport parlays).
TICKET_ML_SPORT_KEYS: tuple[str, ...] = (
    "combined",
    "nba",
    "wnba",
    "nhl",
    "mlb",
    "tennis",
    "soccer",
    "cbb",
    "mixed",
)

NBA_FAMILY = frozenset({"NBA", "NBA1Q", "NBA1H"})
CBB_FAMILY = frozenset({"CBB", "WCBB"})

MIN_ROWS_COMBINED = 80
MIN_ROWS_SPORT = 40
MIN_ROWS_BUCKET = 80


def sport_family(sport: Any) -> str:
    su = str(sport or "").strip().upper()
    if not su or su == "NAN":
        return ""
    if su in NBA_FAMILY:
        return "nba"
    if su in CBB_FAMILY:
        return "cbb"
    if su == "WNBA":
        return "wnba"
    if su == "NHL":
        return "nhl"
    if su == "MLB":
        return "mlb"
    if su == "TENNIS":
        return "tennis"
    if su in ("SOCCER", "EPL"):
        return "soccer"
    if su == "NFL":
        return "nfl"
    if su == "CFB":
        return "cfb"
    return su.lower()


def infer_ticket_sport_key(ticket: dict[str, Any]) -> str:
    """Map a slip to a sport ML bucket for model selection."""
    legs = [leg for leg in (ticket.get("legs") or ticket.get("rows") or []) if isinstance(leg, dict)]
    families = {sport_family(leg.get("sport")) for leg in legs}
    families.discard("")
    if not families:
        return "combined"
    if len(families) > 1:
        return "mixed"
    return next(iter(families))


def _row_sport_families(row: pd.Series) -> set[str]:
    dom = sport_family(row.get("dominant_sport"))
    if dom:
        try:
            n_sports = int(row.get("sports_in_ticket") or 0)
        except (TypeError, ValueError):
            n_sports = 0
        if n_sports <= 1:
            return {dom}
    fams: set[str] = set()
    for col, fam in (
        ("legs_nba", "nba"),
        ("legs_wnba", "wnba"),
        ("legs_nhl", "nhl"),
        ("legs_mlb", "mlb"),
        ("legs_soccer", "soccer"),
        ("legs_cbb", "cbb"),
    ):
        try:
            n = int(row.get(col) or 0)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            fams.add(fam)
    if len(fams) > 1:
        return fams
    if len(fams) == 1:
        return fams
    if dom:
        return {dom}
    return set()


def filter_training_rows(df: pd.DataFrame, sport_key: str) -> pd.DataFrame:
    """Subset ticket training rows for a sport-specific or combined model."""
    key = str(sport_key or "combined").strip().lower()
    if key == "combined":
        return df.copy()
    if key == "mixed":
        out = df[pd.to_numeric(df.get("sports_in_ticket", 0), errors="coerce").fillna(0) > 1].copy()
        return out
    rows: list[bool] = []
    for _, row in df.iterrows():
        fams = _row_sport_families(row)
        if len(fams) != 1:
            rows.append(False)
            continue
        rows.append(next(iter(fams)) == key)
    return df.loc[rows].copy()


def dataset_path_for_sport(sport_key: str, ml_dir: Path | None = None) -> Path:
    base = ml_dir or (ROOT / "data" / "ml")
    key = str(sport_key or "combined").strip().lower()
    if key == "combined":
        return base / "ticket_training_dataset.csv"
    return base / f"ticket_training_dataset_{key}.csv"


def model_artifact_paths(sport_key: str, models_dir: Path | None = None) -> dict[str, Path]:
    """Paths for model, features, metadata, and leg-count bucket models."""
    mdir = models_dir or MODELS_DIR
    key = str(sport_key or "combined").strip().lower()
    suffix = "" if key == "combined" else f"_{key}"
    return {
        "model": mdir / f"ticket_model{suffix}.pkl",
        "features": mdir / f"ticket_model{suffix}_features.json",
        "metadata": mdir / f"ticket_model{suffix}_metadata.json",
        "2leg": mdir / f"ticket_model{suffix}_2leg.pkl",
        "3leg": mdir / f"ticket_model{suffix}_3leg.pkl",
        "4plus": mdir / f"ticket_model{suffix}_4plus.pkl",
    }


def min_rows_for_sport(sport_key: str) -> int:
    return MIN_ROWS_COMBINED if str(sport_key).lower() == "combined" else MIN_ROWS_SPORT


def sport_display_name(sport_key: str) -> str:
    names = {
        "combined": "Combined (all sports)",
        "nba": "NBA family (NBA / NBA1Q / NBA1H)",
        "wnba": "WNBA",
        "nhl": "NHL",
        "mlb": "MLB",
        "tennis": "Tennis",
        "soccer": "Soccer",
        "cbb": "CBB / WCBB",
        "mixed": "Multi-sport parlays",
    }
    return names.get(str(sport_key).lower(), sport_key)


REGISTRY_PATH = ROOT / "data" / "ml" / "ticket_model_registry.json"


def load_ticket_model_registry() -> dict[str, Any]:
    if not REGISTRY_PATH.is_file():
        return {}
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def sport_model_auc_test(sport_key: str) -> float | None:
    """Holdout AUC for a sport ticket model (None if untrained / missing)."""
    reg = load_ticket_model_registry()
    ent = (reg.get("sports") or {}).get(str(sport_key or "").strip().lower()) or {}
    if not ent.get("trained"):
        return None
    try:
        return float(ent["auc_test"])
    except (TypeError, ValueError, KeyError):
        return None


def ticket_rerank_weight_for_sport(sport_key: str, base_weight: float) -> float:
    """
    Scale ticket-model rerank blend by sport model quality.
    Weak buckets (AUC < 0.55) get zero weight; strong buckets get up to 1.8× base.
    """
    base = max(0.0, min(1.0, float(base_weight)))
    auc = sport_model_auc_test(sport_key)
    if auc is None:
        return base * 0.5
    if auc >= 0.68:
        return min(0.45, base * 1.8)
    if auc >= 0.62:
        return base
    if auc < 0.55:
        return 0.0
    return base * 0.65
