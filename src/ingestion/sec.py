from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any

from src.config.settings import CompanyConfig
from src.ingestion.http import CachedHttpClient, RateLimiter
from src.utils.provenance import canonical_hash

REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "Revenues",
)
METRIC_TAGS: dict[str, tuple[str, ...]] = {
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "diluted_eps": ("EarningsPerShareDiluted",),
    "diluted_shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
}
METRIC_UNITS = {
    "operating_income": ("USD",),
    "net_income": ("USD",),
    "diluted_eps": ("USD/shares", "USD / shares"),
    "diluted_shares": ("shares",),
}


@dataclass(frozen=True)
class QuarterlyFinancial:
    company_id: str
    fiscal_year: int
    fiscal_quarter: str
    period_start: date | None
    period_end: date
    filed_date: date
    available_date: date
    accession: str
    form: str
    selected_tag: str
    revenue: float
    operating_income: float | None = None
    net_income: float | None = None
    diluted_eps: float | None = None
    diluted_shares: float | None = None
    unit: str = "USD"
    amendment: bool = False
    quality_status: str = "valid"
    derivation: str = "reported_quarter"


class SecClient:
    def __init__(
        self,
        http: CachedHttpClient,
        user_agent: str,
        requests_per_second: float = 2.0,
    ):
        if "@" not in user_agent and "http" not in user_agent:
            raise ValueError("SEC user agent must contain contact information")
        self.http = http
        self.user_agent = user_agent
        self.limiter = RateLimiter(requests_per_second)

    def company_facts(self, cik: str, *, refresh: bool = False) -> dict[str, Any]:
        cik10 = str(int(cik)).zfill(10)
        self.limiter.wait()
        return self.http.get_json(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json",
            cache_key=f"sec_companyfacts_{cik10}",
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            refresh=refresh,
        )

    def submissions(self, cik: str, *, refresh: bool = False) -> dict[str, Any]:
        cik10 = str(int(cik)).zfill(10)
        self.limiter.wait()
        return self.http.get_json(
            f"https://data.sec.gov/submissions/CIK{cik10}.json",
            cache_key=f"sec_submissions_{cik10}",
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            refresh=refresh,
        )


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _duration_days(fact: dict[str, Any]) -> int | None:
    start = _parse_date(fact.get("start"))
    end = _parse_date(fact.get("end"))
    return (end - start).days if start and end else None


def _facts_for_tag(payload: dict[str, Any], tag: str, allowed_units: tuple[str, ...]) -> list[dict[str, Any]]:
    tag_payload = payload.get("facts", {}).get("us-gaap", {}).get(tag, {})
    units = tag_payload.get("units", {})
    result: list[dict[str, Any]] = []
    for unit in allowed_units:
        for fact in units.get(unit, []):
            if fact.get("form") in {"10-Q", "10-Q/A", "10-K", "10-K/A"} and fact.get("fp") in {
                "Q1",
                "Q2",
                "Q3",
                "FY",
            }:
                result.append({**fact, "_tag": tag, "_unit": unit})
    return result


def _revenue_facts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    facts = [fact for tag in REVENUE_TAGS for fact in _facts_for_tag(payload, tag, ("USD",))]
    if facts:
        return facts
    raise ValueError("No supported quarterly revenue tag found")


def _best_fact(facts: list[dict[str, Any]], fiscal_year: int, fiscal_period: str) -> dict[str, Any] | None:
    candidates = [fact for fact in facts if int(fact.get("fy", -1)) == fiscal_year and fact.get("fp") == fiscal_period]
    if not candidates:
        return None
    direct = [fact for fact in candidates if (_duration_days(fact) or 999) <= 120 or fact.get("frame")]
    pool = direct or candidates
    return sorted(pool, key=lambda fact: (fact.get("filed", ""), fact.get("accn", "")))[-1]


def _best_revenue_fact(facts: list[dict[str, Any]], fiscal_year: int, fiscal_period: str) -> dict[str, Any] | None:
    for tag in REVENUE_TAGS:
        selected = _best_fact([fact for fact in facts if fact["_tag"] == tag], fiscal_year, fiscal_period)
        if selected is not None:
            return selected
    return None


def _metric_value(payload: dict[str, Any], metric: str, fiscal_year: int, fiscal_period: str) -> float | None:
    for tag in METRIC_TAGS[metric]:
        fact = _best_fact(_facts_for_tag(payload, tag, METRIC_UNITS[metric]), fiscal_year, fiscal_period)
        if fact is not None:
            duration = _duration_days(fact)
            if duration is None or duration <= 120 or fact.get("frame"):
                return float(fact["val"])
    return None


def normalize_company_facts(payload: dict[str, Any], company: CompanyConfig) -> list[QuarterlyFinancial]:
    payload_cik = str(payload.get("cik", "")).zfill(10)
    if payload_cik != company.cik:
        raise ValueError(f"CIK mismatch: expected {company.cik}, received {payload_cik}")

    revenue_facts = _revenue_facts(payload)
    years = sorted({int(fact["fy"]) for fact in revenue_facts if fact.get("fy")})
    output: list[QuarterlyFinancial] = []
    for fiscal_year in years:
        cumulative_revenue = 0.0
        for quarter_number, period in enumerate(("Q1", "Q2", "Q3"), start=1):
            fact = _best_revenue_fact(revenue_facts, fiscal_year, period)
            if fact is None:
                continue
            reported = float(fact["val"])
            duration = _duration_days(fact)
            if quarter_number > 1 and duration is not None and duration > 120 and not fact.get("frame"):
                revenue = reported - cumulative_revenue
                derivation = "ytd_difference"
            else:
                revenue = reported
                derivation = "reported_quarter"
            if revenue <= 0:
                continue
            cumulative_revenue += revenue
            filed = date.fromisoformat(fact["filed"])
            output.append(
                QuarterlyFinancial(
                    company_id=company.ticker,
                    fiscal_year=fiscal_year,
                    fiscal_quarter=f"{fiscal_year}Q{quarter_number}",
                    period_start=_parse_date(fact.get("start")),
                    period_end=date.fromisoformat(fact["end"]),
                    filed_date=filed,
                    available_date=filed,
                    accession=fact["accn"],
                    form=fact["form"],
                    selected_tag=fact["_tag"],
                    revenue=revenue,
                    operating_income=_metric_value(payload, "operating_income", fiscal_year, period),
                    net_income=_metric_value(payload, "net_income", fiscal_year, period),
                    diluted_eps=_metric_value(payload, "diluted_eps", fiscal_year, period),
                    diluted_shares=_metric_value(payload, "diluted_shares", fiscal_year, period),
                    amendment=fact["form"].endswith("/A"),
                    derivation=derivation,
                )
            )
    return sorted(output, key=lambda row: (row.fiscal_year, row.fiscal_quarter))


def financial_to_row(
    financial: QuarterlyFinancial, created_at: datetime | None = None, source: str = "sec_edgar"
) -> dict[str, Any]:
    values = asdict(financial)
    derivation = values.pop("derivation")
    values.update(
        {
            "financial_id": canonical_hash([financial.company_id, financial.fiscal_quarter, financial.accession])[:24],
            "quality_status": f"{financial.quality_status}:{derivation}",
            "source": source,
            "source_version": "sec-companyfacts-v1",
            "created_at": created_at or datetime.now(UTC),
        }
    )
    return values
