from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import Field, model_validator

from src.database.engine import Database
from src.live_monitor.engine import EligibilityEvidence
from src.live_monitor.types import BarIntervalValue, Direction, LiveMonitorModel, MarketBar, MarketQuote
from src.strategies.library import StrategyContext, generate_signals
from src.strategies.types import StrategySpec, canonical_hash

EMPTY_COHORT_HASH = "0" * 64
REQUIRED_READINESS_GATES = frozenset(
    {
        "causal_integrity",
        "cohort_integrity",
        "minimum_forward_observations",
        "operational_integrity",
        "positive_paper_edge",
        "robustness",
        "stressed_net_edge",
    }
)
LIVE_READINESS_POLICY = {
    "minimum_equity_sessions": 60,
    "minimum_crypto_days": 90,
    "minimum_closed_trades": 100,
    "minimum_bootstrap_probability": "0.95",
    "minimum_deflated_sharpe_probability": "0.95",
    "maximum_pbo": "0.40",
    "minimum_parameter_stability": "0.70",
    "maximum_slippage_model_error": "0.20",
    "receipt_hours": 24,
}
LIVE_ALERT_POLICY = {
    "maximum_age_seconds": 30,
    "minimum_probability": "0.55",
    "minimum_vote_margin": "0.20",
    "minimum_breadth": 2,
    "equity_session": "XNYS_regular_only",
    "equity_shortability": "shortable_and_easy_to_borrow",
}


class ActiveReadinessGate(LiveMonitorModel):
    name: str
    passed: bool
    detail: str


class ActiveReadinessReceipt(LiveMonitorModel):
    receipt_id: str
    cohort_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    gates: tuple[ActiveReadinessGate, ...]
    issued_at: datetime
    expires_at: datetime

    def valid_at(self, instant: datetime, *, cohort_hash: str) -> bool:
        return (
            self.issued_at <= instant < self.expires_at
            and cohort_hash == self.cohort_hash
            and bool(self.gates)
            and all(gate.passed for gate in self.gates)
            and {gate.name for gate in self.gates} == REQUIRED_READINESS_GATES
        )


class SealedComponent(LiveMonitorModel):
    spec: StrategySpec
    strategy_version: str
    weight: Decimal = Field(gt=0, le=1)
    promoted: bool
    causal_audit_passed: bool
    calibration_method: Literal["development_only_beta_binomial_v1"]
    calibration_observations: int = Field(ge=5)
    calibration_successes: int = Field(ge=0)
    expected_edge: Decimal = Field(ge=0)
    expected_cost: Decimal = Field(ge=0)
    uncertainty: Decimal = Field(ge=0)
    model_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    robustness_evidence: dict[str, object]

    @model_validator(mode="after")
    def configured_version_matches(self) -> SealedComponent:
        if not self.strategy_version.strip():
            raise ValueError("strategy version is required")
        if self.calibration_successes > self.calibration_observations:
            raise ValueError("calibration successes cannot exceed observations")
        receipt_payload = self.robustness_evidence.get("receipt_payload")
        receipt_hash = self.robustness_evidence.get("receipt_hash")
        evidence_hash = self.robustness_evidence.get("evidence_hash")
        if (
            not isinstance(receipt_payload, dict)
            or not isinstance(receipt_hash, str)
            or canonical_hash(receipt_payload) != receipt_hash
            or not isinstance(evidence_hash, str)
            or canonical_hash({key: value for key, value in self.robustness_evidence.items() if key != "evidence_hash"})
            != evidence_hash
        ):
            raise ValueError("component robustness evidence is not authenticated")
        return self


class SealedCohort(LiveMonitorModel):
    cohort_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str
    feed: str
    dataset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol: str
    interval: BarIntervalValue
    mode: Literal["frozen", "paper"]
    cost_buffer_multiplier: Decimal = Field(gt=0)
    components: tuple[SealedComponent, ...] = Field(min_length=1, max_length=100)

    @property
    def evidence_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


def _bar_frame(bars: tuple[MarketBar, ...]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "provider": item.provider,
                "feed": item.feed,
                "symbol": item.symbol,
                "interval": item.interval,
                "open_timestamp": item.start,
                "close_timestamp": item.end,
                "available_at": item.available_at,
                # Strategy ledgers use one-based revision ordinals; provider streams use zero for originals.
                "revision": item.revision + 1,
                "finalized": item.finalized,
                "open": float(item.open),
                "high": float(item.high),
                "low": float(item.low),
                "close": float(item.close),
                "volume": float(item.volume),
            }
            for item in bars
        ]
    )


