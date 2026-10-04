"""Account tests assert executable quotes, conservative barriers and retained losses."""

from datetime import timedelta
from decimal import Decimal as D

import pytest

from tests.unit.test_trader_workflow import NOW, bars, calendar, protocol


def engine():
    from src.research.trader_workflow import WorkflowPolicy, select_setups
    from src.research.trader_workflow_account import WorkflowAccount, advance_account

    policy = WorkflowPolicy(round_protocol=protocol())
    decisions = select_setups(protocol(), bars(), calendar(), NOW, policy)
    account = WorkflowAccount.initial(policy, NOW)

    def advance(account, decisions, observations, now, policy):
        # Entry fixtures supply the same causal context the runtime must supply.
        if account.pending_entry and not decisions and observations:
            current = next((r for r in observations if r.symbol == account.pending_entry.symbol), None)
            if current and current.available_at <= now:
                decisions = select_setups(policy.round_protocol, (*bars()[:-1], current), calendar(), now, policy)
        return advance_account(account, decisions, observations, now, policy)

    return policy, decisions, account, advance


def quote(at, bid="103.69", ask="103.71", size=100, **changes):
    row = bars()[-1].model_dump()
    row.update(
        source_key=f"tick-{at.isoformat()}",
        quote_source_key=f"quote-{at.isoformat()}",
        received_at=at,
        available_at=at,
        quote_provider_at=at,
        quote_received_at=at,
        quote_available_at=at,
        bid=D(bid),
        ask=D(ask),
        bid_size=D(size),
        ask_size=D(size),
    )
    row.update(changes)
    from src.research.day_trader_context import ContextObservation

    return ContextObservation.model_validate(row)


def entered():
    p, ds, account, advance = engine()
    first = advance(account, ds, bars(), NOW, p)
    second = advance(first.account, (), (quote(NOW + timedelta(seconds=1)),), NOW + timedelta(seconds=1), p)
    assert second.account.position is not None
    return p, ds, second.account, advance


def test_account_engine_is_available():
    import importlib.util

    assert importlib.util.find_spec("src.research.trader_workflow_account") is not None


def test_ready_intent_fills_only_on_later_quote_and_pays_entry_costs():
    p, ds, account, advance = engine()
    first = advance(account, ds, bars(), NOW, p)
    assert first.account.position is None and first.account.pending_entry is not None
    same = advance(first.account, ds, bars(), NOW, p)
    assert same.account == first.account
    filled = advance(
        first.account, (), (quote(NOW + timedelta(seconds=1), size=2),), NOW + timedelta(seconds=1), p
    ).account
    pos = filled.position
    assert pos.quantity == D(2)
    assert pos.entry_price == D("103.761855")
    assert pos.entry_fee == D(".207523710")
    assert filled.cash == D("9792.268766290")
    assert filled.daily_entries == filled.total_entries == 1
    assert filled.pending_entry is None


def test_risk_exposure_volume_and_lot_caps():
    p, ds, account, advance = engine()
    pending = advance(account, ds, bars(), NOW, p).account
    filled = advance(
        pending, (), (quote(NOW + timedelta(seconds=1), volume=D("1.234567")),), NOW + timedelta(seconds=1), p
    ).account
    assert filled.position.quantity == D(".01234")
    p, ds, account, advance = entered()
    pos = account.position
    assert pos.quantity * (pos.entry_price * D("1.001")) <= D(2500)
    assert pos.quantity * (pos.entry_price * D("1.001") - pos.stop * D(".9995") * D(".999")) <= D(50)


def test_intent_expires_and_gap_cancels_without_entry():
    p, ds, account, advance = engine()
    pending = advance(account, ds, bars(), NOW, p).account
    expired = advance(pending, (), (), NOW + timedelta(seconds=60), p).account
    assert expired.pending_entry is None and expired.total_entries == 0
    gap = quote(
        NOW + timedelta(seconds=10),
        provider_at=NOW - timedelta(seconds=2) + timedelta(minutes=2),
        received_at=NOW + timedelta(minutes=2),
        available_at=NOW + timedelta(minutes=2),
        quote_provider_at=NOW + timedelta(minutes=2),
        quote_received_at=NOW + timedelta(minutes=2),
        quote_available_at=NOW + timedelta(minutes=2),
    )
    assert advance(pending, (), (gap,), NOW + timedelta(minutes=2), p).account.pending_entry is None


