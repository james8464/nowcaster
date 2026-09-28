"""Immutable campaign identity and versioned paper-only progress contracts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.deep_research.candidates import CandidateSearchSpace
from src.learning.grammar import RuleNode
from src.research.round_two_contracts import (
    SUPPORTED_SYMBOLS,
    ResearchRoundProtocol,
    WalkForwardSchedule,
    _freeze_parameter,
    _utc,
)
from src.strategies.types import canonical_hash, canonical_json

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=256, strict=True)]
Count = Annotated[int, Field(ge=0, strict=True)]
State = Literal["idle", "training", "waiting", "pausing", "paused", "blocked", "completed", "failed"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    def validated(self):
        return type(self).model_validate(self.model_dump(mode="python"))


class LearningSearchSpace(FrozenModel):
    """Serializable, deeply immutable declaration for the existing search grammar."""

    symbol: Literal["BTCUSDT", "ETHUSDT"]
    strategy_id: Identifier
    base_parameters: tuple[tuple[str, Any], ...] = ()
    parameter_grid: tuple[tuple[str, tuple[Any, ...]], ...] = ()
    seed_rules: tuple[RuleNode, ...] = ()
    indicators: tuple[str, ...]
    thresholds: tuple[float, ...]
    maximum_lag: Count = 2
    max_depth: Annotated[int, Field(ge=3, strict=True)] = 4
    max_nodes: Annotated[int, Field(ge=3, strict=True)] = 15

    @field_validator("base_parameters", "parameter_grid", mode="before")
    @classmethod
    def freeze_mapping(cls, value):
        frozen = _freeze_parameter(value)
        if any(not isinstance(pair, tuple) or len(pair) != 2 for pair in frozen):
            raise ValueError("search parameters require named pairs")
        if len({pair[0] for pair in frozen}) != len(frozen):
            raise ValueError("search parameter names must be unique")
        return tuple(sorted(frozen))

    def to_search_space(self) -> CandidateSearchSpace:
        return CandidateSearchSpace(
            strategy_id=self.strategy_id,
            base_parameters=dict(self.base_parameters),
            parameter_grid=dict(self.parameter_grid),
            seed_rules=self.seed_rules,
            indicators=self.indicators,
            thresholds=self.thresholds,
            maximum_lag=self.maximum_lag,
            max_depth=self.max_depth,
            max_nodes=self.max_nodes,
        )

    @model_validator(mode="after")
    def valid_grammar(self):
        self.to_search_space()
        pending = list(self.seed_rules)
        while pending:
            node = pending.pop()
            if node.lag > self.maximum_lag:
                raise ValueError("seed rule lag exceeds the registered maximum lag")
            pending.extend(node.children)
        if any(type(value) not in (float, int, str, bool) for _, value in self.base_parameters):
            raise ValueError("base parameters must be scalar JSON values")
        canonical_json(self.model_dump(mode="json"))
        return self


class LearningCostPolicy(FrozenModel):
    """All source costs and evidence gates, copied exactly at registration."""

    maximum_spread_bps: Decimal = Field(gt=0)
    minimum_coverage: Decimal = Field(gt=0, le=1)
    maximum_observation_age_seconds: Annotated[int, Field(gt=0, strict=True)]
    warmup_minutes: Annotated[int, Field(gt=0, strict=True)]
    fee_bps: Decimal = Field(ge=0)
    slippage_bps: Decimal = Field(ge=0)
    latency_ms: Count
    minimum_closed_trades: Annotated[int, Field(gt=0, strict=True)]
    maximum_drawdown: Decimal = Field(gt=0, le=1)
    minimum_stressed_lower_edge: Decimal = Field(ge=0)
    maximum_volume_participation: Decimal = Field(gt=0, le=1)
    maximum_initial_cash_exposure: Decimal = Field(gt=0, le=1)
    maximum_feature_bars: Annotated[int, Field(gt=0, strict=True)]

    @classmethod
    def from_protocol(cls, protocol: ResearchRoundProtocol) -> LearningCostPolicy:
        return cls(**{name: getattr(protocol, name) for name in cls.model_fields})


class LearningCampaign(FrozenModel):
    campaign_id: Identifier
    source_directory: Path
    source_protocol_hash: Digest
    code_hash: Digest
    symbols: tuple[str, ...]
    search_spaces: tuple[LearningSearchSpace, ...]
    cost_policy: LearningCostPolicy
    schedule: WalkForwardSchedule
    seed: Count
    max_attempts_per_batch: Annotated[int, Field(gt=0, le=100, strict=True)] = 100
    max_batches_per_asset_day: Annotated[int, Field(gt=0, le=1, strict=True)] = 1
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def utc_created_at(cls, value):
        return _utc(value, "created_at")

    @field_validator("schedule", mode="before")
    @classmethod
    def strict_schedule(cls, value):
        fields = value.model_dump() if isinstance(value, WalkForwardSchedule) else value
        for name in ("train_days", "validation_days", "sealed_test_days", "step_days"):
            if name in fields and type(fields[name]) is not int:
                raise ValueError("schedule days must be integers")
        return WalkForwardSchedule.model_validate(fields)

    @model_validator(mode="after")
    def declared_symbols(self):
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("campaign symbols must be nonempty and unique")
        if any(symbol not in SUPPORTED_SYMBOLS for symbol in self.symbols):
            raise ValueError("unsupported campaign symbol")
        pairs = [(space.symbol, space.strategy_id) for space in self.search_spaces]
        if len(set(pairs)) != len(pairs) or {pair[0] for pair in pairs} != set(self.symbols):
            raise ValueError("search spaces must uniquely cover every campaign symbol")
        return self

    @property
    def identity_hash(self) -> str:
        return canonical_hash(self.validated().model_dump(mode="json"))


class LearningBatch(FrozenModel):
    batch_id: Identifier
    campaign_hash: Digest
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    utc_day: date
    data_fingerprint: Digest
    training_start: datetime
    training_end: datetime
    validation_end: datetime
    holdout_end: datetime
    max_attempts: Annotated[int, Field(gt=0, le=100, strict=True)]
    created_at: datetime

    @field_validator("training_start", "training_end", "validation_end", "holdout_end", "created_at")
    @classmethod
    def explicit_utc(cls, value, info):
        return _utc(value, info.field_name)

    @model_validator(mode="after")
    def chronological(self):
        if not self.training_start < self.training_end < self.validation_end < self.holdout_end <= self.created_at:
            raise ValueError("batch windows must be chronological and available at creation")
        if self.utc_day != self.created_at.date():
            raise ValueError("batch UTC day must match creation")
        return self


class LearningStatus(FrozenModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    campaign_hash: Digest
    campaign_id: str | None = None
    batch_id: str | None = None
    state: State = "idle"
    reason: str = "Waiting for eligible observations"
    attempt_count: Count = 0
    failure_count: Count = 0
    batch_attempt_count: Count = 0
    batch_failure_count: Count = 0
    last_checkpoint: str | None = None
    next_eligible_at: datetime | None = None
    paper_only: Literal[True] = True

    @field_validator("schema_version", "paper_only", mode="before")
    @classmethod
    def strict_literals(cls, value, info):
        expected_type = int if info.field_name == "schema_version" else bool
        if type(value) is not expected_type:
            raise ValueError("status version and paper_only must use their exact JSON types")
        return value

    @field_validator("next_eligible_at")
    @classmethod
    def utc_next_eligible_at(cls, value):
        return _utc(value, "next_eligible_at") if value is not None else None


class LearningEvent(FrozenModel):
    """Versioned worker envelope. Attempts are reserved before candidate evaluation."""

    schema_version: Literal[1] = 1
    kind: Literal["attempt", "attempt_result", "checkpoint", "state", "artifact", "completion"]
    attempt_id: Identifier | None = None
    candidate_hash: Digest | None = None
    outcome: Literal["reserved", "completed", "rejected", "invalid", "failed", "interrupted"] | None = None
    state: State | None = None
    reason: str | None = None
    checkpoint: str | None = None
    next_eligible_at: datetime | None = None
    phase: Literal["selection", "validation", "holdout", "proposal", "interruption"] | None = None
    payload: dict[str, Any] | None = None

    @field_validator("next_eligible_at")
    @classmethod
    def utc_next(cls, value):
        return _utc(value, "next_eligible_at") if value is not None else None

    @model_validator(mode="after")
    def required_fields(self):
        if self.kind in {"attempt", "attempt_result"} and (self.attempt_id is None or self.candidate_hash is None):
            raise ValueError("attempt events require attempt_id and candidate_hash")
        if self.kind == "attempt_result" and self.outcome in {None, "reserved"}:
            raise ValueError("attempt_result requires a terminal outcome")
        if self.kind in {"state", "completion"} and (self.state is None or not self.reason):
            raise ValueError("state events require state and reason")
        if self.kind == "completion" and self.state not in {"waiting", "completed", "failed"}:
            raise ValueError("completion requires a terminal disposition")
        if self.kind == "checkpoint" and not self.checkpoint:
            raise ValueError("checkpoint event requires a checkpoint")
        if self.kind == "artifact" and (self.phase is None or self.payload is None):
            raise ValueError("artifact event requires phase and structured payload")
        allowed = {
            "attempt": {"attempt_id", "candidate_hash", "outcome", "payload"},
            "attempt_result": {"attempt_id", "candidate_hash", "outcome", "payload"},
            "state": {"state", "reason", "next_eligible_at"},
            "completion": {"state", "reason", "next_eligible_at"},
            "checkpoint": {"checkpoint"},
            "artifact": {"phase", "payload"},
        }[self.kind] | {"schema_version", "kind"}
        if any(value is not None and name not in allowed for name, value in self.model_dump().items()):
            raise ValueError("event fields do not match its kind")
        canonical_json(self.model_dump(mode="json"))
        return self
