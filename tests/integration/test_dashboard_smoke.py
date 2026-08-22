from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.database.engine import Database


def test_dashboard_entrypoint_renders_useful_empty_state_without_exceptions(tmp_path, monkeypatch):
    database_url = f"duckdb:///{tmp_path / 'dashboard-smoke.duckdb'}"
    database = Database.from_url(database_url)
    database.initialize()
    monkeypatch.setenv("NOWCASTER_DATABASE_URL", database_url)
    app_path = Path(__file__).resolve().parents[2] / "dashboard" / "app.py"

    app = AppTest.from_file(str(app_path)).run(timeout=30)

    assert not app.exception
    assert any("Alternative-Data Earnings Nowcaster" in title.value for title in app.title)
    assert any(metric.label == "Companies" for metric in app.metric)
    assert app.info or app.warning
