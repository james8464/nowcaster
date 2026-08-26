from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.live_monitor.evidence import SealedCohort, SealedComponent, evaluate_sealed_cohort
from src.live_monitor.types import Direction, MarketBar, MarketQuote
from src.strategies.types import BarInterval, StrategyFamily, StrategySpec

NOW = datetime(2026, 8, 26, 14, tzinfo=UTC)


def component(strategy_id: str, weight: str) -> SealedComponent:
    parameters = (
        {"fast_period": 2, "slow_period": 3, "signal_period": 2}
        if strategy_id == "macd_histogram_trend"
        else {"fast_period": 2, "slow_period": 3, "adx_period": 2, "adx_threshold": 0}
    )
    spec = StrategySpec(
        strategy_id=strategy_id,
        family=StrategyFamily.TREND,
        version="1.0.0",
        intervals=(BarInterval.FIVE_MINUTES,),
        warmup_bars=3,
        parameters=parameters,
    )
    return SealedComponent(
        spec=spec,
        strategy_version=spec.deterministic_version,
        weight=Decimal(weight),
        promoted=True,
        causal_audit_passed=True,
    )


def bars(count: int = 8) -> tuple[MarketBar, ...]:
    result = []
    for index in range(count):
        start = NOW + timedelta(minutes=5 * index)
        close = Decimal(100 + index)
        result.append(
            MarketBar(
                provider="alpaca",
                feed="iex",
                symbol="AAPL",
                interval="5m",
                start=start,
                end=start + timedelta(minutes=5),
                available_at=start + timedelta(minutes=5),
                received_at=start + timedelta(minutes=5),
                open=close - Decimal("0.5"),
                high=close + Decimal("0.5"),
                low=close - Decimal("1"),
                close=close,
                volume=Decimal("1000"),
                finalized=True,
                revision=0,
            )
        )
    return tuple(result)


def quote() -> MarketQuote:
    at = bars()[-1].end
    return MarketQuote(
        provider="alpaca",
        feed="iex",
        symbol="AAPL",
        bid=Decimal("106.9"),
        ask=Decimal("107.0"),
        last=Decimal("106.95"),
        tick_size=Decimal("0.01"),
        provider_time=at,
        received_at=at,
    )


def cohort(**updates) -> SealedCohort:
    values = {
        "provider": "alpaca",
        "feed": "iex",
        "dataset_hash": "d" * 64,
        "symbol": "AAPL",
        "interval": "5m",
        "mode": "frozen",
        "sealed_direction": Direction.LONG,
        "sealed_probability": Decimal("0.68"),
        "sealed_expected_net_edge": Decimal("0.002"),
        "components": (
            component("macd_histogram_trend", "0.6"),
            component("ema_adx_trend", "0.4"),
        ),
    }
    values.update(updates)
    return SealedCohort(**values)


def test_live_evidence_recalculates_only_current_causal_component_signals() -> None:
    evidence = evaluate_sealed_cohort(cohort(), bars(), quote())

    assert evidence.direction is Direction.LONG
    assert evidence.breadth == 2
    assert evidence.promoted is True
    assert evidence.no_repaint_passed is True
    assert evidence.calibration_status == "calibrated"
    assert evidence.economic_evidence_status == "authenticated"
    assert evidence.data_through == bars()[-1].end


def test_live_evidence_fails_closed_when_version_or_sealed_direction_does_not_match() -> None:
    bad = component("macd_histogram_trend", "1").model_copy(update={"strategy_version": "wrong"})
    evidence = evaluate_sealed_cohort(
        cohort(sealed_direction=Direction.SHORT, components=(bad, component("ema_adx_trend", "1"))),
        bars(),
        quote(),
    )

    assert evidence.calibration_status == "unavailable"
    assert "strategy_version_mismatch" in evidence.reasons
    assert "live_direction_not_covered_by_sealed_calibration" in evidence.reasons
