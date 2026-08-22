from pathlib import Path

from typer.testing import CliRunner

from src.cli import app


def test_init_db_is_an_explicit_subcommand(project_root, tmp_path):
    database_url = f"duckdb:///{tmp_path / 'cli.duckdb'}"

    result = CliRunner().invoke(
        app,
        ["init-db", "--project-root", str(project_root), "--database-url", database_url],
    )

    assert result.exit_code == 0, result.output
    assert "Initialized" in result.output


def test_demo_command_runs_without_api_keys(tmp_path):
    root = Path(__file__).resolve().parents[2]
    database_url = f"duckdb:///{tmp_path / 'cli-demo.duckdb'}"

    result = CliRunner().invoke(
        app,
        ["demo", "--project-root", str(root), "--database-url", database_url],
    )

    assert result.exit_code == 0, result.output
    assert "Demo complete" in result.output
