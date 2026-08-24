from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
import pytest

from src.backtest.execution import OrderIntent
from src.trading.risk import PreTradeRiskEngine, RiskContext, RiskPolicy
from src.trading.types import TradingEnvironment

NOW = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)


def _intent(side="buy", quantity=1) -> OrderIntent:
    return OrderIntent(
        order_id="order-1",
        strategy_id="strategy-1",
        symbol="AAPL",
        decision_timestamp=pd.Timestamp(NOW),
        side=side,
        quantity=quantity,
    )


def _context(**updates) -> RiskContext:
    values = dict(
        environment=TradingEnvironment.PAPER,
        account_suffix="1234",
        expected_account_suffix="1234",
        cohort_hash="c" * 64,
        expected_cohort_hash="c" * 64,
        provider="alpaca",
        expected_provider="alpaca",
        feed="iex",
        expected_feed="iex",
        data_age_seconds=1,
        unresolved_mismatches=0,
        account_equity=Decimal("100000"),
        buying_power=Decimal("100000"),
        current_position_notional=Decimal("0"),
        gross_exposure=Decimal("0"),
        turnover_today=Decimal("0"),
        orders_last_minute=0,
        spread_bps=Decimal("5"),
        reference_price=Decimal("190"),
        limit_price=Decimal("190.20"),
        daily_pnl=Decimal("0"),
        drawdown_fraction=Decimal("0"),
        frozen=False,
        duplicate_order=False,
        conflicting_order=False,
        asset_tradable=True,
        asset_shortable=True,
        asset_easy_to_borrow=True,
        is_opening_short=False,
    )
    values.update(updates)
    return RiskContext(**values)


def test_valid_paper_order_is_admitted_with_exact_utilization() -> None:
    decision = PreTradeRiskEngine().evaluate(_intent(), _context())
    assert decision.allowed and decision.reasons == ()
    assert decision.utilization["proposed_notional"] == "190.20"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"data_age_seconds": 31}, "market_data_stale"),
        ({"unresolved_mismatches": 1}, "reconciliation_unresolved"),
        ({"daily_pnl": Decimal("-1000.01")}, "daily_loss_limit"),
        ({"provider": "binance"}, "evidence_venue_mismatch"),
        ({"feed": "sip"}, "evidence_feed_mismatch"),
        ({"account_suffix": "9999"}, "account_mismatch"),
        ({"cohort_hash": "x" * 64}, "cohort_mismatch"),
        ({"frozen": True}, "global_freeze"),
        ({"duplicate_order": True}, "duplicate_order"),
        ({"conflicting_order": True}, "conflicting_order"),
        ({"buying_power": Decimal("100")}, "buying_power_limit"),
        ({"gross_exposure": Decimal("9999")}, "gross_exposure_limit"),
        ({"turnover_today": Decimal("24999")}, "turnover_limit"),
        ({"orders_last_minute": 10}, "order_rate_limit"),
        ({"spread_bps": Decimal("31")}, "spread_limit"),
        ({"limit_price": Decimal("191")}, "price_collar"),
        ({"drawdown_fraction": Decimal("0.051")}, "drawdown_limit"),
        ({"asset_tradable": False}, "asset_not_tradable"),
    ],
)
def test_risk_engine_fails_closed(mutation, reason) -> None:
    decision = PreTradeRiskEngine().evaluate(_intent(), _context(**mutation))
    assert not decision.allowed and reason in decision.reasons


@pytest.mark.parametrize("field", ["account_equity", "buying_power", "reference_price", "limit_price"])
@pytest.mark.parametrize("value", [None, Decimal("NaN"), Decimal("Infinity")])
def test_missing_or_nonfinite_inputs_reject_instead_of_raise(field, value) -> None:
    decision = PreTradeRiskEngine().evaluate(_intent(), _context(**{field: value}))
    assert not decision.allowed and "invalid_risk_input" in decision.reasons


def test_short_requires_broker_shortable_and_easy_to_borrow() -> None:
    context = _context(is_opening_short=True, asset_shortable=False, asset_easy_to_borrow=False)
    decision = PreTradeRiskEngine().evaluate(_intent(side="sell"), context)
    assert {"asset_not_shortable", "asset_not_easy_to_borrow"} <= set(decision.reasons)


def test_exact_boundaries_pass_and_policy_hash_is_stable() -> None:
    policy = RiskPolicy()
    context = _context(
        data_age_seconds=30,
        spread_bps=policy.max_spread_bps,
        daily_pnl=-(Decimal("100000") * policy.max_daily_loss_fraction),
        drawdown_fraction=policy.max_drawdown_fraction,
    )
    first = PreTradeRiskEngine(policy).evaluate(_intent(), context)
    second = PreTradeRiskEngine(policy.model_copy()).evaluate(_intent(), context)
    assert first.allowed and first.policy_hash == second.policy_hash
