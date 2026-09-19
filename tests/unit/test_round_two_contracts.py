from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.research.round_two_contracts import (
    ResearchRoundProtocol,
    RoundCandidate,
    RoundObservation,
    RoundSource,
    WalkForwardSchedule,
)
from src.research.round_two_registry import load_round_protocol, register_round

UTC_START = datetime(2026, 1, 1, tzinfo=UTC)


def test_protocol_hash_is_stable_and_changed_restart_is_refused(tmp_path):
    """Would fail if registration overwrote an existing protocol manifest."""
    protocol = ResearchRoundProtocol.default(round_id="round-002", starts_at=UTC_START)

    directory = register_round(protocol, tmp_path / "round-002")

    assert load_round_protocol(directory).identity_hash == protocol.identity_hash
    changed = protocol.model_copy(update={"maximum_spread_bps": Decimal("24")})
    with pytest.raises(ValueError, match="protocol identity"):
        register_round(changed, directory)


def test_spot_protocol_rejects_short_candidate_and_unknown_symbol():
    """Would fail if a spot protocol could silently become short-capable."""
    protocol = ResearchRoundProtocol.default(round_id="round-002", starts_at=UTC_START)

    with pytest.raises(ValueError, match="spot direction"):
        protocol.model_copy(
            update={"candidates": (RoundCandidate(symbol="BTCUSDT", strategy_id="ema", direction="short"),)}
        ).validated()
    with pytest.raises(ValueError, match="supported symbols"):
        protocol.model_copy(update={"symbols": ("SOLUSDT",)}).validated()


def test_default_protocol_is_frozen_and_binds_source_cost_and_schedule_identity():
    """Would fail if future source, cost, or sealed-window edits reused a round identity."""
    protocol = ResearchRoundProtocol.default(round_id="round-002", starts_at=UTC_START)

    assert protocol.symbols == ("BTCUSDT", "ETHUSDT")
    assert protocol.source.provider == "binance"
    with pytest.raises(ValidationError):
        protocol.round_id = "round-003"
    assert protocol.identity_hash != protocol.model_copy(
        update={"source": RoundSource(provider="binance", feed="spot", revision="public-v2")}
    ).identity_hash


def test_observation_preserves_immutable_source_key_and_requires_protocol_source():
    """Would fail if ingestion could alter a provider's immutable evidence identifier."""
    protocol = ResearchRoundProtocol.default(round_id="round-002", starts_at=UTC_START)
    observation = RoundObservation(
        provider="binance",
        feed="spot",
        symbol="BTCUSDT",
        provider_at=UTC_START,
        received_at=UTC_START,
        available_at=UTC_START,
        source_key="Binance:CaseSensitiveKey",
        close=Decimal("100"),
    )

    assert observation.source_key == "Binance:CaseSensitiveKey"
    assert observation.validate_for(protocol) is observation
    assert protocol.identity_hash != protocol.model_copy(update={"fee_bps": Decimal("11")}).identity_hash
    assert protocol.identity_hash != protocol.model_copy(
        update={
            "schedule": WalkForwardSchedule(
                starts_at=UTC_START + timedelta(days=1),
                train_days=90,
                validation_days=30,
                sealed_test_days=30,
                step_days=30,
            )
        }
    ).identity_hash