def evaluate_sealed_cohort(
    cohort: SealedCohort,
    bars: tuple[MarketBar, ...],
    quote: MarketQuote,
    *,
    shortable: bool | None = None,
    easy_to_borrow: bool | None = None,
) -> EligibilityEvidence:
    reasons: list[str] = []
    scoped = tuple(
        item
        for item in bars
        if (item.provider, item.feed, item.symbol, item.interval)
        == (cohort.provider, cohort.feed, cohort.symbol, cohort.interval)
    )
    if not scoped:
        reasons.append("live_warmup_incomplete")
        data_through = quote.provider_time
    else:
        data_through = scoped[-1].end
    frame = _bar_frame(scoped)
    weighted_vote = Decimal(0)
    active_mass = Decimal(0)
    breadth = 0
    versions_match = True
    all_promoted = True
    all_causal = True
    for component in cohort.components:
        all_promoted = all_promoted and component.promoted
        all_causal = all_causal and component.causal_audit_passed
        if component.strategy_version != component.spec.deterministic_version:
            versions_match = False
            reasons.append("strategy_version_mismatch")
            continue
        if cohort.interval not in {item.value for item in component.spec.intervals}:
            reasons.append("strategy_interval_mismatch")
            continue
        if len(scoped) < component.spec.warmup_bars:
            reasons.append("live_warmup_incomplete")
            continue
        try:
            current = generate_signals(
                component.spec,
                frame,
                StrategyContext.for_market(cohort.provider, cohort.feed),
            ).iloc[-1]
            signal = int(current.signal)
            strength = Decimal(str(float(current.strength)))
        except (IndexError, KeyError, TypeError, ValueError):
            reasons.append("current_signal_unavailable")
            continue
        if signal not in {-1, 0, 1} or not strength.is_finite():
            reasons.append("current_signal_unavailable")
            continue
        if signal == 0:
            continue
        bounded_strength = min(max(strength, Decimal(0)), Decimal(1))
        weighted_vote += component.weight * bounded_strength * Decimal(signal)
        active_mass += component.weight
        breadth += 1
    if active_mass > 0:
        live_direction = Direction.LONG if weighted_vote > 0 else Direction.SHORT
        vote_margin = abs(weighted_vote) / active_mass
    else:
        # Direction is required by the typed evidence envelope, but this branch is
        # always non-actionable because it carries no_current_signal and zero breadth.
        live_direction = Direction.LONG
        vote_margin = Decimal(0)
        reasons.append("no_current_signal")
    if breadth < 2:
        reasons.append("minimum_breadth")
    active = [
        item
        for item in cohort.components
        if item.strategy_version == item.spec.deterministic_version
        and cohort.interval in {interval.value for interval in item.spec.intervals}
    ]
    calibrated_mass = sum((item.weight for item in active), Decimal(0))
    probability = (
        sum(
            (
                item.weight * Decimal(item.calibration_successes + 1) / Decimal(item.calibration_observations + 2)
                for item in active
            ),
            Decimal(0),
        )
        / calibrated_mass
        if calibrated_mass
        else Decimal("0.5")
    )
    gross_edge = (
        sum((item.weight * item.expected_edge for item in active), Decimal(0)) / calibrated_mass
        if calibrated_mass
        else Decimal(0)
    )
    estimated_cost = (
        sum((item.weight * item.expected_cost for item in active), Decimal(0)) / calibrated_mass
        if calibrated_mass
        else Decimal(0)
    )
    uncertainty = (
        sum((item.weight * item.uncertainty for item in active), Decimal(0))
        / calibrated_mass
        * cohort.cost_buffer_multiplier
        if calibrated_mass
        else Decimal(0)
    )
    expected_net_edge = gross_edge - estimated_cost - uncertainty
    unique_reasons = tuple(dict.fromkeys(reasons))
    calibrated = bool(active) and len(active) == len(cohort.components)
    authenticated = calibrated and expected_net_edge > 0
    return EligibilityEvidence(
        cohort_id=cohort.cohort_id,
        dataset_hash=cohort.dataset_hash,
        evidence_hash=cohort.evidence_hash,
        policy_hash=canonical_hash(
            {
                "cost_buffer_multiplier": str(cohort.cost_buffer_multiplier),
                "models": [item.model_hash for item in cohort.components],
            }
        ),
        strategy_versions=tuple(sorted((item.spec.strategy_id, item.strategy_version) for item in cohort.components)),
        provider=cohort.provider,
        feed=cohort.feed,
        symbol=cohort.symbol,
        interval=cohort.interval,
        mode=cohort.mode,
        promoted=all_promoted,
        no_repaint_passed=all_causal and versions_match,
        calibration_status="calibrated" if calibrated else "unavailable",
        economic_evidence_status="authenticated" if authenticated else "unavailable",
        direction=live_direction,
        probability=probability,
        vote_margin=vote_margin,
        expected_net_edge=expected_net_edge if authenticated else Decimal(0),
        breadth=breadth,
        data_through=data_through,
        shortable=cohort.provider != "alpaca" if shortable is None else shortable,
        easy_to_borrow=cohort.provider != "alpaca" if easy_to_borrow is None else easy_to_borrow,
        reasons=unique_reasons,
    )


