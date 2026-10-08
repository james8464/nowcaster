import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.intraday.contracts import InstrumentSpec
from src.intraday.live_service import LiveIndicatorSession, LiveRoundManifest, LiveRule, LiveSessionWindow

NOW = datetime(2026, 10, 8, 8, 0, tzinfo=UTC)


def instrument(symbol, market, product="cfd", currency="EUR"):
    return InstrumentSpec(
        provider="oanda_practice",
        broker_symbol=symbol,
        market=market,
        product=product,
        quote_currency=currency,
        point_value=Decimal(1),
    )


PRODUCTS = (
    instrument("DE30_EUR", "germany40"),
    instrument("SPX500_USD", "us500", currency="USD"),
    instrument("EUR_USD", "eurusd", "margin_fx", "USD"),
    instrument("WTICO_USD", "wti", currency="USD"),
)


def manifest():
    return LiveRoundManifest(
        round_id="round-2",
        account_feed_hash="a" * 64,
        instruments=PRODUCTS,
        rules={
            item.broker_symbol: LiveRule(strategy_id="trend_pullback", direction="long", selection_hash="b" * 64)
            for item in PRODUCTS
        },
        sessions={
            item.broker_symbol: LiveSessionWindow(
                opened_at=NOW - timedelta(minutes=5), closed_at=NOW + timedelta(hours=8)
            )
            for item in PRODUCTS
        },
    )


def line(symbol, at=NOW, bid="100", ask="101"):
    return json.dumps(
        {
            "type": "PRICE",
            "instrument": symbol,
            "time": at.isoformat(),
            "tradeable": True,
            "bids": [{"price": bid}],
            "asks": [{"price": ask}],
        }
    )


def test_four_products_are_routed_and_inventory_is_not_a_paper_signal(tmp_path):
    session = LiveIndicatorSession.restore(tmp_path, manifest())
    for item in PRODUCTS:
        status = session.on_event(line(item.broker_symbol), NOW + timedelta(seconds=1))
    assert len(status.markets) == 4
    assert status.opportunities == ()
    assert status.paper_positions == ()
    assert (tmp_path / "quotes.jsonl").exists()
    assert (tmp_path / "summary.json").exists()


def test_real_oanda_status_field_makes_account_quote_tradeable(tmp_path):
    session = LiveIndicatorSession.restore(tmp_path, manifest(), restart_at=NOW)
    event = {
        "type": "PRICE",
        "instrument": "DE30_EUR",
        "time": NOW.isoformat(),
        "status": "tradeable",
        "bids": [{"price": "100"}],
        "asks": [{"price": "101"}],
    }
    status = session.on_event(json.dumps(event), NOW + timedelta(milliseconds=100))
    assert status.feed_health == "healthy"
    assert session.last_quote is not None
    assert session.last_quote.status == "tradeable"
    assert session.journal.events()[-1]["payload"]["tradeable"] == "True"


def test_restart_is_stale_and_duplicate_quote_does_not_create_another_event(tmp_path):
    session = LiveIndicatorSession.restore(tmp_path, manifest())
    first = line("DE30_EUR")
    session.on_event(first, NOW + timedelta(seconds=1))
    restarted = LiveIndicatorSession.restore(tmp_path, manifest())
    assert restarted.status.feed_health == "stale"
    before = len((tmp_path / "quotes.jsonl").read_text().splitlines())
    restarted.on_event(first, NOW + timedelta(seconds=2))
    assert len((tmp_path / "quotes.jsonl").read_text().splitlines()) == before


def test_future_and_unknown_events_fail_closed(tmp_path):
    session = LiveIndicatorSession.restore(tmp_path, manifest())
    status = session.on_event(line("DE30_EUR", NOW + timedelta(seconds=10)), NOW)
    assert status.feed_health in ("stale", "error")
    assert status.opportunities == ()
    status = session.on_event(line("UNKNOWN"), NOW)
    assert status.opportunities == ()


def test_one_decision_per_complete_bar_after_a_later_quote(tmp_path):
    session = LiveIndicatorSession.restore(tmp_path, manifest())
    for seconds in range(0, 300, 10):
        at = NOW + timedelta(seconds=seconds)
        session.on_event(line("DE30_EUR", at), at + timedelta(milliseconds=100))
    seal = NOW + timedelta(minutes=5)
    session.on_event(line("DE30_EUR", seal), seal + timedelta(milliseconds=100))
    assert not [event for event in session.journal.events() if event["kind"] == "decision"]
    later = seal + timedelta(seconds=1)
    session.on_event(line("DE30_EUR", later), later + timedelta(milliseconds=100))
    assert len([event for event in session.journal.events() if event["kind"] == "decision"]) == 1


