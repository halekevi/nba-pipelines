"""Allow: python -m proporacle.cli (forwards to Typer app)."""

from proporacle.cli.main import app

if __name__ == "__main__":
    app()
