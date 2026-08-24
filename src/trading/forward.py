from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from src.database.engine import Database
from src.database.schema import TABLES
from src.strategies.types import canonical_hash


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

    @property
    def cohort_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


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
    ) -> ForwardDailyEvidence:
        status = (
            "complete"
            if reconciliation_mismatches == 0
            and health_breakers == 0
            and paper_net_return is not None
            and stressed_net_return is not None
            and drawdown is not None
            else "unavailable"
        )
        payload = {
            "cohort_hash": cohort.cohort_hash,
            "period_start": period_start,
            "period_end": period_end,
            "closed_trades": closed_trades,
            "paper_net_return": str(paper_net_return) if paper_net_return is not None else None,
            "stressed_net_return": str(stressed_net_return) if stressed_net_return is not None else None,
            "drawdown": str(drawdown) if drawdown is not None else None,
            "reconciliation_mismatches": reconciliation_mismatches,
            "health_breakers": health_breakers,
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
                    table.c.cohort_hash == cohort.cohort_hash,
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
                    "forward_evidence_id": canonical_hash((cohort.cohort_hash, period_start, period_end)),
                    "cohort_hash": cohort.cohort_hash,
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
