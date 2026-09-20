"""Fixed, paper-only minute replay with durable, consume-before-read test seals.

HistoricalReplay's hourly archive envelope cannot represent receipt-time minute
quotes. This adapter follows its reveal/decision/next-observation/account phases;
it never coerces minute data into that frozen archive contract. Window endpoints
are exclusive. No position or pending decision crosses a phase boundary.
"""

from __future__ import annotations

import hashlib
import inspect
import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from itertools import product
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from scipy.stats import t as student_t

from src.research.historical_replay_account import LOT_INCREMENTS
from src.research.round_two_contracts import ResearchRoundProtocol, RoundCandidate, RoundObservation, RoundStatus
from src.research.round_two_quality import QualitySummary, _intrinsic_reason
from src.research.round_two_registry import (
    _write_first_manifest,
    append_jsonl_fsync,
    jsonl_writer_lock,
    load_round_protocol,
)
from src.strategies.library import StrategyContext
from src.strategies.registry import RegisteredStrategy, StrategyRegistry
from src.strategies.types import ImmutableParameters, StrategySpec, canonical_hash, canonical_json

D = Decimal
MINUTE = timedelta(minutes=1)
INITIAL_CASH = D("10000")
RECEIPTS_FILE = "sealed-test-receipts.jsonl"


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WalkForwardFold(Record):
    fold_id: str
    train_start: datetime
    train_end: datetime
    validation_end: datetime
    test_end: datetime


class SealedTestReceipt(Record):
    round_hash: str
    fold_id: str
    sealed_at: datetime
    selection_hash: str = ""
    selections: tuple[dict, ...] = ()
    state: Literal["consumed"] = "consumed"


class SimulatedTrade(Record):
    decision_at: datetime
    entry_at: datetime
    exit_at: datetime
    exit_reason: Literal["signal", "stop_loss", "target"] = "signal"
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    net_pnl: Decimal
    gross_pnl: Decimal
    fees: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal


class EvaluationMetrics(Record):
    gross_return: Decimal = D(0)
    net_return: Decimal = D(0)
    stressed_net_return: Decimal = D(0)
    lower_edge: Decimal | None = None
    trade_count: int = 0
    maximum_drawdown: Decimal = D(0)
    fees: Decimal = D(0)
    spread_cost: Decimal = D(0)
    slippage_cost: Decimal = D(0)
    open_quantity: Decimal = D(0)
    coverage: Decimal = D(0)
    reasons: tuple[str, ...] = ()
    trades: tuple[SimulatedTrade, ...] = ()


class TrainingTrial(Record):
    parameters: ImmutableParameters
    metrics: EvaluationMetrics


class FoldResult(Record):
    fold: WalkForwardFold
    selected_parameters: ImmutableParameters = Field(default_factory=dict)
    training_trials: tuple[TrainingTrial, ...] = ()
    train: EvaluationMetrics = Field(default_factory=EvaluationMetrics)
    validation: EvaluationMetrics = Field(default_factory=EvaluationMetrics)
    sealed_test: EvaluationMetrics = Field(default_factory=EvaluationMetrics)
    baselines: dict[str, EvaluationMetrics] = Field(default_factory=dict)
    status: RoundStatus
    reasons: tuple[str, ...] = ()
    receipt: SealedTestReceipt | None = None


class CandidateResult(Record):
    candidate: RoundCandidate
    # Last chronological fold; earlier selections and every trial remain in folds.
    selected_parameters: ImmutableParameters = Field(default_factory=dict)
    train: EvaluationMetrics = Field(default_factory=EvaluationMetrics)
    validation: EvaluationMetrics = Field(default_factory=EvaluationMetrics)
    sealed_test: EvaluationMetrics = Field(default_factory=EvaluationMetrics)
    folds: tuple[FoldResult, ...] = ()
    status: RoundStatus
    reasons: tuple[str, ...] = ()
    paper_only: Literal[True] = True
    qualification_status: Literal["unqualified"] = "unqualified"


