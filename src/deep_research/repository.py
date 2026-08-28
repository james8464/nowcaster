from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, insert, select, update

from src.database.engine import Database
from src.database.schema import TABLES
from src.deep_research.contracts import (
    CandidateAttempt,
    FoldEvidence,
    PromotionEvidence,
    ResearchProtocol,
    ResourceSample,
    ResumeState,
    RunState,
    StressEvidence,
    global_trial_identity,
)
from src.strategies.types import canonical_hash


class DeepResearchRepository:
    """Single-writer append ledger for reproducible research evidence."""

    def __init__(self, database: Database, *, clock: Callable[[], datetime] | None = None):
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is not UTC:
            raise ValueError("repository clock must return an explicit UTC datetime")
        return now

    def _common(self) -> dict[str, Any]:
        return {"source": "nowcaster_deep_research", "source_version": "1", "created_at": self._now()}

    @staticmethod
    def _run_id(run_id: str) -> str:
        value = run_id.strip()
        if not value:
            raise ValueError("run_id must not be empty")
        return value

    def create_run(self, run_id: str, protocol: ResearchProtocol) -> None:
        run_id = self._run_id(run_id)
        now = self._now()
        row = {
            "run_id": run_id,
            "protocol_id": protocol.identity,
            "dataset_hash": protocol.dataset_hash,
            "code_hash": protocol.code_hash,
            "search_space_hash": protocol.search_space_hash,
            "cost_policy_hash": protocol.cost_policy_hash,
            "symbol": protocol.symbol,
            "provider": protocol.provider,
            "feed": protocol.feed,
            "interval": protocol.interval,
            "seed": protocol.seed,
            "workers": protocol.workers,
            "trial_budget": protocol.trial_budget,
            "continuous": protocol.continuous,
            "cycle_budget": protocol.cycle_budget,
            "final_test_start": protocol.final_test_start,
            "started_at": now,
            "updated_at": now,
            "ended_at": None,
            "state": RunState.RUNNING.value,
            "terminal_reason": None,
            "protocol": protocol.as_record(),
            **self._common(),
        }
        self.database.insert("deep_research_runs", [row])

    def append_attempt(self, run_id: str, attempt: CandidateAttempt) -> None:
        self.append_attempts_ordered(run_id, [attempt])

    def append_attempts_ordered(self, run_id: str, attempts: Sequence[CandidateAttempt]) -> None:
        run_id = self._run_id(run_id)
        ordered = sorted(attempts, key=lambda attempt: attempt.ordinal)
        if len({attempt.ordinal for attempt in ordered}) != len(ordered):
            raise ValueError("attempt batch contains duplicate ordinals")
        table = TABLES["deep_research_trials"]
        with self.database.engine.begin() as connection:
            run_table = TABLES["deep_research_runs"]
            run = connection.execute(
                select(run_table.c.protocol_id, run_table.c.dataset_hash, run_table.c.protocol).where(
                    run_table.c.run_id == run_id
                )
            ).first()
            if run is None:
                raise ValueError(f"unknown deep research run: {run_id}")
            protocol_record = dict(run.protocol)
            search_family = str(protocol_record.get("search_family", "deep_strategy_search"))
            existing = {
                int(row[0]) for row in connection.execute(select(table.c.ordinal).where(table.c.run_id == run_id)).all()
            }
            collision = next((attempt.ordinal for attempt in ordered if attempt.ordinal in existing), None)
            if collision is not None:
                raise ValueError(f"trial ordinal {collision} already exists")
            sequence = int(
                connection.execute(
                    select(func.max(table.c.persisted_sequence)).where(table.c.run_id == run_id)
                ).scalar_one_or_none()
                or 0
            )
            rows = []
            for offset, attempt in enumerate(ordered, start=1):
                rows.append(
                    {
                        "trial_id": canonical_hash({"run_id": run_id, "ordinal": attempt.ordinal}),
                        "global_trial_id": global_trial_identity(
                            search_family=search_family,
                            dataset_hash=str(run.dataset_hash),
                            protocol_hash=str(run.protocol_id),
                            candidate_hash=attempt.candidate_hash,
                            attempt_ordinal=attempt.ordinal,
                        ),
                        "run_id": run_id,
                        "ordinal": attempt.ordinal,
                        "persisted_sequence": sequence + offset,
                        "generation": attempt.generation,
                        "candidate_hash": attempt.candidate_hash,
                        "definition": attempt.definition,
                        "status": attempt.status.value,
                        "attempted_at": attempt.attempted_at,
                        "completed_at": attempt.completed_at,
                        "fitness": attempt.fitness,
                        "error_summary": attempt.error_summary,
                        **self._common(),
                    }
                )
            if rows:
                connection.execute(insert(table), rows)

    def global_trial_count(self, protocol: ResearchProtocol) -> int:
        """Count distinct logical trials across restarts and protocol revisions."""
        frame = self.database.frame(
            "select t.global_trial_id, r.protocol from deep_research_trials t "
            "join deep_research_runs r on r.run_id = t.run_id where r.dataset_hash = :dataset_hash",
            {"dataset_hash": protocol.dataset_hash},
        )
        if frame.empty:
            return 0
        identities = {
            str(row.global_trial_id)
            for row in frame.itertuples(index=False)
            if isinstance(row.protocol, dict)
            and str(row.protocol.get("search_family", "deep_strategy_search")) == protocol.search_family
        }
        return len(identities)

    def global_successful_fitness(self, protocol: ResearchProtocol) -> tuple[float, ...]:
        """Return one authenticated fitness per global trial for multiplicity control."""
        frame = self.database.frame(
            "select t.global_trial_id, t.fitness, t.status, r.protocol from deep_research_trials t "
            "join deep_research_runs r on r.run_id = t.run_id where r.dataset_hash = :dataset_hash",
            {"dataset_hash": protocol.dataset_hash},
        )
        values: dict[str, float] = {}
        for row in frame.itertuples(index=False):
            if (
                not isinstance(row.protocol, dict)
                or str(row.protocol.get("search_family", "deep_strategy_search")) != protocol.search_family
                or str(row.status) != "succeeded"
                or row.fitness is None
            ):
                continue
            identity = str(row.global_trial_id)
            fitness = float(row.fitness)
            previous = values.get(identity)
            if previous is not None and previous != fitness:
                raise ValueError("duplicate global trial has conflicting fitness evidence")
            values[identity] = fitness
        return tuple(values[identity] for identity in sorted(values))

    def append_fold_evidence(self, run_id: str, evidence: FoldEvidence) -> None:
        self.database.insert(
            "deep_research_fold_metrics",
            [
                {
                    "fold_metric_id": canonical_hash(
                        {"run_id": run_id, "ordinal": evidence.ordinal, "fold": evidence.fold_index}
                    ),
                    "run_id": run_id,
                    "ordinal": evidence.ordinal,
                    "fold_index": evidence.fold_index,
                    "metrics": evidence.metrics,
                    **self._common(),
                }
            ],
        )

    def append_stress_evidence(self, run_id: str, evidence: StressEvidence) -> None:
        self.database.insert(
            "deep_research_stress_metrics",
            [
                {
                    "stress_metric_id": canonical_hash(
                        {"run_id": run_id, "ordinal": evidence.ordinal, "scenario": evidence.scenario}
                    ),
                    "run_id": run_id,
                    "ordinal": evidence.ordinal,
                    "scenario": evidence.scenario,
                    "metrics": evidence.metrics,
                    "passed": evidence.passed,
                    **self._common(),
                }
            ],
        )

    def append_promotion(self, run_id: str, evidence: PromotionEvidence) -> str:
        now = max(self._now(), evidence.transition.transitioned_at)
        promotion_id = canonical_hash({"run_id": run_id, "evaluated_at": now})
        self.database.insert(
            "deep_research_promotions",
            [
                {
                    "promotion_id": promotion_id,
                    "run_id": run_id,
                    "candidate_hash": evidence.candidate_hash,
                    "challenger_hash": evidence.transition.challenger_hash,
                    "incumbent_hash": evidence.incumbent_hash,
                    "deployment_state": evidence.transition.deployment_state.value,
                    "shadow_cohort_hash": evidence.transition.shadow_cohort_hash,
                    "rollback_target_hash": evidence.transition.rollback_target_hash,
                    "forward_evidence_reset": evidence.transition.forward_evidence_reset,
                    "evaluated_at": now,
                    "promoted": evidence.promoted,
                    "outcome": evidence.outcome,
                    "score": evidence.score,
                    "evidence": {**evidence.evidence, "transition": evidence.transition.as_record()},
                    "failed_gates": list(evidence.failed_gates),
                    **self._common(),
                }
            ],
        )
        return promotion_id

    def checkpoint(
        self,
        run_id: str,
        *,
        next_ordinal: int,
        generation: int,
        payload: dict[str, Any],
    ) -> str:
        run_id = self._run_id(run_id)
        if next_ordinal < 1 or generation < 1:
            raise ValueError("checkpoint ordinals and generations must be positive")
        run_table = TABLES["deep_research_runs"]
        with self.database.engine.connect() as connection:
            protocol_id = connection.execute(
                select(run_table.c.protocol_id).where(run_table.c.run_id == run_id)
            ).scalar_one_or_none()
        if protocol_id is None:
            raise ValueError(f"unknown deep research run: {run_id}")
        now = self._now()
        checkpoint_id = canonical_hash(
            {"run_id": run_id, "protocol_id": protocol_id, "next_ordinal": next_ordinal, "generation": generation}
        )
        if self.database.scalar(
            "select count(*) from deep_research_checkpoints where checkpoint_id = :checkpoint_id",
            {"checkpoint_id": checkpoint_id},
        ):
            return checkpoint_id
        self.database.insert(
            "deep_research_checkpoints",
            [
                {
                    "checkpoint_id": checkpoint_id,
                    "run_id": run_id,
                    "protocol_id": protocol_id,
                    "next_ordinal": next_ordinal,
                    "generation": generation,
                    "payload": payload,
                    "checkpointed_at": now,
                    **self._common(),
                }
            ],
        )
        return checkpoint_id

    def set_state(self, run_id: str, state: RunState, *, reason: str | None = None) -> None:
        run_id = self._run_id(run_id)
        state = RunState(state)
        table = TABLES["deep_research_runs"]
        now = self._now()
        terminal = state in {RunState.COMPLETED, RunState.STOPPED, RunState.FAILED}
        with self.database.engine.begin() as connection:
            current = connection.execute(select(table.c.state).where(table.c.run_id == run_id)).scalar_one_or_none()
            if current is None:
                raise ValueError(f"unknown deep research run: {run_id}")
            if RunState(current) in {RunState.COMPLETED, RunState.STOPPED, RunState.FAILED}:
                raise ValueError("terminal deep research runs cannot transition")
            connection.execute(
                update(table)
                .where(table.c.run_id == run_id)
                .values(
                    state=state.value,
                    updated_at=now,
                    ended_at=now if terminal else None,
                    terminal_reason=reason,
                )
            )

    def load_resume_state(self, run_id: str, protocol: ResearchProtocol) -> ResumeState:
        run_id = self._run_id(run_id)
        runs = TABLES["deep_research_runs"]
        checkpoints = TABLES["deep_research_checkpoints"]
        with self.database.engine.connect() as connection:
            stored_protocol = connection.execute(
                select(runs.c.protocol_id).where(runs.c.run_id == run_id)
            ).scalar_one_or_none()
            if stored_protocol is None:
                raise ValueError(f"unknown deep research run: {run_id}")
            if stored_protocol != protocol.identity:
                raise ValueError("resume protocol identity does not match the original run")
            row = connection.execute(
                select(
                    checkpoints.c.checkpoint_id,
                    checkpoints.c.next_ordinal,
                    checkpoints.c.generation,
                    checkpoints.c.payload,
                )
                .where(checkpoints.c.run_id == run_id)
                .order_by(checkpoints.c.checkpointed_at.desc(), checkpoints.c.next_ordinal.desc())
            ).first()
        if row is None:
            raise ValueError("deep research run has no checkpoint")
        return ResumeState(
            checkpoint_id=str(row.checkpoint_id),
            next_ordinal=int(row.next_ordinal),
            generation=int(row.generation),
            payload=dict(row.payload),
        )

    def resume_run(self, run_id: str, protocol: ResearchProtocol) -> ResumeState:
        state = self.load_resume_state(run_id, protocol)
        runs = TABLES["deep_research_runs"]
        now = self._now()
        with self.database.engine.begin() as connection:
            current = connection.execute(select(runs.c.state).where(runs.c.run_id == run_id)).scalar_one()
            if RunState(current) in {RunState.COMPLETED, RunState.STOPPED, RunState.FAILED}:
                raise ValueError("terminal deep research runs cannot be resumed")
            connection.execute(
                update(runs)
                .where(runs.c.run_id == run_id)
                .values(state=RunState.RUNNING.value, updated_at=now, terminal_reason=None)
            )
        return state

    def append_resource_sample(self, run_id: str, sample: ResourceSample) -> str:
        identity = canonical_hash(
            {
                "run_id": run_id,
                "sampled_at": sample.sampled_at,
                "active_workers": sample.active_workers,
                "queued_trials": sample.queued_trials,
                "memory_bytes": sample.memory_bytes,
                "thermal_state": sample.thermal_state,
            }
        )
        self.database.insert(
            "deep_research_resource_samples",
            [
                {
                    "resource_sample_id": identity,
                    "run_id": run_id,
                    "sampled_at": sample.sampled_at,
                    "active_workers": sample.active_workers,
                    "queued_trials": sample.queued_trials,
                    "memory_bytes": sample.memory_bytes,
                    "thermal_state": sample.thermal_state,
                    **self._common(),
                }
            ],
        )
        return identity


__all__ = ["DeepResearchRepository"]
