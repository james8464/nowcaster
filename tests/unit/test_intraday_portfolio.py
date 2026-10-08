from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.intraday.contracts import InstrumentSpec, MarketQuote
from src.intraday.eligibility import CostEvidence, SessionEvidence, evaluate_product
from src.intraday.paper import FXConversion
from src.intraday.portfolio import LivePaperPortfolio
from src.intraday.strategies import SetupDecision


T = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
INSTRUMENT = InstrumentSpec(provider="oanda_practice", broker_symbol="DE30_EUR", market="germany40",
                            product="cfd", quote_currency="EUR", point_value=Decimal(1))


def quote(at=T + timedelta(seconds=1), bid="100", ask="101"):
    return MarketQuote(instrument=INSTRUMENT, account_feed_hash="a" * 64,
                       observed_at=at, received_at=at, bid=Decimal(bid), ask=Decimal(ask),
                       status="tradeable", source_key=f"quote:{at}:{bid}:{ask}")


def conversion(at):
    return FXConversion(from_currency="EUR", to_currency="GBP", rate=Decimal("0.86"), observed_at=at)


def eligibility(at=T):
    costs = CostEvidence(
        broker_symbol="DE30_EUR", product="cfd", margin_rate=Decimal("0.05"),
        commission_per_unit=Decimal("0.1"), financing_per_unit=Decimal("0.1"),
        slippage_points=Decimal("0.5"), observed_at=T,
        source="https://broker.example/terms", source_kind="broker_terms",
        session=SessionEvidence(weekdays=(0, 1, 2, 3, 4), opens_utc="07:00", closes_utc="20:00"),
    )
    return evaluate_product(INSTRUMENT, {"name": "DE30_EUR", "type": "CFD", "displayName": "Germany 30",
                                          "marginRate": "0.05"}, costs, conversion(at), at)


def plan(direction="long"):
    return SetupDecision(
        status="ready", strategy_id="trend_pullback", direction=direction, reason="confirmed trend pullback",
        decision_at=T, entry_at=T + timedelta(milliseconds=500), entry=Decimal("101" if direction == "long" else "100"),
        stop=Decimal("96" if direction == "long" else "105"),
        target=Decimal("111" if direction == "long" else "90"),
        exit_by=T + timedelta(hours=1), estimated_roundtrip_cost=Decimal(1), evidence_hash="b" * 64,
    )


def test_complete_ticket_and_adverse_stop_retains_net_loss(tmp_path):
    portfolio = LivePaperPortfolio(tmp_path, "c" * 64, initial_cash=Decimal("10000"))
    opened = portfolio.on_decision(INSTRUMENT, plan(), quote(), eligibility(T))
    assert opened.kind == "opened"
    assert opened.payload["broker_symbol"] == "DE30_EUR"
    assert opened.payload["direction"] == "long"
    assert Decimal(opened.payload["effective_leverage"]) <= 1
    assert Decimal(opened.payload["margin_estimate_gbp"]) > 0
    assert Decimal(opened.payload["notional_gbp"]) > 0
    assert opened.payload["stop"] == "96"
    assert opened.payload["target"] == "111"
    closed = portfolio.on_quote(quote(T + timedelta(minutes=1), "94", "95"), conversion(T + timedelta(minutes=1)))
    assert closed is not None and closed.kind == "closed"
    assert Decimal(closed.payload["net_pnl_gbp"]) < 0
    assert closed.payload["exit_reason"] == "stop"


def test_missing_conversion_or_costs_rejects_entry_without_a_position(tmp_path):
    portfolio = LivePaperPortfolio(tmp_path, "c" * 64, initial_cash=Decimal("10000"))
    bad = eligibility(T).model_copy(update={"conversion_rate": None,
                                             "reasons": ("currency_conversion_unavailable",)})
    result = portfolio.on_decision(INSTRUMENT, plan(), quote(), bad)
    assert result.kind == "no_trade"
    assert portfolio.positions == {}


def test_restart_retains_unresolved_position_and_does_not_make_a_fill(tmp_path):
    portfolio = LivePaperPortfolio(tmp_path, "c" * 64, initial_cash=Decimal("10000"))
    portfolio.on_decision(INSTRUMENT, plan("short"), quote(), eligibility(T))
    restarted = LivePaperPortfolio(tmp_path, "c" * 64, initial_cash=Decimal("10000"))
    assert "DE30_EUR" in restarted.positions
    assert restarted.on_quote(quote(T + timedelta(minutes=2), "90", "91"), None) is None
    assert "DE30_EUR" in restarted.positions
