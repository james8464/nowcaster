"""Fixed comparisons of hypothetical lifecycles, never executable fill returns.

Callers supply the complete retained lifecycle revision history. No model is fit:
each expanding training window is recorded solely to identify the chronological
test boundary. Intervals describe daily-block sample uncertainty under independence;
serial dependence and selection across earlier research rounds remain limitations.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import mean, stdev
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from scipy.stats import t

from src.research.day_trader_lifecycle import PaperLifecycle, advance_lifecycle
from src.research.round_two_contracts import _utc
from src.research.round_two_registry import _write_first_manifest, append_jsonl_fsync, jsonl_writer_lock
from src.strategies.types import canonical_hash, canonical_json

D = Decimal
HASH = r"^[0-9a-f]{64}$"


class _Immutable(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class FixedVariant(_Immutable):
    variant_id: str = Field(min_length=1, max_length=128)
    candidate_hash: str = Field(pattern=HASH)
    maximum_holding_seconds: int = Field(default=3600, ge=60, le=86400)


class EvaluationProtocol(_Immutable):
    """Register before test collection; changes require a separate directory/round."""

    source_protocol_hash: str = Field(pattern=HASH)
    context_protocol_hash: str = Field(pattern=HASH)
    registered_at: datetime
    training_end: datetime
    evaluation_end: datetime
    fold_days: int = Field(default=7, ge=1, le=365)
    variants: tuple[FixedVariant, ...] = Field(min_length=1, max_length=100)
    fee_bps: Decimal = Field(default=D("10"), ge=0, le=1000)
    slippage_bps: Decimal = Field(default=D("5"), ge=0, le=1000)
    minimum_outcomes: int = Field(default=100, ge=2, le=1000000)
    minimum_active_days: int = Field(default=20, ge=2, le=10000)
    maximum_drawdown_bps: Decimal = Field(default=D("1000"), gt=0, le=10000)
    maximum_daily_turnover: Decimal = Field(default=D("20"), gt=0, le=100000)
    family_alpha: float = Field(default=0.05, ge=0.001, le=0.1)
    schema_version: Literal[1] = 1

    @field_validator("registered_at", "training_end", "evaluation_end")
    @classmethod
    def utc(cls, value):
        return _utc(value, "evaluation time")

    @model_validator(mode="after")
    def coherent(self):
        if not self.registered_at < self.training_end < self.evaluation_end:
            raise ValueError("evaluation registration must precede training and test boundaries")
        if self.evaluation_end - self.training_end > timedelta(days=3650):
            raise ValueError("evaluation window exceeds bounded horizon")
        keys = {(v.candidate_hash, v.maximum_holding_seconds) for v in self.variants}
        if len(keys) != len(self.variants) or len({v.variant_id for v in self.variants}) != len(self.variants):
            raise ValueError("fixed variants must have unique names and identities")
        return self

    @property
    def identity_hash(self):
        return canonical_hash(self.model_dump(mode="json"))


class OutcomeEvaluation(_Immutable):
    lifecycle_hash: str = Field(pattern=HASH)
    record_hash: str = Field(pattern=HASH)
    created_at: datetime
    completed_at: datetime | None
    fold_index: int | None
    net_return_bps: Decimal | None
    exclusions: tuple[str, ...]


class FoldEvaluation(_Immutable):
    index: int
    training_end: datetime
    test_start: datetime
    test_end: datetime
    scored_outcomes: int
    mean_return_bps: Decimal | None


class VariantEvaluation(_Immutable):
    protocol_hash: str = Field(pattern=HASH)
    evaluated_at: datetime
    variant: FixedVariant
    status: Literal["diagnostic_only"] = "diagnostic_only"
    paper_only: Literal[True] = True
    outcomes: tuple[OutcomeEvaluation, ...] = Field(max_length=1000000)
    folds: tuple[FoldEvaluation, ...] = Field(max_length=3650)
    scored_outcomes: int = Field(ge=0)
    active_days: int = Field(ge=0)
    mean_return_bps: Decimal | None
    daily_mean_return_bps: Decimal | None
    mean_lower_bps: Decimal | None
    mean_upper_bps: Decimal | None
    adjusted_alpha: float = Field(gt=0, lt=1)
    maximum_drawdown_bps: Decimal = Field(ge=0)
    maximum_daily_turnover: Decimal = Field(ge=0)
    exclusions: tuple[str, ...]
    result_hash: str = Field(pattern=HASH)

    @model_validator(mode="after")
    def identity(self):
        if self.result_hash != canonical_hash(self.model_dump(mode="json", exclude={"result_hash"})):
            raise ValueError("evaluation result hash mismatch")
        return self


def _retained(protocol, lifecycles):
    latest = {}
    decisions = set()
    declared = {(v.candidate_hash, v.maximum_holding_seconds) for v in protocol.variants}
    for raw in lifecycles:
        item = PaperLifecycle.model_validate(raw.model_dump())
        origin = item.origin_report
        if origin.protocol_hash != protocol.source_protocol_hash:
            raise ValueError("evaluation source protocol mismatch")
        if origin.context.context_protocol_hash != protocol.context_protocol_hash:
            raise ValueError("evaluation context protocol mismatch")
        if (origin.suggestion.candidate_hash, item.maximum_holding_seconds) not in declared:
            raise ValueError("unregistered lifecycle candidate or holding policy")
        previous = latest.get(item.lifecycle_hash)
        if previous is None:
            if item.revision != 0:
                raise ValueError("initial lifecycle revision missing")
            decision = (origin.report_hash, item.maximum_holding_seconds)
            if decision in decisions:
                raise ValueError("duplicate decision lifecycle")
            decisions.add(decision)
        elif (
            previous.completed_at is not None
            or item.revision != previous.revision + 1
            or item.previous_record_hash != previous.record_hash
            or advance_lifecycle(previous, item.last_observation) != item
        ):
            raise ValueError("lifecycle transition mismatch")
        latest[item.lifecycle_hash] = item
    return tuple(sorted(latest.values(), key=lambda v: (v.created_at, v.lifecycle_hash)))


def _score(item, protocol, windows, evaluated_at):
    reasons, fold = [], None
    completed = item.completed_at
    if item.created_at > evaluated_at or (completed is not None and completed > evaluated_at):
        reasons.append("outcome_not_yet_known")
    if completed is None:
        reasons.append("incomplete")
    elif completed <= protocol.training_end:
        reasons.append("training_only")
    elif item.created_at < protocol.training_end:
        reasons.append("crosses_fold_boundary")
    elif item.created_at >= protocol.evaluation_end or completed >= protocol.evaluation_end:
        reasons.append("outside_evaluation_window")
    else:
        for index, (start, end) in enumerate(windows):
            if start <= item.created_at < end:
                if completed >= end:
                    reasons.append("crosses_fold_boundary")
                else:
                    fold = index
                break
    if item.exit_reason == "expired":
        reasons.append("unpriced_expiry")
    net = None
    if not reasons:
        bar = item.last_observation.bar
        if (
            bar is None
            or bar.close is None
            or not bar.close.is_finite()
            or bar.provider_error is not None
            or bar.provider_at > item.deadline
            or item.last_observation.evaluated_at - bar.provider_at > timedelta(seconds=15)
        ):
            reasons.append("terminal_price_unavailable")
        else:
            hypothesis = item.origin_report.suggestion
            # The upper zone is the conservative entry hypothesis. For a stop,
            # include a possible gap below the invalidation using the bar open
            # only when the whole candle occurred after lifecycle creation.
            if item.exit_reason == "invalidation":
                whole_bar = bar.provider_at - timedelta(minutes=1) >= item.created_at
                eligible_open = bar.open if whole_bar and bar.open is not None else bar.close
                terminal = min(hypothesis.invalidation, bar.close, eligible_open)
            elif item.exit_reason == "target":
                terminal = hypothesis.target
            else:
                terminal = bar.close
            net = (terminal / hypothesis.entry_high - 1) * 10000 - 2 * (protocol.fee_bps + protocol.slippage_bps)
    return OutcomeEvaluation(
        lifecycle_hash=item.lifecycle_hash,
        record_hash=item.record_hash,
        created_at=item.created_at,
        completed_at=completed,
        fold_index=fold,
        net_return_bps=net,
        exclusions=tuple(reasons),
    )


def evaluate_variants(
    protocol: EvaluationProtocol, lifecycles, variants, *, evaluated_at: datetime | None = None
) -> tuple[VariantEvaluation, ...]:
    """Score every registered variant without fitting, ranking or promoting any.

    Supply full histories (``LifecycleLedger.events()``), never selected winners
    or latest-only revisions. Closed lifecycles crossing a fold are purged.
    Daily block means estimate uncertainty; return proxy/drawdown/turnover are
    unscaled hypothetical statistics, not a portfolio or asserted executions.
    """
    protocol = EvaluationProtocol.model_validate(protocol.model_dump())
    evaluated_at = _utc(evaluated_at or datetime.now(UTC), "evaluation timestamp")
    variants = tuple(FixedVariant.model_validate(v.model_dump()) for v in variants)
    if variants != protocol.variants:
        raise ValueError("must retain all registered variants in registration order")
    retained = _retained(protocol, lifecycles)
    windows, start = [], protocol.training_end
    while start < protocol.evaluation_end:
        end = min(start + timedelta(days=protocol.fold_days), protocol.evaluation_end)
        windows.append((start, end))
        start = end
    adjusted = protocol.family_alpha / (len(variants) * len(windows))
    results = []
    for variant in variants:
        outcomes = tuple(
            _score(item, protocol, windows, evaluated_at)
            for item in retained
            if (
                item.origin_report.suggestion.candidate_hash == variant.candidate_hash
                and item.maximum_holding_seconds == variant.maximum_holding_seconds
            )
        )
        scored = sorted(
            (o for o in outcomes if o.net_return_bps is not None), key=lambda o: (o.completed_at, o.lifecycle_hash)
        )
        values = [o.net_return_bps for o in scored]
        daily, turnover = defaultdict(list), defaultdict(int)
        for outcome in outcomes:
            for at in (outcome.created_at, outcome.completed_at):
                if at is not None and protocol.training_end <= at < protocol.evaluation_end and at <= evaluated_at:
                    turnover[at.date()] += 1
        cumulative = peak = drawdown = D(0)
        for outcome in scored:
            daily[outcome.completed_at.date()].append(outcome.net_return_bps)
            cumulative += outcome.net_return_bps
            peak = max(peak, cumulative)
            drawdown = max(drawdown, peak - cumulative)
        blocks = [float(mean(v)) for v in daily.values()]
        lower = upper = None
        if len(blocks) >= 2:
            center = mean(blocks)
            error = float(t.ppf(1 - adjusted / 2, len(blocks) - 1)) * stdev(blocks) / len(blocks) ** 0.5
            lower, upper = D(str(center - error)), D(str(center + error))
        reasons = [
            "hypothetical_unfilled_returns",
            "dependent_outcomes_uncertainty",
            "earlier_selection_rounds_unadjusted",
            "registration_timestamp_not_independently_verified",
        ]
        if len(values) < protocol.minimum_outcomes:
            reasons.append("insufficient_outcomes")
        if len(blocks) < protocol.minimum_active_days:
            reasons.append("insufficient_active_days")
        if any(o.exclusions for o in outcomes):
            reasons.append("unscored_outcomes_retained")
        if drawdown > protocol.maximum_drawdown_bps:
            reasons.append("drawdown_exceeded")
        max_turnover = max(turnover.values(), default=0)
        if max_turnover > protocol.maximum_daily_turnover:
            reasons.append("turnover_exceeded")
        if lower is None or lower <= 0:
            reasons.append("lower_edge_not_positive")
        folds = []
        for index, (start, end) in enumerate(windows):
            fold_values = [o.net_return_bps for o in scored if o.fold_index == index]
            folds.append(
                FoldEvaluation(
                    index=index,
                    training_end=start,
                    test_start=start,
                    test_end=end,
                    scored_outcomes=len(fold_values),
                    mean_return_bps=mean(fold_values) if fold_values else None,
                )
            )
        payload = dict(
            protocol_hash=protocol.identity_hash,
            evaluated_at=evaluated_at,
            variant=variant,
            outcomes=outcomes,
            folds=tuple(folds),
            scored_outcomes=len(values),
            active_days=len(blocks),
            mean_return_bps=mean(values) if values else None,
            daily_mean_return_bps=mean([mean(v) for v in daily.values()]) if daily else None,
            mean_lower_bps=lower,
            mean_upper_bps=upper,
            adjusted_alpha=adjusted,
            maximum_drawdown_bps=drawdown,
            maximum_daily_turnover=D(max_turnover),
            exclusions=tuple(reasons),
        )
        unsigned = VariantEvaluation.model_construct(**payload, result_hash="0" * 64)
        wire = unsigned.model_dump(mode="json", exclude={"result_hash"})
        results.append(VariantEvaluation.model_validate({**wire, "result_hash": canonical_hash(wire)}))
    return tuple(results)


class EvaluationLedger:
    """Append immutable evaluation batches and reject rewritten outcome evidence."""

    def __init__(self, directory: Path, protocol: EvaluationProtocol):
        self.directory = Path(directory)
        self.protocol = EvaluationProtocol.model_validate(protocol.model_dump())
        self.directory.mkdir(parents=True, exist_ok=True)
        with jsonl_writer_lock(self.events_path):
            self._manifest()
            self._read()

    @property
    def events_path(self):
        return self.directory / "day-trader-evaluations.jsonl"

    def _manifest(self):
        manifest = self.directory / "day-trader-evaluation-protocol.json"
        payload = (canonical_json(self.protocol.model_dump(mode="json")) + "\n").encode()
        if self.events_path.exists() and not manifest.exists():
            raise ValueError("evaluation history has no manifest")
        if not _write_first_manifest(manifest, payload) and manifest.read_bytes() != payload:
            raise ValueError("evaluation protocol mismatch")

    def _check(self, batch, history, previous):
        if tuple(r.variant for r in batch) != self.protocol.variants or any(
            r.protocol_hash != self.protocol.identity_hash for r in batch
        ):
            raise ValueError("evaluation variants/protocol mismatch")
        if previous and history[: len(previous[-1][1])] != previous[-1][1]:
            raise ValueError("evaluation evidence cannot be omitted or rewritten")
        if len({r.evaluated_at for r in batch}) != 1:
            raise ValueError("evaluation timestamps disagree")
        if previous and batch[0].evaluated_at < previous[-1][0][0].evaluated_at:
            raise ValueError("evaluation time cannot go backwards")
        if batch != evaluate_variants(
            self.protocol, history, self.protocol.variants, evaluated_at=batch[0].evaluated_at
        ):
            raise ValueError("evaluation does not match recomputed evidence")

    def _read(self):
        if not self.events_path.exists():
            return ()
        data = self.events_path.read_bytes()
        if data and not data.endswith(b"\n"):
            raise ValueError("unterminated evaluation history")
        batches = []
        for line in data.splitlines():
            row = json.loads(line)
            if set(row) != {"results", "history"}:
                raise ValueError("invalid evaluation batch")
            batch = tuple(VariantEvaluation.model_validate(v) for v in row["results"])
            history = tuple(PaperLifecycle.model_validate(v) for v in row["history"])
            self._check(batch, history, batches)
            batches.append((batch, history))
        return tuple(batches)

    def evaluations(self):
        with jsonl_writer_lock(self.events_path):
            self._manifest()
            return tuple(batch for batch, _ in self._read())

    def append(self, results, lifecycles):
        batch = tuple(VariantEvaluation.model_validate(v.model_dump()) for v in results)
        history = tuple(PaperLifecycle.model_validate(v.model_dump()) for v in lifecycles)
        with jsonl_writer_lock(self.events_path):
            self._manifest()
            previous = self._read()
            if (batch, history) in previous:
                return
            self._check(batch, history, previous)
            append_jsonl_fsync(
                self.events_path,
                [
                    {
                        "results": [v.model_dump(mode="json") for v in batch],
                        "history": [v.model_dump(mode="json") for v in history],
                    }
                ],
                writer_lock_held=True,
            )
