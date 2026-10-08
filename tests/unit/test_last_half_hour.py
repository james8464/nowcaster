from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.intraday.contracts import ConfirmedBar, InstrumentSpec
from src.intraday.last_half_hour import evaluate_last_half_hour

NY = ZoneInfo("America/New_York")
PRODUCT = InstrumentSpec(
    provider="oanda_practice", broker_symbol="SPX500_USD", market="us500", product="cfd",
    quote_currency="USD", point_value=Decimal("1"),
)


def session(day: str, *, start: Decimal, first: Decimal, penultimate: Decimal,
            final: Decimal) -> list[ConfirmedBar]:
    local = datetime.fromisoformat(day).replace(hour=9, minute=30, tzinfo=NY)
    base = local.astimezone(UTC)
    points = [start] * 78
    for index in range(5, 66):
        points[index] = first
    for index in range(66, 72):
        points[index] = penultimate
    for index in range(72, 78):
        points[index] = final
    return [
        ConfirmedBar(
            instrument=PRODUCT, start=base + timedelta(minutes=5 * index),
            end=base + timedelta(minutes=5 * (index + 1)),
            available_at=base + timedelta(minutes=5 * (index + 1)),
            bid_open=value - Decimal("0.1"), bid_high=value + Decimal("0.1"),
            bid_low=value - Decimal("0.1"), bid_close=value - Decimal("0.1"),
            ask_open=value + Decimal("0.1"), ask_high=value + Decimal("0.1"),
            ask_low=value - Decimal("0.1"), ask_close=value + Decimal("0.1"),
            source_key=f"{day}:{index}",
        ) for index, value in enumerate(points)
    ]


def test_confirmed_signals_enter_only_at_1535_new_york():
    prior = session("2025-03-07", start=Decimal("99"), first=Decimal("99"),
                    penultimate=Decimal("99"), final=Decimal("100"))
    current = session("2025-03-10", start=Decimal("100"), first=Decimal("101"),
                      penultimate=Decimal("102"), final=Decimal("103"))
    result = evaluate_last_half_hour(prior, current, slippage_points=Decimal("0.5"))
    assert result.direction == "long"
    assert result.decision_at.astimezone(NY).strftime("%H:%M") == "15:30"
    assert result.entered_at.astimezone(NY).strftime("%H:%M") == "15:35"
    assert result.entered_at > result.decision_at
    assert result.price_scope == "historical_base_exploratory"


def test_disagreeing_signs_and_missing_bar_abstain():
    prior = session("2025-03-07", start=Decimal("99"), first=Decimal("99"),
                    penultimate=Decimal("99"), final=Decimal("100"))
    current = session("2025-03-10", start=Decimal("100"), first=Decimal("101"),
                      penultimate=Decimal("99"), final=Decimal("99"))
    assert evaluate_last_half_hour(prior, current, slippage_points=Decimal("0.5")).reason == "signals_disagree"
    assert evaluate_last_half_hour(prior, current[:-1], slippage_points=Decimal("0.5")).reason == "incomplete_session"


def test_short_and_stressed_slippage_reduce_net_return():
    prior = session("2025-03-07", start=Decimal("101"), first=Decimal("101"),
                    penultimate=Decimal("101"), final=Decimal("100"))
    current = session("2025-03-10", start=Decimal("100"), first=Decimal("99"),
                      penultimate=Decimal("98"), final=Decimal("97"))
    normal = evaluate_last_half_hour(prior, current, slippage_points=Decimal("0.5"))
    stressed = evaluate_last_half_hour(prior, current, slippage_points=Decimal("1.0"))
    assert normal.direction == "short"
    assert normal.net_points > stressed.net_points


def test_invalid_slippage_rejected():
    prior = session("2025-03-07", start=Decimal("99"), first=Decimal("99"),
                    penultimate=Decimal("99"), final=Decimal("100"))
    current = session("2025-03-10", start=Decimal("100"), first=Decimal("101"),
                      penultimate=Decimal("102"), final=Decimal("103"))
    with pytest.raises(ValueError):
        evaluate_last_half_hour(prior, current, slippage_points=Decimal("-1"))


def test_same_bar_stop_and_target_uses_stop_first():
    prior = session("2025-03-07", start=Decimal("99"), first=Decimal("99"),
                    penultimate=Decimal("99"), final=Decimal("100"))
    current = session("2025-03-10", start=Decimal("100"), first=Decimal("101"),
                      penultimate=Decimal("102"), final=Decimal("103"))
    current[73] = current[73].model_copy(update={
        "bid_low": Decimal("100"), "ask_low": Decimal("100"),
        "bid_high": Decimal("106"), "ask_high": Decimal("106"),
    })
    outcome = evaluate_last_half_hour(prior, current, slippage_points=Decimal("0.5"))
    assert outcome.reason == "stop"
    assert outcome.exit < outcome.stop


def test_full_cfd_bars_on_cash_market_holiday_still_abstain():
    prior = session("2023-06-16", start=Decimal("99"), first=Decimal("99"),
                    penultimate=Decimal("99"), final=Decimal("100"))
    holiday = session("2023-06-19", start=Decimal("100"), first=Decimal("101"),
                      penultimate=Decimal("102"), final=Decimal("103"))
    assert evaluate_last_half_hour(prior, holiday, slippage_points=Decimal("0.5")).reason == "incomplete_session"
