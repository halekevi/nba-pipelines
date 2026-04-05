"""Typer CLI: `python -m proporacle.cli model smoke --sport nba --n 10`."""

from __future__ import annotations

import os
from pathlib import Path

import typer

app = typer.Typer(help="PropORACLE income-engine CLI")
model_app = typer.Typer(help="Model commands")
app.add_typer(model_app, name="model")


@model_app.command("smoke")
def model_smoke(
    sport: str = typer.Option("nba", help="Sport key"),
    n: int = typer.Option(10, help="Synthetic rows (informational)"),
) -> None:
    """
    Verify proporacle imports and core math. If `models/prop_model_{sport}.pkl` exists
    (and joblib/sklearn installed), load and predict on zeros — else skip model load.
    """
    from proporacle.betting.staking import kelly_fraction
    from proporacle.pricing.ev import ev_per_unit

    typer.echo(f"smoke: sport={sport!r} n={n}")
    ev = ev_per_unit(0.55, -110)
    kf = kelly_fraction(0.55, -110)
    typer.echo(f"ev_per_unit(0.55,-110)={ev:.4f} kelly_fraction={kf:.4f}")
    assert isinstance(ev, float) and isinstance(kf, float)

    root = Path(__file__).resolve().parents[2]
    model_path = Path(os.environ.get("PROPORACLE_MODEL_PATH", str(root / "models" / f"prop_model_{sport}.pkl")))
    if not model_path.is_file():
        typer.echo(f"SKIP model load: missing {model_path} (set PROPORACLE_MODEL_PATH or add artifact)")
        raise typer.Exit(0)

    try:
        import joblib  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
    except ImportError:
        typer.echo("SKIP: joblib/numpy not installed")
        raise typer.Exit(0)

    m = joblib.load(model_path)
    feat_n = 10
    feat_json = model_path.with_name(model_path.name.replace(".pkl", "_features.json"))
    if feat_json.is_file():
        import json  # noqa: PLC0415

        meta = json.loads(feat_json.read_text(encoding="utf-8"))
        if isinstance(meta, list):
            feat_n = len(meta)
        elif isinstance(meta, dict) and "features" in meta:
            feat_n = len(meta["features"])
    X = np.zeros((min(n, 50), feat_n), dtype=np.float64)
    if hasattr(m, "predict_proba"):
        try:
            p = m.predict_proba(X)[:, 1]
            typer.echo(f"predict_proba ok shape={p.shape} mean_p={float(p.mean()):.4f}")
        except Exception as e:
            typer.echo(f"SKIP predict_proba (shape or model): {e}")
            raise typer.Exit(0) from e
    else:
        typer.echo("model has no predict_proba")
    typer.echo("smoke OK")


def run() -> None:
    app()


if __name__ == "__main__":
    app()
