from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.config.settings import Settings
from src.database.engine import Database
from src.strategies import pipeline as strategy_pipeline
from src.strategies.pipeline import (
    BarProviderName,
    DeepResearchOptions,
    IngestOptions,
    StrategyScope,
    create_strategy_pipeline,
)
from src.strategies.types import BarInterval, StrategyMode


def _configure(project_root: Path) -> None:
    (project_root / "config" / "strategies.yaml").write_text(
        """
strategy_weight_cap: 0.5
family_weight_caps: {mean_reversion: 0.5}
strategies:
  - strategy_id: rsi_reversal
    family: mean_reversion
    version: 1.0.0
    intervals: [5m]
    warmup_bars: 3
    parameters: {period: 3, oversold: 30, overbought: 70}
    enabled: true
""".lstrip(),
        encoding="utf-8",
    )


BAR_COUNT = 1_200


def _bars(path: Path, count: int = BAR_COUNT) -> None:
    start = datetime(2026, 8, 20, tzinfo=UTC)
    rows = ["timestamp,open,high,low,close,volume,vwap,trade_count,finalized,available_at,revision"]
    previous = 100.0
    for index in range(count):
        opened = start + timedelta(minutes=5 * index)
        closed = opened + timedelta(minutes=5)
        close = previous + (0.8 if index % 4 else -0.6)
        rows.append(
            f"{opened.isoformat().replace('+00:00', 'Z')},{previous},{max(previous, close) + 0.1},"
            f"{min(previous, close) - 0.1},{close},{1000 + index},{close},10,true,"
            f"{closed.isoformat().replace('+00:00', 'Z')},1"
        )
        previous = close
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _scope() -> StrategyScope:
    return StrategyScope(
        strategy_ids=("rsi_reversal",),
        provider=BarProviderName.CSV,
        feed="local",
        symbol="BTCUSDT",
        interval=BarInterval.FIVE_MINUTES,
        mode=StrategyMode.WALK_FORWARD_LEARNING,
    )


def _pipeline(project_root: Path, tmp_path: Path):
    _configure(project_root)
    csv_path = tmp_path / "bars.csv"
    _bars(csv_path)
    database = Database.from_url(f"duckdb:///{tmp_path / 'deep.duckdb'}")
    loaded = Settings.load(project_root, mode="test")
    settings = loaded.model_copy(
        update={
            "database_url": str(database.engine.url),
            "deep_research": loaded.deep_research.model_copy(update={"default_cycle_budget": 4}),
        }
    )
    return create_strategy_pipeline(settings, database, csv_path=csv_path), database


def test_deep_research_refuses_missing_authenticated_coverage(project_root, tmp_path) -> None:
    pipeline, database = _pipeline(project_root, tmp_path)
    outcome = pipeline.deep_research(
        DeepResearchOptions(
            scope=_scope(),
            workers=2,
            evaluation_budget=4,
            seed=7,
            control_directory=tmp_path / "control",
            control_nonce="n" * 32,
        )
    )

    assert outcome.status == "unavailable"
    assert "coverage" in outcome.message
    assert database.scalar("select count(*) from deep_research_runs") == 0


def test_deep_research_uses_complete_provider_snapshot_and_persists_every_trial(project_root, tmp_path) -> None:
    pipeline, database = _pipeline(project_root, tmp_path)
    start = datetime(2026, 8, 20, tzinfo=UTC)
    ingested = pipeline.ingest(IngestOptions(scope=_scope(), start=start, end=start + timedelta(minutes=5 * BAR_COUNT)))
    events = []

    outcome = pipeline.deep_research(
        DeepResearchOptions(
            scope=_scope(),
            workers=2,
            evaluation_budget=4,
            seed=7,
            control_directory=tmp_path / "control",
            control_nonce="n" * 32,
        ),
        events.append,
    )

    assert ingested.status == "completed"
    assert outcome.status == "completed"
    assert database.scalar("select count(*) from deep_research_trials") == 4
    assert database.scalar("select count(*) from deep_research_trials where status = 'succeeded'") >= 1
    assert database.scalar("select count(*) from deep_research_fold_metrics") >= 1
    run = database.frame("select dataset_hash, provider, feed, symbol, interval, state from deep_research_runs").iloc[0]
    assert run.to_dict() == {
        "dataset_hash": outcome.dataset_hash,
        "provider": "csv",
        "feed": "local",
        "symbol": "BTCUSDT",
        "interval": "5m",
        "state": "completed",
    }
    assert any(event.stage == "deep_research" for event in events)
    assert database.scalar("select count(*) from broker_order_intents") == 0


def test_continuous_deep_research_runs_checkpointed_generations_until_time_budget(
    project_root, tmp_path, monkeypatch
) -> None:
    pipeline, database = _pipeline(project_root, tmp_path)
    start = datetime(2026, 8, 20, tzinfo=UTC)
    pipeline.ingest(IngestOptions(scope=_scope(), start=start, end=start + timedelta(minutes=5 * BAR_COUNT)))
    monotonic_ticks = iter((0.0, 5.0, 7.0))
    monkeypatch.setattr(
        strategy_pipeline,
        "time",
        SimpleNamespace(monotonic=lambda: next(monotonic_ticks)),
    )

    outcome = pipeline.deep_research(
        DeepResearchOptions(
            scope=_scope(),
            workers=4,
            evaluation_budget=None,
            continuous=True,
            time_budget_seconds=6,
            seed=11,
            control_directory=tmp_path / "continuous-control",
            control_nonce="c" * 32,
        )
    )

    assert outcome.status == "completed"
    assert outcome.evaluated_candidates >= 4
    assert database.scalar("select count(*) from deep_research_trials") == outcome.evaluated_candidates
    assert database.scalar("select max(generation) from deep_research_trials") >= 2
    assert database.scalar("select count(*) from deep_research_checkpoints") >= 2
    assert database.scalar("select state from deep_research_runs") == "completed"
    assert database.scalar("select count(*) from broker_order_intents") == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"workers": 0},
        {"evaluation_budget": 0},
        {"continuous": True, "evaluation_budget": 10},
        {"continuous": False, "evaluation_budget": None},
        {"time_budget_seconds": 0},
        {"control_nonce": "short"},
    ],
)
def test_deep_research_options_reject_unsafe_or_ambiguous_resource_controls(changes, tmp_path) -> None:
    values = {
        "scope": _scope(),
        "workers": 2,
        "evaluation_budget": 10,
        "continuous": False,
        "seed": 7,
        "control_directory": tmp_path,
        "control_nonce": "n" * 32,
    }
    values.update(changes)
    with pytest.raises(ValidationError):
        DeepResearchOptions(**values)
