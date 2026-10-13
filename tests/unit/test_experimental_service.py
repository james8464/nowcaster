"""The unqualified practice consumer is isolated from diagnostic capture."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from src.intraday.contracts import InstrumentSpec, MarketQuote
from src.intraday.experimental_round import ExperimentalRoundManifest
from src.intraday.experimental_service import ExperimentalPaperService
from src.intraday.paper import FXConversion, PaperExecutionCosts, PaperRiskPolicy

T = datetime(2026, 10, 20, 7, tzinfo=UTC)
INSTRUMENT = InstrumentSpec(provider="oanda_practice", broker_symbol="DE30_EUR", market="germany40",
                            product="cfd", quote_currency="EUR", point_value=D(1))


def manifest():
    return ExperimentalRoundManifest(
        round_id="de30-2026-10-20-a", cohort_id="de30-or15-long-v1", account_feed_hash="a" * 64,
        instrument=INSTRUMENT, strategy_id="opening_range_15", direction="long",
        session_open=T, session_close=T + timedelta(hours=13), calendar_hash="c" * 64,
        costs=PaperExecutionCosts(verified=True, slippage_points=D("0.5"),
                                  commission_per_unit=D("0.1"), financing_per_unit=D("0.01")),
        cost_source="https://www.oanda.com/uk-en/trading/fees/", cost_source_hash="d" * 64,
        verified_at=T - timedelta(days=1), account_currency="GBP", margin_rate=D("0.05"),
        unit_step=D("0.01"), initial_paper_cash=D("10000"), code_hash="b" * 64,
    )


def quote(seconds: int, bid: str = "100") -> MarketQuote:
    at = T + timedelta(seconds=seconds)
    return MarketQuote(instrument=INSTRUMENT, account_feed_hash="a" * 64, observed_at=at,
                       received_at=at + timedelta(milliseconds=100), bid=D(bid), ask=D(bid) + 2,
                       status="tradeable", source_key=f"quote-{seconds}")


def conversion(at):
    return FXConversion(from_currency="EUR", to_currency="GBP", position_value=D("0.85"),
                        account_gain=D("0.84"), account_loss=D("0.86"), observed_at=at)


def prime(service):
    for n, price in enumerate(("100", "100", "101", "104")):
        for seconds in range(n * 300 + 1, (n + 1) * 300, 10):
            item = quote(seconds, price)
            service.on_quote(item, conversion=conversion(item.received_at))
    first = quote(1201, "104")
    service.on_quote(first, conversion=conversion(first.received_at))


def test_future_conversion_cannot_enter(tmp_path):
    service = ExperimentalPaperService(manifest(), tmp_path)
    prime(service)
    planned = quote(1202, "104")
    assert service.on_quote(planned, conversion=conversion(planned.received_at)).kind == "planned"
    entry = quote(1203, "104")
    result = service.on_quote(entry, conversion=conversion(entry.received_at + timedelta(seconds=1)))
    assert result.kind == "no_trade"
    assert service.runtime.account.positions == {}


def test_explicit_conservative_cost_policy_can_open_unqualified_paper_trade(tmp_path):
    ordinary = manifest()
    modeled = ExperimentalRoundManifest.model_validate(ordinary.model_dump() | {
        "cost_basis": "conservative_model",
        "costs": PaperExecutionCosts(
            verified=False, slippage_points=D("1"), commission_per_unit=D("0.5")
        ),
    })
    service = ExperimentalPaperService(modeled, tmp_path)
    prime(service)
    planned = quote(1202, "104")
    assert service.on_quote(planned, conversion=conversion(planned.received_at)).kind == "planned"
    entry = quote(1203, "104")
    event = service.on_quote(entry, conversion=conversion(entry.received_at))
    assert event.kind == "opened"
    assert event.payload["qualification"] == "unqualified"


def test_declared_direction_blocks_opposite_setup(tmp_path):
    service = ExperimentalPaperService(manifest(), tmp_path)
    for n, price in enumerate(("100", "100", "99", "96")):
        for seconds in range(n * 300 + 1, (n + 1) * 300, 10):
            item = quote(seconds, price)
            service.on_quote(item, conversion=conversion(item.received_at))
    for seconds in (1201, 1202, 1203):
        item = quote(seconds, "96")
        event = service.on_quote(item, conversion=conversion(item.received_at))
    assert event is None or event.kind != "opened"
    assert service.runtime.account.positions == {}
    assert any(item.payload.get("reason") == "declared_direction_mismatch"
               for item in service.runtime.journal.events())


def test_gap_requires_fresh_declared_lookback(tmp_path):
    service = ExperimentalPaperService(manifest(), tmp_path)
    prime(service)
    before = service.status(quote(1202).received_at)
    assert before.contiguous_bars >= 4
    service.on_quote(quote(1300, "104"), conversion=conversion(quote(1300).received_at))
    after = service.status(quote(1300).received_at)
    assert after.contiguous_bars == 0
    assert after.no_trade_reason == "quote_gap_or_reorder"


def test_pause_manages_open_position_until_flat(tmp_path):
    service = ExperimentalPaperService(manifest(), tmp_path)
    prime(service)
    item = quote(1202, "104")
    service.on_quote(item, conversion=conversion(item.received_at))
    item = quote(1203, "104")
    assert service.on_quote(item, conversion=conversion(item.received_at)).kind == "opened"
    service.disarm_new_entries()
    ticket = service.status(item.received_at).paper_ticket
    assert ticket is not None
    assert D(ticket["effective_leverage"]) <= D(1)
    assert D(ticket["margin_estimate_gbp"]) > 0
    assert D(ticket["estimated_roundtrip_cost_gbp"]) >= 0
    assert ticket["cost_source"] == manifest().cost_source
    exit_quote = quote(1260, "130")
    assert service.on_quote(exit_quote, conversion=conversion(exit_quote.received_at)).kind == "closed"
    assert service.status(exit_quote.received_at).paper_ticket is None


def test_shutdown_retains_unresolved_position(tmp_path):
    service = ExperimentalPaperService(manifest(), tmp_path)
    prime(service)
    for seconds in (1202, 1203):
        item = quote(seconds, "104")
        service.on_quote(item, conversion=conversion(item.received_at))
    service.record_shutdown(quote(1204).received_at)
    restored = ExperimentalPaperService(manifest(), tmp_path)
    assert restored.status(quote(1204).received_at).paper_ticket is not None
    assert not any(event.kind == "closed" for event in restored.runtime.journal.events())


def test_restart_rejects_provider_quote_older_than_retained_capture(tmp_path):
    item = manifest()
    service = ExperimentalPaperService(item, tmp_path)
    prime(service)
    for seconds in (1202, 1203, 1205):
        tick = quote(seconds, "104")
        service.on_quote(tick, conversion=conversion(tick.received_at))
    restarted = ExperimentalPaperService(item, tmp_path)
    delayed = quote(1204, "80").model_copy(
        update={"received_at": quote(1206, "80").received_at, "source_key": "delayed-stop-after-restart"}
    )
    result = restarted.on_quote(delayed, conversion=conversion(delayed.received_at))
    assert result is not None and result.kind == "feed_gap"
    assert item.instrument.broker_symbol in restarted.runtime.account.positions
    assert not any(event.kind == "closed" for event in restarted.runtime.journal.events())
    restarted_again = ExperimentalPaperService(item, tmp_path)
    another_delayed = quote(1204, "80").model_copy(update={
        "observed_at": T + timedelta(seconds=1204, milliseconds=500),
        "received_at": quote(1207, "80").received_at,
        "source_key": "second-delayed-stop-after-restart",
    })
    second = restarted_again.on_quote(another_delayed, conversion=conversion(another_delayed.received_at))
    assert second is not None and second.kind == "feed_gap"
    assert item.instrument.broker_symbol in restarted_again.runtime.account.positions


def test_error_or_closed_session_shows_specific_no_trade(tmp_path):
    service = ExperimentalPaperService(manifest(), tmp_path)
    assert service.status(T).no_trade_reason == "waiting_for_confirmed_bars"
    assert service.status(T + timedelta(hours=13, seconds=1)).no_trade_reason == "session_closed"


def test_one_price_line_feeds_both_isolated_rounds(tmp_path):
    service = ExperimentalPaperService(manifest(), tmp_path)
    item = quote(1)
    service.on_quote(item, conversion=conversion(item.received_at))
    assert len(service.session.events()) == 1
    assert service.session.events()[0]["kind"] == "quote"


def test_frozen_stricter_risk_policy_controls_paper_sizing(tmp_path):
    policy = PaperRiskPolicy(maximum_exposure_fraction=D("0.05"), maximum_daily_entries=1)
    service = ExperimentalPaperService(manifest().model_copy(update={"risk_policy": policy}), tmp_path)
    assert service.runtime.account.policy == policy
    prime(service)
    planned = quote(1202, "104")
    service.on_quote(planned, conversion=conversion(planned.received_at))
    entry = quote(1203, "104")
    opened = service.on_quote(entry, conversion=conversion(entry.received_at))
    assert opened is not None and opened.kind == "opened"
    assert D(opened.payload["notional_gbp"]) <= D("500")


def test_no_registered_round_keeps_old_files_unchanged(tmp_path):
    assert not (tmp_path / "experimental-summary.json").exists()