def walk_forward_folds(protocol: ResearchRoundProtocol, through: datetime) -> tuple[WalkForwardFold, ...]:
    protocol = protocol.validated()
    if through.tzinfo is None or through.utcoffset() != timedelta(0):
        raise ValueError("through must be explicit UTC")
    schedule = protocol.schedule
    start = schedule.starts_at
    folds = []
    while True:
        train_end = start + timedelta(days=schedule.train_days)
        validation_end = train_end + timedelta(days=schedule.validation_days)
        test_end = validation_end + timedelta(days=schedule.sealed_test_days)
        if test_end > through:
            return tuple(folds)
        folds.append(
            WalkForwardFold(
                fold_id=validation_end.isoformat(),
                train_start=start,
                train_end=train_end,
                validation_end=validation_end,
                test_end=test_end,
            )
        )
        start += timedelta(days=schedule.step_days)


def causal_finalized_slice(
    observations: Sequence[RoundObservation],
    start: datetime,
    end: datetime,
) -> tuple[RoundObservation, ...]:
    """Retain arrival order and only bars finalized/available inside this phase."""
    return tuple(
        item
        for item in observations
        if start <= item.provider_at < end
        and item.available_at < end
        and item.close is not None
        and item.provider_error is None
    )


def _receipts(directory: Path) -> tuple[SealedTestReceipt, ...]:
    path = directory / RECEIPTS_FILE
    if not path.exists():
        return ()
    return tuple(SealedTestReceipt.model_validate_json(line) for line in path.read_text().splitlines())


def persist_sealed_test_receipt(
    directory: Path,
    *,
    round_hash: str,
    fold_id: str,
    selection_hash: str = "",
    selections: tuple[dict, ...] = (),
) -> SealedTestReceipt:
    """Atomically consume a protocol/fold before its outcomes can be inspected.

    A crash burns the receipt; there is deliberately no retry/reset operation.
    """
    directory = Path(directory)
    with jsonl_writer_lock(directory / RECEIPTS_FILE):
        if any(row.round_hash == round_hash and row.fold_id == fold_id for row in _receipts(directory)):
            raise ValueError("sealed test already evaluated")
        receipt = SealedTestReceipt(
            round_hash=round_hash,
            fold_id=fold_id,
            sealed_at=datetime.now(UTC),
            selection_hash=canonical_hash(selections) if selections else selection_hash,
            selections=selections,
        )
        append_jsonl_fsync(directory / RECEIPTS_FILE, [receipt.model_dump(mode="json")], writer_lock_held=True)
    return receipt


