from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from src.cli import app

RUNNER = CliRunner()


def test_strategy_help_exposes_deep_research() -> None:
    result = RUNNER.invoke(app, ["strategy", "--help"])
    assert result.exit_code == 0
    assert "deep-research" in result.output


def test_deep_research_cli_validates_worker_budget_and_never_accepts_broker_environment(project_root: Path) -> None:
    result = RUNNER.invoke(
        app,
        [
            "strategy",
            "deep-research",
            "--project-root",
            str(project_root),
            "--strategy-id",
            "rsi_reversal",
            "--workers",
            "0",
            "--evaluation-budget",
            "4",
        ],
    )

    assert result.exit_code != 0
    assert "workers" in result.output.lower()
    help_result = RUNNER.invoke(app, ["strategy", "deep-research", "--help"])
    assert "account" not in help_result.output.lower()
    assert "live" not in help_result.output.lower()
    assert "api-key" not in help_result.output.lower()
