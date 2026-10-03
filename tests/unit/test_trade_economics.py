"""Catch omitted round-trip costs and misleading probability interpretations."""

from decimal import Decimal as D

import pytest

from src.research import trend_advisor


def economics(**changes):
    values = dict(bid=D(100), ask=D(100), stop=D(99), target=D(102), fee_bps=D(0), slippage_bps=D(0))
    values.update(changes)
    return trend_advisor.long_trade_economics(**values)


def test_cost_free_payoff_and_required_win_rate_are_not_a_prediction():
    result = economics()
    assert result.entry_debit == D(100)
    assert result.target_credit == D(102)
    assert result.stop_credit == D(99)
    assert result.net_reward == D(2)
    assert result.net_risk == D(1)
    assert result.reward_to_risk == D(2)
    assert result.required_win_rate == D(1) / D(3)
    assert result.reasons == ()


def test_stress_charges_fees_and_slippage_on_both_actual_notionals():
    result = economics(target=D("101.5"), fee_bps=D(10), slippage_bps=D(5))
    assert result.entry_debit == D("100.300200")
    assert result.target_credit == D("101.1957030")
    assert result.stop_credit == D("98.703198")
    assert result.net_reward == D("0.8955030")
    assert result.net_risk == D("1.597002")
    assert result.reasons == ("net_reward_below_risk",)


def test_spread_alone_can_make_the_target_unprofitable():
    result = economics(bid=D("99.8"), target=D("100.1"))
    assert result.entry_debit == D("100.1")
    assert result.target_credit == D("99.9")
    assert result.net_reward == D("-0.2")
    assert result.required_win_rate is None
    assert result.reasons == ("target_does_not_cover_costs",)


def test_exact_net_reward_risk_boundary_passes_without_float_rounding():
    assert economics(target=D(101)).reasons == ()
    assert economics(target=D("100.999999999999")).reasons == ("net_reward_below_risk",)


@pytest.mark.parametrize(
    "changes",
    [
        {"bid": D(101)},
        {"ask": D(0)},
        {"stop": D(101)},
        {"target": D(99)},
        {"bid": D("NaN")},
        {"fee_bps": D("Infinity")},
        {"fee_bps": D(-1)},
        {"slippage_bps": D(-1)},
        {"fee_bps": D(5000)},
        {"slippage_bps": D(5000)},
    ],
)
def test_invalid_or_non_executable_economics_fail_closed(changes):
    with pytest.raises(ValueError):
        economics(**changes)