class SealedCohortResolver:
    def __init__(
        self,
        cohorts: Sequence[SealedCohort],
        *,
        asset_metadata: dict[tuple[str, str], tuple[bool, bool]] | None = None,
    ):
        self._cohorts = {(item.provider, item.feed, item.symbol, item.interval): item for item in cohorts}
        self._asset_metadata = asset_metadata or {}

    def __call__(self, bars: tuple[MarketBar, ...], quote: MarketQuote) -> EligibilityEvidence | None:
        intervals = {item.interval for item in bars}
        if len(intervals) != 1:
            return None
        interval = next(iter(intervals))
        cohort = self._cohorts.get((quote.provider, quote.feed, quote.symbol, interval))
        if cohort is None:
            return None
        shortable, easy = self._asset_metadata.get((quote.provider, quote.symbol), (False, False))
        return evaluate_sealed_cohort(cohort, bars, quote, shortable=shortable, easy_to_borrow=easy)


def selected_cohort_hash(cohorts: Sequence[SealedCohort]) -> str:
    """Return the exact immutable identity supplied by the selected research cohorts."""
    identities = tuple(sorted(item.cohort_id for item in cohorts))
    if not identities:
        return EMPTY_COHORT_HASH
    if len(set(identities)) != len(identities):
        raise ValueError("selected cohort identities must be unique")
    return identities[0] if len(identities) == 1 else hashlib.sha256("|".join(identities).encode()).hexdigest()


def select_monitor_cohorts(
    cohorts: Sequence[SealedCohort],
    *,
    stocks: Sequence[str],
    crypto: Sequence[str],
    interval: BarIntervalValue,
    stock_feed: Literal["iex", "sip"],
) -> tuple[SealedCohort, ...]:
    if stock_feed not in {"iex", "sip"}:
        raise ValueError("unsupported Alpaca stock feed")
    stock_symbols = {item.upper() for item in stocks}
    crypto_symbols = {item.upper() for item in crypto}
    return tuple(
        sorted(
            (
                item
                for item in cohorts
                if item.interval == interval
                and (
                    (item.provider == "alpaca" and item.feed == stock_feed and item.symbol.upper() in stock_symbols)
                    or (item.provider == "binance" and item.feed == "spot" and item.symbol.upper() in crypto_symbols)
                )
            ),
            key=lambda item: (item.provider, item.feed, item.symbol, item.interval, item.mode, item.cohort_id),
        )
    )


def _forward_evidence_rows(database: Database, cohort_hash: str) -> tuple[dict[str, object], ...]:
    frame = database.frame(
        "select evidence from forward_evidence_daily where cohort_hash = :cohort_hash "
        "order by period_start, period_end",
        {"cohort_hash": cohort_hash},
    )
    if frame.empty:
        return ()
    return tuple(item for item in frame["evidence"] if isinstance(item, dict))


def live_readiness_evidence_hash(cohorts: Sequence[SealedCohort], forward_evidence: Sequence[dict[str, object]]) -> str:
    robustness = derive_live_readiness_robustness(cohorts, forward_evidence)
    return canonical_hash(
        {
            "schema_version": 2,
            "selection_hash": selected_cohort_hash(cohorts),
            "sealed_cohorts": [
                {"cohort_id": item.cohort_id, "evidence_hash": item.evidence_hash}
                for item in sorted(cohorts, key=lambda value: value.cohort_id)
            ],
            "forward_evidence": list(forward_evidence),
            "robustness_evidence": robustness,
        }
    )


