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


def test_snapshot_does_not_fallback_when_terminal_aggregate_coverage_is_stale(tmp_path) -> None:
    settings, database = _empty_snapshot_database(tmp_path)
    created_at = datetime(2026, 8, 23, 12, tzinfo=UTC)
    database.insert(
        "strategy_runs",
        [
            {
                "strategy_run_id": "stale-xnys-run",
                "dataset_hash": "o" * 64,
                "strategy_id": "rsi_reversal",
                "strategy_version": "1",
                "family": "mean_reversion",
                "symbol": "AAPL",
                "interval": "5m",
                "mode": "paper",
                "run_timestamp": created_at,
                "parameters": {},
                "status": "evaluated",
                "metrics": {
                    "coverage_manifest": {
                        "schema_version": 1,
                        "dataset_hash": "o" * 64,
                        "provider": "alpaca",
                        "feed": "iex",
                        "symbol": "AAPL",
                        "interval": "5m",
                        "requested_start": "2026-08-21T13:30:00Z",
                        "requested_end": "2026-08-21T20:00:00Z",
                        "coverage_start": "2026-08-21T13:30:00Z",
                        "coverage_end": "2026-08-21T20:00:00Z",
                        "row_count": 78,
                        "gaps": [],
                        "calendar_id": "XNYS",
                        "calendar_version": "offline-rules-2026.2",
                        "contributing_requests": [],
                    }
                },
                "started_at": created_at,
                "ended_at": created_at,
                "source": "strategy_pipeline",
                "source_version": "2",
                "created_at": created_at,
            }
        ],
    )
    database.insert(
        "dataset_coverage_requests",
        [
            {
                "coverage_request_id": "current-never-evaluated",
                "provider": "alpaca",
                "feed": "iex",
                "symbol": "AAPL",
                "interval": "5m",
                "requested_start": created_at,
                "requested_end": created_at + timedelta(hours=6, minutes=30),
                "requested_at": created_at,
                "force": False,
                "status": "complete",
                "dataset_hash": "n" * 64,
                "row_count": 78,
                "gaps": {
                    "calendar_id": "XNYS",
                    "calendar_version": "offline-rules-2026.3",
                    "missing": [],
                },
                "source": "strategy_pipeline_coverage",
                "source_version": "2",
                "created_at": created_at,
            }
        ],
    )
    database.insert(
        "strategy_runs",
        [
            {
                "strategy_run_id": "malformed-xnys-run",
                "dataset_hash": "m" * 64,
                "strategy_id": "rsi_reversal",
                "strategy_version": "2",
                "family": "mean_reversion",
                "symbol": "AAPL",
                "interval": "5m",
                "mode": "paper",
                "run_timestamp": created_at + timedelta(microseconds=1),
                "parameters": {},
                "status": "failed",
                "metrics": {
                    "coverage_manifest": {
                        "schema_version": 1,
                        "dataset_hash": "m" * 64,
                        "provider": "alpaca",
                        "feed": "iex",
                        "symbol": "AAPL",
                        "interval": "5m",
                        "requested_start": "not-a-datetime",
                        "requested_end": "2026-08-21T20:00:00Z",
                        "row_count": 78,
                        "gaps": [],
                        "calendar_id": "XNYS",
                        "calendar_version": "offline-rules-2026.3",
                    }
                },
                "started_at": created_at,
                "ended_at": created_at,
                "source": "strategy_pipeline",
                "source_version": "2",
                "created_at": created_at,
            }
        ],
    )
    database.insert(
        "strategy_runs",
        [
            {
                "strategy_run_id": "malformed-gaps-xnys-run",
                "dataset_hash": "g" * 64,
                "strategy_id": "rsi_reversal",
                "strategy_version": "3",
                "family": "mean_reversion",
                "symbol": "AAPL",
                "interval": "5m",
                "mode": "paper",
                "run_timestamp": created_at + timedelta(microseconds=2),
                "parameters": {},
                "status": "failed",
                "metrics": {
                    "coverage_manifest": {
                        "schema_version": 1,
                        "dataset_hash": "g" * 64,
                        "provider": "alpaca",
                        "feed": "iex",
                        "symbol": "AAPL",
                        "interval": "5m",
                        "requested_start": "2026-08-21T13:30:00Z",
                        "requested_end": "2026-08-21T20:00:00Z",
                        "coverage_start": "2026-08-21T13:30:00Z",
                        "coverage_end": "2026-08-21T20:00:00Z",
                        "row_count": 78,
                        "gaps": ["malformed"],
                        "calendar_id": "XNYS",
                        "calendar_version": "offline-rules-2026.3",
                    }
                },
                "started_at": created_at,
                "ended_at": created_at,
                "source": "strategy_pipeline",
                "source_version": "2",
                "created_at": created_at,
            }
        ],
    )

    snapshot = build_app_snapshot(database, settings)

    assert snapshot.dataset_coverage == []


