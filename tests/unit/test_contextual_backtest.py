from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from src.contextual.backtest import realize_weighted_outcomes

AS_OF = datetime(2026, 8, 1, 12, tzinfo=UTC)
WEIGHTS = {("BTCUSDT", "short", "alpha"): 0.3, ("BTCUSDT", "short", "beta"): 0.1}


def outcomes():
    return pd.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "direction": direction,
                "strategy_id": strategy,
                "decision_timestamp": AS_OF,
                "outcome_available_at": AS_OF + timedelta(minutes=5),
                "gross_return": gross,
                "modeled_cost": 0.002,
                "net_return": gross - 0.002,
                "holding_horizon_bars": 1,
            }
            for direction, strategy, gross in [
                ("short", "alpha", 0.02),
                ("short", "beta", -0.01),
                ("long", "alpha", 0.8),
            ]
        ]
    )


def test_portfolio_accounts_for_exact_components_direction_costs_and_cash():
    result = realize_weighted_outcomes(WEIGHTS, outcomes(), AS_OF, timedelta(minutes=5), {}, 0.001)
    assert result.gross_return == pytest.approx(0.005)
    assert result.source_costs == pytest.approx(0.0008)
    assert result.turnover == pytest.approx(0.4)
    assert result.costs == pytest.approx(0.0012)
    assert result.net_return == pytest.approx(0.0038)  # Short outcomes already carry their sign.
    assert result.gross_exposure == pytest.approx(0.4)


@pytest.mark.parametrize("damage", ["wrong_time", "missing", "duplicate", "wrong_horizon"])
def test_backtest_never_substitutes_the_first_future_or_incomplete_outcome(damage):
    frame = outcomes()
    if damage == "wrong_time":
        frame["decision_timestamp"] += timedelta(minutes=5)
    elif damage == "missing":
        frame = frame.loc[frame["strategy_id"] != "beta"]
    elif damage == "duplicate":
        frame = pd.concat([frame, frame.iloc[[0]]])
    else:
        frame["holding_horizon_bars"] = 2
    with pytest.raises(ValueError, match="outcome|horizon"):
        realize_weighted_outcomes(WEIGHTS, frame, AS_OF, timedelta(minutes=5), {}, 0.001)


def test_exiting_to_cash_pays_rebalancing_cost_without_an_outcome():
    result = realize_weighted_outcomes({}, outcomes().iloc[:0], AS_OF, timedelta(minutes=5), WEIGHTS, 0.001)
    assert result.net_return == pytest.approx(-0.0004)
    assert result.turnover == pytest.approx(0.4)