def _bind_evaluation(directory: Path, protocol: ResearchRoundProtocol, registry: StrategyRegistry) -> None:
    if load_round_protocol(directory).identity_hash != protocol.identity_hash:
        raise ValueError("protocol identity does not match retained round")
    # Registry generators can be identical wrappers around different rules.
    # Bind the entire static strategy package, including indicator/session/pair
    # helpers, plus the generator's defining module for custom registrations.
    strategy_root = Path(inspect.getfile(StrategySpec)).resolve().parent
    strategy_sources = {
        path.relative_to(strategy_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(strategy_root.rglob("*.py"))
    }
    definitions = {}
    for candidate in protocol.candidates:
        try:
            item = registry.resolve(candidate.strategy_id)
            definitions[candidate.strategy_id] = {
                "spec": item.spec.model_dump(mode="json"),
                "generator": hashlib.sha256(inspect.getsource(item.generator).encode()).hexdigest(),
                "generator_module": hashlib.sha256(
                    Path(inspect.getsourcefile(item.generator)).read_bytes()
                ).hexdigest(),
            }
        except KeyError:
            definitions[candidate.strategy_id] = None
    payload = (
        canonical_json(
            {
                "protocol_hash": protocol.identity_hash,
                "strategies": definitions,
                "strategy_sources": strategy_sources,
                "evaluator": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            }
        )
        + "\n"
    )
    path = directory / "evaluation-manifest.json"
    if not _write_first_manifest(path, payload.encode()) and path.read_text() != payload:
        raise ValueError("evaluation identity changed; register a new round")


def _phase_inputs(protocol, observations, quality, symbol, start, end):
    # Quality evidence cannot be hidden by passing only a favorable eligible subset.
    evidence = tuple(
        item
        for item in quality.observations
        if item.symbol == symbol and start <= item.provider_at < end and item.available_at < end
    )
    rows = causal_finalized_slice(tuple(item for item in observations if item.symbol == symbol), start, end)
    reasons = set()
    last = None
    clean = []
    for item in evidence:
        intrinsic = _intrinsic_reason(item, protocol)
        if intrinsic:
            reasons.add(intrinsic)
        if item.available_at - item.provider_at > timedelta(seconds=protocol.maximum_observation_age_seconds):
            reasons.add("observation_stale")
        elif last is not None and item.provider_at <= last:
            reasons.add("clock_regression")
        elif intrinsic is None:
            clean.append(item)
        last = max(last, item.provider_at) if last else item.provider_at
    keys = {item.source_key for item in clean}
    rows = tuple(item for item in rows if item.source_key in keys)
    coverage = D(len({item.provider_at for item in rows})) / D(int((end - start) / MINUTE))
    if coverage < protocol.minimum_coverage:
        reasons.add("coverage_below_minimum")
    if not rows:
        reasons.add("no_available_observations")
    elif rows[-1].provider_at - rows[0].provider_at < timedelta(minutes=protocol.warmup_minutes):
        reasons.add("continuity_warmup")
    return rows, coverage, tuple(sorted(reasons))


def _parameter_specs(candidate: RoundCandidate, registered: RegisteredStrategy) -> tuple[StrategySpec, ...]:
    if not registered.spec.enabled or registered.spec.version != candidate.strategy_version:
        raise ValueError("strategy_definition_mismatch")
    if "1m" not in registered.spec.intervals:
        raise ValueError("strategy_interval_unsupported")
    options = dict(candidate.parameters)
    keys = sorted(options)
    values = [value if isinstance(value, tuple) else (value,) for value in (options[key] for key in keys)]
    if any(not value for value in values) or math.prod(map(len, values)) > 256:
        raise ValueError("invalid_parameter_grid")
    return tuple(
        StrategySpec.model_validate(
            {
                **registered.spec.model_dump(mode="python"),
                "parameters": {**registered.spec.parameters, **dict(zip(keys, choices, strict=True))},
            }
        )
        for choices in product(*values)
    )


def _signals(rows, protocol, item, spec, *, baseline=False, abstain=False):
    history = []
    signals = []
    last = None
    for row in rows:
        if last is not None and row.provider_at != last + MINUTE:
            history = []
        last = row.provider_at
        history.append(
            {
                "provider": row.provider,
                "feed": row.feed,
                "symbol": row.symbol,
                "interval": "1m",
                "open_timestamp": row.provider_at - MINUTE,
                "close_timestamp": row.provider_at,
                "available_at": row.available_at,
                "finalized": True,
                "revision": 1,
                **{
                    key: float(getattr(row, key)) if getattr(row, key) is not None else float("nan")
                    for key in ("open", "high", "low", "close", "volume")
                },
            }
        )
        history = history[-protocol.maximum_feature_bars :]
        warm = len(history) > protocol.warmup_minutes and len(history) >= spec.warmup_bars
        if not warm or abstain:
            signals.append(0)
            continue
        if baseline:
            signals.append(1)
            continue
        frame = pd.DataFrame(history)
        generated = item.generator(spec, frame.copy(), StrategyContext.for_market(row.provider, row.feed))
        if len(generated) != len(frame):
            raise ValueError("strategy_signal_shape")
        signal = generated.iloc[-1]
        decision = pd.Timestamp(signal["decision_timestamp"])
        through = pd.Timestamp(signal["data_through"])
        if (
            decision.tzinfo is None
            or through.tzinfo is None
            or decision != row.available_at
            or through > row.available_at
            or pd.isna(through)
        ):
            raise ValueError("strategy_unavailable_data")
        if signal["signal"] not in (-1, 0, 1):
            raise ValueError("strategy_invalid_signal")
        signals.append(1 if signal["signal"] == 1 else 0)
    return signals


def _simulate(rows, signals, protocol, *, multiplier, candidate=None):
    cash = peak = INITIAL_CASH
    quantity = D(0)
    pending = entry = None
    trades, reasons, daily = [], set(), {}
    fees = spread_cost = slippage_cost = drawdown = D(0)
    gross_pnl = D(0)
    fee = protocol.fee_bps / 10000 * multiplier
    slip = protocol.slippage_bps / 10000 * multiplier
    equity = cash
    unrealized_gross = D(0)
    for index, row in enumerate(rows):
        at = row.available_at
        gap = index > 0 and row.provider_at != rows[index - 1].provider_at + MINUTE
        if gap:
            if pending is not None and pending[0] == 1:
                pending = None
            if quantity:
                reasons.add("gap_with_open_position")
        executable = row.bid is not None and row.ask is not None and row.volume is not None and row.volume > 0
        if pending is not None and at >= pending[1] + timedelta(milliseconds=protocol.latency_ms):
            desired, decision_at, exit_reason = pending
            # Failed entries may be reconsidered; triggered exits must survive
            # missing liquidity, gaps, and subsequent price/signal recovery.
            if desired == 1:
                pending = None
            if not executable:
                reasons.add("execution_unavailable")
            else:
                midpoint = (row.bid + row.ask) / 2
                half = (row.ask - row.bid) / 2 * multiplier
                buy = desired == 1
                reference = midpoint + half if buy else midpoint - half
                price = reference * (1 + slip if buy else 1 - slip)
                limit = row.volume * protocol.maximum_volume_participation
                if buy and not quantity:
                    lot = LOT_INCREMENTS[row.symbol]
                    units = (
                        min(
                            limit,
                            cash / (price * (1 + fee)),
                            INITIAL_CASH * protocol.maximum_initial_cash_exposure / (price * (1 + fee)),
                        )
                        / lot
                    ).to_integral_value(rounding=ROUND_DOWN) * lot
                    if units > 0:
                        debit = units * price * (1 + fee)
                        entry = (
                            decision_at,
                            at,
                            price,
                            midpoint,
                            debit,
                            units * price * fee,
                            units * half,
                            units * reference * slip,
                        )
                        cash -= debit
                        quantity = units
                        fees += entry[5]
                        spread_cost += entry[6]
                        slippage_cost += entry[7]
                elif not buy and quantity:
                    if quantity > limit:
                        reasons.add("participation_limit")
                    else:
                        credit = quantity * price * (1 - fee)
                        pnl = credit - entry[4]
                        gross = quantity * (midpoint - entry[3])
                        exit_fee, exit_spread, exit_slip = (
                            quantity * price * fee,
                            quantity * half,
                            quantity * reference * slip,
                        )
                        trades.append(
                            SimulatedTrade(
                                decision_at=entry[0],
                                entry_at=entry[1],
                                exit_at=at,
                                exit_reason=exit_reason,
                                entry_price=entry[2],
                                exit_price=price,
                                quantity=quantity,
                                net_pnl=pnl,
                                gross_pnl=gross,
                                fees=entry[5] + exit_fee,
                                spread_cost=entry[6] + exit_spread,
                                slippage_cost=entry[7] + exit_slip,
                            )
                        )
                        cash += credit
                        fees += exit_fee
                        spread_cost += exit_spread
                        slippage_cost += exit_slip
                        gross_pnl += gross
                        daily[at.date()] = daily.get(at.date(), D(0)) + pnl / INITIAL_CASH
                        quantity, entry = D(0), None
                        pending = None
        if quantity:
            # Missing quotes cannot improve equity: conservatively keep the prior
            # mark, and withhold eligibility until executable evidence returns.
            if executable:
                midpoint = (row.bid + row.ask) / 2
                liquidation = (midpoint - (row.ask - row.bid) / 2 * multiplier) * (1 - slip) * (1 - fee)
                equity = cash + quantity * liquidation
                unrealized_gross = quantity * (midpoint - entry[3])
            else:
                reasons.add("execution_unavailable")
        else:
            equity = cash
            unrealized_gross = D(0)
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak)
        desired = signals[index]
        if desired != int(quantity > 0) and pending is None:
            pending = (desired, at, "signal")
        if quantity and candidate is not None and at > entry[1]:
            # A finalized range can establish a crossing, not its intrabar order
            # or a historical fill. Queue the stop first and use a later quote.
            low = row.low if row.low is not None else row.close
            high = row.high if row.high is not None else row.close
            stopped = low <= entry[2] * (1 - candidate.stop_loss_bps / 10000)
            targeted = high >= entry[2] * (1 + candidate.target_bps / 10000)
            if (stopped or targeted) and (pending is None or pending[2] == "signal"):
                pending = (0, pending[1] if pending is not None else at, "stop_loss" if stopped else "target")
    if quantity:
        reasons.add("open_position_at_boundary")
    lower = None
    if len(daily) >= 2:
        values = pd.Series([float(value) for value in daily.values()])
        lower = D(
            str(values.mean() - student_t.ppf(0.975, len(values) - 1) * values.std(ddof=1) / math.sqrt(len(values)))
        )
    return EvaluationMetrics(
        gross_return=(gross_pnl + unrealized_gross) / INITIAL_CASH,
        net_return=(equity - INITIAL_CASH) / INITIAL_CASH,
        lower_edge=lower,
        trade_count=len(trades),
        maximum_drawdown=drawdown,
        fees=fees,
        spread_cost=spread_cost,
        slippage_cost=slippage_cost,
        open_quantity=quantity,
        trades=tuple(trades),
        reasons=tuple(sorted(reasons)),
    )


