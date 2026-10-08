"""Immutable research identity and prospective evidence gates."""

from __future__ import annotations

import fcntl
import json
import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from math import ceil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.intraday.contracts import InstrumentSpec
from src.strategies.types import canonical_hash

D = Decimal
STRATEGIES = frozenset({"opening_range_15", "opening_range_30", "trend_pullback", "range_reversion"})


@dataclass(frozen=True)
class ResearchAttempt:
    stage: str
    strategy_id: str
    broker_symbol: str
    net_pnl: Decimal
    stressed_net_pnl: Decimal
    closed_trades: int
    data_hash: str
    attempt_hash: str


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    @field_validator("*", mode="after")
    @classmethod
    def explicit_utc(cls, value):
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
            raise ValueError("research time must be explicit UTC")
        return value


class IntradayRound(FrozenModel):
    schema_version: Literal[1] = 1
    paper_only: Literal[True] = True
    round_id: str
    instruments: tuple[InstrumentSpec, ...]
    strategy_ids: tuple[str, ...]
    train_start: datetime
    validation_start: datetime
    sealed_start: datetime
    sealed_end: datetime
    minimum_historical_coverage: Decimal = D("0.995")
    minimum_account_quote_coverage: Decimal = D("0.99")
    minimum_forward_days: int = 90
    minimum_closed_trades: int = 100
    maximum_drawdown: Decimal = D("0.05")
    stress_spread_multiple: Decimal = D("2")

    @model_validator(mode="after")
    def chronology_and_inventory(self):
        if not self.train_start < self.validation_start < self.sealed_start < self.sealed_end:
            raise ValueError("round windows must be strictly chronological")
        if not self.round_id.strip() or not self.instruments or not self.strategy_ids:
            raise ValueError("round needs ID, instruments and strategies")
        if len({item.broker_symbol for item in self.instruments}) != len(self.instruments):
            raise ValueError("duplicate instrument")
        if len(set(self.strategy_ids)) != len(self.strategy_ids) or not set(self.strategy_ids) <= STRATEGIES:
            raise ValueError("unsupported or duplicate strategy")
        if not (D(0) < self.minimum_historical_coverage <= 1 and D(0) < self.minimum_account_quote_coverage <= 1):
            raise ValueError("coverage thresholds invalid")
        if self.minimum_forward_days < 90 or self.minimum_closed_trades < 100:
            raise ValueError("prospective minimum window and trade count cannot be weakened")
        if not D(0) < self.maximum_drawdown <= D("0.05"):
            raise ValueError("maximum drawdown limit cannot be weakened")
        if self.stress_spread_multiple < D(2):
            raise ValueError("cost stress multiple cannot be weakened")
        return self

    @property
    def identity_hash(self) -> str:
        return canonical_hash(type(self).model_validate(self.model_dump()).model_dump(mode="json"))


