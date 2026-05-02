"""Regression tests: MLB step1 snapshot fallback selection order."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_mlb_step1() -> object:
    path = REPO_ROOT / "Sports" / "MLB" / "scripts" / "step1_fetch_prizepicks_mlb.py"
    spec = importlib.util.spec_from_file_location("mlb_step1_test_mod", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mlb_mod():
    return _load_mlb_step1()


def test_resolve_fallback_prefers_same_game_date(tmp_path, mlb_mod, monkeypatch):
    snap = tmp_path / "step1_snapshots"
    snap.mkdir(parents=True)
    monkeypatch.setattr(mlb_mod, "SNAPSHOT_DIR", snap)

    row = {
        "projection_id": "p1",
        "player": "Test Player",
        "team": "NYY",
        "prop_type": "Hits",
        "line": 1.5,
        "standard_line": 1.5,
        "pick_type": "Standard",
        "start_time": "2026-04-20T23:00:00Z",
        "pp_game_id": "g1",
    }
    pd.DataFrame([row]).to_csv(snap / "step1_mlb_props_2026-04-19.csv", index=False)
    pd.DataFrame([row]).to_csv(snap / "step1_mlb_props_2026-04-20.csv", index=False)

    out_path = tmp_path / "out.csv"
    df, src, tag = mlb_mod._resolve_fallback_frame(out_path, "2026-04-20", "America/New_York")
    assert tag == "2026-04-20"
    assert src.name == "step1_mlb_props_2026-04-20.csv"
    assert len(df) == 1


def test_resolve_fallback_uses_latest_dated_file_when_no_target_date_match(tmp_path, mlb_mod, monkeypatch):
    snap = tmp_path / "step1_snapshots"
    snap.mkdir(parents=True)
    monkeypatch.setattr(mlb_mod, "SNAPSHOT_DIR", snap)

    old_row = {
        "projection_id": "old",
        "player": "Old",
        "team": "BOS",
        "prop_type": "Hits",
        "line": 1.0,
        "standard_line": 1.0,
        "pick_type": "Standard",
        "start_time": "2026-04-18T23:00:00Z",
        "pp_game_id": "g2",
    }
    pd.DataFrame([old_row]).to_csv(snap / "step1_mlb_props_2026-04-18.csv", index=False)

    out_path = tmp_path / "missing_out.csv"
    df, src, tag = mlb_mod._resolve_fallback_frame(out_path, "2099-01-01", "America/New_York")
    assert len(df) == 1
    assert "2026-04-18" in src.name
    assert tag == "2026-04-18"
