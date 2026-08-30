from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from src.config.settings import PortfolioSelectionPolicyConfig
from src.contextual.portfolio import (
    ResearchOpportunity,
    research_size_ceiling,
    select_portfolio_opportunities,
)
from src.contextual.types import StrategyDirection
from src.strategies.types import StrategyFamily

AS_OF = datetime(2026, 8, 30, 12, tzinfo=UTC)
POLICY = PortfolioSelectionPolicyConfig(
    maximum_candidates=10,
    maximum_opportunities=3,
    maximum_gross_exposure=0.40,
    maximum_net_exposure=0.30,
    maximum_asset_weight=0.10,
    maximum_asset_class_weight=0.25,
    maximum_sector_weight=0.20,
    maximum_correlation=0.75,
    minimum_research_weight=0.0025,
    kelly_fraction=0.10,
    volatility_target=0.10,
)


def _opportunity(
    symbol: str,
    edge: float,
    *,
    asset_class: str = "equity",
    sector: str = "Technology",
) -> ResearchOpportunity:
    return ResearchOpportunity(
        decision_hash=f"decision-{symbol}",
        context_hash=f"context-{symbol}",
        symbol=symbol,
        direction=StrategyDirection.LONG,
        asset_class=asset_class,
        sector=sector,
        family=StrategyFamily.TREND,
        decision_time=AS_OF,
        horizon_minutes=60,
        eligible=True,
        lower_net_edge=edge,
        liquidity_quality=0.95,
        probability_lower=0.60,
        payoff_lower=1.5,
        realized_volatility=0.25,
        liquidity_capacity_weight=0.10,
        remaining_risk_weight=0.10,
    )


def _returns() -> pd.DataFrame:
    random = np.random.default_rng(24)
    index = pd.date_range("2026-08-01", periods=200, freq="5min", tz="UTC")
    technology = random.normal(0, 0.01, len(index))
    return pd.DataFrame(
        {
            "AAPL": technology,
            "MSFT": technology + random.normal(0, 0.0001, len(index)),
            "BTCUSDT": random.normal(0, 0.015, len(index)),
        },
        index=index,
    )


def test_selector_keeps_distinct_edge_and_rejects_correlated_duplicate() -> None:
    aapl = _opportunity("AAPL", 0.010)
    msft = _opportunity("MSFT", 0.009)
    btc = _opportunity("BTCUSDT", 0.008, asset_class="crypto", sector="Digital Assets")

    result = select_portfolio_opportunities((aapl, msft, btc), _returns(), POLICY, AS_OF)

    assert result.status == "selected"
    assert result.selected_symbols == ("AAPL", "BTCUSDT")
    assert result.exclusions["MSFT"] == ("correlation_cluster_limit",)
    assert result.gross_weight <= POLICY.maximum_gross_exposure
    assert abs(result.net_weight) <= POLICY.maximum_net_exposure


def test_selector_is_allowed_to_hold_only_cash() -> None:
    aapl = _opportunity("AAPL", -0.001)

    result = select_portfolio_opportunities((aapl,), _returns()[["AAPL"]], POLICY, AS_OF)

    assert result.selected == ()
    assert result.cash_weight == 1.0
    assert result.exclusions["AAPL"] == ("nonpositive_lower_net_edge",)


def test_invalid_payoff_evidence_never_receives_an_optimistic_size() -> None:
    opportunity = replace(_opportunity("AAPL", 0.01), payoff_lower=0.0)

    evidence = research_size_ceiling(opportunity, POLICY)

    assert evidence.ceiling == 0
    assert evidence.reasons == ("invalid_payoff_evidence",)
