from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.database.engine import Database
from src.trading.forward import ForwardCohortIdentity, ForwardEvidenceBuilder
from src.trading.types import ExecutionObservation

START = datetime(2026, 8, 24, tzinfo=UTC)
END = START + timedelta(days=1)


def _identity(**updates):
    values = dict(
        asset_class="equity",
        provider="alpaca",
        feed="iex",
        symbol="AAPL",
        interval="1Min",
        strategy_id="ensemble-v1",
        strategy_version="1",
        parameters_hash="a" * 64,
        weights_hash="b" * 64,
        dataset_hash="c" * 64,
        code_hash="d" * 64,
        config_hash="e" * 64,
        risk_policy_hash="f" * 64,
        cost_policy_hash="0" * 64,
    )
    values.update(updates)
    return ForwardCohortIdentity(**values)


def _execution_observation(**updates) -> ExecutionObservation:
    values = dict(
        observation_id="1" * 64,
        session_id="paper-1",
        cohort_hash=_identity().cohort_hash,
        intent_id="intent-1",
        broker_order_id="order-1",
        symbol="AAPL",
        side="buy",
        decision_at=START,
        submitted_at=START,
        first_fill_at=START + timedelta(milliseconds=100),
        terminal_at=START + timedelta(milliseconds=100),
        requested_quantity=Decimal("1"),
        filled_quantity=Decimal("1"),
        reference_price=Decimal("100"),
        predicted_fill_price=Decimal("100.05"),
        realized_fill_price=Decimal("100.055"),
        predicted_spread_bps=Decimal("2"),
        realized_spread_bps=Decimal("2"),
        predicted_slippage_bps=Decimal("3"),
        realized_slippage_bps=Decimal("3.5"),
        predicted_impact_bps=Decimal("0"),
        realized_impact_bps=Decimal("0"),
        predicted_latency_ms=Decimal("50"),
        realized_latency_ms=Decimal("100"),
        observed_at=START + timedelta(milliseconds=100),
    )
    values.update(updates)
    return ExecutionObservation(**values)


@pytest.mark.parametrize(
    "field",
    [
        "provider",
        "feed",
        "symbol",
        "interval",
        "strategy_version",
        "parameters_hash",
        "weights_hash",
        "dataset_hash",
        "code_hash",
        "config_hash",
        "risk_policy_hash",
        "cost_policy_hash",
    ],
)
def test_every_material_mutation_resets_cohort(field) -> None:
    assert _identity().cohort_hash != _identity(**{field: "changed"}).cohort_hash


def test_closed_period_is_immutable_and_incomplete_reconciliation_is_unavailable(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'forward.duckdb'}")
    database.initialize()
    builder = ForwardEvidenceBuilder(database, clock=lambda: END)
    evidence = builder.close_period(
        cohort=_identity(),
        period_start=START,
        period_end=END,
        closed_trades=2,
        paper_net_return=Decimal("0.01"),
        stressed_net_return=Decimal("0.004"),
        drawdown=Decimal("0.002"),
        reconciliation_mismatches=1,
        health_breakers=0,
    )
    assert evidence.status == "unavailable"
    assert builder.close_period(**evidence.replay_arguments(_identity())) == evidence
    with pytest.raises(ValueError, match="conflicting closed forward period"):
        builder.close_period(**{**evidence.replay_arguments(_identity()), "closed_trades": 3})


def test_complete_period_requires_matching_low_error_execution_observations(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'execution-forward.duckdb'}")
    database.initialize()
    builder = ForwardEvidenceBuilder(database, clock=lambda: END)
    arguments = dict(
        cohort=_identity(),
        period_start=START,
        period_end=END,
        closed_trades=1,
        paper_net_return=Decimal("0.01"),
        stressed_net_return=Decimal("0.004"),
        drawdown=Decimal("0.002"),
        reconciliation_mismatches=0,
        health_breakers=0,
    )

    missing = builder.close_period(**arguments)
    assert missing.status == "unavailable"
    assert missing.execution_model_status == "unavailable"

    calibrated_database = Database.from_url(f"duckdb:///{tmp_path / 'execution-forward-2.duckdb'}")
    calibrated_database.initialize()
    calibrated = ForwardEvidenceBuilder(calibrated_database, clock=lambda: END).close_period(
        **arguments,
        execution_observations=(_execution_observation(),),
    )
    assert calibrated.status == "complete"
    assert calibrated.execution_observations == 1
    assert calibrated.execution_error_upper_ratio <= Decimal("0.20")
    assert calibrated.modeled_slippage_bps == Decimal("5")
    assert calibrated.observed_slippage_bps == Decimal("5.5")
