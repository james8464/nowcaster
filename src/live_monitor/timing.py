"""Keep provider, receive and processing clocks separate without time travel."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from src.live_monitor.types import MAXIMUM_PROVIDER_CLOCK_LEAD, MarketBar, MarketEvent, ProviderHealthEvent


class CausalClock:
    """Reject a wall-clock rollback anywhere in one live process."""

    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._clock = clock
        self._watermark: datetime | None = None
        self._failure: ValueError | None = None

    @property
    def watermark(self) -> datetime | None:
        return self._watermark

    def now(self) -> datetime:
        if self._failure is not None:
            raise self._failure
        current = self._clock()
        if current.tzinfo is not UTC:
            self._failure = ValueError("local processing clock must use explicit UTC")
            raise self._failure
        if self._watermark is not None and current < self._watermark:
            self._failure = ValueError("local clock regressed during live processing")
            raise self._failure
        self._watermark = current
        return current


async def prepare_market_event(
    event: MarketEvent,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    pause: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> MarketEvent:
    """Wait at most one second for a provider clock lead; never rewrite receipt time."""
    now = clock()
    if isinstance(event, ProviderHealthEvent):
        if event.occurred_at > now:
            raise ValueError("local clock regressed after provider health observation")
        return event.model_copy(update={"occurred_at": now})
    if now < event.received_at:
        raise ValueError("local clock regressed after market receipt")
    available = max(event.end, event.available_at) if isinstance(event, MarketBar) else event.provider_time
    if available > now:
        lead = available - now
        if lead > MAXIMUM_PROVIDER_CLOCK_LEAD:
            raise ValueError("provider clock lead exceeds the supported bound")
        await pause(lead.total_seconds())
        now = clock()
    if now < max(available, event.received_at):
        raise ValueError("clock did not settle within the supported bound")
    return event.model_copy(update={"processed_at": now})
