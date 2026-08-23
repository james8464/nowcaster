from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from src.app_snapshot.builder import build_app_snapshot
from src.app_snapshot.models import AppSnapshot
from src.app_snapshot.writer import write_snapshot_atomic
from src.cli import app
from src.config.settings import Settings
from src.database.engine import Database


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
    assert json.loads(result.output)["schema_version"] == 2
    assert AppSnapshot.model_validate_json(output.read_text()).overview.company_count == 3


def _empty_snapshot_database(tmp_path) -> tuple[Settings, Database]:
    root = Path(__file__).resolve().parents[2]
    database_url = f"duckdb:///{tmp_path / 'snapshot-v2.duckdb'}"
    settings = Settings.load(root, mode="test").model_copy(update={"database_url": database_url})
    database = Database.from_url(database_url)
    database.initialize()
    return settings, database


def test_snapshot_v2_builds_strategy_ensemble_coverage_learning_and_causal_audit_sections(tmp_path) -> None:
    settings, database = _empty_snapshot_database(tmp_path)
    created_at = datetime(2026, 8, 22, 12, tzinfo=UTC)
    dataset_hash = "d" * 64
    for index in (1, 0):
        opened_at = created_at + timedelta(minutes=5 * index)
        database.insert(
            "market_bars",
            [
                {
                    "bar_id": f"bar-{index}",
                    "provider": "binance",
                    "feed": "spot",
                    "symbol": "BTCUSDT",
                    "interval": "5m",
                    "open_timestamp": opened_at,
                    "close_timestamp": opened_at + timedelta(minutes=5),
                    "available_at": opened_at + timedelta(minutes=5),
                    "revision": 1,
                    "finalized": True,
                    "open": 100 + index,
                    "high": 102 + index,
                    "low": 99 + index,
                    "close": 101 + index,
                    "volume": 1_000,
                    "vwap": 100.5 + index,
                    "trade_count": 20,
                    "payload_hash": str(index) * 64,
                    "source": "binance",
                    "source_version": "1",
                    "created_at": created_at,
                }
            ],
        )
    database.insert(
        "strategy_runs",
        [
            {
                "strategy_run_id": "strategy-run-1",
                "dataset_hash": dataset_hash,
                "strategy_id": "rsi_reversal",
                "strategy_version": "1.0.0-abc",
                "family": "mean_reversion",
                "symbol": "BTCUSDT",
                "interval": "5m",
                "mode": "paper",
                "run_timestamp": created_at,
                "parameters": {"period": 14},
                "status": "evaluated",
                "metrics": {
                    "state": "paper",
                    "development_metrics": {"sharpe": 1.25, "dsr_probability": None},
                    "final_test_metrics": {"sharpe": 0.5},
                    "promotion": {"promoted": False, "reasons": ["observed trial Sharpe vector is unavailable"]},
                    "causal_audit_passed": True,
                    "trial_count": 0,
                    "warnings": ["Historical evidence is not live proof"],
                },
                "started_at": created_at,
                "ended_at": created_at + timedelta(minutes=1),
                "source": "strategy_pipeline",
                "source_version": "2",
                "created_at": created_at,
            }
        ],
    )
    database.insert(
        "ensemble_weights",
        [
            {
                "weight_id": "weight-1",
                "strategy_run_id": "strategy-run-1",
                "dataset_hash": dataset_hash,
                "strategy_id": "rsi_reversal",
                "strategy_version": "1.0.0-abc",
                "family": "mean_reversion",
                "symbol": "BTCUSDT",
                "interval": "5m",
                "mode": "paper",
                "effective_at": created_at + timedelta(minutes=1),
                "weight": 0.25,
                "evidence": {"trial_count": 0, "contribution": 0.1},
                "source": "evidence_ensemble",
                "source_version": "1",
                "created_at": created_at,
            }
        ],
    )
    database.insert(
        "learning_trials",
        [
            {
                "trial_id": "trial-2",
                "learning_run_id": "learn-1",
                "candidate_hash": "b" * 64,
                "dataset_hash": dataset_hash,
                "symbol": "BTCUSDT",
                "interval": "5m",
                "mode": "walk_forward_learning",
                "evaluated_at": created_at + timedelta(microseconds=1),
                "candidate": {
                    "ordinal": 1,
                    "rule_text": "volume_zscore[t-1] > 1",
                    "rule": {"operator": "gt"},
                    "fold_count": 2,
                    "state": "shadow",
                },
                "fitness": 0.5,
                "status": "succeeded",
                "error_summary": None,
                "source": "interpretable_learning",
                "source_version": "2",
                "created_at": created_at,
            },
            {
                "trial_id": "trial-1",
                "learning_run_id": "learn-1",
                "candidate_hash": "a" * 64,
                "dataset_hash": dataset_hash,
                "symbol": "BTCUSDT",
                "interval": "5m",
                "mode": "walk_forward_learning",
                "evaluated_at": created_at,
                "candidate": {
                    "ordinal": 0,
                    "rule_text": "rsi[t-1] > 50",
                    "rule": {"operator": "gt"},
                    "fold_count": 2,
                    "state": "shadow",
                },
                "fitness": 0.75,
                "status": "succeeded",
                "error_summary": None,
                "source": "interpretable_learning",
                "source_version": "2",
                "created_at": created_at,
            },
        ],
    )
    database.insert(
        "discovered_rules",
        [
            {
                "rule_id": "rule-1",
                "learning_run_id": "learn-1",
                "rule_hash": "a" * 64,
                "rule_version": "1.0.0+a",
                "dataset_hash": dataset_hash,
                "symbol": "BTCUSDT",
                "interval": "5m",
                "discovered_at": created_at,
                "state": "shadow",
                "rule": {"strategy_id": "learned-a", "plain_language": "rsi[t-1] > 50"},
                "evidence": {
                    "fitness": -0.25,
                    "trial_count": 2,
                    "final_boundary": "2026-08-23T00:00:00Z",
                },
                "source": "interpretable_learning",
                "source_version": "2",
                "created_at": created_at,
            },
            {
                "rule_id": "rule-2",
                "learning_run_id": "learn-1",
                "rule_hash": "b" * 64,
                "rule_version": "1.0.0+b",
                "dataset_hash": dataset_hash,
                "symbol": "BTCUSDT",
                "interval": "5m",
                "discovered_at": created_at + timedelta(microseconds=1),
                "state": "shadow",
                "rule": {"strategy_id": "learned-b", "plain_language": "volume_zscore[t-1] > 1"},
                "evidence": {
                    "fitness": 0.0,
                    "trial_count": 2,
                    "final_boundary": "2026-08-23T00:00:00Z",
                },
                "source": "interpretable_learning",
                "source_version": "2",
                "created_at": created_at,
            },
        ],
    )
    database.insert(
        "causal_audits",
        [
            {
                "audit_id": "audit-1",
                "dataset_hash": dataset_hash,
                "strategy_id": "rsi_reversal",
                "strategy_version": "1.0.0-abc",
                "symbol": "BTCUSDT",
                "interval": "5m",
                "mode": "paper",
                "audited_at": created_at + timedelta(minutes=2),
                "passed": True,
                "details": {"outer_block_consumed": True, "no_repaint": True},
                "source": "strategy_pipeline_promotion_boundary",
                "source_version": "1",
                "created_at": created_at,
            }
        ],
    )

    snapshot = build_app_snapshot(database, settings)

    assert snapshot.schema_version == 2
    assert [(item.strategy_id, item.version, item.weight) for item in snapshot.strategies] == [
        ("rsi_reversal", "1.0.0-abc", 0.25)
    ]
    assert snapshot.strategies[0].development_metrics["sharpe"] == 1.25
    assert snapshot.strategies[0].final_test_metrics["sharpe"] == 0.5
    assert snapshot.strategies[0].no_repaint_badge == "passed"
    assert [(item.strategy_id, item.weight) for item in snapshot.ensemble_components] == [("rsi_reversal", 0.25)]
    assert [(item.provider, item.row_count) for item in snapshot.dataset_coverage] == [("binance", 2)]
    assert snapshot.learning_runs[0].evaluated_candidates == snapshot.learning_runs[0].evaluation_budget == 2
    assert [trial.trial_id for trial in snapshot.learning_runs[0].trials] == ["trial-1", "trial-2"]
    assert snapshot.learning_runs[0].best_rule == "volume_zscore[t-1] > 1"
    assert snapshot.learning_runs[0].best_rule_detail.rule_id == "rule-2"
    assert snapshot.learning_runs[0].final_boundary == datetime(2026, 8, 23, tzinfo=UTC)
    assert [(item.audit_id, item.outer_block_consumed) for item in snapshot.causal_audits] == [("audit-1", True)]


