from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.intraday.contracts import InstrumentSpec
from src.intraday.eligibility import CostEvidence, SessionEvidence, evaluate_product
from src.intraday.paper import FXConversion

NOW = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


def product(symbol="DE30_EUR", market="germany40", kind="cfd", currency="EUR"):
    return InstrumentSpec(
        provider="oanda_practice", broker_symbol=symbol, market=market,
        product=kind, quote_currency=currency, point_value=Decimal("1"),
    )


def row(name="DE30_EUR", kind="CFD", margin="0.05"):
    return {"name": name, "type": kind, "displayName": "Germany 30", "marginRate": margin}


def evidence(session_end=20, conversion_fee=Decimal("0.01")):
    return CostEvidence(
        broker_symbol="DE30_EUR", product="cfd", margin_rate=Decimal("0.05"),
        commission_per_unit=Decimal("0"), financing_per_unit=Decimal("0.10"),
        slippage_points=Decimal("0.5"), conversion_fee_fraction=conversion_fee,
        observed_at=NOW - timedelta(days=1),
        source="https://broker.example/terms/de30", source_kind="broker_terms",
        session=SessionEvidence(weekdays=(0, 1, 2, 3, 4), opens_utc="07:00", closes_utc=f"{session_end:02d}:00"),
    )


def fx(at=NOW):
    return FXConversion(from_currency="EUR", to_currency="GBP", rate=Decimal("0.86"), observed_at=at)


def test_inventory_alone_cannot_enable_paper_entry():
    result = evaluate_product(product(), row(), None, fx(), NOW)
    assert not result.paper_eligible
    assert "cost_evidence_missing" in result.reasons
    assert result.product_label == "Germany 30"


def test_exact_symbol_and_broker_type_are_required():
    wrong_symbol = evaluate_product(product(), row(name="DE40_EUR"), evidence(), fx(), NOW)
    wrong_type = evaluate_product(product(), row(kind="CURRENCY"), evidence(), fx(), NOW)
    assert "broker_identity_mismatch" in wrong_symbol.reasons
    assert "broker_identity_mismatch" in wrong_type.reasons


def test_session_and_conversion_fail_closed():
    assert "session_closed" in evaluate_product(product(), row(), evidence(session_end=8), fx(), NOW).reasons
    assert "currency_conversion_unavailable" in evaluate_product(product(), row(), evidence(), None, NOW).reasons
    stale_fx = evaluate_product(product(), row(), evidence(), fx(NOW - timedelta(minutes=1)), NOW)
    assert "currency_conversion_unavailable" in stale_fx.reasons


def test_margin_change_blocks_old_terms():
    result = evaluate_product(product(), row(margin="0.10"), evidence(), fx(), NOW)
    assert not result.paper_eligible
    assert "margin_rate_changed" in result.reasons
    assert result.broker_margin_rate == Decimal("0.10")


def test_attested_current_terms_allow_paper_eligibility_but_not_profitability():
    result = evaluate_product(product(), row(), evidence(), fx(), NOW)
    assert result.paper_eligible
    assert result.reasons == ()
    assert result.costs is not None
    assert result.broker_margin_rate == Decimal("0.05")
    assert result.session_open


def test_foreign_currency_product_cannot_enter_without_source_backed_conversion_fee():
    result = evaluate_product(product(), row(), evidence(conversion_fee=None), fx(), NOW)
    assert not result.paper_eligible
    assert "currency_conversion_fee_unverified" in result.reasons
