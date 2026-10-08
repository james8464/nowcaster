from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.intraday.contracts import InstrumentSpec
from src.intraday.research import IntradayRound, ProspectiveEvidence, _daily_block_lower, assess_prospective

T = datetime(2026, 10, 6, tzinfo=UTC)
INSTRUMENT = InstrumentSpec(
    provider="oanda_practice",
    broker_symbol="DE30_EUR",
    market="germany40",
    product="cfd",
    quote_currency="EUR",
    point_value=Decimal("1"),
)


def protocol():
    return IntradayRound(
        round_id="cfd-round-001",
        instruments=(INSTRUMENT,),
        strategy_ids=("opening_range_15", "opening_range_30", "trend_pullback", "range_reversion"),
        train_start=T - timedelta(days=180),
        validation_start=T - timedelta(days=90),
        sealed_start=T - timedelta(days=30),
        sealed_end=T,
    )


def evidence(**changes):
    values = dict(
        protocol_hash=protocol().identity_hash,
        started_at=T,
        assessed_at=T + timedelta(days=100),
        account_quote_coverage=Decimal("0.999"),
        historical_bar_coverage=Decimal("0.999"),
        material_feed_gaps=0,
        maximum_drawdown=Decimal("0.02"),
        daily_net_pnl=tuple((T + timedelta(days=n), Decimal("8")) for n in range(100)),
        closed_trades=100,
        frozen_rule=True,
        account_specific_quotes=True,
        execution_costs_verified=True,
        currency_conversion_verified=True,
        session_calendar_verified=True,
    )
    return ProspectiveEvidence(**{**values, **changes})


def test_round_identity_is_immutable_and_chronological():
    p = protocol()
    assert p.identity_hash == protocol().identity_hash
    with pytest.raises(ValueError, match="chronological"):
        IntradayRound(**{**p.model_dump(), "sealed_start": p.validation_start})
    with pytest.raises(ValueError, match="duplicate"):
        IntradayRound(**{**p.model_dump(), "instruments": (INSTRUMENT, INSTRUMENT)})


def test_positive_history_cannot_replace_forward_account_quotes():
    result = assess_prospective(protocol(), evidence(account_specific_quotes=False))
    assert not result.supported
    assert "account_quotes_unavailable" in result.reasons
    assert not assess_prospective(protocol(), evidence(closed_trades=99)).supported
    assert not assess_prospective(protocol(), evidence(material_feed_gaps=1)).supported


def test_supported_requires_net_daily_lower_bound_and_risk_gates():
    result = assess_prospective(protocol(), evidence())
    assert result.supported
    assert result.lower_net_daily_pnl > 0
    bad = evidence(daily_net_pnl=tuple((T + timedelta(days=n), Decimal("-5")) for n in range(100)))
    assert not assess_prospective(protocol(), bad).supported
    assert not assess_prospective(protocol(), evidence(maximum_drawdown=Decimal("0.051"))).supported


def test_empty_forward_sample_is_an_assessment_not_a_crash():
    result = assess_prospective(protocol(), evidence(daily_net_pnl=(), closed_trades=0))
    assert not result.supported
    assert "net_lower_bound_nonpositive" in result.reasons
    assert result.lower_net_daily_pnl == 0


def test_missing_cost_or_calendar_provenance_cannot_be_supported():
    for field in ("execution_costs_verified", "currency_conversion_verified", "session_calendar_verified"):
        result = assess_prospective(protocol(), evidence(**{field: False}))
        assert not result.supported
        assert field in result.reasons
    partial = evidence(daily_net_pnl=tuple((T + timedelta(days=n), Decimal("8")) for n in range(99)))
    incomplete = assess_prospective(protocol(), partial)
    assert "daily_ledger_incomplete" in incomplete.reasons
    assert incomplete.lower_net_daily_pnl == 0


def test_daily_lower_resamples_clusters_not_independent_days():
    clustered = (Decimal(4),) * 60 + (Decimal(-5),) * 30
    assert sum(clustered) > 0
    assert _daily_block_lower(clustered) < 0


def test_current_incomplete_day_cannot_improve_forward_lower_bound():
    current_day = evidence(daily_net_pnl=tuple((T + timedelta(days=n), Decimal("8")) for n in range(101)))
    result = assess_prospective(protocol(), current_day)
    assert not result.supported
    assert "daily_ledger_incomplete" in result.reasons
    assert result.lower_net_daily_pnl == 0


def test_round_cannot_weaken_minimum_forward_or_risk_limits():
    baseline = protocol().model_dump()
    for change in (
        {"minimum_forward_days": 1},
        {"minimum_closed_trades": 1},
        {"maximum_drawdown": Decimal("0.5")},
        {"stress_spread_multiple": Decimal("1")},
    ):
        with pytest.raises(ValueError, match="minimum|limit|stress"):
            IntradayRound(**{**baseline, **change})
