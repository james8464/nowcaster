from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from src.cli import app
from src.database.engine import Database
from src.learning.grammar import RuleNode
from src.learning.promotion import ForwardEvidence
from src.learning.search import RuleCandidate
from src.pipeline import PipelineSummary
from src.strategies import pipeline as strategy_pipeline
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


def test_strategy_cli_is_nested_without_removing_legacy_earnings_commands() -> None:
    root_help = RUNNER.invoke(app, ["--help"])
    strategy_help = RUNNER.invoke(app, ["strategy", "--help"])

    assert root_help.exit_code == strategy_help.exit_code == 0
    assert {"demo", "fetch-fundamentals", "train", "backtest", "report", "export-app-snapshot"} <= {
        name for name in root_help.output.split() if name
    }
    assert {"ingest", "evaluate", "learn", "export"} <= {name for name in strategy_help.output.split() if name}


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


def test_strategy_learning_uses_observed_bounded_trial_ledger_and_jsonl_progress(project_root, tmp_path) -> None:
    _configure_strategy(project_root)
    bars = tmp_path / "bars.csv"
    _write_bars(bars, 100)
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
            "2026-08-20T08:20:00Z",
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
