from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from scripts.run_intraday_research import _runtime_status
from src.intraday.contracts import InstrumentSpec, MarketQuote
from src.intraday.paper import FXConversion, PaperExecutionCosts
from src.intraday.runtime import LivePaperRuntime

T = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)
INSTRUMENT = InstrumentSpec(
    provider="oanda_practice",
    broker_symbol="DE30_EUR",
    market="germany40",
    product="cfd",
    quote_currency="EUR",
    point_value=D(1),
)


def quote(seconds: int, bid: str) -> MarketQuote:
    at = T + timedelta(seconds=seconds)
    return MarketQuote(
        instrument=INSTRUMENT,
        account_feed_hash="a" * 64,
        observed_at=at,
        received_at=at + timedelta(milliseconds=100),
        bid=D(bid),
        ask=D(bid) + 2,
        status="tradeable",
        source_key=f"account-quote:{seconds}",
    )


def runtime(directory, *, session_open=T, account_currency="EUR"):
    return LivePaperRuntime(
        directory=directory,
        protocol_hash="b" * 64,
        instrument=INSTRUMENT,
        strategy_id="opening_range_15",
        account_currency=account_currency,
        costs=PaperExecutionCosts(verified=True),
        session_open=session_open,
        session_close=session_open + timedelta(hours=1),
        initial_cash=D("10000"),
        unit_step=D("0.01"),
        maximum_quote_gap=timedelta(seconds=180),
    )


def conversion(at):
    return FXConversion(
        from_currency="EUR",
        to_currency="GBP",
        position_value=D("0.85"),
        account_gain=D("0.84"),
        account_loss=D("0.86"),
        observed_at=at,
    )


def test_foreign_currency_paper_lifecycle_uses_fresh_account_factors(tmp_path):
    worker = runtime(tmp_path / "foreign-paper-study", account_currency="GBP")
    for n, price in enumerate(("100", "100", "101", "104")):
        for offset in (1, 120, 299):
            worker.on_quote(quote(n * 300 + offset, price), conversion(quote(n * 300 + offset, price).received_at))
    worker.on_quote(quote(1201, "104"), conversion(quote(1201, "104").received_at))
    entry_quote = quote(1202, "104")
    opened = worker.on_quote(entry_quote, conversion(entry_quote.received_at))
    assert opened is not None and opened.kind == "opened"
    exit_quote = quote(1260, "130")
    assert worker.on_quote(exit_quote, conversion(entry_quote.received_at)) is None
    assert INSTRUMENT.broker_symbol in worker.account.positions
    closed = worker.on_quote(exit_quote, conversion(exit_quote.received_at))
    assert closed is not None and closed.kind == "closed"
    assert worker.account.equity > D("10000")


def test_open_position_survives_restart_and_closes_from_next_account_quote(tmp_path):
    worker = runtime(tmp_path / "new-paper-study")
    prices = ["100", "100", "101", "104"]
    for n, price in enumerate(prices):
        for offset in (1, 120, 299):
            worker.on_quote(quote(n * 300 + offset, price))
    assert worker.on_quote(quote(1201, "104")) is None
    opened = worker.on_quote(quote(1202, "104"))
    assert opened is not None and opened.kind == "opened"
    assert opened.payload["decided_at"] < opened.payload["opened_at"]
    published = _runtime_status(worker, T + timedelta(seconds=1202), health="healthy", opened=True)
    assert published.opportunities[0].decided_at < published.opportunities[0].entry_at
    assert worker.account.positions[INSTRUMENT.broker_symbol].entry == D("106")
    restored = runtime(tmp_path / "new-paper-study")
    assert restored.account.positions[INSTRUMENT.broker_symbol].entry == D("106")
    assert [event.kind for event in restored.journal.events()].count("session_window") == 1
    assert (
        restored.account.positions[INSTRUMENT.broker_symbol].decided_at
        < restored.account.positions[INSTRUMENT.broker_symbol].opened_at
    )
    closed = restored.on_quote(quote(1260, "130"))
    assert closed is not None and closed.kind == "closed"
    assert not restored.account.positions
    assert restored.account.equity > D("10000")
    assert [event.kind for event in restored.journal.events()].count("opened") == 1
    assert [event.kind for event in restored.journal.events()].count("closed") == 1


def test_stale_quote_produces_no_plan_and_a_gap_event(tmp_path):
    worker = runtime(tmp_path / "new-paper-study")
    stale = quote(1, "100").model_copy(update={"received_at": T + timedelta(seconds=20)})
    worker.on_quote(stale)
    assert worker.account.positions == {}
    assert any(event.kind == "feed_gap" for event in worker.journal.events())


def test_worsened_exit_quote_closes_position_even_when_bar_coverage_gaps(tmp_path):
    worker = runtime(tmp_path / "new-paper-study")
    for n, price in enumerate(("100", "100", "101", "104")):
        for offset in (1, 120, 299):
            worker.on_quote(quote(n * 300 + offset, price))
    worker.on_quote(quote(1201, "104"))
    assert worker.on_quote(quote(1202, "104")).kind == "opened"
    closed = worker.on_quote(quote(1501, "80"))
    assert closed is not None and closed.kind == "closed"
    assert closed.payload["exit_reason"] == "stop"
    assert closed.payload["exit_price"] == "80"


def test_pre_entry_or_other_account_quote_cannot_close_open_paper_position(tmp_path):
    worker = runtime(tmp_path / "new-paper-study")
    for n, price in enumerate(("100", "100", "101", "104")):
        for offset in (1, 120, 299):
            worker.on_quote(quote(n * 300 + offset, price))
    worker.on_quote(quote(1201, "104"))
    assert worker.on_quote(quote(1202, "104")).kind == "opened"
    backdated = quote(1201, "80").model_copy(update={"received_at": T + timedelta(seconds=1203)})
    worker.on_quote(backdated)
    other_account = quote(1204, "80").model_copy(update={"account_feed_hash": "c" * 64})
    worker.on_quote(other_account)
    assert INSTRUMENT.broker_symbol in worker.account.positions
    assert all(event.kind != "closed" for event in worker.journal.events())


def test_next_session_keeps_frozen_study_equity_and_records_session_identity(tmp_path):
    directory = tmp_path / "new-paper-study"
    worker = runtime(directory)
    for n, price in enumerate(("100", "100", "101", "104")):
        for offset in (1, 120, 299):
            worker.on_quote(quote(n * 300 + offset, price))
    worker.on_quote(quote(1201, "104"))
    assert worker.on_quote(quote(1202, "104")).kind == "opened"
    assert worker.on_quote(quote(1260, "130")).kind == "closed"
    retained = worker.account.equity
    next_day = runtime(directory, session_open=T + timedelta(days=1))
    assert next_day.account.equity == retained
    assert [event.kind for event in next_day.journal.events()].count("session_window") == 2


def test_first_quote_just_after_session_close_records_time_exit(tmp_path):
    worker = runtime(tmp_path / "new-paper-study")
    for n, price in enumerate(("100", "100", "101", "104")):
        for offset in (1, 120, 299):
            worker.on_quote(quote(n * 300 + offset, price))
    worker.on_quote(quote(1201, "104"))
    assert worker.on_quote(quote(1202, "104")).kind == "opened"
    closed = worker.on_quote(quote(3601, "104"))
    assert closed is not None and closed.kind == "closed"
    assert closed.payload["exit_reason"] == "time_limit"
    assert not worker.account.positions