def test_pre_entry_extrema_cannot_trigger_and_trailing_ratchets_for_future_quotes():
    p, ds, account, advance = entered()
    at = NOW + timedelta(seconds=2)
    # These extrema occurred in the already closed minute before entry.
    old = quote(at, high=D(120), low=D(90))
    unchanged = advance(account, (), (old,), at, p).account
    assert unchanged.pending_exit is None and unchanged.position.stop == account.position.stop
    favorable = quote(at + timedelta(seconds=1), bid="106", ask="106.02")
    raised = advance(unchanged, (), (favorable,), at + timedelta(seconds=1), p).account
    assert raised.position.stop > account.position.stop
    retreat = quote(at + timedelta(seconds=2), bid="105.9", ask="105.92")
    assert advance(raised, (), (retreat,), at + timedelta(seconds=2), p).account.position.stop == raised.position.stop


def test_both_barriers_choose_stop_then_pay_next_bid_even_if_price_recovers():
    p, ds, account, advance = entered()
    at = NOW + timedelta(minutes=2)
    closed = quote(
        at, bid="105", ask="105.02", provider_at=NOW.replace(second=0) + timedelta(minutes=2), high=D(120), low=D(90)
    )
    triggered = advance(account, (), (closed,), at, p).account
    assert triggered.pending_exit.reason == "stop"
    assert triggered.position is not None
    stale = quote(at + timedelta(seconds=1), bid="105", ask="105.02", quote_provider_at=at - timedelta(seconds=20))
    waiting = advance(triggered, (), (stale,), at + timedelta(seconds=1), p).account
    assert waiting.pending_exit.reason == "stop"
    recovery = quote(at + timedelta(seconds=2), bid="110", ask="110.02")
    result = advance(waiting, (), (recovery,), at + timedelta(seconds=2), p).account
    assert result.position is None and result.completed_trades == 1
    outcome = result.history[-1]
    assert outcome.exit_price == D("109.945") and outcome.reason == "stop"
    assert outcome.net_pnl == result.realized_pnl
    assert result.setup_reviews[0].completed == 1


def test_max_holding_survives_missing_quote_and_partial_exit_respects_bid_size():
    p, ds, account, advance = entered()
    at = account.position.entry_at + timedelta(hours=1)
    timed = advance(account, (), (), at, p).account
    assert timed.pending_exit.reason == "maximum_holding"
    partial = advance(timed, (), (quote(at + timedelta(seconds=1), size=1),), at + timedelta(seconds=1), p).account
    assert partial.position.quantity == account.position.quantity - 1
    assert partial.completed_trades == 0 and partial.pending_exit is not None


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"daily_loss": D(200)}, "daily_loss_limit"),
        ({"daily_entries": 6}, "daily_trade_limit"),
        ({"cooldown_until": NOW + timedelta(hours=1)}, "losing_streak_cooldown"),
    ],
)
def test_risk_limits_block_new_intents(updates, reason):
    p, ds, account, advance = engine()
    result = advance(account.model_copy(update=updates), ds, bars(), NOW, p)
    assert result.account.pending_entry is None
    assert reason in result.events[-1].reasons


def test_utc_rollover_preserves_cash_lifetime_losses_and_bounded_history():
    p, ds, account, advance = entered()
    at = NOW + timedelta(seconds=2)
    stopped = advance(account, (), (quote(at, bid="95", ask="95.02"),), at, p).account
    closed = advance(
        stopped, (), (quote(at + timedelta(seconds=1), bid="94", ask="94.02"),), at + timedelta(seconds=1), p
    ).account
    assert closed.realized_pnl < 0 and closed.total_losses == 1
    next_day = NOW + timedelta(days=1)
    rolled = advance(closed, (), (), next_day, p).account
    assert rolled.daily_loss == 0 and rolled.daily_entries == 0
    assert rolled.cash == closed.cash and rolled.realized_pnl == closed.realized_pnl
    assert rolled.total_losses == 1 and rolled.history == closed.history


