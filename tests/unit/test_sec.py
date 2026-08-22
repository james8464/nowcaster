from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from src.config.settings import CompanyConfig
from src.ingestion.http import CachedHttpClient
from src.ingestion.sec import SecClient, normalize_company_facts


@pytest.fixture
def sec_payload():
    path = Path(__file__).parents[1] / "fixtures" / "sec" / "companyfacts_sample.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def company():
    return CompanyConfig(ticker="SBUX", cik="829224", name="Starbucks")


def test_normalizer_prefers_standard_revenue_tag_and_filing_availability(sec_payload, company):
    rows = normalize_company_facts(sec_payload, company)

    q1 = next(row for row in rows if row.fiscal_quarter == "2024Q1")
    assert q1.revenue == 9_350_000_000
    assert q1.selected_tag == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert q1.available_date == date(2024, 1, 31)
    assert q1.operating_income == 1_410_000_000
    assert q1.diluted_eps == 0.9


def test_normalizer_derives_standalone_quarters_from_tied_ytd_facts(sec_payload, company):
    rows = normalize_company_facts(sec_payload, company)

    q2 = next(row for row in rows if row.fiscal_quarter == "2024Q2")
    q3 = next(row for row in rows if row.fiscal_quarter == "2024Q3")
    assert q2.revenue == 8_640_000_000
    assert q3.revenue == 9_110_000_000
    assert q2.derivation == "ytd_difference"


def test_normalizer_continues_history_when_issuer_changes_revenue_tag(sec_payload, company):
    standard = sec_payload["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"]
    standard[:] = [fact for fact in standard if fact["fp"] == "Q1"]
    legacy = sec_payload["facts"]["us-gaap"]["SalesRevenueNet"]["units"]["USD"]
    legacy.append(
        {
            "start": "2023-10-02",
            "end": "2024-03-31",
            "val": 17_990_000_000,
            "accn": "tag-transition",
            "fy": 2024,
            "fp": "Q2",
            "form": "10-Q",
            "filed": "2024-05-01",
        }
    )

    rows = normalize_company_facts(sec_payload, company)

    assert next(row for row in rows if row.fiscal_quarter == "2024Q2").selected_tag == "SalesRevenueNet"


def test_normalizer_rejects_wrong_company_payload(sec_payload):
    other = CompanyConfig(ticker="NKE", cik="320187", name="Nike")

    with pytest.raises(ValueError, match="CIK mismatch"):
        normalize_company_facts(sec_payload, other)


def test_sec_client_requires_contactable_user_agent(tmp_path):
    http = CachedHttpClient(tmp_path)

    with pytest.raises(ValueError, match="contact"):
        SecClient(http, "anonymous-script")


@respx.mock
def test_cached_http_uses_saved_response_on_repeat(tmp_path):
    route = respx.get("https://example.test/data").mock(return_value=httpx.Response(200, json={"value": 7}))
    client = CachedHttpClient(tmp_path)

    assert client.get_json("https://example.test/data", cache_key="example") == {"value": 7}
    assert client.get_json("https://example.test/data", cache_key="example") == {"value": 7}
    assert route.call_count == 1
