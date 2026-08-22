from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class SnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


class BacktestSnapshot(SnapshotModel):
    backtest_id: str
    asset_class: str
    strategy_name: str
    readiness: str
    verdict: str
    sample_size: int
    development_metrics: dict[str, float | None] = Field(default_factory=dict)
    final_test_metrics: dict[str, float | None] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    equity_curve: list[BacktestPoint] = Field(default_factory=list)
    drawdown_curve: list[BacktestPoint] = Field(default_factory=list)


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


class AppSnapshot(SnapshotModel):
    schema_version: int = 1
    metadata: SnapshotMetadata
    overview: OverviewSnapshot = Field(default_factory=OverviewSnapshot)
    instruments: list[InstrumentSnapshot] = Field(default_factory=list)
    earnings: list[EarningsSnapshot] = Field(default_factory=list)
    signals: list[ResearchSignalSnapshot] = Field(default_factory=list)
    model_diagnostics: list[ModelDiagnosticSnapshot] = Field(default_factory=list)
    backtests: list[BacktestSnapshot] = Field(default_factory=list)
    quality_issues: list[QualityIssueSnapshot] = Field(default_factory=list)
    pipeline_runs: list[PipelineRunSnapshot] = Field(default_factory=list)
