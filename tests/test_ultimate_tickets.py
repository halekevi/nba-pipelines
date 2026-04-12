"""Tests for ultimate ticket EV helpers and build_ultimate_tickets CLI smoke."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from combined_slate_tickets import compute_ticket_ev  # noqa: E402


def test_2leg_all_standard():
    legs = [
        {"pick_type": "standard", "line_distance": 0, "hit_prob": 0.65},
        {"pick_type": "standard", "line_distance": 0, "hit_prob": 0.65},
    ]
    result = compute_ticket_ev(legs, "power", 2)
    assert result["first_place_payout"] == 3.0
    assert abs(result["p_all_win"] - 0.4225) < 0.001
    assert result["recommendation"] in ("OK", "MARGINAL", "SKIP", "STRONG")


def test_2leg_1goblin():
    legs = [
        {"pick_type": "goblin", "line_distance": 2.0, "hit_prob": 0.72},
        {"pick_type": "standard", "line_distance": 0, "hit_prob": 0.65},
    ]
    result = compute_ticket_ev(legs, "power", 2)
    assert result["first_place_payout"] < 3.0
    assert result["min_guarantee_adjustment"] < 1.0


def test_smoke_dry_run():
    script = ROOT / "scripts" / "build_ultimate_tickets.py"
    result = subprocess.run(
        [sys.executable, str(script), "--dry-run", "--max-candidates", "5", "--max-combos", "2000"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 and "need at least 2 legs" in out:
        pytest.skip("step8 outputs missing — run pipeline locally to exercise dry-run")
    assert result.returncode == 0, out
