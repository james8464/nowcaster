from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    generated_at: datetime
    git_commit: str
    data_mode: str
    source_posture: str
    expectation_mode: str
    last_refresh: datetime | None = None


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
    detected_at: datetime


class PipelineRunSnapshot(SnapshotModel):
    pipeline_run_id: str
    command: str
    mode: str
    started_at: datetime
    ended_at: datetime | None = None
    status: str
    row_counts: dict[str, int] = Field(default_factory=dict)
    error_summary: str | None = None


class StrategySnapshot(SnapshotModel):
    strategy_id: str
    version: str
    family: str
    symbol: str
    interval: str
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
    latest_run_at: datetime | None = None


class EnsembleComponentSnapshot(SnapshotModel):
    strategy_id: str
    version: str
    family: str
    symbol: str
    interval: str
    mode: str
    effective_at: datetime
    weight: float = Field(ge=0)
    contribution: float | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class DatasetGapSnapshot(SnapshotModel):
    start: datetime
    end: datetime
    missing_bars: int = Field(gt=0)


class DatasetCoverageSnapshot(SnapshotModel):
    dataset_hash: str
    provider: str
    feed: str
    symbol: str
    interval: str
    requested_start: datetime
    requested_end: datetime
    coverage_start: datetime | None = None
    coverage_end: datetime | None = None
    row_count: int = Field(ge=0)
    gaps: list[DatasetGapSnapshot] = Field(default_factory=list)
    complete: bool = False


class LearningTrialSnapshot(SnapshotModel):
    trial_id: str
    candidate_hash: str
    status: str
    fitness: float | None = None
    evaluated_at: datetime
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
    discovered_at: datetime
    evidence_through: datetime | None = None
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
    final_boundary: datetime
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


class CausalAuditSnapshot(SnapshotModel):
    audit_id: str
    dataset_hash: str
    strategy_id: str
    version: str
    symbol: str
    interval: str
    mode: str
    audited_at: datetime
    passed: bool
    outer_block_consumed: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
    no_repaint_badge: Literal["passed", "failed"]


class AppSnapshot(SnapshotModel):
    schema_version: Literal[2] = 2
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
    causal_audits: list[CausalAuditSnapshot] = Field(default_factory=list)