def test_snapshot_uses_one_bounded_coverage_projection_and_summarizes_ensemble_contributors(tmp_path) -> None:
    settings, database = _empty_snapshot_database(tmp_path)
    created_at = datetime(2026, 8, 23, 12, tzinfo=UTC)
    dataset_hash = "d" * 64

    def coverage_manifest(contributors):
        return {
            "schema_version": 1,
            "dataset_hash": dataset_hash,
            "provider": "binance",
            "feed": "spot",
            "symbol": "BTCUSDT",
            "interval": "5m",
            "requested_start": "2026-08-20T00:00:00Z",
            "requested_end": "2026-08-20T06:40:00Z",
            "coverage_start": "2026-08-20T00:00:00Z",
            "coverage_end": "2026-08-20T06:40:00Z",
            "row_count": 80,
            "gaps": [],
            "calendar_id": "24x7",
            "calendar_version": "continuous-v1",
            "contributing_requests": contributors,
        }

    runs = []
    for index in range(205):
        run_at = created_at + timedelta(microseconds=index)
        runs.append(
            {
                "strategy_run_id": f"refresh-run-{index:03d}",
                "dataset_hash": dataset_hash,
                "strategy_id": "rsi_reversal",
                "strategy_version": "1",
                "family": "mean_reversion",
                "symbol": "BTCUSDT",
                "interval": "5m",
                "mode": "paper",
                "run_timestamp": run_at,
                "parameters": {},
                "status": "failed",
                "metrics": {
                    "coverage_manifest": coverage_manifest(
                        [
                            {
                                "coverage_request_id": f"refresh-{index:03d}",
                                "dataset_hash": dataset_hash,
                            }
                        ]
                    )
                },
                "started_at": run_at,
                "ended_at": run_at,
                "source": "strategy_pipeline",
                "source_version": "2",
                "created_at": run_at,
            }
        )
    database.insert("strategy_runs", runs)
    all_contributors = [
        {"coverage_request_id": f"refresh-{index:03d}", "dataset_hash": dataset_hash} for index in range(205)
    ]
    database.insert(
        "ensemble_weights",
        [
            {
                "weight_id": "bounded-ensemble-weight",
                "strategy_run_id": "refresh-run-204",
                "dataset_hash": dataset_hash,
                "strategy_id": "rsi_reversal",
                "strategy_version": "1",
                "family": "mean_reversion",
                "symbol": "BTCUSDT",
                "interval": "5m",
                "mode": "paper",
                "effective_at": created_at + timedelta(seconds=1),
                "weight": 0.5,
                "evidence": {
                    "contribution": 0.0,
                    "coverage_manifest": coverage_manifest(all_contributors),
                },
                "source": "evidence_ensemble",
                "source_version": "1",
                "created_at": created_at,
            }
        ],
    )

    snapshot = build_app_snapshot(database, settings)
    ensemble_manifest = snapshot.ensemble_components[0].evidence["coverage_manifest"]

    assert len(snapshot.dataset_coverage) == 1
    assert snapshot.dataset_coverage[0].dataset_hash == dataset_hash
    assert "contributing_requests" not in ensemble_manifest
    assert ensemble_manifest["contributing_request_count"] == 205
    assert len(ensemble_manifest["contributing_requests_hash"]) == 64
    assert len(json.dumps(ensemble_manifest, sort_keys=True)) < 2_000


def test_snapshot_replaces_malformed_ensemble_coverage_manifest_with_bounded_summary(tmp_path) -> None:
    settings, database = _empty_snapshot_database(tmp_path)
    created_at = datetime(2026, 8, 23, 12, tzinfo=UTC)
    database.insert(
        "ensemble_weights",
        [
            {
                "weight_id": "malformed-ensemble-weight",
                "strategy_run_id": "malformed-ensemble-run",
                "dataset_hash": "d" * 64,
                "strategy_id": "rsi_reversal",
                "strategy_version": "1",
                "family": "mean_reversion",
                "symbol": "BTCUSDT",
                "interval": "5m",
                "mode": "paper",
                "effective_at": created_at,
                "weight": 0.5,
                "evidence": {
                    "contribution": 0.0,
                    "coverage_manifest": ["licensed-raw-entry"] * 10_000,
                },
                "source": "evidence_ensemble",
                "source_version": "1",
                "created_at": created_at,
            }
        ],
    )

    snapshot = build_app_snapshot(database, settings)
    evidence = snapshot.ensemble_components[0].evidence

    assert evidence["coverage_manifest"] == {
        "reason": "malformed_coverage_manifest",
        "status": "unavailable",
    }
    encoded = json.dumps(evidence, sort_keys=True)
    assert "licensed-raw-entry" not in encoded
    assert len(encoded) < 500


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


