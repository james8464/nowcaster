"""Synthetic background research fixtures; never read a live study."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.background_research.contracts import LearningCampaign, LearningCostPolicy, LearningSearchSpace
from src.research.round_two_contracts import (
    ResearchRoundProtocol,
    RoundCandidate,
    RoundObservation,
    WalkForwardSchedule,
)
from src.research.round_two_registry import register_round


def campaign_fixture(tmp_path, *, campaign_id="test", seed=0) -> LearningCampaign:
    protocol = ResearchRoundProtocol.default(
        round_id=campaign_id, starts_at=datetime(2026, 1, 1, tzinfo=UTC)
    ).model_copy(
        update={
            "schedule": WalkForwardSchedule(
                starts_at=datetime(2026, 1, 1, tzinfo=UTC),
                train_days=90,
                validation_days=30,
                sealed_test_days=30,
                step_days=30,
            ),
            "minimum_coverage": Decimal("0.995"),
            "minimum_closed_trades": 100,
        }
    )
    source = register_round(protocol, tmp_path / "sources" / campaign_id)
    return LearningCampaign(
        campaign_id=campaign_id,
        source_directory=source,
        source_protocol_hash=protocol.identity_hash,
        code_hash="a" * 64,
        symbols=protocol.symbols,
        search_spaces=tuple(
            LearningSearchSpace(
                symbol=symbol,
                strategy_id="ema",
                base_parameters={"fast": 5, "slow": 20},
                parameter_grid={"fast": (5, 10)},
                indicators=("close",),
                thresholds=(1.0,),
            )
            for symbol in protocol.symbols
        ),
        cost_policy=LearningCostPolicy.from_protocol(protocol),
        schedule=protocol.schedule,
        seed=seed,
        created_at=datetime(2026, 5, 31, tzinfo=UTC),
    )


def learning_fixture(tmp_path, *, attempts=4, observations_per_day=40, training_count=None, maximum_lag=2):
    """Small real receipts with deliberately insufficient calendar coverage."""
    from src.research.round_two_quality import append_observations

    start = datetime(2026, 1, 1, tzinfo=UTC)
    strategy = "desk_donchian_breakout_1m"
    protocol = (
        ResearchRoundProtocol.default(round_id="learning", starts_at=start)
        .model_copy(
            update={
                "symbols": ("BTCUSDT",),
                "candidates": (RoundCandidate(symbol="BTCUSDT", strategy_id=strategy, strategy_version="1.0.0"),),
                "schedule": WalkForwardSchedule(
                    starts_at=start, train_days=1, validation_days=1, sealed_test_days=1, step_days=1
                ),
                "warmup_minutes": 1,
                "maximum_feature_bars": 20,
            }
        )
        .validated()
    )
    source = register_round(protocol, tmp_path / "source")
    observations = tuple(
        RoundObservation(
            provider="binance",
            feed="spot",
            symbol="BTCUSDT",
            provider_at=start + timedelta(days=day, minutes=i),
            received_at=start + timedelta(days=day, minutes=i, seconds=1),
            available_at=start + timedelta(days=day, minutes=i, seconds=2),
            source_key=f"{day}:{i}",
            open=Decimal("100"),
            high=Decimal("102"),
            low=Decimal("98"),
            close=Decimal("101" if i % 4 < 2 else "99"),
            bid=Decimal("99.99"),
            ask=Decimal("100.01"),
            volume=Decimal("1000"),
        )
        for day in range(3)
        for i in range(training_count if day == 0 and training_count is not None else observations_per_day)
    )
    append_observations(source, protocol, observations)
    campaign = LearningCampaign(
        campaign_id="learning",
        source_directory=source,
        source_protocol_hash=protocol.identity_hash,
        code_hash="a" * 64,
        symbols=protocol.symbols,
        search_spaces=(
            LearningSearchSpace(
                symbol="BTCUSDT",
                strategy_id=strategy,
                base_parameters={"lookback": 2},
                parameter_grid={"lookback": (2, 3)},
                indicators=("close",),
                thresholds=(100.0,),
                maximum_lag=maximum_lag,
            ),
        ),
        cost_policy=LearningCostPolicy.from_protocol(protocol),
        schedule=protocol.schedule,
        seed=7,
        max_attempts_per_batch=attempts,
        created_at=start + timedelta(days=3),
    )
    return campaign, protocol, observations
