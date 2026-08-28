from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from src.backtest.costs import execution_model_error
from src.database.engine import Database
from src.database.schema import TABLES
from src.strategies.types import canonical_hash
from src.trading.types import ExecutionObservation


class ForwardCohortIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_class: Literal["equity", "crypto"]
    provider: str
    feed: str
    symbol: str
    interval: str
    strategy_id: str
    strategy_version: str
    parameters_hash: str
    weights_hash: str
    dataset_hash: str
    code_hash: str
    config_hash: str
    risk_policy_hash: str
    cost_policy_hash: str
    selection_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @property
    def cohort_hash(self) -> str:
        return self.selection_hash or canonical_hash(self.model_dump(mode="json", exclude={"selection_hash"}))


class ForwardDailyEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cohort_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    period_start: datetime
    period_end: datetime
    closed_trades: int = Field(ge=0)
    paper_net_return: Decimal | None
    stressed_net_return: Decimal | None
    drawdown: Decimal | None
    reconciliation_mismatches: int = Field(ge=0)
    health_breakers: int = Field(ge=0)
    modeled_slippage_bps: Decimal | None = Field(default=None, ge=0)
    observed_slippage_bps: Decimal | None = Field(default=None, ge=0)
    execution_observations: int = Field(default=0, ge=0)
    execution_effective_observations: Decimal = Field(default=Decimal(0), ge=0)
    execution_error_upper_ratio: Decimal | None = Field(default=None, ge=0)
    execution_cost_buffer_bps: Decimal | None = Field(default=None, ge=0)
    execution_model_status: Literal["calibrated", "unavailable"] = "unavailable"
    execution_error_limit_ratio: Decimal = Field(default=Decimal("0.20"), ge=0)
    execution_observation_records: tuple[ExecutionObservation, ...] = ()
    status: Literal["complete", "unavailable"]
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    closed_at: datetime

    @model_validator(mode="after")
    def valid_period(self) -> ForwardDailyEvidence:
        for value in (self.period_start, self.period_end, self.closed_at):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError("forward evidence timestamps must be explicit UTC")
        if self.period_end <= self.period_start:
            raise ValueError("forward evidence period must be positive")
        return self

    def replay_arguments(self, cohort: ForwardCohortIdentity) -> dict[str, object]:
        return {
            "cohort": cohort,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "closed_trades": self.closed_trades,
            "paper_net_return": self.paper_net_return,
            "stressed_net_return": self.stressed_net_return,
            "drawdown": self.drawdown,
            "reconciliation_mismatches": self.reconciliation_mismatches,
            "health_breakers": self.health_breakers,
            "modeled_slippage_bps": self.modeled_slippage_bps,
            "observed_slippage_bps": self.observed_slippage_bps,
            "execution_observations": self.execution_observation_records,
            "maximum_execution_error_ratio": self.execution_error_limit_ratio,
        }


