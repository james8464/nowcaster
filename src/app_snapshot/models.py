from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

# Wire-safety limits shared conceptually with the native decoder. They keep the
# aggregate evidence useful without allowing recursive/unbounded JSON payloads.
MAX_EVIDENCE_DEPTH = 16
MAX_EVIDENCE_NODES = 50_000
MAX_EVIDENCE_COLLECTION_LENGTH = 2_000
MAX_EVIDENCE_STRING_BYTES = 16 * 1_024

_ZULU_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


def _require_literal_z(value: Any) -> Any:
    if isinstance(value, str) and _ZULU_INSTANT.fullmatch(value) is None:
        raise ValueError("instant must use an ISO-8601 timestamp with literal Z UTC")
    return value


UTCInstant = Annotated[datetime, BeforeValidator(_require_literal_z)]


def _validate_bounded_json(value: Any) -> Any:
    nodes = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_EVIDENCE_NODES:
            raise ValueError("evidence exceeds the maximum node count")
        if depth > MAX_EVIDENCE_DEPTH:
            raise ValueError("evidence exceeds the maximum recursion depth")
        if isinstance(item, str):
            if len(item.encode("utf-8")) > MAX_EVIDENCE_STRING_BYTES:
                raise ValueError("evidence string exceeds the maximum byte length")
        elif isinstance(item, dict):
            if len(item) > MAX_EVIDENCE_COLLECTION_LENGTH:
                raise ValueError("evidence collection exceeds the maximum length")
            for key, nested in item.items():
                visit(str(key), depth + 1)
                visit(nested, depth + 1)
        elif isinstance(item, (list, tuple)):
            if len(item) > MAX_EVIDENCE_COLLECTION_LENGTH:
                raise ValueError("evidence collection exceeds the maximum length")
            for nested in item:
                visit(nested, depth + 1)

    visit(value, 0)
    return value


BoundedJSONObject = Annotated[dict[str, Any], BeforeValidator(_validate_bounded_json)]


class SnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def finite_numbers_and_explicit_utc(self) -> SnapshotModel:
        def validate(value: Any, name: str) -> Any:
            if isinstance(value, datetime):
                if value.tzinfo is None or value.utcoffset() != timedelta(0) or value.tzname() != "UTC":
                    raise ValueError(f"{name} must be an explicit UTC datetime")
                return value.astimezone(UTC).replace(tzinfo=UTC)
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{name} must be finite or null")
            if isinstance(value, dict):
                return {key: validate(item, f"{name}.{key}") for key, item in value.items()}
            if isinstance(value, list):
                return [validate(item, f"{name}[{index}]") for index, item in enumerate(value)]
            if isinstance(value, tuple):
                return tuple(validate(item, f"{name}[{index}]") for index, item in enumerate(value))
            return value

        for field_name in type(self).model_fields:
            setattr(self, field_name, validate(getattr(self, field_name), field_name))
        return self


class SnapshotMetadata(SnapshotModel):
    generated_at: UTCInstant
    git_commit: str
    data_mode: str
    source_posture: str
    expectation_mode: str
    last_refresh: UTCInstant | None = None


class OverviewSnapshot(SnapshotModel):
    company_count: int = 0
    instrument_count: int = 0
    company_quarter_count: int = 0
    alternative_observation_count: int = 0
    forecast_count: int = 0
    signal_count: int = 0
    event_window_count: int = 0
    quality_issue_count: int = 0
    forecast_mae_improvement: float | None = None
    alternative_incremental_mae_improvement: float | None = None
    event_spread: float | None = None


class PricePoint(SnapshotModel):
    date: date
    close: float
    volume: float | None = None


class InstrumentSnapshot(SnapshotModel):
    instrument_id: str
    symbol: str
    display_name: str
    asset_class: str
    last_price: float | None = None
    daily_return: float | None = None
    weekly_return: float | None = None
    realized_volatility: float | None = None
    trend_regime: str = "insufficient"
    freshness_date: date | None = None
    price_history: list[PricePoint] = Field(default_factory=list)


class EarningsSnapshot(SnapshotModel):
    forecast_id: str
    company_id: str
    fiscal_quarter: str
    earnings_date: date
    forecast_cutoff_date: date
    horizon_days: int
    model_name: str
    ablation: str
    forecast_revenue: float
    actual_revenue: float | None = None
    expectation_revenue: float
    expectation_mode: str
    variant: float
    variant_zscore: float | None = None
    confidence_score: float | None = None