def _replay(rows, coverage, protocol, item, spec, *, baseline=False, abstain=False, candidate=None):
    signals = _signals(rows, protocol, item, spec, baseline=baseline, abstain=abstain)
    normal = _simulate(rows, signals, protocol, multiplier=1, candidate=candidate)
    stress = _simulate(rows, signals, protocol, multiplier=2, candidate=candidate)
    return normal.model_copy(
        update={
            "coverage": coverage,
            "stressed_net_return": stress.net_return,
            "lower_edge": stress.lower_edge,
            "maximum_drawdown": max(normal.maximum_drawdown, stress.maximum_drawdown),
            "reasons": tuple(sorted(set(normal.reasons + stress.reasons))),
        }
    )


def _gate(metrics, protocol, prefix):
    reasons = set(metrics.reasons)
    if metrics.lower_edge is None or metrics.lower_edge <= protocol.minimum_stressed_lower_edge:
        reasons.add(f"{prefix}_lower_edge")
    if metrics.trade_count < protocol.minimum_closed_trades:
        reasons.add(f"{prefix}_insufficient_trades")
    if metrics.maximum_drawdown > protocol.maximum_drawdown:
        reasons.add(f"{prefix}_drawdown")
    return reasons


def _aggregate(metrics):
    if not metrics:
        return EvaluationMetrics()
    # Equal initial capital per independent replay; returns are arithmetic means,
    # not an investable concatenated account. Fold evidence remains authoritative.
    return EvaluationMetrics(
        **{
            key: sum((getattr(x, key) for x in metrics), D(0)) / len(metrics)
            for key in ("gross_return", "net_return", "stressed_net_return", "coverage")
        },
        **{
            key: sum((getattr(x, key) for x in metrics), D(0))
            for key in ("fees", "spread_cost", "slippage_cost", "open_quantity")
        },
        lower_edge=min(x.lower_edge for x in metrics) if all(x.lower_edge is not None for x in metrics) else None,
        trade_count=sum(x.trade_count for x in metrics),
        maximum_drawdown=max(x.maximum_drawdown for x in metrics),
        trades=tuple(trade for x in metrics for trade in x.trades),
        reasons=tuple(sorted({reason for x in metrics for reason in x.reasons})),
    )


