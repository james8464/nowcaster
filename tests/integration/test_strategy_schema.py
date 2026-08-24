from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from src.database.engine import Database

TIMESTAMPED_NATURAL_KEYS = {
    "market_bars": ("provider", "feed", "symbol", "interval", "open_timestamp", "revision", "available_at"),
    "strategy_runs": ("dataset_hash", "strategy_id", "strategy_version", "symbol", "interval", "mode", "run_timestamp"),
    "strategy_signals": (
        "dataset_hash",
        "strategy_id",
        "strategy_version",
        "symbol",
        "interval",
        "mode",
        "decision_timestamp",
    ),
    "ensemble_weights": (
        "dataset_hash",
        "strategy_id",
        "strategy_version",
        "symbol",
        "interval",
        "mode",
        "effective_at",
    ),
    "strategy_executions": (
        "dataset_hash",
        "strategy_id",
        "strategy_version",
        "symbol",
        "interval",
        "mode",
        "decision_timestamp",
        "execution_timestamp",
    ),
    "learning_trials": ("learning_run_id", "candidate_hash", "evaluated_at"),
    "discovered_rules": ("rule_hash", "rule_version", "dataset_hash", "symbol", "interval", "discovered_at"),
    "causal_audits": (
        "dataset_hash",
        "strategy_id",
        "strategy_version",
        "symbol",
        "interval",
        "mode",
        "audited_at",
    ),
}


def _assert_timestamped_strategy_schema(database: Database) -> None:
    inspector = inspect(database.engine)
    assert set(TIMESTAMPED_NATURAL_KEYS) <= set(database.table_names())

    for table_name, expected_key in TIMESTAMPED_NATURAL_KEYS.items():
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        assert set(expected_key) <= columns
        assert any("timestamp" in column or column.endswith("_at") for column in columns)
        constraints = database.frame(
            "SELECT constraint_type, constraint_column_names FROM duckdb_constraints() WHERE table_name = :table_name",
            {"table_name": table_name},
        )
        unique_keys = [
            tuple(columns)
            for constraint_type, columns in constraints.itertuples(index=False)
            if constraint_type == "UNIQUE"
        ]
        assert expected_key in unique_keys


def test_fresh_database_initialization_creates_timestamped_strategy_tables(tmp_path):
    database = Database.from_url(f"duckdb:///{tmp_path / 'fresh.duckdb'}")

    database.initialize()

    _assert_timestamped_strategy_schema(database)
    assert database.schema_version() == 3


def test_legacy_database_migrates_idempotently_without_altering_daily_table(tmp_path):
    database = Database.from_url(f"duckdb:///{tmp_path / 'legacy.duckdb'}")
    with database.engine.begin() as connection:
        connection.execute(text("CREATE TABLE legacy_daily_prices (symbol VARCHAR, trading_date DATE)"))

    database.initialize()
    database.initialize()

    _assert_timestamped_strategy_schema(database)
    assert "legacy_daily_prices" in database.table_names()
    assert database.schema_version() == 3
    assert database.scalar("SELECT count(*) FROM schema_versions WHERE version = 3") == 1


def test_v2_market_bar_table_migrates_observation_provenance_idempotently(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'v2-bars.duckdb'}")
    database.initialize()
    with database.engine.begin() as connection:
        connection.execute(text("DROP TABLE market_bars CASCADE"))
        connection.execute(text("CREATE TABLE market_bars (available_at TIMESTAMPTZ, created_at TIMESTAMPTZ)"))
        connection.execute(text("DELETE FROM schema_versions WHERE version = 3"))
        connection.execute(text("INSERT INTO schema_versions VALUES (2, CURRENT_TIMESTAMP)"))

    database.initialize()
    database.initialize()

    columns = {column["name"] for column in inspect(database.engine).get_columns("market_bars")}
    assert {"source_available_at", "observed_at", "vintage_fidelity"} <= columns
    assert database.schema_version() == 3


def test_ensemble_weights_reject_negative_database_weights(tmp_path):
    database = Database.from_url(f"duckdb:///{tmp_path / 'weights.duckdb'}")
    database.initialize()

    with pytest.raises(IntegrityError):
        database.insert(
            "ensemble_weights",
            [
                {
                    "weight_id": "weight-1",
                    "strategy_run_id": "run-1",
                    "dataset_hash": "dataset-1",
                    "strategy_id": "ema_adx_trend",
                    "strategy_version": "1.0.0",
                    "family": "trend",
                    "symbol": "BTCUSDT",
                    "interval": "5m",
                    "mode": "frozen",
                    "effective_at": "2026-08-22T12:00:00Z",
                    "weight": -0.01,
                    "evidence": {},
                    "source": "test",
                    "source_version": "1",
                    "created_at": "2026-08-22T12:00:00Z",
                }
            ],
        )