def test_snapshot_searches_past_more_than_one_thousand_incomplete_cohorts(tmp_path) -> None:
    settings, database = _empty_snapshot_database(tmp_path)
    created_at = datetime(2026, 8, 22, 12, tzinfo=UTC)
    members = [
        {"strategy_id": "older-a", "strategy_version": "1", "family": "mean_reversion"},
        {"strategy_id": "older-b", "strategy_version": "1", "family": "mean_reversion"},
    ]
    complete_evidence = {
        "cohort_id": "complete-older",
        "cohort_members": members,
        "cohort_decision_hash": "complete-decision",
        "cohort_generation": 1,
    }
    rows = [
        {
            "weight_id": f"complete-{strategy_id}",
            "strategy_run_id": f"run-{strategy_id}",
            "dataset_hash": "d" * 64,
            "strategy_id": strategy_id,
            "strategy_version": "1",
            "family": "mean_reversion",
            "symbol": "BTCUSDT",
            "interval": "5m",
            "mode": "paper",
            "effective_at": created_at,
            "weight": 0.5,
            "evidence": complete_evidence,
            "source": "test",
            "source_version": "1",
            "created_at": created_at,
        }
        for strategy_id in ("older-a", "older-b")
    ]
    for index in range(1_001):
        effective_at = created_at + timedelta(microseconds=index + 1)
        rows.append(
            {
                "weight_id": f"incomplete-{index}",
                "strategy_run_id": f"incomplete-run-{index}",
                "dataset_hash": "d" * 64,
                "strategy_id": "aaa-incomplete",
                "strategy_version": "1",
                "family": "mean_reversion",
                "symbol": "BTCUSDT",
                "interval": "5m",
                "mode": "paper",
                "effective_at": effective_at,
                "weight": 0.5,
                "evidence": {
                    "cohort_id": f"incomplete-{index}",
                    "cohort_members": members,
                    "cohort_decision_hash": f"incomplete-decision-{index}",
                    "cohort_generation": index + 2,
                },
                "source": "test",
                "source_version": "1",
                "created_at": effective_at,
            }
        )
    database.insert("ensemble_weights", rows)

    snapshot = build_app_snapshot(database, settings)

    assert [(item.strategy_id, item.weight) for item in snapshot.ensemble_components] == [
        ("older-a", 0.5),
        ("older-b", 0.5),
    ]
    assert {item.evidence["cohort_id"] for item in snapshot.ensemble_components} == {"complete-older"}
    assert {item.evidence["cohort_decision_hash"] for item in snapshot.ensemble_components} == {"complete-decision"}


def test_snapshot_complete_cohort_tie_is_independent_of_insertion_order(tmp_path) -> None:
    created_at = datetime(2026, 8, 22, 12, tzinfo=UTC)
    members = [
        {"strategy_id": "alpha", "strategy_version": "1", "family": "mean_reversion"},
        {"strategy_id": "beta", "strategy_version": "1", "family": "mean_reversion"},
    ]

    def cohort_rows(dataset_hash: str, cohort_id: str, decision_hash: str) -> list[dict[str, object]]:
        evidence = {
            "cohort_id": cohort_id,
            "cohort_members": members,
            "cohort_decision_hash": decision_hash,
            "cohort_generation": 1,
        }
        return [
            {
                "weight_id": f"{cohort_id}-{strategy_id}",
                "strategy_run_id": f"{cohort_id}-run-{strategy_id}",
                "dataset_hash": dataset_hash,
                "strategy_id": strategy_id,
                "strategy_version": "1",
                "family": "mean_reversion",
                "symbol": "BTCUSDT",
                "interval": "5m",
                "mode": "paper",
                "effective_at": created_at,
                "weight": 0.5,
                "evidence": evidence,
                "source": "test",
                "source_version": "1",
                "created_at": created_at,
            }
            for strategy_id in ("alpha", "beta")
        ]

    selected: list[tuple[tuple[str, str], ...]] = []
    alpha = cohort_rows("a" * 64, "cohort-alpha", "decision-alpha")
    omega = cohort_rows("f" * 64, "cohort-omega", "decision-omega")
    for index, rows in enumerate((omega + alpha, alpha + omega)):
        settings, database = _empty_snapshot_database(tmp_path / f"tie-{index}")
        database.insert("ensemble_weights", rows)
        snapshot = build_app_snapshot(database, settings)
        selected.append(
            tuple(
                (item.evidence["cohort_id"], item.evidence["cohort_decision_hash"])
                for item in snapshot.ensemble_components
            )
        )

    assert selected == [
        (("cohort-alpha", "decision-alpha"), ("cohort-alpha", "decision-alpha")),
        (("cohort-alpha", "decision-alpha"), ("cohort-alpha", "decision-alpha")),
    ]


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


