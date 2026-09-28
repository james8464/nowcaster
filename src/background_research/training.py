"""Bounded adaptive training, immutable selection and consume-before-read holdout."""

from __future__ import annotations

import math
import os
import statistics
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from scipy.stats import kurtosis, skew

from src.background_research.contracts import LearningBatch, LearningCampaign, LearningStatus
from src.background_research.data import EligibleLearningData, LearningPhase, load_learning_data
from src.background_research.registry import LearningRegistry
from src.backtest.execution import ExecutionAssumptions
from src.backtest.intraday import RiskLimits
from src.backtest.robustness import effective_sample_size, lower_mean_confidence_bound
from src.database.engine import Database
from src.deep_research.candidates import CandidateDefinition, generate_candidates
from src.deep_research.contracts import AttemptStatus, CandidateAttempt, FoldEvidence, ResearchProtocol, RunState
from src.deep_research.control import ControlState, ResearchControl
from src.deep_research.coordinator import CandidateWork, DeepResearchCoordinator
from src.deep_research.evaluation import CandidateEvaluationPayload, RetainedEvaluationInput, evaluate_candidate_payload
from src.deep_research.promotion import ReliabilityEvidence, evaluate_research_promotion
from src.deep_research.repository import DeepResearchRepository
from src.deep_research.statistics import (
    bootstrap_positive_edge_probability,
    deflated_sharpe_probability,
    parameter_stability,
    probability_of_backtest_overfitting,
)
from src.deep_research.worker import _fold_metric
from src.learning.grammar import RuleNode
from src.research.round_two_registry import jsonl_writer_lock
from src.research.round_two_runtime import _strategy_registry
from src.research.round_two_walkforward import EvaluationMetrics, _gate
from src.strategies.types import canonical_hash


def _candidate(payload):
    def rule(value):
        children = tuple(rule(child) for child in value.get("children", ()))
        if value["operator"] in {"and", "or"} and len(children) > 2:
            while len(children) > 2:
                children = tuple(
                    RuleNode(value["operator"], children[i : i + 2]) if len(children[i : i + 2]) == 2 else children[i]
                    for i in range(0, len(children), 2)
                )
        return RuleNode(
            value["operator"],
            children,
            name=value.get("name"),
            value=value.get("value"),
            lag=value.get("lag", 0),
            parameters=tuple(value.get("parameters", {}).items()),
        )

    return CandidateDefinition(
        payload["kind"],
        payload["strategy_id"],
        tuple(payload["parameters"].items()),
        rule(payload["rule"]) if payload["rule"] is not None else None,
    )


def candidate_payload(
    candidate: CandidateDefinition, data: EligibleLearningData, phase: LearningPhase, *, symbol: str
) -> CandidateEvaluationPayload:
    registered = _strategy_registry().resolve(candidate.strategy_id)
    sources = [
        item for item in data.protocol.candidates if item.symbol == symbol and item.strategy_id == candidate.strategy_id
    ]
    if len(sources) != 1:
        raise ValueError("candidate source direction must be unambiguous")
    span = (phase.end - phase.start) / 4
    return CandidateEvaluationPayload(
        candidate,
        registered.spec.model_dump(mode="json"),
        pd.DataFrame(),
        pd.DataFrame(),
        data.protocol.source.provider,
        data.protocol.source.feed,
        symbol,
        phase.start,
        phase.end,
        tuple((phase.start + i * span, phase.start + (i + 1) * span) for i in range(4)),
        ExecutionAssumptions(),
        RiskLimits(),
        RetainedEvaluationInput(data.protocol, sources[0], phase.observations, phase.coverage, phase.reasons),
    )