def test_one_position_and_identity_finite_time_checks():
    p, ds, account, advance = entered()
    assert advance(account, ds, bars(), NOW + timedelta(seconds=2), p).account.pending_entry is None
    with pytest.raises(ValueError, match="identity"):
        advance(account.model_copy(update={"policy_hash": "0" * 64}), (), (), NOW + timedelta(seconds=2), p)
    with pytest.raises(ValueError, match="regression"):
        advance(account, (), (), NOW, p)
    with pytest.raises(ValueError):
        advance(account.model_copy(update={"cash": D("NaN")}), (), (), NOW + timedelta(seconds=2), p)


def test_trigger_invalidation_exits_only_on_subsequent_quote():
    p, ds, account, advance = entered()
    at = NOW + timedelta(seconds=2)
    invalid = advance(account, (), (quote(at, bid="103.3", ask="103.32"),), at, p).account
    assert invalid.pending_exit.reason == "invalidation"
    result = advance(
        invalid, (), (quote(at + timedelta(seconds=1), bid="103.2", ask="103.22"),), at + timedelta(seconds=1), p
    ).account
    assert result.position is None and result.history[-1].reason == "invalidation"


def test_incomplete_context_cannot_claim_valid_adverse_regime():
    p, ds, account, advance = entered()
    d = ds[0].model_copy(update=dict(status="blocked", reasons=("insufficient_15m_history", "trend_not_aligned_up")))
    assert advance(account, (d,), (), NOW + timedelta(seconds=2), p).account.pending_exit is None


def test_three_losing_closes_start_cooldown_and_history_bound_does_not_erase_losses():
    p, ds, account, advance = engine()
    from src.research.trader_workflow_account import WorkflowAccount

    p = p.model_copy(update={"history_limit": 1})
    from src.research.trader_workflow import select_setups

    ds = select_setups(protocol(), bars(), calendar(), NOW, p)
    account = WorkflowAccount.initial(p, NOW)
    for n in range(3):
        at = NOW + timedelta(seconds=n * 4)
        account = advance(account, ds, bars(), at, p).account
        account = advance(account, (), (quote(at + timedelta(seconds=1)),), at + timedelta(seconds=1), p).account
        account = advance(
            account, (), (quote(at + timedelta(seconds=2), bid="101", ask="101.02"),), at + timedelta(seconds=2), p
        ).account
        account = advance(
            account, (), (quote(at + timedelta(seconds=3), bid="101", ask="101.02"),), at + timedelta(seconds=3), p
        ).account
    assert account.completed_trades == account.total_losses == 3
    assert account.consecutive_losses == 3
    assert account.cooldown_until == NOW + timedelta(seconds=3611)
    assert len(account.history) == 1 and account.setup_reviews[0].completed == 3
    assert account.setup_reviews[0].net_pnl == account.realized_pnl
    assert account.realized_losses == -account.realized_pnl


def test_pending_decision_mutation_and_changed_quote_provenance_rejected():
    p, ds, account, advance = engine()
    pending = advance(account, ds, bars(), NOW, p).account
    changed = ds[0].model_copy(update={"stop": D(101)})
    with pytest.raises(ValueError, match="contradictory"):
        advance(pending, (changed,), (), NOW + timedelta(seconds=1), p)
    future = quote(NOW + timedelta(seconds=2))
    waiting = advance(pending, (), (future,), NOW + timedelta(seconds=1), p).account
    assert waiting.position is None


def test_bad_entry_quote_does_not_execute_and_daily_limit_rechecked_at_fill():
    p, ds, account, advance = engine()
    pending = advance(account, ds, bars(), NOW, p).account
    wide = quote(NOW + timedelta(seconds=1), bid="103", ask="104")
    assert advance(pending, (), (wide,), NOW + timedelta(seconds=1), p).account.position is None
    capped = pending.model_copy(update={"daily_loss": D(200)})
    transition = advance(capped, (), (quote(NOW + timedelta(seconds=1)),), NOW + timedelta(seconds=1), p)
    assert transition.account.pending_entry is None and transition.account.position is None
    assert "daily_loss_limit" in transition.events[0].reasons