def test_new_plan_is_exposed_once_for_downstream_paper_admission(tmp_path):
    session = LiveIndicatorSession.restore(tmp_path, manifest())
    for seconds in range(0, 300, 10):
        at = NOW + timedelta(seconds=seconds)
        session.on_event(line("DE30_EUR", at), at + timedelta(milliseconds=100))
    seal = NOW + timedelta(minutes=5)
    session.on_event(line("DE30_EUR", seal), seal + timedelta(milliseconds=100))
    later = seal + timedelta(seconds=1)
    session.on_event(line("DE30_EUR", later), later + timedelta(milliseconds=100))
    assert session.new_plan is not None
    assert session.new_plan.strategy_id == "trend_pullback"
    assert session.last_quote is not None
    session.on_event(line("DE30_EUR", later + timedelta(seconds=1)), later + timedelta(seconds=1, milliseconds=100))
    assert session.new_plan is None
    next_quote = seal + timedelta(seconds=2)
    session.on_event(line("DE30_EUR", next_quote), next_quote + timedelta(milliseconds=100))
    assert len([event for event in session.journal.events() if event["kind"] == "decision"]) == 1


def test_no_trade_reason_survives_unrelated_quotes_until_next_decision(tmp_path):
    session = LiveIndicatorSession.restore(tmp_path, manifest())
    for seconds in range(0, 300, 10):
        at = NOW + timedelta(seconds=seconds)
        session.on_event(line("DE30_EUR", at), at + timedelta(milliseconds=100))
    seal = NOW + timedelta(minutes=5)
    session.on_event(line("DE30_EUR", seal), seal + timedelta(milliseconds=100))
    later = seal + timedelta(seconds=1)
    decision_status = session.on_event(line("DE30_EUR", later), later + timedelta(milliseconds=100))
    assert "trend history unavailable" in decision_status.no_trade_reason.lower()
    other_status = session.on_event(line("SPX500_USD", later), later + timedelta(milliseconds=200))
    assert "DE30_EUR" in other_status.no_trade_reason
    assert "trend history unavailable" in other_status.no_trade_reason.lower()


def test_watch_only_rule_can_explain_a_confirmed_short_without_opening_paper_position(tmp_path):
    item = PRODUCTS[0]
    watch = LiveRoundManifest(
        round_id="watch-only-both-directions",
        account_feed_hash="a" * 64,
        instruments=(item,),
        rules={item.broker_symbol: LiveRule(strategy_id="opening_range_15", direction="both", selection_hash="0" * 64)},
        sessions={item.broker_symbol: LiveSessionWindow(opened_at=NOW, closed_at=NOW + timedelta(hours=2))},
    )
    session = LiveIndicatorSession.restore(tmp_path, watch, restart_at=NOW)
    for seconds in range(0, 1200, 10):
        at = NOW + timedelta(seconds=seconds)
        bid, ask = ("100", "101") if seconds < 900 else ("97", "98")
        session.on_event(line(item.broker_symbol, at, bid, ask), at + timedelta(milliseconds=100))
    seal = NOW + timedelta(minutes=20)
    session.on_event(line(item.broker_symbol, seal, "97", "98"), seal + timedelta(milliseconds=100))
    after_seal = seal + timedelta(seconds=1)
    status = session.on_event(
        line(item.broker_symbol, after_seal, "97", "98"), after_seal + timedelta(milliseconds=100)
    )
    assert len(status.opportunities) == 1
    assert status.opportunities[0].direction == "short"
    assert status.markets[0].eligibility == "diagnostic"
    assert status.paper_positions == ()
    expired = session._status(after_seal + timedelta(seconds=121), "healthy", session.latest_no_trade_reason)
    assert expired.opportunities == ()
    assert "diagnostic setup expired" in expired.no_trade_reason.lower()


def test_both_direction_watch_rule_cannot_carry_a_selected_rule_hash():
    with pytest.raises(ValueError):
        LiveRule(strategy_id="trend_pullback", direction="both", selection_hash="b" * 64)
