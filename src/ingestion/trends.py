from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from src.config.settings import CompanyConfig
from src.ingestion.wikipedia import AlternativeObservation


class ManualTrendsProvider:
    """Validated import path used because arbitrary Google Trends has no stable unrestricted official API."""

    def __init__(self, path: Path, *, availability_lag_days: int = 1):
        self.path = path
        self.availability_lag_days = availability_lag_days

    def fetch(self, company: CompanyConfig, start: date, end: date) -> list[AlternativeObservation]:
        frame = pd.read_csv(self.path)
        required = {"ticker", "observation_date", "available_date", "search_term", "interest"}
        if missing := required - set(frame.columns):
            raise ValueError(f"Trends CSV missing columns: {', '.join(sorted(missing))}")
        frame["observation_date"] = pd.to_datetime(frame["observation_date"]).dt.date
        frame["available_date"] = pd.to_datetime(frame["available_date"]).dt.date
        selected = frame[
            (frame["ticker"].str.upper() == company.ticker) & frame["observation_date"].between(start, end)
        ]
        return [
            AlternativeObservation(
                company_id=company.ticker,
                signal="search_interest",
                observation_date=row.observation_date,
                available_date=row.available_date,
                value=float(row.interest),
                unit="index_0_100",
                dimensions={"search_term": row.search_term, "provider": "manual_csv"},
            )
            for row in selected.itertuples(index=False)
        ]
