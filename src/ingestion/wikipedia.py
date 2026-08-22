from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote

from src.config.settings import CompanyConfig
from src.ingestion.http import CachedHttpClient, RateLimiter
from src.utils.provenance import canonical_hash


@dataclass(frozen=True)
class AlternativeObservation:
    company_id: str
    signal: str
    observation_date: date
    available_date: date
    value: float
    unit: str
    dimensions: dict[str, Any]

    def __post_init__(self) -> None:
        if self.available_date < self.observation_date:
            raise ValueError("Alternative-data availability cannot precede observation")


def parse_pageviews(
    payload: dict[str, Any],
    *,
    company_id: str,
    availability_lag_days: int = 1,
) -> list[AlternativeObservation]:
    if availability_lag_days < 0:
        raise ValueError("availability_lag_days must be non-negative")
    rows: list[AlternativeObservation] = []
    for item in payload.get("items", []):
        observation_date = datetime.strptime(item["timestamp"][:8], "%Y%m%d").date()
        rows.append(
            AlternativeObservation(
                company_id=company_id.upper(),
                signal="wikipedia_pageviews",
                observation_date=observation_date,
                available_date=observation_date + timedelta(days=availability_lag_days),
                value=float(item["views"]),
                unit="pageviews",
                dimensions={
                    "article": item["article"],
                    "project": item["project"],
                    "access": item["access"],
                    "agent": item["agent"],
                },
            )
        )
    return sorted(rows, key=lambda row: row.observation_date)


class WikipediaProvider:
    def __init__(
        self,
        http: CachedHttpClient,
        *,
        user_agent: str,
        availability_lag_days: int = 1,
        requests_per_second: float = 2,
    ):
        if "@" not in user_agent and "http" not in user_agent:
            raise ValueError("Wikimedia user-agent must contain contact information")
        self.http = http
        self.user_agent = user_agent
        self.availability_lag_days = availability_lag_days
        self.limiter = RateLimiter(requests_per_second)

    def fetch(self, company: CompanyConfig, start: date, end: date) -> list[AlternativeObservation]:
        if not company.wikipedia_article:
            return []
        article = quote(company.wikipedia_article.replace(" ", "_"), safe="_()'-,")
        url = (
            "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
            f"en.wikipedia.org/all-access/user/{article}/daily/{start:%Y%m%d}00/{end:%Y%m%d}00"
        )
        self.limiter.wait()
        payload = self.http.get_json(
            url,
            cache_key=f"wikimedia_{company.ticker}_{start:%Y%m%d}_{end:%Y%m%d}",
            headers={"User-Agent": self.user_agent},
        )
        return parse_pageviews(
            payload,
            company_id=company.ticker,
            availability_lag_days=self.availability_lag_days,
        )


def wikipedia_rows(
    observations: list[AlternativeObservation],
    *,
    source: str = "wikimedia_analytics_api",
) -> list[dict[str, object]]:
    created_at = datetime.now(UTC)
    rows: list[dict[str, object]] = []
    for observation in observations:
        values = asdict(observation)
        values.update(
            {
                "observation_id": canonical_hash(
                    [observation.company_id, observation.signal, observation.observation_date, source]
                )[:24],
                "source": source,
                "source_version": "wikimedia-pageviews-v1",
                "created_at": created_at,
            }
        )
        rows.append(values)
    return rows