def derive_live_readiness_robustness(
    cohorts: Sequence[SealedCohort], forward_evidence: Sequence[dict[str, object]]
) -> dict[str, object]:
    """Derive readiness metrics only from sealed component and closed forward evidence."""
    components = tuple(component for cohort in cohorts for component in cohort.components)
    paper_returns: list[float] = []
    slippage_errors: list[Decimal] = []
    for row in forward_evidence:
        try:
            paper = Decimal(str(row["paper_net_return"]))
            modeled = Decimal(str(row["modeled_slippage_bps"]))
            observed = Decimal(str(row["observed_slippage_bps"]))
            if not paper.is_finite() or not modeled.is_finite() or not observed.is_finite() or modeled <= 0:
                raise ValueError
            paper_returns.append(float(paper))
            slippage_errors.append(abs(observed - modeled) / modeled)
        except (KeyError, TypeError, ValueError):
            paper_returns = []
            slippage_errors = []
            break

    bootstrap_probability: float | None = None
    if paper_returns:
        values = np.asarray(paper_returns, dtype=float)
        block_size = min(10, len(values))
        blocks_needed = int(np.ceil(len(values) / block_size))
        offsets = np.arange(block_size)
        generator = np.random.default_rng(42)
        positive = 0
        for _ in range(2_000):
            starts = generator.integers(0, len(values), size=blocks_needed)
            indices = ((starts[:, None] + offsets) % len(values)).ravel()[: len(values)]
            positive += bool(values[indices].mean() > 0)
        bootstrap_probability = positive / 2_000

    dsr: list[Decimal] = []
    pbo: list[Decimal] = []
    stability: list[Decimal] = []
    causal = bool(components)
    receipt_hashes: list[str] = []
    for component in components:
        record = component.robustness_evidence
        payload = record.get("receipt_payload")
        metrics = payload.get("metrics") if isinstance(payload, dict) else None
        try:
            dsr_value = Decimal(str(record["deflated_sharpe_probability"]))
            pbo_value = Decimal(str(metrics["pbo_probability"]))
            stable = metrics["parameter_neighborhood_stable"] is True
            stability_value = Decimal(str(metrics["parameter_neighbor_positive_fraction"])) if stable else Decimal(0)
            if not all(value.is_finite() for value in (dsr_value, pbo_value, stability_value)):
                raise ValueError
            dsr.append(dsr_value)
            pbo.append(pbo_value)
            stability.append(stability_value)
            causal = causal and record.get("causal_audit_passed") is True
            receipt_hashes.append(str(record["receipt_hash"]))
        except (KeyError, TypeError, ValueError):
            dsr = []
            pbo = []
            stability = []
            causal = False
            break

    source = {
        "schema_version": 1,
        "selection_hash": selected_cohort_hash(cohorts),
        "component_robustness_receipts": sorted(receipt_hashes),
        "forward_evidence_hashes": sorted(str(row.get("evidence_hash", "")) for row in forward_evidence),
    }
    metrics_record: dict[str, object] = {
        "cohort_hash": selected_cohort_hash(cohorts),
        "causal_passed": causal,
        "bootstrap_probability_positive": bootstrap_probability,
        "deflated_sharpe_probability": str(min(dsr)) if dsr else None,
        "pbo": str(max(pbo)) if pbo else None,
        "parameter_stability": str(min(stability)) if stability else None,
        "slippage_model_error": str(max(slippage_errors)) if slippage_errors else None,
        "source_hash": canonical_hash(source),
    }
    metrics_record["evidence_hash"] = canonical_hash(metrics_record)
    return metrics_record


def live_readiness_policy_hash(cohorts: Sequence[SealedCohort]) -> str:
    return canonical_hash(
        {
            "schema_version": 1,
            "readiness": LIVE_READINESS_POLICY,
            "alert_eligibility": LIVE_ALERT_POLICY,
            "cohort_policies": [
                {
                    "cohort_id": item.cohort_id,
                    "cost_buffer_multiplier": str(item.cost_buffer_multiplier),
                    "component_models": [component.model_hash for component in item.components],
                }
                for item in sorted(cohorts, key=lambda value: value.cohort_id)
            ],
        }
    )


