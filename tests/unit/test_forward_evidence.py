from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.database.engine import Database
from src.trading.forward import ForwardCohortIdentity, ForwardEvidenceBuilder

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
