from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.app_snapshot.models import AppSnapshot
from src.config.settings import Settings
from src.database.engine import Database
from src.strategies.pipeline import (
    BarProviderName,
    DeepResearchOptions,
    ExportOptions,
    IngestOptions,
    StrategyScope,
    create_strategy_pipeline,
)
from src.strategies.types import BarInterval, StrategyMode


def _write_strategy_config(project_root: Path) -> None:
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


def _write_bars(path: Path, count: int = BAR_COUNT) -> None:
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


def test_deep_research_full_evidence_export_is_honest_reproducible_and_broker_isolated(
    project_root: Path,
    tmp_path: Path,
) -> None:
    _write_strategy_config(project_root)
    csv_path = tmp_path / "bars.csv"
    _write_bars(csv_path)
    database = Database.from_url(f"duckdb:///{tmp_path / 'deep-e2e.duckdb'}")
    loaded = Settings.load(project_root, mode="test")
    settings = loaded.model_copy(
        update={
            "database_url": str(database.engine.url),
            "deep_research": loaded.deep_research.model_copy(update={"default_cycle_budget": 4}),
        }
    )
    pipeline = create_strategy_pipeline(settings, database, csv_path=csv_path)
    scope = StrategyScope(
        strategy_ids=("rsi_reversal",),
        provider=BarProviderName.CSV,
        feed="local",
        symbol="BTCUSDT",
        interval=BarInterval.FIVE_MINUTES,
        mode=StrategyMode.WALK_FORWARD_LEARNING,
    )
    start = datetime(2026, 8, 20, tzinfo=UTC)
    pipeline.ingest(IngestOptions(scope=scope, start=start, end=start + timedelta(minutes=5 * BAR_COUNT)))

    research = pipeline.deep_research(
        DeepResearchOptions(
            scope=scope,
            workers=4,
            evaluation_budget=4,
            seed=17,
            run_id="deep-e2e",
            control_directory=tmp_path / "control",
            control_nonce="e" * 32,
        )
    )
    snapshot_path = tmp_path / "nowcaster-snapshot.json"
    report_path = tmp_path / "deep-research-report.md"
    pipeline.export(ExportOptions(snapshot_path=snapshot_path, report_path=report_path))

    snapshot = AppSnapshot.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
    run = snapshot.deep_research_runs[0]
    assert snapshot.schema_version == 5
    assert research.evaluated_candidates == run.evaluated_attempts == 4
    assert run.outcome in {
        "no_reliable_strategy_found",
        "research_champion_found",
        "shadow_cohort_started",
        "existing_champion_retained",
    }
    if run.outcome == "no_reliable_strategy_found":
        assert run.failed_gates
    assert database.scalar("select count(*) from deep_research_trials") == 4
    assert database.scalar("select count(*) from deep_research_checkpoints") >= 2
    assert database.scalar("select count(*) from deep_research_promotions") == 1
    assert database.scalar("select count(*) from broker_order_intents") == 0
    assert database.scalar("select count(*) from broker_orders") == 0
    serialized = snapshot_path.read_text(encoding="utf-8")
    assert "control_nonce" not in serialized
    assert str(tmp_path / "control") not in serialized
    report = report_path.read_text(encoding="utf-8")
    assert "Hypothetical research result" in report
    assert "does not promise profit" in report
