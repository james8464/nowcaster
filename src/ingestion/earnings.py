from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.utils.provenance import canonical_hash

REQUIRED_EARNINGS_COLUMNS = {
    "ticker",
    "fiscal_quarter",
    "earnings_date",
    "timing_confidence",
    "available_date",
    "source",
}


def load_earnings_calendar(path: Path) -> list[dict[str, object]]:
    frame = pd.read_csv(path)
    missing = REQUIRED_EARNINGS_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"Earnings calendar missing columns: {', '.join(sorted(missing))}")
    frame["earnings_date"] = pd.to_datetime(frame["earnings_date"]).dt.date
    frame["available_date"] = pd.to_datetime(frame["available_date"]).dt.date
    if (frame["available_date"] > frame["earnings_date"]).any():
        raise ValueError("Earnings event availability cannot follow the event date")
    if frame.duplicated(["ticker", "fiscal_quarter"]).any():
        raise ValueError("Earnings calendar contains duplicate company-quarters")
    created_at = datetime.now(UTC)
    rows: list[dict[str, object]] = []
    for row in frame.itertuples(index=False):
        rows.append(
            {
                "event_id": canonical_hash([row.ticker, row.fiscal_quarter])[:24],
                "company_id": row.ticker.upper(),
                "fiscal_quarter": row.fiscal_quarter,
                "earnings_date": row.earnings_date,
                "earnings_time": getattr(row, "earnings_time", None),
                "timing_confidence": row.timing_confidence,
                "available_date": row.available_date,
                "source": row.source,
                "source_version": "earnings-calendar-v1",
                "created_at": created_at,
            }
        )
    return rows


def filing_event_proxy_rows(financial_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, object]]:
    """Create a transparent event-date proxy from SEC filing availability dates."""
    created_at = datetime.now(UTC)
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for financial in sorted(financial_rows, key=lambda row: row["available_date"]):
        key = (str(financial["company_id"]), str(financial["fiscal_quarter"]))
        if key in seen:
            continue
        seen.add(key)
        event_date = financial["available_date"]
        rows.append(
            {
                "event_id": canonical_hash(key)[:24],
                "company_id": key[0],
                "fiscal_quarter": key[1],
                "earnings_date": event_date,
                "earnings_time": None,
                "timing_confidence": "sec_filing_date_proxy",
                "available_date": event_date,
                "source": "sec_filing_event_proxy",
                "source_version": "filing-proxy-v1",
                "created_at": created_at,
            }
        )
    return rows