def test_same_source_key_cannot_be_mutated_into_a_later_entry_observation():
    p, ds, account, advance = engine()
    pending = advance(account, ds, bars(), NOW, p).account
    changed = quote(NOW + timedelta(seconds=1), source_key=ds[0].source_key)
    with pytest.raises(ValueError, match="identity"):
        advance(pending, (), (changed,), NOW + timedelta(seconds=1), p)


def test_actual_daily_losses_stop_next_ready_intent_and_cooldown_expires():
    p, ds, account, advance = engine()
    for n in range(2):
        at = NOW + timedelta(seconds=n * 4)
        account = advance(account, ds, bars(), at, p).account
        account = advance(account, (), (quote(at + timedelta(seconds=1)),), at + timedelta(seconds=1), p).account
        account = advance(
            account, (), (quote(at + timedelta(seconds=2), bid="95", ask="95.02"),), at + timedelta(seconds=2), p
        ).account
        account = advance(
            account, (), (quote(at + timedelta(seconds=3), bid="94", ask="94.02"),), at + timedelta(seconds=3), p
        ).account
    assert account.daily_loss >= D(200)
    result = advance(account, ds, bars(), NOW + timedelta(seconds=8), p)
    assert result.account.pending_entry is None and "daily_loss_limit" in result.events[-1].reasons


def test_mark_and_account_roundtrip_retain_executable_net_valuation():
    p, ds, account, advance = entered()
    from src.research.trader_workflow_account import WorkflowAccount

    assert WorkflowAccount.model_validate_json(account.model_dump_json()) == account
    pos = account.position
    at = NOW + timedelta(seconds=2)
    marked = advance(account, (), (quote(at, bid="104", ask="104.02"),), at, p).account
    credit = pos.quantity * D(104) * D(".9995") * D(".999")
    assert marked.equity == marked.cash + credit
    assert marked.unrealized_pnl == credit - pos.quantity * pos.unit_debit
    assert marked.valuation_at == at and marked.completed_trades == 0


def test_quote_only_entry_waits_for_current_calendar_and_regime_evidence():
    p, ds, account, advance = engine()
    from src.research.trader_workflow import select_setups
    from src.research.trader_workflow_account import advance_account

    pending = advance(account, ds, bars(), NOW, p).account
    at = NOW + timedelta(seconds=1)
    current = quote(at)
    result = advance_account(pending, (), (current,), at, p)
    assert result.account.position is None and result.account.pending_entry is not None
    blocked = select_setups(protocol(), (*bars()[:-1], current), None, at, p)
    canceled = advance_account(pending, blocked, (current,), at, p)
    assert canceled.account.position is None and canceled.account.pending_entry is None
    assert "calendar_missing" in canceled.events[0].reasons


@pytest.mark.parametrize("evidence", ["absent", "stale_decision", "mismatched_observation"])
def test_adverse_regime_requires_current_matching_observation(evidence):
    p, ds, account, advance = entered()
    at = NOW + timedelta(seconds=301 if evidence != "mismatched_observation" else 2)
    row = quote(at, bid="104", ask="104.02")
    from src.strategies.types import canonical_hash

    changed = dict(
        status="blocked",
        reasons=("trend_not_aligned_up",),
        source_key=row.source_key,
        observation_hash=canonical_hash(row.model_dump(mode="json")),
    )
    if evidence == "mismatched_observation":
        changed.update(decision_at=at, expires_at=at + timedelta(seconds=60), observation_hash="0" * 64)
    decision = ds[0].model_copy(update=changed)
    result = advance(account, (decision,), () if evidence == "absent" else (row,), at, p)
    assert result.account.pending_exit is None
    assert not any(event.kind == "exit_trigger" for event in result.events)


