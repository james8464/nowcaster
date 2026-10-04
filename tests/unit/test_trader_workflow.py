"""Causal triggers and eligibility, using the real context and cost screen."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from src.research.day_trader_context import CalendarSnapshot, ContextObservation
from src.research.round_two_contracts import ResearchRoundProtocol

START = datetime(2026, 9, 22, 12, tzinfo=UTC)
NOW = START + timedelta(hours=1, seconds=2)


def protocol():
    return ResearchRoundProtocol.default(round_id="workflow-test", starts_at=START)


def bars(symbol="BTCUSDT", trigger="breakout"):
    rows = []
    for i in range(60):
        at = START + timedelta(minutes=i + 1)
        price = D(100) + D(i) / 20
        rows.append(
            ContextObservation(
                provider="binance",
                feed="spot",
                symbol=symbol,
                provider_at=at,
                received_at=at + timedelta(seconds=1),
                available_at=at + timedelta(seconds=1),
                source_key=f"{symbol}-bar-{i}",
                open=price,
                high=price + D(".5"),
                low=price - D(".5"),
                close=price,
                volume=1000,
                bid=price - D(".01"),
                ask=price + D(".01"),
                bid_size=100,
                ask_size=100,
                quote_provider_at=at,
                quote_received_at=at + timedelta(seconds=1),
                quote_available_at=at + timedelta(seconds=1),
                quote_source_key=f"{symbol}-quote-{i}",
            )
        )
    if trigger == "breakout":
        rows[-1] = rows[-1].model_copy(
            update=dict(
                open=D("103.4"), close=D("103.7"), high=D("103.8"), low=D("103.2"), bid=D("103.69"), ask=D("103.71")
            )
        )
    elif trigger == "reclaim":
        rows[-1] = rows[-1].model_copy(update=dict(open=D("102.3"), close=D("102.95"), high=D("103.1"), low=D("102.2")))
    return tuple(rows)


def calendar():
    return CalendarSnapshot(
        source="test-calendar",
        revision="v1",
        published_at=START,
        available_at=START,
        valid_until=START + timedelta(days=2),
        coverage_starts_at=START - timedelta(hours=1),
        coverage_ends_at=START + timedelta(days=2),
        events=(),
    )


def select(rows=None, cal=None, now=NOW):
    from src.research.trader_workflow import WorkflowPolicy, select_setups

    p = protocol()
    return select_setups(p, rows or bars(), calendar() if cal is None else cal, now, WorkflowPolicy(round_protocol=p))


def test_workflow_engine_is_available():
    import importlib.util

    assert importlib.util.find_spec("src.research.trader_workflow") is not None


def test_breakout_uses_preceding_twenty_highs_and_records_immutable_identity():
    result = next(d for d in select() if d.symbol == "BTCUSDT")
    assert result.status == "ready" and result.setup == "breakout"
    assert result.trigger_level == D("103.4")
    assert result.stop == D("101.95")
    assert result.target == D("107.23")
    assert len(result.context_hash) == len(result.policy_hash) == len(result.decision_id) == 64
    assert result.calendar_available is True
    assert result.source_key == "BTCUSDT-bar-59"


def test_rising_indicator_without_observed_trigger_is_watching():
    result = next(d for d in select(bars(trigger="none")) if d.symbol == "BTCUSDT")
    assert result.status == "watching"
    assert result.reasons == ("setup_not_triggered",)


def test_reclaim_requires_actual_pullback_below_preceding_mean():
    result = next(d for d in select(bars(trigger="reclaim")) if d.symbol == "BTCUSDT")
    assert result.status == "ready" and result.setup == "pullback_reclaim"
    assert result.trigger_level == D("102.425")


def test_later_bar_cannot_repaint_prior_decision():
    future = bars()[-1].model_copy(
        update=dict(
            source_key="future",
            provider_at=NOW + timedelta(minutes=1),
            received_at=NOW + timedelta(minutes=1),
            available_at=NOW + timedelta(minutes=1),
            quote_provider_at=NOW + timedelta(minutes=1),
            quote_received_at=NOW + timedelta(minutes=1),
            quote_available_at=NOW + timedelta(minutes=1),
        )
    )
    assert select((*bars(), future)) == select()


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"quote_provider_at": START}, "order_book_stale"),
        ({"ask_size": None}, "order_book_unavailable"),
        ({"volume": D(0)}, "liquidity_unavailable"),
        ({"bid": D(100)}, "spread_exceeds_maximum"),
    ],
)
def test_quote_and_liquidity_blocks_retain_reasons(changes, reason):
    rows = (*bars()[:-1], bars()[-1].model_copy(update=changes))
    result = next(d for d in select(rows) if d.symbol == "BTCUSDT")
    assert result.status == "blocked" and reason in result.reasons


def test_missing_calendar_and_unsupported_identity_fail_closed():
    from src.research.trader_workflow import WorkflowPolicy, select_setups

    p = protocol()
    result = select_setups(p, bars(), None, NOW, WorkflowPolicy(round_protocol=p))[0]
    assert "calendar_missing" in result.reasons
    with pytest.raises(ValueError, match="identity"):
        select_setups(
            p.model_copy(update={"round_id": "different"}), bars(), calendar(), NOW, WorkflowPolicy(round_protocol=p)
        )


def test_rank_is_volatility_relative_to_spread_and_all_assets_retained():
    eth = tuple(r.model_copy(update=dict(bid=r.bid - D(".05"), ask=r.ask + D(".05"))) for r in bars("ETHUSDT"))
    result = select((*bars(), *eth))
    assert [d.symbol for d in result] == ["BTCUSDT", "ETHUSDT"]
    assert result[0].rank_score > result[1].rank_score


def test_cost_screen_rejects_small_risk_target_even_in_uptrend():
    rows = tuple(
        r.model_copy(update=dict(high=r.close + D(".02"), low=r.close - D(".02"))) for r in bars(trigger="none")
    )
    result = next(d for d in select(rows) if d.symbol == "BTCUSDT")
    assert result.status == "blocked" and "net_reward_below_risk" in result.reasons


def test_less_than_one_lot_quote_size_blocks_before_ready():
    rows = (*bars()[:-1], bars()[-1].model_copy(update={"ask_size": D(".000001")}))
    result = next(d for d in select(rows) if d.symbol == "BTCUSDT")
    assert result.status == "blocked" and "quote_size_below_lot" in result.reasons


def test_range_and_conflicting_higher_trends_stand_aside():
    rows = tuple(
        r.model_copy(update=dict(open=D(100), close=D(100), high=D(101), low=D(99), bid=D("99.99"), ask=D("100.01")))
        for r in bars()
    )
    assert "trend_not_aligned_up" in select(rows)[0].reasons


def test_missing_observations_preserve_all_declared_asset_reasons():
    from src.research.trader_workflow import WorkflowPolicy, select_setups

    p = protocol()
    ds = select_setups(p, (), calendar(), NOW, WorkflowPolicy(round_protocol=p))
    assert {d.symbol for d in ds} == {"BTCUSDT", "ETHUSDT"}
    assert all(d.reasons == ("observations_unavailable",) for d in ds)