def test_snapshot_rejects_legacy_frozen_online_state(tmp_path) -> None:
    settings, database = _empty_snapshot_database(tmp_path)
    created_at = datetime(2026, 8, 22, 12, tzinfo=UTC)
    database.insert(
        "strategy_runs",
        [
            {
                "strategy_run_id": "legacy-frozen",
                "dataset_hash": "d" * 64,
                "strategy_id": "rsi_reversal",
                "strategy_version": "legacy",
                "family": "mean_reversion",
                "symbol": "BTCUSDT",
                "interval": "5m",
                "mode": "frozen",
                "run_timestamp": created_at,
                "parameters": {},
                "status": "evaluated",
                "metrics": {"online_state": {"weight": 1.0}},
                "started_at": created_at,
                "ended_at": created_at,
                "source": "legacy",
                "source_version": "1",
                "created_at": created_at,
            }
        ],
    )

    with pytest.raises(ValueError, match="regenerated"):
        build_app_snapshot(database, settings)


def test_crypto_backtest_exports_when_equity_event_observations_are_zero(tmp_path) -> None:
    settings, database = _empty_snapshot_database(tmp_path)
    created_at = datetime(2026, 8, 22, 12, tzinfo=UTC)
    database.insert(
        "backtest_runs",
        [
            {
                "backtest_run_id": "crypto-only",
                "strategy_name": "BTC research strategy",
                "symbol": "BTC-USD",
                "asset_class": "crypto",
                "protocol": {"horizon_days": 5, "fee_bps": 10, "slippage_bps": 5},
                "development_metrics": {"sharpe": 0.5},
                "final_test_metrics": {"sharpe": 0.25},
                "full_metrics": {"trades": 3},
                "robustness": {},
                "readiness": "research_only",
                "readiness_score": 0.5,
                "readiness_reasons": ["small sample"],
                "development_start": date(2025, 1, 1),
                "development_end": date(2025, 6, 30),
                "final_test_start": date(2025, 7, 1),
                "final_test_end": date(2025, 8, 1),
                "status": "completed",
                "source": "test",
                "source_version": "1",
                "created_at": created_at,
            }
        ],
    )

    snapshot = build_app_snapshot(database, settings)

    assert [(item.backtest_id, item.asset_class) for item in snapshot.backtests] == [("crypto-only", "crypto")]