class LearningTrainer:
    def __init__(self, registry: LearningRegistry):
        self.registry = registry

    def _state(self):
        with self.registry._locked():
            return self.registry._read()[0]

    def _append(self, batch, **event):
        self.registry.append_event(batch.batch_id, event)

    def _status(self, campaign, batch, state, reason):
        self._append(batch, kind="state", state=state, reason=reason)
        return self.registry.read_status(campaign.identity_hash)

    def _finish(self, campaign, batch, state, reason):
        self._append(batch, kind="completion", state=state, reason=reason)
        return self.registry.read_status(campaign.identity_hash)

    def _artifact(self, batch, phase, payload):
        self._append(
            batch,
            kind="artifact",
            phase=phase,
            payload={
                "batch_id": batch.batch_id,
                "campaign_hash": batch.campaign_hash,
                "data_fingerprint": batch.data_fingerprint,
                **payload,
            },
        )

    def _artifacts(self, batch):
        artifacts = {}
        for event in self._state().events[batch.batch_id]:
            if event.kind != "artifact" or event.phase == "interruption":
                continue
            payload = event.payload
            if (
                payload.get("batch_id") != batch.batch_id
                or payload.get("campaign_hash") != batch.campaign_hash
                or payload.get("data_fingerprint") != batch.data_fingerprint
                or event.phase in artifacts
            ):
                raise ValueError("retained evaluation artifact identity mismatch")
            artifacts[event.phase] = payload
        selection = artifacts.get("selection")
        if selection:
            if _candidate(selection["candidate"]).identity != selection["candidate_hash"]:
                raise ValueError("locked candidate identity mismatch")
            _, results = self._trials(batch)
            successful = [event for event in results.values() if event.outcome == "completed"]
            winner = (
                min(successful, key=lambda event: (-event.payload["fitness"], event.candidate_hash))
                if successful
                else None
            )
            if (
                winner is None
                or winner.attempt_id != selection["attempt_id"]
                or winner.candidate_hash != selection["candidate_hash"]
                or winner.payload["metrics"] != selection["metrics"]
                or winner.payload["fitness"] != selection["fitness"]
            ):
                raise ValueError("selection is not the retained training winner")
            for phase in ("validation", "holdout", "proposal"):
                if phase in artifacts and artifacts[phase]["candidate_hash"] != selection["candidate_hash"]:
                    raise ValueError("evaluation differs from locked training candidate")
        elif any(phase in artifacts for phase in ("validation", "holdout", "proposal")):
            raise ValueError("evaluation has no locked training selection")
        if "holdout" in artifacts:
            if "validation" not in artifacts or artifacts["validation"]["failed_gates"]:
                raise ValueError("holdout result requires passed locked validation")
            expected = (batch.batch_id, selection["candidate_hash"], artifacts["holdout"]["exposure_id"])
            if expected not in self._state().exposures:
                raise ValueError("holdout result has no durable exposure receipt")
        return artifacts

    def run_batch(
        self,
        campaign: LearningCampaign,
        batch: LearningBatch,
        *,
        control: ResearchControl,
        emit: Callable[[dict], None],
    ) -> LearningStatus:
        campaign, batch = campaign.validated(), batch.validated()
        state = self._state()
        if state.batches.get(batch.batch_id) != batch or state.campaigns[batch.campaign_hash][0] != campaign:
            raise ValueError("worker must use the original registered campaign and batch")
        # The root registry lock is brief; this separate batch lock excludes a
        # second worker for the entire computation without blocking controls.
        directory = self.registry.root / "batches" / canonical_hash(batch.batch_id)
        if directory.is_symlink() or (self.registry.root / "batches").is_symlink():
            raise ValueError("batch checkpoint directory cannot be linked")
        directory.mkdir(parents=True, exist_ok=True)
        database_name = f"training-{canonical_hash(control.run_id)}.duckdb"
        for name in (database_name, database_name + ".wal", ".worker.lock"):
            path = directory / name
            if path.is_symlink() or (path.exists() and path.stat().st_nlink > 1):
                raise ValueError("batch checkpoint files cannot be linked")
        with jsonl_writer_lock(directory / "worker"):
            state = self._state()
            data = load_learning_data(campaign, batch, now=datetime.now(UTC))
            artifacts = self._artifacts(batch)
            if control.read() is ControlState.STOPPED:
                raise ValueError("terminal control requires a fresh execution identity")
            if state.batch_finished(batch.batch_id):
                return self.registry.read_status(campaign.identity_hash)
            if state.batch_state(batch.batch_id) in {"paused", "pausing", "blocked"}:
                return self.registry.read_status(campaign.identity_hash)
            if control.read() is not ControlState.RUNNING:
                return self._status(campaign, batch, "paused", "Research control stopped or paused dispatch")
            if data.training.reasons:
                self._artifact(
                    batch,
                    "interruption",
                    {
                        "phase": "eligibility",
                        "reasons": data.training.reasons,
                        "coverage": str(data.training.coverage),
                        "gaps": [[start.isoformat(), end.isoformat()] for start, end in data.training.gaps],
                    },
                )
                return self._finish(
                    campaign,
                    batch,
                    "waiting",
                    "Insufficient eligible training history: " + ", ".join(data.training.reasons),
                )
            if "selection" not in artifacts:
                status = self._train(campaign, batch, data, directory / database_name, control, emit)
                if status is not None:
                    return status
                artifacts = self._artifacts(batch)
            selection = artifacts["selection"]
            candidate = _candidate(selection["candidate"])
            if control.read() is not ControlState.RUNNING:
                return self._status(campaign, batch, "paused", "Paused before validation")
            if "validation" not in artifacts:
                if data.validation.observations:
                    evidence = evaluate_candidate_payload(
                        candidate_payload(candidate, data, data.validation, symbol=batch.symbol)
                    )
                    metrics = evidence.retained_metrics
                else:
                    metrics = EvaluationMetrics(coverage=data.validation.coverage, reasons=data.validation.reasons)
                failures = sorted(
                    _gate(metrics, data.protocol, "validation")
                    | _gate(EvaluationMetrics.model_validate(selection["metrics"]), data.protocol, "training")
                )
                self._artifact(
                    batch,
                    "validation",
                    {
                        "candidate_hash": candidate.identity,
                        "metrics": metrics.model_dump(mode="json"),
                        "failed_gates": failures,
                        "global_trial_count": self.registry.read_status(campaign.identity_hash).attempt_count,
                    },
                )
                artifacts = self._artifacts(batch)
            validation = artifacts["validation"]
            if validation["failed_gates"]:
                insufficient = any(
                    "coverage" in gate or "insufficient_trades" in gate or "no_available" in gate
                    for gate in validation["failed_gates"]
                )
                return self._finish(
                    campaign,
                    batch,
                    "waiting" if insufficient else "completed",
                    "No qualified strategy: " + ", ".join(validation["failed_gates"]),
                )
            if control.read() is not ControlState.RUNNING:
                return self._status(campaign, batch, "paused", "Paused before final holdout reservation")
            if "holdout" not in artifacts:
                try:
                    exposure = self.registry.reserve_holdout(batch.batch_id, candidate.identity)
                except ValueError as error:
                    if "already exposed" not in str(error):
                        raise
                    return self._finish(
                        campaign, batch, "waiting", "Final interval already exposed; waiting for new held-out data"
                    )
                # Any failure after this point burns the interval permanently.
                try:
                    evidence = evaluate_candidate_payload(
                        candidate_payload(candidate, data, data.holdout, symbol=batch.symbol)
                    )
                    metrics = evidence.retained_metrics
                    self._artifact(
                        batch,
                        "holdout",
                        {
                            "candidate_hash": candidate.identity,
                            "exposure_id": exposure,
                            "metrics": metrics.model_dump(mode="json"),
                            "failed_gates": sorted(_gate(metrics, data.protocol, "holdout")),
                        },
                    )
                except Exception as error:
                    self._artifact(
                        batch, "interruption", {"phase": "holdout", "exposure_id": exposure, "error": str(error)[:500]}
                    )
                    return self._finish(
                        campaign, batch, "waiting", "Final evaluation interrupted; exposed interval cannot be retried"
                    )
                artifacts = self._artifacts(batch)
            if "proposal" not in artifacts:
                holdout = artifacts["holdout"]
                failures = sorted(set(holdout["failed_gates"]) | set(self._reliability(campaign, batch, artifacts)))
                self._artifact(
                    batch,
                    "proposal",
                    {
                        "candidate_hash": candidate.identity,
                        "candidate": candidate.payload(),
                        "purpose": "separately_registered_prospective_paper_evaluation",
                        "paper_only": True,
                        "qualification_status": "unqualified",
                        "training": selection["metrics"],
                        "validation": validation["metrics"],
                        "holdout": holdout["metrics"],
                        "failed_gates": failures,
                        "global_trial_count": self.registry.read_status(campaign.identity_hash).attempt_count,
                    },
                )
            return self._finish(
                campaign, batch, "completed", "Paper research proposal retained for separate prospective registration"
            )

    def _reliability(self, campaign, batch, artifacts):
        """Retain the existing stricter research gates; never apply a promotion."""
        state = self._state()
        trials = [
            event
            for other in state.batches.values()
            if state.key(other) == state.key(batch)
            for event in state.events[other.batch_id]
            if event.kind == "attempt_result" and event.outcome == "completed"
        ]
        selection, holdout = artifacts["selection"], EvaluationMetrics.model_validate(artifacts["holdout"]["metrics"])
        selected = next(
            event.payload
            for event in state.events[batch.batch_id]
            if event.kind == "attempt_result" and event.attempt_id == selection["attempt_id"]
        )
        folds = tuple(_fold_metric(tuple(values)) for values in selected["fold_returns"])
        net = np.asarray(selected["gross_returns"]) - np.asarray(selected["costs"])
        sharpes = [event.payload["fitness"] for event in trials]
        dsr = math.nan
        if len(sharpes) >= 2 and len(net) >= 3 and np.std(net) > 0:
            dsr = deflated_sharpe_probability(
                statistics.median(fold.net_sharpe for fold in folds),
                observations=len(net),
                trial_sharpes=sharpes,
                skew=float(skew(net, bias=False)),
                kurtosis=float(kurtosis(net, fisher=False, bias=False)),
            )
        try:
            matrix = pd.DataFrame(
                {
                    event.attempt_id: [
                        _fold_metric(tuple(values)).net_return for values in event.payload["fold_returns"]
                    ]
                    for event in trials
                }
            )
            pbo = probability_of_backtest_overfitting(matrix, segments=4)
        except ValueError:
            pbo = math.nan
        try:
            stability = parameter_stability(
                [event.payload["fitness"] for event in trials if event.candidate_hash != selection["candidate_hash"]],
                best_score=selection["fitness"],
            )
        except ValueError:
            stability = math.nan
        positive = net[net > 0]
        phases = [
            EvaluationMetrics.model_validate(artifacts[phase]["metrics"])
            for phase in ("selection", "validation", "holdout")
        ]
        decision = evaluate_research_promotion(
            ReliabilityEvidence(
                trade_count=holdout.trade_count,
                fold_net_returns=tuple(fold.net_return for fold in folds),
                fold_net_sharpes=tuple(fold.net_sharpe for fold in folds),
                doubled_cost_return=float(holdout.stressed_net_return),
                deflated_sharpe_probability=dsr,
                bootstrap_positive_probability=bootstrap_positive_edge_probability(
                    net, block_size=min(10, len(net)), samples=1000, seed=campaign.seed
                ),
                backtest_overfitting_probability=pbo,
                parameter_stability=stability,
                maximum_drawdown=max(float(phase.maximum_drawdown) for phase in phases),
                profit_concentration=float(positive.max() / positive.sum()) if len(positive) else math.nan,
                sealed_test_return=float(holdout.net_return),
                causal_audit_passed=True,
                provenance_audit_passed=True,
                coverage_complete=all(phase.coverage >= campaign.cost_policy.minimum_coverage for phase in phases),
                execution_audit_passed=all(not phase.reasons for phase in phases),
                candidate_score=selection["fitness"],
                incumbent_score=0.0,
                validation_tier="promotion",
                effective_sample_size=effective_sample_size(net),
                lower_net_edge=lower_mean_confidence_bound(net) if len(net) >= 2 else math.nan,
                # One inspected final interval is ONE held-out result. Training folds
                # must never masquerade as the three independent rolling holdouts.
                rolling_holdout_returns=(float(holdout.net_return),),
                global_trial_count=self.registry.read_status(campaign.identity_hash).attempt_count,
            )
        )
        return decision.failed_gates

    def _train(self, campaign, batch, data, database_path, control, emit):
        self._status(campaign, batch, "training", "Searching only the registered training interval")
        # An execution can stop permanently while its immutable batch remains
        # resumable. A new launch gets a new derived index, never a reset budget.
        execution_id = control.run_id
        database = Database.from_url(f"duckdb:///{database_path}")
        database.initialize()
        repository = DeepResearchRepository(database)
        protocol = ResearchProtocol(
            dataset_hash=batch.data_fingerprint,
            code_hash=campaign.code_hash,
            search_space_hash=canonical_hash([space.model_dump(mode="json") for space in campaign.search_spaces]),
            cost_policy_hash=canonical_hash(campaign.cost_policy.model_dump(mode="json")),
            symbol=batch.symbol,
            provider=data.protocol.source.provider,
            feed=data.protocol.source.feed,
            interval=data.protocol.interval,
            seed=campaign.seed,
            workers=max(1, min((os.cpu_count() or 1) // 2, (os.cpu_count() or 1) - 2)),
            trial_budget=None,
            continuous=True,
            cycle_budget=50,
            final_test_start=batch.validation_end,
            created_at=batch.created_at,
            search_family="background_learning",
        )

        def forbidden(_):
            raise AssertionError("background training cannot inspect sealed results")

        def retain_result(attempt, result):
            payload = {"ordinal": attempt.ordinal, "generation": attempt.generation, "candidate": attempt.definition}
            if result is None:
                outcome = "failed"
                payload["error"] = attempt.error_summary
            else:
                if result.retained_metrics is None:
                    raise ValueError("worker result has no receipt-replay evidence")
                payload.update(
                    fitness=result.fitness,
                    fold_returns=result.fold_returns,
                    gross_returns=result.gross_returns,
                    costs=result.costs,
                    metrics=result.retained_metrics.model_dump(mode="json"),
                )
                outcome = "completed"
            self._append(
                batch,
                kind="attempt_result",
                attempt_id=f"{batch.batch_id}:{attempt.ordinal}",
                candidate_hash=attempt.candidate_hash,
                outcome=outcome,
                payload=payload,
            )
            self._append(batch, kind="checkpoint", checkpoint=f"{batch.batch_id}:attempt:{attempt.ordinal + 1}")

        coordinator = DeepResearchCoordinator(
            run_id=execution_id,
            protocol=protocol,
            repository=repository,
            control=control,
            sealed_evaluator=forbidden,
            emit=emit,
            on_result=retain_result,
        )
        spaces = sorted(
            (space.to_search_space() for space in campaign.search_spaces if space.symbol == batch.symbol),
            key=lambda space: space.strategy_id,
        )
        try:
            if database.scalar("select count(*) from deep_research_runs"):
                retained_protocol = database.scalar("select protocol_id from deep_research_runs")
                if retained_protocol != protocol.identity:
                    raise ValueError("training checkpoint protocol identity mismatch")
                if not database.scalar("select count(*) from deep_research_checkpoints"):
                    repository.checkpoint(
                        execution_id, next_ordinal=1, generation=1, payload={"reason": "recover_from_parent_receipts"}
                    )
                repository.resume_run(execution_id, protocol)
            else:
                repository.create_run(execution_id, protocol)
                repository.checkpoint(execution_id, next_ordinal=1, generation=1, payload={"reason": "reserved_batch"})
            for generation in (1, 2):
                first = (generation - 1) * 50 + 1
                count = min(50, batch.max_attempts - first + 1)
                if count <= 0:
                    break
                attempts, results = self._trials(batch)
                incumbent = None
                if generation == 2:
                    successful = [
                        event
                        for event in results.values()
                        if event.outcome == "completed" and event.payload["ordinal"] < 51
                    ]
                    if not successful:
                        break
                    winner = min(successful, key=lambda event: (-event.payload["fitness"], event.candidate_hash))
                    incumbent = _candidate(winner.payload["candidate"])
                    spaces = [space for space in spaces if space.strategy_id == incumbent.strategy_id]
                    spaces = [
                        replace(
                            spaces[0],
                            base_parameters=dict(incumbent.parameters) or dict(spaces[0].base_parameters),
                            seed_rules=(incumbent.rule,) if incumbent.rule else spaces[0].seed_rules,
                        )
                    ]
                generated = [
                    generate_candidates(space, count=count, seed=campaign.seed + generation - 1, incumbent=incumbent)
                    for space in spaces
                ]
                works = []
                for index in range(count):
                    ordinal = first + index
                    candidate = generated[index % len(spaces)][index // len(spaces)].candidate
                    attempt_id = f"{batch.batch_id}:{ordinal}"
                    if attempt_id in results:
                        continue
                    if control.read() is not ControlState.RUNNING:
                        self._append(batch, kind="checkpoint", checkpoint=f"{batch.batch_id}:attempt:{ordinal}")
                        return self._status(campaign, batch, "paused", "Dispatch checkpointed by research control")
                    if attempt_id in attempts:
                        self._append(
                            batch,
                            kind="attempt_result",
                            attempt_id=attempt_id,
                            candidate_hash=attempts[attempt_id].candidate_hash,
                            outcome="interrupted",
                            payload={
                                "ordinal": ordinal,
                                "generation": generation,
                                "reason": "reserved attempt interrupted before retained result",
                            },
                        )
                        continue
                    duplicate = next(
                        (event for event in attempts.values() if event.candidate_hash == candidate.identity), None
                    )
                    self._append(
                        batch,
                        kind="attempt",
                        attempt_id=attempt_id,
                        candidate_hash=candidate.identity,
                        payload={"ordinal": ordinal, "generation": generation, "candidate": candidate.payload()},
                    )
                    payload = {"ordinal": ordinal, "generation": generation, "candidate": candidate.payload()}
                    if duplicate:
                        outcome = "rejected"
                        payload["duplicate_of"] = duplicate.attempt_id
                    else:
                        try:
                            if candidate.rule is not None:
                                space = spaces[index % len(spaces)]
                                candidate.rule.validate_bounds(max_depth=space.max_depth, max_nodes=space.max_nodes)
                                nodes = [candidate.rule]
                                while nodes:
                                    node = nodes.pop()
                                    if node.lag > space.maximum_lag:
                                        raise ValueError("generated rule exceeds registered maximum lag")
                                    nodes.extend(node.children)
                            evaluation = candidate_payload(candidate, data, data.training, symbol=batch.symbol)
                            works.append(
                                CandidateWork(
                                    ordinal,
                                    candidate.identity,
                                    candidate.payload(),
                                    (),
                                    (),
                                    (),
                                    evaluation_payload=evaluation,
                                )
                            )
                            attempts, results = self._trials(batch)
                            continue
                        except (ValueError, KeyError, ArithmeticError) as error:
                            outcome = "invalid"
                            payload["error"] = f"{type(error).__name__}: {error}"[:500]
                    self._append(
                        batch,
                        kind="attempt_result",
                        attempt_id=attempt_id,
                        candidate_hash=candidate.identity,
                        outcome=outcome,
                        payload=payload,
                    )
                    attempts, results = self._trials(batch)
                self._sync_trials(batch, repository, execution_id)
                if works:
                    outcome = coordinator.run(
                        works, generation=generation, create_run=False, finish_run=False, evaluate_final=False
                    )
                    if outcome.state != RunState.RUNNING:
                        return self._status(campaign, batch, "paused", outcome.promotion_outcome)
                self._append(batch, kind="checkpoint", checkpoint=f"{batch.batch_id}:generation:{generation + 1}")
            _, results = self._trials(batch)
            successful = [event for event in results.values() if event.outcome == "completed"]
            if not successful:
                return self._finish(campaign, batch, "failed", "No evaluable training candidate; all attempts retained")
            winner = min(successful, key=lambda event: (-event.payload["fitness"], event.candidate_hash))
            self._artifact(
                batch,
                "selection",
                {
                    "candidate_hash": winner.candidate_hash,
                    "candidate": winner.payload["candidate"],
                    "fitness": winner.payload["fitness"],
                    "metrics": winner.payload["metrics"],
                    "attempt_id": winner.attempt_id,
                    "global_trial_count": self.registry.read_status(campaign.identity_hash).attempt_count,
                },
            )
            return None
        finally:
            database.engine.dispose()

    def _sync_trials(self, batch, repository, execution_id):
        """Repair only the derived training index from durable parent receipts."""
        existing = set(repository.database.frame("select ordinal from deep_research_trials")["ordinal"])
        _, results = self._trials(batch)
        for event in sorted(results.values(), key=lambda item: item.payload["ordinal"]):
            payload = event.payload
            ordinal = payload["ordinal"]
            if ordinal in existing:
                continue
            status = {
                "completed": AttemptStatus.SUCCEEDED,
                "rejected": AttemptStatus.DUPLICATE,
                "invalid": AttemptStatus.INVALID,
                "interrupted": AttemptStatus.INTERRUPTED,
                "failed": AttemptStatus.FAILED,
            }[event.outcome]
            repository.append_attempt(
                execution_id,
                CandidateAttempt(
                    ordinal=ordinal,
                    candidate_hash=event.candidate_hash,
                    definition=payload.get("candidate", {}),
                    status=status,
                    attempted_at=batch.created_at,
                    completed_at=datetime.now(UTC),
                    generation=payload["generation"],
                    fitness=payload.get("fitness"),
                    error_summary=payload.get("error") or payload.get("reason"),
                ),
            )
            for index, values in enumerate(payload.get("fold_returns", ())):
                metric = _fold_metric(tuple(values))
                repository.append_fold_evidence(
                    execution_id,
                    FoldEvidence(
                        ordinal,
                        index,
                        {
                            "net_return": metric.net_return,
                            "net_sharpe": metric.net_sharpe,
                            "maximum_drawdown": metric.maximum_drawdown,
                            "observations": float(metric.observations),
                        },
                    ),
                )

    def _trials(self, batch):
        events = self._state().events[batch.batch_id]
        attempts = {event.attempt_id: event for event in events if event.kind == "attempt"}
        results = {event.attempt_id: event for event in events if event.kind == "attempt_result"}
        for event in results.values():
            if event.outcome == "completed" and (
                not math.isfinite(event.payload["fitness"])
                or _candidate(event.payload["candidate"]).identity != event.candidate_hash
            ):
                raise ValueError("retained training result identity mismatch")
        return attempts, results
