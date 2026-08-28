from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from src.database.engine import Database


def company_row():
    return {
        "company_id": "SBUX",
        "ticker": "SBUX",
        "cik": "0000829224",
        "name": "Starbucks Corporation",
        "sector": "Consumer Discretionary",
        "sector_etf": "XLY",
        "fiscal_year_end_month": 9,
        "active": True,
        "created_at": datetime(2026, 8, 22, tzinfo=UTC),
    }


def test_database_initialization_creates_required_tables(tmp_path):
    database = Database.from_url(f"duckdb:///{tmp_path / 'test.duckdb'}")

    database.initialize()

    names = set(database.table_names())
    assert {"companies", "financials_quarterly", "features_quarterly", "forecasts", "backtest_results"} <= names
    bar_columns = {column["name"] for column in inspect(database.engine).get_columns("market_bars")}
    assert {"quote_volume", "taker_buy_base_volume", "taker_buy_quote_volume"} <= bar_columns
    assert database.schema_version() == 7


def test_upsert_is_idempotent_for_natural_key(tmp_path):
    database = Database.from_url(f"duckdb:///{tmp_path / 'test.duckdb'}")
    database.initialize()

    assert database.upsert("companies", [company_row()]) == 1
    assert database.upsert("companies", [company_row()]) == 0
    assert database.scalar("select count(*) from companies") == 1


def test_database_rejects_duplicate_company_natural_key(tmp_path):
    database = Database.from_url(f"duckdb:///{tmp_path / 'test.duckdb'}")
    database.initialize()
    database.insert("companies", [company_row()])

    try:
        database.insert("companies", [company_row()])
    except IntegrityError:
        return
    raise AssertionError("duplicate company was accepted")
