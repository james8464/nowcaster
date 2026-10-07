"""Construct causal five-minute bars from account-specific sampled quotes."""

from __future__ import annotations

from datetime import datetime, timedelta

from src.intraday.contracts import ConfirmedBar, InstrumentSpec, MarketQuote
from src.strategies.types import canonical_hash


def _bucket_start(at: datetime) -> datetime:
    return at.replace(minute=at.minute - at.minute % 5, second=0, microsecond=0)


class LiveBarBuilder:
    """A completed bar is unavailable until a later account quote seals it.

    OANDA's stream is sampled. High/low are observed extrema, not exchange
    extrema; users must not treat this as a true tick-complete price record.
    """

    def __init__(self, instrument: InstrumentSpec, *, maximum_quote_gap: timedelta = timedelta(seconds=30)):
        if maximum_quote_gap <= timedelta(0):
            raise ValueError("maximum quote gap must be positive")
        self.instrument = instrument
        self.maximum_quote_gap = maximum_quote_gap
        self.gaps = 0
        self._start: datetime | None = None
        self._quotes: list[MarketQuote] = []
        self._invalid = False
        self._feed_hash: str | None = None

    def accept(self, quote: MarketQuote) -> ConfirmedBar | None:
        if quote.instrument != self.instrument:
            raise ValueError("live quote instrument mismatch")
        if quote.received_at - quote.observed_at > timedelta(seconds=5):
            self.gaps += 1
            self._invalid = True
            return None
        bucket = _bucket_start(quote.observed_at)
        if self._start is None:
            self._start = bucket
            self._feed_hash = quote.account_feed_hash
        elif quote.account_feed_hash != self._feed_hash:
            self.gaps += 1
            self._start = bucket
            self._quotes = []
            self._feed_hash = quote.account_feed_hash
            self._invalid = True
            return None
        closed = None
        if bucket > self._start:
            if bucket - self._start > timedelta(minutes=5):
                self.gaps += int((bucket - self._start) / timedelta(minutes=5)) - 1
            closed = self._finish(available_at=quote.received_at)
            self._start = bucket
            self._quotes = []
            self._invalid = False
        elif bucket < self._start:
            self.gaps += 1
            self._invalid = True
            return None
        if quote.status != "tradeable" or (self._quotes and quote.observed_at <= self._quotes[-1].observed_at):
            self._invalid = True
        if self._quotes and quote.observed_at - self._quotes[-1].observed_at > self.maximum_quote_gap:
            self._invalid = True
        self._quotes.append(quote)
        return closed

    def _finish(self, *, available_at: datetime) -> ConfirmedBar | None:
        if self._start is None:
            return None
        end = self._start + timedelta(minutes=5)
        if (
            self._invalid
            or not self._quotes
            or self._quotes[0].observed_at - self._start > timedelta(seconds=15)
            or end - self._quotes[-1].observed_at > timedelta(seconds=15)
        ):
            self.gaps += 1
            return None
        quotes = self._quotes
        return ConfirmedBar(
            instrument=self.instrument,
            start=self._start,
            end=end,
            available_at=available_at,
            price_scope="account_stream",
            bid_open=quotes[0].bid,
            bid_high=max(item.bid for item in quotes),
            bid_low=min(item.bid for item in quotes),
            bid_close=quotes[-1].bid,
            ask_open=quotes[0].ask,
            ask_high=max(item.ask for item in quotes),
            ask_low=min(item.ask for item in quotes),
            ask_close=quotes[-1].ask,
            source_key=canonical_hash([item.source_key for item in quotes]),
        )
