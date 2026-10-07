"""Account-quote paper lifecycle, with restart-safe append-only evidence."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from src.intraday.contracts import InstrumentSpec, MarketQuote
from src.intraday.journal import PaperEvent, PaperJournal
from src.intraday.live import LiveBarBuilder
from src.intraday.paper import PaperAccount, PaperExecutionCosts, PaperPosition, PaperRiskPolicy
from src.intraday.strategies import evaluate_setup

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
        maximum_quote_gap: timedelta = timedelta(seconds=30),
    ):
        if session_close <= session_open or unit_step <= 0:
            raise ValueError("invalid paper session")
        self.instrument = instrument
        self.strategy_id = strategy_id
        self.costs = costs
        self.session_open = session_open
        self.session_close = session_close
        self.unit_step = unit_step
        self.builder = LiveBarBuilder(instrument, maximum_quote_gap=maximum_quote_gap)
        self.bars = []
        self._pending_decision = False
        self.account = PaperAccount(PaperRiskPolicy(), initial_cash, account_currency=account_currency, costs=costs)
        self.journal = PaperJournal(directory, protocol_hash)
        identity = {
            "schema_version": 2,
            "paper_only": True,
            "protocol_hash": protocol_hash,
            "instrument": instrument.model_dump(mode="json"),
            "strategy_id": strategy_id,
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
            if event.kind == "opened":
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
        self.account._day = self.session_open.date()

    def on_quote(self, quote: MarketQuote) -> PaperEvent | None:
        if quote.instrument != self.instrument:
            raise ValueError("paper quote instrument mismatch")
        if quote.received_at - quote.observed_at > timedelta(seconds=5):
            return self._append(
                "feed_gap", quote.received_at, {"reason": "stale_account_quote", "source_key": quote.source_key}
            )
        closed = self.account.update(quote)
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
                    "source_key": quote.source_key,
                },
            )
        before = self.builder.gaps
        completed = self.builder.accept(quote)
        if self.builder.gaps > before:
            self.bars.clear()
            self._pending_decision = False
            gap = self._append(
                "feed_gap",
                quote.received_at,
                {"reason": "incomplete_or_changed_account_bar", "source_key": quote.source_key},
            )
            return close_event or gap
        if close_event is not None:
            return close_event
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
                },
            )
        position = self.account.open(plan, self.instrument, quote, unit_step=self.unit_step)
        if position is None:
            return self._append(
                "no_trade",
                quote.received_at,
                {
                    "reason": self.account.last_rejection or "paper_open_rejected",
                    "strategy_id": self.strategy_id,
                    "source_key": quote.source_key,
                },
            )
        return self._append(
            "opened",
            quote.received_at,
            {
                "broker_symbol": self.instrument.broker_symbol,
                "strategy_id": position.strategy_id,
                "direction": position.direction,
                "decided_at": position.decided_at.isoformat(),
                "opened_at": position.opened_at.isoformat(),
                "account_feed_hash": position.account_feed_hash,
                "entry": str(position.entry),
                "stop": str(position.stop),
                "target": str(position.target),
                "exit_by": position.exit_by.isoformat(),
                "units": str(position.units),
                "evidence_hash": position.evidence_hash,
                "source_key": quote.source_key,
            },
        )
