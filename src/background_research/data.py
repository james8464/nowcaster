"""Read immutable receipt prefixes and replay each phase without retrospective data."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd

from src.background_research.contracts import LearningBatch, LearningCampaign
from src.background_research.registry import _source, _validate_source
from src.research.round_two_contracts import ResearchRoundProtocol, RoundObservation, _utc
from src.research.round_two_quality import QualitySummary
from src.research.round_two_walkforward import _phase_inputs
from src.strategies.types import canonical_hash


@dataclass(frozen=True, slots=True)
class LearningSource:
    observations: tuple[RoundObservation, ...]
    observation_identities: tuple[str, ...]
    data_fingerprint: str
    through: datetime | None


@dataclass(frozen=True, slots=True)
class LearningPhase:
    start: datetime
    end: datetime
    observations: tuple[RoundObservation, ...]
    coverage: Decimal
    reasons: tuple[str, ...]
    gaps: tuple[tuple[datetime, datetime], ...]


@dataclass(frozen=True, slots=True)
class EligibleLearningData:
    protocol: ResearchRoundProtocol
    observations: tuple[RoundObservation, ...]
    observation_identities: tuple[str, ...]
    data_fingerprint: str
    training: LearningPhase
    validation: LearningPhase
    holdout: LearningPhase


def _read_prefix(campaign, *, now, fingerprint=None):
    protocol = _source(campaign)
    _validate_source(campaign, protocol)
    now = _utc(now, "now")
    path = campaign.source_directory / "observations.jsonl"
    if path.is_symlink():
        raise ValueError("observation ledger cannot link to another source")
    raw = path.read_bytes() if path.exists() else b""
    if raw and not raw.endswith(b"\n"):
        raise ValueError("torn observation ledger prefix")
    digest = hashlib.sha256()
    observations, identities, keys = [], [], set()
    matched = fingerprint == digest.hexdigest()
    for line in raw.splitlines(keepends=True):
        if fingerprint is not None and matched:
            break
        row = RoundObservation.model_validate_json(line).validate_for(protocol)
        if row.source_key in keys:
            raise ValueError("duplicate observation identity in retained prefix")
        if row.available_at > now:
            break
        keys.add(row.source_key)
        observations.append(row)
        identities.append(canonical_hash(row.model_dump(mode="json")))
        digest.update(line)
        matched = digest.hexdigest() == fingerprint
    if fingerprint is not None and not matched:
        raise ValueError("original observation prefix fingerprint is unavailable or replaced")
    through = min(now, max(row.provider_at for row in observations) + timedelta(minutes=1)) if observations else None
    return protocol, LearningSource(tuple(observations), tuple(identities), digest.hexdigest(), through)


def read_learning_source(campaign: LearningCampaign, *, now: datetime) -> LearningSource:
    """Fingerprint exact retained bytes, including quality failures, before scheduling."""
    return _read_prefix(campaign.validated(), now=now)[1]


def _phase(protocol, observations, symbol, start, end):
    quality = QualitySummary(protocol=protocol, observations=observations)
    rows, coverage, reasons = _phase_inputs(protocol, observations, quality, symbol, start, end)
    # A receipt cannot be replayed before an earlier retained receipt. Keep the
    # offending evidence, but do not execute out-of-order availability.
    clean, prior = [], None
    for row in rows:
        if prior is not None and row.available_at < prior:
            reasons = tuple(sorted(set(reasons) | {"availability_regression"}))
            continue
        clean.append(row)
        prior = row.available_at
    coverage = Decimal(len({row.provider_at for row in clean})) / Decimal(int((end - start).total_seconds() / 60))
    if coverage < protocol.minimum_coverage:
        reasons = tuple(sorted(set(reasons) | {"coverage_below_minimum"}))
    gaps, cursor = [], start
    for row in clean:
        if row.provider_at > cursor:
            gaps.append((cursor, row.provider_at))
        cursor = row.provider_at + timedelta(minutes=1)
    if cursor < end:
        gaps.append((cursor, end))
    return LearningPhase(start, end, tuple(clean), coverage, reasons, tuple(gaps))


def load_learning_data(campaign: LearningCampaign, batch: LearningBatch, *, now: datetime) -> EligibleLearningData:
    campaign, batch = campaign.validated(), batch.validated()
    if batch.campaign_hash != campaign.identity_hash or batch.symbol not in campaign.symbols:
        raise ValueError("batch does not belong to the registered campaign")
    now = _utc(now, "now")
    if now < batch.created_at:
        raise ValueError("batch observations are unavailable before batch creation")
    protocol, source = _read_prefix(campaign, now=batch.created_at, fingerprint=batch.data_fingerprint)
    return EligibleLearningData(
        protocol,
        source.observations,
        source.observation_identities,
        source.data_fingerprint,
        _phase(protocol, source.observations, batch.symbol, batch.training_start, batch.training_end),
        _phase(protocol, source.observations, batch.symbol, batch.training_end, batch.validation_end),
        _phase(protocol, source.observations, batch.symbol, batch.validation_end, batch.holdout_end),
    )


def evaluate_retained_payload(payload):
    """Use the existing receipt-causal long-only simulator and twice-cost stress."""
    from src.deep_research.evaluation import CandidatePathEvidence
    from src.research.round_two_runtime import _strategy_registry
    from src.research.round_two_walkforward import INITIAL_CASH, _signals, _simulate
    from src.strategies.indicators import rolling_zscore, rsi
    from src.strategies.registry import RegisteredStrategy

    retained = payload.retained_input
    protocol = retained.protocol
    rows = retained.observations
    if not rows:
        raise ValueError("no available retained observations")
    if any(
        not payload.evaluation_start <= row.provider_at < payload.evaluation_end
        or row.available_at >= payload.evaluation_end
        for row in rows
    ):
        raise ValueError("retained evaluation inputs cross the phase boundary")
    spec = payload.base_spec.model_copy(
        update={"parameters": dict(payload.candidate.parameters) or dict(payload.base_spec.parameters)}
    )
    registered = _strategy_registry().resolve(spec.strategy_id)
    if spec.version != retained.round_candidate.strategy_version or "1m" not in spec.intervals:
        raise ValueError("strategy definition does not match the retained source")

    def rule_signal(spec, frame, context):
        frame["rsi"] = rsi(frame["close"], min(14, max(2, spec.warmup_bars)))
        frame["volume_zscore"] = rolling_zscore(frame["volume"], min(20, max(3, spec.warmup_bars)))
        active = payload.candidate.rule.evaluate(frame).fillna(False).astype(bool)
        return pd.DataFrame(
            {
                "decision_timestamp": frame["available_at"],
                "data_through": frame["available_at"],
                "signal": active.astype(int),
            }
        )

    item = RegisteredStrategy(spec, rule_signal if payload.candidate.rule is not None else registered.generator)
    signals = _signals(rows, protocol, item, spec, abstain=retained.round_candidate.direction == "abstain")
    normal = _simulate(rows, signals, protocol, multiplier=1, candidate=retained.round_candidate)
    stress = _simulate(rows, signals, protocol, multiplier=2, candidate=retained.round_candidate)
    metrics = normal.model_copy(
        update={
            "coverage": retained.coverage,
            "stressed_net_return": stress.net_return,
            "lower_edge": stress.lower_edge,
            "maximum_drawdown": max(normal.maximum_drawdown, stress.maximum_drawdown),
            "reasons": tuple(sorted(set(normal.reasons + stress.reasons + retained.reasons))),
        }
    )
    # Convert realized account changes to returns on preceding equity so worker
    # compounding reproduces the existing replay's fixed-cash account exactly.
    # Include its conservative boundary mark, without inventing a closed trade.
    gross, costs, timed_net = [], [], []
    equity, realized_gross = INITIAL_CASH, Decimal(0)
    for trade in normal.trades:
        gross.append(float(trade.gross_pnl / equity))
        cost = trade.fees + trade.spread_cost + trade.slippage_cost
        costs.append(float(cost / equity))
        timed_net.append((trade.exit_at, float(trade.net_pnl / equity)))
        equity += trade.net_pnl
        realized_gross += trade.gross_pnl
    residual_net = INITIAL_CASH * (1 + normal.net_return) - equity
    residual_gross = INITIAL_CASH * normal.gross_return - realized_gross
    if residual_net or residual_gross or not gross:
        gross.append(float(residual_gross / equity))
        costs.append(float((residual_gross - residual_net) / equity))
        timed_net.append((rows[-1].available_at, float(residual_net / equity)))
    folds = tuple(
        tuple(value for at, value in timed_net if start <= at < end) or (0.0,) for start, end in payload.fold_ranges
    )
    return CandidatePathEvidence(
        folds,
        tuple(gross),
        tuple(costs),
        normal.trade_count,
        metrics,
        tuple((row.available_at, signal) for row, signal in zip(rows, signals, strict=True)),
    )
