from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.trading.forward import ForwardCohortIdentity, ForwardDailyEvidence
from src.trading.readiness import ReadinessEvaluator, ReadinessPolicy

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def _cohort(asset_class="equity"):
    return ForwardCohortIdentity(
        asset_class=asset_class,
        provider="alpaca",
        feed="iex" if asset_class == "equity" else "crypto",
        symbol="AAPL" if asset_class == "equity" else "BTC/USD",
        interval="1Min",
        strategy_id="ensemble",
        strategy_version="1",
        parameters_hash="a" * 64,
        weights_hash="b" * 64,
        dataset_hash="c" * 64,
        code_hash="d" * 64,
        config_hash="e" * 64,
        risk_policy_hash="f" * 64,
        cost_policy_hash="0" * 64,
    )


def _evidence(cohort, periods, trades, stressed="0.01"):
    return tuple(
        ForwardDailyEvidence(
            cohort_hash=cohort.cohort_hash,
            period_start=NOW - timedelta(days=periods - index),
            period_end=NOW - timedelta(days=periods - index - 1),
            closed_trades=trades // periods + (1 if index < trades % periods else 0),
            paper_net_return=Decimal("0.02"),
            stressed_net_return=Decimal(stressed),
            drawdown=Decimal("0.01"),
            reconciliation_mismatches=0,
            health_breakers=0,
            status="complete",
            evidence_hash=(f"{index:064x}"[-64:]),
            closed_at=NOW,
        )
        for index in range(periods)
    )


def _robustness(cohort):
    return {
        "cohort_hash": cohort.cohort_hash,
        "causal_passed": True,
        "bootstrap_probability_positive": "0.96",
        "deflated_sharpe_probability": "0.96",
        "pbo": "0.20",
        "parameter_stability": "0.80",
        "slippage_model_error": "0.10",
    }


def test_equity_requires_60_sessions_and_100_trades() -> None:
    cohort = _cohort()
    evaluator = ReadinessEvaluator()
    assert evaluator.evaluate(cohort, _evidence(cohort, 60, 99), _robustness(cohort), as_of=NOW).status == "locked"
    assert evaluator.evaluate(cohort, _evidence(cohort, 59, 100), _robustness(cohort), as_of=NOW).status == "locked"
    ready = evaluator.evaluate(cohort, _evidence(cohort, 60, 100), _robustness(cohort), as_of=NOW)
    assert ready.status == "eligible" and ready.receipt is not None
    assert ready.receipt.expires_at == NOW + timedelta(hours=24)


def test_crypto_requires_90_days_and_any_operational_or_statistical_failure_locks() -> None:
    cohort = _cohort("crypto")
    evaluator = ReadinessEvaluator(ReadinessPolicy())
    assert evaluator.evaluate(cohort, _evidence(cohort, 89, 100), _robustness(cohort), as_of=NOW).status == "locked"
    bad = _robustness(cohort)
    bad["pbo"] = "0.51"
    result = evaluator.evaluate(cohort, _evidence(cohort, 90, 100), bad, as_of=NOW)
    assert result.status == "locked" and not result.gate("robustness").passed


def test_mismatched_cohort_or_nonpositive_stressed_edge_locks() -> None:
    cohort = _cohort()
    robustness = _robustness(cohort)
    robustness["cohort_hash"] = "x" * 64
    mismatch = ReadinessEvaluator().evaluate(cohort, _evidence(cohort, 60, 100), robustness, as_of=NOW)
    assert mismatch.status == "locked"
    negative = ReadinessEvaluator().evaluate(
        cohort, _evidence(cohort, 60, 100, stressed="-0.001"), _robustness(cohort), as_of=NOW
    )
    assert not negative.gate("stressed_net_edge").passed


def test_missing_robustness_metrics_lock_instead_of_crashing() -> None:
    cohort = _cohort()
    missing = {"cohort_hash": cohort.cohort_hash, "causal_passed": True}

    result = ReadinessEvaluator().evaluate(cohort, _evidence(cohort, 60, 100), missing, as_of=NOW)

    assert result.status == "locked"
    assert not result.gate("robustness").passed
