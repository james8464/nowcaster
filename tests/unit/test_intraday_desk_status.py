from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from src.intraday.desk import DeskStatus, MarketStatus, OpportunityStatus


def test_unconfigured_desk_is_explicit_no_trade_and_not_supported():
    status = DeskStatus.unconfigured(datetime(2026, 10, 6, tzinfo=UTC))
    assert status.feed_health == "not_configured"
    assert status.evidence_status == "not_supported"
    assert status.opportunities == ()
    assert status.paper_positions == ()
    assert "demo" in status.no_trade_reason.lower()
    assert len(status.markets) == 4


def test_inventory_verified_is_still_not_trade_eligible():
    status = DeskStatus.unconfigured(datetime(2026, 10, 6, tzinfo=UTC)).model_copy(
        update={
            "feed_health": "inventory_verified",
            "markets": (
                MarketStatus(market="germany40", broker_symbol="DE30_EUR", product="cfd", eligibility="unverified"),
            ),
        }
    )
    assert status.markets[0].eligibility == "unverified"
    assert not status.opportunities


def test_diagnostic_opportunity_requires_healthy_feed_and_paper_label():
    at = datetime(2026, 10, 6, tzinfo=UTC)
    idea = OpportunityStatus(
        market="germany40",
        broker_symbol="DE30_EUR",
        strategy_id="opening_range_15",
        direction="long",
        decided_at=at,
        entry_at=at + timedelta(seconds=1),
        entry=D(100),
        stop=D(90),
        target=D(120),
        exit_by=at + timedelta(hours=1),
        estimated_roundtrip_cost=D(2),
        explanation="Paper only",
        evidence_hash="a" * 64,
    )
    base = DeskStatus.unconfigured(at).model_dump()
    base.update(
        feed_health="healthy",
        markets=(MarketStatus(market="germany40", broker_symbol="DE30_EUR", product="cfd", eligibility="diagnostic"),),
        opportunities=(idea,),
        no_trade_reason="",
    )
    assert DeskStatus.model_validate(base).opportunities[0].paper_only
    base["feed_health"] = "stale"
    with pytest.raises(ValueError, match="unhealthy"):
        DeskStatus.model_validate(base)


def test_opportunity_rejects_reversed_levels_and_noncausal_times():
    at = datetime(2026, 10, 7, tzinfo=UTC)
    valid = dict(
        market="germany40",
        broker_symbol="DE30_EUR",
        strategy_id="opening_range_15",
        direction="long",
        decided_at=at,
        entry_at=at + timedelta(seconds=1),
        entry=D(100),
        stop=D(90),
        target=D(120),
        exit_by=at + timedelta(hours=1),
        estimated_roundtrip_cost=D(2),
        explanation="Paper only",
        evidence_hash="a" * 64,
    )
    with pytest.raises(ValueError, match="levels"):
        OpportunityStatus(**{**valid, "stop": D(110)})
    with pytest.raises(ValueError, match="timing"):
        OpportunityStatus(**{**valid, "entry_at": at})
