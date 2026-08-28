from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.live_monitor.levels import EmpiricalLevelEvidence, plan_trade_levels, select_empirical_levels
from src.live_monitor.types import Direction, MarketQuote, TradeLevelPolicy
from src.models.trade_outcomes import TradeOutcome

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
POLICY = TradeLevelPolicy(
    atr_multiplier=Decimal("1"),
    maximum_chase_bps=Decimal("10"),
    maximum_stop_atr=Decimal("4"),
    minimum_stop_noise_multiple=Decimal("2"),
    minimum_target_1_r=Decimal("1"),
    minimum_target_2_r=Decimal("1.5"),
    expires_after_bars=3,
)


def quote(*, bid: str = "99.90", ask: str = "100.00", tick: str = "0.01") -> MarketQuote:
    return MarketQuote(
        provider="alpaca",
        feed="iex",
        symbol="AAPL",
        bid=Decimal(bid),
        ask=Decimal(ask),
        last=Decimal("99.95"),
        tick_size=Decimal(tick),
        provider_time=NOW,
        received_at=NOW + timedelta(milliseconds=50),
    )


def test_long_plan_uses_executable_ask_structure_atr_and_supported_targets() -> None:
    plan = plan_trade_levels(
        quote(),
        Direction.LONG,
        atr=Decimal("1"),
        structural_invalidation=Decimal("97"),
        expected_targets=(Decimal("101.5"), Decimal("103"), Decimal("104.5")),
        decision_interval="5m",
        decision_time=NOW,
        policy=POLICY,
    )

    assert plan is not None
    assert (plan.entry_low, plan.entry_high, plan.stop, plan.target_1, plan.target_2) == (
        Decimal("100.00"),
        Decimal("100.10"),
        Decimal("97.00"),
        Decimal("103.00"),
        Decimal("104.50"),
    )
    assert plan.risk_per_unit == Decimal("3.00")
    assert plan.expires_at == NOW + timedelta(minutes=15)


def test_short_plan_rounds_risk_outward_and_labels_crypto_venue_dependency() -> None:
    crypto_quote = quote(bid="100.00", ask="100.10", tick="0.10").model_copy(
        update={"provider": "binance", "feed": "spot", "symbol": "BTCUSDT"}
    )
    plan = plan_trade_levels(
        crypto_quote,
        Direction.SHORT,
        atr=Decimal("1"),
        structural_invalidation=Decimal("103"),
        expected_targets=(Decimal("96.9"), Decimal("95.3")),
        decision_interval="5m",
        decision_time=NOW,
        policy=POLICY,
    )

    assert plan is not None
    assert (plan.entry_low, plan.entry_high, plan.stop, plan.target_1, plan.target_2) == (
        Decimal("99.90"),
        Decimal("100.00"),
        Decimal("103.00"),
        Decimal("96.90"),
        Decimal("95.30"),
    )
    assert plan.venue_note == "Short execution availability is venue-dependent."


def test_planner_abstains_when_spread_noise_stop_or_reward_is_not_feasible() -> None:
    assert (
        plan_trade_levels(
            quote(bid="98", ask="100"),
            Direction.LONG,
            atr=Decimal("0.5"),
            structural_invalidation=Decimal("99"),
            expected_targets=(Decimal("103"), Decimal("104")),
            decision_interval="5m",
            decision_time=NOW,
            policy=POLICY,
        )
        is None
    )


def test_empirical_level_selector_requires_effective_sample_and_positive_lower_edge() -> None:
    base = TradeOutcome(
        provider="test",
        feed="sip",
        symbol="XYZ",
        direction="long",
        decision_timestamp=NOW,
        entry_timestamp=NOW + timedelta(minutes=1),
        exit_timestamp=NOW + timedelta(minutes=2),
        outcome_available_at=NOW + timedelta(minutes=2, seconds=1),
        entry_price=100.0,
        stop_price=99.0,
        target_price=102.0,
        exit_price=102.0,
        risk_distance=1.0,
        target_before_stop=True,
        exit_reason="target",
        gross_return=0.02,
        net_return=0.018,
        maximum_favourable_excursion_r=2.2,
        maximum_adverse_excursion_r=0.4,
        bars_held=1,
    )
    observations = tuple(
        base.model_copy(
            update={
                "decision_timestamp": NOW + timedelta(minutes=index),
                "entry_timestamp": NOW + timedelta(minutes=index + 1),
                "exit_timestamp": NOW + timedelta(minutes=index + 2),
                "outcome_available_at": NOW + timedelta(minutes=index + 2, seconds=1),
                "net_return": 0.012 if index % 3 else 0.006,
                "maximum_favourable_excursion_r": 2.0 + (index % 5) * 0.1,
                "maximum_adverse_excursion_r": 0.4 + (index % 4) * 0.05,
            }
        )
        for index in range(120)
    )

    assert select_empirical_levels(observations[:20], direction=Direction.LONG, minimum_effective_sample=50) is None
    evidence = select_empirical_levels(observations, direction=Direction.LONG, minimum_effective_sample=50)
    assert evidence is not None
    assert evidence.sample_size == 120
    assert evidence.effective_sample_size >= 50
    assert evidence.stop_atr > 0
    assert evidence.target_2_atr > evidence.target_1_atr
    assert evidence.lower_net_edge > 0


def test_planner_uses_sealed_empirical_atr_levels_when_supplied() -> None:
    empirical = EmpiricalLevelEvidence(
        sample_size=120,
        effective_sample_size=100,
        stop_atr=Decimal("1.2"),
        target_1_atr=Decimal("2.0"),
        target_2_atr=Decimal("3.0"),
        success_probability=Decimal("0.65"),
        expected_net_edge=Decimal("0.004"),
        lower_net_edge=Decimal("0.001"),
    )

    plan = plan_trade_levels(
        quote(),
        Direction.LONG,
        atr=Decimal("1"),
        structural_invalidation=Decimal("90"),
        expected_targets=(),
        empirical_evidence=empirical,
        decision_interval="5m",
        decision_time=NOW,
        policy=POLICY,
    )

    assert plan is not None
    assert plan.stop == Decimal("98.80")
    assert plan.target_1 == Decimal("102.00")
    assert plan.target_2 == Decimal("103.00")

    assert (
        plan_trade_levels(
            quote(),
            Direction.LONG,
            atr=Decimal("1"),
            structural_invalidation=Decimal("90"),
            expected_targets=(Decimal("103"), Decimal("104")),
            decision_interval="5m",
            decision_time=NOW,
            policy=POLICY,
        )
        is None
    )

    assert (
        plan_trade_levels(
            quote(),
            Direction.LONG,
            atr=Decimal("1"),
            structural_invalidation=Decimal("97"),
            expected_targets=(Decimal("101"), Decimal("102")),
            decision_interval="5m",
            decision_time=NOW,
            policy=POLICY,
        )
        is None
    )
