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


def contextual_trial_identity(
    *,
    dataset_hash: str,
    protocol_hash: str,
    candidate_hash: str,
    attempt_ordinal: int = 0,
) -> str:
    """Identify one bounded contextual-policy evaluation across sessions and restarts."""

    return global_trial_identity(
        search_family="contextual_policy_search",
        dataset_hash=dataset_hash,
        protocol_hash=protocol_hash,
        candidate_hash=candidate_hash,
        attempt_ordinal=attempt_ordinal,
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


class DeploymentState(StrEnum):
    REJECTED = "rejected"
    SHADOW = "shadow"
    FORWARD_QUALIFIED = "forward_qualified"
    ROLLED_BACK = "rolled_back"


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
    reserved_processors: int = 2

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
        if (
            isinstance(self.reserved_processors, bool)
            or not isinstance(self.reserved_processors, int)
            or self.reserved_processors < 1
        ):
            raise ValueError("reserved processors must be a positive integer")
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
                "reserved_processors": self.reserved_processors,
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
            "reserved_processors": self.reserved_processors,
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
class ChampionChallengerTransition:
    challenger_hash: str
    incumbent_hash: str | None
    protocol_hash: str
    deployment_state: DeploymentState
    shadow_cohort_hash: str | None
    rollback_target_hash: str | None
    forward_evidence_reset: bool
    transitioned_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "challenger_hash", _hash(self.challenger_hash, "challenger_hash"))
        object.__setattr__(self, "protocol_hash", _hash(self.protocol_hash, "protocol_hash"))
        object.__setattr__(self, "deployment_state", DeploymentState(self.deployment_state))
        if self.incumbent_hash is not None:
            object.__setattr__(self, "incumbent_hash", _hash(self.incumbent_hash, "incumbent_hash"))
        if self.shadow_cohort_hash is not None:
            object.__setattr__(self, "shadow_cohort_hash", _hash(self.shadow_cohort_hash, "shadow_cohort_hash"))
        if self.rollback_target_hash is not None:
            object.__setattr__(
                self,
                "rollback_target_hash",
                _hash(self.rollback_target_hash, "rollback_target_hash"),
            )
        _utc(self.transitioned_at, "transitioned_at")
        if type(self.forward_evidence_reset) is not bool:
            raise ValueError("forward evidence reset must be a boolean")
        if self.deployment_state is DeploymentState.SHADOW:
            if self.shadow_cohort_hash is None or not self.forward_evidence_reset:
                raise ValueError("shadow transitions require a new cohort and forward evidence reset")
            if self.rollback_target_hash != self.incumbent_hash:
                raise ValueError("shadow rollback target must be the incumbent")
        elif self.deployment_state is DeploymentState.FORWARD_QUALIFIED:
            if self.shadow_cohort_hash is None or self.forward_evidence_reset:
                raise ValueError("forward qualification must retain completed shadow evidence")
            if self.rollback_target_hash != self.incumbent_hash:
                raise ValueError("forward qualification rollback target must be the incumbent")
        elif self.deployment_state is DeploymentState.REJECTED:
            if self.shadow_cohort_hash is not None or self.forward_evidence_reset:
                raise ValueError("rejected challengers cannot create a forward cohort")
        elif self.rollback_target_hash is None:
            raise ValueError("rollback transitions require a rollback target")

    @property
    def transition_id(self) -> str:
        return canonical_hash(self.as_record())

    def as_record(self) -> dict[str, Any]:
        return {
            "challenger_hash": self.challenger_hash,
            "incumbent_hash": self.incumbent_hash,
            "protocol_hash": self.protocol_hash,
            "deployment_state": self.deployment_state.value,
            "shadow_cohort_hash": self.shadow_cohort_hash,
            "rollback_target_hash": self.rollback_target_hash,
            "forward_evidence_reset": self.forward_evidence_reset,
            "transitioned_at": self.transitioned_at.isoformat().replace("+00:00", "Z"),
        }

    @classmethod
    def start_shadow(
        cls,
        *,
        challenger_hash: str,
        incumbent_hash: str | None,
        protocol_hash: str,
        transitioned_at: datetime,
    ) -> ChampionChallengerTransition:
        shadow_cohort_hash = canonical_hash(
            {
                "challenger_hash": challenger_hash,
                "incumbent_hash": incumbent_hash,
                "protocol_hash": protocol_hash,
                "deployment_state": DeploymentState.SHADOW.value,
            }
        )
        return cls(
            challenger_hash=challenger_hash,
            incumbent_hash=incumbent_hash,
            protocol_hash=protocol_hash,
            deployment_state=DeploymentState.SHADOW,
            shadow_cohort_hash=shadow_cohort_hash,
            rollback_target_hash=incumbent_hash,
            forward_evidence_reset=True,
            transitioned_at=transitioned_at,
        )

    @classmethod
    def reject(
        cls,
        *,
        challenger_hash: str,
        incumbent_hash: str | None,
        protocol_hash: str,
        transitioned_at: datetime,
    ) -> ChampionChallengerTransition:
        return cls(
            challenger_hash=challenger_hash,
            incumbent_hash=incumbent_hash,
            protocol_hash=protocol_hash,
            deployment_state=DeploymentState.REJECTED,
            shadow_cohort_hash=None,
            rollback_target_hash=incumbent_hash,
            forward_evidence_reset=False,
            transitioned_at=transitioned_at,
        )

    def roll_back(self, *, transitioned_at: datetime) -> ChampionChallengerTransition:
        if self.incumbent_hash is None:
            raise ValueError("a challenger without an incumbent has no strategy rollback target")
        return type(self)(
            challenger_hash=self.challenger_hash,
            incumbent_hash=self.incumbent_hash,
            protocol_hash=self.protocol_hash,
            deployment_state=DeploymentState.ROLLED_BACK,
            shadow_cohort_hash=self.shadow_cohort_hash,
            rollback_target_hash=self.incumbent_hash,
            forward_evidence_reset=False,
            transitioned_at=transitioned_at,
        )


@dataclass(frozen=True, slots=True)
class PromotionEvidence:
    candidate_hash: str | None
    incumbent_hash: str | None
    promoted: bool
    outcome: str
    score: float | None
    evidence: dict[str, Any]
    failed_gates: tuple[str, ...]
    transition: ChampionChallengerTransition

    def __post_init__(self) -> None:
        if self.candidate_hash != self.transition.challenger_hash:
            raise ValueError("promotion candidate and challenger identities must match")
        if self.incumbent_hash != self.transition.incumbent_hash:
            raise ValueError("promotion incumbent and rollback transition must match")
        if self.promoted != (self.transition.deployment_state is DeploymentState.SHADOW):
            raise ValueError("offline promotion can only start a shadow cohort")


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
