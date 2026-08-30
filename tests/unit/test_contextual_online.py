from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.contextual.online import (
    ContextualOnlineState,
    attribute_soft_regime_outcome,
    replay_contextual_outcomes,
)

NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)
POSTERIOR = {
    "trend_normal": 0.40,
    "trend_elevated_volatility": 0.20,
    "range_liquid": 0.30,
    "stressed_or_illiquid": 0.10,
}


def outcome() -> dict[str, object]:
    return {
        "outcome_id": "outcome-1",
        "content_hash": "content-1",
        "context_hash": "context-1",
        "source_decision_hash": "decision-1",
        "strategy_id": "ema_adx_trend",
        "decision_timestamp": NOW,
        "outcome_available_at": NOW + timedelta(minutes=5),
        "net_return": 0.002,
        "regime_probabilities": POSTERIOR,
    }


def test_soft_regime_credit_is_conserved_and_replay_idempotent() -> None:
    attributed = attribute_soft_regime_outcome(outcome(), POSTERIOR)
    assert sum(item.credit for item in attributed) == pytest.approx(1.0)
    base = {"ema_adx_trend": 0.5, "rsi_reversal": 0.5}

    once = replay_contextual_outcomes(base, attributed)
    duplicated = replay_contextual_outcomes(base, attributed * 2)

    assert isinstance(once, ContextualOnlineState)
    assert once == duplicated
    assert once.processed_outcome_ids == ("outcome-1",)
    assert once.outcome_watermark == NOW + timedelta(minutes=5)


def test_attribution_rejects_noncausal_or_unnormalized_outcomes() -> None:
    with pytest.raises(ValueError, match="available"):
        attribute_soft_regime_outcome(
            {**outcome(), "outcome_available_at": NOW - timedelta(seconds=1)},
            POSTERIOR,
        )
    with pytest.raises(ValueError, match="normalized"):
        attribute_soft_regime_outcome(outcome(), {**POSTERIOR, "trend_normal": 0.5})
