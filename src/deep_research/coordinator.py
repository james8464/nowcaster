from __future__ import annotations

import math
import os
import pickle
import shutil
import statistics
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import kurtosis, skew

from src.backtest.robustness import effective_sample_size, lower_mean_confidence_bound
from src.deep_research.contracts import (
    AttemptStatus,
    CandidateAttempt,
    ChampionChallengerTransition,
    FoldEvidence,
    PromotionEvidence,
    ResearchProtocol,
    ResourceSample,
    RunState,
    StressEvidence,
)
from src.deep_research.control import ControlState, ResearchControl
from src.deep_research.promotion import ReliabilityEvidence, evaluate_research_promotion
from src.deep_research.repository import DeepResearchRepository
from src.deep_research.statistics import (
    bootstrap_positive_edge_probability,
    deflated_sharpe_probability,
    parameter_stability,
    probability_of_backtest_overfitting,
)
from src.deep_research.stress import evaluate_stress_matrix
from src.deep_research.worker import WorkerResult, configure_worker_environment, evaluate_candidate_work

EventSink = Callable[[dict[str, Any]], None]
SealedEvaluator = Callable[["CandidateWork"], tuple[float, ...]]
ResourceGuard = Callable[[Sequence["CandidateWork"], int, str], tuple[bool, str, int | None]]


def recommended_worker_count(
    active_processors: int,
    *,
    reserved_processors: int = 2,
    configured_max: int | None = None,
) -> int:
    """Reserve host capacity for live monitoring, the UI, and the operating system."""
    for name, value in {"active_processors": active_processors, "reserved_processors": reserved_processors}.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if configured_max is not None and (
        isinstance(configured_max, bool) or not isinstance(configured_max, int) or configured_max < 1
    ):
        raise ValueError("configured worker maximum must be a positive integer")
    safe = max(active_processors - reserved_processors, 1)
    return min(safe, configured_max) if configured_max is not None else safe


def resource_preemption_reason(
    *,
    thermal_state: str = "nominal",
    memory_pressure: bool = False,
    low_disk_space: bool = False,
    sleeping: bool = False,
    live_heartbeat_healthy: bool = True,
) -> str | None:
    """Return the highest-priority reason to checkpoint before dispatching more research."""
    if not live_heartbeat_healthy:
        return "live_heartbeat_unhealthy"
    normalized_thermal = thermal_state.strip().lower()
    if normalized_thermal in {"serious", "critical", "unknown"}:
        return f"thermal_{normalized_thermal}"
    if memory_pressure:
        return "memory_pressure"
    if low_disk_space:
        return "low_disk_space"
    if sleeping:
        return "system_sleep"
    return None


def evaluate_resource_capacity(
    works: Sequence[CandidateWork],
    workers: int,
    control_directory: str,
) -> tuple[bool, str, int | None]:
    if not works:
        return True, "no work", 0
    try:
        bytes_per_worker = len(pickle.dumps(works[0], protocol=pickle.HIGHEST_PROTOCOL))
    except (MemoryError, pickle.PickleError, TypeError, ValueError):
        return False, "candidate payload memory estimate failed", None
    estimated = bytes_per_worker * min(max(workers, 1), len(works))
    try:
        physical = int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
    except (OSError, TypeError, ValueError):
        physical = 0
    if physical and estimated > int(physical * 0.70):
        return False, "estimated worker memory exceeds 70% of physical memory", estimated
    try:
        free_disk = shutil.disk_usage(control_directory).free
    except OSError:
        return False, "resource guard could not inspect free disk space", estimated
    required_disk = max(64 * 1_024 * 1_024, estimated * 2)
    if free_disk < required_disk:
        return False, "free disk space is below the checkpoint safety reserve", estimated
    return True, "resource capacity available", estimated


@dataclass(frozen=True, slots=True)
class CandidateWork:
    ordinal: int
    candidate_hash: str
    definition: dict[str, Any]
    fold_returns: tuple[tuple[float, ...], ...]
    gross_returns: tuple[float, ...]
    costs: tuple[float, ...]
    trade_count: int | None = None
    evaluation_payload: Any | None = None
    failures_before_success: int = 0
    duplicate_of: int | None = None
    delay_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.ordinal < 1 or len(self.candidate_hash) != 64:
            raise ValueError("candidate work identity is invalid")
        if self.failures_before_success < 0 or self.delay_seconds < 0:
            raise ValueError("candidate work retry and delay values cannot be negative")
        precomputed = (
            bool(self.gross_returns) and len(self.gross_returns) == len(self.costs) and bool(self.fold_returns)
        )
        if not precomputed and self.evaluation_payload is None and self.duplicate_of is None:
            raise ValueError("candidate work requires precomputed returns or a typed evaluation payload")
        if self.trade_count is not None and self.trade_count < 0:
            raise ValueError("candidate work trade_count cannot be negative")


