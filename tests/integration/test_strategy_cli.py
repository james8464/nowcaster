from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd
import pytest
from sqlalchemy import event, update
from sqlalchemy.exc import IntegrityError
from typer.testing import CliRunner

from src.app_snapshot.models import AppSnapshot
from src.backtest.execution import ExecutionAssumptions
from src.cli import app
from src.config.settings import Settings
from src.database.engine import Database
from src.database.schema import dataset_coverage_requests
from src.ingestion.bars import BarRequest, MarketBar
from src.learning.grammar import RuleNode
from src.learning.promotion import ForwardEvidence
from src.learning.search import RuleCandidate
from src.pipeline import PipelineSummary
from src.strategies import pipeline as strategy_pipeline
from src.strategies.pipeline import (
    BarProviderName,
    EvaluationOptions,
    ExportOptions,
    IngestOptions,
    LearningOptions,
    StageOutcome,
    StrategyPipeline,
    StrategyScope,
    create_strategy_pipeline,
)
from src.strategies.registry import StrategyRegistry
from src.strategies.types import BarInterval, StrategyMode, canonical_hash
from src.strategies.validation import PromotionDecision

RUNNER = CliRunner()


def _configure_strategy(project_root: Path, *, version: str = "1.0.0") -> None:
    (project_root / "config" / "strategies.yaml").write_text(
        """
strategy_weight_cap: 0.5
family_weight_caps:
  mean_reversion: 0.5
strategies:
  - strategy_id: rsi_reversal
    family: mean_reversion
    version: VERSION
    intervals: [5m]
    warmup_bars: 3
    parameters: {period: 2, oversold: 30, overbought: 70}
    enabled: true
""".replace("VERSION", version).lstrip(),
        encoding="utf-8",
    )


def _configure_plural_strategies(
    project_root: Path,
    *,
    strategy_weight_cap: float = 0.5,
    family_weight_cap: float = 1.0,
) -> None:
    (project_root / "config" / "strategies.yaml").write_text(
        f"""
strategy_weight_cap: {strategy_weight_cap}
family_weight_caps:
  mean_reversion: {family_weight_cap}
strategies:
  - strategy_id: rsi_reversal
    family: mean_reversion
    version: 1.0.0
    intervals: [5m]
    warmup_bars: 3
    parameters: {{period: 2, oversold: 30, overbought: 70}}
    enabled: true
  - strategy_id: extreme_return_reversal
    family: mean_reversion
    version: 1.0.0
    intervals: [5m]
    warmup_bars: 3
    parameters: {{lookback: 2, entry_zscore: 0.5}}
    enabled: true
""".lstrip(),
        encoding="utf-8",
    )


def _write_bars(path: Path, count: int) -> None:
    start = datetime(2026, 8, 20, tzinfo=UTC)
    rows = ["timestamp,open,high,low,close,volume,vwap,trade_count,finalized,available_at,revision"]
    previous = 100.0
    for index in range(count):
        opened_at = start + timedelta(minutes=5 * index)
        closed_at = opened_at + timedelta(minutes=5)
        close = previous + (1.0 if index % 3 else -0.5)
        rows.append(
            f"{opened_at.isoformat().replace('+00:00', 'Z')},{previous},{max(previous, close) + 0.2},"
            f"{min(previous, close) - 0.2},{close},{1000 + index},{close},10,true,"
            f"{closed_at.isoformat().replace('+00:00', 'Z')},1"
        )
        previous = close
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _market_bar(
    opened_at: datetime,
    *,
    provider: str,
    feed: str,
    symbol: str,
    close: float,
    retrieved_at: datetime | None = None,
    payload_suffix: str = "initial",
) -> MarketBar:
    closed_at = opened_at + timedelta(minutes=5)
    return MarketBar(
        provider=provider,
        feed=feed,
        symbol=symbol,
        interval=BarInterval.FIVE_MINUTES,
        open_timestamp=opened_at,
        close_timestamp=closed_at,
        available_at=closed_at,
        retrieved_at=retrieved_at,
        open=close - 0.25,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        volume=1_000,
        vwap=close,
        trade_count=10,
        payload_hash=canonical_hash([provider, feed, symbol, opened_at, close, payload_suffix]),
    )


def _base_arguments(project_root: Path, database_url: str, bars: Path) -> list[str]:
    return [
        "--project-root",
        str(project_root),
        "--database-url",
        database_url,
        "--strategy-id",
        "rsi_reversal",
        "--provider",
        "csv",
        "--feed",
        "local",
        "--symbol",
        "BTCUSDT",
        "--interval",
        "5m",
        "--csv-path",
        str(bars),
    ]


