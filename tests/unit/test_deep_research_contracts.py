from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.deep_research.contracts import (
    AttemptStatus,
    CandidateAttempt,
    ResearchProtocol,
    RunState,
)

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def _protocol(**changes: object) -> ResearchProtocol:
    values: dict[str, object] = {
        "dataset_hash": "a" * 64,
        "code_hash": "b" * 64,
        "search_space_hash": "c" * 64,
        "cost_policy_hash": "d" * 64,
        "symbol": "BTCUSDT",
        "provider": "binance",
        "feed": "spot",
        "interval": "5m",
        "seed": 42,
        "workers": 4,
        "trial_budget": 500,
        "continuous": False,
        "final_test_start": NOW,
        "created_at": NOW,
    }
    values.update(changes)
    return ResearchProtocol(**values)  # type: ignore[arg-type]


def test_protocol_identity_is_canonical_and_normalizes_scope() -> None:
    first = _protocol(symbol=" btcusdt ", provider=" BINANCE ", feed=" spot ")
    second = _protocol()

    assert first.symbol == "BTCUSDT"
    assert first.provider == "binance"
    assert first.identity == second.identity
    assert len(first.identity) == 64


@pytest.mark.parametrize(
    ("field", "value"),
    [("workers", 0), ("trial_budget", 0), ("seed", True), ("final_test_start", datetime(2026, 8, 24))],
)
def test_protocol_rejects_unsafe_resource_and_time_bounds(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        _protocol(**{field: value})


def test_continuous_protocol_has_no_global_trial_budget() -> None:
    protocol = _protocol(continuous=True, trial_budget=None)
    assert protocol.continuous is True
    assert protocol.trial_budget is None

    with pytest.raises(ValueError, match="continuous"):
        _protocol(continuous=True, trial_budget=20)


def test_candidate_attempt_requires_finite_metrics_and_terminal_chronology() -> None:
    attempt = CandidateAttempt(
        ordinal=1,
        candidate_hash="e" * 64,
        definition={"kind": "rule", "rule": {"operator": "gt"}},
        status=AttemptStatus.SUCCEEDED,
        attempted_at=NOW,
        completed_at=NOW,
        fitness=0.25,
    )
    assert attempt.status is AttemptStatus.SUCCEEDED

    with pytest.raises(ValueError, match="finite"):
        CandidateAttempt(
            ordinal=1,
            candidate_hash="e" * 64,
            definition={},
            status=AttemptStatus.SUCCEEDED,
            attempted_at=NOW,
            completed_at=NOW,
            fitness=float("nan"),
        )

    with pytest.raises(ValueError, match="completed_at"):
        CandidateAttempt(
            ordinal=2,
            candidate_hash="f" * 64,
            definition={},
            status=AttemptStatus.FAILED,
            attempted_at=NOW,
        )


def test_run_and_attempt_states_are_closed_enums() -> None:
    assert {state.value for state in RunState} == {"running", "paused", "completed", "stopped", "failed"}
    assert {state.value for state in AttemptStatus} == {
        "generated",
        "duplicate",
        "invalid",
        "succeeded",
        "failed",
        "interrupted",
    }