def load_active_readiness_receipt(
    database: Database,
    *,
    cohorts: Sequence[SealedCohort],
    now: datetime,
) -> ActiveReadinessReceipt | None:
    if now.tzinfo is not UTC:
        raise ValueError("readiness validation requires explicit UTC")
    cohort_hash = selected_cohort_hash(cohorts)
    if cohort_hash == EMPTY_COHORT_HASH:
        return None
    forward_evidence = _forward_evidence_rows(database, cohort_hash)
    expected_evidence_hash = live_readiness_evidence_hash(cohorts, forward_evidence)
    expected_policy_hash = live_readiness_policy_hash(cohorts)
    frame = database.frame(
        "select readiness_receipt_id, cohort_hash, evidence_hash, policy_hash, gates, issued_at, "
        "expires_at, status, invalidated_at from readiness_receipts where cohort_hash = :cohort_hash "
        "order by issued_at desc limit 1",
        {"cohort_hash": cohort_hash},
    )
    if frame.empty:
        return None
    row = frame.iloc[0]
    if str(row["status"]) not in {"active", "eligible"} or pd.notna(row["invalidated_at"]):
        return None

    def utc(value: object) -> datetime:
        timestamp = pd.Timestamp(value)
        normalized = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
        return normalized.to_pydatetime()

    try:
        receipt = ActiveReadinessReceipt(
            receipt_id=str(row["readiness_receipt_id"]),
            cohort_hash=str(row["cohort_hash"]),
            evidence_hash=str(row["evidence_hash"]),
            policy_hash=str(row["policy_hash"]),
            gates=tuple(row["gates"]),
            issued_at=utc(row["issued_at"]),
            expires_at=utc(row["expires_at"]),
        )
    except (TypeError, ValueError):
        return None
    return (
        receipt
        if receipt.valid_at(now, cohort_hash=cohort_hash)
        and receipt.evidence_hash == expected_evidence_hash
        and receipt.policy_hash == expected_policy_hash
        else None
    )


def load_sealed_cohorts(database: Database, specs: Sequence[StrategySpec]) -> tuple[SealedCohort, ...]:
    """Load only the newest complete, actionable, internally consistent evidence cohorts."""
    weights = database.frame(
        "select strategy_run_id, dataset_hash, strategy_id, strategy_version, symbol, interval, "
        "mode, effective_at, weight, evidence from ensemble_weights order by effective_at desc"
    )
    runs = database.frame(
        "select strategy_run_id, metrics from strategy_runs where status = 'evaluated' order by run_timestamp desc"
    )
    if weights.empty or runs.empty:
        return ()
    run_metrics = {
        str(row.strategy_run_id): row.metrics
        for row in runs.drop_duplicates("strategy_run_id", keep="first").itertuples(index=False)
        if isinstance(row.metrics, dict)
    }
    configured = {item.strategy_id: item for item in specs if item.enabled}
    groups: dict[tuple[str, object], list[object]] = {}
    for row in weights.itertuples(index=False):
        evidence = row.evidence if isinstance(row.evidence, dict) else {}
        cohort_id = str(evidence.get("cohort_id", ""))
        if not cohort_id:
            continue
        groups.setdefault((cohort_id, row.effective_at), []).append(row)

    newest_by_scope: dict[tuple[str, str, str, str], SealedCohort] = {}
    for (_cohort_id, _effective_at), rows in groups.items():
        first = rows[0]
        evidence = first.evidence
        decision = evidence.get("current_decision")
        members = evidence.get("cohort_members")
        ensemble_config = evidence.get("ensemble_config")
        if not isinstance(decision, dict) or not isinstance(ensemble_config, dict):
            continue
        if not isinstance(members, list):
            continue
        expected = {
            (str(item.get("strategy_id")), str(item.get("strategy_version")))
            for item in members
            if isinstance(item, dict)
        }
        observed = {(str(item.strategy_id), str(item.strategy_version)) for item in rows}
        if not expected or observed != expected or len(rows) != len(expected):
            continue
        components: list[SealedComponent] = []
        coverage_identity: tuple[str, str, str, str] | None = None
        valid = True
        for row in rows:
            metrics = run_metrics.get(str(row.strategy_run_id))
            spec = configured.get(str(row.strategy_id))
            if metrics is None or spec is None:
                valid = False
                break
            promotion = metrics.get("promotion")
            coverage = metrics.get("coverage_manifest")
            live_model = metrics.get("live_decision_model")
            robustness = metrics.get("robustness_evidence")
            if (
                not isinstance(promotion, dict)
                or not isinstance(coverage, dict)
                or not isinstance(live_model, dict)
                or not isinstance(robustness, dict)
            ):
                valid = False
                break
            identity = (
                str(coverage.get("provider", "")),
                str(coverage.get("feed", "")),
                str(coverage.get("symbol", "")),
                str(coverage.get("interval", "")),
            )
            if (
                coverage.get("dataset_hash") != row.dataset_hash
                or coverage.get("gaps") != []
                or int(coverage.get("row_count", 0)) < spec.warmup_bars
                or identity[2:] != (str(row.symbol), str(row.interval))
            ):
                valid = False
                break
            if coverage_identity is None:
                coverage_identity = identity
            elif coverage_identity != identity:
                valid = False
                break
            calibration = live_model.get("calibration")
            calibration_hash = live_model.get("calibration_hash")
            if (
                live_model.get("calibration_status") != "calibrated"
                or live_model.get("economic_evidence_status") != "authenticated"
                or not isinstance(calibration, dict)
                or calibration.get("method") != "development_only_beta_binomial_v1"
                or calibration_hash != canonical_hash(calibration)
            ):
                valid = False
                break
            try:
                components.append(
                    SealedComponent(
                        spec=spec,
                        strategy_version=str(row.strategy_version),
                        weight=Decimal(str(row.weight)),
                        promoted=promotion.get("promoted") is True,
                        causal_audit_passed=metrics.get("causal_audit_passed") is True,
                        calibration_method=calibration["method"],
                        calibration_observations=int(calibration["observations"]),
                        calibration_successes=int(calibration["successes"]),
                        expected_edge=Decimal(str(live_model["expected_edge"])),
                        expected_cost=Decimal(str(live_model["expected_cost"])),
                        uncertainty=Decimal(str(live_model["uncertainty"])),
                        model_hash=canonical_hash(live_model),
                        robustness_evidence=robustness,
                    )
                )
            except (KeyError, TypeError, ValueError):
                valid = False
                break
        if not valid or coverage_identity is None or not components:
            continue
        try:
            cohort = SealedCohort(
                cohort_id=_cohort_id,
                provider=coverage_identity[0],
                feed=coverage_identity[1],
                dataset_hash=str(first.dataset_hash),
                symbol=coverage_identity[2],
                interval=coverage_identity[3],
                mode=str(first.mode),
                cost_buffer_multiplier=Decimal(str(ensemble_config["cost_buffer_multiplier"])),
                components=tuple(components),
            )
        except (KeyError, TypeError, ValueError):
            continue
        scope = (cohort.provider, cohort.feed, cohort.symbol, cohort.interval)
        newest_by_scope.setdefault(scope, cohort)
    return tuple(newest_by_scope.values())