class ForwardEvidenceBuilder:
    def __init__(self, database: Database, *, clock=None):
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def close_period(
        self,
        *,
        cohort: ForwardCohortIdentity,
        period_start: datetime,
        period_end: datetime,
        closed_trades: int,
        paper_net_return: Decimal | None,
        stressed_net_return: Decimal | None,
        drawdown: Decimal | None,
        reconciliation_mismatches: int,
        health_breakers: int,
        modeled_slippage_bps: Decimal | None = None,
        observed_slippage_bps: Decimal | None = None,
        execution_observations: Sequence[ExecutionObservation] = (),
        maximum_execution_error_ratio: Decimal = Decimal("0.20"),
    ) -> ForwardDailyEvidence:
        return self.close_selection_period(
            cohort_hash=cohort.cohort_hash,
            period_start=period_start,
            period_end=period_end,
            closed_trades=closed_trades,
            paper_net_return=paper_net_return,
            stressed_net_return=stressed_net_return,
            drawdown=drawdown,
            reconciliation_mismatches=reconciliation_mismatches,
            health_breakers=health_breakers,
            modeled_slippage_bps=modeled_slippage_bps,
            observed_slippage_bps=observed_slippage_bps,
            execution_observations=execution_observations,
            maximum_execution_error_ratio=maximum_execution_error_ratio,
        )

    def close_selection_period(
        self,
        *,
        cohort_hash: str,
        period_start: datetime,
        period_end: datetime,
        closed_trades: int,
        paper_net_return: Decimal | None,
        stressed_net_return: Decimal | None,
        drawdown: Decimal | None,
        reconciliation_mismatches: int,
        health_breakers: int,
        modeled_slippage_bps: Decimal | None = None,
        observed_slippage_bps: Decimal | None = None,
        execution_observations: Sequence[ExecutionObservation] = (),
        maximum_execution_error_ratio: Decimal = Decimal("0.20"),
    ) -> ForwardDailyEvidence:
        if len(cohort_hash) != 64 or any(item not in "0123456789abcdef" for item in cohort_hash):
            raise ValueError("forward cohort identity must be a SHA-256 hash")
        if maximum_execution_error_ratio < 0 or not maximum_execution_error_ratio.is_finite():
            raise ValueError("maximum execution error ratio must be finite and non-negative")
        observations = tuple(execution_observations)
        for observation in observations:
            if observation.cohort_hash != cohort_hash:
                raise ValueError("execution observation cohort does not match the forward cohort")
            if not period_start <= observation.decision_at <= observation.terminal_at <= period_end:
                raise ValueError("execution observation falls outside its forward period")
        report = execution_model_error(observations, minimum_observations=max(closed_trades, 1))
        if observations:
            predicted = sum((item.predicted_execution_cost_bps for item in observations), Decimal(0)) / len(
                observations
            )
            realized = sum((item.realized_execution_cost_bps for item in observations), Decimal(0)) / len(
                observations
            )
            if modeled_slippage_bps is not None and modeled_slippage_bps != predicted:
                raise ValueError("modeled execution cost conflicts with its observation ledger")
            if observed_slippage_bps is not None and observed_slippage_bps != realized:
                raise ValueError("observed execution cost conflicts with its observation ledger")
            modeled_slippage_bps = predicted
            observed_slippage_bps = realized
        status = (
            "complete"
            if reconciliation_mismatches == 0
            and health_breakers == 0
            and paper_net_return is not None
            and stressed_net_return is not None
            and drawdown is not None
            and report.status == "calibrated"
            and report.upper_relative_error is not None
            and report.upper_relative_error <= maximum_execution_error_ratio
            else "unavailable"
        )
        payload = {
            "cohort_hash": cohort_hash,
            "period_start": period_start,
            "period_end": period_end,
            "closed_trades": closed_trades,
            "paper_net_return": str(paper_net_return) if paper_net_return is not None else None,
            "stressed_net_return": str(stressed_net_return) if stressed_net_return is not None else None,
            "drawdown": str(drawdown) if drawdown is not None else None,
            "reconciliation_mismatches": reconciliation_mismatches,
            "health_breakers": health_breakers,
            "modeled_slippage_bps": str(modeled_slippage_bps) if modeled_slippage_bps is not None else None,
            "observed_slippage_bps": str(observed_slippage_bps) if observed_slippage_bps is not None else None,
            "execution_observations": report.observation_count,
            "execution_effective_observations": str(report.effective_observations),
            "execution_error_upper_ratio": (
                str(report.upper_relative_error) if report.upper_relative_error is not None else None
            ),
            "execution_cost_buffer_bps": (
                str(report.conservative_cost_buffer_bps)
                if report.conservative_cost_buffer_bps is not None
                else None
            ),
            "execution_model_status": report.status,
            "execution_error_limit_ratio": str(maximum_execution_error_ratio),
            "execution_observation_records": [item.model_dump(mode="json") for item in observations],
            "status": status,
        }
        evidence = ForwardDailyEvidence(
            **payload,
            evidence_hash=canonical_hash(payload),
            closed_at=self.clock(),
        )
        table = TABLES["forward_evidence_daily"]
        with self.database.engine.connect() as connection:
            existing = connection.execute(
                select(table.c.evidence).where(
                    table.c.cohort_hash == cohort_hash,
                    table.c.period_start == period_start,
                    table.c.period_end == period_end,
                )
            ).scalar_one_or_none()
        if existing is not None:
            existing_hash = existing.get("evidence_hash") if isinstance(existing, dict) else None
            if existing_hash != evidence.evidence_hash:
                raise ValueError("conflicting closed forward period")
            return ForwardDailyEvidence.model_validate(existing)
        now = self.clock()
        self.database.insert(
            "forward_evidence_daily",
            [
                {
                    "forward_evidence_id": canonical_hash((cohort_hash, period_start, period_end)),
                    "cohort_hash": cohort_hash,
                    "period_start": period_start,
                    "period_end": period_end,
                    "closed_trades": closed_trades,
                    "paper_net_return": paper_net_return,
                    "stressed_net_return": stressed_net_return,
                    "drawdown": drawdown,
                    "reconciliation_mismatches": reconciliation_mismatches,
                    "health_breakers": health_breakers,
                    "status": status,
                    "evidence": evidence.model_dump(mode="json"),
                    "closed_at": evidence.closed_at,
                    "source": "nowcaster_trading",
                    "source_version": "1",
                    "created_at": now,
                }
            ],
        )
        return evidence


__all__ = ["ForwardCohortIdentity", "ForwardDailyEvidence", "ForwardEvidenceBuilder"]
