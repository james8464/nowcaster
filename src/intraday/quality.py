"""Fail-closed feasibility before historical P&L selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median

from src.intraday.contracts import ConfirmedBar, InstrumentSpec, MarketQuote

D = Decimal
MINIMUM_HISTORICAL_COVERAGE = D("0.995")
MINIMUM_ACCOUNT_COVERAGE = D("0.99")
MAXIMUM_MEDIAN_SPREAD_BPS = D("25")


@dataclass(frozen=True)
class MarketFeasibility:
    historical_bar_coverage: Decimal
    account_quote_coverage: Decimal
    median_spread: Decimal | None
    median_spread_bps: Decimal | None
    reasons: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return not self.reasons


def assess_market_feasibility(
    instrument: InstrumentSpec,
    expected_bar_starts: tuple[datetime, ...],
    bars: list[ConfirmedBar] | tuple[ConfirmedBar, ...],
    quotes: list[MarketQuote] | tuple[MarketQuote, ...],
    *,
    minimum_historical_coverage: Decimal = MINIMUM_HISTORICAL_COVERAGE,
    minimum_account_coverage: Decimal = MINIMUM_ACCOUNT_COVERAGE,
    maximum_median_spread_bps: Decimal = MAXIMUM_MEDIAN_SPREAD_BPS,
) -> MarketFeasibility:
    """Expected slots come from a versioned trading calendar, not observed bars."""
    if not expected_bar_starts or sorted(set(expected_bar_starts)) != list(expected_bar_starts):
        raise ValueError("expected session slots must be nonempty, unique and sorted")
    if any(bar.instrument != instrument for bar in bars) or any(item.instrument != instrument for item in quotes):
        raise ValueError("instrument identity mismatch")
    bar_starts = [bar.start for bar in bars]
    reasons = []
    if len(set(bar_starts)) != len(bar_starts):
        reasons.append("duplicate_historical_bar")
    expected = set(expected_bar_starts)
    historical_coverage = D(len(expected & set(bar_starts))) / len(expected)
    if historical_coverage < minimum_historical_coverage:
        reasons.append("historical_gap")
    identities = {item.account_feed_hash for item in quotes}
    if len(identities) != 1:
        reasons.append("account_feed_identity_conflict")
    observed = set()
    usable_quotes = []
    for slot in expected_bar_starts:
        eligible = [
            item
            for item in quotes
            if item.status == "tradeable"
            and slot + timedelta(minutes=5) < item.observed_at <= slot + timedelta(minutes=5, seconds=15)
            and item.received_at <= slot + timedelta(minutes=5, seconds=15)
        ]
        if eligible:
            observed.add(slot)
            usable_quotes.append(min(eligible, key=lambda item: item.received_at))
    account_coverage = D(len(observed)) / len(expected)
    if account_coverage < minimum_account_coverage:
        reasons.append("account_feed_gap")
    spread = D(str(median(item.spread for item in usable_quotes))) if usable_quotes else None
    spread_bps = (
        D(str(median(item.spread / ((item.bid + item.ask) / 2) * D(10000) for item in usable_quotes)))
        if usable_quotes
        else None
    )
    if spread_bps is None or spread_bps > maximum_median_spread_bps:
        reasons.append("spread_unusable")
    return MarketFeasibility(historical_coverage, account_coverage, spread, spread_bps, tuple(reasons))