def _retained_folds(directory, protocol, folds):
    """Reuse complete results only after validating their original consumed seal."""
    receipts = _receipts(directory)
    if not receipts:
        return {}
    by_fold = {receipt.fold_id: receipt for receipt in receipts}
    expected = {fold.fold_id: fold for fold in folds}
    if len(by_fold) != len(receipts) or any(
        receipt.round_hash != protocol.identity_hash
        or receipt.fold_id not in expected
        or receipt.selection_hash != canonical_hash(receipt.selections)
        for receipt in receipts
    ):
        raise ValueError("sealed receipt identity does not match retained round")
    path = directory / "candidate-results.jsonl"
    if not path.is_file():
        raise ValueError("sealed test has no complete retained results")
    candidates = {canonical_hash(item.model_dump(mode="json")): index for index, item in enumerate(protocol.candidates)}
    retained = {}
    for line in path.read_text().splitlines():
        candidate_result = CandidateResult.model_validate_json(line)
        identity = canonical_hash(candidate_result.candidate.model_dump(mode="json"))
        if identity not in candidates:
            raise ValueError("sealed receipt identity has an unknown candidate")
        index = candidates[identity]
        for result in candidate_result.folds:
            receipt = by_fold.get(result.fold.fold_id)
            if receipt is None:
                continue
            selection = next(
                (item for item in receipt.selections if canonical_hash(item["candidate"]) == identity), None
            )
            if (
                result.fold != expected[receipt.fold_id]
                or (
                    selection is not None
                    and (result.receipt != receipt or dict(result.selected_parameters) != selection["parameters"])
                )
                or (selection is None and result.receipt is not None)
            ):
                raise ValueError("sealed receipt identity does not match retained result")
            key = (receipt.fold_id, index)
            if key in retained and retained[key] != result:
                raise ValueError("sealed receipt identity has conflicting retained results")
            retained[key] = result
    if any((fold_id, index) not in retained for fold_id in by_fold for index in range(len(protocol.candidates))):
        raise ValueError("sealed test has no complete retained results")
    return retained