@dataclass(frozen=True, slots=True)
class DeepResearchOutcome:
    run_id: str
    state: RunState
    evaluated_attempts: int
    fitness_by_ordinal: tuple[tuple[int, float | None], ...]
    best_candidate_hash: str | None
    promotion_outcome: str
    worker_thread_limits: tuple[str, ...]


class DeepResearchCoordinator:
    def __init__(
        self,
        *,
        run_id: str,
        protocol: ResearchProtocol,
        repository: DeepResearchRepository,
        control: ResearchControl,
        sealed_evaluator: SealedEvaluator,
        emit: EventSink | None = None,
        clock: Callable[[], datetime] | None = None,
        resource_guard: ResourceGuard | None = None,
    ):
        self.run_id = run_id
        self.protocol = protocol
        self.repository = repository
        self.control = control
        self.sealed_evaluator = sealed_evaluator
        self.emit = emit
        self.clock = clock or (lambda: datetime.now(UTC))
        self.resource_guard = resource_guard or evaluate_resource_capacity
        self.worker_count = recommended_worker_count(
            os.cpu_count() or 1,
            reserved_processors=protocol.reserved_processors,
            configured_max=protocol.workers,
        )

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is not UTC:
            raise ValueError("coordinator clock must return an explicit UTC datetime")
        return value

    def _event(self, stage: str, progress: float, message: str, **details: Any) -> None:
        if self.emit is not None:
            self.emit(
                {
                    "event": "progress",
                    "stage": stage,
                    "progress": max(0.0, min(1.0, progress)),
                    "message": message,
                    **details,
                }
            )

    def _stopped_outcome(
        self,
        *,
        next_ordinal: int,
        generation: int,
        evaluated_attempts: int,
        worker_limits: set[str] | None = None,
    ) -> DeepResearchOutcome:
        self.repository.checkpoint(
            self.run_id,
            next_ordinal=next_ordinal,
            generation=generation,
            payload={"reason": "operator_stop"},
        )
        self.repository.set_state(self.run_id, RunState.STOPPED, reason="operator_stop")
        return DeepResearchOutcome(
            self.run_id,
            RunState.STOPPED,
            evaluated_attempts,
            (),
            None,
            "stopped",
            tuple(sorted(worker_limits or set())),
        )

    def run(
        self,
        works: Sequence[CandidateWork],
        *,
        generation: int = 1,
        create_run: bool = True,
        finish_run: bool | None = None,
    ) -> DeepResearchOutcome:
        ordered_work = tuple(sorted(works, key=lambda item: item.ordinal))
        if generation < 1:
            raise ValueError("generation must be positive")
        if len(ordered_work) != len({item.ordinal for item in ordered_work}):
            raise ValueError("candidate work ordinals must be unique")
        if create_run and not self.protocol.continuous and len(ordered_work) != self.protocol.trial_budget:
            raise ValueError("candidate work count must match the pre-registered trial budget")
        if create_run:
            self.repository.create_run(self.run_id, self.protocol)
        finish_run = not self.protocol.continuous if finish_run is None else finish_run
        state = self.control.wait_until_runnable()
        if state is ControlState.STOPPED:
            return self._stopped_outcome(
                next_ordinal=ordered_work[0].ordinal if ordered_work else 1,
                generation=generation,
                evaluated_attempts=0,
            )

        started = self._now()
        attempts: dict[int, CandidateAttempt] = {}
        results: dict[int, WorkerResult] = {}
        work_by_ordinal = {item.ordinal: item for item in ordered_work}
        worker_limits: set[str] = set()
        completed_count = 0
        capacity_ok, capacity_reason, memory_estimate = self.resource_guard(
            ordered_work,
            self.worker_count,
            str(self.control.directory),
        )
        self.repository.append_resource_sample(
            self.run_id,
            ResourceSample(
                sampled_at=self._now(),
                active_workers=min(self.worker_count, len(ordered_work)),
                queued_trials=len(ordered_work),
                memory_bytes=memory_estimate,
                thermal_state="host_managed",
            ),
        )
        if not capacity_ok:
            first_ordinal = ordered_work[0].ordinal if ordered_work else 1
            self.repository.checkpoint(
                self.run_id,
                next_ordinal=first_ordinal,
                generation=generation,
                payload={"reason": "resource_guard", "detail": capacity_reason},
            )
            self.repository.set_state(self.run_id, RunState.PAUSED, reason=capacity_reason)
            self._event("resource_guard", 0.0, capacity_reason)
            return DeepResearchOutcome(
                self.run_id,
                RunState.PAUSED,
                0,
                (),
                None,
                "resource_preempted",
                (),
            )
        with ProcessPoolExecutor(
            max_workers=self.worker_count,
            initializer=configure_worker_environment,
        ) as executor:
            for offset in range(0, len(ordered_work), self.worker_count):
                batch = ordered_work[offset : offset + self.worker_count]
                capacity_ok, capacity_reason, memory_estimate = self.resource_guard(
                    batch,
                    self.worker_count,
                    str(self.control.directory),
                )
                if not capacity_ok:
                    self.repository.append_resource_sample(
                        self.run_id,
                        ResourceSample(
                            sampled_at=self._now(),
                            active_workers=0,
                            queued_trials=len(ordered_work) - completed_count,
                            memory_bytes=memory_estimate,
                            thermal_state="preempted",
                        ),
                    )
                    self.repository.checkpoint(
                        self.run_id,
                        next_ordinal=batch[0].ordinal,
                        generation=generation,
                        payload={"reason": "resource_preemption", "detail": capacity_reason},
                    )
                    self.repository.set_state(self.run_id, RunState.PAUSED, reason=capacity_reason)
                    self._event(
                        "resource_preemption",
                        completed_count / max(1, len(ordered_work)),
                        capacity_reason,
                    )
                    return DeepResearchOutcome(
                        self.run_id,
                        RunState.PAUSED,
                        completed_count,
                        tuple((ordinal, attempts[ordinal].fitness) for ordinal in sorted(attempts)),
                        None,
                        "resource_preempted",
                        tuple(sorted(worker_limits)),
                    )
                state = self.control.read()
                if state is ControlState.PAUSED:
                    self.repository.set_state(self.run_id, RunState.PAUSED, reason="operator_pause")
                    self._event("paused", completed_count / max(1, len(ordered_work)), "dispatch paused")
                    state = self.control.wait_until_runnable()
                    if state is ControlState.RUNNING:
                        self.repository.set_state(self.run_id, RunState.RUNNING, reason="operator_resume")
                if state is ControlState.STOPPED:
                    next_ordinal = batch[0].ordinal if batch else (ordered_work[-1].ordinal + 1)
                    return self._stopped_outcome(
                        next_ordinal=next_ordinal,
                        generation=generation,
                        evaluated_attempts=completed_count,
                        worker_limits=worker_limits,
                    )

                batch_attempts: dict[int, CandidateAttempt] = {}
                futures: dict[Future[WorkerResult], tuple[CandidateWork, int]] = {}
                for item in batch:
                    if item.duplicate_of is not None:
                        batch_attempts[item.ordinal] = CandidateAttempt(
                            ordinal=item.ordinal,
                            candidate_hash=item.candidate_hash,
                            definition={**item.definition, "duplicate_of": item.duplicate_of},
                            status=AttemptStatus.DUPLICATE,
                            attempted_at=started,
                            completed_at=self._now(),
                            generation=generation,
                        )
                    else:
                        futures[executor.submit(evaluate_candidate_work, item, 1)] = (item, 1)

                while futures:
                    future = next(as_completed(tuple(futures)))
                    item, attempt_number = futures.pop(future)
                    try:
                        result = future.result()
                    except Exception as error:
                        if attempt_number == 1:
                            retry = executor.submit(evaluate_candidate_work, item, 2)
                            futures[retry] = (item, 2)
                            continue
                        batch_attempts[item.ordinal] = CandidateAttempt(
                            ordinal=item.ordinal,
                            candidate_hash=item.candidate_hash,
                            definition=item.definition,
                            status=AttemptStatus.FAILED,
                            attempted_at=started,
                            completed_at=self._now(),
                            error_summary=str(error)[:500],
                            generation=generation,
                        )
                    else:
                        results[item.ordinal] = result
                        worker_limits.add(result.thread_limit)
                        batch_attempts[item.ordinal] = CandidateAttempt(
                            ordinal=item.ordinal,
                            candidate_hash=item.candidate_hash,
                            definition=item.definition,
                            status=AttemptStatus.SUCCEEDED,
                            attempted_at=started,
                            completed_at=self._now(),
                            fitness=result.fitness,
                            generation=generation,
                        )

                self.repository.append_attempts_ordered(self.run_id, [batch_attempts[item.ordinal] for item in batch])
                attempts.update(batch_attempts)
                for item in batch:
                    result = results.get(item.ordinal)
                    if result is None:
                        continue
                    for fold_index, fold in enumerate(result.folds):
                        self.repository.append_fold_evidence(
                            self.run_id,
                            FoldEvidence(
                                item.ordinal,
                                fold_index,
                                {
                                    "net_return": fold.net_return,
                                    "net_sharpe": fold.net_sharpe,
                                    "maximum_drawdown": fold.maximum_drawdown,
                                    "observations": float(fold.observations),
                                },
                            ),
                        )
                completed_count += len(batch)
                self.repository.checkpoint(
                    self.run_id,
                    next_ordinal=batch[-1].ordinal + 1,
                    generation=generation,
                    payload={"completed_attempts": completed_count},
                )
                self._event(
                    "search",
                    completed_count / max(1, len(ordered_work)),
                    f"evaluated {completed_count} of {len(ordered_work)} trials",
                    completed_trials=completed_count,
                    total_trials=len(ordered_work),
                    workers=self.worker_count,
                    generation=generation,
                )

        self.repository.append_resource_sample(
            self.run_id,
            ResourceSample(
                sampled_at=self._now(),
                active_workers=0,
                queued_trials=0,
                memory_bytes=None,
                thermal_state="host_managed",
            ),
        )

        best_hash: str | None = None
        promotion_outcome = "no_reliable_strategy_found"
        if results:
            best_ordinal, best_result = max(results.items(), key=lambda item: (item[1].fitness, -item[0]))
            best_work = work_by_ordinal[best_ordinal]
            best_hash = best_work.candidate_hash
            stress = evaluate_stress_matrix(
                list(best_result.gross_returns),
                list(best_result.costs),
                seed=self.protocol.seed,
                liquidity_observed=bool(best_work.definition.get("liquidity_observed", False)),
            )
            for scenario in stress.scenarios:
                self.repository.append_stress_evidence(
                    self.run_id,
                    StressEvidence(
                        best_ordinal,
                        scenario.name,
                        {
                            "cumulative_return": scenario.cumulative_return,
                            "maximum_drawdown": scenario.maximum_drawdown,
                            "sharpe": scenario.sharpe,
                        },
                        scenario.passed,
                    ),
                )
            sealed_returns = self.sealed_evaluator(best_work)
            if not sealed_returns or not all(math.isfinite(value) for value in sealed_returns):
                raise ValueError("sealed evaluator must return finite unseen returns")
            incumbent_hash, incumbent_score = self._incumbent()
            promotion = self._promotion(best_work, best_result, stress, sealed_returns, incumbent_score)
            promotion_outcome = (
                "existing_champion_retained"
                if incumbent_hash is not None and not promotion.promoted
                else promotion.outcome
            )
            transitioned_at = self._now()
            transition = (
                ChampionChallengerTransition.start_shadow(
                    challenger_hash=best_hash,
                    incumbent_hash=incumbent_hash,
                    protocol_hash=self.protocol.identity,
                    transitioned_at=transitioned_at,
                )
                if promotion.promoted
                else ChampionChallengerTransition.reject(
                    challenger_hash=best_hash,
                    incumbent_hash=incumbent_hash,
                    protocol_hash=self.protocol.identity,
                    transitioned_at=transitioned_at,
                )
            )
            if promotion.promoted:
                promotion_outcome = "shadow_cohort_started"
            self.repository.append_promotion(
                self.run_id,
                PromotionEvidence(
                    candidate_hash=best_hash,
                    incumbent_hash=incumbent_hash,
                    promoted=promotion.promoted,
                    outcome=promotion_outcome,
                    score=promotion.score,
                    evidence={
                        "stress_evidence_grade": stress.evidence_grade,
                        "governance": transition.as_record(),
                    },
                    failed_gates=promotion.failed_gates,
                    transition=transition,
                ),
            )

        next_ordinal = (ordered_work[-1].ordinal + 1) if ordered_work else 1
        self.repository.checkpoint(
            self.run_id,
            next_ordinal=next_ordinal,
            generation=generation + 1,
            payload={"best_candidate_hash": best_hash, "promotion_outcome": promotion_outcome},
        )
        outcome_state = RunState.COMPLETED if finish_run else RunState.RUNNING
        if finish_run:
            self.repository.set_state(self.run_id, RunState.COMPLETED, reason="cycle_complete")
        self._event("complete" if finish_run else "cycle_complete", 1.0, promotion_outcome)
        return DeepResearchOutcome(
            run_id=self.run_id,
            state=outcome_state,
            evaluated_attempts=len(ordered_work),
            fitness_by_ordinal=tuple((item.ordinal, attempts[item.ordinal].fitness) for item in ordered_work),
            best_candidate_hash=best_hash,
            promotion_outcome=promotion_outcome,
            worker_thread_limits=tuple(sorted(worker_limits)),
        )

    def _incumbent(self) -> tuple[str | None, float]:
        frame = self.repository.database.frame(
            "select candidate_hash, score from deep_research_promotions "
            "where run_id = :run_id and promoted = true and score is not null "
            "order by score desc, evaluated_at asc limit 1",
            {"run_id": self.run_id},
        )
        if frame.empty:
            return None, 0.0
        return str(frame.iloc[0].candidate_hash), float(frame.iloc[0].score)

    def _promotion(self, work, result, stress, sealed_returns, incumbent_score):
        net = np.asarray(result.gross_returns, dtype=float) - np.asarray(result.costs, dtype=float)
        sealed = np.asarray(sealed_returns, dtype=float)
        trial_rows = self.repository.database.frame(
            "select ordinal, fitness from deep_research_trials "
            "where run_id = :run_id and status = 'succeeded' and fitness is not null order by ordinal",
            {"run_id": self.run_id},
        )
        trial_sharpes = list(self.repository.global_successful_fitness(self.protocol))
        if len(trial_sharpes) >= 2 and len(net) >= 3:
            dsr = deflated_sharpe_probability(
                statistics.median(fold.net_sharpe for fold in result.folds),
                observations=len(net),
                trial_sharpes=trial_sharpes,
                skew=float(skew(net, bias=False)),
                kurtosis=float(kurtosis(net, fisher=False, bias=False)),
            )
        else:
            dsr = math.nan
        try:
            fold_rows = self.repository.database.frame(
                "select ordinal, fold_index, metrics from deep_research_fold_metrics "
                "where run_id = :run_id order by ordinal, fold_index",
                {"run_id": self.run_id},
            )
            fold_scores: dict[int, list[float]] = {}
            for row in fold_rows.itertuples(index=False):
                if isinstance(row.metrics, dict) and "net_return" in row.metrics:
                    fold_scores.setdefault(int(row.ordinal), []).append(float(row.metrics["net_return"]))
            matrix = pd.DataFrame({ordinal: pd.Series(values) for ordinal, values in fold_scores.items()}).dropna()
            pbo = probability_of_backtest_overfitting(matrix, segments=4)
        except ValueError:
            pbo = math.nan
        other_scores = [
            float(row.fitness) for row in trial_rows.itertuples(index=False) if int(row.ordinal) != work.ordinal
        ]
        try:
            stability = parameter_stability(other_scores, best_score=result.fitness)
        except ValueError:
            stability = math.nan
        positive = net[net > 0]
        concentration = float(positive.max() / positive.sum()) if len(positive) and positive.sum() > 0 else math.nan
        baseline = stress.by_name("baseline")
        doubled = stress.by_name("doubled_costs")
        return evaluate_research_promotion(
            ReliabilityEvidence(
                trade_count=result.trade_count,
                fold_net_returns=tuple(fold.net_return for fold in result.folds),
                fold_net_sharpes=tuple(fold.net_sharpe for fold in result.folds),
                doubled_cost_return=doubled.cumulative_return,
                deflated_sharpe_probability=dsr,
                bootstrap_positive_probability=bootstrap_positive_edge_probability(
                    net, block_size=min(10, len(net)), samples=1_000, seed=self.protocol.seed
                ),
                backtest_overfitting_probability=pbo,
                parameter_stability=stability,
                maximum_drawdown=baseline.maximum_drawdown,
                profit_concentration=concentration,
                sealed_test_return=float(np.prod(1 + np.clip(sealed, -0.999999, None)) - 1),
                causal_audit_passed=True,
                provenance_audit_passed=True,
                coverage_complete=True,
                execution_audit_passed=True,
                candidate_score=result.fitness,
                incumbent_score=incumbent_score,
                validation_tier="promotion",
                effective_sample_size=effective_sample_size(net) if len(net) else math.nan,
                lower_net_edge=lower_mean_confidence_bound(net) if len(net) >= 2 else math.nan,
                rolling_holdout_returns=tuple(fold.net_return for fold in result.folds),
                global_trial_count=self.repository.global_trial_count(self.protocol),
            )
        )


__all__ = [
    "CandidateWork",
    "DeepResearchCoordinator",
    "DeepResearchOutcome",
    "evaluate_resource_capacity",
    "recommended_worker_count",
    "resource_preemption_reason",
]
