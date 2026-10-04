"""Immutable, quote-executable diagnostic account. No broker or file effects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any, Literal

from pydantic import Field, model_validator

from src.research.day_trader_context import ContextObservation
from src.research.round_two_contracts import _utc
from src.research.trader_workflow import HASH, WorkflowDecision, WorkflowModel, WorkflowPolicy, parse_observations
from src.research.trend_advisor import long_trade_economics
from src.strategies.types import canonical_hash

D = Decimal


class WorkflowExit(WorkflowModel):
    reason: str
    triggered_at: datetime
    source_key: str | None = None


class WorkflowPosition(WorkflowModel):
    origin: WorkflowDecision
    entry_at: datetime
    entry_source_key: str
    entry_quote_key: str
    initial_quantity: Decimal = Field(gt=0)
    quantity: Decimal = Field(gt=0)
    entry_price: Decimal = Field(gt=0)
    entry_fee: Decimal = Field(ge=0)
    entry_slippage: Decimal = Field(ge=0)
    unit_debit: Decimal = Field(gt=0)
    initial_risk: Decimal = Field(gt=0)
    stop: Decimal = Field(gt=0)
    target: Decimal = Field(gt=0)
    realized_pnl: Decimal = D(0)
    exit_notional: Decimal = Field(default=D(0), ge=0)
    exit_fees: Decimal = Field(default=D(0), ge=0)
    exit_slippage: Decimal = Field(default=D(0), ge=0)

    @model_validator(mode="after")
    def coherent_position(self):
        if self.quantity > self.initial_quantity or self.stop < self.origin.stop or self.target != self.origin.target:
            raise ValueError("position contradicts immutable origin")
        return self

    @property
    def symbol(self) -> str:
        return self.origin.symbol


class WorkflowOutcome(WorkflowModel):
    decision_id: str = Field(pattern=HASH)
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    setup: Literal["breakout", "pullback_reclaim"]
    entry_at: datetime
    exit_at: datetime
    entry_source_key: str
    exit_source_key: str
    quantity: Decimal = Field(gt=0)
    entry_price: Decimal = Field(gt=0)
    exit_price: Decimal = Field(gt=0)
    fees: Decimal = Field(ge=0)
    slippage_cost: Decimal = Field(ge=0)
    net_pnl: Decimal
    net_return: Decimal
    reason: str


class WorkflowSetupReview(WorkflowModel):
    setup: Literal["breakout", "pullback_reclaim"]
    completed: int = Field(default=0, ge=0)
    wins: int = Field(default=0, ge=0)
    losses: int = Field(default=0, ge=0)
    net_pnl: Decimal = D(0)
    fees: Decimal = Field(default=D(0), ge=0)


class WorkflowEvent(WorkflowModel):
    kind: Literal["intent", "cancel", "entry", "exit_trigger", "exit", "limit", "trailing_stop"]
    at: datetime
    symbol: str | None = None
    decision_id: str | None = None
    source_key: str | None = None
    reasons: tuple[str, ...] = ()
    quantity: Decimal | None = None
    price: Decimal | None = None
    fee: Decimal | None = None
    slippage_cost: Decimal | None = None
    net_pnl: Decimal | None = None


class WorkflowAccount(WorkflowModel):
    schema_version: Literal[1] = 1
    policy_hash: str = Field(pattern=HASH)
    protocol_hash: str = Field(pattern=HASH)
    source_identity_hash: str = Field(pattern=HASH)
    activated_at: datetime
    last_at: datetime
    utc_day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    cash: Decimal = Field(ge=0)
    equity: Decimal = Field(ge=0)
    unrealized_pnl: Decimal = D(0)
    valuation_at: datetime | None = None
    realized_pnl: Decimal = D(0)
    realized_losses: Decimal = Field(default=D(0), ge=0)
    fees: Decimal = Field(default=D(0), ge=0)
    slippage_cost: Decimal = Field(default=D(0), ge=0)
    peak_equity: Decimal = Field(default=D(10000), gt=0)
    maximum_drawdown: Decimal = Field(default=D(0), ge=0, le=1)
    daily_loss: Decimal = Field(default=D(0), ge=0)
    daily_entries: int = Field(default=0, ge=0)
    consecutive_losses: int = Field(default=0, ge=0)
    cooldown_until: datetime | None = None
    total_entries: int = Field(default=0, ge=0)
    completed_trades: int = Field(default=0, ge=0)
    total_wins: int = Field(default=0, ge=0)
    total_losses: int = Field(default=0, ge=0)
    pending_entry: WorkflowDecision | None = None
    position: WorkflowPosition | None = None
    pending_exit: WorkflowExit | None = None
    last_observation_hash: str | None = Field(default=None, pattern=HASH)
    last_consumed_quote_key: str | None = None
    last_consumed_quote_at: datetime | None = None
    last_consumed_quote_quantity: Decimal | None = Field(default=None, gt=0)
    history: tuple[WorkflowOutcome, ...] = Field(default=(), max_length=1000)
    setup_reviews: tuple[WorkflowSetupReview, ...] = Field(default=(), max_length=2)

    @model_validator(mode="after")
    def coherent_account(self):
        if self.last_at < self.activated_at or (self.valuation_at and self.valuation_at > self.last_at):
            raise ValueError("invalid account chronology")
        consumed = (self.last_consumed_quote_key, self.last_consumed_quote_at, self.last_consumed_quote_quantity)
        if any(value is not None for value in consumed) and any(value is None for value in consumed):
            raise ValueError("incomplete consumed quote identity/capacity")
        if self.last_consumed_quote_at is not None and self.last_consumed_quote_at > self.last_at:
            raise ValueError("future consumed quote")
        if self.position and self.pending_entry or self.pending_exit and not self.position:
            raise ValueError("invalid account position/intents")
        origins = [self.pending_entry] if self.pending_entry else [self.position.origin] if self.position else []
        for origin in origins:
            if (origin.policy_hash, origin.protocol_hash, origin.source_identity_hash) != (
                self.policy_hash,
                self.protocol_hash,
                self.source_identity_hash,
            ):
                raise ValueError("pending decision identity mismatch")
            if origin.status != "ready":
                raise ValueError("pending decision must be ready")
        return self

    @classmethod
    def initial(cls, policy: WorkflowPolicy, now: datetime) -> WorkflowAccount:
        policy = WorkflowPolicy.model_validate(policy.model_dump())
        now = _utc(now, "now")
        return cls(
            policy_hash=policy.identity_hash,
            protocol_hash=policy.round_protocol.identity_hash,
            source_identity_hash=policy.source_identity_hash,
            activated_at=now,
            last_at=now,
            utc_day=now.date().isoformat(),
            cash=policy.initial_cash,
            equity=policy.initial_cash,
            valuation_at=now,
        )


class WorkflowTransition(WorkflowModel):
    account: WorkflowAccount
    events: tuple[WorkflowEvent, ...]
    decisions: tuple[WorkflowDecision, ...]


def _usable_quote(row: ContextObservation | None, now: datetime, policy: WorkflowPolicy, *, entry=False) -> bool:
    if row is None or row.provider_error:
        return False
    required = (
        row.bid,
        row.bid_size,
        row.quote_provider_at,
        row.quote_received_at,
        row.quote_available_at,
        row.quote_source_key,
    )
    if any(v is None for v in required):
        return False
    if not row.quote_provider_at <= row.quote_received_at <= row.quote_available_at <= now:
        return False
    if now - row.quote_provider_at >= timedelta(seconds=policy.context_protocol.maximum_quote_age_seconds):
        return False
    if row.ask is not None and row.ask < row.bid:
        return False
    if entry:
        if any(v is None for v in (row.ask, row.ask_size, row.volume)) or row.volume <= 0:
            return False
        spread = (row.ask - row.bid) / ((row.ask + row.bid) / 2) * 10000
        if spread > policy.round_protocol.maximum_spread_bps:
            return False
        if now - row.provider_at >= timedelta(seconds=policy.round_protocol.maximum_observation_age_seconds):
            return False
    return True


def _limits(a: dict, now: datetime, policy: WorkflowPolicy) -> tuple[str, ...]:
    reasons = []
    if a["daily_loss"] >= policy.initial_cash * policy.daily_loss_fraction:
        reasons.append("daily_loss_limit")
    if a["daily_entries"] >= policy.maximum_daily_entries:
        reasons.append("daily_trade_limit")
    if a["cooldown_until"] is not None and now < a["cooldown_until"]:
        reasons.append("losing_streak_cooldown")
    return tuple(reasons)


def advance_account(
    account: WorkflowAccount,
    decisions: Sequence[WorkflowDecision | Mapping[str, Any]],
    observations: Sequence[ContextObservation | Mapping[str, Any]],
    now: datetime,
    policy: WorkflowPolicy,
) -> WorkflowTransition:
    """Advance once; the journal owns input deduplication and durable evidence.

    Entry requires a current eligible context decision bound to its later quote;
    empty decisions are management-only. Risk exits need no calendar. Closed bar
    extrema are used only when the whole minute begins after the paper entry.
    Missing context never obstructs an already pending risk-reducing exit.
    """
    now = _utc(now, "now")
    policy = WorkflowPolicy.model_validate(policy.model_dump())
    account = WorkflowAccount.model_validate(account.model_dump())
    if (account.policy_hash, account.protocol_hash, account.source_identity_hash) != (
        policy.identity_hash,
        policy.round_protocol.identity_hash,
        policy.source_identity_hash,
    ):
        raise ValueError("workflow account identity mismatch")
    if now < account.last_at:
        raise ValueError("workflow clock regression")
    ds = tuple(
        WorkflowDecision.model_validate(d.model_dump() if isinstance(d, WorkflowDecision) else d) for d in decisions
    )
    for d in ds:
        if (d.policy_hash, d.protocol_hash, d.source_identity_hash) != (
            account.policy_hash,
            account.protocol_hash,
            account.source_identity_hash,
        ):
            raise ValueError("workflow decision identity mismatch")
        if d.decision_at > now:
            raise ValueError("future workflow decision")
        if (
            account.pending_entry
            and d.source_key == account.pending_entry.source_key
            and d.status == "ready"
            and any(
                getattr(d, k) != getattr(account.pending_entry, k)
                for k in ("observation_hash", "setup", "trigger_level", "entry", "stop", "target")
            )
        ):
            raise ValueError("contradictory pending decision identity")
    rows = parse_observations(observations, policy)
    causal = [r for r in rows if r.available_at <= now]
    a = {name: getattr(account, name) for name in WorkflowAccount.model_fields}
    events = []

    def emit(kind, **fields):
        events.append(WorkflowEvent(kind=kind, at=now, **fields))

    if a["utc_day"] != now.date().isoformat():
        a.update(utc_day=now.date().isoformat(), daily_loss=D(0), daily_entries=0)
    a["last_at"] = now
    fee, slip = policy.round_protocol.fee_bps / 10000, policy.round_protocol.slippage_bps / 10000
    position = a["position"]
    pending = a["pending_entry"]
    active_symbol = position.symbol if position else pending.symbol if pending else None
    symbol_rows = [r for r in causal if r.symbol == active_symbol]
    row = max(
        symbol_rows, key=lambda r: (r.quote_available_at or r.available_at, r.available_at, r.source_key), default=None
    )
    row_hash = canonical_hash(row.model_dump(mode="json")) if row else None
    fresh_input = row is not None and row_hash != a["last_observation_hash"]
    # A new candle envelope does not replenish an already consumed book quote.
    # Provider time must advance as well as quote identity before another fill.
    unconsumed_quote = row is not None and (
        a["last_consumed_quote_at"] is None
        or (
            row.quote_provider_at is not None
            and row.quote_provider_at > a["last_consumed_quote_at"]
            and row.quote_source_key != a["last_consumed_quote_key"]
        )
    )

    if position:
        pending_exit = a["pending_exit"]
        usable = fresh_input and _usable_quote(row, now, policy)
        if pending_exit and usable and unconsumed_quote and row.quote_provider_at > pending_exit.triggered_at:
            quantity = min(position.quantity, row.bid_size)
            quantity = (quantity / policy.lot_step(position.symbol)).to_integral_value(
                rounding=ROUND_DOWN
            ) * policy.lot_step(position.symbol)
            if quantity > 0:
                price = row.bid * (1 - slip)
                exit_fee, exit_slip = quantity * price * fee, quantity * row.bid * slip
                credit = quantity * price - exit_fee
                pnl = credit - quantity * position.unit_debit
                loss = max(D(0), -pnl)
                a.update(
                    cash=a["cash"] + credit,
                    realized_pnl=a["realized_pnl"] + pnl,
                    realized_losses=a["realized_losses"] + loss,
                    daily_loss=a["daily_loss"] + loss,
                    fees=a["fees"] + exit_fee,
                    slippage_cost=a["slippage_cost"] + exit_slip,
                    last_consumed_quote_key=row.quote_source_key,
                    last_consumed_quote_at=row.quote_provider_at,
                    last_consumed_quote_quantity=quantity,
                )
                emit(
                    "exit",
                    symbol=position.symbol,
                    decision_id=position.origin.decision_id,
                    source_key=row.source_key,
                    reasons=(pending_exit.reason,),
                    quantity=quantity,
                    price=price,
                    fee=exit_fee,
                    slippage_cost=exit_slip,
                    net_pnl=pnl,
                )
                remaining = position.quantity - quantity
                total_pnl = position.realized_pnl + pnl
                if remaining:
                    position = position.model_copy(
                        update=dict(
                            quantity=remaining,
                            realized_pnl=total_pnl,
                            exit_notional=position.exit_notional + quantity * price,
                            exit_fees=position.exit_fees + exit_fee,
                            exit_slippage=position.exit_slippage + exit_slip,
                        )
                    )
                    a["position"] = position
                else:
                    outcome = WorkflowOutcome(
                        decision_id=position.origin.decision_id,
                        symbol=position.symbol,
                        setup=position.origin.setup,
                        entry_at=position.entry_at,
                        exit_at=now,
                        entry_source_key=position.entry_source_key,
                        exit_source_key=row.source_key,
                        quantity=position.initial_quantity,
                        entry_price=position.entry_price,
                        exit_price=(position.exit_notional + quantity * price) / position.initial_quantity,
                        fees=position.entry_fee + position.exit_fees + exit_fee,
                        slippage_cost=position.entry_slippage + position.exit_slippage + exit_slip,
                        net_pnl=total_pnl,
                        net_return=total_pnl / (position.initial_quantity * position.unit_debit),
                        reason=pending_exit.reason,
                    )
                    a["history"] = (*a["history"], outcome)[-policy.history_limit :]
                    a["completed_trades"] += 1
                    a["total_wins"] += int(total_pnl > 0)
                    a["total_losses"] += int(total_pnl < 0)
                    a["consecutive_losses"] = a["consecutive_losses"] + 1 if total_pnl < 0 else 0
                    if a["consecutive_losses"] >= policy.losing_close_limit:
                        a["cooldown_until"] = now + timedelta(seconds=policy.cooldown_seconds)
                    review = next(
                        (r for r in a["setup_reviews"] if r.setup == outcome.setup),
                        WorkflowSetupReview(setup=outcome.setup),
                    )
                    review = review.model_copy(
                        update=dict(
                            completed=review.completed + 1,
                            wins=review.wins + int(total_pnl > 0),
                            losses=review.losses + int(total_pnl < 0),
                            net_pnl=review.net_pnl + total_pnl,
                            fees=review.fees + outcome.fees,
                        )
                    )
                    a["setup_reviews"] = tuple(
                        sorted(
                            (*[r for r in a["setup_reviews"] if r.setup != review.setup], review), key=lambda r: r.setup
                        )
                    )
                    a.update(position=None, pending_exit=None, equity=a["cash"], unrealized_pnl=D(0), valuation_at=now)
                    position = None
        elif not pending_exit:
            reason = None
            complete_bar = fresh_input and row.finalized and row.provider_at - timedelta(minutes=1) >= position.entry_at
            low = row.low if complete_bar else None
            high = row.high if complete_bar else None
            if (usable and row.bid <= position.stop) or (low is not None and low <= position.stop):
                reason = "stop"
            elif (usable and row.bid >= position.target) or (high is not None and high >= position.target):
                reason = "target"
            elif usable and row.bid <= position.origin.trigger_level:
                reason = "invalidation"
            elif now - position.entry_at >= timedelta(seconds=policy.maximum_holding_seconds):
                reason = "maximum_holding"
            elif any(
                d.symbol == position.symbol
                and usable
                and d.decision_at == now
                and d.source_key == row.source_key
                and d.observation_hash == row_hash
                and d.context_hash
                and any(
                    r in d.reasons
                    for r in ("trend_not_aligned_up", "range_regime", "volatility_exceeds_maximum", "abnormal_range")
                )
                and not any(
                    r.startswith("insufficient_")
                    or r
                    in (
                        "observation_stale",
                        "order_book_stale",
                        "history_gap",
                        "conflicting_observations",
                        "provider_error",
                        "unfinalized_input",
                        "unaligned_bar",
                        "atr_unavailable",
                        "volatility_unavailable",
                    )
                    for r in d.reasons
                )
                for d in ds
            ):
                reason = "adverse_regime"
            if reason:
                a["pending_exit"] = WorkflowExit(
                    reason=reason, triggered_at=now, source_key=row.source_key if row else None
                )
                emit(
                    "exit_trigger",
                    symbol=position.symbol,
                    decision_id=position.origin.decision_id,
                    source_key=row.source_key if row else None,
                    reasons=(reason,),
                )
            elif usable and row.bid - position.entry_price >= position.initial_risk:
                new_stop = max(position.stop, row.bid - position.initial_risk)
                if new_stop > position.stop:
                    position = position.model_copy(update={"stop": new_stop})
                    a["position"] = position
                    emit("trailing_stop", symbol=position.symbol, price=new_stop, source_key=row.source_key)
        if position and usable:
            value = position.quantity * row.bid * (1 - slip) * (1 - fee)
            a.update(
                equity=a["cash"] + value,
                unrealized_pnl=value - position.quantity * position.unit_debit,
                valuation_at=now,
            )
        if fresh_input:
            a["last_observation_hash"] = row_hash

    elif pending:
        if any(
            r.source_key == pending.source_key and canonical_hash(r.model_dump(mode="json")) != pending.observation_hash
            for r in symbol_rows
        ):
            raise ValueError("pending observation source identity changed")
        reasons = _limits(a, now, policy)
        if now >= pending.expires_at:
            reasons += ("intent_expired",)
        if row and row.provider_at - pending.observation_at > timedelta(minutes=1):
            reasons += ("history_gap",)
        current = next((d for d in ds if d.symbol == pending.symbol), None)
        if current and current.status == "blocked":
            reasons += current.reasons
        if reasons:
            a["pending_entry"] = None
            emit("cancel", symbol=pending.symbol, decision_id=pending.decision_id, reasons=reasons)
        elif (
            current is not None
            and current.decision_at == now
            and current.calendar_available
            and current.status in ("ready", "watching")
            and current.reasons in ((), ("setup_not_triggered",))
            and current.observation_hash == row_hash
            and fresh_input
            and unconsumed_quote
            and _usable_quote(row, now, policy, entry=True)
            and row.quote_provider_at > pending.decision_at
        ):
            if row.bid <= pending.trigger_level:
                reasons = ("trigger_invalidated",)
            elif not pending.stop < row.bid <= row.ask < pending.target:
                reasons = ("invalid_setup_levels",)
            else:
                economics = long_trade_economics(
                    bid=row.bid,
                    ask=row.ask,
                    stop=pending.stop,
                    target=pending.target,
                    fee_bps=policy.round_protocol.fee_bps,
                    slippage_bps=policy.round_protocol.slippage_bps,
                )
                reasons = economics.reasons
            if reasons:
                a["pending_entry"] = None
                emit("cancel", symbol=pending.symbol, decision_id=pending.decision_id, reasons=reasons)
            else:
                price = row.ask * (1 + slip)
                debit = price * (1 + fee)
                risk = debit - pending.stop * (1 - slip) * (1 - fee)
                quantity = min(
                    a["cash"] / debit,
                    a["equity"] * policy.exposure_fraction / debit,
                    a["equity"] * policy.risk_fraction / risk,
                    row.volume * policy.volume_participation,
                    row.ask_size,
                )
                step = policy.lot_step(pending.symbol)
                quantity = (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
                if quantity > 0:
                    entry_fee, entry_slip = quantity * price * fee, quantity * row.ask * slip
                    position = WorkflowPosition(
                        origin=pending,
                        entry_at=now,
                        entry_source_key=row.source_key,
                        entry_quote_key=row.quote_source_key,
                        initial_quantity=quantity,
                        quantity=quantity,
                        entry_price=price,
                        entry_fee=entry_fee,
                        entry_slippage=entry_slip,
                        unit_debit=debit,
                        initial_risk=price - pending.stop,
                        stop=pending.stop,
                        target=pending.target,
                    )
                    a.update(
                        position=position,
                        pending_entry=None,
                        cash=a["cash"] - quantity * debit,
                        daily_entries=a["daily_entries"] + 1,
                        total_entries=a["total_entries"] + 1,
                        fees=a["fees"] + entry_fee,
                        slippage_cost=a["slippage_cost"] + entry_slip,
                        last_consumed_quote_key=row.quote_source_key,
                        last_consumed_quote_at=row.quote_provider_at,
                        last_consumed_quote_quantity=quantity,
                    )
                    value = quantity * row.bid * (1 - slip) * (1 - fee)
                    a.update(equity=a["cash"] + value, unrealized_pnl=value - quantity * debit, valuation_at=now)
                    emit(
                        "entry",
                        symbol=pending.symbol,
                        decision_id=pending.decision_id,
                        source_key=row.source_key,
                        quantity=quantity,
                        price=price,
                        fee=entry_fee,
                        slippage_cost=entry_slip,
                    )
                else:
                    a["pending_entry"] = None
                    emit("cancel", symbol=pending.symbol, reasons=("size_below_lot",))
            a["last_observation_hash"] = row_hash

    else:
        ready = next(
            (d for d in ds if d.status == "ready" and d.decision_at >= account.activated_at and d.expires_at > now),
            None,
        )
        if ready:
            reasons = _limits(a, now, policy)
            if reasons:
                emit("limit", symbol=ready.symbol, decision_id=ready.decision_id, reasons=reasons)
            else:
                # Bind the exact origin input; never register an invented setup.
                if not any(
                    r.source_key == ready.source_key
                    and canonical_hash(r.model_dump(mode="json")) == ready.observation_hash
                    for r in causal
                ):
                    raise ValueError("pending decision source identity unavailable")
                a["pending_entry"] = ready
                emit("intent", symbol=ready.symbol, decision_id=ready.decision_id, source_key=ready.source_key)
    a["peak_equity"] = max(a["peak_equity"], a["equity"])
    a["maximum_drawdown"] = max(a["maximum_drawdown"], (a["peak_equity"] - a["equity"]) / a["peak_equity"])
    return WorkflowTransition(account=WorkflowAccount.model_validate(a), events=tuple(events), decisions=ds)
