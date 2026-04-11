#!/usr/bin/env python3
"""Emit data/payout_rate_cards.json — reference rates + UI-ready card deck for /payout."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "payout_rate_cards.json"
COEFF_PATH = ROOT / "data" / "payout_formula_coefficients.json"

# Align with scripts/fit_payout_formula.py and ui_runner/components/payout_calculator.jsx
POWER_FIRST_STANDARD = {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0, 6: 37.5}
FLEX_FIRST_STANDARD = {
    2: {2: 3.0},
    3: {3: 3.0, 2: 1.0},
    4: {4: 6.0, 3: 1.5},
    5: {5: 10.0, 4: 2.0, 3: 0.4},
    6: {6: 25.0, 5: 2.0, 4: 0.4},
}
FLEX_MIN_GUARANTEE_BASE = {2: 1.5, 3: 1.25, 4: 1.5, 5: 2.0, 6: 2.0}
GOBLIN_POWER = {1: 0.84, 2: 0.747, 3: 0.707}
GOBLIN_FLEX = {1: 0.8, 2: 0.72, 3: 0.6}
DEMON_POWER = {1: 1.627, 2: 2.4, 3: 2.72}
DEMON_FLEX = {1: 1.6, 2: 1.52, 3: 1.56}


def _sk(d: dict[int, float]) -> dict[str, float]:
    return {str(k): float(v) for k, v in sorted(d.items())}


def _flex_json() -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for n, row in sorted(FLEX_FIRST_STANDARD.items()):
        out[str(n)] = {str(k): float(v) for k, v in sorted(row.items(), key=lambda x: -x[0])}
    return out


def load_fitted() -> dict | None:
    if not COEFF_PATH.is_file():
        return None
    try:
        return json.loads(COEFF_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def build_cards(fitted: dict | None) -> list[dict]:
    cards: list[dict] = []
    cards.append(
        {
            "id": "dict-overview",
            "category": "dictionary",
            "title": "How to read these cards",
            "subtitle": "Published-style baselines used in PropOracle tools",
            "bullets": [
                "Power: every leg must win; one multiplier for the full slip.",
                "Flex: partial wins pay smaller multipliers; top row is n/n correct.",
                "Goblin / Demon: easier or harder lines; estimate mode multiplies per-leg factors (see modifier cards).",
                "Site rules change — log real slips to refine coefficients (data/payout_formula_coefficients.json).",
            ],
        }
    )
    for n, mult in sorted(POWER_FIRST_STANDARD.items()):
        cards.append(
            {
                "id": f"power-{n}",
                "category": "power",
                "title": f"{n}-leg Power (all Standard)",
                "subtitle": f"First-place multiplier {mult}×",
                "bullets": [
                    f"All {n} legs correct → {mult}× stake (before Goblin/Demon adjustments).",
                    "Breakeven implied win rate ≈ 1 ÷ multiplier if legs were independent and fair (illustrative only).",
                ],
            }
        )
    for n in sorted(FLEX_FIRST_STANDARD.keys()):
        row = FLEX_FIRST_STANDARD[n]
        mg = FLEX_MIN_GUARANTEE_BASE.get(n)
        lines = [f"{k}/{n} correct → {v}×" for k, v in sorted(row.items(), key=lambda x: -x[0])]
        bl = lines + (
            [f"Flex min-guarantee baseline (fitter): {mg}× — used as denominator for some flex adjustments."]
            if mg is not None
            else []
        )
        cards.append(
            {
                "id": f"flex-{n}",
                "category": "flex",
                "title": f"{n}-leg Flex (Standard table)",
                "subtitle": "Partial payouts",
                "bullets": bl,
            }
        )

    def mod_card(key: str, title: str, d: dict[int, float]) -> dict:
        return {
            "id": key,
            "category": "modifier",
            "title": title,
            "subtitle": "Per leg — multiply together in estimate mode",
            "bullets": [f"Tier {dev}: ×{v}" for dev, v in sorted(d.items())],
        }

    cards.append(mod_card("mod-goblin-power", "Goblin — Power multiplier factor", GOBLIN_POWER))
    cards.append(mod_card("mod-goblin-flex", "Goblin — Flex multiplier factor", GOBLIN_FLEX))
    cards.append(mod_card("mod-demon-power", "Demon — Power multiplier factor", DEMON_POWER))
    cards.append(mod_card("mod-demon-flex", "Demon — Flex multiplier factor", DEMON_FLEX))

    if fitted:
        fitted_at = str(fitted.get("fitted_at", "") or "")
        if fitted.get("model_type") == "multiplicative_per_leg_power_law_demon":
            cards.append(
                {
                    "id": "fitted-coefficients",
                    "category": "fitted",
                    "title": "Empirical coefficients (manual observations)",
                    "subtitle": str(COEFF_PATH.name),
                    "bullets": [
                        f"fitted_at: {fitted_at}",
                        f"n_observations: {fitted.get('n_observations', '')}",
                        f"goblin_discount_per_unit: {fitted.get('goblin_discount_per_unit')} (R²={fitted.get('goblin_r_squared')})",
                        f"demon: coeff={fitted.get('demon_power_coeff')} exp={fitted.get('demon_power_exp')} (R²={fitted.get('demon_r_squared')})",
                        str(fitted.get("notes") or ""),
                    ],
                }
            )
        else:
            n = fitted.get("n_clean_samples")
            cards.append(
                {
                    "id": "fitted-coefficients",
                    "category": "fitted",
                    "title": "Last fitted coefficients file",
                    "subtitle": str(COEFF_PATH.name),
                    "bullets": [
                        f"fitted_at: {fitted_at}",
                        f"n_clean_samples: {n}" if n is not None else "n_clean_samples: (missing)",
                        "Re-run: python scripts/fit_payout_formula.py after collecting payout_samples.",
                    ],
                }
            )
    return cards


def build_payload() -> dict:
    fitted = load_fitted()
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reference": {
            "power_first_standard": _sk(POWER_FIRST_STANDARD),
            "flex_first_standard": _flex_json(),
            "flex_min_guarantee_base": _sk(FLEX_MIN_GUARANTEE_BASE),
            "modifiers": {
                "goblin_power": _sk(GOBLIN_POWER),
                "goblin_flex": _sk(GOBLIN_FLEX),
                "demon_power": _sk(DEMON_POWER),
                "demon_flex": _sk(DEMON_FLEX),
            },
        },
        "fitted_file": fitted,
        "cards": build_cards(fitted),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Write data/payout_rate_cards.json")
    ap.add_argument("--stdout", action="store_true", help="Print JSON instead of writing file")
    args = ap.parse_args()
    payload = build_payload()
    text = json.dumps(payload, indent=2) + "\n"
    if args.stdout:
        print(text, end="")
    else:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(text, encoding="utf-8")
        print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