def validate_retained_candidate_results(
    directory: Path, protocol: ResearchRoundProtocol
) -> tuple[CandidateResult, ...]:
    """Read-only validation before a retained result can be displayed as experimental.

    The evaluator writes these records, but a report must independently bind an
    experimental label back to the immutable candidate, consumed receipt,
    selected parameters, fold identity, and simulated trade ledger.  A missing
    or malformed record is evidence of an unavailable result, not a reason to
    trust a stored status string.
    """
    protocol = protocol.validated()
    path = Path(directory) / "candidate-results.jsonl"
    if not path.is_file():
        return ()
    lines = path.read_text().splitlines()
    if any(not line for line in lines):
        raise ValueError("candidate_results_corrupt")
    try:
        rows = tuple(CandidateResult.model_validate_json(line) for line in lines)
    except ValueError as error:
        raise ValueError("candidate_results_corrupt") from error
    candidates = {canonical_hash(item.model_dump(mode="json")): item for item in protocol.candidates}
    latest: dict[str, CandidateResult] = {}
    for result in rows:
        identity = canonical_hash(result.candidate.model_dump(mode="json"))
        if identity not in candidates:
            raise ValueError("candidate_evidence_missing")
        latest[identity] = result
    receipts = _receipts(Path(directory))
    receipt_by_fold = {receipt.fold_id: receipt for receipt in receipts}
    if len(receipt_by_fold) != len(receipts) or any(
        receipt.round_hash != protocol.identity_hash or receipt.selection_hash != canonical_hash(receipt.selections)
        for receipt in receipts
    ):
        raise ValueError("candidate_evidence_missing")
    for identity, result in latest.items():
        if result.status != RoundStatus.EXPERIMENTAL_PAPER_ONLY:
            continue
        if not result.folds:
            raise ValueError("candidate_evidence_missing")
        retained_folds: set[str] = set()
        for fold_result in result.folds:
            receipt = receipt_by_fold.get(fold_result.fold.fold_id)
            if (
                receipt is None
                or fold_result.receipt != receipt
                or fold_result.status != RoundStatus.EXPERIMENTAL_PAPER_ONLY
                or fold_result.sealed_test.trade_count != len(fold_result.sealed_test.trades)
            ):
                raise ValueError("candidate_evidence_missing")
            selection = next(
                (
                    item
                    for item in receipt.selections
                    if canonical_hash(item.get("candidate")) == identity
                ),
                None,
            )
            if selection is None or dict(fold_result.selected_parameters) != selection.get("parameters"):
                raise ValueError("candidate_evidence_missing")
            retained_folds.add(receipt.fold_id)
        selected_receipts = {
            receipt.fold_id
            for receipt in receipts
            if any(canonical_hash(item.get("candidate")) == identity for item in receipt.selections)
        }
        if retained_folds != selected_receipts:
            raise ValueError("candidate_evidence_missing")
    return tuple(
        latest[canonical_hash(candidate.model_dump(mode="json"))]
        for candidate in protocol.candidates
        if canonical_hash(candidate.model_dump(mode="json")) in latest
    )