def load_decision_history(
    database: Database, cohort: SealedCohort, *, maximum_bars: int = 5_000
) -> tuple[MarketBar, ...]:
    if maximum_bars < 1 or maximum_bars > 100_000:
        raise ValueError("historical warm-up limit is invalid")
    frame = database.frame(
        "select provider, feed, symbol, interval, open_timestamp, close_timestamp, available_at, "
        "revision, open, high, low, close, volume from market_bars where provider = :provider and "
        "feed = :feed and symbol = :symbol and interval = :interval and finalized = true "
        "order by open_timestamp desc, revision desc limit :maximum_bars",
        {
            "provider": cohort.provider,
            "feed": cohort.feed,
            "symbol": cohort.symbol,
            "interval": cohort.interval,
            "maximum_bars": maximum_bars,
        },
    )
    if frame.empty:
        return ()
    frame = frame.sort_values(["open_timestamp", "revision"], kind="stable").drop_duplicates(
        "open_timestamp", keep="last"
    )

    def utc(value: object) -> datetime:
        timestamp = pd.Timestamp(value)
        normalized = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
        return normalized.to_pydatetime()

    return tuple(
        MarketBar(
            provider=str(row.provider),
            feed=str(row.feed),
            symbol=str(row.symbol),
            interval=str(row.interval),
            start=utc(row.open_timestamp),
            end=utc(row.close_timestamp),
            available_at=utc(row.available_at),
            received_at=max(utc(row.available_at), utc(row.close_timestamp)),
            open=Decimal(str(row.open)),
            high=Decimal(str(row.high)),
            low=Decimal(str(row.low)),
            close=Decimal(str(row.close)),
            volume=Decimal(str(row.volume)),
            finalized=True,
            revision=max(int(row.revision), 0),
        )
        for row in frame.itertuples(index=False)
    )


__all__ = [
    "SealedCohort",
    "SealedCohortResolver",
    "SealedComponent",
    "evaluate_sealed_cohort",
    "load_decision_history",
    "load_active_readiness_receipt",
    "load_sealed_cohorts",
    "selected_cohort_hash",
    "select_monitor_cohorts",
    "live_readiness_evidence_hash",
    "derive_live_readiness_robustness",
    "live_readiness_policy_hash",
]
