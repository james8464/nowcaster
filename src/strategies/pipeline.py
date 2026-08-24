from __future__ import annotations

import json
import math
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import httpx
import numpy as np
import pandas as pd
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from src.app_snapshot.builder import build_app_snapshot
from src.app_snapshot.writer import write_snapshot_atomic
from src.backtest.execution import ExecutionAssumptions
from src.backtest.intraday import RiskLimits, run_intraday_backtest
from src.config.settings import Settings
from src.database.engine import Database
from src.database.schema import NATURAL_KEYS, TABLES, causal_audits, dataset_coverage_requests, strategy_runs
from src.ingestion.alpaca_bars import AlpacaBarProvider
from src.ingestion.bars import INTERVAL_DURATION, BarProvider, BarQuery, BarRequest
from src.ingestion.binance_bars import BinanceBarProvider
from src.ingestion.csv_bars import CSVBarProvider
from src.learning.promotion import ForwardEvidence, promote_candidate
from src.learning.search import LearningExperiment, RuleCandidate, discover_rules
from src.reporting.strategy_report import write_strategy_research_report_atomic
from src.strategies.datasets import BarRepository, DatasetGap, DatasetManifest
from src.strategies.engine import generate_current_decision
from src.strategies.ensemble import DEFAULT_ENSEMBLE_CONFIG, EnsembleConfig, EnsembleDecision, evidence_weight_rows
from src.strategies.indicators import rolling_zscore, rsi
from src.strategies.library import StrategyContext, audit_prefix_invariance, build_strategy_registry
from src.strategies.registry import RegisteredStrategy, StrategyRegistry
from src.strategies.types import BarInterval, StrategyMode, canonical_hash
from src.strategies.validation import (
    DEFAULT_VALIDATION_CONFIG,
    EvaluationRequest,
    FinalBoundary,
    FoldEvidence,
    PromotionDecision,
    StrategyEvaluation,
    StrategyRunEvidence,
    TrialEvidence,
    ValidationConfig,
    WalkForwardFold,
    calculate_fold_calibration_error,
    evaluate_registry,
    make_outer_folds,
    select_final_boundary,
    validation_policy_hash,
)


class BarProviderName(StrEnum):
    ALPACA = "alpaca"
    BINANCE = "binance"
    CSV = "csv"


class PipelineEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event: Literal["started", "progress", "complete", "error"]
    stage: str | None = None
    progress: float | None = Field(default=None, ge=0, le=1)
    message: str | None = None

    def json_line(self) -> str:
        return json.dumps(
            self.model_dump(exclude_none=True),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


class StrategyScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    strategy_ids: tuple[str, ...] = Field(validation_alias=AliasChoices("strategy_ids", "strategy_id"))
    provider: BarProviderName
    feed: str
    symbol: str
    interval: BarInterval
    mode: StrategyMode = StrategyMode.PAPER

    @field_validator("strategy_ids", mode="before")
    @classmethod
    def normalized_strategy_ids(cls, value: Any) -> tuple[str, ...]:
        raw = (value,) if isinstance(value, str) else tuple(value or ())
        normalized = tuple(str(item).strip() for item in raw if str(item).strip())
        if not normalized:
            raise ValueError("at least one strategy ID is required")
        if len(normalized) != len(set(normalized)):
            raise ValueError("strategy IDs must be unique")
        return normalized

    @field_validator("feed")
    @classmethod
    def non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("strategy ID and feed must not be empty")
        return normalized

    @property
    def strategy_id(self) -> str:
        """Retain scalar compatibility for stages that require one strategy."""

        return self.strategy_ids[0]

    @field_validator("symbol")
    @classmethod
    def normalized_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol must not be empty")
        return normalized


class IngestOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: StrategyScope
    start: datetime
    end: datetime
    force: bool = False

    @field_validator("start", "end")
    @classmethod
    def explicit_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
            raise ValueError("ingestion boundaries must be explicit UTC datetimes")
        return value.astimezone(UTC).replace(tzinfo=UTC)

    @model_validator(mode="after")
    def ordered_range(self) -> IngestOptions:
        if self.end <= self.start:
            raise ValueError("ingestion end must be after start")
        return self


class EvaluationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: StrategyScope
    force: bool = False


class LearningOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: StrategyScope
    evaluation_budget: int = Field(default=20, ge=1, le=100)
    seed: int = 42
    force: bool = False


class ExportOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_path: Path
    report_path: Path


class CoverageRequestEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    coverage_request_id: str
    dataset_hash: str
    requested_start: datetime
    requested_end: datetime
    requested_at: datetime
    row_count: int = Field(ge=0)


class EvaluationCoverageManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    dataset_hash: str
    provider: str
    feed: str
    symbol: str
    interval: str
    requested_start: datetime
    requested_end: datetime
    coverage_start: datetime | None
    coverage_end: datetime | None
    row_count: int = Field(ge=0)
    gaps: tuple[DatasetGap, ...]
    calendar_id: str
    calendar_version: str
    contributing_requests: tuple[CoverageRequestEvidence, ...]


@dataclass(frozen=True, slots=True)
class StageOutcome:
    status: Literal["completed", "reused", "unavailable"]
    message: str
    dataset_hash: str | None = None
    strategy_run_id: str | None = None
    strategy_run_ids: tuple[str, ...] = ()
    learning_run_id: str | None = None
    evaluated_candidates: int = 0
    snapshot_path: Path | None = None
    report_path: Path | None = None


EventSink = Callable[[PipelineEvent], None]


@dataclass(frozen=True, slots=True)
class ComponentEvaluation:
    registered: RegisteredStrategy
    evaluation: StrategyEvaluation
    signals: pd.DataFrame
    backtest: Any
    audit_details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EvaluationBatch:
    components: tuple[ComponentEvaluation, ...]
    ensemble_decision: EnsembleDecision
    resolved_outcomes: pd.DataFrame


@dataclass(frozen=True, slots=True)
class SealedResearchSnapshot:
    query: BarQuery
    manifest: DatasetManifest
    coverage_manifest: EvaluationCoverageManifest
    as_of: datetime
    signal_bars: pd.DataFrame
    causal_bars: pd.DataFrame


_RESOLVED_OUTCOME_DTYPES: tuple[tuple[str, str], ...] = (
    ("strategy_id", "string"),
    ("decision_timestamp", "datetime64[ns, UTC]"),
    ("execution_timestamp", "datetime64[ns, UTC]"),
    ("outcome_available_at", "datetime64[ns, UTC]"),
    ("signal", "int8"),
    ("realized_return", "float64"),
    ("cost", "float64"),
    ("source_decision_hash", "string"),
    ("source_execution_hash", "string"),
    ("dataset_hash", "string"),
    ("strategy_version", "string"),
    ("symbol", "string"),
    ("interval", "string"),
    ("mode", "string"),
)


_CONSUMPTION_LOCKS: dict[str, threading.Lock] = {}
_CONSUMPTION_LOCKS_GUARD = threading.Lock()


def _lock_for(identity: str) -> threading.Lock:
    with _CONSUMPTION_LOCKS_GUARD:
        return _CONSUMPTION_LOCKS.setdefault(identity, threading.Lock())


def consume_forward_evidence_and_promote(
    database: Database,
    candidate: RuleCandidate,
    evidence: ForwardEvidence,
    *,
    dataset_hash: str,
    symbol: str,
    interval: BarInterval,
    mode: StrategyMode = StrategyMode.PAPER,
) -> PromotionDecision:
    """Spend one inspected learned-rule forward block before evaluating promotion."""

    if not evidence.outer_block_inspected:
        return promote_candidate(candidate, evidence)
    normalized_symbol = symbol.strip().upper()
    if not dataset_hash.strip() or not normalized_symbol:
        raise ValueError("promotion context must have a dataset hash and symbol")
    identity_payload = {
        "kind": "learned_forward_outer_block_consumption_v1",
        "dataset_hash": dataset_hash,
        "strategy_id": candidate.strategy_id,
        "strategy_version": candidate.version,
        "candidate_hash": candidate.candidate_hash,
        "symbol": normalized_symbol,
        "interval": interval.value,
        "period_start": evidence.period_start,
        "period_end": evidence.period_end,
    }
    audit_id = canonical_hash(identity_payload)
    details = {
        **identity_payload,
        "period_start": evidence.period_start.isoformat(),
        "period_end": evidence.period_end.isoformat(),
        "mode": mode.value,
        "outer_block_inspected": True,
        "outer_block_consumed": True,
        "validation_promoted": evidence.validation.promoted,
        "validation_reasons": list(evidence.validation.reasons),
    }
    row = {
        "audit_id": audit_id,
        "dataset_hash": dataset_hash,
        "strategy_id": candidate.strategy_id,
        "strategy_version": candidate.version,
        "symbol": normalized_symbol,
        "interval": interval.value,
        "mode": mode.value,
        "audited_at": evidence.causal_audited_at,
        "passed": evidence.causal_audit_passed,
        "details": details,
        "source": "strategy_pipeline_promotion_boundary",
        "source_version": "1",
        "created_at": evidence.evaluated_at,
    }
    with _lock_for(audit_id):
        try:
            with database.engine.begin() as connection:
                consumed = connection.execute(
                    select(causal_audits.c.audit_id).where(causal_audits.c.audit_id == audit_id)
                ).scalar_one_or_none()
                if consumed is not None:
                    return PromotionDecision(False, ("forward outer block has already been consumed",))
                connection.execute(insert(causal_audits).values(**row))
        except IntegrityError:
            return PromotionDecision(False, ("forward outer block has already been consumed",))
    return promote_candidate(candidate, evidence)


class StrategyPipeline:
    """Dependency-injectable orchestration around the causal Task 1-6 engines."""

    def __init__(
        self,
        database: Database,
        registry: StrategyRegistry,
        providers: Mapping[BarProviderName, BarProvider],
        *,
        provider_unavailable: Mapping[BarProviderName, str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        validation_config: ValidationConfig = DEFAULT_VALIDATION_CONFIG,
        ensemble_config: EnsembleConfig = DEFAULT_ENSEMBLE_CONFIG,
        execution_assumptions: ExecutionAssumptions | None = None,
    ):
        self.database = database
        self.database.initialize()
        self.registry = registry
        self.providers = dict(providers)
        self.provider_unavailable = dict(provider_unavailable or {})
        self.clock = clock
        self.validation_config = validation_config
        self.ensemble_config = ensemble_config
        self.execution_assumptions = execution_assumptions or ExecutionAssumptions()
        self.bars = BarRepository(database)

    def ingest(self, options: IngestOptions, emit: EventSink | None = None) -> StageOutcome:
        registered = self._registered_many(options.scope)
        identities = ",".join(item.spec.strategy_id for item in registered)
        self._emit(emit, "progress", "ingest", 0.1, f"resolved {identities}")
        query = BarQuery(
            provider=options.scope.provider.value,
            feed=options.scope.feed,
            symbol=options.scope.symbol,
            interval=options.scope.interval,
            start=options.start,
            end=options.end,
        )
        reservation_id = self._reserve_coverage_request(query, self.bars.manifest(query), force=options.force)
        provider = self.providers.get(options.scope.provider)
        if provider is None:
            reason = self.provider_unavailable.get(options.scope.provider, "provider is not configured")
            manifest = self.bars.manifest(query)
            self._finalize_coverage_request(reservation_id, manifest, "unavailable")
            return StageOutcome("unavailable", f"provider unavailable: {reason}")
        gaps = self.bars.gaps(query)
        if options.force:
            gaps = (DatasetGap(start=options.start, end=options.end, missing_bars=1),)
        if not gaps:
            manifest = self.bars.manifest(query)
            self._finalize_coverage_request(reservation_id, manifest, "complete")
            self._emit(emit, "progress", "ingest", 1.0, "coverage already available")
            return StageOutcome("reused", "coverage already available", dataset_hash=manifest.dataset_hash)

        inserted = 0
        fetched_count = 0
        try:
            for position, gap in enumerate(gaps, start=1):
                fetched = list(
                    provider.fetch(
                        BarRequest(
                            symbol=options.scope.symbol,
                            interval=options.scope.interval,
                            start=gap.start,
                            end=gap.end,
                            feed=options.scope.feed,
                        )
                    )
                )
                fetched_count += len(fetched)
                append_identity = _market_bar_lock_identity(
                    query.provider,
                    query.feed,
                    query.symbol,
                    query.interval,
                )
                with _lock_for(append_identity):
                    inserted += self.bars.append(fetched)
                self._emit(
                    emit,
                    "progress",
                    "ingest",
                    0.1 + 0.8 * position / len(gaps),
                    f"fetched missing coverage {position}/{len(gaps)}",
                )
        except Exception as error:
            try:
                self._finalize_coverage_request(reservation_id, self.bars.manifest(query), "unavailable")
            except Exception as persistence_error:
                error.add_note(f"failed to persist unavailable coverage: {persistence_error}")
            raise
        manifest = self.bars.manifest(query)
        missing = sum(gap.missing_bars for gap in manifest.gaps)
        empty_force = options.force and fetched_count == 0
        if empty_force:
            message = "data unavailable: forced refresh returned an empty response"
            status: Literal["completed", "reused", "unavailable"] = "unavailable"
            request_status = "unavailable" if not missing else "incomplete"
        elif missing:
            message = f"data unavailable: {missing} requested bars remain missing"
            status = "unavailable"
            request_status = "incomplete"
        else:
            message = f"appended {inserted} immutable revisions"
            status = "completed"
            request_status = "complete"
        self._finalize_coverage_request(reservation_id, manifest, request_status)
        self._emit(emit, "progress", "ingest", 1.0, message)
        return StageOutcome(status, message, dataset_hash=manifest.dataset_hash)

    def evaluate(self, options: EvaluationOptions, emit: EventSink | None = None) -> StageOutcome:
        registered = self._registered_many(options.scope)
        for source_attempt in range(3):
            snapshot, unavailable = self._capture_research_snapshot(options.scope)
            if snapshot is None:
                return StageOutcome("unavailable", unavailable or "requested coverage is unavailable")
            cohort = self._cohort_payload(
                options.scope,
                registered,
                snapshot.manifest.dataset_hash,
                snapshot.as_of,
            )
            cohort_id = canonical_hash(cohort)
            cohort["coverage_manifest"] = snapshot.coverage_manifest.model_dump(mode="json")
            cached, run_contexts, cohort_generation = self._claim_evaluation_cohort(
                options.scope,
                registered,
                snapshot.manifest.dataset_hash,
                cohort,
                cohort_id,
                force=options.force,
            )
            if cached:
                if not self._source_snapshot_is_current(options.scope, snapshot):
                    continue
                self._emit(emit, "progress", "evaluate", 1.0, "reused cached evaluation")
                return StageOutcome(
                    "reused",
                    "reused cached evaluation",
                    dataset_hash=snapshot.manifest.dataset_hash,
                    strategy_run_id=cached[0],
                    strategy_run_ids=cached,
                )
            self._emit(emit, "progress", "evaluate", 0.1, "loaded one sealed compatible-history snapshot")
            batch: EvaluationBatch | None = None
            try:
                batch = self._evaluate_engines(
                    options.scope,
                    registered,
                    snapshot.query,
                    snapshot.manifest,
                    snapshot.as_of,
                    signal_bars=snapshot.signal_bars,
                    bars=snapshot.causal_bars,
                )
                if not self._persist_evaluation_batch_if_current(
                    options.scope,
                    snapshot,
                    run_contexts,
                    batch,
                    cohort,
                    cohort_id,
                    cohort_generation,
                ):
                    for item, run_id, run_timestamp in run_contexts:
                        self._persist_failed_run(
                            options.scope,
                            item,
                            snapshot.manifest.dataset_hash,
                            run_id,
                            run_timestamp,
                            "source bar generation changed before terminal commit",
                            stage="source_snapshot",
                        )
                    if source_attempt < 2:
                        continue
                    return StageOutcome("unavailable", "source bars changed repeatedly; retry evaluation")
            except Exception as error:
                committed = batch is not None and self._evaluation_cohort_is_complete(
                    options.scope,
                    snapshot.manifest.dataset_hash,
                    run_contexts,
                    batch,
                    cohort_id,
                )
                if committed:
                    pass
                else:
                    for item, run_id, run_timestamp in run_contexts:
                        try:
                            self._persist_failed_run(
                                options.scope,
                                item,
                                snapshot.manifest.dataset_hash,
                                run_id,
                                run_timestamp,
                                str(error),
                            )
                        except Exception as persistence_error:
                            error.add_note(f"failed to persist evaluation failure: {persistence_error}")
                    raise
            assert batch is not None
            statuses = {component.evaluation.status.value for component in batch.components}
            message = (
                "evaluation completed" if statuses == {"evaluated"} else f"evaluation statuses: {sorted(statuses)}"
            )
            self._emit(emit, "progress", "evaluate", 1.0, message)
            run_ids = tuple(run_id for _, run_id, _ in run_contexts)
            return StageOutcome(
                "completed",
                message,
                dataset_hash=snapshot.manifest.dataset_hash,
                strategy_run_id=run_ids[0],
                strategy_run_ids=run_ids,
            )
        return StageOutcome("unavailable", "source bars changed repeatedly; retry evaluation")

    def _claim_evaluation_cohort(
        self,
        scope: StrategyScope,
        registered: Sequence[RegisteredStrategy],
        dataset_hash: str,
        cohort: Mapping[str, Any],
        cohort_id: str,
        *,
        force: bool,
    ) -> tuple[tuple[str, ...], list[tuple[RegisteredStrategy, str, datetime]], int]:
        lock_identities = {
            canonical_hash(["strategy_evaluation_cohort_reservation", cohort_id]),
            *(
                canonical_hash(
                    ["strategy_evaluation_component_reservation", *self._cache_key(scope, item, dataset_hash)]
                )
                for item in registered
            ),
        }
        with ExitStack() as locks:
            for lock_identity in sorted(lock_identities):
                locks.enter_context(_lock_for(lock_identity))
            retry_error: Exception | None = None
            contexts: list[tuple[RegisteredStrategy, str, datetime]] = []
            for _attempt in range(5):
                cached = self._cached_cohort(scope, registered, dataset_hash, cohort_id)
                if cached and not force:
                    return cached, [], 0
                contexts, rows, cohort_generation = self._evaluation_cohort_reservation_plan(
                    scope,
                    registered,
                    dataset_hash,
                    cohort,
                    cohort_id,
                )
                try:
                    with self.database.engine.begin() as connection:
                        connection.execute(insert(strategy_runs), rows)
                except (IntegrityError, OperationalError) as error:
                    retry_error = error
                    continue
                except Exception as error:
                    self._persist_cohort_reservation_failure(scope, dataset_hash, contexts, cohort, error)
                    raise
                return (), contexts, cohort_generation
            assert retry_error is not None
            self._persist_cohort_reservation_failure(scope, dataset_hash, contexts, cohort, retry_error)
            raise retry_error

    def _evaluation_cohort_reservation_plan(
        self,
        scope: StrategyScope,
        registered: Sequence[RegisteredStrategy],
        dataset_hash: str,
        cohort: Mapping[str, Any],
        cohort_id: str,
    ) -> tuple[list[tuple[RegisteredStrategy, str, datetime]], list[dict[str, Any]], int]:
        prior = self.database.frame(
            "select metrics from strategy_runs where dataset_hash = :dataset_hash "
            "and symbol = :symbol and interval = :interval and mode = :mode",
            {
                "dataset_hash": dataset_hash,
                "symbol": scope.symbol,
                "interval": scope.interval.value,
                "mode": scope.mode.value,
            },
        )
        generations = [
            int(metrics["cohort_generation"])
            for metrics in prior.get("metrics", [])
            if isinstance(metrics, dict)
            and metrics.get("cohort_id") == cohort_id
            and isinstance(metrics.get("cohort_generation"), int)
        ]
        cohort_generation = max(generations, default=0) + 1
        run_timestamp = self._cohort_run_timestamp(scope, registered, dataset_hash)
        contexts: list[tuple[RegisteredStrategy, str, datetime]] = []
        rows: list[dict[str, Any]] = []
        for item in registered:
            component_generation = self._exact_run_count(scope, item, dataset_hash) + 1
            run_id = canonical_hash(
                {
                    "cache_key": self._cache_key(scope, item, dataset_hash),
                    "cohort_id": cohort_id,
                    "cohort_generation": cohort_generation,
                    "run_timestamp": run_timestamp,
                    "component_generation": component_generation,
                }
            )
            metrics = {
                "reservation_generation": component_generation,
                "cohort_generation": cohort_generation,
                "cohort_id": cohort_id,
                "cohort_members": cohort["members"],
                "cohort_effective_at": run_timestamp.isoformat(),
                "coverage_manifest": cohort["coverage_manifest"],
                "state": scope.mode.value,
            }
            rows.append(
                self._strategy_run_row(
                    scope,
                    item,
                    dataset_hash,
                    run_id,
                    run_timestamp,
                    status="running",
                    metrics=metrics,
                    ended_at=None,
                )
            )
            contexts.append((item, run_id, run_timestamp))
        return contexts, rows, cohort_generation

    def _persist_cohort_reservation_failure(
        self,
        scope: StrategyScope,
        dataset_hash: str,
        contexts: Sequence[tuple[RegisteredStrategy, str, datetime]],
        cohort: Mapping[str, Any],
        error: Exception,
    ) -> None:
        for item, run_id, run_timestamp in contexts:
            try:
                self._persist_failed_run(
                    scope,
                    item,
                    dataset_hash,
                    run_id,
                    run_timestamp,
                    str(error),
                    stage="reservation",
                    initial_metrics={"coverage_manifest": cohort["coverage_manifest"]},
                )
            except Exception as persistence_error:
                error.add_note(f"failed to persist reservation failure: {persistence_error}")

    def learn(self, options: LearningOptions, emit: EventSink | None = None) -> StageOutcome:
        registered = self._registered(options.scope)
        with _lock_for(_scope_bar_lock_identity(options.scope)):
            query, manifest, _coverage_manifest, unavailable = self._authenticated_coverage(options.scope)
            if query is None or manifest is None:
                return StageOutcome("unavailable", unavailable or "requested coverage is unavailable")
            as_of = self._query_as_of(query)
            bars = self.bars.causal_bars_as_of(query, as_of).copy(deep=True)
            experiment, development = self._learning_experiment(
                options,
                registered,
                manifest,
                bars,
                self._raw_final_boundary(bars),
            )
            if not options.force and self.database.scalar(
                "select count(*) from learning_trials where learning_run_id = :run_id",
                {"run_id": experiment.learning_run_id},
            ):
                existing = int(
                    self.database.scalar(
                        "select count(*) from learning_trials where learning_run_id = :run_id",
                        {"run_id": experiment.learning_run_id},
                    )
                    or 0
                )
                if existing == options.evaluation_budget:
                    self._emit(emit, "progress", "learn", 1.0, "reused observed learning trial ledger")
                    return StageOutcome(
                        "reused",
                        "reused observed learning trial ledger",
                        dataset_hash=manifest.dataset_hash,
                        learning_run_id=experiment.learning_run_id,
                        evaluated_candidates=existing,
                    )
            self._emit(emit, "progress", "learn", 0.1, "sealed final boundary before bounded search")
            result = discover_rules(experiment, development)
            self._emit(emit, "progress", "learn", 1.0, f"observed {result.trial_count} trial ledger rows")
            return StageOutcome(
                "completed",
                f"observed {result.trial_count} trial ledger rows",
                dataset_hash=manifest.dataset_hash,
                learning_run_id=result.learning_run_id,
                evaluated_candidates=result.trial_count,
            )

    def export(self, options: ExportOptions, emit: EventSink | None = None) -> StageOutcome:
        settings = self._settings
        if not (settings.project_root / ".git").exists():
            settings = settings.model_copy(update={"project_root": Path(__file__).resolve().parents[2]})
        snapshot = build_app_snapshot(self.database, settings)
        providers = self.database.frame("select distinct provider, feed from market_bars order by provider, feed")
        if not providers.empty:
            identities = ", ".join(f"{row.provider}/{row.feed}" for row in providers.itertuples(index=False))
            snapshot = snapshot.model_copy(
                update={
                    "metadata": snapshot.metadata.model_copy(
                        update={
                            "data_mode": "strategy_provider_data",
                            "source_posture": f"Source-backed strategy bars: {identities}",
                        }
                    )
                }
            )
        snapshot_path = write_snapshot_atomic(snapshot, options.snapshot_path)
        report_path = write_strategy_research_report_atomic(snapshot, options.report_path)
        message = f"exported research snapshot schema v{snapshot.schema_version}"
        self._emit(emit, "progress", "export", 1.0, message)
        return StageOutcome(
            "completed",
            message,
            snapshot_path=snapshot_path,
            report_path=report_path,
        )

    def bind_settings(self, settings: Settings) -> StrategyPipeline:
        self._settings = settings
        return self

    def _registered(self, scope: StrategyScope) -> RegisteredStrategy:
        registered = self._registered_many(scope)
        if len(registered) != 1:
            raise ValueError("this strategy stage requires exactly one strategy ID")
        return registered[0]

    def _registered_many(self, scope: StrategyScope) -> tuple[RegisteredStrategy, ...]:
        resolved: list[RegisteredStrategy] = []
        for strategy_id in scope.strategy_ids:
            try:
                registered = self.registry.resolve(strategy_id)
            except KeyError as error:
                raise ValueError(str(error).strip("'")) from error
            if scope.interval not in registered.spec.intervals:
                raise ValueError(f"Strategy '{strategy_id}' is not registered for interval '{scope.interval.value}'")
            resolved.append(registered)
        return tuple(resolved)

    def _requested_coverage(self, scope: StrategyScope) -> tuple[BarQuery | None, DatasetManifest | None, str | None]:
        query, manifest, _evidence, unavailable = self._authenticated_coverage(scope)
        return query, manifest, unavailable

    def _capture_research_snapshot(self, scope: StrategyScope) -> tuple[SealedResearchSnapshot | None, str | None]:
        with _lock_for(_scope_bar_lock_identity(scope)):
            query, manifest, coverage_manifest, unavailable = self._authenticated_coverage(scope)
            if query is None or manifest is None or coverage_manifest is None:
                return None, unavailable
            as_of = self._query_as_of(query)
            signal_bars = self.bars.revision_ledger_as_of(query, as_of).copy(deep=True)
            causal_bars = self.bars.causal_bars_as_of(query, as_of).copy(deep=True)
            return (
                SealedResearchSnapshot(
                    query=query,
                    manifest=manifest,
                    coverage_manifest=coverage_manifest,
                    as_of=as_of,
                    signal_bars=signal_bars,
                    causal_bars=causal_bars,
                ),
                None,
            )

    def _source_snapshot_is_current(self, scope: StrategyScope, snapshot: SealedResearchSnapshot) -> bool:
        with _lock_for(_scope_bar_lock_identity(scope)):
            return self._source_snapshot_is_current_unlocked(scope, snapshot)

    def _source_snapshot_is_current_unlocked(
        self,
        scope: StrategyScope,
        snapshot: SealedResearchSnapshot,
    ) -> bool:
        current_query = self._local_query(scope)
        if (
            current_query is None
            or current_query.start != snapshot.query.start
            or current_query.end != snapshot.query.end
        ):
            return False
        return self.bars.manifest(current_query).dataset_hash == snapshot.manifest.dataset_hash

    def _authenticated_coverage(
        self, scope: StrategyScope
    ) -> tuple[BarQuery | None, DatasetManifest | None, EvaluationCoverageManifest | None, str | None]:
        query = self._local_query(scope)
        if query is None:
            return None, None, None, "requested coverage is unavailable; no finalized bars are stored"
        aggregate = self.bars.manifest(query)
        if not aggregate.strict_revision_as_of:
            return (
                None,
                None,
                None,
                "strict revision-as-of evidence is unavailable for backfilled provider history",
            )
        aggregate_missing = sum(gap.missing_bars for gap in aggregate.gaps)
        if aggregate_missing:
            return None, None, None, f"local compatible history is incomplete: {aggregate_missing} bars unavailable"
        frame = self.database.frame(
            "select coverage_request_id, requested_start, requested_end, requested_at, status, "
            "dataset_hash, row_count, gaps from dataset_coverage_requests "
            "where provider = :provider and feed = :feed and symbol = :symbol and interval = :interval "
            "order by requested_at desc, coverage_request_id desc",
            {
                "provider": scope.provider.value,
                "feed": scope.feed,
                "symbol": scope.symbol,
                "interval": scope.interval.value,
            },
        )
        if frame.empty:
            return None, None, None, "requested coverage is unavailable; run strategy ingest first"

        covered: list[tuple[datetime, datetime]] = []
        contributors: list[CoverageRequestEvidence] = []
        for row in frame.itertuples(index=False):
            requested_start = max(_utc_datetime(row.requested_start), query.start)
            requested_end = min(_utc_datetime(row.requested_end), query.end)
            if requested_end <= requested_start or _range_is_covered(requested_start, requested_end, covered):
                continue
            requested_query = BarQuery(
                provider=scope.provider.value,
                feed=scope.feed,
                symbol=scope.symbol,
                interval=scope.interval,
                start=_utc_datetime(row.requested_start),
                end=_utc_datetime(row.requested_end),
            )
            current = self.bars.manifest(requested_query)
            stored = row.gaps if isinstance(row.gaps, dict) else {}
            missing = sum(gap.missing_bars for gap in current.gaps)
            valid = (
                str(row.status) == "complete"
                and str(row.dataset_hash) == current.dataset_hash
                and int(row.row_count) == current.row_count
                and stored.get("calendar_id") == current.calendar_id
                and stored.get("calendar_version") == current.calendar_version
                and missing == 0
            )
            if not valid:
                return (
                    None,
                    None,
                    None,
                    "requested coverage is incomplete or stale; run strategy ingest to refresh every range",
                )
            covered = _merge_ranges((*covered, (requested_start, requested_end)))
            contributors.append(
                CoverageRequestEvidence(
                    coverage_request_id=str(row.coverage_request_id),
                    dataset_hash=current.dataset_hash,
                    requested_start=requested_query.start,
                    requested_end=requested_query.end,
                    requested_at=_utc_datetime(row.requested_at),
                    row_count=current.row_count,
                )
            )

        if not _range_is_covered(query.start, query.end, covered):
            return None, None, None, "requested coverage is incomplete; ingest the missing local history ranges"
        ordered_contributors = tuple(
            sorted(
                contributors,
                key=lambda item: (
                    item.requested_start,
                    item.requested_end,
                    item.requested_at,
                    item.coverage_request_id,
                ),
            )
        )
        evidence = EvaluationCoverageManifest(
            dataset_hash=aggregate.dataset_hash,
            provider=aggregate.provider,
            feed=aggregate.feed,
            symbol=aggregate.symbol,
            interval=aggregate.interval.value,
            requested_start=query.start,
            requested_end=query.end,
            coverage_start=aggregate.coverage_start,
            coverage_end=aggregate.coverage_end,
            row_count=aggregate.row_count,
            gaps=aggregate.gaps,
            calendar_id=aggregate.calendar_id,
            calendar_version=aggregate.calendar_version,
            contributing_requests=ordered_contributors,
        )
        return query, aggregate, evidence, None

    def _local_query(self, scope: StrategyScope) -> BarQuery | None:
        frame = self.database.frame(
            "select min(open_timestamp) as start, max(close_timestamp) as end "
            "from market_bars where provider = :provider and feed = :feed "
            "and symbol = :symbol and interval = :interval and finalized = true",
            {
                "provider": scope.provider.value,
                "feed": scope.feed,
                "symbol": scope.symbol,
                "interval": scope.interval.value,
            },
        )
        if frame.empty or pd.isna(frame.iloc[0]["start"]) or pd.isna(frame.iloc[0]["end"]):
            return None
        return BarQuery(
            provider=scope.provider.value,
            feed=scope.feed,
            symbol=scope.symbol,
            interval=scope.interval,
            start=_utc_datetime(frame.iloc[0]["start"]),
            end=_utc_datetime(frame.iloc[0]["end"]),
        )

    def _reserve_coverage_request(
        self,
        query: BarQuery,
        manifest: DatasetManifest,
        *,
        force: bool,
    ) -> str:
        parameters = {
            "provider": query.provider,
            "feed": query.feed,
            "symbol": query.symbol,
            "interval": query.interval.value,
            "start": query.start,
            "end": query.end,
        }
        lock_identity = canonical_hash(["dataset_coverage_reservation", parameters])
        with _lock_for(lock_identity):
            for _attempt in range(5):
                prior = int(
                    self.database.scalar(
                        "select count(*) from dataset_coverage_requests where provider = :provider and feed = :feed "
                        "and symbol = :symbol and interval = :interval and requested_start = :start "
                        "and requested_end = :end",
                        parameters,
                    )
                    or 0
                )
                requested_at = self._run_timestamp()
                if prior:
                    latest = self.database.scalar(
                        "select max(requested_at) from dataset_coverage_requests where provider = :provider "
                        "and feed = :feed and symbol = :symbol and interval = :interval "
                        "and requested_start = :start and requested_end = :end",
                        parameters,
                    )
                    latest_at = _utc_datetime(latest)
                    if requested_at <= latest_at:
                        requested_at = latest_at + timedelta(microseconds=1)
                identity = {
                    "provider": query.provider,
                    "feed": query.feed,
                    "symbol": query.symbol,
                    "interval": query.interval.value,
                    "requested_start": query.start,
                    "requested_end": query.end,
                    "requested_at": requested_at,
                }
                coverage_request_id = canonical_hash({**identity, "generation": prior + 1})
                try:
                    self.database.insert(
                        "dataset_coverage_requests",
                        [
                            {
                                "coverage_request_id": coverage_request_id,
                                **identity,
                                "force": force,
                                "status": "running",
                                "dataset_hash": manifest.dataset_hash,
                                "row_count": manifest.row_count,
                                "gaps": self._coverage_evidence(manifest),
                                "source": "strategy_pipeline_coverage",
                                "source_version": "2",
                                "created_at": requested_at,
                            }
                        ],
                    )
                except (IntegrityError, OperationalError) as error:
                    message = str(error).lower()
                    if "duplicate" not in message and "unique constraint" not in message:
                        raise
                    continue
                return coverage_request_id
        raise RuntimeError("could not reserve a unique dataset coverage request")

    def _finalize_coverage_request(
        self,
        coverage_request_id: str,
        manifest: DatasetManifest,
        status: Literal["complete", "incomplete", "unavailable"],
    ) -> None:
        with self.database.engine.begin() as connection:
            connection.execute(
                update(dataset_coverage_requests)
                .where(dataset_coverage_requests.c.coverage_request_id == coverage_request_id)
                .where(dataset_coverage_requests.c.status == "running")
                .values(
                    status=status,
                    dataset_hash=manifest.dataset_hash,
                    row_count=manifest.row_count,
                    gaps=self._coverage_evidence(manifest),
                    source_version="2",
                )
            )

    @staticmethod
    def _coverage_evidence(manifest: DatasetManifest) -> dict[str, Any]:
        return {
            "calendar_id": manifest.calendar_id,
            "calendar_version": manifest.calendar_version,
            "missing": [
                {
                    "start": gap.start.isoformat(),
                    "end": gap.end.isoformat(),
                    "missing_bars": gap.missing_bars,
                }
                for gap in manifest.gaps
            ],
        }

    def _query_as_of(self, query: BarQuery) -> datetime:
        value = self.database.scalar(
            "select max(available_at) from market_bars where provider = :provider and feed = :feed "
            "and symbol = :symbol and interval = :interval and open_timestamp >= :start and open_timestamp < :end",
            {
                "provider": query.provider,
                "feed": query.feed,
                "symbol": query.symbol,
                "interval": query.interval.value,
                "start": query.start,
                "end": query.end,
            },
        )
        return max(_utc_datetime(value), query.end)

    @staticmethod
    def _cache_key(scope: StrategyScope, registered: RegisteredStrategy, dataset_hash: str) -> tuple[str, ...]:
        return (
            dataset_hash,
            registered.spec.strategy_id,
            registered.spec.deterministic_version,
            scope.symbol,
            scope.interval.value,
            scope.mode.value,
        )

    def _cohort_payload(
        self,
        scope: StrategyScope,
        registered: Sequence[RegisteredStrategy],
        dataset_hash: str,
        as_of: datetime,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "dataset_hash": dataset_hash,
            "symbol": scope.symbol,
            "interval": scope.interval.value,
            "mode": scope.mode.value,
            "as_of": as_of,
            "members": [
                {
                    "strategy_id": item.spec.strategy_id,
                    "strategy_version": item.spec.deterministic_version,
                    "family": item.spec.family.value,
                }
                for item in sorted(
                    registered, key=lambda candidate: (candidate.spec.strategy_id, candidate.spec.deterministic_version)
                )
            ],
            "ensemble_policy": _ensemble_config_record(self.ensemble_config),
            "execution_policy": _execution_assumptions_record(self.execution_assumptions),
            "validation_policy": _validation_config_record(self.validation_config),
            "validation_policy_hash": validation_policy_hash(self.validation_config),
        }

    def _cached_cohort(
        self,
        scope: StrategyScope,
        registered: Sequence[RegisteredStrategy],
        dataset_hash: str,
        cohort_id: str,
    ) -> tuple[str, ...]:
        frame = self.database.frame(
            "select strategy_run_id, strategy_id, metrics from strategy_runs where dataset_hash = :dataset_hash "
            "and symbol = :symbol and interval = :interval and mode = :mode and status = 'evaluated' "
            "order by run_timestamp desc",
            {
                "dataset_hash": dataset_hash,
                "symbol": scope.symbol,
                "interval": scope.interval.value,
                "mode": scope.mode.value,
            },
        )
        expected = tuple(sorted(item.spec.strategy_id for item in registered))
        matches = frame[
            frame["metrics"].map(
                lambda value: (
                    isinstance(value, dict)
                    and value.get("cohort_id") == cohort_id
                    and tuple(sorted(item["strategy_id"] for item in value.get("cohort_members", []))) == expected
                )
            )
        ]
        if not matches.empty:
            latest_effective_at = matches.iloc[0]["metrics"].get("cohort_effective_at")
            matches = matches[
                matches["metrics"].map(
                    lambda value: isinstance(value, dict) and value.get("cohort_effective_at") == latest_effective_at
                )
            ]
        if len(matches) != len(expected) or tuple(sorted(matches["strategy_id"].astype(str))) != expected:
            return ()
        metrics = list(matches["metrics"])
        if (
            len({item.get("cohort_decision_hash") for item in metrics}) != 1
            or len({item.get("cohort_effective_at") for item in metrics}) != 1
        ):
            return ()
        by_strategy = {str(row.strategy_id): str(row.strategy_run_id) for row in matches.itertuples(index=False)}
        return tuple(by_strategy[item.spec.strategy_id] for item in registered)

    def _cohort_run_timestamp(
        self,
        scope: StrategyScope,
        registered: Sequence[RegisteredStrategy],
        dataset_hash: str,
    ) -> datetime:
        timestamp = self._run_timestamp()
        latest = [self._latest_exact_run_timestamp(scope, item, dataset_hash) for item in registered]
        observed = [item for item in latest if item is not None]
        if observed and timestamp <= max(observed):
            timestamp = max(observed) + timedelta(microseconds=1)
        return timestamp

    def _exact_run_count(self, scope: StrategyScope, registered: RegisteredStrategy, dataset_hash: str) -> int:
        count = self.database.scalar(
            "select count(*) from strategy_runs where dataset_hash = :dataset_hash "
            "and strategy_id = :strategy_id and strategy_version = :strategy_version "
            "and symbol = :symbol and interval = :interval and mode = :mode",
            dict(
                zip(
                    ("dataset_hash", "strategy_id", "strategy_version", "symbol", "interval", "mode"),
                    self._cache_key(scope, registered, dataset_hash),
                    strict=True,
                )
            ),
        )
        return int(count or 0)

    def _latest_exact_run_timestamp(
        self, scope: StrategyScope, registered: RegisteredStrategy, dataset_hash: str
    ) -> datetime | None:
        value = self.database.scalar(
            "select max(run_timestamp) from strategy_runs where dataset_hash = :dataset_hash "
            "and strategy_id = :strategy_id and strategy_version = :strategy_version "
            "and symbol = :symbol and interval = :interval and mode = :mode",
            dict(
                zip(
                    ("dataset_hash", "strategy_id", "strategy_version", "symbol", "interval", "mode"),
                    self._cache_key(scope, registered, dataset_hash),
                    strict=True,
                )
            ),
        )
        return None if value is None else _utc_datetime(value)

    def _reserve_run(
        self,
        scope: StrategyScope,
        registered: RegisteredStrategy,
        dataset_hash: str,
        *,
        preferred_timestamp: datetime | None = None,
        reservation_metrics: Mapping[str, Any] | None = None,
    ) -> tuple[str, datetime]:
        cache_key = self._cache_key(scope, registered, dataset_hash)
        lock_identity = canonical_hash(["strategy_evaluation_reservation", *cache_key])
        with _lock_for(lock_identity):
            for _attempt in range(5):
                run_generation = self._exact_run_count(scope, registered, dataset_hash) + 1
                run_timestamp = preferred_timestamp or self._run_timestamp()
                latest_timestamp = self._latest_exact_run_timestamp(scope, registered, dataset_hash)
                if latest_timestamp is not None and run_timestamp <= latest_timestamp:
                    run_timestamp = latest_timestamp + timedelta(microseconds=1)
                run_id = canonical_hash(
                    {
                        "cache_key": cache_key,
                        "run_timestamp": run_timestamp,
                        "run_generation": run_generation,
                    }
                )
                row = self._strategy_run_row(
                    scope,
                    registered,
                    dataset_hash,
                    run_id,
                    run_timestamp,
                    status="running",
                    metrics={
                        "reservation_generation": run_generation,
                        "state": scope.mode.value,
                        **dict(reservation_metrics or {}),
                    },
                    ended_at=None,
                )
                try:
                    self.database.insert("strategy_runs", [row])
                except IntegrityError:
                    continue
                return run_id, run_timestamp
        raise RuntimeError("could not reserve a unique strategy evaluation run")

    def _evaluate_engines(
        self,
        scope: StrategyScope,
        registered: Sequence[RegisteredStrategy],
        query: BarQuery,
        manifest: DatasetManifest,
        as_of: datetime,
        *,
        signal_bars: pd.DataFrame,
        bars: pd.DataFrame,
    ) -> EvaluationBatch:
        del query
        signal_bars = signal_bars.copy(deep=True)
        bars = bars.copy(deep=True)
        if len(bars) < max(max(item.spec.warmup_bars for item in registered) + 2, 3):
            raise ValueError("insufficient locally available compatible history")
        chronology, outcomes, boundary = self._raw_validation_context(bars)
        if len(chronology) < 3:
            raise ValueError("insufficient causal execution history")
        validation_data = pd.DataFrame({"decision_timestamp": chronology, "outcome_available_at": outcomes})
        folds = make_outer_folds(validation_data, boundary=boundary, config=self.validation_config)
        periods = _periods_per_year(scope.interval)
        raw_components: list[tuple[RegisteredStrategy, pd.DataFrame, Any, dict[str, Any]]] = []
        runs: dict[str, StrategyRunEvidence] = {}
        strategy_context = StrategyContext.for_market(scope.provider.value, scope.feed)
        for item in registered:
            signals = item.generator(item.spec, signal_bars, strategy_context)
            signals = self._signal_decision_hashes(scope, manifest, item, signals)
            prefix_size = max(item.spec.warmup_bars, int(len(bars) * 0.8))
            prefix_size = min(prefix_size, len(bars) - 1)
            audit = audit_prefix_invariance(
                item.spec,
                signal_bars.iloc[:prefix_size].copy(),
                signal_bars.copy(),
                strategy_context,
                strategy_context,
                generator=item.generator,
            )
            backtest = run_intraday_backtest(
                bars,
                signals,
                self.execution_assumptions,
                RiskLimits(initial_cash=100_000, periods_per_year=periods),
                strategy_id=item.spec.strategy_id,
                symbol=scope.symbol,
            )
            fold_evidence = tuple(
                self._fold_evidence(index, fold, chronology, signals, backtest.equity_curve, periods)
                for index, fold in enumerate(folds)
            )
            runs[item.spec.strategy_id] = StrategyRunEvidence(
                backtest=backtest,
                signals=signals,
                trial_evidence=self._trial_evidence(manifest, scope, boundary.final_start),
                fold_evidence=fold_evidence,
                causal_audit_passed=audit.passed,
            )
            raw_components.append((item, signals, backtest, asdict(audit)))
        evaluations = evaluate_registry(
            EvaluationRequest(
                registry=_selected_registry(registered),
                runs=runs,
                chronology=chronology,
                outcome_availability=outcomes,
                as_of=as_of,
                mode=scope.mode,
                dataset_hash=manifest.dataset_hash,
                symbol=scope.symbol,
                interval=scope.interval,
                config=self.validation_config,
            )
        )
        resolved_outcomes = self._resolved_outcomes(bars, raw_components, evaluations, boundary)
        ensemble_decision = generate_current_decision(
            evaluations,
            resolved_outcomes,
            as_of,
            config=self.ensemble_config,
            validation_config=self.validation_config,
            database=None,
        )
        by_strategy = {item.strategy_id: item for item in evaluations}
        components = tuple(
            ComponentEvaluation(
                item,
                by_strategy[item.spec.strategy_id],
                signals,
                backtest,
                audit_details,
            )
            for item, signals, backtest, audit_details in raw_components
        )
        return EvaluationBatch(components, ensemble_decision, resolved_outcomes)

    @staticmethod
    def _signal_decision_hashes(
        scope: StrategyScope,
        manifest: DatasetManifest,
        registered: RegisteredStrategy,
        signals: pd.DataFrame,
    ) -> pd.DataFrame:
        result = signals.copy()
        result["decision_hash"] = [
            canonical_hash(
                {
                    "dataset_hash": manifest.dataset_hash,
                    "strategy_id": registered.spec.strategy_id,
                    "strategy_version": registered.spec.deterministic_version,
                    "symbol": scope.symbol,
                    "interval": scope.interval.value,
                    "mode": scope.mode.value,
                    "decision_timestamp": _utc_datetime(row.decision_timestamp),
                    "data_through": _utc_datetime(row.data_through),
                    "signal": int(row.signal),
                    "strength": float(row.strength),
                }
            )
            for row in result.itertuples(index=False)
        ]
        return result

    def _resolved_outcomes(
        self,
        bars: pd.DataFrame,
        components: Sequence[tuple[RegisteredStrategy, pd.DataFrame, Any, dict[str, Any]]],
        evaluations: Sequence[StrategyEvaluation],
        boundary: FinalBoundary,
    ) -> pd.DataFrame:
        ordered = bars.sort_values("open_timestamp", kind="stable").reset_index(drop=True).copy()
        ordered["open_timestamp"] = pd.to_datetime(ordered["open_timestamp"], utc=True)
        ordered["close_timestamp"] = pd.to_datetime(ordered["close_timestamp"], utc=True)
        ordered["available_at"] = pd.to_datetime(ordered["available_at"], utc=True)
        by_strategy = {item.strategy_id: item for item in evaluations}
        rows: list[dict[str, Any]] = []
        for registered, _signals, backtest, _audit in components:
            evaluation = by_strategy[registered.spec.strategy_id]
            fills = backtest.trade_ledger.copy()
            if fills.empty:
                continue
            fills["decision_timestamp"] = pd.to_datetime(fills["decision_timestamp"], utc=True)
            fills["execution_timestamp"] = pd.to_datetime(fills["execution_timestamp"], utc=True)
            fills = fills.sort_values(["execution_timestamp", "order_id"], kind="stable")
            for fill in fills.itertuples(index=False):
                sources = [
                    source
                    for source in fill.source_decisions
                    if str(source.get("strategy_id")) == registered.spec.strategy_id
                ]
                if not sources:
                    continue
                source = max(
                    sources,
                    key=lambda item: (pd.Timestamp(item["decision_timestamp"]), str(item["decision_hash"])),
                )
                execution_timestamp = pd.Timestamp(fill.execution_timestamp)
                matching = ordered.loc[ordered["open_timestamp"] == execution_timestamp]
                if matching.empty:
                    matching = ordered.loc[ordered["close_timestamp"] == execution_timestamp]
                if matching.empty:
                    raise ValueError("Task 4 execution does not map to one causal finalized bar")
                bar = matching.iloc[0]
                outcome_available_at = pd.Timestamp(bar["available_at"])
                if execution_timestamp >= boundary.final_start or outcome_available_at >= boundary.final_start:
                    continue
                decision = pd.Timestamp(source["decision_timestamp"])
                notional = abs(float(fill.notional))
                cost = float(fill.total_cost) / notional if notional > 0 else 0.0
                rows.append(
                    {
                        "strategy_id": registered.spec.strategy_id,
                        "decision_timestamp": decision,
                        "execution_timestamp": execution_timestamp,
                        "outcome_available_at": outcome_available_at,
                        "signal": int(source["signal"]),
                        "realized_return": float(float(bar["close"]) / float(bar["open"]) - 1),
                        "cost": cost,
                        "source_decision_hash": str(source["decision_hash"]),
                        "source_execution_hash": _strategy_execution_id(
                            evaluation.dataset_hash,
                            registered.spec.strategy_id,
                            evaluation.strategy_version,
                            evaluation.symbol,
                            evaluation.interval,
                            evaluation.mode,
                            _utc_datetime(fill.decision_timestamp),
                            _utc_datetime(fill.execution_timestamp),
                        ),
                        "dataset_hash": evaluation.dataset_hash,
                        "strategy_version": evaluation.strategy_version,
                        "symbol": evaluation.symbol,
                        "interval": evaluation.interval.value,
                        "mode": evaluation.mode.value,
                    }
                )
        if not rows:
            return pd.DataFrame({name: pd.Series(dtype=dtype) for name, dtype in _RESOLVED_OUTCOME_DTYPES})
        return pd.DataFrame(rows)

    def _raw_validation_context(self, bars: pd.DataFrame) -> tuple[pd.Series, pd.Series, FinalBoundary]:
        ordered = bars.sort_values("open_timestamp", kind="stable").reset_index(drop=True)
        chronology = pd.to_datetime(ordered["close_timestamp"].iloc[:-1], utc=True).reset_index(drop=True)
        outcomes = pd.to_datetime(ordered["available_at"].iloc[1:], utc=True).reset_index(drop=True)
        boundary = select_final_boundary(
            chronology,
            final_test_fraction=self.validation_config.final_test_fraction,
        )
        return chronology, outcomes, boundary

    def _raw_final_boundary(self, bars: pd.DataFrame) -> FinalBoundary:
        return self._raw_validation_context(bars)[2]

    def _fold_evidence(
        self,
        index: int,
        fold: Any,
        chronology: pd.Series,
        signals: pd.DataFrame,
        curve: pd.DataFrame,
        periods: int,
    ) -> FoldEvidence:
        start = chronology.iloc[fold.validation_index[0]].to_pydatetime()
        end = chronology.iloc[fold.validation_index[-1]].to_pydatetime()
        decisions = pd.to_datetime(curve["decision_timestamp"], utc=True, errors="coerce")
        expected_decisions = chronology.iloc[list(fold.validation_index)]
        selected = curve.loc[decisions.isin(expected_decisions)]
        if len(selected) != len(expected_decisions):
            raise ValueError("walk-forward decisions do not map one-to-one to outcome rows")
        returns = pd.to_numeric(selected["net_return"], errors="coerce").dropna()
        deviation = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
        sharpe = float(returns.mean() / deviation * math.sqrt(periods)) if deviation > 0 else 0.0
        evaluated_at = pd.to_datetime(selected["outcome_available_at"], utc=True).max().to_pydatetime()
        calibration_error = calculate_fold_calibration_error(signals, selected, expected_decisions)
        return FoldEvidence(index, start, end, evaluated_at, sharpe, calibration_error)

    def _trial_evidence(
        self, manifest: DatasetManifest, scope: StrategyScope, final_start: pd.Timestamp
    ) -> tuple[TrialEvidence, ...]:
        frame = self.database.frame(
            "select trial_id, evaluated_at, candidate from learning_trials "
            "where dataset_hash = :dataset_hash and symbol = :symbol and interval = :interval "
            "and status = 'succeeded' order by evaluated_at, trial_id",
            {
                "dataset_hash": manifest.dataset_hash,
                "symbol": scope.symbol,
                "interval": scope.interval.value,
            },
        )
        evidence: list[TrialEvidence] = []
        for row in frame.itertuples(index=False):
            evaluated_at = _utc_datetime(row.evaluated_at)
            if evaluated_at >= final_start.to_pydatetime():
                continue
            payload = row.candidate if isinstance(row.candidate, dict) else {}
            fold_metrics = payload.get("fold_metrics") if isinstance(payload, dict) else None
            sharpes = [
                float(item["net_sharpe"])
                for item in fold_metrics or []
                if isinstance(item, dict) and _finite(item.get("net_sharpe")) is not None
            ]
            if not sharpes:
                continue
            evidence.append(
                TrialEvidence(
                    str(row.trial_id),
                    float(np.median(sharpes)),
                    evaluated_at,
                    evaluated_at,
                )
            )
        return tuple(evidence)

    def _persist_evaluation_batch_if_current(
        self,
        scope: StrategyScope,
        snapshot: SealedResearchSnapshot,
        run_contexts: Sequence[tuple[RegisteredStrategy, str, datetime]],
        batch: EvaluationBatch,
        cohort: Mapping[str, Any],
        cohort_id: str,
        cohort_generation: int,
    ) -> bool:
        with _lock_for(_scope_bar_lock_identity(scope)):
            if not self._source_snapshot_is_current_unlocked(scope, snapshot):
                return False
            self._persist_evaluation_batch(
                scope,
                snapshot.manifest,
                run_contexts,
                batch,
                cohort,
                cohort_id,
                cohort_generation,
            )
        return True

    def _persist_evaluation_batch(
        self,
        scope: StrategyScope,
        manifest: DatasetManifest,
        run_contexts: Sequence[tuple[RegisteredStrategy, str, datetime]],
        batch: EvaluationBatch,
        cohort: Mapping[str, Any],
        cohort_id: str,
        cohort_generation: int,
    ) -> None:
        components = {item.registered.spec.strategy_id: item for item in batch.components}
        reservations = {
            registered.spec.strategy_id: (run_id, run_timestamp) for registered, run_id, run_timestamp in run_contexts
        }
        cohort_effective_at = max(run_timestamp for _, _, run_timestamp in run_contexts)
        cohort_metrics = {
            "cohort_id": cohort_id,
            "cohort_generation": cohort_generation,
            "cohort_members": cohort["members"],
            "cohort_decision_hash": batch.ensemble_decision.decision_hash,
            "cohort_effective_at": cohort_effective_at.isoformat(),
            "ensemble_policy_hash": canonical_hash(cohort["ensemble_policy"]),
            "execution_policy": cohort["execution_policy"],
            "execution_policy_hash": canonical_hash(cohort["execution_policy"]),
            "validation_policy_hash": cohort["validation_policy_hash"],
            "coverage_manifest": cohort["coverage_manifest"],
        }
        outcome_records = [
            {
                "strategy_id": str(row.strategy_id),
                "decision_timestamp": pd.Timestamp(row.decision_timestamp).isoformat(),
                "execution_timestamp": pd.Timestamp(row.execution_timestamp).isoformat(),
                "outcome_available_at": pd.Timestamp(row.outcome_available_at).isoformat(),
                "source_decision_hash": str(row.source_decision_hash),
                "source_execution_hash": str(row.source_execution_hash),
            }
            for row in batch.resolved_outcomes.sort_values(
                ["outcome_available_at", "execution_timestamp", "strategy_id"], kind="stable"
            ).itertuples(index=False)
        ]
        outcome_provenance = {
            "record_count": len(outcome_records),
            "records": outcome_records,
            "records_hash": canonical_hash(outcome_records),
            "feedback_status": "observed_outcomes" if outcome_records else "no_observed_outcomes",
        }
        with _lock_for("strategy_evaluation_persistence"), self.database.engine.begin() as connection:
            for registered, run_id, run_timestamp in run_contexts:
                component = components[registered.spec.strategy_id]
                self._persist_evaluation(
                    connection,
                    scope,
                    registered,
                    manifest,
                    run_id,
                    run_timestamp,
                    component.evaluation,
                    component.signals,
                    component.backtest,
                    component.audit_details,
                    cohort_metrics,
                )
            weight_rows = []
            for row in evidence_weight_rows(
                batch.ensemble_decision.weights,
                batch.ensemble_decision,
            ):
                run_id, _run_timestamp = reservations[str(row["strategy_id"])]
                natural = {
                    "dataset_hash": row["dataset_hash"],
                    "strategy_id": row["strategy_id"],
                    "strategy_version": row["strategy_version"],
                    "symbol": row["symbol"],
                    "interval": row["interval"],
                    "mode": row["mode"],
                    "effective_at": cohort_effective_at,
                }
                weight_rows.append(
                    {
                        **row,
                        "evidence": {
                            **row["evidence"],
                            **cohort_metrics,
                            "ensemble_config": cohort["ensemble_policy"],
                            "validation_policy": cohort["validation_policy"],
                            "resolved_outcome_provenance": outcome_provenance,
                        },
                        "weight_id": canonical_hash(natural),
                        "strategy_run_id": run_id,
                        "effective_at": cohort_effective_at,
                        "created_at": cohort_effective_at,
                    }
                )
            self._insert_missing(connection, "ensemble_weights", weight_rows)

    def _evaluation_cohort_is_complete(
        self,
        scope: StrategyScope,
        dataset_hash: str,
        run_contexts: Sequence[tuple[RegisteredStrategy, str, datetime]],
        batch: EvaluationBatch,
        cohort_id: str,
    ) -> bool:
        run_ids = tuple(run_id for _registered, run_id, _timestamp in run_contexts)
        components = {item.registered.spec.strategy_id: item for item in batch.components}
        runs = self.database.frame(
            "select strategy_run_id, strategy_id, status, metrics from strategy_runs "
            "where dataset_hash = :dataset_hash",
            {"dataset_hash": dataset_hash},
        )
        selected = runs.loc[runs["strategy_run_id"].isin(run_ids)] if not runs.empty else runs
        if len(selected) != len(run_ids) or set(selected["status"]) != {"evaluated"}:
            return False
        for registered, run_id, _run_timestamp in run_contexts:
            run = selected.loc[selected["strategy_run_id"] == run_id]
            if len(run) != 1:
                return False
            metrics = run.iloc[0]["metrics"]
            if (
                not isinstance(metrics, dict)
                or metrics.get("cohort_id") != cohort_id
                or metrics.get("cohort_decision_hash") != batch.ensemble_decision.decision_hash
            ):
                return False
            component = components.get(registered.spec.strategy_id)
            if component is None:
                return False
            signal_count = int(
                self.database.scalar(
                    "select count(*) from strategy_run_signal_links l "
                    "join strategy_signals s on s.strategy_signal_id = l.strategy_signal_id "
                    "where l.strategy_run_id = :run_id",
                    {"run_id": run_id},
                )
                or 0
            )
            execution_count = int(
                self.database.scalar(
                    "select count(*) from strategy_run_execution_links l "
                    "join strategy_executions e on e.execution_id = l.execution_id "
                    "where l.strategy_run_id = :run_id",
                    {"run_id": run_id},
                )
                or 0
            )
            if signal_count != len(component.signals) or execution_count != len(component.backtest.trade_ledger):
                return False
            weights = self.database.frame(
                "select evidence from ensemble_weights where strategy_run_id = :run_id",
                {"run_id": run_id},
            )
            if len(weights) != 1:
                return False
            evidence = weights.iloc[0]["evidence"]
            if (
                not isinstance(evidence, dict)
                or evidence.get("cohort_id") != cohort_id
                or evidence.get("cohort_decision_hash") != batch.ensemble_decision.decision_hash
            ):
                return False
            audit_id = canonical_hash(["prefix_invariance", *self._cache_key(scope, registered, dataset_hash)])
            if not self.database.scalar(
                "select count(*) from causal_audits where audit_id = :audit_id",
                {"audit_id": audit_id},
            ):
                return False
        return True

    def _persist_evaluation(
        self,
        connection: Any,
        scope: StrategyScope,
        registered: RegisteredStrategy,
        manifest: DatasetManifest,
        run_id: str,
        run_timestamp: datetime,
        evaluation: StrategyEvaluation,
        signals: pd.DataFrame,
        backtest: Any,
        audit_details: dict[str, Any],
        cohort_metrics: Mapping[str, Any],
    ) -> None:
        created_at = max(self._run_timestamp(), run_timestamp)
        metrics = {**_evaluation_metrics(evaluation), **cohort_metrics}
        connection.execute(
            update(strategy_runs)
            .where(strategy_runs.c.strategy_run_id == run_id)
            .where(strategy_runs.c.status == "running")
            .values(
                status=evaluation.status.value,
                metrics=metrics,
                ended_at=created_at,
                source="strategy_pipeline",
                source_version="2",
            )
        )
        signal_rows = []
        for row in signals.itertuples(index=False):
            decision = _utc_datetime(row.decision_timestamp)
            signal_rows.append(
                {
                    "strategy_signal_id": canonical_hash([run_id, decision]),
                    "strategy_run_id": run_id,
                    "dataset_hash": manifest.dataset_hash,
                    "strategy_id": registered.spec.strategy_id,
                    "strategy_version": registered.spec.deterministic_version,
                    "symbol": scope.symbol,
                    "interval": scope.interval.value,
                    "mode": scope.mode.value,
                    "decision_timestamp": decision,
                    "data_through_timestamp": _utc_datetime(row.data_through),
                    "executable_at": decision,
                    "signal": int(row.signal),
                    "strength": float(row.strength),
                    "reason": str(row.reason),
                    "source": "registered_strategy_engine",
                    "source_version": registered.spec.deterministic_version,
                    "created_at": created_at,
                }
            )
        self._insert_missing(connection, "strategy_signals", signal_rows)
        signal_ids = [
            self._persisted_evidence_id(connection, "strategy_signals", "strategy_signal_id", row)
            for row in signal_rows
        ]
        self._insert_missing(
            connection,
            "strategy_run_signal_links",
            [
                {
                    "run_signal_link_id": canonical_hash([run_id, signal_id]),
                    "strategy_run_id": run_id,
                    "strategy_signal_id": signal_id,
                    "source": "strategy_pipeline_run_evidence",
                    "source_version": "1",
                    "created_at": created_at,
                }
                for signal_id in signal_ids
            ],
        )
        execution_rows = []
        for row in backtest.trade_ledger.itertuples(index=False):
            decision = _utc_datetime(row.decision_timestamp)
            executed = _utc_datetime(row.execution_timestamp)
            execution_rows.append(
                {
                    "execution_id": _strategy_execution_id(
                        manifest.dataset_hash,
                        registered.spec.strategy_id,
                        registered.spec.deterministic_version,
                        scope.symbol,
                        scope.interval,
                        scope.mode,
                        decision,
                        executed,
                    ),
                    "strategy_run_id": run_id,
                    "dataset_hash": manifest.dataset_hash,
                    "strategy_id": registered.spec.strategy_id,
                    "strategy_version": registered.spec.deterministic_version,
                    "symbol": scope.symbol,
                    "interval": scope.interval.value,
                    "mode": scope.mode.value,
                    "decision_timestamp": decision,
                    "execution_timestamp": executed,
                    "side": str(row.side),
                    "quantity": float(row.quantity),
                    "fill_price": _finite(row.price),
                    "fees": float(row.total_cost),
                    "status": str(row.status),
                    "reason": str(row.fill_reason),
                    "source": "task4_event_driven_execution",
                    "source_version": "1",
                    "created_at": created_at,
                }
            )
        self._insert_missing(connection, "strategy_executions", execution_rows)
        execution_ids = [
            self._persisted_evidence_id(connection, "strategy_executions", "execution_id", row)
            for row in execution_rows
        ]
        self._insert_missing(
            connection,
            "strategy_run_execution_links",
            [
                {
                    "run_execution_link_id": canonical_hash([run_id, execution_id]),
                    "strategy_run_id": run_id,
                    "execution_id": execution_id,
                    "source": "strategy_pipeline_run_evidence",
                    "source_version": "1",
                    "created_at": created_at,
                }
                for execution_id in execution_ids
            ],
        )
        audit_id = canonical_hash(["prefix_invariance", *self._cache_key(scope, registered, manifest.dataset_hash)])
        self._insert_missing(
            connection,
            "causal_audits",
            [
                {
                    "audit_id": audit_id,
                    "dataset_hash": manifest.dataset_hash,
                    "strategy_id": registered.spec.strategy_id,
                    "strategy_version": registered.spec.deterministic_version,
                    "symbol": scope.symbol,
                    "interval": scope.interval.value,
                    "mode": scope.mode.value,
                    "audited_at": manifest.coverage_end or created_at,
                    "passed": evaluation.causal_audit_passed,
                    "details": audit_details,
                    "source": "prefix_invariance",
                    "source_version": "1",
                    "created_at": created_at,
                }
            ],
        )

    @staticmethod
    def _insert_missing(connection: Any, table_name: str, rows: Sequence[Mapping[str, Any]]) -> None:
        table = TABLES[table_name]
        keys = NATURAL_KEYS[table_name]
        for row in rows:
            conditions = [table.c[key] == row[key] for key in keys]
            exists = connection.execute(select(table.c[keys[0]]).where(*conditions)).first()
            if exists is None:
                connection.execute(insert(table).values(**row))

    @staticmethod
    def _persisted_evidence_id(
        connection: Any,
        table_name: str,
        id_column: str,
        row: Mapping[str, Any],
    ) -> str:
        table = TABLES[table_name]
        conditions = [table.c[key] == row[key] for key in NATURAL_KEYS[table_name]]
        value = connection.execute(select(table.c[id_column]).where(*conditions)).scalar_one()
        return str(value)

    def _strategy_run_row(
        self,
        scope: StrategyScope,
        registered: RegisteredStrategy,
        dataset_hash: str,
        run_id: str,
        run_timestamp: datetime,
        *,
        status: str,
        metrics: Mapping[str, Any],
        ended_at: datetime | None,
    ) -> dict[str, Any]:
        return {
            "strategy_run_id": run_id,
            "dataset_hash": dataset_hash,
            "strategy_id": registered.spec.strategy_id,
            "strategy_version": registered.spec.deterministic_version,
            "family": registered.spec.family.value,
            "symbol": scope.symbol,
            "interval": scope.interval.value,
            "mode": scope.mode.value,
            "run_timestamp": run_timestamp,
            "parameters": dict(registered.spec.parameters),
            "status": status,
            "metrics": dict(metrics),
            "started_at": run_timestamp,
            "ended_at": ended_at,
            "source": "strategy_pipeline",
            "source_version": "2",
            "created_at": run_timestamp,
        }

    def _persist_failed_run(
        self,
        scope: StrategyScope,
        registered: RegisteredStrategy,
        dataset_hash: str,
        run_id: str,
        run_timestamp: datetime,
        reason: str,
        *,
        stage: str | None = None,
        initial_metrics: Mapping[str, Any] | None = None,
    ) -> None:
        ended_at = max(self._run_timestamp(), run_timestamp)
        failure_metrics = {**dict(initial_metrics or {}), "error_summary": reason}
        if stage is not None:
            failure_metrics[f"{stage}_error"] = reason
        with self.database.engine.begin() as connection:
            existing = connection.execute(
                select(strategy_runs.c.strategy_run_id, strategy_runs.c.status, strategy_runs.c.metrics).where(
                    strategy_runs.c.strategy_run_id == run_id
                )
            ).one_or_none()
            if existing is None:
                connection.execute(
                    insert(strategy_runs).values(
                        **self._strategy_run_row(
                            scope,
                            registered,
                            dataset_hash,
                            run_id,
                            run_timestamp,
                            status="failed",
                            metrics=failure_metrics,
                            ended_at=ended_at,
                        )
                    )
                )
            elif existing.status == "running":
                prior_metrics = existing.metrics if isinstance(existing.metrics, dict) else {}
                connection.execute(
                    update(strategy_runs)
                    .where(strategy_runs.c.strategy_run_id == run_id)
                    .where(strategy_runs.c.status == "running")
                    .values(
                        status="failed",
                        metrics={**prior_metrics, **failure_metrics},
                        ended_at=ended_at,
                    )
                )

    def _learning_experiment(
        self,
        options: LearningOptions,
        registered: RegisteredStrategy,
        manifest: DatasetManifest,
        bars: pd.DataFrame,
        outer_boundary: FinalBoundary,
    ) -> tuple[LearningExperiment, pd.DataFrame]:
        frame = bars.sort_values("open_timestamp", kind="stable").reset_index(drop=True).copy()
        frame["decision_timestamp"] = pd.to_datetime(frame["close_timestamp"], utc=True)
        frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True)
        frame["outcome_available_at"] = frame["available_at"].shift(-1)
        frame["forward_return"] = pd.to_numeric(frame["close"], errors="coerce").pct_change().shift(-1)
        frame["rsi"] = rsi(frame["close"], min(14, max(2, registered.spec.warmup_bars)))
        frame["volume_zscore"] = rolling_zscore(frame["volume"], min(20, max(3, registered.spec.warmup_bars)))
        frame = frame.dropna(subset=["rsi", "volume_zscore", "forward_return", "outcome_available_at"]).reset_index(
            drop=True
        )
        minimum = self.validation_config.minimum_train_observations + self.validation_config.validation_observations + 3
        if len(frame) < minimum:
            raise ValueError("insufficient finalized development history for nested learning validation")
        final_start = outer_boundary.final_start.to_pydatetime()
        outer_development = (
            frame.loc[frame["decision_timestamp"] < outer_boundary.final_start].copy().reset_index(drop=True)
        )
        if len(outer_development) < 3:
            raise ValueError("outer development block is too small")
        development = outer_development.iloc[:-1].copy().reset_index(drop=True)
        as_of = _utc_datetime(outer_development.iloc[-1]["decision_timestamp"])
        nested_boundary = select_final_boundary(
            development["decision_timestamp"], final_test_fraction=self.validation_config.final_test_fraction
        )
        nested_data = development[["decision_timestamp", "outcome_available_at"]]
        nested = make_outer_folds(nested_data, boundary=nested_boundary, config=self.validation_config)
        inner_folds = tuple(WalkForwardFold(fold.train_index, fold.validation_index) for fold in nested)
        if not inner_folds:
            raise ValueError("nested learning validation produced no chronological inner folds")
        key = {
            "dataset_hash": manifest.dataset_hash,
            "strategy_id": registered.spec.strategy_id,
            "strategy_version": registered.spec.deterministic_version,
            "symbol": options.scope.symbol,
            "interval": options.scope.interval.value,
            "mode": StrategyMode.WALK_FORWARD_LEARNING.value,
            "seed": options.seed,
            "evaluation_budget": options.evaluation_budget,
        }
        base_id = f"learn-{canonical_hash(key)[:24]}"
        learning_run_id = base_id
        if options.force:
            prior = int(
                self.database.scalar(
                    "select count(distinct learning_run_id) from learning_trials where learning_run_id like :prefix",
                    {"prefix": f"{base_id}%"},
                )
                or 0
            )
            learning_run_id = f"{base_id}-force-{prior + 1}"
        experiment = LearningExperiment(
            learning_run_id=learning_run_id,
            dataset_hash=manifest.dataset_hash,
            symbol=options.scope.symbol,
            interval=options.scope.interval,
            started_at=self._run_timestamp(),
            as_of=as_of,
            development_data_through=as_of,
            sealed_final_start=final_start,
            seed=options.seed,
            evaluation_budget=options.evaluation_budget,
            inner_folds=inner_folds,
            indicators=("rsi", "volume_zscore"),
            thresholds=(-1.0, 0.0, 1.0, 30.0, 50.0, 70.0),
            database=self.database,
            execution_assumptions=self.execution_assumptions,
            risk_limits=RiskLimits(
                initial_cash=100_000,
                periods_per_year=_periods_per_year(options.scope.interval),
            ),
        )
        return experiment, development

    def _run_timestamp(self) -> datetime:
        value = self.clock()
        if value.tzinfo is not UTC:
            raise ValueError("pipeline clock must return an explicit UTC datetime")
        return value

    @staticmethod
    def _emit(
        emit: EventSink | None,
        event: Literal["started", "progress", "complete", "error"],
        stage: str,
        progress: float,
        message: str,
    ) -> None:
        if emit is not None:
            emit(PipelineEvent(event=event, stage=stage, progress=progress, message=message))


def create_strategy_pipeline(
    settings: Settings,
    database: Database,
    *,
    csv_path: Path | None = None,
    http_client: httpx.Client | None = None,
) -> StrategyPipeline:
    registry = build_strategy_registry(settings.strategies.enabled)
    client = http_client or httpx.Client(timeout=30)
    providers: dict[BarProviderName, BarProvider] = {
        BarProviderName.BINANCE: BinanceBarProvider(client),
    }
    unavailable: dict[BarProviderName, str] = {}
    if csv_path is not None:
        providers[BarProviderName.CSV] = CSVBarProvider(csv_path)
    else:
        unavailable[BarProviderName.CSV] = "--csv-path is required"
    try:
        providers[BarProviderName.ALPACA] = AlpacaBarProvider(client)
    except ValueError:
        unavailable[BarProviderName.ALPACA] = "Alpaca credentials are unavailable"
    family_caps = settings.strategies.family_weight_caps
    ensemble_config = EnsembleConfig(
        maximum_strategy_weight=settings.strategies.strategy_weight_cap,
        maximum_family_weight=max(family_caps.values(), default=DEFAULT_ENSEMBLE_CONFIG.maximum_family_weight),
        family_weight_caps=family_caps,
    )
    return StrategyPipeline(
        database,
        registry,
        providers,
        provider_unavailable=unavailable,
        ensemble_config=ensemble_config,
    ).bind_settings(settings)


def _selected_registry(registered: Sequence[RegisteredStrategy]) -> StrategyRegistry:
    registry = StrategyRegistry()
    for item in registered:
        registry.register(item.spec, item.generator, item.metadata)
    return registry


def _utc_datetime(value: Any) -> datetime:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError("timestamp is unavailable")
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC").to_pydatetime().replace(tzinfo=UTC)


def _merge_ranges(ranges: Sequence[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(ranges):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _range_is_covered(start: datetime, end: datetime, ranges: Sequence[tuple[datetime, datetime]]) -> bool:
    return any(covered_start <= start and covered_end >= end for covered_start, covered_end in _merge_ranges(ranges))


def _market_bar_lock_identity(provider: str, feed: str, symbol: str, interval: BarInterval) -> str:
    return canonical_hash(["market_bar_append", provider, feed, symbol, interval.value])


def _scope_bar_lock_identity(scope: StrategyScope) -> str:
    return _market_bar_lock_identity(scope.provider.value, scope.feed, scope.symbol, scope.interval)


def _strategy_execution_id(
    dataset_hash: str,
    strategy_id: str,
    strategy_version: str,
    symbol: str,
    interval: BarInterval,
    mode: StrategyMode,
    decision_timestamp: datetime,
    execution_timestamp: datetime,
) -> str:
    return canonical_hash(
        [
            dataset_hash,
            strategy_id,
            strategy_version,
            symbol,
            interval.value,
            mode.value,
            decision_timestamp,
            execution_timestamp,
        ]
    )


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _periods_per_year(interval: BarInterval) -> int:
    return max(1, int(round(timedelta(days=365).total_seconds() / INTERVAL_DURATION[interval].total_seconds())))


def _ensemble_config_record(config: EnsembleConfig) -> dict[str, Any]:
    return {
        "equal_weight_shrinkage": config.equal_weight_shrinkage,
        "maximum_strategy_weight": config.maximum_strategy_weight,
        "maximum_family_weight": config.maximum_family_weight,
        "family_weight_caps": {
            family.value: cap
            for family, cap in sorted(config.family_weight_caps.items(), key=lambda item: item[0].value)
        },
        "sharpe_clip": config.sharpe_clip,
        "sample_size_target": config.sample_size_target,
        "fixed_share": config.fixed_share,
        "learning_rate": config.learning_rate,
        "minimum_breadth": config.minimum_breadth,
        "minimum_vote_margin": config.minimum_vote_margin,
        "minimum_probability": config.minimum_probability,
        "cost_buffer_multiplier": config.cost_buffer_multiplier,
    }


def _validation_config_record(config: ValidationConfig) -> dict[str, Any]:
    return {
        "final_test_fraction": config.final_test_fraction,
        "minimum_train_observations": config.minimum_train_observations,
        "validation_observations": config.validation_observations,
        "forecast_horizon_seconds": config.forecast_horizon.total_seconds(),
        "publication_delay_seconds": config.publication_delay.total_seconds(),
        "embargo_seconds": config.embargo.total_seconds(),
        "periods_per_year": config.periods_per_year,
        "minimum_trades": config.minimum_trades,
        "minimum_development_observations": config.minimum_development_observations,
        "maximum_drawdown": config.maximum_drawdown,
        "minimum_dsr_probability": config.minimum_dsr_probability,
    }


def _execution_assumptions_record(assumptions: ExecutionAssumptions) -> dict[str, Any]:
    return {
        "costs": asdict(assumptions.costs),
        "latency_seconds": assumptions.latency.total_seconds(),
        "tick_size": assumptions.tick_size,
        "lot_size": assumptions.lot_size,
        "participation_rate": assumptions.participation_rate,
        "short_borrow_available": assumptions.short_borrow_available,
        "flatten_at_session_end": assumptions.flatten_at_session_end,
    }


def _evaluation_metrics(evaluation: StrategyEvaluation) -> dict[str, Any]:
    final_boundary = evaluation.evidence_provenance.get("sealed_boundary")
    return {
        "status_reason": evaluation.status_reason,
        "state": evaluation.mode.value,
        "development_metrics": {
            "sharpe": _finite(evaluation.development_sharpe),
            "maximum_drawdown": _finite(evaluation.development_maximum_drawdown),
            "downside_risk": _finite(evaluation.downside_risk),
            "observations": evaluation.observations,
            "trades": evaluation.trades,
            "fold_stability": _finite(evaluation.fold_stability),
            "dsr_probability": _finite(evaluation.dsr_probability),
        },
        "final_test_metrics": {"sharpe": _finite(evaluation.final_sharpe)},
        "promotion": {
            "promoted": evaluation.promotion.promoted,
            "reasons": list(evaluation.promotion.reasons),
        },
        "causal_audit_passed": evaluation.causal_audit_passed,
        "trial_count": len(evaluation.trial_sharpes),
        "final_boundary": str(final_boundary) if final_boundary is not None else None,
        "warnings": [
            "Historical evidence is not live proof",
            "This is a research/paper-trading aid; abstain when uncertainty is material",
            *evaluation.promotion.reasons,
        ],
    }


__all__ = [
    "BarProviderName",
    "EvaluationOptions",
    "ExportOptions",
    "IngestOptions",
    "LearningOptions",
    "PipelineEvent",
    "StageOutcome",
    "StrategyPipeline",
    "StrategyScope",
    "consume_forward_evidence_and_promote",
    "create_strategy_pipeline",
]
