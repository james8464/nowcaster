"""Append-only persistence for authenticated contextual research evidence."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel
from sqlalchemy import insert, select

from src.contextual.allocation import ContextualAllocation, CovarianceEvidence
from src.contextual.eligibility import AssetEligibilityEvidence
from src.contextual.hierarchy import HierarchicalEstimate
from src.database.engine import Database
from src.database.schema import TABLES
from src.strategies.types import canonical_hash, canonical_json


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _plain(value.model_dump(mode="python"))
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_plain(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, StrEnum):
        return value.value
    return value


def _json_payload(value: Any) -> Any:
    return json.loads(canonical_json(_plain(value)))


def _utc_datetime(value: object, label: str) -> datetime:
    timestamp = pd.Timestamp(value).to_pydatetime()
    if timestamp.tzinfo is not UTC:
        raise ValueError(f"{label} must be an explicit UTC datetime")
    return timestamp


class ContextualRepository:
    """Single-writer, collision-detecting contextual evidence ledger."""

    def __init__(self, database: Database, *, clock: Callable[[], datetime] | None = None):
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is not UTC:
            raise ValueError("repository clock must return an explicit UTC datetime")
        return value

    def _common(self, *, created_at: datetime | None = None) -> dict[str, Any]:
        return {
            "source": "nowcaster_contextual",
            "source_version": "1",
            "created_at": created_at or self._now(),
        }

    def _append_rows(
        self,
        table_name: str,
        identity_column: str,
        rows: Sequence[Mapping[str, Any]],
    ) -> int:
        if not rows:
            return 0
        table = TABLES[table_name]
        identities = [str(row[identity_column]) for row in rows]
        if any(not value.strip() for value in identities) or len(identities) != len(set(identities)):
            raise ValueError(f"{table_name} identities must be nonblank and unique within a batch")
        with self.database.engine.begin() as connection:
            new_rows: list[Mapping[str, Any]] = []
            for row in rows:
                stored_hash = connection.execute(
                    select(table.c.content_hash).where(table.c[identity_column] == row[identity_column])
                ).scalar_one_or_none()
                if stored_hash is not None:
                    if str(stored_hash) != str(row["content_hash"]):
                        raise ValueError(f"{table_name} identity collision has a conflicting content hash")
                    continue
                new_rows.append(row)
            if new_rows:
                connection.execute(insert(table), list(new_rows))
        return len(new_rows)

    def append_authenticated_row(
        self,
        table_name: str,
        identity_column: str,
        row: Mapping[str, Any],
    ) -> int:
        """Append one already-shaped row while enforcing immutable identity/content binding."""

        payload = dict(row)
        if "content_hash" not in payload:
            evidence = payload.get("evidence", payload)
            payload["content_hash"] = canonical_hash(evidence)
        common = self._common()
        for key, value in common.items():
            payload.setdefault(key, value)
        return self._append_rows(table_name, identity_column, (payload,))

    def row_for_eligibility(self, evidence: AssetEligibilityEvidence) -> dict[str, Any]:
        payload = _json_payload(evidence)
        content_hash = canonical_hash(payload)
        return {
            "eligibility_id": evidence.evidence_id,
            "content_hash": content_hash,
            "policy_hash": evidence.policy_hash,
            "input_hash": evidence.input_hash,
            "provider": evidence.provider,
            "feed": evidence.feed,
            "venue": evidence.venue,
            "product": evidence.product,
            "asset_class": evidence.asset_class,
            "profile": evidence.profile.value,
            "symbol": evidence.symbol,
            "interval": evidence.interval.value,
            "direction": evidence.direction.value,
            "effective_at": evidence.as_of,
            "data_through": evidence.data_through,
            "state": evidence.state.value,
            "quality_score": evidence.quality_score,
            "source_event_watermark": evidence.source_event_watermark,
            "evidence": payload,
            **self._common(),
        }

    def append_eligibility(self, evidence: AssetEligibilityEvidence) -> int:
        return self._append_rows(
            "asset_eligibility_evidence",
            "eligibility_id",
            (self.row_for_eligibility(evidence),),
        )

    def row_for_outcome(self, outcome: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "dataset_hash",
            "protocol_hash",
            "code_hash",
            "config_hash",
            "source_decision_hash",
            "provider",
            "feed",
            "venue",
            "product",
            "asset_class",
            "profile",
            "symbol",
            "interval",
            "direction",
            "mode",
            "strategy_id",
            "decision_timestamp",
            "outcome_available_at",
            "gross_return",
            "modeled_cost",
            "net_return",
            "regime_probabilities",
        }
        missing = sorted(required - set(outcome))
        if missing:
            raise ValueError(f"contextual outcome missing fields: {', '.join(missing)}")
        decision_timestamp = _utc_datetime(outcome["decision_timestamp"], "decision_timestamp")
        outcome_available_at = _utc_datetime(outcome["outcome_available_at"], "outcome_available_at")
        if outcome_available_at < decision_timestamp:
            raise ValueError("contextual outcome cannot be available before its decision")
        gross_return = float(outcome["gross_return"])
        modeled_cost = float(outcome["modeled_cost"])
        net_return = float(outcome["net_return"])
        if not all(math.isfinite(value) for value in (gross_return, modeled_cost, net_return)) or modeled_cost < 0:
            raise ValueError("contextual returns and costs must be finite with nonnegative costs")
        if not math.isclose(gross_return - modeled_cost, net_return, rel_tol=0, abs_tol=1e-12):
            raise ValueError("contextual net return must equal gross return less modeled cost")
        probabilities = {str(key): float(value) for key, value in dict(outcome["regime_probabilities"]).items()}
        if (
            len(probabilities) != 4
            or any(not math.isfinite(value) or value < 0 or value > 1 for value in probabilities.values())
            or not math.isclose(sum(probabilities.values()), 1.0, rel_tol=0, abs_tol=1e-9)
        ):
            raise ValueError("contextual outcome regime probabilities must be a normalized four-state vector")
        evidence = _json_payload(outcome)
        identity = {
            key: evidence[key]
            for key in (
                "dataset_hash",
                "protocol_hash",
                "provider",
                "feed",
                "venue",
                "product",
                "symbol",
                "interval",
                "direction",
                "mode",
                "strategy_id",
                "decision_timestamp",
                "outcome_available_at",
            )
        }
        outcome_id = str(outcome.get("outcome_id") or canonical_hash(identity))
        return {
            "outcome_id": outcome_id,
            "content_hash": canonical_hash(evidence),
            "dataset_hash": str(outcome["dataset_hash"]),
            "protocol_hash": str(outcome["protocol_hash"]),
            "code_hash": str(outcome["code_hash"]),
            "config_hash": str(outcome["config_hash"]),
            "source_decision_hash": str(outcome["source_decision_hash"]),
            "provider": str(outcome["provider"]),
            "feed": str(outcome["feed"]),
            "venue": str(outcome["venue"]),
            "product": str(outcome["product"]),
            "asset_class": str(outcome["asset_class"]),
            "profile": str(outcome["profile"]),
            "symbol": str(outcome["symbol"]).upper(),
            "interval": str(outcome["interval"]),
            "direction": str(outcome["direction"]),
            "mode": str(outcome["mode"]),
            "strategy_id": str(outcome["strategy_id"]),
            "decision_timestamp": decision_timestamp,
            "outcome_available_at": outcome_available_at,
            "gross_return": gross_return,
            "modeled_cost": modeled_cost,
            "net_return": net_return,
            "regime_probabilities": probabilities,
            "evidence": evidence,
            **self._common(),
        }

    def append_outcome(self, outcome: Mapping[str, Any]) -> int:
        return self._append_rows(
            "contextual_outcomes",
            "outcome_id",
            (self.row_for_outcome(outcome),),
        )

    def append_outcome_rows(self, rows: Sequence[Mapping[str, Any]]) -> int:
        shaped = tuple(self.row_for_outcome(row) for row in rows)
        return self._append_rows("contextual_outcomes", "outcome_id", shaped)

    def row_for_estimate(
        self,
        estimate: HierarchicalEstimate,
        *,
        effective_at: datetime,
    ) -> dict[str, Any]:
        _utc_datetime(effective_at, "effective_at")
        payload = _json_payload(estimate)
        return {
            "estimate_id": estimate.estimate_id,
            "content_hash": canonical_hash(payload),
            "parent_estimate_id": estimate.parent_estimate_id,
            "dataset_hash": estimate.dataset_hash,
            "protocol_hash": estimate.protocol_hash,
            "provider": estimate.provider,
            "feed": estimate.feed,
            "venue": estimate.venue,
            "product": estimate.product,
            "asset_class": estimate.asset_class,
            "profile": estimate.profile.value if estimate.profile is not None else None,
            "symbol": estimate.symbol,
            "interval": estimate.interval.value,
            "direction": estimate.direction.value,
            "regime": estimate.regime.value if estimate.regime is not None else None,
            "mode": estimate.mode.value,
            "strategy_id": estimate.strategy_id,
            "level": estimate.level.value,
            "effective_at": effective_at,
            "evidence_through": estimate.evidence_through,
            "mean_net_edge": estimate.mean_net_edge,
            "lower_net_edge": estimate.lower_net_edge,
            "uncertainty": estimate.uncertainty,
            "effective_observations": estimate.effective_observations,
            "evidence": payload,
            **self._common(),
        }

    def append_estimates(
        self,
        estimates: Sequence[HierarchicalEstimate],
        *,
        effective_at: datetime,
    ) -> int:
        rows = tuple(self.row_for_estimate(item, effective_at=effective_at) for item in estimates)
        return self._append_rows("contextual_estimates", "estimate_id", rows)

    def row_for_covariance(
        self,
        covariance: CovarianceEvidence,
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        required = {
            "context_hash",
            "dataset_hash",
            "protocol_hash",
            "provider",
            "feed",
            "venue",
            "product",
            "profile",
            "symbol",
            "interval",
            "direction",
            "effective_at",
        }
        missing = sorted(required - set(context))
        if missing:
            raise ValueError(f"covariance context missing fields: {', '.join(missing)}")
        payload = _json_payload({"covariance": covariance, "context": context})
        return {
            "covariance_id": covariance.evidence_hash,
            "content_hash": canonical_hash(payload),
            "context_hash": str(context["context_hash"]),
            "dataset_hash": str(context["dataset_hash"]),
            "protocol_hash": str(context["protocol_hash"]),
            "provider": str(context["provider"]),
            "feed": str(context["feed"]),
            "venue": str(context["venue"]),
            "product": str(context["product"]),
            "profile": str(context["profile"]),
            "symbol": str(context["symbol"]).upper(),
            "interval": str(context["interval"]),
            "direction": str(context["direction"]),
            "effective_at": _utc_datetime(context["effective_at"], "effective_at"),
            "status": covariance.status,
            "observations": covariance.observations,
            "strategy_ids": list(covariance.strategy_ids),
            "matrix": [list(row) for row in covariance.matrix],
            "evidence": payload,
            **self._common(),
        }

    def append_covariance(self, covariance: CovarianceEvidence, context: Mapping[str, Any]) -> int:
        return self._append_rows(
            "contextual_covariances",
            "covariance_id",
            (self.row_for_covariance(covariance, context),),
        )

    def append_allocation(self, allocation: ContextualAllocation, context: Mapping[str, Any]) -> int:
        required = {
            "context_hash",
            "dataset_hash",
            "protocol_hash",
            "provider",
            "feed",
            "venue",
            "product",
            "profile",
            "symbol",
            "interval",
            "direction",
        }
        missing = sorted(required - set(context))
        if missing:
            raise ValueError(f"allocation context missing fields: {', '.join(missing)}")
        payload = _json_payload({"allocation": allocation, "context": context})
        rows = []
        for weight in allocation.weight_evidence:
            identity = canonical_hash({"allocation_id": allocation.allocation_id, "strategy_id": weight.strategy_id})
            row = {
                "contextual_weight_id": identity,
                "content_hash": canonical_hash({"payload": payload, "strategy_id": weight.strategy_id}),
                "allocation_id": allocation.allocation_id,
                "context_hash": str(context["context_hash"]),
                "dataset_hash": str(context["dataset_hash"]),
                "protocol_hash": str(context["protocol_hash"]),
                "provider": str(context["provider"]),
                "feed": str(context["feed"]),
                "venue": str(context["venue"]),
                "product": str(context["product"]),
                "profile": str(context["profile"]),
                "symbol": str(context["symbol"]).upper(),
                "interval": str(context["interval"]),
                "direction": str(context["direction"]),
                "strategy_id": weight.strategy_id,
                "family": weight.family.value,
                "effective_at": allocation.as_of,
                "weight": weight.weight,
                "cash_weight": allocation.cash_weight,
                "evidence": payload,
                **self._common(),
            }
            rows.append(row)
        return self._append_rows("contextual_weights", "contextual_weight_id", tuple(rows))

    def append_regime_posterior(self, record: Mapping[str, Any]) -> int:
        required = {
            "model_hash",
            "dataset_hash",
            "protocol_hash",
            "provider",
            "feed",
            "venue",
            "product",
            "asset_class",
            "profile",
            "symbol",
            "interval",
            "decision_timestamp",
            "feature_through",
            "status",
            "probabilities",
        }
        missing = sorted(required - set(record))
        if missing:
            raise ValueError(f"regime posterior missing fields: {', '.join(missing)}")
        decision = _utc_datetime(record["decision_timestamp"], "decision_timestamp")
        feature_through = _utc_datetime(record["feature_through"], "feature_through")
        training_value = record.get("training_through")
        training_through = _utc_datetime(training_value, "training_through") if training_value is not None else None
        if feature_through > decision or (training_through is not None and training_through > decision):
            raise ValueError("regime evidence cannot follow its decision timestamp")
        probabilities = {str(key): float(value) for key, value in dict(record["probabilities"]).items()}
        expected = {
            "trend_normal",
            "trend_elevated_volatility",
            "range_liquid",
            "stressed_or_illiquid",
        }
        if (
            set(probabilities) != expected
            or any(not math.isfinite(value) or value < 0 or value > 1 for value in probabilities.values())
            or not math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-9)
        ):
            raise ValueError("regime posterior must contain normalized fixed-taxonomy probabilities")
        payload = _json_payload(record)
        identity = {
            key: payload[key]
            for key in (
                "model_hash",
                "provider",
                "feed",
                "product",
                "symbol",
                "interval",
                "decision_timestamp",
            )
        }
        posterior_id = str(record.get("posterior_id") or canonical_hash(identity))
        row = {
            "posterior_id": posterior_id,
            "content_hash": canonical_hash(payload),
            "model_hash": str(record["model_hash"]),
            "dataset_hash": str(record["dataset_hash"]),
            "protocol_hash": str(record["protocol_hash"]),
            "provider": str(record["provider"]),
            "feed": str(record["feed"]),
            "venue": str(record["venue"]),
            "product": str(record["product"]),
            "asset_class": str(record["asset_class"]),
            "profile": str(record["profile"]),
            "symbol": str(record["symbol"]).upper(),
            "interval": str(record["interval"]),
            "decision_timestamp": decision,
            "feature_through": feature_through,
            "training_through": training_through,
            "status": str(record["status"]),
            "probabilities": probabilities,
            "evidence": payload,
            **self._common(),
        }
        return self._append_rows("regime_posteriors", "posterior_id", (row,))

    def append_portfolio_decision(self, record: Mapping[str, Any]) -> int:
        required = {
            "selection_id",
            "decision_hash",
            "context_hash",
            "symbol",
            "direction",
            "effective_at",
            "status",
            "selected",
            "weight",
            "exclusion_reasons",
        }
        missing = sorted(required - set(record))
        if missing:
            raise ValueError(f"portfolio decision missing fields: {', '.join(missing)}")
        effective_at = _utc_datetime(record["effective_at"], "effective_at")
        weight = float(record["weight"])
        if not math.isfinite(weight) or not 0 <= weight <= 1:
            raise ValueError("portfolio decision weight must be finite and in [0, 1]")
        selected = bool(record["selected"])
        reasons = tuple(str(value) for value in record["exclusion_reasons"])
        if selected and reasons:
            raise ValueError("a selected portfolio decision cannot have exclusion reasons")
        if not selected and weight != 0:
            raise ValueError("an excluded portfolio decision must have zero weight")
        payload = _json_payload(record)
        identity = {
            "selection_id": str(record["selection_id"]),
            "decision_hash": str(record["decision_hash"]),
        }
        row = {
            "portfolio_decision_id": str(record.get("portfolio_decision_id") or canonical_hash(identity)),
            "content_hash": canonical_hash(payload),
            "selection_id": identity["selection_id"],
            "decision_hash": identity["decision_hash"],
            "context_hash": str(record["context_hash"]),
            "symbol": str(record["symbol"]).upper(),
            "direction": str(record["direction"]),
            "effective_at": effective_at,
            "status": str(record["status"]),
            "selected": selected,
            "weight": weight,
            "exclusion_reasons": list(reasons),
            "evidence": payload,
            **self._common(),
        }
        return self._append_rows(
            "portfolio_research_decisions",
            "portfolio_decision_id",
            (row,),
        )

    def append_learning_trial(self, record: Mapping[str, Any]) -> int:
        required = {
            "global_trial_id",
            "dataset_hash",
            "protocol_hash",
            "candidate_hash",
            "ordinal",
            "evaluated_at",
            "status",
            "definition",
        }
        missing = sorted(required - set(record))
        if missing:
            raise ValueError(f"contextual learning trial missing fields: {', '.join(missing)}")
        ordinal = int(record["ordinal"])
        if ordinal < 1:
            raise ValueError("contextual trial ordinal must be positive")
        evaluated_at = _utc_datetime(record["evaluated_at"], "evaluated_at")
        payload = _json_payload(record)
        global_trial_id = str(record["global_trial_id"])
        row = {
            "contextual_trial_id": str(record.get("contextual_trial_id") or canonical_hash(global_trial_id)),
            "content_hash": canonical_hash(payload),
            "global_trial_id": global_trial_id,
            "dataset_hash": str(record["dataset_hash"]),
            "protocol_hash": str(record["protocol_hash"]),
            "candidate_hash": str(record["candidate_hash"]),
            "ordinal": ordinal,
            "evaluated_at": evaluated_at,
            "status": str(record["status"]),
            "definition": _json_payload(record["definition"]),
            "evidence": payload,
            **self._common(),
        }
        return self._append_rows(
            "contextual_learning_trials",
            "contextual_trial_id",
            (row,),
        )

    def append_drift_event(self, record: Mapping[str, Any]) -> int:
        required = {"context_hash", "effective_at", "status", "reason"}
        missing = sorted(required - set(record))
        if missing:
            raise ValueError(f"contextual drift event missing fields: {', '.join(missing)}")
        effective_at = _utc_datetime(record["effective_at"], "effective_at")
        reason = str(record["reason"]).strip()
        if not reason:
            raise ValueError("contextual drift reason cannot be blank")
        payload = _json_payload(record)
        identity = {
            "context_hash": str(record["context_hash"]),
            "effective_at": effective_at,
            "status": str(record["status"]),
            "reason": reason,
        }
        row = {
            "drift_event_id": str(record.get("drift_event_id") or canonical_hash(identity)),
            "content_hash": canonical_hash(payload),
            "context_hash": identity["context_hash"],
            "effective_at": effective_at,
            "status": identity["status"],
            "reason": reason,
            "evidence": payload,
            **self._common(),
        }
        return self._append_rows("contextual_drift_events", "drift_event_id", (row,))

    def append_learning_trial_event(self, record: Mapping[str, Any]) -> int:
        """Append an outcome event without mutating the pre-evaluation trial reservation."""

        required = {"global_trial_id", "status", "rung", "evaluated_at"}
        missing = sorted(required - set(record))
        if missing:
            raise ValueError(f"contextual trial event missing fields: {', '.join(missing)}")
        global_trial_id = str(record["global_trial_id"])
        if not self.database.scalar(
            "select count(*) from contextual_learning_trials where global_trial_id = :identity",
            {"identity": global_trial_id},
        ):
            raise ValueError("contextual trial must be reserved before appending evaluation events")
        status = str(record["status"])
        if status not in {"duplicate", "succeeded", "failed", "interrupted", "halved", "shadow"}:
            raise ValueError("contextual trial event status is invalid")
        rung = int(record["rung"])
        if rung < 0:
            raise ValueError("contextual trial event rung cannot be negative")
        evaluated_at = _utc_datetime(record["evaluated_at"], "evaluated_at")
        fitness = record.get("fitness")
        if fitness is not None and not math.isfinite(float(fitness)):
            raise ValueError("contextual trial event fitness must be finite")
        payload = _json_payload(record)
        content_hash = canonical_hash(payload)
        row = {
            "trial_event_id": canonical_hash(
                {"global_trial_id": global_trial_id, "status": status, "rung": rung, "content_hash": content_hash}
            ),
            "content_hash": content_hash,
            "global_trial_id": global_trial_id,
            "status": status,
            "rung": rung,
            "evaluated_at": evaluated_at,
            "fitness": float(fitness) if fitness is not None else None,
            "evidence": payload,
            **self._common(),
        }
        return self._append_rows("contextual_learning_trial_events", "trial_event_id", (row,))


__all__ = ["ContextualRepository"]
