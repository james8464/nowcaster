from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import BigInteger, Engine, create_engine, insert, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

from src.database.schema import NATURAL_KEYS, TABLES, metadata, schema_versions

SCHEMA_VERSION = 14


class Database:
    def __init__(self, engine: Engine):
        self.engine = engine

    @classmethod
    def from_url(cls, url: str) -> Database:
        if url.startswith("duckdb:///"):
            database_path = Path(url.removeprefix("duckdb:///"))
            if str(database_path) != ":memory:":
                database_path.parent.mkdir(parents=True, exist_ok=True)
        return cls(create_engine(url, future=True))

    def initialize(self) -> None:
        metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            if connection.dialect.name == "duckdb":
                sequence_type = next(
                    column["type"]
                    for column in inspect(connection).get_columns("live_market_events")
                    if column["name"] == "sequence"
                )
                if not isinstance(sequence_type, BigInteger):
                    # Preserve ledger payloads while accommodating real exchange update IDs.
                    connection.execute(text("ALTER TABLE live_market_events ALTER COLUMN sequence TYPE BIGINT"))
            columns = {column["name"] for column in inspect(connection).get_columns("market_bars")}
            migrations = {
                "source_available_at": "TIMESTAMP WITH TIME ZONE",
                "observed_at": "TIMESTAMP WITH TIME ZONE",
                "vintage_fidelity": "VARCHAR",
                "quote_volume": "DOUBLE",
                "taker_buy_base_volume": "DOUBLE",
                "taker_buy_quote_volume": "DOUBLE",
            }
            for name, sql_type in migrations.items():
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE market_bars ADD COLUMN {name} {sql_type}"))
            connection.execute(
                text("UPDATE market_bars SET source_available_at = available_at WHERE source_available_at IS NULL")
            )
            connection.execute(text("UPDATE market_bars SET observed_at = created_at WHERE observed_at IS NULL"))
            connection.execute(
                text("UPDATE market_bars SET vintage_fidelity = 'unknown_legacy' WHERE vintage_fidelity IS NULL")
            )
            for table_name in ("learning_trials", "deep_research_trials"):
                trial_columns = {column["name"] for column in inspect(connection).get_columns(table_name)}
                if "global_trial_id" not in trial_columns:
                    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN global_trial_id VARCHAR"))
                connection.execute(
                    text(f"UPDATE {table_name} SET global_trial_id = trial_id WHERE global_trial_id IS NULL")
                )
            readiness_columns = {column["name"] for column in inspect(connection).get_columns("readiness_receipts")}
            if "drift_policy_hash" not in readiness_columns:
                connection.execute(text("ALTER TABLE readiness_receipts ADD COLUMN drift_policy_hash VARCHAR"))
            connection.execute(
                text("UPDATE readiness_receipts SET drift_policy_hash = :legacy_hash WHERE drift_policy_hash IS NULL"),
                {"legacy_hash": "0" * 64},
            )
            promotion_columns = {
                column["name"] for column in inspect(connection).get_columns("deep_research_promotions")
            }
            promotion_migrations = {
                "challenger_hash": "VARCHAR",
                "deployment_state": "VARCHAR",
                "shadow_cohort_hash": "VARCHAR",
                "rollback_target_hash": "VARCHAR",
                "forward_evidence_reset": "BOOLEAN",
            }
            for name, sql_type in promotion_migrations.items():
                if name not in promotion_columns:
                    connection.execute(text(f"ALTER TABLE deep_research_promotions ADD COLUMN {name} {sql_type}"))
            connection.execute(
                text(
                    "UPDATE deep_research_promotions SET challenger_hash = candidate_hash WHERE challenger_hash IS NULL"
                )
            )
            connection.execute(
                text(
                    "UPDATE deep_research_promotions SET deployment_state = "
                    "CASE WHEN promoted THEN 'legacy_research_promoted' ELSE 'rejected' END "
                    "WHERE deployment_state IS NULL"
                )
            )
            connection.execute(
                text(
                    "UPDATE deep_research_promotions SET forward_evidence_reset = false "
                    "WHERE forward_evidence_reset IS NULL"
                )
            )
            applied = connection.execute(
                select(schema_versions.c.version).where(schema_versions.c.version == SCHEMA_VERSION)
            ).scalar_one_or_none()
            if applied is None:
                connection.execute(insert(schema_versions).values(version=SCHEMA_VERSION, applied_at=datetime.now(UTC)))

    def schema_version(self) -> int:
        with self.engine.connect() as connection:
            statement = select(schema_versions.c.version).order_by(schema_versions.c.version.desc())
            version = connection.execute(statement).scalar()
        return int(version or 0)

    def table_names(self) -> list[str]:
        return inspect(self.engine).get_table_names()

    def insert(self, table_name: str, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        with self.engine.begin() as connection:
            connection.execute(insert(TABLES[table_name]), list(rows))
        return len(rows)

    def upsert(self, table_name: str, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        table = TABLES[table_name]
        keys = NATURAL_KEYS.get(table_name)
        if keys is None:
            return self.insert(table_name, rows)
        unique_rows: dict[tuple[Any, ...], Mapping[str, Any]] = {}
        for row in rows:
            unique_rows[tuple(row[key] for key in keys)] = row
        candidates = list(unique_rows.values())
        existing: set[tuple[Any, ...]] = set()
        with self.engine.connect() as connection:
            for row in candidates:
                conditions = [table.c[key] == row[key] for key in keys]
                if connection.execute(select(*[table.c[key] for key in keys]).where(*conditions)).first():
                    existing.add(tuple(row[key] for key in keys))
        new_rows = [row for row in candidates if tuple(row[key] for key in keys) not in existing]
        return self.insert(table_name, new_rows)

    def frame(self, statement: str, params: Mapping[str, Any] | None = None) -> pd.DataFrame:
        with self.engine.connect() as connection:
            return pd.read_sql(text(statement), connection, params=params)

    def scalar(self, statement: str, params: Mapping[str, Any] | None = None) -> Any:
        with self.engine.connect() as connection:
            return connection.execute(text(statement), params or {}).scalar()

    def dispose(self) -> None:
        self.engine.dispose()


__all__ = ["Database", "SQLAlchemyError"]