class ResearchSignalSnapshot(SnapshotModel):
    signal_id: str
    instrument_id: str
    asset_class: str
    decision_date: date
    horizon: str
    posture: str
    eligibility: str
    strength: float | None = None
    calibrated_probability: float | None = None
    confidence_score: float | None = None
    catalyst: str
    invalidation: str
    evidence_summary: str
    reasons: list[str] = Field(default_factory=list)
    provider: str | None = None
    feed: str | None = None
    venue: str | None = None
    product: str | None = None
    probability_definition: str | None = None
    probability_lower_bound: float | None = Field(default=None, ge=0, le=1)
    probability_upper_bound: float | None = Field(default=None, ge=0, le=1)
    calibration_observations: int | None = Field(default=None, ge=0)
    calibration_effective_observations: float | None = Field(default=None, ge=0)
    brier_score: float | None = Field(default=None, ge=0, le=1)
    expected_calibration_error: float | None = Field(default=None, ge=0, le=1)
    gross_edge: float | None = None
    estimated_cost: float | None = Field(default=None, ge=0)
    lower_net_edge: float | None = None
    model_age_seconds: float | None = Field(default=None, ge=0)
    regime: str | None = None
    drift_status: str | None = None
    drift_score: float | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)
    coverage_ratio: float | None = Field(default=None, ge=0, le=1)
    coverage_status: str | None = None

    @model_validator(mode="after")
    def evidence_is_coherent(self) -> ResearchSignalSnapshot:
        lower = self.probability_lower_bound
        upper = self.probability_upper_bound
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("probability lower bound cannot exceed upper bound")
        if self.calibrated_probability is not None:
            if lower is not None and self.calibrated_probability < lower:
                raise ValueError("calibrated probability cannot be below its lower bound")
            if upper is not None and self.calibrated_probability > upper:
                raise ValueError("calibrated probability cannot exceed its upper bound")
        if (
            self.calibration_observations is not None
            and self.calibration_effective_observations is not None
            and self.calibration_effective_observations > self.calibration_observations
        ):
            raise ValueError("effective calibration observations cannot exceed raw observations")
        return self


class ModelDiagnosticSnapshot(SnapshotModel):
    model_name: str
    ablation: str
    horizon_days: int
    observations: int
    mae: float
    rmse: float
    mape: float | None = None
    directional_accuracy: float | None = None


class BacktestPoint(SnapshotModel):
    date: date
    value: float


class SensitivitySnapshot(SnapshotModel):
    scenario: str
    cost_multiplier: float
    metrics: dict[str, float | None] = Field(default_factory=dict)


class BacktestSnapshot(SnapshotModel):
    backtest_id: str
    asset_class: str
    strategy_name: str
    readiness: str
    verdict: str
    sample_size: int
    development_metrics: dict[str, float | None] = Field(default_factory=dict)
    final_test_metrics: dict[str, float | None] = Field(default_factory=dict)
    full_metrics: dict[str, float | None] = Field(default_factory=dict)
    robustness: dict[str, float | None] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    equity_curve: list[BacktestPoint] = Field(default_factory=list)
    drawdown_curve: list[BacktestPoint] = Field(default_factory=list)
    rolling_sharpe_curve: list[BacktestPoint] = Field(default_factory=list)
    exposure_curve: list[BacktestPoint] = Field(default_factory=list)
    turnover_curve: list[BacktestPoint] = Field(default_factory=list)
    monthly_returns: list[BacktestPoint] = Field(default_factory=list)
    sensitivities: list[SensitivitySnapshot] = Field(default_factory=list)


class QualityIssueSnapshot(SnapshotModel):
    issue_id: str
    stage: str
    severity: str
    rule: str
    entity_key: str
    message: str
    detected_at: UTCInstant


class PipelineRunSnapshot(SnapshotModel):
    pipeline_run_id: str
    command: str
    mode: str
    started_at: UTCInstant
    ended_at: UTCInstant | None = None
    status: str
    row_counts: dict[str, int] = Field(default_factory=dict)
    error_summary: str | None = None


class StrategySnapshot(SnapshotModel):
    strategy_id: str
    version: str
    family: str
    dataset_hash: str
    symbol: str
    interval: str
    mode: str
    cohort_id: str | None = None
    state: str
    weight: float = Field(ge=0)
    development_metrics: dict[str, float | None] = Field(default_factory=dict)
    final_test_metrics: dict[str, float | None] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    generation: int = Field(default=1, ge=1)
    progress: float = Field(default=1.0, ge=0, le=1)
    complexity: int | None = Field(default=None, ge=0)
    promotion_state: str = "research_only"
    causal_audit_passed: bool | None = None
    no_repaint_badge: Literal["passed", "failed", "not_audited"] = "not_audited"
    latest_run_at: UTCInstant | None = None


