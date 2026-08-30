from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.config.settings import Settings
from src.contextual.eligibility import (
    EligibilityInputs,
    evaluate_asset_eligibility,
    strategy_is_applicable,
)
from src.contextual.types import EligibilityState, StrategyDirection

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)


def _settings_context():
    settings = Settings.load(PROJECT_ROOT, mode="test")
    assert settings.asset_selection is not None
    instrument = next(item for item in settings.instruments.instruments if item.symbol == "BTCUSDT")
    assert instrument.profile is not None
    return settings, instrument, settings.asset_selection.profiles[instrument.profile]


def _eligible_inputs(**changes: object) -> EligibilityInputs:
    _, instrument, _ = _settings_context()
    values = {
        "provider": instrument.provider,
        "feed": instrument.feed,
        "venue": instrument.venue,
        "product": instrument.product,
        "asset_class": instrument.asset_class,
        "profile": instrument.profile,
        "trading_calendar": instrument.trading_calendar,
        "symbol": instrument.symbol,
        "interval": "5m",
        "direction": StrategyDirection.LONG,
        "as_of": NOW,
        "data_through": NOW - timedelta(seconds=5),
        "listing_at": NOW - timedelta(days=1_000),
        "delisting_at": None,
        "trading_status": "active",
        "halted": False,
        "session_state": "continuous",
        "finalized_history_bars": 1_200,
        "coverage": 1.0,
        "sequence_continuous": True,
        "correction_pending": False,
        "median_notional_volume": 50_000_000.0,
        "spread_bps": 2.0,
        "depth_notional": 1_000_000.0,
        "estimated_price_impact_bps": 2.0,
        "participation_rate": 0.001,
        "last_price": 100.0,
        "tick_size": 0.01,
        "lot_size_valid": True,
        "realized_volatility": 0.02,
        "shortable": instrument.shortable,
        "short_mechanism": instrument.short_mechanism,
        "funding_applicable": instrument.funding_applicable,
        "funding_rate_bps": None,
        "borrow_applicable": instrument.borrow_applicable,
        "borrow_fee_bps": None,
        "research_provider": instrument.provider,
        "research_feed": instrument.feed,
        "liquidity_grade": "observed",
        "source_event_watermark": "bar-100",
    }
    values.update(changes)
    return EligibilityInputs(**values)  # type: ignore[arg-type]


def test_spot_short_and_wide_spread_fail_closed() -> None:
    settings, _, policy = _settings_context()
    assert settings.asset_selection is not None
    policy_hash = "a" * 64

    short = evaluate_asset_eligibility(
        _eligible_inputs(direction=StrategyDirection.SHORT), policy, policy_hash
    )
    wide = evaluate_asset_eligibility(_eligible_inputs(spread_bps=11.0), policy, policy_hash)

    assert short.state is EligibilityState.BLOCKED
    assert "direction_not_supported" in short.reasons
    assert wide.state is EligibilityState.BLOCKED
    assert "spread_limit" in wide.reasons
    assert short.quality_score == 0
    assert wide.quality_score == 0


def test_missing_observed_depth_stays_non_executable_watch() -> None:
    _, _, policy = _settings_context()

    evidence = evaluate_asset_eligibility(
        _eligible_inputs(depth_notional=None, liquidity_grade="bar_proxy"), policy, "b" * 64
    )

    assert evidence.state is EligibilityState.WATCH
    assert "observed_depth_required" in evidence.reasons
    assert evidence.quality_score > 0


def test_session_specialist_is_inapplicable_to_continuous_crypto() -> None:
    settings, instrument, policy = _settings_context()
    opening_range = next(
        item for item in settings.strategies.strategies if item.strategy_id == "opening_range_breakout"
    )

    assert not strategy_is_applicable(
        opening_range,
        instrument,
        policy,
        StrategyDirection.LONG,
        "continuous",
        interval="5m",
    )

    crypto_session = next(
        item
        for item in settings.strategies.strategies
        if item.strategy_id == "bitcoin_active_session_momentum"
    )
    assert strategy_is_applicable(
        crypto_session,
        instrument,
        policy,
        StrategyDirection.LONG,
        "continuous",
        interval="15m",
    )
