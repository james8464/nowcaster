from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.intraday.contracts import ConfirmedBar, InstrumentSpec
from src.intraday.historical import ReplayCosts
from src.intraday.eligibility import CostEvidence, SessionEvidence
from src.intraday.selection import SelectionManifest, run_selection


INSTRUMENT = InstrumentSpec(
    provider="oanda_practice", broker_symbol="DE30_EUR", market="germany40",
    product="cfd", quote_currency="EUR", point_value=Decimal(1),
)


def bar(day):
    start = datetime(2026, 1, day, 8, tzinfo=UTC)
    return ConfirmedBar(
        instrument=INSTRUMENT, start=start, end=start + timedelta(minutes=5),
        available_at=start + timedelta(days=100), source_key=f"historical-{day}",
        bid_open=Decimal(100), bid_high=Decimal(101), bid_low=Decimal(99), bid_close=Decimal(100),
        ask_open=Decimal(101), ask_high=Decimal(102), ask_low=Decimal(100), ask_close=Decimal(101),
    )


def manifest(costs=None):
    evidence = CostEvidence(
        broker_symbol="DE30_EUR", product="cfd", margin_rate=Decimal("0.05"),
        commission_per_unit=Decimal("0.1"), financing_per_unit=Decimal("0.1"),
        slippage_points=Decimal("0.5"), observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        source="https://broker.example/terms", source_kind="broker_terms",
        session=SessionEvidence(weekdays=(0, 1, 2, 3, 4), opens_utc="07:00", closes_utc="20:00"),
    )
    return SelectionManifest(
        round_id="round-2", instruments=(INSTRUMENT,),
        development_end=datetime(2026, 1, 2, tzinfo=UTC),
        validation_end=datetime(2026, 1, 3, tzinfo=UTC),
        sealed_end=datetime(2026, 1, 4, tzinfo=UTC),
        costs={} if costs is None else {"DE30_EUR": costs},
        cost_evidence={} if costs is None else {"DE30_EUR": evidence},
    )


def test_missing_costs_retains_every_development_attempt_and_selects_nothing():
    report = run_selection(manifest(), {"DE30_EUR": (bar(1), bar(2), bar(3))})
    assert len(report.attempts) == 16  # four rules × two directions × two development stages
    assert all(item.rejection_reason == "costs_unverified" for item in report.attempts)
    assert report.selected == ()
    assert all(item.stage != "sealed" for item in report.attempts)
    assert report.cash_baseline == Decimal(0)
    assert report.price_scope == "historical_base_exploratory"


def test_zero_trade_attempts_do_not_become_positive_selection():
    costs = ReplayCosts(
        account_currency="GBP", quote_to_account=Decimal("0.86"),
        slippage_points=Decimal("0.5"), commission_per_unit=Decimal("0.1"),
        financing_per_unit=Decimal("0.1"),
    )
    report = run_selection(manifest(costs), {"DE30_EUR": (bar(1), bar(2), bar(3))})
    assert report.selected == ()
    assert len(report.attempts) == 16
    assert all(item.closed_trades == 0 for item in report.attempts)
    assert all(item.rejection_reason == "insufficient_net_evidence" for item in report.attempts)
