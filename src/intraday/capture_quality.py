"""Observed account-stream quality, not strategy eligibility or fill proof."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from math import ceil

from pydantic import BaseModel, ConfigDict, Field

from src.intraday.contracts import InstrumentSpec


class CaptureQuality(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    broker_symbol: str
    observed_from: datetime
    observed_until: datetime
    expected_intervals: int = Field(ge=0)
    covered_intervals: int = Field(ge=0)
    quote_count: int = Field(ge=0)
    tradeable_quote_count: int = Field(ge=0)
    invalid_quote_count: int = Field(ge=0)
    coverage: Decimal = Field(ge=0, le=1)
    median_spread: Decimal | None = None
    p95_spread: Decimal | None = None
    max_quote_gap_seconds: Decimal | None = None
    price_scope: str = "account_stream_observation"
    paper_eligible: bool = False


def summarize_capture(
    instrument: InstrumentSpec,
    opened_at: datetime,
    as_of: datetime,
    events: tuple[dict, ...],
) -> CaptureQuality:
    """Count completed five-minute intervals with any valid tradeable account quote.

    This is deliberately descriptive. It never promotes a product to paper trading;
    historical depth, full broker terms, conversion and a frozen rule are separate gates.
    """
    if (
        opened_at.tzinfo is None
        or as_of.tzinfo is None
        or opened_at.utcoffset() != timedelta(0)
        or as_of.utcoffset() != timedelta(0)
        or as_of < opened_at
    ):
        raise ValueError("capture window must be chronological UTC")
    interval = timedelta(minutes=5)
    expected = int((as_of - opened_at) // interval)
    covered: set[int] = set()
    spreads: list[Decimal] = []
    received_times: list[datetime] = []
    quote_count = tradeable_count = invalid_count = 0
    for event in events:
        if event.get("kind") != "quote":
            continue
        payload = event.get("payload", {})
        if payload.get("broker_symbol") != instrument.broker_symbol:
            continue
        quote_count += 1
        try:
            observed = datetime.fromisoformat(payload["observed_at"])
            received = datetime.fromisoformat(event["at"])
            bid, ask = Decimal(payload["bid"]), Decimal(payload["ask"])
            if (
                observed.utcoffset() != timedelta(0)
                or received.utcoffset() != timedelta(0)
                or not opened_at <= observed <= received <= as_of
                or received - observed > timedelta(seconds=5)
                or not bid.is_finite()
                or not ask.is_finite()
                or bid <= 0
                or ask <= bid
            ):
                raise ValueError("invalid account quote")
        except (KeyError, TypeError, ValueError, InvalidOperation):
            invalid_count += 1
            continue
        received_times.append(received)
        if payload.get("tradeable") != "True":
            continue
        tradeable_count += 1
        spreads.append(ask - bid)
        slot = int((observed - opened_at) // interval)
        if 0 <= slot < expected and received < opened_at + (slot + 1) * interval:
            covered.add(slot)
    spreads.sort()
    times = sorted(set(received_times))
    gaps = [Decimal(str((right - left).total_seconds())) for left, right in zip(times, times[1:])]
    return CaptureQuality(
        broker_symbol=instrument.broker_symbol,
        observed_from=opened_at,
        observed_until=as_of,
        expected_intervals=expected,
        covered_intervals=len(covered),
        quote_count=quote_count,
        tradeable_quote_count=tradeable_count,
        invalid_quote_count=invalid_count,
        coverage=Decimal(len(covered)) / expected if expected else Decimal(0),
        median_spread=(spreads[(len(spreads) - 1) // 2] + spreads[len(spreads) // 2]) / 2 if spreads else None,
        p95_spread=spreads[ceil(len(spreads) * 0.95) - 1] if spreads else None,
        max_quote_gap_seconds=max(gaps) if gaps else None,
    )