def test_strategy_snapshot_joins_weight_and_audit_to_its_exact_cohort(tmp_path) -> None:
    settings, database = _empty_snapshot_database(tmp_path)
    created_at = datetime(2026, 8, 22, 12, tzinfo=UTC)
    dataset_hash = "d" * 64

    def run(run_id: str, cohort_id: str, at: datetime) -> dict[str, object]:
        return {
            "strategy_run_id": run_id,
            "dataset_hash": dataset_hash,
            "strategy_id": "rsi_reversal",
            "strategy_version": "1",
            "family": "mean_reversion",
            "symbol": "BTCUSDT",
            "interval": "5m",
            "mode": "paper",
            "run_timestamp": at,
            "parameters": {},
            "status": "evaluated",
            "metrics": {
                "cohort_id": cohort_id,
                "development_metrics": {"sharpe": 1.0},
                "final_test_metrics": {},
            },
            "started_at": at,
            "ended_at": at,
            "source": "test",
            "source_version": "2",
            "created_at": at,
        }

    database.insert(
        "strategy_runs",
        [
            run("run-a", "cohort-a", created_at),
            run("run-b", "cohort-b", created_at + timedelta(minutes=1)),
        ],
    )
    member = [{"strategy_id": "rsi_reversal", "strategy_version": "1"}]
    database.insert(
        "ensemble_weights",
        [
            {
                "weight_id": "weight-b",
                "strategy_run_id": "run-b",
                "dataset_hash": dataset_hash,
                "strategy_id": "rsi_reversal",
                "strategy_version": "1",
                "family": "mean_reversion",
                "symbol": "BTCUSDT",
                "interval": "5m",
                "mode": "paper",
                "effective_at": created_at + timedelta(minutes=1),
                "weight": 0.2,
                "evidence": {
                    "cohort_id": "cohort-b",
                    "cohort_members": member,
                    "cohort_decision_hash": "decision-b",
                },
                "source": "test",
                "source_version": "1",
                "created_at": created_at,
            },
            {
                "weight_id": "weight-a-late",
                "strategy_run_id": "run-a",
                "dataset_hash": dataset_hash,
                "strategy_id": "rsi_reversal",
                "strategy_version": "1",
                "family": "mean_reversion",
                "symbol": "BTCUSDT",
                "interval": "5m",
                "mode": "paper",
                "effective_at": created_at + timedelta(minutes=2),
                "weight": 0.9,
                "evidence": {
                    "cohort_id": "cohort-a",
                    "cohort_members": member,
                    "cohort_decision_hash": "decision-a",
                },
                "source": "test",
                "source_version": "1",
                "created_at": created_at,
            },
        ],
    )
    database.insert(
        "causal_audits",
        [
            {
                "audit_id": "audit-b",
                "dataset_hash": dataset_hash,
                "strategy_id": "rsi_reversal",
                "strategy_version": "1",
                "symbol": "BTCUSDT",
                "interval": "5m",
                "mode": "paper",
                "audited_at": created_at + timedelta(minutes=1),
                "passed": True,
                "details": {"cohort_id": "cohort-b"},
                "source": "test",
                "source_version": "1",
                "created_at": created_at,
            },
            {
                "audit_id": "audit-a-late",
                "dataset_hash": dataset_hash,
                "strategy_id": "rsi_reversal",
                "strategy_version": "1",
                "symbol": "BTCUSDT",
                "interval": "5m",
                "mode": "frozen",
                "audited_at": created_at + timedelta(minutes=2),
                "passed": False,
                "details": {"cohort_id": "cohort-a"},
                "source": "test",
                "source_version": "1",
                "created_at": created_at,
            },
        ],
    )

    snapshot = build_app_snapshot(database, settings)
    strategy = snapshot.strategies[0]
    assert strategy.dataset_hash == dataset_hash
    assert strategy.mode == "paper"
    assert strategy.cohort_id == "cohort-b"
    assert strategy.weight == pytest.approx(0.2)
    assert strategy.causal_audit_passed is True
    assert strategy.no_repaint_badge == "passed"
    assert snapshot.ensemble_components[0].dataset_hash == dataset_hash
    assert snapshot.ensemble_components[0].cohort_id == "cohort-a"
