from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Literal

import pandas as pd
from pydantic import Field, model_validator

from src.database.engine import Database
from src.live_monitor.engine import EligibilityEvidence
from src.live_monitor.types import BarIntervalValue, Direction, LiveMonitorModel, MarketBar, MarketQuote
from src.strategies.library import StrategyContext, generate_signals
from src.strategies.types import StrategySpec


class SealedComponent(LiveMonitorModel):
    spec: StrategySpec
    strategy_version: str
    weight: Decimal = Field(gt=0, le=1)
    promoted: bool
    causal_audit_passed: bool

    @model_validator(mode="after")
    def configured_version_matches(self) -> SealedComponent:
        if not self.strategy_version.strip():
            raise ValueError("strategy version is required")
        return self


class SealedCohort(LiveMonitorModel):
    provider: str
    feed: str
    dataset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol: str
    interval: BarIntervalValue
    mode: Literal["frozen", "paper"]
    sealed_direction: Direction
    sealed_probability: Decimal = Field(ge=0, le=1)
    sealed_expected_net_edge: Decimal
    components: tuple[SealedComponent, ...] = Field(min_length=1, max_length=100)


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
        live_direction = cohort.sealed_direction
        vote_margin = Decimal(0)
        reasons.append("no_current_signal")
    calibration_matches = live_direction is cohort.sealed_direction
    if not calibration_matches:
        reasons.append("live_direction_not_covered_by_sealed_calibration")
    if breadth < 2:
        reasons.append("minimum_breadth")
    unique_reasons = tuple(dict.fromkeys(reasons))
    authenticated = calibration_matches and cohort.sealed_expected_net_edge > 0
    return EligibilityEvidence(
        provider=cohort.provider,
        feed=cohort.feed,
        symbol=cohort.symbol,
        interval=cohort.interval,
        mode=cohort.mode,
        promoted=all_promoted,
        no_repaint_passed=all_causal and versions_match,
        calibration_status="calibrated" if calibration_matches else "unavailable",
        economic_evidence_status="authenticated" if authenticated else "unavailable",
        direction=live_direction,
        probability=cohort.sealed_probability if calibration_matches else Decimal("0.5"),
        vote_margin=vote_margin,
        expected_net_edge=cohort.sealed_expected_net_edge if authenticated else Decimal(0),
        breadth=breadth,
        data_through=data_through,
        shortable=cohort.provider != "alpaca",
        easy_to_borrow=cohort.provider != "alpaca",
        reasons=unique_reasons,
    )


class SealedCohortResolver:
    def __init__(self, cohorts: Sequence[SealedCohort]):
        self._cohorts = {(item.provider, item.feed, item.symbol, item.interval): item for item in cohorts}

    def __call__(self, bars: tuple[MarketBar, ...], quote: MarketQuote) -> EligibilityEvidence | None:
        intervals = {item.interval for item in bars}
        if len(intervals) != 1:
            return None
        interval = next(iter(intervals))
        cohort = self._cohorts.get((quote.provider, quote.feed, quote.symbol, interval))
        return evaluate_sealed_cohort(cohort, bars, quote) if cohort is not None else None


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
        if not isinstance(decision, dict) or decision.get("status") not in {"long", "short"}:
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
            if not isinstance(promotion, dict) or not isinstance(coverage, dict):
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
            components.append(
                SealedComponent(
                    spec=spec,
                    strategy_version=str(row.strategy_version),
                    weight=Decimal(str(row.weight)),
                    promoted=promotion.get("promoted") is True,
                    causal_audit_passed=metrics.get("causal_audit_passed") is True,
                )
            )
        if not valid or coverage_identity is None or not components:
            continue
        try:
            signal = int(decision["signal"])
            cohort = SealedCohort(
                provider=coverage_identity[0],
                feed=coverage_identity[1],
                dataset_hash=str(first.dataset_hash),
                symbol=coverage_identity[2],
                interval=coverage_identity[3],
                mode=str(first.mode),
                sealed_direction=Direction.LONG if signal > 0 else Direction.SHORT,
                sealed_probability=Decimal(str(decision["probability"])),
                sealed_expected_net_edge=Decimal(str(decision["expected_net_edge"])),
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
    "load_sealed_cohorts",
]
