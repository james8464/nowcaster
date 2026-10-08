"""Multi-product, causal OANDA practice indicator; no order capability."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.intraday.contracts import InstrumentSpec, MarketQuote
from src.intraday.desk import DeskStatus, MarketStatus, OpportunityStatus
from src.intraday.live import LiveBarBuilder
from src.intraday.session_journal import SessionJournal
from src.intraday.strategies import SetupDecision, evaluate_setup
from src.strategies.types import canonical_hash


class LiveRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    strategy_id: Literal["opening_range_15", "opening_range_30", "trend_pullback", "range_reversion"]
    direction: Literal["long", "short"]
    selection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class LiveSessionWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    opened_at: datetime
    closed_at: datetime

    @model_validator(mode="after")
    def chronological(self):
        if self.opened_at.tzinfo is None or self.opened_at.utcoffset() != timedelta(0):
            raise ValueError("session must be UTC")
        if self.closed_at.tzinfo is None or self.closed_at.utcoffset() != timedelta(0):
            raise ValueError("session must be UTC")
        if self.closed_at <= self.opened_at:
            raise ValueError("session window must be positive")
        return self


class LiveRoundManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    paper_only: Literal[True] = True
    round_id: str
    account_feed_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    instruments: tuple[InstrumentSpec, ...]
    rules: dict[str, LiveRule]
    sessions: dict[str, LiveSessionWindow]

    @model_validator(mode="after")
    def matching_products(self):
        symbols = {item.broker_symbol for item in self.instruments}
        if not self.round_id.strip() or not symbols or len(symbols) != len(self.instruments):
            raise ValueError("round needs unique products")
        if set(self.rules) != symbols or set(self.sessions) != symbols:
            raise ValueError("each product needs one frozen rule and session")
        return self

    @property
    def identity_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


class LiveIndicatorSession:
    """One account stream; prospective decisions only from confirmed account bars."""

    def __init__(self, directory: Path, manifest: LiveRoundManifest, *, restart_at: datetime | None = None):
        restart_at = restart_at or datetime.now(UTC)
        if restart_at.tzinfo is None or restart_at.utcoffset() != timedelta(0):
            raise ValueError("restart time must be UTC")
        self.manifest = manifest
        self.directory = Path(directory)
        self.journal = SessionJournal(directory, manifest.model_dump(mode="json"))
        self.by_symbol = {item.broker_symbol: item for item in manifest.instruments}
        self.builders = {symbol: LiveBarBuilder(item) for symbol, item in self.by_symbol.items()}
        self.bars = {symbol: [] for symbol in self.by_symbol}
        self.pending = {symbol: False for symbol in self.by_symbol}
        events = self.journal.events()
        if events and restart_at < datetime.fromisoformat(events[-1]["at"]):
            raise ValueError("restart precedes retained event")
        self.last_source_key: dict[str, str] = {}
        self.last_quote_at: dict[str, datetime] = {}
        for event in events:
            if event["kind"] == "quote":
                payload = event["payload"]
                self.last_source_key[payload["broker_symbol"]] = payload["source_key"]
                self.last_quote_at[payload["broker_symbol"]] = datetime.fromisoformat(payload["observed_at"])
        self.decision_bars = {event["payload"]["bar_key"] for event in events if event["kind"] == "decision"}
        self.latest_opportunity: OpportunityStatus | None = None
        self.new_plan: SetupDecision | None = None
        self.last_quote: MarketQuote | None = None
        self.status = self._status(restart_at, "stale", "Awaiting fresh account quotes after start or restart.")
        if events:
            self.journal.append("gap", restart_at, {"reason": "process_restart"})
            self._publish(self.status)

    @classmethod
    def restore(
        cls, directory: Path, manifest: LiveRoundManifest, *, restart_at: datetime | None = None
    ) -> LiveIndicatorSession:
        return cls(directory, manifest, restart_at=restart_at)

    def _status(self, at: datetime, health: str, reason: str) -> DeskStatus:
        markets = []
        for item in self.manifest.instruments:
            last_quote = self.last_quote_at.get(item.broker_symbol)
            valid_quote = last_quote if last_quote is not None and at >= last_quote else None
            markets.append(
                MarketStatus(
                    market=item.market,
                    broker_symbol=item.broker_symbol,
                    product=item.product,
                    eligibility="diagnostic",
                    reason="Historical selection only; broker costs and paper eligibility remain separate.",
                    last_quote_at=valid_quote,
                    feed_age_seconds=(Decimal(str((at - valid_quote).total_seconds())) if valid_quote else None),
                )
            )
        opportunity = self.latest_opportunity
        if opportunity and (health != "healthy" or at - opportunity.entry_at > timedelta(seconds=120)):
            opportunity = None
        return DeskStatus(
            generated_at=at,
            feed_health=health,
            evidence_status="not_supported",
            markets=tuple(markets),
            opportunities=(opportunity,) if opportunity else (),
            paper_positions=(),
            no_trade_reason="" if opportunity else reason,
        )

    def _publish(self, status: DeskStatus) -> None:
        path = self.directory / "summary.json"
        temporary = self.directory / ".summary.json.tmp"
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(status.model_dump_json(indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        self.status = status

    def _gap(self, at: datetime, reason: str, *, symbol: str = "") -> DeskStatus:
        if symbol in self.by_symbol:
            self.builders[symbol] = LiveBarBuilder(self.by_symbol[symbol])
            self.bars[symbol] = []
            self.pending[symbol] = False
        self.latest_opportunity = None
        self.journal.append("gap", at, {"reason": reason, "broker_symbol": symbol})
        status = self._status(at, "stale", reason)
        self._publish(status)
        return status

    def on_event(self, raw_line: str, received_at: datetime) -> DeskStatus:
        self.new_plan = None
        self.last_quote = None
        if received_at.tzinfo is None or received_at.utcoffset() != timedelta(0):
            raise ValueError("receive time must be UTC")
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            return self._gap(received_at, "invalid_provider_event")
        if event.get("type") == "HEARTBEAT":
            return self.status
        symbol = event.get("instrument")
        if event.get("type") != "PRICE" or symbol not in self.by_symbol:
            return self._gap(received_at, "unknown_provider_event")
        try:
            observed = datetime.fromisoformat(event["time"].replace("Z", "+00:00"))
            if observed.tzinfo is None or observed.utcoffset() != timedelta(0):
                raise ValueError("provider time must be UTC")
            if observed > received_at + timedelta(seconds=2) or received_at - observed > timedelta(seconds=5):
                return self._gap(received_at, "stale_or_future_quote", symbol=symbol)
            bid = Decimal(event["bids"][0]["price"])
            ask = Decimal(event["asks"][0]["price"])
            source_key = canonical_hash(
                {
                    "round": self.manifest.identity_hash,
                    "symbol": symbol,
                    "time": observed.isoformat(),
                    "bid": str(bid),
                    "ask": str(ask),
                }
            )
            if source_key == self.last_source_key.get(symbol):
                return self.status
            quote = MarketQuote(
                instrument=self.by_symbol[symbol],
                account_feed_hash=self.manifest.account_feed_hash,
                observed_at=observed,
                received_at=received_at,
                bid=bid,
                ask=ask,
                status="tradeable" if event.get("tradeable") is True else "non_tradeable",
                source_key=source_key,
            )
        except (KeyError, IndexError, TypeError, ValueError, InvalidOperation):
            return self._gap(received_at, "invalid_account_quote", symbol=symbol)
        self.journal.append(
            "quote",
            received_at,
            {
                "broker_symbol": symbol,
                "observed_at": observed.isoformat(),
                "bid": str(bid),
                "ask": str(ask),
                "tradeable": str(quote.status == "tradeable"),
                "source_key": source_key,
            },
        )
        self.last_source_key[symbol] = source_key
        self.last_quote = quote
        previous = self.last_quote_at.get(symbol)
        self.last_quote_at[symbol] = observed
        if previous is not None and (observed <= previous or observed - previous > timedelta(seconds=30)):
            return self._gap(received_at, "quote_gap_or_reorder", symbol=symbol)
        builder = self.builders[symbol]
        before = builder.gaps
        completed = builder.accept(quote)
        if builder.gaps > before:
            return self._gap(received_at, "incomplete_account_bar", symbol=symbol)
        if completed is not None:
            if self.bars[symbol] and self.bars[symbol][-1].end != completed.start:
                self.bars[symbol] = []
            self.bars[symbol].append(completed)
            self.pending[symbol] = True
        elif self.pending[symbol] and self.bars[symbol] and observed > self.bars[symbol][-1].available_at:
            self.pending[symbol] = False
            latest = self.bars[symbol][-1]
            bar_key = latest.source_key
            if bar_key not in self.decision_bars:
                window = self.manifest.sessions[symbol]
                plan = evaluate_setup(
                    self.manifest.rules[symbol].strategy_id,
                    self.bars[symbol],
                    quote,
                    session_open=window.opened_at,
                    session_close=window.closed_at,
                )
                decision = {
                    "broker_symbol": symbol,
                    "bar_key": bar_key,
                    "status": plan.status,
                    "reason": plan.reason,
                    "strategy_id": plan.strategy_id,
                    "direction": plan.direction or "",
                }
                self.journal.append("decision", received_at, decision)
                self.decision_bars.add(bar_key)
                self.new_plan = plan
                if plan.status == "ready" and plan.direction == self.manifest.rules[symbol].direction:
                    self.latest_opportunity = OpportunityStatus(
                        market=quote.instrument.market,
                        broker_symbol=symbol,
                        strategy_id=plan.strategy_id,
                        direction=plan.direction,
                        decided_at=plan.decision_at,
                        entry_at=plan.entry_at,
                        entry=plan.entry,
                        stop=plan.stop,
                        target=plan.target,
                        exit_by=plan.exit_by,
                        estimated_roundtrip_cost=plan.estimated_roundtrip_cost,
                        explanation=plan.reason,
                        evidence_hash=plan.evidence_hash,
                    )
        status = self._status(received_at, "healthy", "No confirmed setup on the current account quotes.")
        self._publish(status)
        return status
