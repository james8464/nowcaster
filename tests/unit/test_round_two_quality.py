from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.research.round_two_contracts import ResearchRoundProtocol, RoundCandidate, RoundObservation
from src.research.round_two_quality import (
    append_observations,
    eligible_segments,
    load_observations,
    validate_observation,
)
from src.research.round_two_registry import register_round

UTC_T = datetime(2026, 1, 1, tzinfo=UTC)


def registered_round(tmp_path):
    protocol = ResearchRoundProtocol.default(round_id="round-002", starts_at=UTC_T).model_copy(
        update={
            "symbols": ("BTCUSDT",),
            "candidates": (RoundCandidate(symbol="BTCUSDT", strategy_id="ema"),),
        }
    ).validated()
    return protocol, register_round(protocol, tmp_path / "round-002")


def observation(*, at=UTC_T, source_key=None, close="100", available_at=None, **changes):
    return RoundObservation(
        provider="binance",
        feed="spot",
        symbol="BTCUSDT",
        provider_at=at,
        received_at=at,
        available_at=available_at or at,
        source_key=source_key or f"binance:{at.isoformat()}",
        close=Decimal(close) if close is not None else None,
        **changes,
    )


def continuous_bars(*, minutes, start=UTC_T):
    return [observation(at=start + timedelta(minutes=index)) for index in range(minutes)]


def test_conflicting_duplicate_source_key_is_rejected(tmp_path):
    protocol, directory = registered_round(tmp_path)
    first = observation(source_key="binance:42", close="100")
    conflicting = observation(source_key="binance:42", close="101")

    append_observations(directory, protocol, [first])

    with pytest.raises(ValueError, match="conflicting source key"):
        append_observations(directory, protocol, [conflicting])


def test_append_refuses_a_protocol_other_than_the_registered_round(tmp_path):
    protocol, directory = registered_round(tmp_path)
    changed = protocol.model_copy(update={"maximum_spread_bps": Decimal("24")})

    with pytest.raises(ValueError, match="protocol identity"):
        append_observations(directory, changed, [observation()])


def test_gap_and_late_bar_force_warmup_abstention(tmp_path):
    protocol, directory = registered_round(tmp_path)
    append_observations(directory, protocol, continuous_bars(minutes=60))
    append_observations(directory, protocol, [observation(at=UTC_T + timedelta(minutes=62))])

    summary = append_observations(directory, protocol, [])

    assert "continuity_warmup" in summary.reasons_for(UTC_T + timedelta(minutes=63))
    assert "available_after_decision" in validate_observation(
        observation(available_at=UTC_T + timedelta(minutes=2)), UTC_T
    )
    assert eligible_segments(load_observations(directory), protocol) == ()


def test_rejects_unfinalized_quote_and_records_provider_error_as_ineligible(tmp_path):
    protocol, directory = registered_round(tmp_path)
    quote = observation(close=None, bid=Decimal("99"), ask=Decimal("100"))
    with pytest.raises(ValueError, match="finalized"):
        append_observations(directory, protocol, [quote])

    error = observation(close=None, provider_error="upstream timeout")
    summary = append_observations(directory, protocol, [error])
    assert "provider_error" in summary.reasons_for(UTC_T)


def test_eligible_segment_needs_clean_coverage_spread_and_warmup(tmp_path):
    protocol, directory = registered_round(tmp_path)
    bars = continuous_bars(minutes=61)
    append_observations(directory, protocol, bars)

    assert len(eligible_segments(load_observations(directory), protocol)) == 1
    assert append_observations(directory, protocol, []).reasons_for(UTC_T + timedelta(minutes=60)) == ()
