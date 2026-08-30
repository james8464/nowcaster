from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from src.config.settings import AllocationPolicyConfig
from src.contextual.allocation import allocate_contextual_weights, estimate_strategy_covariance
from src.contextual.hierarchy import BlendedRegimeEstimate
from src.contextual.types import StrategyDirection
from src.strategies.types import StrategyFamily

AS_OF = datetime(2026, 8, 30, 12, tzinfo=UTC)
POLICY = AllocationPolicyConfig(
    minimum_effective_strategies=2,
    maximum_strategy_weight=0.45,
    minimum_covariance_overlap=40,
    risk_penalty=25.0,
    turnover_penalty=0.1,
    prior_penalty=0.1,
    family_weight_caps={
        StrategyFamily.TREND: 0.45,
        StrategyFamily.MEAN_REVERSION: 0.50,
    },
)


def _estimate(strategy_id: str, lower: float) -> BlendedRegimeEstimate:
    return BlendedRegimeEstimate(
        strategy_id=strategy_id,
        symbol="BTCUSDT",
        direction=StrategyDirection.LONG,
        mean_net_edge=lower + 0.001,
        lower_net_edge=lower,
        uncertainty=0.001,
        parent_fallback_mass=0.0,
        component_estimate_ids=(f"estimate-{strategy_id}",),
        blend_hash=f"blend-{strategy_id}",
    )


def _returns() -> pd.DataFrame:
    random = np.random.default_rng(42)
    index = pd.date_range("2026-08-01", periods=180, freq="5min", tz="UTC")
    series = random.normal(0.0003, 0.01, len(index))
    diverse = random.normal(0.0003, 0.01, len(index))
    return pd.DataFrame({"alpha": series, "clone": series, "diverse": diverse}, index=index)


def test_duplicate_strategies_do_not_receive_false_diversification() -> None:
    returns = _returns()
    estimates = {name: _estimate(name, 0.006) for name in returns.columns}
    families = {
        "alpha": StrategyFamily.TREND,
        "clone": StrategyFamily.TREND,
        "diverse": StrategyFamily.MEAN_REVERSION,
    }

    result = allocate_contextual_weights(
        estimates,
        returns,
        {name: 1 / 3 for name in returns.columns},
        {},
        families,
        POLICY,
        AS_OF,
    )

    assert result.status == "allocated"
    assert result.weights["alpha"] + result.weights["clone"] <= result.weights["diverse"] + 0.05
    assert result.effective_strategy_count >= POLICY.minimum_effective_strategies
    assert result.covariance.correlation("alpha", "clone") > 0.90
    assert sum(result.weights.values()) + result.cash_weight == 1.0


def test_nonpositive_lower_edges_allocate_all_mass_to_cash() -> None:
    returns = _returns()
    estimates = {name: _estimate(name, -0.001) for name in returns.columns}
    families = {
        "alpha": StrategyFamily.TREND,
        "clone": StrategyFamily.TREND,
        "diverse": StrategyFamily.MEAN_REVERSION,
    }

    result = allocate_contextual_weights(estimates, returns, {}, {}, families, POLICY, AS_OF)

    assert result.status == "all_cash"
    assert result.cash_weight == 1.0
    assert all(value == 0 for value in result.weights.values())


def test_insufficient_covariance_overlap_fails_closed_and_runs_are_deterministic() -> None:
    returns = _returns()
    evidence = estimate_strategy_covariance(returns.iloc[:20], AS_OF, minimum_overlap=40)
    assert evidence.status == "insufficient"

    estimates = {name: _estimate(name, 0.006) for name in returns.columns}
    families = {
        "alpha": StrategyFamily.TREND,
        "clone": StrategyFamily.TREND,
        "diverse": StrategyFamily.MEAN_REVERSION,
    }
    first = allocate_contextual_weights(estimates, returns, {}, {}, families, POLICY, AS_OF)
    second = allocate_contextual_weights(estimates, returns, {}, {}, families, POLICY, AS_OF)
    assert first == second
