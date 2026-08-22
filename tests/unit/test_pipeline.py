from __future__ import annotations

from src.config.settings import Settings
from src.pipeline import Pipeline


def test_pipeline_runs_stages_in_declared_order_and_restarts_without_repeating(project_root, tmp_path):
    settings = Settings.load(project_root, mode="test").model_copy(
        update={"database_url": f"duckdb:///{tmp_path / 'pipeline.duckdb'}"}
    )
    calls: list[str] = []
    handlers = {name: lambda name=name: calls.append(name) or 1 for name in ("first", "second")}
    pipeline = Pipeline(settings, stage_handlers=handlers, stage_order=("first", "second"))

    first = pipeline.run(("second", "first"), mode="test")
    second = pipeline.run(("first", "second"), mode="test")

    assert first.completed == ("first", "second")
    assert calls == ["first", "second"]
    assert second.skipped == ("first", "second")


def test_pipeline_stops_after_failure_and_records_failed_stage(project_root, tmp_path):
    settings = Settings.load(project_root, mode="test").model_copy(
        update={"database_url": f"duckdb:///{tmp_path / 'failure.duckdb'}"}
    )
    calls: list[str] = []

    def fail():
        raise RuntimeError("broken input")

    pipeline = Pipeline(
        settings,
        stage_handlers={
            "first": lambda: calls.append("first") or 1,
            "broken": fail,
            "last": lambda: calls.append("last") or 1,
        },
        stage_order=("first", "broken", "last"),
    )

    summary = pipeline.run(("first", "broken", "last"), mode="test")

    assert summary.failed_stage == "broken"
    assert calls == ["first"]
    assert "broken input" in summary.error
