"""Acceptance boundary for the unqualified OANDA practice paper lane."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

from src.intraday.contracts import InstrumentSpec, MarketQuote
from src.intraday.experimental_report import build_experimental_report
from src.intraday.experimental_round import ExperimentalRoundManifest
from src.intraday.experimental_service import ExperimentalPaperService
from src.intraday.paper import FXConversion, PaperExecutionCosts

START = datetime(2026, 10, 20, 7, tzinfo=UTC)
PRODUCT = InstrumentSpec(
    provider="oanda_practice",
    broker_symbol="DE30_EUR",
    market="germany40",
    product="cfd",
    quote_currency="EUR",
    point_value=D(1),
)


def _manifest() -> ExperimentalRoundManifest:
    return ExperimentalRoundManifest(
        round_id="de30-2026-10-20-a",
        cohort_id="de30-or15-long-v1",
        account_feed_hash="a" * 64,
        instrument=PRODUCT,
        strategy_id="opening_range_15",
        direction="long",
        session_open=START,
        session_close=START + timedelta(hours=13),
        calendar_hash="c" * 64,
        costs=PaperExecutionCosts(
            verified=True, slippage_points=D("0.5"), commission_per_unit=D("0.1"), financing_per_unit=D("0.01")
        ),
        cost_source="fixture-only-not-broker-evidence",
        cost_source_hash="d" * 64,
        verified_at=START - timedelta(days=1),
        account_currency="GBP",
        margin_rate=D("0.05"),
        unit_step=D("0.01"),
        initial_paper_cash=D("10000"),
        code_hash="b" * 64,
    )


def _quote(second: int, bid: str) -> MarketQuote:
    at = START + timedelta(seconds=second)
    return MarketQuote(
        instrument=PRODUCT,
        account_feed_hash="a" * 64,
        observed_at=at,
        received_at=at + timedelta(milliseconds=100),
        bid=D(bid),
        ask=D(bid) + 2,
        status="tradeable",
        source_key=f"quote-{second}",
    )


def _conversion(quote: MarketQuote) -> FXConversion:
    return FXConversion(
        from_currency="EUR",
        to_currency="GBP",
        position_value=D("0.85"),
        account_gain=D("0.84"),
        account_loss=D("0.86"),
        observed_at=quote.received_at,
    )


def _warm(service: ExperimentalPaperService) -> None:
    for n, bid in enumerate(("100", "100", "101", "104")):
        for second in range(n * 300 + 1, (n + 1) * 300, 10):
            quote = _quote(second, bid)
            service.on_quote(quote, conversion=_conversion(quote))
    quote = _quote(1201, "104")
    service.on_quote(quote, conversion=_conversion(quote))


def test_stream_to_closed_ticket_to_report_without_orders(tmp_path):
    item = _manifest()
    service = ExperimentalPaperService(item, tmp_path)
    _warm(service)
    planned_quote = _quote(1202, "104")
    assert service.on_quote(planned_quote, conversion=_conversion(planned_quote)).kind == "planned"
    entry = _quote(1203, "104")
    assert service.on_quote(entry, conversion=_conversion(entry)).kind == "opened"
    ticket = service.status(entry.received_at).paper_ticket
    assert ticket is not None and ticket["qualification"] == "unqualified"
    assert {
        "entry",
        "stop",
        "target",
        "units",
        "effective_leverage",
        "margin_estimate_gbp",
        "estimated_roundtrip_cost_gbp",
        "cost_source",
    } <= ticket.keys()
    exit_quote = _quote(1260, "130")
    assert service.on_quote(exit_quote, conversion=_conversion(exit_quote)).kind == "closed"
    report = build_experimental_report(item, service.runtime.journal, service.session, as_of=exit_quote.received_at)
    assert report.qualification == "unqualified"
    assert report.performance.closed_trades == 1
    assert (
        report.performance.gross_pnl_gbp
        - report.performance.commission_gbp
        - report.performance.financing_gbp
        - report.performance.conversion_fee_gbp
        == report.performance.net_pnl_gbp
    )
    assert service.status(exit_quote.received_at).paper_ticket is None


def test_future_quote_mutation_keeps_prior_decision_hashes(tmp_path):
    first = ExperimentalPaperService(_manifest(), tmp_path / "first")
    second = ExperimentalPaperService(_manifest(), tmp_path / "second")
    for service in (first, second):
        _warm(service)
        quote = _quote(1202, "104")
        service.on_quote(quote, conversion=_conversion(quote))
    prior_first = tuple(event.record_hash for event in first.runtime.journal.events())
    prior_second = tuple(event.record_hash for event in second.runtime.journal.events())
    assert prior_first == prior_second
    for service, price in ((first, "104"), (second, "80")):
        quote = _quote(1203, price)
        service.on_quote(quote, conversion=_conversion(quote))
    assert tuple(event.record_hash for event in first.runtime.journal.events()[: len(prior_first)]) == prior_first
    assert tuple(event.record_hash for event in second.runtime.journal.events()[: len(prior_second)]) == prior_second


def test_unknown_cost_or_missing_conversion_never_opens(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="cost basis"):
        ExperimentalRoundManifest.model_validate(
            _manifest().model_dump() | {"costs": PaperExecutionCosts(verified=False)}
        )
    service = ExperimentalPaperService(_manifest(), tmp_path)
    _warm(service)
    quote = _quote(1202, "104")
    service.on_quote(quote, conversion=_conversion(quote))
    entry = _quote(1203, "104")
    result = service.on_quote(entry, conversion=None)
    assert result is not None and result.kind == "no_trade"
    assert not service.runtime.account.positions


def test_experimental_pnl_cannot_select_historical_rule():
    root = Path(__file__).resolve().parents[2]
    helper = (root / "scripts/intraday_service_entry.py").read_text()
    service = (root / "src/intraday/experimental_service.py").read_text()
    for source in (helper, service):
        assert "ResearchCatalog" not in source
        assert "record_attempt(" not in source
    assert "paper_eligible" not in service


def test_helper_has_no_order_or_live_account_routes():
    root = Path(__file__).resolve().parents[2]
    helper = (root / "scripts/intraday_service_entry.py").read_text()
    feed = (root / "src/intraday/oanda_practice.py").read_text()
    assert "api-fxtrade.oanda.com" not in helper + feed
    assert "stream-fxtrade.oanda.com" not in helper + feed
    assert "/orders" not in helper + feed
    assert "create_order" not in helper + feed


def test_operations_docs_disclose_model_and_registration_boundary():
    root = Path(__file__).resolve().parents[2]
    guide = (root / "docs/research/live-paper-signals.md").read_text()
    assert "Experimental OANDA practice paper lane" in guide
    assert "explicitly registered for a future session" in guide
    assert "actual broker commission" in guide
    assert "conservative_model" in guide