def test_current_matching_adverse_context_triggers_risk_exit():
    p, ds, account, advance = entered()
    at = NOW + timedelta(seconds=2)
    row = quote(at, bid="104", ask="104.02")
    from src.strategies.types import canonical_hash

    decision = ds[0].model_copy(
        update=dict(
            status="blocked",
            reasons=("trend_not_aligned_up",),
            decision_at=at,
            expires_at=at + timedelta(seconds=60),
            source_key=row.source_key,
            observation_hash=canonical_hash(row.model_dump(mode="json")),
        )
    )
    assert advance(account, (decision,), (row,), at, p).account.pending_exit.reason == "adverse_regime"


def test_partial_exit_quote_capacity_is_not_reused_by_new_bar_envelope():
    p, ds, account, advance = entered()
    at = NOW + timedelta(seconds=2)
    triggered = advance(account, (), (quote(at, bid="95", ask="95.02"),), at, p).account
    executable = quote(at + timedelta(seconds=1), bid="94", ask="94.02", size=1)
    partial = advance(triggered, (), (executable,), at + timedelta(seconds=1), p).account
    remaining = partial.position.quantity
    assert remaining == account.position.quantity - 1
    # New bar provenance cannot replenish the same quote key/time and capacity.
    changed = executable.model_copy(
        update=dict(
            source_key="new-bar-envelope",
            volume=D(999),
            received_at=at + timedelta(seconds=2),
            available_at=at + timedelta(seconds=2),
        )
    )
    from src.research.trader_workflow_account import WorkflowAccount

    resumed = WorkflowAccount.model_validate_json(partial.model_dump_json())
    repeated = advance(resumed, (), (changed,), at + timedelta(seconds=2), p)
    assert repeated.account.position.quantity == remaining
    assert repeated.account.cash == partial.cash
    assert repeated.account.fees == partial.fees
    assert not any(event.kind == "exit" for event in repeated.events)
    refreshed = quote(at + timedelta(seconds=3), bid="94", ask="94.02", size=1)
    later = advance(repeated.account, (), (refreshed,), at + timedelta(seconds=3), p)
    assert later.account.position.quantity == remaining - 1


def ratcheted_position():
    p, ds, account, advance = entered()
    at = NOW.replace(second=0) + timedelta(minutes=2, seconds=2)
    candle_end = at.replace(second=0)
    row = quote(
        at, bid="106", ask="106.02", provider_at=candle_end, open=D(105), high=D("106.5"), low=D(104), close=D(105)
    )
    raised = advance(account, (), (row,), at, p).account
    assert raised.position.stop == D("104.188145") and raised.pending_exit is None
    return p, raised, advance, at, row


@pytest.mark.parametrize("restart", [False, True])
def test_same_candle_enrichment_cannot_retroactively_hit_ratcheted_stop(restart):
    p, raised, advance, at, row = ratcheted_position()
    if restart:
        from src.research.trader_workflow_account import WorkflowAccount

        raised = WorkflowAccount.model_validate_json(raised.model_dump_json())
    later = at + timedelta(seconds=1)
    enriched = row.model_copy(
        update=dict(
            source_key="same-candle-new-quote",
            received_at=later,
            available_at=later,
            quote_source_key="later-quote",
            quote_provider_at=later,
            quote_received_at=later,
            quote_available_at=later,
        )
    )
    result = advance(raised, (), (enriched,), later, p).account
    assert result.pending_exit is None
    assert result.position.stop == D("104.188145")


def test_candle_straddling_ratchet_time_cannot_hit_new_stop_but_next_bar_can():
    p, raised, advance, at, row = ratcheted_position()
    from src.research.trader_workflow_account import WorkflowAccount

    raised = WorkflowAccount.model_validate_json(raised.model_dump_json())
    straddling_end = at.replace(second=0) + timedelta(minutes=1)
    later = straddling_end + timedelta(seconds=2)
    straddling = quote(
        later,
        bid="106",
        ask="106.02",
        provider_at=straddling_end,
        open=D(105),
        high=D("106.5"),
        low=D(104),
        close=D(105),
    )
    result = advance(raised, (), (straddling,), later, p).account
    assert result.pending_exit is None
    next_end = straddling_end + timedelta(minutes=1)
    next_at = next_end + timedelta(seconds=2)
    next_bar = quote(
        next_at, bid="106", ask="106.02", provider_at=next_end, open=D(105), high=D("106.5"), low=D(104), close=D(105)
    )
    stopped = advance(result, (), (next_bar,), next_at, p).account
    assert stopped.pending_exit.reason == "stop"


