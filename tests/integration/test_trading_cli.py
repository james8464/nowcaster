from __future__ import annotations

from typer.testing import CliRunner

from src.cli import app

runner = CliRunner()


def test_paper_refuses_missing_credentials_and_exposes_no_endpoint_override(tmp_path) -> None:
    result = runner.invoke(app, ["trading", "paper", "--project-root", str(tmp_path)])
    assert result.exit_code != 0
    assert "credentials" in result.output.lower()
    help_result = runner.invoke(app, ["trading", "paper", "--help"])
    assert help_result.exit_code == 0
    assert "api.alpaca.markets" not in help_result.output and "--live" not in help_result.output


def test_paper_never_echoes_secret_on_construction_failure(monkeypatch, tmp_path) -> None:
    secret = "do-not-print-this-paper-secret"
    monkeypatch.setenv("APCA_API_KEY_ID", "paper-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", secret)
    result = runner.invoke(app, ["trading", "paper", "--project-root", str(tmp_path)])
    assert result.exit_code != 0
    assert secret not in result.output


def test_trading_status_is_bounded_json_for_empty_database(project_root, tmp_path) -> None:
    database_url = f"duckdb:///{tmp_path / 'status.duckdb'}"
    result = runner.invoke(
        app,
        ["trading", "status", "--project-root", str(project_root), "--database-url", database_url],
    )
    assert result.exit_code == 0
    assert '"environment": "none"' in result.output
    assert "secret" not in result.output.lower()
