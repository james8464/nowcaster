from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.config.settings import CompanyConfig
from src.database.engine import Database
from src.ingestion.sec import financial_to_row, normalize_company_facts


def test_normalized_sec_financials_persist_idempotently(tmp_path):
    payload_path = Path(__file__).parents[1] / "fixtures" / "sec" / "companyfacts_sample.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    company = CompanyConfig(ticker="SBUX", cik="829224", name="Starbucks")
    database = Database.from_url(f"duckdb:///{tmp_path / 'sec.duckdb'}")
    database.initialize()
    rows = [
        financial_to_row(item, datetime.now(UTC), "sec_test_fixture")
        for item in normalize_company_facts(payload, company)
    ]

    assert database.upsert("financials_quarterly", rows) == 3
    assert database.upsert("financials_quarterly", rows) == 0
    assert database.scalar("select count(*) from financials_quarterly") == 3
