"""An isolated, explicitly unqualified account-quote paper consumer."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field

from src.intraday.contracts import FrozenModel, MarketQuote
from src.intraday.experimental_round import ExperimentalRoundManifest
from src.intraday.journal import PaperEvent
from src.intraday.paper import FXConversion
from src.intraday.runtime import LivePaperRuntime
from src.intraday.session_journal import SessionJournal
from src.strategies.types import canonical_hash


class ExperimentalDeskStatus(FrozenModel):
    schema_version: Literal[1] = 1
    paper_only: Literal[True] = True
    qualification: Literal["unqualified"] = "unqualified"
    round_id: str
    cohort_id: str
    generated_at: datetime
    broker_symbol: str
    feed_health: Literal["waiting", "healthy", "stale", "closed", "error"]
    quote_age_seconds: Decimal | None = Field(default=None, ge=0)
    contiguous_bars: int = Field(ge=0)
    gap_count: int = Field(ge=0)
    no_trade_reason: str
    paper_ticket: dict | None = None


class ExperimentalPaperService:
    def __init__(self, manifest: ExperimentalRoundManifest, directory: Path):
        self.manifest = manifest
        self.directory = Path(directory)
        self.session = SessionJournal(self.directory / "capture", manifest.model_dump(mode="json"))
        self.runtime = LivePaperRuntime(
            directory=self.directory / "paper", protocol_hash=canonical_hash(manifest.model_dump(mode="json")),
            instrument=manifest.instrument, strategy_id=manifest.strategy_id,
            allowed_direction=manifest.direction,
            account_currency=manifest.account_currency, costs=manifest.costs,
            session_open=manifest.session_open, session_close=manifest.session_close,
            initial_cash=manifest.initial_paper_cash, unit_step=manifest.unit_step,
        )
        retained = self.session.events()
        self.gap_count = sum(event["kind"] == "gap" for event in retained)
        self.last_quote: MarketQuote | None = None
        self.no_trade_reason = "waiting_for_confirmed_bars"
        self._disarmed = False
        self._last_source_key = next(
            (event["payload"]["source_key"] for event in reversed(retained) if event["kind"] == "quote"), None
        )
        if retained:
            self.session.append("gap", datetime.now(manifest.session_open.tzinfo), {"reason": "process_restart"})
            self.gap_count += 1

    def disarm_new_entries(self) -> None:
        self._disarmed = True
        self.runtime.disarm_new_entries()

    def record_shutdown(self, at: datetime) -> None:
        if self.runtime.account.positions:
            self.runtime.record_unresolved_shutdown(at)

    def on_quote(self, quote: MarketQuote, *, conversion: FXConversion | None) -> PaperEvent | None:
        if quote.instrument != self.manifest.instrument or quote.account_feed_hash != self.manifest.account_feed_hash:
            raise ValueError("experimental quote does not match frozen account/product")
        if quote.source_key == self._last_source_key:
            return None
        if not self.manifest.session_open <= quote.received_at <= self.manifest.session_close and not self.runtime.account.positions:
            self.no_trade_reason = "session_closed"
            return None
        if quote.received_at > self.manifest.session_close:
            self.runtime.disarm_new_entries()
        self.session.append("quote", quote.received_at,
                            {"broker_symbol": quote.instrument.broker_symbol,
                             "source_key": quote.source_key, "observed_at": quote.observed_at.isoformat(),
                             "bid": str(quote.bid), "ask": str(quote.ask),
                             "tradeable": str(quote.status == "tradeable")})
        self._last_source_key = quote.source_key
        self.last_quote = quote
        before = self.runtime.builder.gaps
        event = self.runtime.on_quote(quote, conversion=conversion)
        if self.runtime.builder.gaps > before or (event is not None and event.kind == "feed_gap"):
            reason = "incomplete_account_bar" if self.runtime.builder.gaps > before else event.payload["reason"]
            self.session.append("gap", quote.received_at,
                                {"broker_symbol": quote.instrument.broker_symbol, "reason": reason})
            self.gap_count += 1
            self.no_trade_reason = reason
        elif event is not None and event.kind in ("planned", "no_trade"):
            self.no_trade_reason = "planned_next_quote" if event.kind == "planned" else event.payload["reason"]
            if "bar_key" in event.payload:
                self.session.append("decision", quote.received_at,
                                    {"broker_symbol": quote.instrument.broker_symbol,
                                     "bar_key": event.payload["bar_key"],
                                     "status": "ready" if event.kind == "planned" else "no_trade",
                                     "reason": self.no_trade_reason, "source_key": quote.source_key})
        elif event is not None and event.kind == "opened":
            self.no_trade_reason = "paper_position_open"
        elif event is not None and event.kind == "closed":
            self.no_trade_reason = "paper_position_closed"
        elif not self.runtime.bars:
            self.no_trade_reason = "waiting_for_confirmed_bars"
        elif not self.runtime.account.positions:
            self.no_trade_reason = "no_confirmed_setup"
        return event

    def status(self, at: datetime) -> ExperimentalDeskStatus:
        position = self.runtime.account.positions.get(self.manifest.instrument.broker_symbol)
        age = None if self.last_quote is None else max(Decimal(0), Decimal(str((at - self.last_quote.received_at).total_seconds())))
        if at >= self.manifest.session_close:
            health, reason = "closed", "session_closed"
        elif self._disarmed:
            health, reason = "stale" if age is None or age > 5 else "healthy", "entries_paused"
        elif age is None:
            health, reason = "waiting", "waiting_for_confirmed_bars"
        elif age > 5:
            health, reason = "stale", "stale_account_quote"
        else:
            health, reason = "healthy", self.no_trade_reason
        ticket = None
        if position is not None:
            ticket = {
                "qualification": "unqualified", "broker_symbol": position.instrument.broker_symbol,
                "product": position.instrument.product, "direction": position.direction,
                "units": str(position.units), "entry": str(position.entry),
                "stop": str(position.stop), "target": str(position.target),
                "opened_at": position.opened_at.isoformat(), "exit_by": position.exit_by.isoformat(),
                "evidence_hash": position.evidence_hash,
                "cost_source": self.manifest.cost_source,
            }
        return ExperimentalDeskStatus(
            round_id=self.manifest.round_id, cohort_id=self.manifest.cohort_id,
            generated_at=at, broker_symbol=self.manifest.instrument.broker_symbol,
            feed_health=health, quote_age_seconds=age,
            contiguous_bars=len(self.runtime.bars), gap_count=self.gap_count,
            no_trade_reason=reason, paper_ticket=ticket,
        )
