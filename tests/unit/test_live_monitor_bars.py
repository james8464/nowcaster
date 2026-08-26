from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.live_monitor.bars import FinalizedBarLedger, aggregate_finalized, missing_ranges
from src.live_monitor.types import MarketBar

START = datetime(2026, 8, 26, 13, 30, tzinfo=UTC)


def bar(index: int, *, revision: int = 0, close_offset: str = "0") -> MarketBar:
    opened = Decimal("100") + Decimal(index)
    closed = opened + Decimal(close_offset)
    start = START + timedelta(minutes=index)
    return MarketBar(
        provider="alpaca",
        feed="iex",
        symbol="SPY",
        interval="1m",
        start=start,
        end=start + timedelta(minutes=1),
        available_at=start + timedelta(minutes=1, seconds=2),
        received_at=start + timedelta(minutes=1, seconds=3),
        open=opened,
        high=max(opened, closed) + Decimal("1"),
        low=min(opened, closed) - Decimal("1"),
        close=closed,
        volume=Decimal(index + 1),
        finalized=True,
        revision=revision,
    )


def test_ledger_is_idempotent_and_preserves_revisions_without_mutation() -> None:
    ledger = FinalizedBarLedger()
    original = bar(0)
    duplicate = original.model_copy(update={"received_at": original.received_at + timedelta(seconds=3)})
    revised = bar(0, revision=1, close_offset="0.5")

    assert ledger.accept(original).status == "accepted"
    assert ledger.accept(duplicate).status == "duplicate"
    revision = ledger.accept(revised)

    assert revision.status == "revised"
    assert revision.previous_bar_id == original.bar_id
    assert ledger.bars == (original, revised)


def test_aggregate_requires_every_finalized_constituent_and_uses_literal_ohlcv() -> None:
    complete = tuple(bar(index) for index in range(5))
    result = aggregate_finalized(complete, "5m")

    assert len(result) == 1
    aggregated = result[0]
    assert (aggregated.start, aggregated.end) == (START, START + timedelta(minutes=5))
    assert (aggregated.open, aggregated.high, aggregated.low, aggregated.close, aggregated.volume) == (
        Decimal("100"),
        Decimal("105"),
        Decimal("99"),
        Decimal("104"),
        Decimal("15"),
    )
    assert aggregate_finalized(complete[:-1], "5m") == ()


def test_gaps_are_explicit_and_bounded() -> None:
    observed = (bar(0), bar(1), bar(4))
    gaps = missing_ranges(
        observed,
        start=START,
        end=START + timedelta(minutes=5),
        interval="1m",
        maximum_ranges=4,
    )

    assert [(item.start, item.end) for item in gaps] == [(START + timedelta(minutes=2), START + timedelta(minutes=4))]


def test_appending_future_bars_cannot_change_prior_aggregate_identity_or_values() -> None:
    first_window = tuple(bar(index) for index in range(5))
    before = aggregate_finalized(first_window, "5m")
    after = aggregate_finalized(first_window + tuple(bar(index) for index in range(5, 10)), "5m")

    assert after[: len(before)] == before
    assert after[0].bar_id == before[0].bar_id