def _events(output: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def _settings(project_root: Path, database_url: str) -> Settings:
    return Settings.load(project_root, mode="test").model_copy(update={"database_url": database_url})


def _csv_pipeline(project_root: Path, database_url: str, bars: Path):
    database = Database.from_url(database_url)
    return create_strategy_pipeline(_settings(project_root, database_url), database, csv_path=bars), database


def _scope(*strategy_ids: str) -> StrategyScope:
    return StrategyScope(
        strategy_id=strategy_ids[0] if len(strategy_ids) == 1 else strategy_ids,
        provider=BarProviderName.CSV,
        feed="local",
        symbol="BTCUSDT",
        interval=BarInterval.FIVE_MINUTES,
        mode=StrategyMode.PAPER,
    )


def _ingest_options(scope: StrategyScope, count: int = 80, *, force: bool = False) -> IngestOptions:
    start = datetime(2026, 8, 20, tzinfo=UTC)
    return IngestOptions(
        scope=scope,
        start=start,
        end=start + timedelta(minutes=5 * count),
        force=force,
    )


def _captured_learning_experiment(pipeline: StrategyPipeline, scope: StrategyScope):
    registered = pipeline._registered(scope)
    query, manifest, _coverage, unavailable = pipeline._authenticated_coverage(scope)
    assert query is not None and manifest is not None, unavailable
    as_of = pipeline._query_as_of(query)
    bars = pipeline.bars.causal_bars_as_of(query, as_of).copy(deep=True)
    experiment, _development = pipeline._learning_experiment(
        LearningOptions(scope=scope, evaluation_budget=1),
        registered,
        manifest,
        bars,
        pipeline._raw_final_boundary(bars),
    )
    return experiment


def test_strategy_cli_is_nested_without_removing_legacy_earnings_commands() -> None:
    for command in ("demo", "fetch-fundamentals", "train", "backtest", "report", "export-app-snapshot"):
        result = RUNNER.invoke(app, [command, "--help"])
        assert result.exit_code == 0, result.output
    for command in ("ingest", "evaluate", "learn", "export"):
        result = RUNNER.invoke(app, ["strategy", command, "--help"])
        assert result.exit_code == 0, result.output


def test_injected_lot_size_is_effective_and_matches_persisted_policy_hash(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    bars = tmp_path / "effective-lot-bars.csv"
    learning_bar_count = 800
    _write_bars(bars, learning_bar_count)
    database_url = f"duckdb:///{tmp_path / 'effective-lot.duckdb'}"
    configured, database = _csv_pipeline(project_root, database_url, bars)
    assumptions = ExecutionAssumptions(lot_size=1_000_000)
    pipeline = StrategyPipeline(
        database,
        configured.registry,
        configured.providers,
        provider_unavailable=configured.provider_unavailable,
        validation_config=configured.validation_config,
        ensemble_config=configured.ensemble_config,
        execution_assumptions=assumptions,
    ).bind_settings(_settings(project_root, database_url))
    scope = _scope("rsi_reversal")

    assert pipeline.ingest(_ingest_options(scope, count=learning_bar_count)).status == "completed"
    assert pipeline.evaluate(EvaluationOptions(scope=scope)).status == "completed"
    metrics = database.frame("select metrics from strategy_runs where status = 'evaluated'").iloc[0]["metrics"]

    assert metrics["execution_policy"]["lot_size"] == 1_000_000
    assert metrics["execution_policy_hash"] == canonical_hash(metrics["execution_policy"])
    assert database.scalar("select count(*) from strategy_executions") == 0
    assert _captured_learning_experiment(pipeline, scope).execution_assumptions == assumptions


def test_default_pipeline_uses_and_records_default_lot_size_without_override(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    bars = tmp_path / "default-lot-bars.csv"
    learning_bar_count = 800
    _write_bars(bars, learning_bar_count)
    database_url = f"duckdb:///{tmp_path / 'default-lot.duckdb'}"
    pipeline, _database = _csv_pipeline(project_root, database_url, bars)
    scope = _scope("rsi_reversal")

    assert pipeline.ingest(_ingest_options(scope, count=learning_bar_count)).status == "completed"
    experiment = _captured_learning_experiment(pipeline, scope)

    assert pipeline.execution_assumptions.lot_size == 1.0
    assert experiment.execution_assumptions.lot_size == 1.0


def test_strategy_cli_rejects_unsafe_registry_mode_and_provider_strings(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    bars = tmp_path / "bars.csv"
    _write_bars(bars, 4)
    database_url = f"duckdb:///{tmp_path / 'unsafe.duckdb'}"
    common = _base_arguments(project_root, database_url, bars)

    unknown_strategy = RUNNER.invoke(
        app,
        ["strategy", "evaluate", *common, "--strategy-id", "os.system"],
    )
    unknown_mode = RUNNER.invoke(
        app,
        ["strategy", "evaluate", *common, "--mode", "live"],
    )
    unknown_provider = RUNNER.invoke(
        app,
        ["strategy", "evaluate", *common, "--provider", "../../plugin.py"],
    )

    assert unknown_strategy.exit_code != 0
    assert _events(unknown_strategy.stdout)[-1]["event"] == "error"
    assert unknown_mode.exit_code != 0
    assert unknown_provider.exit_code != 0
    assert _events(unknown_mode.stdout)[-1]["event"] == "error"
    assert _events(unknown_provider.stdout)[-1]["event"] == "error"


def test_native_demo_accepts_the_explicit_demo_mode_without_changing_keyless_behavior(
    project_root, tmp_path, monkeypatch
) -> None:
    database_url = f"duckdb:///{tmp_path / 'native-demo.duckdb'}"
    monkeypatch.setattr("src.cli.run_demo", lambda settings, force=False: PipelineSummary(completed=("demo",)))
    monkeypatch.setattr("src.cli.generate_research_report", lambda database, path: path)

    compatible = RUNNER.invoke(
        app,
        [
            "demo",
            "--project-root",
            str(project_root),
            "--database-url",
            database_url,
            "--mode",
            "demo",
        ],
    )
    unsafe = RUNNER.invoke(
        app,
        [
            "demo",
            "--project-root",
            str(project_root),
            "--database-url",
            database_url,
            "--mode",
            "paper",
        ],
    )

    assert compatible.exit_code == 0
    assert "Demo complete" in compatible.output
    assert unsafe.exit_code != 0


def test_strategy_ingest_fetches_only_missing_coverage_and_appends(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    bars = tmp_path / "bars.csv"
    _write_bars(bars, 4)
    database_url = f"duckdb:///{tmp_path / 'incremental.duckdb'}"
    common = _base_arguments(project_root, database_url, bars)

    first = RUNNER.invoke(
        app,
        [
            "strategy",
            "ingest",
            *common,
            "--start",
            "2026-08-20T00:00:00Z",
            "--end",
            "2026-08-20T00:15:00Z",
        ],
    )
    repeated = RUNNER.invoke(
        app,
        [
            "strategy",
            "ingest",
            *common,
            "--start",
            "2026-08-20T00:00:00Z",
            "--end",
            "2026-08-20T00:15:00Z",
        ],
    )
    extended = RUNNER.invoke(
        app,
        [
            "strategy",
            "ingest",
            *common,
            "--start",
            "2026-08-20T00:00:00Z",
            "--end",
            "2026-08-20T00:20:00Z",
        ],
    )

    assert first.exit_code == repeated.exit_code == extended.exit_code == 0
    assert _events(first.stdout)[-1]["event"] == "complete"
    assert _events(repeated.stdout)[-1]["message"] == "coverage already available"
    assert Database.from_url(database_url).scalar("select count(*) from market_bars") == 4


def test_evaluation_uses_all_contiguous_local_history_across_completed_requests(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    bars = tmp_path / "contiguous-bars.csv"
    _write_bars(bars, 80)
    database_url = f"duckdb:///{tmp_path / 'contiguous.duckdb'}"
    common = _base_arguments(project_root, database_url, bars)

    for start, end in (
        ("2026-08-20T00:00:00Z", "2026-08-20T03:20:00Z"),
        ("2026-08-20T03:20:00Z", "2026-08-20T06:40:00Z"),
    ):
        ingested = RUNNER.invoke(
            app,
            ["strategy", "ingest", *common, "--start", start, "--end", end],
        )
        assert ingested.exit_code == 0, ingested.output

    evaluated = RUNNER.invoke(app, ["strategy", "evaluate", *common, "--mode", "paper"])
    database = Database.from_url(database_url)

    assert evaluated.exit_code == 0, evaluated.output
    assert database.scalar("select count(*) from strategy_signals") == 80


def test_contiguous_coverage_union_blocks_a_stale_contributing_range_until_refreshed(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    bars_path = tmp_path / "coverage-union-bars.csv"
    _write_bars(bars_path, 80)
    database_url = f"duckdb:///{tmp_path / 'coverage-union.duckdb'}"
    pipeline, _database = _csv_pipeline(project_root, database_url, bars_path)
    scope = _scope("rsi_reversal")
    first = _ingest_options(scope, count=40)
    second = first.model_copy(
        update={
            "start": first.end,
            "end": first.end + timedelta(minutes=5 * 40),
        }
    )
    assert pipeline.ingest(first).status == "completed"
    assert pipeline.ingest(second).status == "completed"
    assert pipeline.evaluate(EvaluationOptions(scope=scope)).status == "completed"

    correction_receipt = datetime(2026, 8, 20, 6, 45, tzinfo=UTC)
    assert (
        pipeline.bars.append(
            [
                _market_bar(
                    datetime(2026, 8, 20, 1, 40, tzinfo=UTC),
                    provider="csv",
                    feed="local",
                    symbol="BTCUSDT",
                    close=1_000,
                    retrieved_at=correction_receipt,
                    payload_suffix="stale-first-segment",
                )
            ]
        )
        == 1
    )

    blocked_evaluation = pipeline.evaluate(EvaluationOptions(scope=scope))
    blocked_learning = pipeline.learn(LearningOptions(scope=scope, evaluation_budget=1))
    refreshed = pipeline.ingest(first)
    recovered = pipeline.evaluate(EvaluationOptions(scope=scope))

    assert blocked_evaluation.status == blocked_learning.status == "unavailable"
    assert "coverage" in blocked_evaluation.message
    assert refreshed.status == "reused"
    assert recovered.status == "completed"


def test_terminal_evaluation_persists_and_exports_exact_aggregate_coverage_manifest(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    bars_path = tmp_path / "aggregate-manifest-bars.csv"
    _write_bars(bars_path, 80)
    database_url = f"duckdb:///{tmp_path / 'aggregate-manifest.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, bars_path)
    scope = _scope("rsi_reversal")
    first = _ingest_options(scope, count=40)
    second = first.model_copy(
        update={
            "start": first.end,
            "end": first.end + timedelta(minutes=5 * 40),
        }
    )
    assert pipeline.ingest(first).status == "completed"
    assert pipeline.ingest(second).status == "completed"

    outcome = pipeline.evaluate(EvaluationOptions(scope=scope))
    run = database.frame(
        "select dataset_hash, metrics from strategy_runs where status = 'evaluated' order by run_timestamp desc"
    ).iloc[0]
    requests = database.frame(
        "select coverage_request_id from dataset_coverage_requests order by requested_start, requested_end, "
        "requested_at, coverage_request_id"
    )
    evidence = run.metrics["coverage_manifest"]
    snapshot_path = tmp_path / "aggregate-manifest-snapshot.json"
    pipeline.export(
        ExportOptions(
            snapshot_path=snapshot_path,
            report_path=tmp_path / "aggregate-manifest-report.md",
        )
    )
    snapshot = AppSnapshot.model_validate_json(snapshot_path.read_text(encoding="utf-8"))

    assert outcome.status == "completed"
    assert evidence["dataset_hash"] == run.dataset_hash == outcome.dataset_hash
    assert evidence["requested_start"] == "2026-08-20T00:00:00Z"
    assert evidence["requested_end"] == "2026-08-20T06:40:00Z"
    assert evidence["row_count"] == 80
    assert evidence["gaps"] == []
    assert [item["coverage_request_id"] for item in evidence["contributing_requests"]] == requests[
        "coverage_request_id"
    ].tolist()
    assert len(snapshot.dataset_coverage) == 1
    exported = snapshot.dataset_coverage[0]
    assert exported.dataset_hash == outcome.dataset_hash
    assert exported.requested_start.isoformat() == "2026-08-20T00:00:00+00:00"
    assert exported.requested_end.isoformat() == "2026-08-20T06:40:00+00:00"
    assert exported.coverage_start.isoformat() == "2026-08-20T00:00:00+00:00"
    assert exported.coverage_end.isoformat() == "2026-08-20T06:40:00+00:00"
    assert exported.row_count == 80
    assert exported.complete is True


def test_revision_between_authentication_and_engine_never_mixes_dataset_hash_and_signal_ledger(
    project_root, tmp_path
) -> None:
    _configure_strategy(project_root)
    bars_path = tmp_path / "sealed-evaluation-bars.csv"
    _write_bars(bars_path, 80)
    database_url = f"duckdb:///{tmp_path / 'sealed-evaluation.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, bars_path)
    scope = _scope("rsi_reversal")
    options = _ingest_options(scope)
    configured = pipeline.registry.resolve("rsi_reversal")
    engine_ledger_sizes: list[int] = []

    def capture_engine_ledger(spec, bars, context):
        engine_ledger_sizes.append(len(bars))
        return configured.generator(spec, bars, context)

    registry = StrategyRegistry()
    registry.register(configured.spec, capture_engine_ledger, configured.metadata)
    pipeline.registry = registry
    initial = pipeline.ingest(options)
    assert initial.status == "completed"
    old_hash = initial.dataset_hash
    inserted = False
    original = pipeline._evaluate_engines

    def append_revision_after_authentication(*args, **kwargs):
        nonlocal inserted
        if not inserted:
            inserted = True
            assert (
                pipeline.bars.append(
                    [
                        _market_bar(
                            datetime(2026, 8, 20, 1, 40, tzinfo=UTC),
                            provider="csv",
                            feed="local",
                            symbol="BTCUSDT",
                            close=1_000,
                            retrieved_at=datetime(2026, 8, 20, 6, 32, tzinfo=UTC),
                            payload_suffix="between-auth-and-engine",
                        )
                    ]
                )
                == 1
            )
            assert pipeline.ingest(options).status == "reused"
        return original(*args, **kwargs)

    pipeline._evaluate_engines = append_revision_after_authentication  # type: ignore[method-assign]
    outcome = pipeline.evaluate(EvaluationOptions(scope=scope, force=True))
    current_query = pipeline._local_query(scope)
    assert current_query is not None
    new_hash = pipeline.bars.manifest(current_query).dataset_hash
    evaluated = database.frame(
        "select strategy_run_id, dataset_hash from strategy_runs where status = 'evaluated' order by run_timestamp"
    )
    assert len(evaluated) == 1
    run = evaluated.iloc[0]
    persisted_signal_count = int(
        database.scalar(
            "select count(*) from strategy_run_signal_links where strategy_run_id = :run_id",
            {"run_id": str(run.strategy_run_id)},
        )
        or 0
    )

    assert outcome.status == "completed"
    assert (str(run.dataset_hash), engine_ledger_sizes[-1]) in {(str(old_hash), 80), (new_hash, 81)}
    assert (str(run.dataset_hash), engine_ledger_sizes[-1]) != (str(old_hash), 81)
    assert persisted_signal_count == engine_ledger_sizes[-1]


def test_live_adapter_backfill_is_unavailable_for_strict_revision_as_of_evaluation(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    start = datetime(2026, 8, 20, tzinfo=UTC)
    count = 80
    payload: list[list[object]] = []
    previous = 100.0
    for index in range(count):
        opened_at = start + timedelta(minutes=5 * index)
        close = previous + (1.0 if index % 3 else -0.5)
        open_ms = int(opened_at.timestamp() * 1_000)
        close_ms = int((opened_at + timedelta(minutes=5)).timestamp() * 1_000) - 1
        payload.append(
            [
                open_ms,
                str(previous),
                str(max(previous, close) + 0.2),
                str(min(previous, close) - 0.2),
                str(close),
                "1000",
                close_ms,
                "100000",
                10,
                "500",
                "50000",
                "0",
            ]
        )
        previous = close

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    database_url = f"duckdb:///{tmp_path / 'live-shaped.duckdb'}"
    database = Database.from_url(database_url)
    pipeline = create_strategy_pipeline(
        _settings(project_root, database_url),
        database,
        http_client=client,
    )
    scope = StrategyScope(
        strategy_id="rsi_reversal",
        provider=BarProviderName.BINANCE,
        feed="spot",
        symbol="BTCUSDT",
        interval=BarInterval.FIVE_MINUTES,
        mode=StrategyMode.PAPER,
    )

    ingested = pipeline.ingest(IngestOptions(scope=scope, start=start, end=start + timedelta(minutes=5 * count)))
    evaluated = pipeline.evaluate(EvaluationOptions(scope=scope))
    signals = database.frame("select decision_timestamp from strategy_signals order by decision_timestamp")

    assert ingested.status == "completed"
    assert evaluated.status == "unavailable"
    assert "revision-as-of" in evaluated.message
    assert signals.empty
    assert database.scalar("select count(*) from strategy_runs where status = 'evaluated'") == 0


def test_corrected_refetch_preserves_signal_prefix_and_adds_receipt_decision(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    bars_path = tmp_path / "revision-bars.csv"
    _write_bars(bars_path, 80)
    database_url = f"duckdb:///{tmp_path / 'revision-causality.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, bars_path)
    scope = _scope("rsi_reversal")
    options = _ingest_options(scope)
    assert pipeline.ingest(options).status == "completed"
    assert pipeline.evaluate(EvaluationOptions(scope=scope)).status == "completed"

    registered = pipeline.registry.resolve("rsi_reversal")
    query, _manifest, unavailable = pipeline._requested_coverage(scope)
    assert query is not None and unavailable is None
    original_ledger = pipeline.bars._matching_frame(query)
    original_signals = registered.generator(registered.spec, original_ledger, strategy_pipeline.StrategyContext())
    corrected_open = datetime(2026, 8, 20, 1, 40, tzinfo=UTC)
    receipt = datetime(2026, 8, 20, 6, 45, tzinfo=UTC)
    correction = _market_bar(
        corrected_open,
        provider="csv",
        feed="local",
        symbol="BTCUSDT",
        close=1_000,
        retrieved_at=receipt,
        payload_suffix="correction",
    )
    assert pipeline.bars.append([correction]) == 1
    assert pipeline.ingest(options).status == "reused"

    seen_ledgers: list[pd.DataFrame] = []

    def capture_generator(spec, bars, context):
        seen_ledgers.append(bars.copy())
        return registered.generator(spec, bars, context)

    capturing_registry = StrategyRegistry()
    capturing_registry.register(registered.spec, capture_generator, registered.metadata)
    pipeline.registry = capturing_registry
    recomputed = pipeline.evaluate(EvaluationOptions(scope=scope, force=True))
    revised_ledger = pipeline.bars._matching_frame(query)
    revised_signals = registered.generator(registered.spec, revised_ledger, strategy_pipeline.StrategyContext())

    assert recomputed.status == "completed"
    assert len(seen_ledgers[-1]) == 81
    assert len(revised_signals) == 81
    pd.testing.assert_frame_equal(
        revised_signals.iloc[:80].reset_index(drop=True),
        original_signals.reset_index(drop=True),
        check_dtype=False,
    )
    assert revised_signals.iloc[-1]["decision_timestamp"] == pd.Timestamp(receipt)
    assert revised_signals.iloc[-1]["data_through"] == pd.Timestamp("2026-08-20T06:40:00Z")
    assert revised_signals["decision_timestamp"].is_monotonic_increasing
    assert revised_signals["decision_timestamp"].is_unique


def test_mid_history_correction_feedback_uses_final_state_for_the_actual_execution_bar(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    bars_path = tmp_path / "outcome-revision-bars.csv"
    _write_bars(bars_path, 80)
    database_url = f"duckdb:///{tmp_path / 'outcome-revision.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, bars_path)
    scope = _scope("rsi_reversal")
    assert pipeline.ingest(_ingest_options(scope)).status == "completed"
    correction_receipt = datetime(2026, 8, 20, 2, 36, tzinfo=UTC)
    assert (
        pipeline.bars.append(
            [
                _market_bar(
                    datetime(2026, 8, 20, 1, 40, tzinfo=UTC),
                    provider="csv",
                    feed="local",
                    symbol="BTCUSDT",
                    close=1_000,
                    retrieved_at=correction_receipt,
                    payload_suffix="mid-history-correction",
                )
            ]
        )
        == 1
    )
    assert pipeline.ingest(_ingest_options(scope)).status == "reused"

    captured: list[pd.DataFrame] = []
    original = pipeline._resolved_outcomes

    def capture(*args, **kwargs):
        resolved = original(*args, **kwargs)
        captured.append(resolved.copy())
        return resolved

    pipeline._resolved_outcomes = capture  # type: ignore[method-assign]
    outcome = pipeline.evaluate(EvaluationOptions(scope=scope, force=True))
    resolved = captured[-1]
    ordinary = resolved.loc[resolved["decision_timestamp"] == pd.Timestamp("2026-08-20T03:25:00Z")].iloc[0]
    shared_execution = resolved.loc[resolved["execution_timestamp"] == pd.Timestamp("2026-08-20T02:40:00Z")]
    evidence = database.frame("select evidence from ensemble_weights order by effective_at desc limit 1").iloc[0][
        "evidence"
    ]
    provenance = evidence["resolved_outcome_provenance"]

    assert outcome.status == "completed"
    assert ordinary["outcome_available_at"] == pd.Timestamp("2026-08-20T03:30:00Z")
    assert ordinary["realized_return"] == pytest.approx(121.0 / 120.0 - 1)
    assert shared_execution.empty
    assert not resolved.duplicated(["strategy_id", "source_execution_hash"]).any()
    assert resolved["source_decision_hash"].str.len().eq(64).all()
    assert resolved["source_execution_hash"].str.len().eq(64).all()
    assert resolved["source_execution_hash"].is_unique
    assert provenance["record_count"] == len(resolved)
    assert provenance["records_hash"] == canonical_hash(provenance["records"])
    assert {row["source_decision_hash"] for row in provenance["records"]} == set(resolved["source_decision_hash"])


def test_feedback_uses_task4_delayed_fills_and_excludes_outcomes_crossing_the_final_boundary(
    project_root, tmp_path
) -> None:
    _configure_strategy(project_root)
    bars_path = tmp_path / "delayed-fill-bars.csv"
    _write_bars(bars_path, 80)
    raw = pd.read_csv(bars_path)
    raw.loc[[21, 22, 62, 63], "volume"] = 0
    raw.to_csv(bars_path, index=False)
    database_url = f"duckdb:///{tmp_path / 'delayed-fill.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, bars_path)
    configured = pipeline.registry.resolve("rsi_reversal")

    def sparse_transitions(_spec, bars, _context):
        rows = []
        for index, signal in ((20, 1), (61, -1)):
            if len(bars) <= index:
                continue
            decision = pd.Timestamp(bars.iloc[index]["available_at"])
            rows.append(
                {
                    "decision_timestamp": decision,
                    "data_through": pd.Timestamp(bars.iloc[index]["close_timestamp"]),
                    "signal": signal,
                    "strength": 1.0,
                    "reason": "test transition",
                }
            )
        return pd.DataFrame(
            rows,
            columns=["decision_timestamp", "data_through", "signal", "strength", "reason"],
        )

    registry = StrategyRegistry()
    registry.register(configured.spec, sparse_transitions, configured.metadata)
    pipeline.registry = registry
    scope = _scope("rsi_reversal")
    assert pipeline.ingest(_ingest_options(scope)).status == "completed"
    captured: dict[str, object] = {}
    original = pipeline._resolved_outcomes

    def capture(bars, components, evaluations, boundary):
        resolved = original(bars, components, evaluations, boundary)
        captured.update(components=components, boundary=boundary, resolved=resolved.copy())
        return resolved

    pipeline._resolved_outcomes = capture  # type: ignore[method-assign]
    outcome = pipeline.evaluate(EvaluationOptions(scope=scope, force=True))
    components = captured["components"]
    signals = components[0][1]  # type: ignore[index]
    ledger = components[0][2].trade_ledger  # type: ignore[index]
    boundary = captured["boundary"]
    resolved = captured["resolved"]
    first_hash, sealed_hash = signals["decision_hash"].tolist()
    first_fill = ledger.loc[ledger["source_decision_hashes"].map(lambda hashes: first_hash in hashes)].iloc[0]
    sealed_fill = ledger.loc[ledger["source_decision_hashes"].map(lambda hashes: sealed_hash in hashes)].iloc[0]
    first_feedback = resolved.loc[resolved["source_decision_hash"] == first_hash].iloc[0]
    persisted = database.frame(
        "select execution_id, decision_timestamp, execution_timestamp from strategy_executions "
        "where execution_timestamp = :executed and decision_timestamp = :decision",
        {
            "executed": first_fill.execution_timestamp,
            "decision": first_fill.decision_timestamp,
        },
    )

    assert outcome.status == "completed"
    assert first_fill.execution_timestamp == pd.Timestamp("2026-08-20T01:55:00Z")
    assert first_feedback["execution_timestamp"] == first_fill.execution_timestamp
    assert first_feedback["source_execution_hash"] == persisted.iloc[0]["execution_id"]
    assert sealed_fill.execution_timestamp == boundary.final_start  # type: ignore[union-attr]
    assert sealed_hash not in set(resolved["source_decision_hash"])
    assert (resolved["execution_timestamp"] < boundary.final_start).all()  # type: ignore[union-attr]
    assert (resolved["outcome_available_at"] < boundary.final_start).all()  # type: ignore[union-attr]


def test_valid_abstention_without_fills_persists_typed_no_feedback_evidence(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    bars_path = tmp_path / "abstention-bars.csv"
    _write_bars(bars_path, 80)
    database_url = f"duckdb:///{tmp_path / 'abstention.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, bars_path)
    configured = pipeline.registry.resolve("rsi_reversal")

    def always_abstain(_spec, bars, _context):
        return pd.DataFrame(
            {
                "decision_timestamp": pd.to_datetime(bars["available_at"], utc=True),
                "data_through": pd.to_datetime(bars["close_timestamp"], utc=True),
                "signal": pd.Series(0, index=bars.index, dtype="int8"),
                "strength": pd.Series(0.0, index=bars.index, dtype="float64"),
                "reason": "abstain: no actionable state",
            }
        )

    registry = StrategyRegistry()
    registry.register(configured.spec, always_abstain, configured.metadata)
    pipeline.registry = registry
    scope = _scope("rsi_reversal")
    assert pipeline.ingest(_ingest_options(scope)).status == "completed"
    captured: list[pd.DataFrame] = []
    original = pipeline._resolved_outcomes

    def capture(*args, **kwargs):
        resolved = original(*args, **kwargs)
        captured.append(resolved.copy())
        return resolved

    pipeline._resolved_outcomes = capture  # type: ignore[method-assign]
    evaluated = pipeline.evaluate(EvaluationOptions(scope=scope))
    repeated = pipeline.evaluate(EvaluationOptions(scope=scope))
    resolved = captured[0]
    weight = database.frame("select weight, evidence from ensemble_weights").iloc[0]
    provenance = weight["evidence"]["resolved_outcome_provenance"]

    assert evaluated.status == "completed"
    assert repeated.status == "reused"
    assert resolved.empty
    assert resolved.columns.tolist() == [
        "strategy_id",
        "decision_timestamp",
        "execution_timestamp",
        "outcome_available_at",
        "signal",
        "realized_return",
        "cost",
        "source_decision_hash",
        "source_execution_hash",
        "dataset_hash",
        "strategy_version",
        "symbol",
        "interval",
        "mode",
    ]
    assert str(resolved["decision_timestamp"].dtype) == "datetime64[ns, UTC]"
    assert str(resolved["signal"].dtype) == "int8"
    assert str(resolved["realized_return"].dtype) == "float64"
    assert database.scalar("select count(*) from strategy_runs where status = 'evaluated'") == 1
    assert database.scalar("select count(*) from strategy_executions") == 0
    assert provenance == {
        "record_count": 0,
        "records": [],
        "records_hash": canonical_hash([]),
        "feedback_status": "no_observed_outcomes",
    }
    assert weight["evidence"]["outcomes_through"] is None
    assert weight["evidence"]["current_decision"]["status"] == "abstain"


def test_concurrent_forced_plural_evaluations_allocate_complete_atomic_cohorts(project_root, tmp_path) -> None:
    _configure_plural_strategies(project_root)
    bars = tmp_path / "concurrent-plural-bars.csv"
    _write_bars(bars, 80)
    database_url = f"duckdb:///{tmp_path / 'concurrent-plural.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, bars)
    pipeline.clock = lambda: datetime(2026, 8, 23, 12, tzinfo=UTC)
    scope = _scope("rsi_reversal", "extreme_return_reversal")
    assert pipeline.ingest(_ingest_options(scope)).status == "completed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda _: pipeline.evaluate(EvaluationOptions(scope=scope, force=True)),
                range(2),
            )
        )
    reused = pipeline.evaluate(EvaluationOptions(scope=scope))
    runs = database.frame(
        "select strategy_run_id, strategy_id, run_timestamp, metrics from strategy_runs "
        "where status = 'evaluated' order by run_timestamp, strategy_id"
    )
    weights = database.frame(
        "select strategy_run_id, strategy_id, effective_at, evidence from ensemble_weights "
        "order by effective_at, strategy_id"
    )

    assert [item.status for item in outcomes] == ["completed", "completed"]
    assert len(runs) == len(weights) == 4
    generations = {int(row["cohort_generation"]) for row in runs["metrics"]}
    assert generations == {1, 2}
    for generation in generations:
        selected = runs.loc[
            runs["metrics"].map(
                lambda value, selected_generation=generation: value["cohort_generation"] == selected_generation
            )
        ]
        selected_weights = weights.loc[
            weights["evidence"].map(
                lambda value, selected_generation=generation: value["cohort_generation"] == selected_generation
            )
        ]
        assert selected["strategy_id"].nunique() == len(selected) == 2
        assert selected["run_timestamp"].nunique() == 1
        assert selected_weights["effective_at"].nunique() == 1
        assert selected_weights["strategy_id"].nunique() == len(selected_weights) == 2
        assert len({row["cohort_decision_hash"] for row in selected_weights["evidence"]}) == 1
    newest_generation = max(generations)
    newest_run_ids = set(
        runs.loc[runs["metrics"].map(lambda value: value["cohort_generation"] == newest_generation), "strategy_run_id"]
    )
    assert reused.status == "reused"
    assert set(reused.strategy_run_ids) == newest_run_ids


def test_overlapping_scalar_and_plural_cohorts_serialize_component_reservations(project_root, tmp_path) -> None:
    _configure_plural_strategies(project_root)
    bars = tmp_path / "overlapping-cohort-bars.csv"
    _write_bars(bars, 80)
    database_url = f"duckdb:///{tmp_path / 'overlapping-cohort.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, bars)
    pipeline.clock = lambda: datetime(2026, 8, 23, 12, tzinfo=UTC)
    plural_scope = _scope("rsi_reversal", "extreme_return_reversal")
    scalar_scope = _scope("rsi_reversal")
    assert pipeline.ingest(_ingest_options(plural_scope)).status == "completed"
    original_timestamp = pipeline._cohort_run_timestamp
    reservation_barrier = threading.Barrier(2)

    def synchronize_after_timestamp(*args, **kwargs):
        timestamp = original_timestamp(*args, **kwargs)
        with suppress(threading.BrokenBarrierError):
            reservation_barrier.wait(timeout=0.5)
        return timestamp

    pipeline._cohort_run_timestamp = synchronize_after_timestamp  # type: ignore[method-assign]
    outcomes: list[StageOutcome] = []
    errors: list[Exception] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(pipeline.evaluate, EvaluationOptions(scope=plural_scope, force=True)),
            executor.submit(pipeline.evaluate, EvaluationOptions(scope=scalar_scope, force=True)),
        ]
        for future in futures:
            try:
                outcomes.append(future.result(timeout=30))
            except Exception as error:
                errors.append(error)
    pipeline._cohort_run_timestamp = original_timestamp  # type: ignore[method-assign]
    plural_reuse = pipeline.evaluate(EvaluationOptions(scope=plural_scope))
    scalar_reuse = pipeline.evaluate(EvaluationOptions(scope=scalar_scope))
    runs = database.frame(
        "select strategy_run_id, strategy_id, run_timestamp, status, metrics from strategy_runs "
        "order by run_timestamp, strategy_id"
    )

    assert errors == []
    assert [item.status for item in outcomes] == ["completed", "completed"]
    assert len(runs) == 3
    assert set(runs["status"]) == {"evaluated"}
    assert runs["strategy_run_id"].is_unique
    rsi_runs = runs.loc[runs["strategy_id"] == "rsi_reversal"]
    assert len(rsi_runs) == 2
    assert rsi_runs["run_timestamp"].is_unique
    assert {len(item["cohort_members"]) for item in runs["metrics"]} == {1, 2}
    assert plural_reuse.status == scalar_reuse.status == "reused"
    assert len(plural_reuse.strategy_run_ids) == 2
    assert len(scalar_reuse.strategy_run_ids) == 1


def test_cohort_reservation_failure_is_persisted_as_terminal_failure(project_root, tmp_path) -> None:
    _configure_plural_strategies(project_root)
    bars = tmp_path / "reservation-failure-bars.csv"
    _write_bars(bars, 80)
    database_url = f"duckdb:///{tmp_path / 'reservation-failure.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, bars)
    scope = _scope("rsi_reversal", "extreme_return_reversal")
    assert pipeline.ingest(_ingest_options(scope)).status == "completed"
    injected = False

    def fail_reservation(_connection, _cursor, statement, _parameters, _context, _executemany):
        nonlocal injected
        if not injected and "INSERT INTO strategy_runs" in statement:
            injected = True
            raise RuntimeError("injected cohort reservation failure")

    event.listen(database.engine, "before_cursor_execute", fail_reservation)
    try:
        with pytest.raises(RuntimeError, match="injected cohort reservation failure"):
            pipeline.evaluate(EvaluationOptions(scope=scope, force=True))
    finally:
        event.remove(database.engine, "before_cursor_execute", fail_reservation)
    runs = database.frame("select strategy_id, status, metrics, ended_at from strategy_runs order by strategy_id")

    assert len(runs) == 2
    assert set(runs["status"]) == {"failed"}
    assert runs["ended_at"].notna().all()
    assert all(item["reservation_error"] == "injected cohort reservation failure" for item in runs["metrics"])
    assert all(item["coverage_manifest"]["row_count"] == 80 for item in runs["metrics"])


def test_transient_cohort_reservation_conflict_retries_from_fresh_database_state(project_root, tmp_path) -> None:
    _configure_plural_strategies(project_root)
    bars = tmp_path / "reservation-retry-bars.csv"
    _write_bars(bars, 80)
    database_url = f"duckdb:///{tmp_path / 'reservation-retry.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, bars)
    scope = _scope("rsi_reversal", "extreme_return_reversal")
    assert pipeline.ingest(_ingest_options(scope)).status == "completed"
    injected = False

    def conflict_once(_connection, _cursor, statement, parameters, _context, _executemany):
        nonlocal injected
        if not injected and "INSERT INTO strategy_runs" in statement:
            injected = True
            raise IntegrityError(statement, parameters, RuntimeError("injected reservation conflict"))

    event.listen(database.engine, "before_cursor_execute", conflict_once)
    try:
        outcome = pipeline.evaluate(EvaluationOptions(scope=scope, force=True))
    finally:
        event.remove(database.engine, "before_cursor_execute", conflict_once)
    runs = database.frame("select strategy_id, status, metrics from strategy_runs order by strategy_id")

    assert injected is True
    assert outcome.status == "completed"
    assert len(runs) == 2
    assert set(runs["status"]) == {"evaluated"}
    assert not any("reservation_error" in item for item in runs["metrics"])


def test_concurrent_fixed_clock_forced_ingests_reserve_independent_terminal_requests(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    bars_path = tmp_path / "concurrent-ingest-bars.csv"
    _write_bars(bars_path, 80)
    database_url = f"duckdb:///{tmp_path / 'concurrent-ingest.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, bars_path)
    pipeline.clock = lambda: datetime(2026, 8, 23, 12, tzinfo=UTC)
    scope = _scope("rsi_reversal")
    source = pipeline.providers[BarProviderName.CSV]
    barrier = __import__("threading").Barrier(2)

    class SynchronizedProvider:
        def fetch(self, request: BarRequest):
            barrier.wait(timeout=10)
            return source.fetch(request)

    pipeline.providers[BarProviderName.CSV] = SynchronizedProvider()
    outcomes: list[StageOutcome] = []
    errors: list[Exception] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(pipeline.ingest, _ingest_options(scope, force=True)) for _ in range(2)]
        for future in futures:
            try:
                outcomes.append(future.result())
            except Exception as error:
                errors.append(error)
    requests = database.frame(
        "select coverage_request_id, requested_at, status from dataset_coverage_requests order by requested_at"
    )

    assert errors == []
    assert [item.status for item in outcomes] == ["completed", "completed"]
    assert len(requests) == 2
    assert requests["coverage_request_id"].nunique() == requests["requested_at"].nunique() == 2
    assert requests["status"].tolist() == ["complete", "complete"]


def test_partial_requested_coverage_is_persisted_and_blocks_cli_evaluation(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    bars = tmp_path / "partial-bars.csv"
    _write_bars(bars, 60)
    database_url = f"duckdb:///{tmp_path / 'partial.duckdb'}"
    common = _base_arguments(project_root, database_url, bars)

    ingested = RUNNER.invoke(
        app,
        [
            "strategy",
            "ingest",
            *common,
            "--start",
            "2026-08-20T00:00:00Z",
            "--end",
            "2026-08-20T06:40:00Z",
        ],
    )
    evaluated = RUNNER.invoke(app, ["strategy", "evaluate", *common, "--mode", "paper"])
    database = Database.from_url(database_url)
    requests = database.frame("select status, requested_start, requested_end, gaps from dataset_coverage_requests")
    snapshot_path = tmp_path / "partial-snapshot.json"
    exported = RUNNER.invoke(
        app,
        [
            "strategy",
            "export",
            "--project-root",
            str(project_root),
            "--database-url",
            database_url,
            "--output",
            str(snapshot_path),
            "--report-output",
            str(tmp_path / "partial-report.md"),
        ],
    )
    snapshot = AppSnapshot.model_validate_json(snapshot_path.read_text(encoding="utf-8"))

    assert ingested.exit_code != 0
    assert _events(ingested.stdout)[-1]["event"] == "error"
    assert "unavailable" in str(_events(ingested.stdout)[-1]["message"])
    assert evaluated.exit_code != 0
    assert _events(evaluated.stdout)[-1]["event"] == "error"
    assert "coverage" in str(_events(evaluated.stdout)[-1]["message"])
    assert requests["status"].tolist() == ["incomplete"]
    assert requests.iloc[0]["gaps"]
    assert exported.exit_code == 0, exported.output
    assert snapshot.dataset_coverage[0].requested_end.isoformat() == "2026-08-20T06:40:00+00:00"
    assert snapshot.dataset_coverage[0].complete is False
    assert snapshot.dataset_coverage[0].gaps
    assert database.scalar("select count(*) from strategy_runs") == 0


def test_empty_forced_refresh_is_unavailable_even_when_prior_coverage_exists(project_root, tmp_path) -> None:
    class EmptyProvider:
        def fetch(self, request: BarRequest) -> list[MarketBar]:
            return []

    _configure_strategy(project_root)
    bars = tmp_path / "complete-bars.csv"
    _write_bars(bars, 80)
    database_url = f"duckdb:///{tmp_path / 'empty-force.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, bars)
    scope = _scope("rsi_reversal")
    options = _ingest_options(scope)
    assert pipeline.ingest(options).status == "completed"
    pipeline.providers[BarProviderName.CSV] = EmptyProvider()

    refreshed = pipeline.ingest(options.model_copy(update={"force": True}))
    persisted = database.frame(
        "select status from dataset_coverage_requests order by requested_at, coverage_request_id"
    )

    assert refreshed.status == "unavailable"
    assert "empty" in refreshed.message
    assert persisted["status"].tolist() == ["complete", "unavailable"]


def test_failed_forced_fetch_invalidates_stale_coverage_and_successful_retry_recovers(project_root, tmp_path) -> None:
    class FailingProvider:
        def __init__(self, error: RuntimeError):
            self.error = error

        def fetch(self, request: BarRequest) -> list[MarketBar]:
            raise self.error

    _configure_strategy(project_root)
    bars = tmp_path / "fetch-recovery.csv"
    _write_bars(bars, 80)
    database_url = f"duckdb:///{tmp_path / 'fetch-recovery.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, bars)
    scope = _scope("rsi_reversal")
    options = _ingest_options(scope)
    assert pipeline.ingest(options).status == "completed"
    assert pipeline.evaluate(EvaluationOptions(scope=scope)).status == "completed"
    working_provider = pipeline.providers[BarProviderName.CSV]
    original_error = RuntimeError("provider connection failed after request")
    pipeline.providers[BarProviderName.CSV] = FailingProvider(original_error)

    with pytest.raises(RuntimeError) as captured:
        pipeline.ingest(options.model_copy(update={"force": True}))

    failed_statuses = database.frame(
        "select status from dataset_coverage_requests order by requested_at, coverage_request_id"
    )["status"].tolist()
    blocked = pipeline.evaluate(EvaluationOptions(scope=scope))
    pipeline.providers[BarProviderName.CSV] = working_provider
    recovered = pipeline.ingest(options.model_copy(update={"force": True}))
    available_again = pipeline.evaluate(EvaluationOptions(scope=scope))

    assert captured.value is original_error
    assert failed_statuses[0] == "complete" and failed_statuses[-1] in {"incomplete", "unavailable"}
    assert blocked.status == "unavailable"
    assert recovered.status == "completed"
    assert available_again.status in {"completed", "reused"}


def test_alpaca_exchange_calendar_skips_weekend_and_detects_only_in_session_gap(project_root, tmp_path) -> None:
    class StaticProvider:
        def __init__(self, bars: list[MarketBar]):
            self.bars = bars

        def fetch(self, request: BarRequest) -> list[MarketBar]:
            return [bar for bar in self.bars if request.start <= bar.open_timestamp < request.end]

    _configure_strategy(project_root)
    friday = pd.date_range("2026-03-06T14:30:00Z", periods=78, freq="5min")
    monday = pd.date_range("2026-03-09T13:30:00Z", periods=78, freq="5min")
    session_opens = [timestamp.to_pydatetime() for timestamp in (*friday, *monday)]
    bars = [
        _market_bar(
            opened_at,
            provider="alpaca",
            feed="iex",
            symbol="AAPL",
            close=100 + index / 100,
        )
        for index, opened_at in enumerate(session_opens)
    ]
    scope = StrategyScope(
        strategy_id="rsi_reversal",
        provider=BarProviderName.ALPACA,
        feed="iex",
        symbol="AAPL",
        interval=BarInterval.FIVE_MINUTES,
        mode=StrategyMode.PAPER,
    )
    options = IngestOptions(
        scope=scope,
        start=datetime(2026, 3, 6, 14, 30, tzinfo=UTC),
        end=datetime(2026, 3, 9, 20, 0, tzinfo=UTC),
    )

    complete_url = f"duckdb:///{tmp_path / 'calendar-complete.duckdb'}"
    complete_pipeline, complete_database = _csv_pipeline(project_root, complete_url, tmp_path / "unused.csv")
    complete_pipeline.providers[BarProviderName.ALPACA] = StaticProvider(bars)
    complete = complete_pipeline.ingest(options)
    evaluated = complete_pipeline.evaluate(EvaluationOptions(scope=scope))
    snapshot_path = tmp_path / "calendar-snapshot.json"
    complete_pipeline.export(
        ExportOptions(
            snapshot_path=snapshot_path,
            report_path=tmp_path / "calendar-report.md",
        )
    )
    snapshot = AppSnapshot.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
    coverage_evidence = complete_database.frame("select gaps from dataset_coverage_requests").iloc[0]["gaps"]

    missing_url = f"duckdb:///{tmp_path / 'calendar-missing.duckdb'}"
    missing_pipeline, missing_database = _csv_pipeline(project_root, missing_url, tmp_path / "unused-missing.csv")
    missing_pipeline.providers[BarProviderName.ALPACA] = StaticProvider(
        [bar for bar in bars if bar.open_timestamp != datetime(2026, 3, 9, 15, 0, tzinfo=UTC)]
    )
    incomplete = missing_pipeline.ingest(options)
    missing_evidence = missing_database.frame("select gaps from dataset_coverage_requests").iloc[0]["gaps"]

    assert complete.status == evaluated.status == "completed"
    assert coverage_evidence["calendar_id"] == "XNYS"
    assert coverage_evidence["calendar_version"]
    assert coverage_evidence["missing"] == []
    assert snapshot.dataset_coverage[0].calendar_id == "XNYS"
    assert snapshot.dataset_coverage[0].calendar_version == coverage_evidence["calendar_version"]
    assert incomplete.status == "unavailable"
    assert missing_evidence["calendar_id"] == "XNYS"
    assert missing_evidence["missing"] == [
        {
            "start": "2026-03-09T15:00:00+00:00",
            "end": "2026-03-09T15:05:00+00:00",
            "missing_bars": 1,
        }
    ]


def test_stale_calendar_coverage_blocks_research_until_reingest_and_snapshot_uses_current_evidence(
    project_root, tmp_path
) -> None:
    class StaticProvider:
        def __init__(self, bars: list[MarketBar]):
            self.bars = bars

        def fetch(self, request: BarRequest) -> list[MarketBar]:
            return [bar for bar in self.bars if request.start <= bar.open_timestamp < request.end]

    _configure_strategy(project_root)
    opens = pd.date_range("2027-12-31T14:30:00Z", periods=78, freq="5min")
    bars = [
        _market_bar(
            opened_at.to_pydatetime(),
            provider="alpaca",
            feed="iex",
            symbol="AAPL",
            close=100 + index / 100,
        )
        for index, opened_at in enumerate(opens)
    ]
    database_url = f"duckdb:///{tmp_path / 'stale-calendar.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, tmp_path / "unused.csv")
    pipeline.providers[BarProviderName.ALPACA] = StaticProvider(bars)
    scope = StrategyScope(
        strategy_id="rsi_reversal",
        provider=BarProviderName.ALPACA,
        feed="iex",
        symbol="AAPL",
        interval=BarInterval.FIVE_MINUTES,
        mode=StrategyMode.PAPER,
    )
    options = IngestOptions(
        scope=scope,
        start=datetime(2027, 12, 31, 14, 30, tzinfo=UTC),
        end=datetime(2027, 12, 31, 21, 0, tzinfo=UTC),
    )
    initial = pipeline.ingest(options)
    stale = database.frame("select coverage_request_id, gaps from dataset_coverage_requests").iloc[0]
    stale_evidence = dict(stale.gaps)
    stale_evidence["calendar_version"] = "offline-rules-2026.2"
    with database.engine.begin() as connection:
        connection.execute(
            update(dataset_coverage_requests)
            .where(dataset_coverage_requests.c.coverage_request_id == stale.coverage_request_id)
            .values(dataset_hash="e" * 64, gaps=stale_evidence)
        )

    blocked_evaluation = pipeline.evaluate(EvaluationOptions(scope=scope))
    blocked_learning = pipeline.learn(LearningOptions(scope=scope, evaluation_budget=1))
    runs_before_refresh = database.scalar("select count(*) from strategy_runs")
    recovered = pipeline.ingest(options)
    evaluated = pipeline.evaluate(EvaluationOptions(scope=scope))
    current_request = database.frame(
        "select coverage_request_id, dataset_hash, gaps from dataset_coverage_requests "
        "where coverage_request_id != :stale_id order by requested_at desc limit 1",
        {"stale_id": str(stale.coverage_request_id)},
    ).iloc[0]
    mismatched_current_evidence = dict(stale_evidence)
    mismatched_current_evidence["calendar_version"] = "offline-rules-2026.3"
    with database.engine.begin() as connection:
        connection.execute(
            update(dataset_coverage_requests)
            .where(dataset_coverage_requests.c.coverage_request_id == stale.coverage_request_id)
            .values(
                requested_at=datetime(2030, 1, 1, tzinfo=UTC),
                gaps=mismatched_current_evidence,
            )
        )
    snapshot_path = tmp_path / "current-calendar-snapshot.json"
    pipeline.export(
        ExportOptions(
            snapshot_path=snapshot_path,
            report_path=tmp_path / "current-calendar-report.md",
        )
    )
    snapshot = AppSnapshot.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
    evaluated_hash = database.scalar("select dataset_hash from strategy_runs")

    assert initial.status == "completed"
    assert blocked_evaluation.status == blocked_learning.status == "unavailable"
    assert runs_before_refresh == 0
    assert recovered.status == "reused"
    assert evaluated.status == "completed"
    assert current_request.gaps["calendar_id"] == "XNYS"
    assert current_request.gaps["calendar_version"] == "offline-rules-2026.3"
    assert current_request.dataset_hash == evaluated_hash == evaluated.dataset_hash
    assert len(snapshot.dataset_coverage) == 1
    assert snapshot.dataset_coverage[0].dataset_hash == evaluated_hash
    assert snapshot.dataset_coverage[0].calendar_id == "XNYS"
    assert snapshot.dataset_coverage[0].calendar_version == "offline-rules-2026.3"


def test_strategy_evaluation_reuses_exact_cache_key_and_force_appends_only_that_key(
    project_root, tmp_path, monkeypatch
) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 23, 12, tzinfo=tz)

    monkeypatch.setattr(strategy_pipeline, "datetime", FixedDateTime)
    _configure_strategy(project_root)
    bars = tmp_path / "bars.csv"
    _write_bars(bars, 80)
    database_url = f"duckdb:///{tmp_path / 'evaluate.duckdb'}"
    common = _base_arguments(project_root, database_url, bars)
    ingest = RUNNER.invoke(
        app,
        [
            "strategy",
            "ingest",
            *common,
            "--start",
            "2026-08-20T00:00:00Z",
            "--end",
            "2026-08-20T06:40:00Z",
        ],
    )
    assert ingest.exit_code == 0, ingest.output

    first = RUNNER.invoke(app, ["strategy", "evaluate", *common, "--mode", "paper"])
    repeated = RUNNER.invoke(app, ["strategy", "evaluate", *common, "--mode", "paper"])
    database = Database.from_url(database_url)
    selected = database.frame("select * from strategy_runs").iloc[0]
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "update strategy_runs set status = 'failed' where strategy_run_id = ?",
            (str(selected.strategy_run_id),),
        )
    retried_failed = RUNNER.invoke(app, ["strategy", "evaluate", *common, "--mode", "paper"])
    created_at = datetime(2026, 8, 23, tzinfo=UTC)
    unrelated_rows = [
        {
            "strategy_run_id": "unrelated-dataset-run",
            "dataset_hash": "f" * 64,
            "strategy_id": str(selected.strategy_id),
            "strategy_version": str(selected.strategy_version),
            "family": str(selected.family),
            "symbol": str(selected.symbol),
            "interval": str(selected.interval),
            "mode": str(selected["mode"]),
            "run_timestamp": created_at,
            "parameters": {},
            "status": "unrelated_dataset_sentinel",
            "metrics": {"sentinel": 1},
            "started_at": created_at,
            "ended_at": created_at,
            "source": "test",
            "source_version": "1",
            "created_at": created_at,
        },
        {
            "strategy_run_id": "unrelated-version-run",
            "dataset_hash": str(selected.dataset_hash),
            "strategy_id": str(selected.strategy_id),
            "strategy_version": "unrelated-version",
            "family": str(selected.family),
            "symbol": str(selected.symbol),
            "interval": str(selected.interval),
            "mode": str(selected["mode"]),
            "run_timestamp": created_at + timedelta(microseconds=1),
            "parameters": {},
            "status": "unrelated_version_sentinel",
            "metrics": {"sentinel": 2},
            "started_at": created_at,
            "ended_at": created_at,
            "source": "test",
            "source_version": "1",
            "created_at": created_at,
        },
    ]
    database.insert("strategy_runs", unrelated_rows)
    forced = RUNNER.invoke(app, ["strategy", "evaluate", *common, "--mode", "paper", "--force"])

    runs = database.frame(
        "select strategy_run_id, dataset_hash, strategy_id, strategy_version, symbol, interval, mode, status, "
        "started_at, ended_at "
        "from strategy_runs order by strategy_run_id"
    )
    assert first.exit_code == 0, first.output
    assert repeated.exit_code == 0, repeated.output
    assert retried_failed.exit_code == 0, retried_failed.output
    assert forced.exit_code == 0, forced.output
    assert _events(repeated.stdout)[-1]["message"] == "reused cached evaluation"
    assert _events(retried_failed.stdout)[-1]["message"] != "reused cached evaluation"
    selected_runs = runs.loc[
        (runs["dataset_hash"] == selected.dataset_hash) & (runs["strategy_version"] == selected.strategy_version)
    ]
    assert len(selected_runs) == 3
    assert (selected_runs["ended_at"] >= selected_runs["started_at"]).all()
    assert runs.loc[runs["strategy_run_id"] == "unrelated-dataset-run", "status"].tolist() == [
        "unrelated_dataset_sentinel"
    ]
    assert runs.loc[runs["strategy_run_id"] == "unrelated-version-run", "status"].tolist() == [
        "unrelated_version_sentinel"
    ]


def test_concurrent_fixed_clock_forced_evaluations_reserve_distinct_runs(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    bars = tmp_path / "concurrent-bars.csv"
    _write_bars(bars, 80)
    database_url = f"duckdb:///{tmp_path / 'concurrent-force.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, bars)
    pipeline.clock = lambda: datetime(2026, 8, 23, 12, tzinfo=UTC)
    scope = _scope("rsi_reversal")
    assert pipeline.ingest(_ingest_options(scope)).status == "completed"
    registered = pipeline.registry.resolve("rsi_reversal")
    created_at = datetime(2026, 8, 23, 11, tzinfo=UTC)
    database.insert(
        "strategy_runs",
        [
            {
                "strategy_run_id": "unrelated-concurrent-sentinel",
                "dataset_hash": "f" * 64,
                "strategy_id": registered.spec.strategy_id,
                "strategy_version": registered.spec.deterministic_version,
                "family": registered.spec.family.value,
                "symbol": scope.symbol,
                "interval": scope.interval.value,
                "mode": scope.mode.value,
                "run_timestamp": created_at,
                "parameters": {},
                "status": "unrelated_sentinel",
                "metrics": {"sentinel": True},
                "started_at": created_at,
                "ended_at": created_at,
                "source": "test",
                "source_version": "1",
                "created_at": created_at,
            }
        ],
    )

    def force_evaluate() -> StageOutcome:
        return pipeline.evaluate(EvaluationOptions(scope=scope, force=True))

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: force_evaluate(), range(2)))

    runs = database.frame(
        "select strategy_run_id, dataset_hash, status, run_timestamp from strategy_runs order by run_timestamp"
    )
    selected = runs.loc[runs["dataset_hash"] != "f" * 64]
    weights = database.frame("select strategy_run_id, effective_at from ensemble_weights order by effective_at")
    assert [outcome.status for outcome in outcomes] == ["completed", "completed"]
    assert len(selected) == 2
    assert selected["strategy_run_id"].nunique() == selected["run_timestamp"].nunique() == 2
    assert len(weights) == weights["strategy_run_id"].nunique() == weights["effective_at"].nunique() == 2
    assert set(weights["strategy_run_id"]) == set(selected["strategy_run_id"])
    assert runs.loc[runs["strategy_run_id"] == "unrelated-concurrent-sentinel", "status"].tolist() == [
        "unrelated_sentinel"
    ]


def test_three_forced_generations_have_complete_immutable_run_evidence_links(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    bars = tmp_path / "run-evidence-bars.csv"
    _write_bars(bars, 80)
    database_url = f"duckdb:///{tmp_path / 'run-evidence.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, bars)
    scope = _scope("rsi_reversal")
    assert pipeline.ingest(_ingest_options(scope)).status == "completed"

    outcomes = [pipeline.evaluate(EvaluationOptions(scope=scope, force=force)) for force in (False, True, True)]
    table_names = set(database.table_names())

    assert [outcome.status for outcome in outcomes] == ["completed", "completed", "completed"]
    assert {"strategy_run_signal_links", "strategy_run_execution_links"} <= table_names
    runs = database.frame(
        "select strategy_run_id, dataset_hash, strategy_id, strategy_version, symbol, interval, mode "
        "from strategy_runs where status = 'evaluated' order by run_timestamp"
    )
    signal_counts = database.frame(
        "select strategy_run_id, count(*) as count from strategy_run_signal_links "
        "group by strategy_run_id order by strategy_run_id"
    )
    execution_counts = database.frame(
        "select strategy_run_id, count(*) as count from strategy_run_execution_links "
        "group by strategy_run_id order by strategy_run_id"
    )
    context_mismatches = database.scalar(
        "select count(*) from strategy_run_signal_links l "
        "join strategy_runs r on r.strategy_run_id = l.strategy_run_id "
        "join strategy_signals s on s.strategy_signal_id = l.strategy_signal_id "
        "where r.dataset_hash != s.dataset_hash or r.strategy_id != s.strategy_id "
        "or r.strategy_version != s.strategy_version or r.symbol != s.symbol "
        "or r.interval != s.interval or r.mode != s.mode"
    )

    assert len(runs) == len(signal_counts) == len(execution_counts) == 3
    assert signal_counts["count"].tolist() == [80, 80, 80]
    assert execution_counts["count"].min() > 0
    assert execution_counts["count"].nunique() == 1
    assert context_mismatches == 0


def test_evaluation_later_write_failure_rolls_back_children_marks_failed_and_retries(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    bars = tmp_path / "atomic-bars.csv"
    _write_bars(bars, 80)
    database_url = f"duckdb:///{tmp_path / 'atomic-evaluation.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, bars)
    scope = _scope("rsi_reversal")
    assert pipeline.ingest(_ingest_options(scope)).status == "completed"
    injected = False

    def fail_late(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        nonlocal injected
        if not injected and "INSERT INTO causal_audits" in statement:
            injected = True
            raise RuntimeError("injected later-write failure")

    event.listen(database.engine, "before_cursor_execute", fail_late)
    try:
        with pytest.raises(RuntimeError, match="injected later-write failure"):
            pipeline.evaluate(EvaluationOptions(scope=scope, force=True))
    finally:
        event.remove(database.engine, "before_cursor_execute", fail_late)

    assert database.scalar("select count(*) from strategy_runs where status = 'failed'") == 1
    assert database.scalar("select count(*) from strategy_signals") == 0
    assert database.scalar("select count(*) from strategy_executions") == 0
    assert database.scalar("select count(*) from strategy_run_signal_links") == 0
    assert database.scalar("select count(*) from strategy_run_execution_links") == 0
    assert database.scalar("select count(*) from causal_audits") == 0
    assert database.scalar("select count(*) from ensemble_weights") == 0

    retried = pipeline.evaluate(EvaluationOptions(scope=scope))

    assert retried.status == "completed"
    assert database.scalar("select count(*) from strategy_runs where status = 'evaluated'") == 1
    assert database.scalar("select count(*) from strategy_signals") > 0
    assert database.scalar("select count(*) from causal_audits") == 1
    assert database.scalar("select count(*) from ensemble_weights") == 1


def test_post_commit_exception_reconciles_complete_cohort_and_failure_handler_cannot_downgrade_it(
    project_root, tmp_path
) -> None:
    _configure_strategy(project_root)
    bars = tmp_path / "post-commit-bars.csv"
    _write_bars(bars, 80)
    database_url = f"duckdb:///{tmp_path / 'post-commit.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, bars)
    scope = _scope("rsi_reversal")
    assert pipeline.ingest(_ingest_options(scope)).status == "completed"
    original = pipeline._persist_evaluation_batch

    def commit_then_raise(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated lost acknowledgement after commit")

    pipeline._persist_evaluation_batch = commit_then_raise  # type: ignore[method-assign]
    evaluated = pipeline.evaluate(EvaluationOptions(scope=scope, force=True))
    repeated = pipeline.evaluate(EvaluationOptions(scope=scope))
    run = database.frame(
        "select strategy_run_id, dataset_hash, run_timestamp, status, metrics from strategy_runs"
    ).iloc[0]
    pipeline._persist_failed_run(
        scope,
        pipeline.registry.resolve("rsi_reversal"),
        str(run.dataset_hash),
        str(run.strategy_run_id),
        run.run_timestamp.to_pydatetime(),
        "late failure handler",
    )
    reconciled = database.frame("select status, metrics from strategy_runs").iloc[0]

    assert evaluated.status == "completed"
    assert repeated.status == "reused"
    assert reconciled.status == "evaluated"
    assert "error_summary" not in reconciled.metrics
    assert database.scalar("select count(*) from strategy_run_signal_links") == 80
    assert database.scalar("select count(*) from strategy_run_execution_links") > 0
    assert database.scalar("select count(*) from causal_audits") == 1
    assert database.scalar("select count(*) from ensemble_weights") == 1


def test_strategy_learning_uses_observed_bounded_trial_ledger_and_jsonl_progress(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    bars = tmp_path / "bars.csv"
    _write_bars(bars, 800)
    database_url = f"duckdb:///{tmp_path / 'learn.duckdb'}"
    common = _base_arguments(project_root, database_url, bars)
    ingested = RUNNER.invoke(
        app,
        [
            "strategy",
            "ingest",
            *common,
            "--start",
            "2026-08-20T00:00:00Z",
            "--end",
            "2026-08-22T18:40:00Z",
        ],
    )
    assert ingested.exit_code == 0, ingested.output

    result = RUNNER.invoke(
        app,
        ["strategy", "learn", *common, "--evaluation-budget", "2", "--seed", "17"],
    )

    events = _events(result.stdout)
    assert result.exit_code == 0, result.output
    assert all(set(event) <= {"event", "stage", "progress", "message"} for event in events)
    assert events[-1]["event"] == "complete"
    assert Database.from_url(database_url).scalar("select count(*) from learning_trials") == 2


def test_post_hoc_learning_trials_are_not_admitted_to_the_historical_sealed_boundary(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    bars = tmp_path / "boundary-bars.csv"
    _write_bars(bars, 800)
    database_url = f"duckdb:///{tmp_path / 'boundary.duckdb'}"
    common = _base_arguments(project_root, database_url, bars)
    ingested = RUNNER.invoke(
        app,
        [
            "strategy",
            "ingest",
            *common,
            "--start",
            "2026-08-20T00:00:00Z",
            "--end",
            "2026-08-22T18:40:00Z",
        ],
    )
    learned = RUNNER.invoke(
        app,
        ["strategy", "learn", *common, "--evaluation-budget", "2", "--seed", "17"],
    )
    evaluated = RUNNER.invoke(app, ["strategy", "evaluate", *common, "--mode", "paper"])
    output = tmp_path / "boundary-snapshot.json"
    report = tmp_path / "boundary-report.md"
    exported = RUNNER.invoke(
        app,
        [
            "strategy",
            "export",
            "--project-root",
            str(project_root),
            "--database-url",
            database_url,
            "--output",
            str(output),
            "--report-output",
            str(report),
        ],
    )
    database = Database.from_url(database_url)
    trial_payloads = database.frame("select candidate from learning_trials order by evaluated_at")["candidate"]
    metrics = database.frame("select metrics from strategy_runs where status = 'evaluated'").iloc[0]["metrics"]
    expected_boundary = "2026-08-22T05:20:00+00:00"
    snapshot = AppSnapshot.model_validate_json(output.read_text(encoding="utf-8"))

    assert ingested.exit_code == learned.exit_code == evaluated.exit_code == exported.exit_code == 0
    assert {payload["sealed_final_start"] for payload in trial_payloads} == {expected_boundary}
    assert metrics["final_boundary"] == expected_boundary
    assert metrics["trial_count"] == 0
    assert all(pd.Timestamp(payload["evaluated_at"]) > pd.Timestamp(expected_boundary) for payload in trial_payloads)
    assert snapshot.learning_runs[0].final_boundary.isoformat() == expected_boundary
    assert isinstance(snapshot.learning_runs[0].best_rule, str)


def test_execution_ids_include_strategy_version_interval_and_mode_natural_context(project_root, tmp_path) -> None:
    _configure_strategy(project_root, version="1.0.0")
    bars = tmp_path / "bars.csv"
    _write_bars(bars, 80)
    database_url = f"duckdb:///{tmp_path / 'execution-context.duckdb'}"
    common = _base_arguments(project_root, database_url, bars)
    ingested = RUNNER.invoke(
        app,
        [
            "strategy",
            "ingest",
            *common,
            "--start",
            "2026-08-20T00:00:00Z",
            "--end",
            "2026-08-20T06:40:00Z",
        ],
    )
    first = RUNNER.invoke(app, ["strategy", "evaluate", *common, "--mode", "paper"])
    _configure_strategy(project_root, version="2.0.0")
    second = RUNNER.invoke(app, ["strategy", "evaluate", *common, "--mode", "paper"])

    executions = Database.from_url(database_url).frame(
        "select execution_id, strategy_version, interval, mode from strategy_executions "
        "order by strategy_version, execution_id"
    )
    assert ingested.exit_code == first.exit_code == second.exit_code == 0, second.output
    assert executions["strategy_version"].nunique() == 2
    assert executions["execution_id"].nunique() == len(executions)


def test_plural_strategy_evaluation_persists_ensemble_decision_and_snapshot_provenance(project_root, tmp_path) -> None:
    _configure_plural_strategies(project_root)
    bars = tmp_path / "plural-bars.csv"
    _write_bars(bars, 80)
    database_url = f"duckdb:///{tmp_path / 'plural.duckdb'}"
    common = _base_arguments(project_root, database_url, bars)
    common.extend(["--strategy-id", "extreme_return_reversal"])
    ingested = RUNNER.invoke(
        app,
        [
            "strategy",
            "ingest",
            *common,
            "--start",
            "2026-08-20T00:00:00Z",
            "--end",
            "2026-08-20T06:40:00Z",
        ],
    )
    evaluated = RUNNER.invoke(app, ["strategy", "evaluate", *common, "--mode", "paper"])
    output = tmp_path / "plural-snapshot.json"
    report = tmp_path / "plural-report.md"
    exported = RUNNER.invoke(
        app,
        [
            "strategy",
            "export",
            "--project-root",
            str(project_root),
            "--database-url",
            database_url,
            "--output",
            str(output),
            "--report-output",
            str(report),
        ],
    )
    database = Database.from_url(database_url)
    runs = database.frame("select strategy_id from strategy_runs where status = 'evaluated' order by strategy_id")
    weights = database.frame("select strategy_id, evidence from ensemble_weights order by strategy_id")
    snapshot = AppSnapshot.model_validate_json(output.read_text(encoding="utf-8"))

    assert ingested.exit_code == evaluated.exit_code == exported.exit_code == 0, evaluated.output
    assert runs["strategy_id"].tolist() == ["extreme_return_reversal", "rsi_reversal"]
    assert weights["strategy_id"].tolist() == ["extreme_return_reversal", "rsi_reversal"]
    assert all(item["current_decision"]["decision_hash"] for item in weights["evidence"])
    assert all("contribution" in item for item in weights["evidence"])
    assert [item.strategy_id for item in snapshot.ensemble_components] == [
        "extreme_return_reversal",
        "rsi_reversal",
    ]
    assert all(item.evidence["current_decision"]["decision_hash"] for item in snapshot.ensemble_components)


def test_scalar_component_runs_do_not_satisfy_plural_cohort_cache(project_root, tmp_path) -> None:
    _configure_plural_strategies(project_root)
    bars = tmp_path / "cohort-bars.csv"
    _write_bars(bars, 80)
    database_url = f"duckdb:///{tmp_path / 'cohort.duckdb'}"
    pipeline, database = _csv_pipeline(project_root, database_url, bars)
    plural_scope = _scope("rsi_reversal", "extreme_return_reversal")
    assert pipeline.ingest(_ingest_options(plural_scope)).status == "completed"
    assert pipeline.evaluate(EvaluationOptions(scope=_scope("rsi_reversal"))).status == "completed"
    assert pipeline.evaluate(EvaluationOptions(scope=_scope("extreme_return_reversal"))).status == "completed"

    plural = pipeline.evaluate(EvaluationOptions(scope=plural_scope))
    run_count_after_plural = database.scalar("select count(*) from strategy_runs where status = 'evaluated'")
    weight_count_after_plural = database.scalar("select count(*) from ensemble_weights")
    repeated = pipeline.evaluate(EvaluationOptions(scope=plural_scope))
    snapshot_path = tmp_path / "cohort-snapshot.json"
    pipeline.export(
        ExportOptions(
            snapshot_path=snapshot_path,
            report_path=tmp_path / "cohort-report.md",
        )
    )
    snapshot = AppSnapshot.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
    latest_weights = database.frame(
        "select effective_at, evidence from ensemble_weights order by effective_at desc, strategy_id limit 2"
    )
    plural_runs = database.frame(
        "select metrics from strategy_runs where status = 'evaluated' order by run_timestamp desc limit 2"
    )

    assert plural.status == "completed"
    assert run_count_after_plural == 4
    assert weight_count_after_plural == 4
    assert repeated.status == "reused"
    assert database.scalar("select count(*) from strategy_runs where status = 'evaluated'") == 4
    assert latest_weights["effective_at"].nunique() == 1
    assert len({row["current_decision"]["decision_hash"] for row in latest_weights["evidence"]}) == 1
    assert len({row["cohort_id"] for row in latest_weights["evidence"]}) == 1
    assert all(len(row["cohort_members"]) == 2 for row in plural_runs["metrics"])
    assert len(snapshot.ensemble_components) == 2
    assert len({item.evidence["cohort_id"] for item in snapshot.ensemble_components}) == 1


def test_configured_caps_are_persisted_and_policy_change_invalidates_cohort_cache(project_root, tmp_path) -> None:
    _configure_plural_strategies(project_root, strategy_weight_cap=0.6, family_weight_cap=1.0)
    bars = tmp_path / "configured-cap-bars.csv"
    _write_bars(bars, 80)
    database_url = f"duckdb:///{tmp_path / 'configured-caps.duckdb'}"
    scope = _scope("rsi_reversal", "extreme_return_reversal")
    first_pipeline, database = _csv_pipeline(project_root, database_url, bars)
    assert first_pipeline.ingest(_ingest_options(scope)).status == "completed"
    first = first_pipeline.evaluate(EvaluationOptions(scope=scope))
    first_weights = database.frame("select weight, evidence from ensemble_weights order by strategy_id")

    _configure_plural_strategies(project_root, strategy_weight_cap=0.55, family_weight_cap=1.0)
    second_pipeline, _ = _csv_pipeline(project_root, database_url, bars)
    second = second_pipeline.evaluate(EvaluationOptions(scope=scope))
    all_weights = database.frame("select weight, evidence from ensemble_weights order by effective_at, strategy_id")

    assert first.status == second.status == "completed"
    assert len(first_weights) == 2 and len(all_weights) == 4
    assert all(row["ensemble_config"]["maximum_strategy_weight"] == 0.6 for row in first_weights["evidence"])
    assert all(
        row["ensemble_config"]["family_weight_caps"] == {"mean_reversion": 1.0} for row in first_weights["evidence"]
    )
    assert first_weights["weight"].max() <= 0.6
    assert len({row["cohort_id"] for row in all_weights["evidence"]}) == 2


def test_invalid_configured_family_cap_is_rejected_before_pipeline_creation(project_root) -> None:
    _configure_plural_strategies(project_root, strategy_weight_cap=0.7, family_weight_cap=0.6)

    with pytest.raises(ValueError, match="strategy weight cap"):
        Settings.load(project_root, mode="test")


def test_strategy_export_writes_snapshot_and_cautious_compact_report(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    database_url = f"duckdb:///{tmp_path / 'export.duckdb'}"
    output = tmp_path / "strategy-snapshot.json"
    report = tmp_path / "strategy-report.md"
    bars = tmp_path / "bars.csv"
    _write_bars(bars, 2)
    common = _base_arguments(project_root, database_url, bars)
    ingested = RUNNER.invoke(
        app,
        [
            "strategy",
            "ingest",
            *common,
            "--start",
            "2026-08-20T00:00:00Z",
            "--end",
            "2026-08-20T00:10:00Z",
        ],
    )

    result = RUNNER.invoke(
        app,
        [
            "strategy",
            "export",
            "--project-root",
            str(project_root),
            "--database-url",
            database_url,
            "--output",
            str(output),
            "--report-output",
            str(report),
        ],
    )

    report_text = report.read_text(encoding="utf-8")
    exported = json.loads(output.read_text(encoding="utf-8"))
    assert ingested.exit_code == 0, ingested.output
    assert result.exit_code == 0, result.output
    assert output.exists()
    assert "research/paper-trading aid" in report_text
    assert "Historical evidence is not live proof" in report_text
    assert "abstain" in report_text and "uncertainty" in report_text
    assert "guaranteed profit" not in report_text.lower()
    assert exported["metadata"]["data_mode"] == "strategy_provider_data"
    assert "csv/local" in exported["metadata"]["source_posture"]
    assert "demo_real_snapshot" not in exported["metadata"]["data_mode"]


def _learned_candidate() -> RuleCandidate:
    discovered_at = datetime(2026, 8, 20, 12, tzinfo=UTC)
    return RuleCandidate(
        rule=RuleNode.compare("gt", RuleNode.indicator("rsi", lag=1), RuleNode.number(50.0)),
        discovered_at=discovered_at,
        evidence_through=discovered_at + timedelta(hours=1),
    )


def _forward_evidence(candidate: RuleCandidate, *, promoted: bool = True) -> ForwardEvidence:
    assert candidate.evidence_through is not None
    return ForwardEvidence(
        candidate_hash=candidate.candidate_hash,
        candidate_version=candidate.version,
        period_start=candidate.evidence_through + timedelta(hours=1),
        period_end=candidate.evidence_through + timedelta(hours=2),
        evaluated_at=candidate.evidence_through + timedelta(hours=3),
        causal_audit_passed=True,
        causal_audited_at=candidate.evidence_through + timedelta(hours=2, minutes=30),
        validation=PromotionDecision(promoted, () if promoted else ("drawdown gate failed",)),
        outer_block_inspected=True,
        outer_block_consumed=False,
    )


def _promotion_boundary():
    boundary = getattr(strategy_pipeline, "consume_forward_evidence_and_promote", None)
    assert callable(boundary), "production promotion consumption boundary is missing"
    return boundary


def test_successful_forward_block_is_atomically_consumed_once_with_deterministic_audit(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'promotion-success.duckdb'}")
    database.initialize()
    candidate = _learned_candidate()
    evidence = _forward_evidence(candidate)
    boundary = _promotion_boundary()

    first = boundary(
        database,
        candidate,
        evidence,
        dataset_hash="d" * 64,
        symbol="BTCUSDT",
        interval=BarInterval.FIVE_MINUTES,
        mode=StrategyMode.PAPER,
    )
    repeated = boundary(
        database,
        candidate,
        evidence,
        dataset_hash="d" * 64,
        symbol="BTCUSDT",
        interval=BarInterval.FIVE_MINUTES,
        mode=StrategyMode.FROZEN,
    )
    persisted = database.frame("select audit_id, details from causal_audits")
    expected_id = canonical_hash(
        {
            "kind": "learned_forward_outer_block_consumption_v1",
            "dataset_hash": "d" * 64,
            "strategy_id": candidate.strategy_id,
            "strategy_version": candidate.version,
            "candidate_hash": candidate.candidate_hash,
            "symbol": "BTCUSDT",
            "interval": "5m",
            "period_start": evidence.period_start,
            "period_end": evidence.period_end,
        }
    )

    assert first.promoted is True
    assert repeated == PromotionDecision(False, ("forward outer block has already been consumed",))
    assert persisted["audit_id"].tolist() == [expected_id]
    assert persisted.iloc[0]["details"]["outer_block_consumed"] is True


def test_rejected_forward_block_is_still_consumed_and_cannot_be_reused(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'promotion-rejected.duckdb'}")
    database.initialize()
    candidate = _learned_candidate()
    evidence = _forward_evidence(candidate, promoted=False)
    boundary = _promotion_boundary()

    rejected = boundary(
        database,
        candidate,
        evidence,
        dataset_hash="e" * 64,
        symbol="BTCUSDT",
        interval=BarInterval.FIVE_MINUTES,
    )
    repeated = boundary(
        database,
        candidate,
        replace(evidence, validation=PromotionDecision(True, ())),
        dataset_hash="e" * 64,
        symbol="BTCUSDT",
        interval=BarInterval.FIVE_MINUTES,
    )

    assert rejected == PromotionDecision(False, ("drawdown gate failed",))
    assert repeated == PromotionDecision(False, ("forward outer block has already been consumed",))
    assert database.scalar("select count(*) from causal_audits") == 1


def test_concurrent_forward_block_use_has_one_winner_and_one_durable_consumption(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'promotion-concurrent.duckdb'}")
    database.initialize()
    candidate = _learned_candidate()
    evidence = _forward_evidence(candidate)
    boundary = _promotion_boundary()

    def promote() -> PromotionDecision:
        return boundary(
            database,
            candidate,
            evidence,
            dataset_hash="c" * 64,
            symbol="BTCUSDT",
            interval=BarInterval.FIVE_MINUTES,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        decisions = list(executor.map(lambda _: promote(), range(2)))

    assert sum(decision.promoted for decision in decisions) == 1
    assert sum(any("already been consumed" in reason for reason in decision.reasons) for decision in decisions) == 1
    assert database.scalar("select count(*) from causal_audits") == 1
