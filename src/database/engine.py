from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Engine, create_engine, insert, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

from src.database.schema import NATURAL_KEYS, TABLES, metadata, schema_versions

SCHEMA_VERSION = 2


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
