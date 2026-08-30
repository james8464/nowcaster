from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from src.learning.search import (
    ContextualCandidate,
    ContextualLearningExperiment,
    ContextualSearchSpace,
    _maximum_drawdown,
    evaluate_contextual_candidate,
    generate_contextual_candidates,
)

DATASET = "d" * 64
PROTOCOL = "a" * 64
AS_OF = datetime(2026, 8, 30, 12, tzinfo=UTC)


def _outcomes(count: int = 90) -> pd.DataFrame:
    rows = []
    start = AS_OF - timedelta(minutes=5 * (count + 2))
    for index in range(count):
        decision = start + timedelta(minutes=5 * index)
        for strategy_index, strategy_id in enumerate(("trend", "reversal", "breakout")):
            edge = 0.0012 if strategy_id == "trend" else 0.0002 - strategy_index * 0.0001
            rows.append(
                {
                    "outcome_id": f"{index}-{strategy_id}",
                    "strategy_id": strategy_id,
                    "symbol": "BTCUSDT",
                    "asset_class": "crypto",
                    "profile": "crypto_major_spot",
                    "direction": "long",
                    "decision_timestamp": decision,
                    "outcome_available_at": decision + timedelta(minutes=4),
                    "net_return": edge + math.sin(index / 7) * 0.0002,
                    "eligibility_quality": 0.98,
                    "regime_trend_normal": 0.70,
                    "regime_trend_elevated_volatility": 0.10,
                    "regime_range_liquid": 0.15,
                    "regime_stressed_or_illiquid": 0.05,
                }
            )
    return pd.DataFrame(rows)


def _experiment(**updates: object) -> ContextualLearningExperiment:
    values = {
        "dataset_hash": DATASET,
        "protocol_hash": PROTOCOL,
        "as_of": AS_OF,
        "sealed_final_start": AS_OF + timedelta(microseconds=1),
        "outer_validation_blocks": 3,
        "minimum_train_timestamps": 30,
        "minimum_validation_timestamps": 10,
    }
    values.update(updates)
    return ContextualLearningExperiment(**values)


def test_each_contextual_degree_of_freedom_changes_global_trial_identity() -> None:
    base = ContextualCandidate.defaults()
    hashes = {
        candidate.global_trial_id(DATASET, PROTOCOL)
        for candidate in (
            base,
            replace(base, asset_regime_prior_strength=base.asset_regime_prior_strength + 10),
            replace(base, risk_penalty=base.risk_penalty * 2),
            replace(base, minimum_liquidity_quality=0.90),
            replace(base, long_holding_horizon_bars=2),
        )
    }

    assert len(hashes) == 5


def test_contextual_search_space_is_deterministic_bounded_and_deduplicated() -> None:
    space = ContextualSearchSpace.conservative(ContextualCandidate.defaults())

    first = generate_contextual_candidates(space, seed=42, budget=40)
    second = generate_contextual_candidates(space, seed=42, budget=40)

    assert first == second
    assert first[0] == ContextualCandidate.defaults()
    assert len(first) == 40
    assert len({item.candidate_hash for item in first}) == 40
    assert all(0.01 <= item.kelly_fraction <= 0.25 for item in first)
    assert all(0.25 <= item.maximum_correlation <= 0.95 for item in first)


def test_contextual_candidate_uses_only_chronological_resolved_outcomes() -> None:
    result = evaluate_contextual_candidate(ContextualCandidate.defaults(), _outcomes(), _experiment())

    assert result.status == "succeeded"
    assert len(result.fold_metrics) == 3
    assert result.observations > 0
    assert math.isfinite(result.fitness)
    assert result.state == "shadow"


def test_contextual_search_never_reads_sealed_rows() -> None:
    leaked = _outcomes()
    leaked["sealed_score"] = 1.0

    with pytest.raises(ValueError, match="sealed"):
        evaluate_contextual_candidate(ContextualCandidate.defaults(), leaked, _experiment())


def test_contextual_search_rejects_outcomes_unavailable_at_the_learning_boundary() -> None:
    leaked = _outcomes()
    leaked.loc[leaked.index[-1], "outcome_available_at"] = AS_OF + timedelta(seconds=1)

    with pytest.raises(ValueError, match="available"):
        evaluate_contextual_candidate(ContextualCandidate.defaults(), leaked, _experiment())


def test_contextual_drawdown_includes_the_initial_cash_peak() -> None:
    assert _maximum_drawdown([-0.10]) == pytest.approx(0.10)


@pytest.mark.parametrize(
    "updates",
    [
        {"kelly_fraction": 0.30},
        {"maximum_correlation": 1.0},
        {"minimum_lower_edge": -0.001},
        {"long_holding_horizon_bars": 100},
        {"risk_penalty": float("nan")},
    ],
)
def test_contextual_candidate_cannot_escape_the_safe_closed_domain(updates: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ContextualCandidate(**updates)


def test_contextual_entropy_handles_zero_probability_mass_without_numeric_errors() -> None:
    outcomes = _outcomes()
    outcomes["regime_trend_normal"] = 1.0
    outcomes["regime_trend_elevated_volatility"] = 0.0
    outcomes["regime_range_liquid"] = 0.0
    outcomes["regime_stressed_or_illiquid"] = 0.0

    with np.errstate(divide="raise", invalid="raise"):
        result = evaluate_contextual_candidate(ContextualCandidate.defaults(), outcomes, _experiment())
    assert math.isfinite(result.fitness)
