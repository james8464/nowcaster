from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from src.cli import app
from src.config.settings import Settings
from src.database.engine import Database
from tests.integration.test_contextual_service import PROJECT_ROOT, seed_contextual_market

RUNNER = CliRunner()


def test_screen_universe_cli_returns_json_progress(tmp_path: Path) -> None:
    database_url = f"duckdb:///{tmp_path / 'contextual-cli.duckdb'}"
    database = Database.from_url(database_url)
    database.initialize()
    as_of = seed_contextual_market(database, count=120)
    assert Settings.load(PROJECT_ROOT, mode="test").asset_selection is not None

    result = RUNNER.invoke(
        app,
        [
            "strategy",
            "screen-universe",
            "--project-root",
            str(PROJECT_ROOT),
            "--database-url",
            database_url,
            "--symbols",
            "BTCUSDT",
            "--provider",
            "binance",
            "--feed",
            "spot",
            "--interval",
            "5m",
            "--mode",
            "paper",
            "--as-of",
            as_of.isoformat(),
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"stage":"eligibility"' in result.stdout
    assert '"stage":"regimes"' in result.stdout
    assert '"event":"complete"' in result.stdout
