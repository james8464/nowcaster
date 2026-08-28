from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from src.strategies.types import canonical_hash


def _utc(value: datetime | None, name: str, *, required: bool = True) -> datetime | None:
    if value is None and not required:
        return None
    if not isinstance(value, datetime) or value.tzinfo is not UTC:
        raise ValueError(f"{name} must be an explicit UTC datetime")
    return value


def _identifier(value: str, name: str, *, lowercase: bool = False, uppercase: bool = False) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    if lowercase:
        normalized = normalized.lower()
    if uppercase:
        normalized = normalized.upper()
    return normalized


def _hash(value: str, name: str) -> str:
    normalized = _identifier(value, name).lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError(f"{name} must be a 64-character lowercase hexadecimal digest")
    return normalized


def global_trial_identity(
    *,
    search_family: str,
    dataset_hash: str,
    protocol_hash: str,
    candidate_hash: str,
    attempt_ordinal: int,
) -> str:
    """Identify one semantic evaluation globally, independent of a run/session id."""
    if isinstance(attempt_ordinal, bool) or not isinstance(attempt_ordinal, int) or attempt_ordinal < 0:
        raise ValueError("attempt ordinal must be a non-negative integer")
    return canonical_hash(
        {
            "search_family": _identifier(search_family, "search_family", lowercase=True),
            "dataset_hash": _hash(dataset_hash, "dataset_hash"),
            "protocol_hash": _hash(protocol_hash, "protocol_hash"),
            "candidate_hash": _hash(candidate_hash, "candidate_hash"),
            "attempt_ordinal": attempt_ordinal,
        }
    )


class RunState(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


class AttemptStatus(StrEnum):
    GENERATED = "generated"
    DUPLICATE = "duplicate"
    INVALID = "invalid"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class ResearchProtocol:
    dataset_hash: str
    code_hash: str
    search_space_hash: str
    cost_policy_hash: str
    symbol: str
    provider: str
    feed: str
    interval: str
    seed: int
    workers: int
    trial_budget: int | None
    continuous: bool
    final_test_start: datetime
    created_at: datetime
    protocol_version: str = "deep-research-v1"
    cycle_budget: int = 100
    search_family: str = "deep_strategy_search"

    def __post_init__(self) -> None:
        for name in ("dataset_hash", "code_hash", "search_space_hash", "cost_policy_hash"):
            object.__setattr__(self, name, _hash(getattr(self, name), name))
        object.__setattr__(self, "symbol", _identifier(self.symbol, "symbol", uppercase=True))
        object.__setattr__(self, "provider", _identifier(self.provider, "provider", lowercase=True))
        object.__setattr__(self, "feed", _identifier(self.feed, "feed", lowercase=True))
        object.__setattr__(self, "interval", _identifier(self.interval, "interval", lowercase=True))
        object.__setattr__(self, "protocol_version", _identifier(self.protocol_version, "protocol_version"))
        object.__setattr__(self, "search_family", _identifier(self.search_family, "search_family", lowercase=True))
        _utc(self.final_test_start, "final_test_start")
        _utc(self.created_at, "created_at")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")
        if isinstance(self.workers, bool) or not isinstance(self.workers, int) or self.workers < 1:
            raise ValueError("workers must be a positive integer")
        if isinstance(self.cycle_budget, bool) or self.cycle_budget < 1:
            raise ValueError("cycle_budget must be a positive integer")
        if self.continuous:
            if self.trial_budget is not None:
                raise ValueError("continuous research cannot set a global trial budget")
        elif isinstance(self.trial_budget, bool) or not isinstance(self.trial_budget, int) or self.trial_budget < 1:
            raise ValueError("bounded research requires a positive trial budget")

    @property
    def identity(self) -> str:
        return canonical_hash(
            {
                "code_hash": self.code_hash,
                "cost_policy_hash": self.cost_policy_hash,
                "cycle_budget": self.cycle_budget,
                "dataset_hash": self.dataset_hash,
                "feed": self.feed,
                "final_test_start": self.final_test_start,
                "interval": self.interval,
                "protocol_version": self.protocol_version,
                "provider": self.provider,
                "search_space_hash": self.search_space_hash,
                "search_family": self.search_family,
                "seed": self.seed,
                "symbol": self.symbol,
            }
        )

    def as_record(self) -> dict[str, Any]:
        return {
            "dataset_hash": self.dataset_hash,
            "code_hash": self.code_hash,
            "search_space_hash": self.search_space_hash,
            "cost_policy_hash": self.cost_policy_hash,
            "symbol": self.symbol,
            "provider": self.provider,
            "feed": self.feed,
            "interval": self.interval,
            "seed": self.seed,
            "workers": self.workers,
            "trial_budget": self.trial_budget,
            "continuous": self.continuous,
            "cycle_budget": self.cycle_budget,
            "final_test_start": self.final_test_start.isoformat().replace("+00:00", "Z"),
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            "protocol_version": self.protocol_version,
            "search_family": self.search_family,
        }


@dataclass(frozen=True, slots=True)
class CandidateAttempt:
    ordinal: int
    candidate_hash: str
    definition: dict[str, Any]
    status: AttemptStatus
    attempted_at: datetime
    completed_at: datetime | None = None
    fitness: float | None = None
    error_summary: str | None = None
    generation: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.ordinal, bool) or self.ordinal < 1:
            raise ValueError("ordinal must be a positive integer")
        if isinstance(self.generation, bool) or self.generation < 1:
            raise ValueError("generation must be a positive integer")
        object.__setattr__(self, "candidate_hash", _hash(self.candidate_hash, "candidate_hash"))
        object.__setattr__(self, "status", AttemptStatus(self.status))
        _utc(self.attempted_at, "attempted_at")
        _utc(self.completed_at, "completed_at", required=False)
        terminal = self.status is not AttemptStatus.GENERATED
        if terminal and self.completed_at is None:
            raise ValueError("terminal attempts require completed_at")
        if self.completed_at is not None and self.completed_at < self.attempted_at:
            raise ValueError("completed_at cannot precede attempted_at")
        if self.fitness is not None and (isinstance(self.fitness, bool) or not math.isfinite(self.fitness)):
            raise ValueError("fitness must be a finite number")
        if not isinstance(self.definition, dict):
            raise ValueError("definition must be a JSON object")


@dataclass(frozen=True, slots=True)
class ResumeState:
    checkpoint_id: str
    next_ordinal: int
    generation: int
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FoldEvidence:
    ordinal: int
    fold_index: int
    metrics: dict[str, float]


@dataclass(frozen=True, slots=True)
class StressEvidence:
    ordinal: int
    scenario: str
    metrics: dict[str, float]
    passed: bool


@dataclass(frozen=True, slots=True)
class PromotionEvidence:
    candidate_hash: str | None
    incumbent_hash: str | None
    promoted: bool
    outcome: str
    score: float | None
    evidence: dict[str, Any]
    failed_gates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResourceSample:
    sampled_at: datetime
    active_workers: int
    queued_trials: int
    memory_bytes: int | None
    thermal_state: str

    def __post_init__(self) -> None:
        _utc(self.sampled_at, "sampled_at")
        if self.active_workers < 0 or self.queued_trials < 0:
            raise ValueError("resource counts cannot be negative")
        if self.memory_bytes is not None and self.memory_bytes < 0:
            raise ValueError("memory_bytes cannot be negative")