class EnsembleComponentSnapshot(SnapshotModel):
    strategy_id: str
    version: str
    family: str
    dataset_hash: str
    provider: str | None = None
    feed: str | None = None
    symbol: str
    interval: str
    mode: str
    cohort_id: str | None = None
    effective_at: UTCInstant
    weight: float = Field(ge=0)
    contribution: float | None = None
    evidence: BoundedJSONObject = Field(default_factory=dict)


class DatasetGapSnapshot(SnapshotModel):
    start: UTCInstant
    end: UTCInstant
    missing_bars: int = Field(gt=0)


class DatasetCoverageSnapshot(SnapshotModel):
    dataset_hash: str
    provider: str
    feed: str
    symbol: str
    interval: str
    requested_start: UTCInstant
    requested_end: UTCInstant
    coverage_start: UTCInstant | None = None
    coverage_end: UTCInstant | None = None
    row_count: int = Field(ge=0)
    gaps: list[DatasetGapSnapshot] = Field(default_factory=list)
    complete: bool = False
    calendar_id: str = "unknown"
    calendar_version: str = "unknown"


class LearningTrialSnapshot(SnapshotModel):
    trial_id: str
    candidate_hash: str
    status: str
    fitness: float | None = None
    evaluated_at: UTCInstant
    rule_text: str
    complexity: int = Field(ge=0)
    error_summary: str | None = None


class DiscoveredRuleSnapshot(SnapshotModel):
    rule_id: str
    strategy_id: str
    version: str
    state: str
    rule_text: str
    fitness: float | None = None
    complexity: int = Field(ge=0)
    discovered_at: UTCInstant
    evidence_through: UTCInstant | None = None
    promotion_state: str = "shadow"
    causal_audit_id: str | None = None
    no_repaint_badge: Literal["passed", "failed", "not_audited"] = "not_audited"


class LearningRunSnapshot(SnapshotModel):
    learning_run_id: str
    state: str
    evaluated_candidates: int = Field(ge=0)
    evaluation_budget: int = Field(gt=0)
    best_rule: str | None = None
    best_rule_detail: DiscoveredRuleSnapshot | None = None
    final_boundary: UTCInstant
    generation: int = Field(default=1, ge=1)
    progress: float = Field(default=0.0, ge=0, le=1)
    trials: list[LearningTrialSnapshot] = Field(default_factory=list)
    discovered_rules: list[DiscoveredRuleSnapshot] = Field(default_factory=list)
    promotion_state: str = "shadow"
    causal_audit_id: str | None = None
    no_repaint_badge: Literal["passed", "failed", "not_audited"] = "not_audited"

    @model_validator(mode="after")
    def counts_fit_budget(self) -> LearningRunSnapshot:
        if self.evaluated_candidates > self.evaluation_budget:
            raise ValueError("evaluated candidates cannot exceed the evaluation budget")
        return self


class DeepResearchResourceSnapshot(SnapshotModel):
    active_workers: int = Field(default=0, ge=0)
    queued_trials: int = Field(default=0, ge=0)
    memory_bytes: int | None = Field(default=None, ge=0)
    thermal_state: str = "unknown"


class DeepResearchRunSnapshot(SnapshotModel):
    run_id: str
    state: str
    symbol: str
    interval: str
    provider: str
    feed: str
    dataset_hash: str
    protocol_id: str
    started_at: UTCInstant
    updated_at: UTCInstant
    final_test_start: UTCInstant
    continuous: bool = False
    trial_budget: int | None = Field(default=None, gt=0)
    cycle_budget: int = Field(gt=0)
    evaluated_attempts: int = Field(default=0, ge=0)
    succeeded_attempts: int = Field(default=0, ge=0)
    failed_attempts: int = Field(default=0, ge=0)
    generation: int = Field(default=1, ge=1)
    progress: float = Field(default=0.0, ge=0, le=1)
    best_candidate_hash: str | None = None
    champion_score: float | None = None
    outcome: Literal[
        "research_running",
        "no_reliable_strategy_found",
        "research_champion_found",
        "shadow_cohort_started",
        "existing_champion_retained",
        "stopped",
        "failed",
    ] = "research_running"
    failed_gates: list[str] = Field(default_factory=list, max_length=100)
    resources: DeepResearchResourceSnapshot = Field(default_factory=DeepResearchResourceSnapshot)

    @model_validator(mode="after")
    def validate_deep_research_counts(self) -> DeepResearchRunSnapshot:
        if self.succeeded_attempts + self.failed_attempts > self.evaluated_attempts:
            raise ValueError("terminal attempt counts cannot exceed evaluated attempts")
        if self.trial_budget is not None and self.evaluated_attempts > self.trial_budget:
            raise ValueError("evaluated attempts cannot exceed a bounded trial budget")
        if self.updated_at < self.started_at:
            raise ValueError("updated_at cannot precede started_at")
        return self


