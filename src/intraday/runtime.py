"""Account-quote paper lifecycle, with restart-safe append-only evidence."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from src.intraday.contracts import InstrumentSpec, MarketQuote
from src.intraday.journal import PaperEvent, PaperJournal
from src.intraday.live import LiveBarBuilder
from src.intraday.paper import FXConversion, PaperAccount, PaperExecutionCosts, PaperPosition, PaperRiskPolicy
from src.intraday.strategies import SetupDecision, evaluate_setup
from src.strategies.types import canonical_hash

D = Decimal


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class LivePaperRuntime:
    """Experimental paper decisions only. No broker order methods are reachable."""

    def __init__(
        self,
        *,
        directory: Path,
        protocol_hash: str,
        instrument: InstrumentSpec,
        strategy_id: str,
        account_currency: str,
        costs: PaperExecutionCosts,
        session_open: datetime,
        session_close: datetime,
        initial_cash: Decimal,
        unit_step: Decimal,
        allowed_direction: Literal["long", "short", "both"] = "both",
        margin_rate: Decimal = D(1),
        cost_source: str = "verified paper cost policy",
        maximum_quote_gap: timedelta = timedelta(seconds=30),
    ):
        if session_close <= session_open or unit_step <= 0 or not 0 < margin_rate <= 1 or not cost_source.strip():
            raise ValueError("invalid paper session")
        self.instrument = instrument
        self.strategy_id = strategy_id
        self.allowed_direction = allowed_direction
        self.margin_rate = margin_rate
        self.cost_source = cost_source
        self.costs = costs
        self.session_open = session_open
        self.session_close = session_close
        self.unit_step = unit_step
        self.maximum_quote_gap = maximum_quote_gap
        self.builder = LiveBarBuilder(instrument, maximum_quote_gap=maximum_quote_gap)
        self.bars = []
        self._pending_decision = False
        self._pending_plan: tuple[SetupDecision, str, str] | None = None
        self._last_quote_received: datetime | None = None
        self._last_quote_observed: datetime | None = None
        self._seen_trade_quote_keys: set[str] = set()
        self._trade_quote_fingerprints: dict[str, str] = {}
        self._conflicted_keys: set[str] = set()
        self._entries_disarmed = False
        self.account = PaperAccount(PaperRiskPolicy(), initial_cash, account_currency=account_currency, costs=costs)
        self.journal = PaperJournal(directory, protocol_hash)
        identity = {
            "schema_version": 2,
            "paper_only": True,
            "protocol_hash": protocol_hash,
            "instrument": instrument.model_dump(mode="json"),
            "strategy_id": strategy_id,
            "allowed_direction": allowed_direction,
            "margin_rate": str(margin_rate),
            "cost_source": cost_source,
            "account_currency": account_currency,
            "costs": costs.model_dump(mode="json"),
            "initial_cash": str(initial_cash),
            "unit_step": str(unit_step),
            "maximum_quote_gap_seconds": maximum_quote_gap.total_seconds(),
        }
        manifest = self.journal.directory / "runtime.json"
        if not manifest.exists():
            if self.journal.events():
                raise ValueError("runtime identity missing for retained paper events")
            with manifest.open("x", encoding="utf-8") as stream:
                json.dump(identity, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
        if json.loads(manifest.read_text(encoding="utf-8")) != identity:
            raise ValueError("paper runtime identity changed; start a new study")
        self._restore()
        self._ensure_session_window()

    def _append(self, kind: str, at: datetime, payload: dict[str, str]) -> PaperEvent:
        with self.journal as journal:
            return journal.append(kind, at, payload)

    def _ensure_session_window(self) -> None:
        declared = [event for event in self.journal.events() if event.kind == "session_window"]
        current = {"open": self.session_open.isoformat(), "close": self.session_close.isoformat()}
        if declared:
            previous = declared[-1].payload
            if previous == current:
                return  # restart of the exact same session preserves every event
            if self.session_open < _time(previous["close"]):
                raise ValueError("paper session windows overlap or move backward")
            if self.account.positions:
                raise ValueError("unresolved prior paper position blocks a new session")
        self._append("session_window", self.session_open, current)

    def _restore(self) -> None:
        for event in self.journal.events():
            payload = event.payload
            if event.kind == "planned":
                plan = SetupDecision.model_validate_json(payload["plan_json"])
                if plan.status != "ready" or plan.strategy_id != self.strategy_id:
                    raise ValueError("retained paper plan identity invalid")
                self._pending_plan = (plan, payload["source_key"], payload["account_feed_hash"])
                self._seen_trade_quote_keys.add(payload["source_key"])
                if "quote_fingerprint" in payload:
                    self._trade_quote_fingerprints[payload["source_key"]] = payload["quote_fingerprint"]
                self._last_quote_received = event.occurred_at
            elif event.kind == "opened":
                self._pending_plan = None
                self._seen_trade_quote_keys.add(payload["source_key"])
                if "quote_fingerprint" in payload:
                    self._trade_quote_fingerprints[payload["source_key"]] = payload["quote_fingerprint"]
                self._last_quote_received = event.occurred_at
                if payload["broker_symbol"] in self.account.positions:
                    raise ValueError("duplicate retained paper position")
                position = PaperPosition(
                    instrument=self.instrument,
                    strategy_id=payload["strategy_id"],
                    direction=payload["direction"],
                    decided_at=_time(payload["decided_at"]),
                    opened_at=_time(payload["opened_at"]),
                    account_feed_hash=payload["account_feed_hash"],
                    entry=D(payload["entry"]),
                    stop=D(payload["stop"]),
                    target=D(payload["target"]),
                    exit_by=_time(payload["exit_by"]),
                    units=D(payload["units"]),
                    evidence_hash=payload["evidence_hash"],
                )
                if payload["broker_symbol"] != self.instrument.broker_symbol:
                    raise ValueError("retained position instrument mismatch")
                self.account.positions[payload["broker_symbol"]] = position
                if event.occurred_at.date() == self.session_open.date():
                    self.account.daily_entries += 1
            elif event.kind == "closed":
                self._seen_trade_quote_keys.add(payload["source_key"])
                if "quote_fingerprint" in payload:
                    self._trade_quote_fingerprints[payload["source_key"]] = payload["quote_fingerprint"]
                self._last_quote_received = event.occurred_at
                if payload["broker_symbol"] not in self.account.positions:
                    raise ValueError("retained close has no open position")
                del self.account.positions[payload["broker_symbol"]]
                pnl = D(payload["net_pnl"])
                if not pnl.is_finite():
                    raise ValueError("retained paper P&L invalid")
                self.account.equity += pnl
                if event.occurred_at.date() == self.session_open.date():
                    self.account.daily_realized_pnl += pnl
                self.account.high_water = max(self.account.high_water, self.account.equity)
            elif event.kind == "feed_gap":
                self._pending_plan = None
                self._last_quote_received = max(self._last_quote_received or event.occurred_at, event.occurred_at)
                if payload.get("reason") == "conflicting_account_quote":
                    self._conflicted_keys.add(payload["source_key"])
            elif event.kind == "no_trade" and self._pending_plan is not None and payload.get("decision_source_key") == self._pending_plan[1]:
                self._pending_plan = None
        self.account._day = self.session_open.date()

    def disarm_new_entries(self) -> None:
        self._entries_disarmed = True

    def record_unresolved_shutdown(self, at: datetime) -> PaperEvent | None:
        if not self.account.positions:
            return None
        return self._append("managed", at,
                            {"reason": "unresolved_at_shutdown", "broker_symbol": self.instrument.broker_symbol})

    def on_quote(self, quote: MarketQuote, conversion: FXConversion | None = None) -> PaperEvent | None:
        if quote.instrument != self.instrument:
            raise ValueError("paper quote instrument mismatch")
        if quote.source_key in self._seen_trade_quote_keys:
            fingerprint = self._trade_quote_fingerprints.get(quote.source_key)
            if fingerprint is not None and fingerprint != canonical_hash(quote.model_dump(mode="json")) and quote.source_key not in self._conflicted_keys:
                self._conflicted_keys.add(quote.source_key)
                return self._append("feed_gap", quote.received_at,
                                    {"reason": "conflicting_account_quote", "source_key": quote.source_key})
            return None
        if self._last_quote_received is not None and quote.received_at <= self._last_quote_received:
            return self._append("feed_gap", quote.received_at,
                                {"reason": "reordered_account_quote", "source_key": quote.source_key})
        if quote.received_at - quote.observed_at > timedelta(seconds=5):
            return self._append(
                "feed_gap", quote.received_at, {"reason": "stale_account_quote", "source_key": quote.source_key}
            )
        timing_gap = self._last_quote_observed is not None and (
            quote.observed_at <= self._last_quote_observed
            or quote.observed_at - self._last_quote_observed > self.maximum_quote_gap
        )
        self._last_quote_observed = quote.observed_at
        self._last_quote_received = quote.received_at
        closed = self.account.update(quote, conversion=conversion)
        close_event = None
        if closed is not None:
            close_event = self._append(
                "closed",
                quote.received_at,
                {
                    "broker_symbol": self.instrument.broker_symbol,
                    "exit_reason": closed.exit_reason,
                    "exit_price": str(closed.exit_price),
                    "net_pnl": str(closed.net_pnl),
                    "net_pnl_gbp": str(closed.net_pnl),
                    "gross_pnl_gbp": str(closed.gross_pnl_gbp),
                    "commission_gbp": str(closed.commission_gbp),
                    "financing_gbp": str(closed.financing_gbp),
                    "conversion_fee_gbp": str(closed.conversion_fee_gbp),
                    "source_key": quote.source_key,
                    "quote_fingerprint": canonical_hash(quote.model_dump(mode="json")),
                },
            )
            self._seen_trade_quote_keys.add(quote.source_key)
            self._trade_quote_fingerprints[quote.source_key] = canonical_hash(quote.model_dump(mode="json"))
        if timing_gap:
            self.builder = LiveBarBuilder(self.instrument, maximum_quote_gap=self.maximum_quote_gap)
            self.builder.accept(quote)
            self.bars.clear()
            self._pending_decision = False
            self._pending_plan = None
            gap = self._append("feed_gap", quote.received_at,
                               {"reason": "quote_gap_or_reorder", "source_key": quote.source_key})
            return close_event or gap
        before = self.builder.gaps
        completed = self.builder.accept(quote)
        if self.builder.gaps > before:
            self.bars.clear()
            self._pending_decision = False
            self._pending_plan = None
            gap = self._append(
                "feed_gap",
                quote.received_at,
                {"reason": "incomplete_or_changed_account_bar", "source_key": quote.source_key},
            )
            return close_event or gap
        if close_event is not None:
            return close_event
        if self._pending_plan is not None:
            plan, decision_source_key, feed_hash = self._pending_plan
            self._pending_plan = None
            if self._entries_disarmed:
                reason = "entries_disarmed"
                position = None
            elif (
                quote.source_key == decision_source_key
                or quote.account_feed_hash != feed_hash
                or quote.received_at <= plan.entry_at
                or quote.observed_at <= plan.entry_at
            ):
                reason = "entry_quote_identity_or_time_invalid"
                position = None
            elif quote.received_at - plan.decision_at > timedelta(seconds=15):
                reason = "entry_window_expired"
                position = None
            else:
                position = self.account.open(plan, self.instrument, quote, unit_step=self.unit_step, conversion=conversion)
                reason = self.account.last_rejection or "paper_open_rejected"
            if position is None:
                return self._append("no_trade", quote.received_at,
                                    {"reason": reason, "strategy_id": self.strategy_id,
                                     "source_key": quote.source_key, "decision_source_key": decision_source_key})
            self._seen_trade_quote_keys.add(quote.source_key)
            self._trade_quote_fingerprints[quote.source_key] = canonical_hash(quote.model_dump(mode="json"))
            value_rate = D(1) if self.instrument.quote_currency == self.account.account_currency else conversion.position_value
            loss_rate = D(1) if self.instrument.quote_currency == self.account.account_currency else conversion.account_loss
            notional = position.entry * self.instrument.point_value * position.units * value_rate
            expected_cost = ((quote.spread + 2 * self.costs.slippage_points) * self.instrument.point_value
                             + self.costs.commission_per_unit + self.costs.financing_per_unit) * position.units * loss_rate
            return self._append(
                "opened", quote.received_at,
                {"broker_symbol": self.instrument.broker_symbol, "strategy_id": position.strategy_id,
                 "direction": position.direction, "decided_at": position.decided_at.isoformat(),
                 "opened_at": position.opened_at.isoformat(), "account_feed_hash": position.account_feed_hash,
                 "entry": str(position.entry), "stop": str(position.stop), "target": str(position.target),
                 "exit_by": position.exit_by.isoformat(), "units": str(position.units),
                 "product": self.instrument.product, "quote_currency": self.instrument.quote_currency,
                 "notional_gbp": str(notional), "effective_leverage": str(notional / self.account.equity),
                 "margin_estimate_gbp": str(notional * self.margin_rate),
                 "estimated_roundtrip_cost_gbp": str(expected_cost), "cost_source": self.cost_source,
                 "qualification": "unqualified",
                 "evidence_hash": position.evidence_hash, "source_key": quote.source_key,
                 "quote_fingerprint": canonical_hash(quote.model_dump(mode="json")),
                 "decision_source_key": decision_source_key},
            )
        if completed is not None:
            if self.bars and self.bars[-1].end != completed.start:
                self.bars.clear()
            self.bars.append(completed)
            self._pending_decision = self.session_open <= completed.start < completed.end <= self.session_close
            # The sealing quote cannot act on a bar that became available
            # only when that same quote was received. Wait for a later quote.
            return None
        if not self._pending_decision or not self.bars or quote.observed_at <= self.bars[-1].available_at:
            return None
        self._pending_decision = False
        plan = evaluate_setup(
            self.strategy_id, self.bars, quote, session_open=self.session_open, session_close=self.session_close
        )
        if plan.status == "no_trade":
            return self._append(
                "no_trade",
                quote.received_at,
                {
                    "reason": plan.reason,
                    "strategy_id": self.strategy_id,
                    "source_key": quote.source_key,
                    "bar_key": self.bars[-1].source_key,
                },
            )
        if self._entries_disarmed:
            return self._append("no_trade", quote.received_at,
                                {"reason": "entries_disarmed", "strategy_id": self.strategy_id,
                                 "source_key": quote.source_key, "bar_key": self.bars[-1].source_key})
        if self.allowed_direction != "both" and plan.direction != self.allowed_direction:
            return self._append("no_trade", quote.received_at,
                                {"reason": "declared_direction_mismatch", "strategy_id": self.strategy_id,
                                 "source_key": quote.source_key, "bar_key": self.bars[-1].source_key})
        self._pending_plan = (plan, quote.source_key, quote.account_feed_hash)
        self._seen_trade_quote_keys.add(quote.source_key)
        self._trade_quote_fingerprints[quote.source_key] = canonical_hash(quote.model_dump(mode="json"))
        return self._append(
            "planned",
            quote.received_at,
            {
                "strategy_id": self.strategy_id,
                "account_feed_hash": quote.account_feed_hash,
                "plan_json": plan.model_dump_json(),
                "source_key": quote.source_key,
                "bar_key": self.bars[-1].source_key,
                "quote_fingerprint": canonical_hash(quote.model_dump(mode="json")),
            },
        )
