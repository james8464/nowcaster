"""One pre-registered, exploratory SPX500 last-half-hour hypothesis.

Historical base candles are not account-specific executable quotes. This
module cannot qualify a live rule or place an order.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from src.intraday.contracts import ConfirmedBar

NY = ZoneInfo("America/New_York")
D = Decimal


@dataclass(frozen=True)
class DayOutcome:
    reason: str
    direction: str | None = None
    decision_at: datetime | None = None
    entered_at: datetime | None = None
    exited_at: datetime | None = None
    entry: Decimal | None = None
    exit: Decimal | None = None
    stop: Decimal | None = None
    target: Decimal | None = None
    net_points: Decimal = D(0)
    price_scope: str = "historical_base_exploratory"


def _mid(bid: Decimal, ask: Decimal) -> Decimal:
    return (bid + ask) / 2


def _full_session(bars: Sequence[ConfirmedBar]) -> bool:
    if len(bars) != 78:
        return False
    first = bars[0].start.astimezone(NY)
    if first.weekday() >= 5 or (first.hour, first.minute) != (9, 30):
        return False
    expected = first.astimezone(bars[0].start.tzinfo)
    return all(
        bar.instrument.broker_symbol == "SPX500_USD"
        and bar.price_scope == "historical_base"
        and bar.start == expected + timedelta(minutes=5 * index)
        and bar.end == bar.start + timedelta(minutes=5)
        and bar.available_at >= bar.end
        for index, bar in enumerate(bars)
    )


def evaluate_last_half_hour(
    prior: Sequence[ConfirmedBar], current: Sequence[ConfirmedBar], *, slippage_points: Decimal
) -> DayOutcome:
    """Decide at NY 15:30; use the 15:35 historical open at the earliest."""
    if not slippage_points.is_finite() or slippage_points < 0:
        raise ValueError("slippage must be finite and nonnegative")
    if not _full_session(prior) or not _full_session(current):
        return DayOutcome("incomplete_session")
    if prior[0].instrument != current[0].instrument:
        return DayOutcome("instrument_mismatch")
    prior_day = prior[0].start.astimezone(NY).date()
    day = current[0].start.astimezone(NY).date()
    if not prior_day < day or (day - prior_day).days > 7:
        return DayOutcome("prior_session_unavailable")
    morning = _mid(current[5].bid_close, current[5].ask_close) - _mid(
        prior[77].bid_close, prior[77].ask_close
    )
    afternoon = _mid(current[71].bid_close, current[71].ask_close) - _mid(
        current[65].bid_close, current[65].ask_close
    )
    if not (morning > 0 and afternoon > 0 or morning < 0 and afternoon < 0):
        return DayOutcome("signals_disagree")
    direction = "long" if morning > 0 else "short"
    first = current[73]  # 15:35; bar 71 closed at 15:30, bar 72 is a latency buffer.
    entry = first.ask_open + slippage_points if direction == "long" else first.bid_open - slippage_points
    if entry <= 0:
        return DayOutcome("invalid_entry")
    stop = entry * (D("0.9975") if direction == "long" else D("1.0025"))
    target = entry * (D("1.005") if direction == "long" else D("0.995"))
    for bar in current[73:]:
        if direction == "long":
            stop_hit = bar.bid_low <= stop
            target_hit = bar.bid_high >= target
            raw_exit = min(stop, bar.bid_open) if stop_hit else target if target_hit else bar.bid_close
            exit_price = raw_exit - slippage_points
            gross = exit_price - entry
        else:
            stop_hit = bar.ask_high >= stop
            target_hit = bar.ask_low <= target
            raw_exit = max(stop, bar.ask_open) if stop_hit else target if target_hit else bar.ask_close
            exit_price = raw_exit + slippage_points
            gross = entry - exit_price
        if stop_hit or target_hit or bar is current[-1]:
            return DayOutcome(
                reason="stop" if stop_hit else "target" if target_hit else "session_end",
                direction=direction,
                decision_at=current[71].end,
                entered_at=first.start,
                exited_at=bar.end,
                entry=entry,
                exit=exit_price,
                stop=stop,
                target=target,
                net_points=gross - abs(gross) * D("0.01"),
            )
    raise AssertionError("full session lacked an exit bar")
