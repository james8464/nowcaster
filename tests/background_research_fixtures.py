"""Synthetic background research fixtures; never read a live study."""

from datetime import UTC, datetime
from decimal import Decimal

from src.background_research.contracts import LearningCampaign, LearningCostPolicy, LearningSearchSpace
from src.research.round_two_contracts import ResearchRoundProtocol, WalkForwardSchedule
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