def test_quote_from_before_ratchet_cannot_hit_new_stop_after_later_receipt():
    p, raised, advance, at, row = ratcheted_position()
    later = at + timedelta(seconds=1)
    old_quote = row.model_copy(
        update=dict(
            source_key="delayed-pre-ratchet-quote",
            bid=D(104),
            ask=D("104.02"),
            received_at=later,
            available_at=later,
            quote_source_key="older-quote",
            quote_provider_at=at - timedelta(seconds=1),
            quote_received_at=later,
            quote_available_at=later,
        )
    )
    result = advance(raised, (), (old_quote,), later, p).account
    assert result.pending_exit is None
    actual = quote(later + timedelta(seconds=1), bid="104", ask="104.02")
    assert advance(result, (), (actual,), later + timedelta(seconds=1), p).account.pending_exit.reason == "stop"


@pytest.mark.parametrize("second_ratchet", [False, True])
@pytest.mark.parametrize("low,reason", [("101", "stop"), ("102", "target")])
def test_straddling_candle_uses_stop_active_at_start_before_target_after_restart(second_ratchet, low, reason):
    p, raised, advance, at, row = ratcheted_position()
    if second_ratchet:
        later = at + timedelta(seconds=1)
        refresh = row.model_copy(
            update=dict(
                source_key="second-raise",
                bid=D("106.5"),
                ask=D("106.52"),
                received_at=later,
                available_at=later,
                quote_source_key="second-raise-quote",
                quote_provider_at=later,
                quote_received_at=later,
                quote_available_at=later,
            )
        )
        raised = advance(raised, (), (refresh,), later, p).account
        assert raised.position.stop == D("104.688145")
    from src.research.trader_workflow_account import WorkflowAccount

    raised = WorkflowAccount.model_validate_json(raised.model_dump_json())
    end = at.replace(second=0) + timedelta(minutes=1)
    next_at = end + timedelta(seconds=2)
    candle = quote(
        next_at, bid="106.5", ask="106.52", provider_at=end, open=D(105), high=D(108), low=D(low), close=D(105)
    )
    result = advance(raised, (), (candle,), next_at, p).account
    assert result.pending_exit.reason == reason


@pytest.mark.parametrize("low,reason", [("104.1", "stop"), ("104.4", "target")])
def test_boundary_ratchet_is_preserved_when_later_same_minute_stop_increases(low, reason):
    p, ds, account, advance = entered()
    at = NOW.replace(second=0) + timedelta(minutes=2)
    row = quote(at, bid="106", ask="106.02", provider_at=at, open=D(105), high=D("106.5"), low=D(104), close=D(105))
    raised = advance(account, (), (row,), at, p).account
    later = at + timedelta(seconds=2)
    refresh = row.model_copy(
        update=dict(
            source_key="boundary-followup",
            bid=D("106.5"),
            ask=D("106.52"),
            received_at=later,
            available_at=later,
            quote_source_key="boundary-followup-quote",
            quote_provider_at=later,
            quote_received_at=later,
            quote_available_at=later,
        )
    )
    raised = advance(raised, (), (refresh,), later, p).account
    from src.research.trader_workflow_account import WorkflowAccount

    raised = WorkflowAccount.model_validate_json(raised.model_dump_json())
    end = at + timedelta(minutes=1)
    next_at = end + timedelta(seconds=2)
    candle = quote(
        next_at, bid="106.5", ask="106.52", provider_at=end, open=D(105), high=D(108), low=D(low), close=D(105)
    )
    assert advance(raised, (), (candle,), next_at, p).account.pending_exit.reason == reason
