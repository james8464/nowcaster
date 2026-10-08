import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.intraday.contracts import InstrumentSpec
from src.intraday.live_service import LiveIndicatorSession, LiveRoundManifest, LiveRule, LiveSessionWindow


NOW = datetime(2026, 10, 8, 8, 0, tzinfo=UTC)


def instrument(symbol, market, product="cfd", currency="EUR"):
    return InstrumentSpec(provider="oanda_practice", broker_symbol=symbol, market=market,
                          product=product, quote_currency=currency, point_value=Decimal(1))


PRODUCTS = (
    instrument("DE30_EUR", "germany40"),
    instrument("SPX500_USD", "us500", currency="USD"),
    instrument("EUR_USD", "eurusd", "margin_fx", "USD"),
    instrument("WTICO_USD", "wti", currency="USD"),
)


def manifest():
    return LiveRoundManifest(
        round_id="round-2", account_feed_hash="a" * 64, instruments=PRODUCTS,
        rules={item.broker_symbol: LiveRule(strategy_id="trend_pullback", direction="long",
                                             selection_hash="b" * 64) for item in PRODUCTS},
        sessions={item.broker_symbol: LiveSessionWindow(opened_at=NOW - timedelta(minutes=5),
                                                        closed_at=NOW + timedelta(hours=8)) for item in PRODUCTS},
    )


def line(symbol, at=NOW, bid="100", ask="101"):
    return json.dumps({"type": "PRICE", "instrument": symbol, "time": at.isoformat(),
                       "tradeable": True, "bids": [{"price": bid}], "asks": [{"price": ask}]})


def test_four_products_are_routed_and_inventory_is_not_a_paper_signal(tmp_path):
    session = LiveIndicatorSession.restore(tmp_path, manifest())
    for item in PRODUCTS:
        status = session.on_event(line(item.broker_symbol), NOW + timedelta(seconds=1))
    assert len(status.markets) == 4
    assert status.opportunities == ()
    assert status.paper_positions == ()
    assert (tmp_path / "quotes.jsonl").exists()
    assert (tmp_path / "summary.json").exists()


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
    next_quote = seal + timedelta(seconds=2)
    session.on_event(line("DE30_EUR", next_quote), next_quote + timedelta(milliseconds=100))
    assert len([event for event in session.journal.events() if event["kind"] == "decision"]) == 1