class CausalAuditSnapshot(SnapshotModel):
    audit_id: str
    dataset_hash: str
    strategy_id: str
    version: str
    symbol: str
    interval: str
    mode: str
    audited_at: UTCInstant
    passed: bool
    outer_block_consumed: bool = False
    details: BoundedJSONObject = Field(default_factory=dict)
    no_repaint_badge: Literal["passed", "failed"]


class BrokerStatusSnapshot(SnapshotModel):
    environment: Literal["research", "shadow", "paper", "live"] = "research"
    state: str = "live_locked"
    account_suffix: str | None = None
    session_status: str = "not_started"
    reconciled_at: UTCInstant | None = None
    unresolved_mismatches: int = Field(default=0, ge=0)


class BrokerPositionSnapshot(SnapshotModel):
    symbol: str
    quantity: float
    market_value: float
    unrealized_pnl: float
    received_at: UTCInstant


class BrokerOrderSnapshot(SnapshotModel):
    client_order_id: str
    symbol: str
    side: str
    quantity: float
    filled_quantity: float
    limit_price: float
    status: str
    updated_at: UTCInstant


class BrokerEventSnapshot(SnapshotModel):
    event_id: str
    client_order_id: str
    event: str
    known_event: bool
    status: str
    received_at: UTCInstant


class RiskStatusSnapshot(SnapshotModel):
    state: str = "not_evaluated"
    allowed: bool = False
    reasons: list[str] = Field(default_factory=lambda: ["live_locked"])
    utilization: dict[str, str | int] = Field(default_factory=dict)
    decided_at: UTCInstant | None = None


class ReadinessGateSnapshot(SnapshotModel):
    name: str
    passed: bool
    detail: str


class ForwardReadinessSnapshot(SnapshotModel):
    state: Literal["live_locked", "eligible", "armed"] = "live_locked"
    cohort_hash: str | None = None
    observed_periods: int = Field(default=0, ge=0)
    closed_trades: int = Field(default=0, ge=0)
    receipt_expires_at: UTCInstant | None = None
    gates: list[ReadinessGateSnapshot] = Field(
        default_factory=lambda: [
            ReadinessGateSnapshot(
                name="external_forward_evidence",
                passed=False,
                detail="Paper evidence and external release conditions are not yet complete.",
            )
        ],
        max_length=50,
    )


class EmergencyStatusSnapshot(SnapshotModel):
    frozen: bool = False
    flatten_state: str = "not_requested"
    reason: str | None = None
    observed_at: UTCInstant | None = None


class AppSnapshot(SnapshotModel):
    schema_version: Literal[5] = 5
    metadata: SnapshotMetadata
    overview: OverviewSnapshot = Field(default_factory=OverviewSnapshot)
    instruments: list[InstrumentSnapshot] = Field(default_factory=list)
    earnings: list[EarningsSnapshot] = Field(default_factory=list)
    signals: list[ResearchSignalSnapshot] = Field(default_factory=list)
    model_diagnostics: list[ModelDiagnosticSnapshot] = Field(default_factory=list)
    backtests: list[BacktestSnapshot] = Field(default_factory=list)
    quality_issues: list[QualityIssueSnapshot] = Field(default_factory=list)
    pipeline_runs: list[PipelineRunSnapshot] = Field(default_factory=list)
    strategies: list[StrategySnapshot] = Field(default_factory=list)
    ensemble_components: list[EnsembleComponentSnapshot] = Field(default_factory=list)
    dataset_coverage: list[DatasetCoverageSnapshot] = Field(default_factory=list)
    learning_runs: list[LearningRunSnapshot] = Field(default_factory=list)
    deep_research_runs: list[DeepResearchRunSnapshot] = Field(default_factory=list)
    causal_audits: list[CausalAuditSnapshot] = Field(default_factory=list)
    broker_status: BrokerStatusSnapshot = Field(default_factory=BrokerStatusSnapshot)
    broker_positions: list[BrokerPositionSnapshot] = Field(default_factory=list, max_length=100)
    broker_orders: list[BrokerOrderSnapshot] = Field(default_factory=list, max_length=100)
    broker_events: list[BrokerEventSnapshot] = Field(default_factory=list, max_length=200)
    risk_status: RiskStatusSnapshot = Field(default_factory=RiskStatusSnapshot)
    forward_readiness: ForwardReadinessSnapshot = Field(default_factory=ForwardReadinessSnapshot)
    emergency_status: EmergencyStatusSnapshot = Field(default_factory=EmergencyStatusSnapshot)
