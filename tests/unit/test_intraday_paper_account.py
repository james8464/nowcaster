from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.intraday.contracts import InstrumentSpec, MarketQuote
from src.intraday.paper import FXConversion, PaperAccount, PaperExecutionCosts, PaperRiskPolicy
from src.intraday.strategies import SetupDecision

T = datetime(2026, 10, 6, 8, 20, tzinfo=UTC)
INSTRUMENT = InstrumentSpec(
    provider="oanda_practice",
    broker_symbol="DE30_EUR",
    market="germany40",
    product="cfd",
    quote_currency="EUR",
    point_value=Decimal("1"),
)


def paper_account():
    return PaperAccount(
        PaperRiskPolicy(),
        initial_cash=Decimal("10000"),
        account_currency="EUR",
        costs=PaperExecutionCosts(verified=True),
    )


def plan(direction="long"):
    return SetupDecision(
        status="ready",
        strategy_id="opening_range_15",
        direction=direction,
        reason="Confirmed breakout; paper only.",
        decision_at=T,
        entry_at=T + timedelta(seconds=1),
        entry=Decimal("24002") if direction == "long" else Decimal("24000"),
        stop=Decimal("23982") if direction == "long" else Decimal("24020"),
        target=Decimal("24042") if direction == "long" else Decimal("23960"),
        exit_by=T + timedelta(hours=1),
        estimated_roundtrip_cost=Decimal("2"),
        evidence_hash="a" * 64,
    )


def quote(bid, seconds=2):
    at = T + timedelta(seconds=seconds)
    return MarketQuote(
        instrument=INSTRUMENT,
        account_feed_hash="b" * 64,
        observed_at=at,
        received_at=at + timedelta(milliseconds=100),
        bid=Decimal(str(bid)),
        ask=Decimal(str(bid)) + 2,
        status="tradeable",
        source_key=f"quote-{bid}-{seconds}",
    )


def test_paper_trade_sizes_to_risk_and_closes_at_executable_bid():
    desk = paper_account()
    opened = desk.open(plan(), INSTRUMENT, quote(24000), unit_step=Decimal("0.01"))
    assert opened is not None
    assert opened.units * (opened.entry - opened.stop) <= Decimal("25")
    assert desk.open(plan(), INSTRUMENT, quote(24000, 3), unit_step=Decimal("0.01")) is None
    closed = desk.update(quote(24050, 60))
    assert closed is not None
    assert closed.exit_reason == "target"
    assert closed.exit_price == Decimal("24050")
    assert closed.net_pnl == (Decimal("24050") - opened.entry) * opened.units


def test_stop_gap_uses_worse_quote_not_stop_level():
    desk = paper_account()
    opened = desk.open(plan("short"), INSTRUMENT, quote(24000), unit_step=Decimal("0.01"))
    closed = desk.update(quote(24050, 60))
    assert closed.exit_reason == "stop"
    assert closed.exit_price == Decimal("24052")
    assert closed.net_pnl == (opened.entry - Decimal("24052")) * opened.units


def test_daily_and_drawdown_kill_switches_abstain():
    desk = paper_account()
    assert desk.open(plan(), INSTRUMENT, quote(24000), unit_step=Decimal("0.01")) is not None
    assert desk.update(quote(23000, 60)) is not None
    later_plan = plan().model_copy(
        update={"decision_at": T + timedelta(seconds=61), "entry_at": T + timedelta(seconds=62)}
    )
    assert desk.open(later_plan, INSTRUMENT, quote(24000, 62), unit_step=Decimal("0.01")) is None
    desk.daily_realized_pnl = Decimal("0")
    desk.equity = Decimal("9499")
    assert desk.open(later_plan, INSTRUMENT, quote(24000, 62), unit_step=Decimal("0.01")) is None


def test_cross_currency_and_unverified_costs_fail_closed():
    unverified = PaperAccount(PaperRiskPolicy(), initial_cash=Decimal("10000"), account_currency="GBP")
    assert unverified.open(plan(), INSTRUMENT, quote(24000), unit_step=Decimal("0.01")) is None
    assert unverified.last_rejection == "execution_costs_unverified"
    desk = PaperAccount(
        PaperRiskPolicy(),
        initial_cash=Decimal("10000"),
        account_currency="GBP",
        costs=PaperExecutionCosts(
            verified=True,
            slippage_points=Decimal("0.5"),
            commission_per_unit=Decimal("0.1"),
            financing_per_unit=Decimal("0"),
        ),
    )
    assert desk.open(plan(), INSTRUMENT, quote(24000), unit_step=Decimal("0.01")) is None
    assert desk.last_rejection == "currency_conversion_unavailable"
    conversion = FXConversion(
        from_currency="EUR", to_currency="GBP", rate=Decimal("0.85"), observed_at=quote(24000).observed_at
    )
    opened = desk.open(plan(), INSTRUMENT, quote(24000), unit_step=Decimal("0.01"), conversion=conversion)
    assert opened is not None
    assert opened.entry == Decimal("24002.5")
    assert desk.update(quote(24050, 60)) is None
    assert desk.last_rejection == "currency_conversion_unavailable"
    exit_conversion = conversion.model_copy(update={"observed_at": quote(24050, 60).observed_at})
    closed = desk.update(quote(24050, 60), conversion=exit_conversion)
    assert closed is not None
    assert closed.net_pnl < (Decimal("24050") - opened.entry) * opened.units * Decimal("0.85")


def test_daily_entry_counter_resets_on_new_utc_day():
    desk = paper_account()
    assert desk.open(plan(), INSTRUMENT, quote(24000), unit_step=Decimal("0.01")) is not None
    assert desk.update(quote(23000, 60)) is not None
    tomorrow = quote(24000).model_copy(
        update={
            "observed_at": T + timedelta(days=1, seconds=2),
            "received_at": T + timedelta(days=1, seconds=2, milliseconds=100),
        }
    )
    next_plan = plan().model_copy(
        update={
            "decision_at": T + timedelta(days=1),
            "entry_at": T + timedelta(days=1, seconds=1),
            "exit_by": T + timedelta(days=1, hours=1),
        }
    )
    assert desk.open(next_plan, INSTRUMENT, tomorrow, unit_step=Decimal("0.01")) is not None


def test_cost_budget_is_quote_currency_not_point_value_scaled():
    instrument = INSTRUMENT.model_copy(update={"point_value": Decimal("10")})
    current = quote(100).model_copy(update={"instrument": instrument})
    small_plan = plan().model_copy(update={"entry": Decimal("102"), "stop": Decimal("92"), "target": Decimal("122")})
    desk = PaperAccount(
        PaperRiskPolicy(),
        initial_cash=Decimal("10000"),
        account_currency="EUR",
        costs=PaperExecutionCosts(verified=True, commission_per_unit=Decimal("2")),
    )
    opened = desk.open(small_plan, instrument, current, unit_step=Decimal("0.001"))
    assert opened is not None
    assert opened.units == Decimal("0.245")
    assert opened.units * ((opened.entry - opened.stop) * Decimal("10") + Decimal("2")) <= Decimal("25")
