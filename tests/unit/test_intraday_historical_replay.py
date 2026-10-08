from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from src.intraday.contracts import ConfirmedBar, InstrumentSpec
from src.intraday.historical import HistoricalFXRate, ReplayCosts, replay_session

T = datetime(2026, 10, 6, 8, tzinfo=UTC)
INSTRUMENT = InstrumentSpec(
    provider="oanda_practice",
    broker_symbol="DE30_EUR",
    market="germany40",
    product="cfd",
    quote_currency="EUR",
    point_value=D(1),
)


def bar(n: int, close: str, *, high: str | None = None, low: str | None = None) -> ConfirmedBar:
    value = D(close)
    start = T + timedelta(minutes=5 * n)
    high_value = D(high) if high else value + 1
    low_value = D(low) if low else value - 1
    return ConfirmedBar(
        instrument=INSTRUMENT,
        start=start,
        end=start + timedelta(minutes=5),
        available_at=start + timedelta(days=1),
        source_key=f"historical-{n}",
        bid_open=value,
        bid_high=high_value,
        bid_low=low_value,
        bid_close=value,
        ask_open=value + 2,
        ask_high=high_value + 2,
        ask_low=low_value + 2,
        ask_close=value + 2,
    )


def costs(slippage="0"):
    return ReplayCosts(
        account_currency="EUR",
        quote_to_account=D(1),
        slippage_points=D(slippage),
        commission_per_unit=D("0.1"),
        financing_per_unit=D(0),
    )


def test_replay_uses_next_bar_open_and_cannot_promote_historical_prices():
    bars = [
        bar(0, "100", high="101"),
        bar(1, "100", high="102"),
        bar(2, "101", high="102"),
        bar(3, "104", high="105"),
        bar(4, "106", high="108"),
        bar(5, "118", high="130", low="117"),
    ]
    result = replay_session(
        "opening_range_15", bars, session_open=T, session_close=T + timedelta(hours=1), costs=costs()
    )
    assert result.price_scope == "historical_base_exploratory"
    assert len(result.trades) == 1
    assert result.trades[0].entered_at == bars[4].start
    assert result.trades[0].entry == bars[4].ask_open
    assert result.trades[0].net_pnl > 0
    changed_future = [*bars[:-1], bar(5, "200", high="210", low="199")]
    other = replay_session(
        "opening_range_15", changed_future, session_open=T, session_close=T + timedelta(hours=1), costs=costs()
    )
    assert other.trades[0].decision_hash == result.trades[0].decision_hash


def test_stressed_costs_reduce_net_and_dual_touch_assumes_stop_first():
    bars = [
        bar(0, "100", high="101"),
        bar(1, "100", high="102"),
        bar(2, "101", high="102"),
        bar(3, "104", high="105"),
        bar(4, "106", high="108"),
        bar(5, "100", high="130", low="90"),
    ]
    normal = replay_session(
        "opening_range_15", bars, session_open=T, session_close=T + timedelta(hours=1), costs=costs()
    )
    stressed = replay_session(
        "opening_range_15", bars, session_open=T, session_close=T + timedelta(hours=1), costs=costs("2")
    )
    assert normal.trades[0].exit_reason == "stop"
    assert stressed.trades[0].net_pnl < normal.trades[0].net_pnl


def test_missing_conversion_or_session_end_abstains():
    with pytest.raises(ValueError, match="greater than 0"):
        ReplayCosts(
            account_currency="GBP",
            quote_to_account=D(0),
            slippage_points=D(0),
            commission_per_unit=D(0),
            financing_per_unit=D(0),
        )
    bars = [bar(0, "100"), bar(1, "100"), bar(2, "100"), bar(3, "100"), bar(4, "100")]
    result = replay_session(
        "opening_range_15", bars, session_open=T, session_close=T + timedelta(minutes=25), costs=costs()
    )
    assert not result.trades
    assert result.no_trade_count > 0


