from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.database.engine import Database


class Repository:
    def __init__(self, database: Database, table_name: str):
        self.database = database
        self.table_name = table_name

    def save(self, rows: Sequence[Mapping[str, Any]]) -> int:
        return self.database.upsert(self.table_name, rows)

    def all(self):
        return self.database.frame(f"select * from {self.table_name}")
