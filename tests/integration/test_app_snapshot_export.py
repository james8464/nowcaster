from __future__ import annotations

import json

from typer.testing import CliRunner

from src.app_snapshot.builder import build_app_snapshot
from src.app_snapshot.models import AppSnapshot
from src.app_snapshot.writer import write_snapshot_atomic
from src.cli import app


def test_demo_database_exports_a_populated_native_snapshot(tmp_path, demo_database):
    settings, database = demo_database

    snapshot = build_app_snapshot(database, settings)
    path = write_snapshot_atomic(snapshot, tmp_path / "nowcaster-snapshot.json")
    decoded = AppSnapshot.model_validate_json(path.read_text())

    assert decoded.metadata.data_mode == "demo_real_snapshot"
    assert decoded.overview.company_count == 3
    assert decoded.instruments
    assert decoded.earnings
    assert decoded.signals
    assert decoded.model_diagnostics
    assert decoded.backtests
    assert "probability of profit" not in path.read_text().lower()


def test_export_app_snapshot_cli_emits_structured_completion(tmp_path, demo_database):
    settings, _ = demo_database
    output = tmp_path / "native.json"

    result = CliRunner().invoke(
        app,
        [
            "export-app-snapshot",
            "--project-root",
            str(settings.project_root),
            "--database-url",
            settings.database_url,
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["schema_version"] == 1
    assert AppSnapshot.model_validate_json(output.read_text()).overview.company_count == 3