def test_one_hour_historical_time_exit_does_not_consume_next_bar_extrema():
    bars = [bar(0, "100"), bar(1, "100"), bar(2, "101"), bar(3, "104")]
    bars.extend(bar(n, "104", high="105", low="103") for n in range(4, 16))
    bars.append(bar(16, "104", high="200", low="1"))
    result = replay_session(
        "opening_range_15", bars, session_open=T, session_close=T + timedelta(hours=2), costs=costs()
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "time_limit"
    assert trade.exited_at - trade.entered_at == timedelta(hours=1)


def test_historical_foreign_currency_conversion_fee_reduces_net_return():
    bars = [
        bar(0, "100", high="101"),
        bar(1, "100", high="102"),
        bar(2, "101", high="102"),
        bar(3, "104", high="105"),
        bar(4, "106", high="108"),
        bar(5, "118", high="130", low="117"),
    ]
    base = costs().model_copy(
        update={"account_currency": "GBP", "quote_to_account": D("0.86"), "conversion_fee_fraction": D(0)}
    )
    charged = base.model_copy(update={"conversion_fee_fraction": D("0.01")})
    without = replay_session("opening_range_15", bars, session_open=T, session_close=T + timedelta(hours=1), costs=base)
    with_fee = replay_session(
        "opening_range_15",
        bars,
        session_open=T,
        session_close=T + timedelta(hours=1),
        costs=charged,
    )
    assert len(without.trades) == len(with_fee.trades) == 1
    assert with_fee.trades[0].net_pnl < without.trades[0].net_pnl


def test_historical_fx_rates_use_entry_loss_and_exit_gain_sides_without_lookahead():
    bars = [
        bar(0, "100", high="101"),
        bar(1, "100", high="102"),
        bar(2, "101", high="102"),
        bar(3, "104", high="105"),
        bar(4, "106", high="108"),
        bar(5, "118", high="130", low="117"),
    ]
    base = costs().model_copy(
        update={
            "account_currency": "GBP",
            "quote_to_account": D("0.86"),
            "conversion_fee_fraction": D(0),
            "commission_per_unit": D(0),
        }
    )
    rates = {item.start: HistoricalFXRate(gain_factor=D("0.70"), loss_factor=D("0.90")) for item in bars}
    rates[bars[5].start] = HistoricalFXRate(gain_factor=D("0.75"), loss_factor=D("0.95"))
    result = replay_session(
        "opening_range_15", bars, session_open=T, session_close=T + timedelta(hours=1), costs=base, fx_at_open=rates
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.net_pnl == (trade.exit - trade.entry) * trade.units * D("0.75")
    changed_future = dict(rates)
    changed_future[bars[5].start] = HistoricalFXRate(gain_factor=D("0.60"), loss_factor=D("0.80"))
    future_result = replay_session(
        "opening_range_15",
        bars,
        session_open=T,
        session_close=T + timedelta(hours=1),
        costs=base,
        fx_at_open=changed_future,
    )
    assert future_result.trades[0].decision_hash == trade.decision_hash
    assert future_result.trades[0].units == trade.units
    assert future_result.trades[0].net_pnl < trade.net_pnl
    missing = replay_session(
        "opening_range_15",
        bars,
        session_open=T,
        session_close=T + timedelta(hours=1),
        costs=base,
        fx_at_open={key: value for key, value in rates.items() if key != bars[5].start},
    )
    assert missing.trades == ()
    assert missing.gaps == 1


def test_historical_fx_rate_rejects_profitable_side_better_than_loss_side():
    with pytest.raises(ValueError):
        HistoricalFXRate(gain_factor=D("0.91"), loss_factor=D("0.90"))


def test_historical_fx_loss_uses_more_expensive_side():
    bars = [
        bar(0, "100"),
        bar(1, "100"),
        bar(2, "101"),
        bar(3, "104"),
        bar(4, "106"),
        bar(5, "90", high="100", low="89"),
    ]
    base = costs().model_copy(
        update={
            "account_currency": "GBP",
            "quote_to_account": D("0.86"),
            "conversion_fee_fraction": D(0),
            "commission_per_unit": D(0),
        }
    )
    rates = {item.start: HistoricalFXRate(gain_factor=D("0.70"), loss_factor=D("0.90")) for item in bars}
    result = replay_session(
        "opening_range_15", bars, session_open=T, session_close=T + timedelta(hours=1), costs=base, fx_at_open=rates
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.net_pnl == (trade.exit - trade.entry) * trade.units * D("0.90")


def test_historical_position_size_respects_cost_inclusive_risk_budget():
    bars = [
        bar(0, "100", high="101"),
        bar(1, "100", high="102"),
        bar(2, "101", high="102"),
        bar(3, "104", high="105"),
        bar(4, "106", high="108"),
        bar(5, "118", high="130", low="117"),
    ]
    ordinary = costs()
    expensive = ordinary.model_copy(update={"commission_per_unit": D("50")})
    baseline = replay_session(
        "opening_range_15",
        bars,
        session_open=T,
        session_close=T + timedelta(hours=1),
        costs=ordinary,
    )
    charged = replay_session(
        "opening_range_15",
        bars,
        session_open=T,
        session_close=T + timedelta(hours=1),
        costs=expensive,
    )
    assert len(baseline.trades) == len(charged.trades) == 1
    assert charged.trades[0].units < baseline.trades[0].units


def test_historical_selection_rejects_below_broker_minimum_and_rounds_fractional_units():
    bars = [
        bar(0, "100", high="101"),
        bar(1, "100", high="102"),
        bar(2, "101", high="102"),
        bar(3, "104", high="105"),
        bar(4, "106", high="108"),
        bar(5, "118", high="130", low="117"),
    ]
    args = {"session_open": T, "session_close": T + timedelta(hours=1), "costs": costs(), "initial_equity": D("100")}
    allowed = replay_session("opening_range_15", bars, **args, minimum_trade_size=D("0.01"), trade_units_precision=2)
    assert len(allowed.trades) == 1
    assert allowed.trades[0].units >= D("0.01")
    assert allowed.trades[0].units % D("0.01") == 0
    blocked = replay_session("opening_range_15", bars, **args, minimum_trade_size=D("1"), trade_units_precision=0)
    assert blocked.trades == ()
    assert blocked.no_trade_count > 0
