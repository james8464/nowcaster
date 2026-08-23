from __future__ import annotations

import json
import math
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import httpx
import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from src.app_snapshot.builder import build_app_snapshot
from src.app_snapshot.writer import write_snapshot_atomic
from src.backtest.execution import ExecutionAssumptions
from src.backtest.intraday import RiskLimits, run_intraday_backtest
from src.config.settings import Settings
from src.database.engine import Database
from src.database.schema import causal_audits
from src.ingestion.alpaca_bars import AlpacaBarProvider
from src.ingestion.bars import INTERVAL_DURATION, BarProvider, BarQuery, BarRequest
from src.ingestion.binance_bars import BinanceBarProvider
from src.ingestion.csv_bars import CSVBarProvider
from src.learning.promotion import ForwardEvidence, promote_candidate
from src.learning.search import LearningExperiment, RuleCandidate, discover_rules
from src.reporting.strategy_report import write_strategy_research_report_atomic
from src.strategies.datasets import BarRepository, DatasetGap, DatasetManifest
from src.strategies.indicators import rolling_zscore, rsi
from src.strategies.library import StrategyContext, audit_prefix_invariance, build_strategy_registry
from src.strategies.registry import RegisteredStrategy, StrategyRegistry
from src.strategies.types import BarInterval, StrategyMode, canonical_hash
from src.strategies.validation import (
    DEFAULT_VALIDATION_CONFIG,
    EvaluationRequest,
    FoldEvidence,
    PromotionDecision,
    StrategyEvaluation,
    StrategyRunEvidence,
    TrialEvidence,
    ValidationConfig,
    WalkForwardFold,
    evaluate_registry,
    make_outer_folds,
    select_final_boundary,
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
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str
    provider: BarProviderName
    feed: str
    symbol: str
    interval: BarInterval
    mode: StrategyMode = StrategyMode.PAPER

    @field_validator("strategy_id", "feed")
    @classmethod
    def non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("strategy ID and feed must not be empty")
        return normalized

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


@dataclass(frozen=True, slots=True)
class StageOutcome:
    status: Literal["completed", "reused", "unavailable"]
    message: str
    dataset_hash: str | None = None
    strategy_run_id: str | None = None
    learning_run_id: str | None = None
    evaluated_candidates: int = 0
    snapshot_path: Path | None = None
    report_path: Path | None = None


EventSink = Callable[[PipelineEvent], None]


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
    ):
        self.database = database
        self.database.initialize()
        self.registry = registry
        self.providers = dict(providers)
        self.provider_unavailable = dict(provider_unavailable or {})
        self.clock = clock
        self.validation_config = validation_config
        self.bars = BarRepository(database)

    def ingest(self, options: IngestOptions, emit: EventSink | None = None) -> StageOutcome:
        registered = self._registered(options.scope)
        self._emit(emit, "progress", "ingest", 0.1, f"resolved {registered.spec.strategy_id}")
        provider = self.providers.get(options.scope.provider)
        if provider is None:
            reason = self.provider_unavailable.get(options.scope.provider, "provider is not configured")
            return StageOutcome("unavailable", f"provider unavailable: {reason}")
        query = BarQuery(
            provider=options.scope.provider.value,
            feed=options.scope.feed,
            symbol=options.scope.symbol,
            interval=options.scope.interval,
            start=options.start,
            end=options.end,
        )
        gaps = self.bars.gaps(query)
        if options.force:
            gaps = (DatasetGap(start=options.start, end=options.end, missing_bars=1),)
        if not gaps:
            manifest = self.bars.manifest(query)
            self._emit(emit, "progress", "ingest", 1.0, "coverage already available")
            return StageOutcome("reused", "coverage already available", dataset_hash=manifest.dataset_hash)

        inserted = 0
        for position, gap in enumerate(gaps, start=1):
            fetched = provider.fetch(
                BarRequest(
                    symbol=options.scope.symbol,
                    interval=options.scope.interval,
                    start=gap.start,
                    end=gap.end,
                    feed=options.scope.feed,
                )
            )
            inserted += self.bars.append(fetched)
            self._emit(
                emit,
                "progress",
                "ingest",
                0.1 + 0.8 * position / len(gaps),
                f"fetched missing coverage {position}/{len(gaps)}",
            )
        manifest = self.bars.manifest(query)
        missing = sum(gap.missing_bars for gap in manifest.gaps)
        if inserted == 0 and missing:
            message = f"data unavailable: {missing} requested bars remain missing"
            status: Literal["completed", "reused", "unavailable"] = "unavailable"
        elif missing:
            message = f"appended {inserted} immutable revisions; {missing} requested bars unavailable"
            status = "completed"
        else:
            message = f"appended {inserted} immutable revisions"
            status = "completed"
        self._emit(emit, "progress", "ingest", 1.0, message)
        return StageOutcome(status, message, dataset_hash=manifest.dataset_hash)

    def evaluate(self, options: EvaluationOptions, emit: EventSink | None = None) -> StageOutcome:
        registered = self._registered(options.scope)
        query = self._local_query(options.scope)
        if query is None:
            return StageOutcome("unavailable", "local compatible bar history is unavailable")
        manifest = self.bars.manifest(query)
        cached = self._cached_run(options.scope, registered, manifest.dataset_hash)
        if cached is not None and not options.force:
            self._emit(emit, "progress", "evaluate", 1.0, "reused cached evaluation")
            return StageOutcome(
                "reused",
                "reused cached evaluation",
                dataset_hash=manifest.dataset_hash,
                strategy_run_id=str(cached),
            )
        self._emit(emit, "progress", "evaluate", 0.1, "loaded all locally available compatible history")
        run_timestamp = self._run_timestamp()
        run_generation = self._exact_run_count(options.scope, registered, manifest.dataset_hash) + 1
        latest_timestamp = self._latest_exact_run_timestamp(options.scope, registered, manifest.dataset_hash)
        if latest_timestamp is not None and run_timestamp <= latest_timestamp:
            run_timestamp = latest_timestamp + timedelta(microseconds=1)
        run_id = canonical_hash(
            {
                "cache_key": self._cache_key(options.scope, registered, manifest.dataset_hash),
                "run_timestamp": run_timestamp,
                "run_generation": run_generation,
            }
        )
        try:
            evaluation, signals, backtest, audit_details = self._evaluate_engines(
                options.scope, registered, query, manifest
            )
            self._persist_evaluation(
                options.scope,
                registered,
                manifest,
                run_id,
                run_timestamp,
                evaluation,
                signals,
                backtest,
                audit_details,
            )
        except Exception as error:
            self._persist_failed_run(
                options.scope,
                registered,
                manifest.dataset_hash,
                run_id,
                run_timestamp,
                str(error),
            )
            raise
        message = (
            "evaluation completed"
            if evaluation.status.value == "evaluated"
            else f"evaluation {evaluation.status.value}: {evaluation.status_reason}"
        )
        self._emit(emit, "progress", "evaluate", 1.0, message)
        return StageOutcome(
            "completed",
            message,
            dataset_hash=manifest.dataset_hash,
            strategy_run_id=run_id,
        )

    def learn(self, options: LearningOptions, emit: EventSink | None = None) -> StageOutcome:
        registered = self._registered(options.scope)
        query = self._local_query(options.scope)
        if query is None:
            return StageOutcome("unavailable", "local compatible bar history is unavailable")
        manifest = self.bars.manifest(query)
        bars = self.bars.bars_as_of(query, self._query_as_of(query))
        experiment, development = self._learning_experiment(options, registered, manifest, bars)
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
        try:
            registered = self.registry.resolve(scope.strategy_id)
        except KeyError as error:
            raise ValueError(str(error).strip("'")) from error
        if scope.interval not in registered.spec.intervals:
            raise ValueError(f"Strategy '{scope.strategy_id}' is not registered for interval '{scope.interval.value}'")
        return registered

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

    def _cached_run(self, scope: StrategyScope, registered: RegisteredStrategy, dataset_hash: str) -> str | None:
        row = self.database.frame(
            "select strategy_run_id from strategy_runs where dataset_hash = :dataset_hash "
            "and strategy_id = :strategy_id and strategy_version = :strategy_version "
            "and symbol = :symbol and interval = :interval and mode = :mode "
            "and status != 'failed' "
            "order by run_timestamp desc limit 1",
            dict(
                zip(
                    ("dataset_hash", "strategy_id", "strategy_version", "symbol", "interval", "mode"),
                    self._cache_key(scope, registered, dataset_hash),
                    strict=True,
                )
            ),
        )
        return None if row.empty else str(row.iloc[0]["strategy_run_id"])

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

    def _evaluate_engines(
        self,
        scope: StrategyScope,
        registered: RegisteredStrategy,
        query: BarQuery,
        manifest: DatasetManifest,
    ) -> tuple[StrategyEvaluation, pd.DataFrame, Any, dict[str, Any]]:
        as_of = self._query_as_of(query)
        bars = self.bars.bars_as_of(query, as_of)
        if len(bars) < max(registered.spec.warmup_bars + 2, 3):
            raise ValueError("insufficient locally available compatible history")
        signals = registered.generator(registered.spec, bars, StrategyContext())
        prefix_size = max(registered.spec.warmup_bars, int(len(bars) * 0.8))
        prefix_size = min(prefix_size, len(bars) - 1)
        audit = audit_prefix_invariance(
            registered.spec,
            bars.iloc[:prefix_size].copy(),
            bars.copy(),
            StrategyContext(),
            StrategyContext(),
            generator=registered.generator,
        )
        periods = _periods_per_year(scope.interval)
        backtest = run_intraday_backtest(
            bars,
            signals,
            ExecutionAssumptions(lot_size=0.000001),
            RiskLimits(initial_cash=100_000, periods_per_year=periods),
            strategy_id=registered.spec.strategy_id,
            symbol=scope.symbol,
        )
        curve_times = pd.to_datetime(backtest.equity_curve["timestamp"], utc=True)
        if len(curve_times) < 3:
            raise ValueError("insufficient causal execution history")
        chronology = curve_times.iloc[:-1].reset_index(drop=True)
        outcomes = curve_times.iloc[1:].reset_index(drop=True)
        boundary = select_final_boundary(chronology, final_test_fraction=self.validation_config.final_test_fraction)
        validation_data = pd.DataFrame({"decision_timestamp": chronology, "outcome_available_at": outcomes})
        folds = make_outer_folds(validation_data, boundary=boundary, config=self.validation_config)
        fold_evidence = tuple(
            self._fold_evidence(index, fold, chronology, backtest.equity_curve, periods)
            for index, fold in enumerate(folds)
        )
        run_evidence = StrategyRunEvidence(
            backtest=backtest,
            signals=signals,
            trial_evidence=self._trial_evidence(manifest, scope, boundary.final_start),
            fold_evidence=fold_evidence,
            causal_audit_passed=audit.passed,
        )
        evaluation = evaluate_registry(
            EvaluationRequest(
                registry=_single_registry(registered),
                runs={registered.spec.strategy_id: run_evidence},
                chronology=chronology,
                outcome_availability=outcomes,
                as_of=as_of,
                mode=scope.mode,
                dataset_hash=manifest.dataset_hash,
                symbol=scope.symbol,
                interval=scope.interval,
                config=self.validation_config,
            )
        )[0]
        return evaluation, signals, backtest, asdict(audit)

    def _fold_evidence(
        self,
        index: int,
        fold: Any,
        chronology: pd.Series,
        curve: pd.DataFrame,
        periods: int,
    ) -> FoldEvidence:
        start = chronology.iloc[fold.validation_index[0]].to_pydatetime()
        end = chronology.iloc[fold.validation_index[-1]].to_pydatetime()
        returns = pd.to_numeric(curve.iloc[list(fold.validation_index)]["net_return"], errors="coerce").dropna()
        deviation = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
        sharpe = float(returns.mean() / deviation * math.sqrt(periods)) if deviation > 0 else 0.0
        return FoldEvidence(index, start, end, end, sharpe, 0.5)

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

    def _persist_evaluation(
        self,
        scope: StrategyScope,
        registered: RegisteredStrategy,
        manifest: DatasetManifest,
        run_id: str,
        run_timestamp: datetime,
        evaluation: StrategyEvaluation,
        signals: pd.DataFrame,
        backtest: Any,
        audit_details: dict[str, Any],
    ) -> None:
        created_at = max(self._run_timestamp(), run_timestamp)
        metrics = _evaluation_metrics(evaluation)
        self.database.insert(
            "strategy_runs",
            [
                {
                    "strategy_run_id": run_id,
                    "dataset_hash": manifest.dataset_hash,
                    "strategy_id": registered.spec.strategy_id,
                    "strategy_version": registered.spec.deterministic_version,
                    "family": registered.spec.family.value,
                    "symbol": scope.symbol,
                    "interval": scope.interval.value,
                    "mode": scope.mode.value,
                    "run_timestamp": run_timestamp,
                    "parameters": dict(registered.spec.parameters),
                    "status": evaluation.status.value,
                    "metrics": metrics,
                    "started_at": run_timestamp,
                    "ended_at": created_at,
                    "source": "strategy_pipeline",
                    "source_version": "2",
                    "created_at": created_at,
                }
            ],
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
        self.database.upsert("strategy_signals", signal_rows)
        execution_rows = []
        for row in backtest.trade_ledger.itertuples(index=False):
            decision = _utc_datetime(row.decision_timestamp)
            executed = _utc_datetime(row.execution_timestamp)
            execution_rows.append(
                {
                    "execution_id": canonical_hash(
                        [
                            manifest.dataset_hash,
                            registered.spec.strategy_id,
                            registered.spec.deterministic_version,
                            scope.symbol,
                            scope.interval.value,
                            scope.mode.value,
                            decision,
                            executed,
                        ]
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
        self.database.upsert("strategy_executions", execution_rows)
        audit_id = canonical_hash(["prefix_invariance", *self._cache_key(scope, registered, manifest.dataset_hash)])
        self.database.upsert(
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

    def _persist_failed_run(
        self,
        scope: StrategyScope,
        registered: RegisteredStrategy,
        dataset_hash: str,
        run_id: str,
        run_timestamp: datetime,
        reason: str,
    ) -> None:
        ended_at = max(self._run_timestamp(), run_timestamp)
        self.database.insert(
            "strategy_runs",
            [
                {
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
                    "status": "failed",
                    "metrics": {"error_summary": reason},
                    "started_at": run_timestamp,
                    "ended_at": ended_at,
                    "source": "strategy_pipeline",
                    "source_version": "2",
                    "created_at": ended_at,
                }
            ],
        )

    def _learning_experiment(
        self,
        options: LearningOptions,
        registered: RegisteredStrategy,
        manifest: DatasetManifest,
        bars: pd.DataFrame,
    ) -> tuple[LearningExperiment, pd.DataFrame]:
        frame = bars.sort_values("open_timestamp", kind="stable").reset_index(drop=True).copy()
        frame["decision_timestamp"] = pd.to_datetime(frame["close_timestamp"], utc=True)
        frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True)
        frame["outcome_available_at"] = frame["decision_timestamp"].shift(-1)
        frame["forward_return"] = pd.to_numeric(frame["close"], errors="coerce").pct_change().shift(-1)
        frame["rsi"] = rsi(frame["close"], min(14, max(2, registered.spec.warmup_bars)))
        frame["volume_zscore"] = rolling_zscore(frame["volume"], min(20, max(3, registered.spec.warmup_bars)))
        frame = frame.dropna(subset=["rsi", "volume_zscore", "forward_return", "outcome_available_at"]).reset_index(
            drop=True
        )
        minimum = self.validation_config.minimum_train_observations + self.validation_config.validation_observations + 3
        if len(frame) < minimum:
            raise ValueError("insufficient finalized development history for nested learning validation")
        outer_boundary = select_final_boundary(
            frame["decision_timestamp"], final_test_fraction=self.validation_config.final_test_fraction
        )
        final_start = outer_boundary.final_start.to_pydatetime()
        outer_development = frame.iloc[list(outer_boundary.development_index)].copy().reset_index(drop=True)
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
            started_at=as_of - timedelta(seconds=1),
            as_of=as_of,
            sealed_final_start=final_start,
            seed=options.seed,
            evaluation_budget=options.evaluation_budget,
            inner_folds=inner_folds,
            indicators=("rsi", "volume_zscore"),
            thresholds=(-1.0, 0.0, 1.0, 30.0, 50.0, 70.0),
            database=self.database,
            execution_assumptions=ExecutionAssumptions(lot_size=0.000001),
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
    return StrategyPipeline(
        database,
        registry,
        providers,
        provider_unavailable=unavailable,
    ).bind_settings(settings)


def _single_registry(registered: RegisteredStrategy) -> StrategyRegistry:
    registry = StrategyRegistry()
    registry.register(registered.spec, registered.generator, registered.metadata)
    return registry


def _utc_datetime(value: Any) -> datetime:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError("timestamp is unavailable")
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC").to_pydatetime().replace(tzinfo=UTC)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _periods_per_year(interval: BarInterval) -> int:
    return max(1, int(round(timedelta(days=365).total_seconds() / INTERVAL_DURATION[interval].total_seconds())))


def _evaluation_metrics(evaluation: StrategyEvaluation) -> dict[str, Any]:
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