class ResearchCatalog:
    """Append-only round inventory. Sealed data may be inspected only once."""

    def __init__(self, directory: Path, protocol: IntradayRound):
        self.directory = Path(directory).expanduser().resolve()
        if {"ProspectiveStudies", "live-paper-study"} & set(self.directory.parts):
            raise ValueError("protected study path")
        self.protocol = protocol
        self.directory.mkdir(parents=True, exist_ok=True)
        self.manifest = self.directory / "protocol.json"
        self.records = self.directory / "attempts.jsonl"
        self.lock = self.directory / ".writer.lock"
        data = protocol.model_dump(mode="json")
        if not self.manifest.exists():
            if self.records.exists():
                raise ValueError("attempts have no protocol")
            with self.manifest.open("x", encoding="utf-8") as stream:
                json.dump(data, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
        if json.loads(self.manifest.read_text(encoding="utf-8")) != data:
            raise ValueError("research protocol mismatch")
        self._events()

    def _events(self) -> list[dict]:
        if not self.records.exists():
            return []
        content = self.records.read_bytes()
        if content and not content.endswith(b"\n"):
            raise ValueError("unterminated research attempt")
        events: list[dict] = []
        previous = "0" * 64
        for line in content.splitlines():
            event = json.loads(line)
            digest = event.pop("attempt_hash")
            if event.pop("previous_hash") != previous or digest != canonical_hash({**event, "previous_hash": previous}):
                raise ValueError("research attempt hash chain mismatch")
            event["attempt_hash"] = digest
            events.append(event)
            previous = digest
        return events

    def attempts(self) -> tuple[ResearchAttempt, ...]:
        return tuple(
            ResearchAttempt(
                stage=event["stage"],
                strategy_id=event["strategy_id"],
                broker_symbol=event["broker_symbol"],
                net_pnl=D(event["net_pnl"]),
                stressed_net_pnl=D(event["stressed_net_pnl"]),
                closed_trades=event["closed_trades"],
                data_hash=event["data_hash"],
                attempt_hash=event["attempt_hash"],
            )
            for event in self._events()
            if event["kind"] == "attempt"
        )

    def _append(self, event: dict) -> dict:
        with self.lock.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            previous_events = self._events()
            previous = previous_events[-1]["attempt_hash"] if previous_events else "0" * 64
            if event["kind"] == "selection" and any(
                item["kind"] == "selection" and item["broker_symbol"] == event["broker_symbol"]
                for item in previous_events
            ):
                raise ValueError("selection already frozen")
            if event["kind"] == "attempt" and event["stage"] == "sealed":
                selected = [
                    item
                    for item in previous_events
                    if item["kind"] == "selection" and item["broker_symbol"] == event["broker_symbol"]
                ]
                if not selected or (selected[0]["strategy_id"], selected[0]["broker_symbol"]) != (
                    event["strategy_id"],
                    event["broker_symbol"],
                ):
                    raise ValueError("sealed assessment requires frozen selection")
                if any(
                    item["kind"] == "attempt"
                    and item["stage"] == "sealed"
                    and item["broker_symbol"] == event["broker_symbol"]
                    for item in previous_events
                ):
                    raise ValueError("sealed period can only be run once")
            full = {**event, "previous_hash": previous}
            full["attempt_hash"] = canonical_hash(full)
            with self.records.open("ab") as stream:
                stream.write((json.dumps(full, sort_keys=True) + "\n").encode())
                stream.flush()
                os.fsync(stream.fileno())
            fcntl.flock(lock, fcntl.LOCK_UN)
        return full

    def record_attempt(
        self,
        stage: Literal["train", "validation", "sealed"],
        strategy_id: str,
        broker_symbol: str,
        net_pnl: Decimal,
        stressed_net_pnl: Decimal,
        closed_trades: int,
        data_hash: str,
    ) -> ResearchAttempt:
        if stage not in ("train", "validation", "sealed") or strategy_id not in self.protocol.strategy_ids:
            raise ValueError("attempt not in protocol")
        if broker_symbol not in {item.broker_symbol for item in self.protocol.instruments} or not data_hash:
            raise ValueError("instrument/data not in protocol")
        if closed_trades < 0 or not net_pnl.is_finite() or not stressed_net_pnl.is_finite():
            raise ValueError("invalid attempt result")
        full = self._append(
            {
                "kind": "attempt",
                "stage": stage,
                "strategy_id": strategy_id,
                "broker_symbol": broker_symbol,
                "net_pnl": str(net_pnl),
                "stressed_net_pnl": str(stressed_net_pnl),
                "closed_trades": closed_trades,
                "data_hash": data_hash,
            }
        )
        return ResearchAttempt(
            stage, strategy_id, broker_symbol, net_pnl, stressed_net_pnl, closed_trades, data_hash, full["attempt_hash"]
        )

    def freeze_selection(self, strategy_id: str, broker_symbol: str) -> str:
        selected = [
            item for item in self.attempts() if item.strategy_id == strategy_id and item.broker_symbol == broker_symbol
        ]
        if {item.stage for item in selected} != {"train", "validation"} or any(
            item.net_pnl <= 0 or item.stressed_net_pnl <= 0 for item in selected
        ):
            raise ValueError("selection needs positive train and validation after stressed costs")
        return self._append({"kind": "selection", "strategy_id": strategy_id, "broker_symbol": broker_symbol})[
            "attempt_hash"
        ]


class ProspectiveEvidence(FrozenModel):
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: datetime
    assessed_at: datetime
    account_quote_coverage: Decimal = Field(ge=0, le=1)
    historical_bar_coverage: Decimal = Field(ge=0, le=1)
    material_feed_gaps: int = Field(ge=0)
    maximum_drawdown: Decimal = Field(ge=0, le=1)
    daily_net_pnl: tuple[tuple[datetime, Decimal], ...]
    closed_trades: int = Field(ge=0)
    frozen_rule: bool
    account_specific_quotes: bool
    execution_costs_verified: bool = False
    currency_conversion_verified: bool = False
    session_calendar_verified: bool = False

    @model_validator(mode="after")
    def consistent_evidence(self):
        if self.assessed_at < self.started_at:
            raise ValueError("prospective assessment precedes start")
        dates = [when.date() for when, _ in self.daily_net_pnl]
        if len(dates) != len(set(dates)) or dates != sorted(dates):
            raise ValueError("daily outcomes must be unique and chronological")
        if any(when < self.started_at or when > self.assessed_at for when, _ in self.daily_net_pnl):
            raise ValueError("daily outcome outside prospective window")
        if any(when.tzinfo is None or when.utcoffset() != timedelta(0) for when, _ in self.daily_net_pnl):
            raise ValueError("daily outcomes require UTC")
        return self


class ProspectiveAssessment(FrozenModel):
    supported: bool
    reasons: tuple[str, ...]
    lower_net_daily_pnl: Decimal
    protocol_hash: str


def _daily_block_lower(values: tuple[Decimal, ...]) -> Decimal:
    """Deterministic 2.5% lower mean from overlapping consecutive-day blocks.

    The cube-root block length is a heuristic, not a calibrated probability
    of profit under changing market regimes.
    """
    if len(values) < 2:
        return D(0)
    rng = random.Random(20261006)
    block_size = min(len(values), max(2, ceil(len(values) ** (1 / 3))))
    sampled = []
    for _ in range(2000):
        draw: list[Decimal] = []
        while len(draw) < len(values):
            start = rng.randrange(len(values) - block_size + 1)
            draw.extend(values[start : start + block_size])
        sampled.append(sum(draw[: len(values)], D(0)) / len(values))
    sampled.sort()
    return sampled[49]


def assess_prospective(protocol: IntradayRound, evidence: ProspectiveEvidence) -> ProspectiveAssessment:
    protocol = IntradayRound.model_validate(protocol.model_dump())
    evidence = ProspectiveEvidence.model_validate(evidence.model_dump())
    if evidence.protocol_hash != protocol.identity_hash:
        raise ValueError("prospective protocol mismatch")
    expected_days = (evidence.assessed_at.date() - evidence.started_at.date()).days
    complete_daily_ledger = len(evidence.daily_net_pnl) == expected_days and all(
        when.date() == (evidence.started_at + timedelta(days=index)).date()
        for index, (when, _) in enumerate(evidence.daily_net_pnl)
    )
    lower = _daily_block_lower(tuple(value for _, value in evidence.daily_net_pnl)) if complete_daily_ledger else D(0)
    reasons = []
    if evidence.assessed_at - evidence.started_at < timedelta(days=protocol.minimum_forward_days):
        reasons.append("forward_window_short")
    if evidence.closed_trades < protocol.minimum_closed_trades:
        reasons.append("trade_sample_small")
    if evidence.account_quote_coverage < protocol.minimum_account_quote_coverage:
        reasons.append("account_quote_coverage_low")
    if evidence.historical_bar_coverage < protocol.minimum_historical_coverage:
        reasons.append("historical_coverage_low")
    if evidence.material_feed_gaps:
        reasons.append("material_feed_gap")
    if evidence.maximum_drawdown > protocol.maximum_drawdown:
        reasons.append("drawdown_exceeded")
    if not evidence.frozen_rule:
        reasons.append("rule_changed")
    if not evidence.account_specific_quotes:
        reasons.append("account_quotes_unavailable")
    for flag in ("execution_costs_verified", "currency_conversion_verified", "session_calendar_verified"):
        if not getattr(evidence, flag):
            reasons.append(flag)
    if not complete_daily_ledger:
        reasons.append("daily_ledger_incomplete")
    if lower <= 0:
        reasons.append("net_lower_bound_nonpositive")
    return ProspectiveAssessment(
        supported=not reasons,
        reasons=tuple(reasons),
        lower_net_daily_pnl=lower,
        protocol_hash=protocol.identity_hash,
    )