def evaluate_round(
    protocol: ResearchRoundProtocol,
    observations: Sequence[RoundObservation],
    quality: QualitySummary,
    registry: StrategyRegistry,
    *,
    directory: Path,
) -> tuple[CandidateResult, ...]:
    """Select on train, decide eligibility on validation, then consume each test once."""
    protocol, directory = protocol.validated(), Path(directory)
    if quality.protocol.identity_hash != protocol.identity_hash:
        raise ValueError("quality protocol identity mismatch")
    retained = {}
    for observation in quality.observations:
        observation.validate_for(protocol)
        if observation.source_key in retained:
            raise ValueError("duplicate source key in quality evidence")
        retained[observation.source_key] = observation
    for observation in observations:
        if retained.get(observation.source_key) != observation:
            raise ValueError("observations must match retained quality evidence")
    if len({item.source_key for item in observations}) != len(observations):
        raise ValueError("duplicate eligible source key")
    _bind_evaluation(directory, protocol, registry)
    horizon = max((item.provider_at + MINUTE for item in quality.observations), default=protocol.schedule.starts_at)
    folds = walk_forward_folds(protocol, horizon)
    if not folds:
        incomplete = tuple(
            CandidateResult(candidate=candidate, status=RoundStatus.INSUFFICIENT_DATA, reasons=("incomplete_fold",))
            for candidate in protocol.candidates
        )
        append_jsonl_fsync(directory / "candidate-results.jsonl", [x.model_dump(mode="json") for x in incomplete])
        return incomplete
    cached = _retained_folds(directory, protocol, folds)
    results: list[list[FoldResult]] = [[] for _ in protocol.candidates]
    for fold in folds:
        if (fold.fold_id, 0) in cached:
            for index in range(len(protocol.candidates)):
                results[index].append(cached[fold.fold_id, index])
            continue
        prepared = []
        for index, candidate in enumerate(protocol.candidates):
            windows = [
                _phase_inputs(protocol, observations, quality, candidate.symbol, start, end)
                for start, end in (
                    (fold.train_start, fold.train_end),
                    (fold.train_end, fold.validation_end),
                    (fold.validation_end, fold.test_end),
                )
            ]
            exclusions = tuple(sorted({reason for _, _, reasons in windows for reason in reasons}))
            if exclusions:
                results[index].append(FoldResult(fold=fold, status=RoundStatus.INSUFFICIENT_DATA, reasons=exclusions))
                continue
            trials = ()
            try:
                item = registry.resolve(candidate.strategy_id)
                if max(item.spec.warmup_bars, protocol.warmup_minutes + 1) > protocol.maximum_feature_bars:
                    results[index].append(
                        FoldResult(fold=fold, status=RoundStatus.REJECTED, reasons=("feature_cap_below_warmup",))
                    )
                    continue
                specs = _parameter_specs(candidate, item)
                trials_list = []
                for spec in specs:
                    try:
                        metrics = _replay(
                            *windows[0][:2],
                            protocol,
                            item,
                            spec,
                            abstain=candidate.direction == "abstain",
                            candidate=candidate,
                        )
                    except Exception:
                        metrics = EvaluationMetrics(reasons=("training_trial_failed",))
                    trials_list.append(TrainingTrial(parameters=spec.parameters, metrics=metrics))
                trials = tuple(trials_list)
                valid_trials = [
                    i for i, trial in enumerate(trials) if "training_trial_failed" not in trial.metrics.reasons
                ]
                if not valid_trials:
                    results[index].append(
                        FoldResult(
                            fold=fold,
                            training_trials=trials,
                            status=RoundStatus.REJECTED,
                            reasons=("strategy_evaluation_failed", "training_trial_failed"),
                        )
                    )
                    continue
                best = max(valid_trials, key=lambda i: (trials[i].metrics.stressed_net_return, -i))
                spec, train = specs[best], trials[best].metrics
                validation = _replay(
                    *windows[1][:2], protocol, item, spec, abstain=candidate.direction == "abstain", candidate=candidate
                )
                reasons = _gate(train, protocol, "train") | _gate(validation, protocol, "validation")
                if len(valid_trials) != len(trials):
                    reasons.add("training_trial_failed")
                prepared.append((index, candidate, item, spec, windows, trials, train, validation, reasons))
            except Exception:
                results[index].append(
                    FoldResult(
                        fold=fold,
                        training_trials=trials,
                        status=RoundStatus.REJECTED,
                        reasons=("strategy_evaluation_failed",),
                    )
                )
        if not prepared:
            continue
        receipt = persist_sealed_test_receipt(
            directory,
            round_hash=protocol.identity_hash,
            fold_id=fold.fold_id,
            selections=tuple(
                {
                    "candidate": row[1].model_dump(mode="json"),
                    "parameters": dict(row[3].parameters),
                    "reasons": sorted(row[8]),
                }
                for row in prepared
            ),
        )
        for index, candidate, item, spec, windows, trials, train, validation, reasons in prepared:
            try:
                sealed = _replay(
                    *windows[2][:2], protocol, item, spec, abstain=candidate.direction == "abstain", candidate=candidate
                )
                hold = _replay(*windows[2][:2], protocol, item, spec, baseline=True)
                cash = EvaluationMetrics(coverage=windows[2][1])
                baselines = {
                    "hold": hold,
                    "cash": cash,
                    "direction_matched": hold if candidate.direction == "long" else cash,
                }
                reasons |= _gate(sealed, protocol, "sealed_test")
                result = FoldResult(
                    fold=fold,
                    selected_parameters=spec.parameters,
                    training_trials=trials,
                    train=train,
                    validation=validation,
                    sealed_test=sealed,
                    baselines=baselines,
                    reasons=tuple(sorted(reasons)),
                    receipt=receipt,
                    status=RoundStatus.REJECTED if reasons else RoundStatus.EXPERIMENTAL_PAPER_ONLY,
                )
            except Exception:
                result = FoldResult(
                    fold=fold,
                    selected_parameters=spec.parameters,
                    training_trials=trials,
                    train=train,
                    validation=validation,
                    receipt=receipt,
                    status=RoundStatus.REJECTED,
                    reasons=tuple(sorted(reasons | {"sealed_evaluation_failed"})),
                )
            append_jsonl_fsync(
                directory / "sealed-test-results.jsonl",
                [{"candidate": candidate.model_dump(mode="json"), "result": result.model_dump(mode="json")}],
            )
            results[index].append(result)
    output = []
    for candidate, candidate_folds in zip(protocol.candidates, results, strict=True):
        reasons = tuple(sorted({reason for result in candidate_folds for reason in result.reasons}))
        status = (
            RoundStatus.INSUFFICIENT_DATA
            if any(x.status == RoundStatus.INSUFFICIENT_DATA for x in candidate_folds)
            else RoundStatus.REJECTED
            if reasons
            else RoundStatus.EXPERIMENTAL_PAPER_ONLY
        )
        output.append(
            CandidateResult(
                candidate=candidate,
                folds=tuple(candidate_folds),
                status=status,
                reasons=reasons,
                selected_parameters=candidate_folds[-1].selected_parameters,
                **{
                    phase: _aggregate([getattr(x, phase) for x in candidate_folds])
                    for phase in ("train", "validation", "sealed_test")
                },
            )
        )
    append_jsonl_fsync(directory / "candidate-results.jsonl", [x.model_dump(mode="json") for x in output])
    return tuple(output)
