from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd

from src.database.engine import Database
from src.live_monitor.evidence import (
    REQUIRED_READINESS_GATES,
    SealedCohort,
    SealedComponent,
    evaluate_sealed_cohort,
    live_readiness_evidence_hash,
    live_readiness_policy_hash,
    load_active_readiness_receipt,
    load_sealed_cohorts,
    select_monitor_cohorts,
    selected_cohort_hash,
)
from src.live_monitor.types import Direction, MarketBar, MarketQuote
from src.strategies.types import BarInterval, StrategyFamily, StrategySpec, canonical_hash
from src.trading.forward import ForwardEvidenceBuilder
from src.trading.live_monitor_readiness import evaluate_and_persist_live_readiness

NOW = datetime(2026, 8, 26, 14, tzinfo=UTC)


def component(strategy_id: str, weight: str) -> SealedComponent:
    parameters = (
        {"fast_period": 2, "slow_period": 3, "signal_period": 2}
        if strategy_id == "macd_histogram_trend"
        else {"fast_period": 2, "slow_period": 3, "adx_period": 2, "adx_threshold": 0}
    )
    spec = StrategySpec(
        strategy_id=strategy_id,
        family=StrategyFamily.TREND,
        version="1.0.0",
        intervals=(BarInterval.FIVE_MINUTES,),
        warmup_bars=3,
        parameters=parameters,
    )
    receipt_payload = {
        "schema_version": 1,
        "metrics": {
            "median_walk_forward_net_edge": 0.01,
            "pbo_probability": 0.20,
            "parameter_neighborhood_stable": True,
            "parameter_neighbor_positive_fraction": 0.80,
            "parameter_neighbor_median_ratio": 0.90,
        },
        "discovered_at": NOW.isoformat(),
        "evaluated_at": NOW.isoformat(),
        "development_data_through": NOW.isoformat(),
        "sealed_final_start": NOW.isoformat(),
        "cohort_id": "component-cohort",
        "dataset_hash": "d" * 64,
        "validation_config_hash": "e" * 64,
        "provenance": {"source": "test"},
    }
    robustness_evidence = {
        "receipt_payload": receipt_payload,
        "receipt_hash": canonical_hash(receipt_payload),
        "deflated_sharpe_probability": 0.96,
        "causal_audit_passed": True,
    }
    robustness_evidence["evidence_hash"] = canonical_hash(robustness_evidence)
    return SealedComponent(
        spec=spec,
        strategy_version=spec.deterministic_version,
        weight=Decimal(weight),
        promoted=True,
        causal_audit_passed=True,
        calibration_method="oof_sigmoid_v2",
        calibration_observations=100,
        calibration_effective_observations=Decimal("100"),
        calibration_successes=67,
        calibrated_probability=Decimal("0.68"),
        probability_lower_bound=Decimal("0.58"),
        probability_upper_bound=Decimal("0.76"),
        brier_score=Decimal("0.19"),
        log_loss=Decimal("0.58"),
        expected_calibration_error=Decimal("0.04"),
        calibration_slice_identity="AAPL:5m:global",
        probability_definition="target_before_stop_after_costs",
        selective_threshold=Decimal("0.60"),
        selective_coverage=Decimal("0.30"),
        expected_edge=Decimal("0.006"),
        expected_cost=Decimal("0.001"),
        uncertainty=Decimal("0.001"),
        lower_expected_net_edge=Decimal("0.004"),
        model_hash="a" * 64,
        robustness_evidence=robustness_evidence,
    )


def bars(count: int = 8) -> tuple[MarketBar, ...]:
    result = []
    for index in range(count):
        start = NOW + timedelta(minutes=5 * index)
        close = Decimal(100 + index)
        result.append(
            MarketBar(
                provider="alpaca",
                feed="iex",
                symbol="AAPL",
                interval="5m",
                start=start,
                end=start + timedelta(minutes=5),
                available_at=start + timedelta(minutes=5),
                received_at=start + timedelta(minutes=5),
                open=close - Decimal("0.5"),
                high=close + Decimal("0.5"),
                low=close - Decimal("1"),
                close=close,
                volume=Decimal("1000"),
                finalized=True,
                revision=0,
            )
        )
    return tuple(result)


def quote() -> MarketQuote:
    at = bars()[-1].end
    return MarketQuote(
        provider="alpaca",
        feed="iex",
        symbol="AAPL",
        bid=Decimal("106.9"),
        ask=Decimal("107.0"),
        last=Decimal("106.95"),
        tick_size=Decimal("0.01"),
        provider_time=at,
        received_at=at,
    )


def cohort(**updates) -> SealedCohort:
    values = {
        "cohort_id": "c" * 64,
        "provider": "alpaca",
        "feed": "iex",
        "dataset_hash": "d" * 64,
        "symbol": "AAPL",
        "interval": "5m",
        "mode": "frozen",
        "cost_buffer_multiplier": Decimal("1"),
        "components": (
            component("macd_histogram_trend", "0.6"),
            component("ema_adx_trend", "0.4"),
        ),
    }
    values.update(updates)
    return SealedCohort(**values)


def test_live_evidence_recalculates_only_current_causal_component_signals() -> None:
    evidence = evaluate_sealed_cohort(cohort(), bars(), quote())

    assert evidence.direction is Direction.LONG
    assert evidence.breadth == 2
    assert evidence.promoted is True
    assert evidence.no_repaint_passed is True
    assert evidence.calibration_status == "calibrated"
    assert evidence.economic_evidence_status == "authenticated"
    assert evidence.data_through == bars()[-1].end


def test_live_evidence_fails_closed_when_component_version_does_not_match() -> None:
    bad = component("macd_histogram_trend", "1").model_copy(update={"strategy_version": "wrong"})
    evidence = evaluate_sealed_cohort(
        cohort(components=(bad, component("ema_adx_trend", "1"))),
        bars(),
        quote(),
    )

    assert "strategy_version_mismatch" in evidence.reasons


def test_live_evidence_abstains_without_crashing_when_no_component_is_ready() -> None:
    evidence = evaluate_sealed_cohort(cohort(), bars(2), quote())

    assert evidence.direction is Direction.LONG
    assert evidence.breadth == 0
    assert "live_warmup_incomplete" in evidence.reasons
    assert "no_current_signal" in evidence.reasons


def test_live_evidence_applies_sealed_models_to_current_short_direction() -> None:
    falling = tuple(
        item.model_copy(
            update={
                "open": Decimal(110 - index) + Decimal("0.5"),
                "high": Decimal(110 - index) + Decimal("1"),
                "low": Decimal(110 - index) - Decimal("0.5"),
                "close": Decimal(110 - index),
            }
        )
        for index, item in enumerate(bars())
    )

    evidence = evaluate_sealed_cohort(cohort(), falling, quote())

    assert evidence.direction is Direction.SHORT
    assert evidence.calibration_status == "calibrated"
    assert evidence.economic_evidence_status == "authenticated"
    assert evidence.probability == Decimal("0.68")
    assert evidence.probability_lower_bound == Decimal("0.58")
    assert evidence.expected_net_edge == Decimal("0.004")


def test_live_evidence_rejects_small_effective_calibration_samples() -> None:
    weak = component("macd_histogram_trend", "0.5").model_copy(
        update={"calibration_effective_observations": Decimal("99")}
    )
    evidence = evaluate_sealed_cohort(cohort(components=(weak, component("ema_adx_trend", "0.5"))), bars(), quote())

    assert evidence.calibration_status == "unavailable"
    assert "minimum_effective_calibration_sample" in evidence.reasons


def test_live_evidence_rejects_nonpositive_lower_net_edge() -> None:
    weak = component("macd_histogram_trend", "0.5").model_copy(update={"lower_expected_net_edge": Decimal("-0.001")})
    evidence = evaluate_sealed_cohort(cohort(components=(weak, component("ema_adx_trend", "0.5"))), bars(), quote())

    assert evidence.economic_evidence_status == "unavailable"
    assert "nonpositive_lower_net_edge" in evidence.reasons


class FrameDatabase:
    def __init__(self, frames: list[pd.DataFrame]):
        self.frames = iter(frames)

    def frame(self, _statement: str, _params=None) -> pd.DataFrame:
        return next(self.frames)


def test_loader_keeps_promoted_cohort_when_cutoff_posture_abstained() -> None:
    configured = (component("macd_histogram_trend", "0.6"), component("ema_adx_trend", "0.4"))
    members = [{"strategy_id": item.spec.strategy_id, "strategy_version": item.strategy_version} for item in configured]
    calibration = {
        "method": "oof_sigmoid_v2",
        "observations": 100,
        "effective_observations": 100,
        "successes": 67,
        "probability": 0.68,
        "confidence_low": 0.58,
        "confidence_high": 0.76,
        "brier_score": 0.19,
        "log_loss": 0.58,
        "expected_calibration_error": 0.04,
        "slice_identity": "AAPL:5m:global",
        "probability_definition": "target_before_stop_after_costs",
        "selective_threshold": 0.60,
        "selective_coverage": 0.30,
        "lower_expected_net_edge": 0.004,
        "outcomes_through": NOW.isoformat(),
        "decision_rows_hash": "b" * 64,
    }
    live_model = {
        "calibration": calibration,
        "calibration_hash": canonical_hash(calibration),
        "calibration_status": "calibrated",
        "economic_evidence_status": "authenticated",
        "expected_edge": 0.006,
        "expected_cost": 0.001,
        "uncertainty": 0.001,
    }
    evidence = {
        "cohort_id": "c" * 64,
        "cohort_members": members,
        "current_decision": {"status": "abstain", "signal": 0},
        "ensemble_config": {"cost_buffer_multiplier": 1},
    }
    weights = pd.DataFrame(
        [
            {
                "strategy_run_id": f"run-{index}",
                "dataset_hash": "d" * 64,
                "strategy_id": item.spec.strategy_id,
                "strategy_version": item.strategy_version,
                "symbol": "AAPL",
                "interval": "5m",
                "mode": "frozen",
                "effective_at": NOW,
                "weight": float(item.weight),
                "evidence": evidence,
            }
            for index, item in enumerate(configured)
        ]
    )
    coverage = {
        "provider": "alpaca",
        "feed": "iex",
        "symbol": "AAPL",
        "interval": "5m",
        "dataset_hash": "d" * 64,
        "gaps": [],
        "row_count": 100,
    }
    runs = pd.DataFrame(
        [
            {
                "strategy_run_id": f"run-{index}",
                "metrics": {
                    "promotion": {"promoted": True},
                    "coverage_manifest": coverage,
                    "causal_audit_passed": True,
                    "live_decision_model": live_model,
                    "robustness_evidence": item.robustness_evidence,
                },
            }
            for index, item in enumerate(configured)
        ]
    )

    loaded = load_sealed_cohorts(FrameDatabase([weights, runs]), [item.spec for item in configured])

    assert len(loaded) == 1
    assert loaded[0].cohort_id == "c" * 64


def test_selected_identity_and_readiness_receipt_must_be_exact_current_and_all_passed() -> None:
    cohorts = (cohort(),)
    selected = selected_cohort_hash(cohorts)
    gates = [{"name": name, "passed": True, "detail": "passed"} for name in sorted(REQUIRED_READINESS_GATES)]
    row = pd.DataFrame(
        [
            {
                "readiness_receipt_id": "receipt",
                "cohort_hash": selected,
                "evidence_hash": live_readiness_evidence_hash(cohorts, ()),
                "policy_hash": live_readiness_policy_hash(cohorts),
                "gates": gates,
                "issued_at": NOW - timedelta(minutes=1),
                "expires_at": NOW + timedelta(minutes=1),
                "status": "active",
                "invalidated_at": None,
            }
        ]
    )

    receipt = load_active_readiness_receipt(
        FrameDatabase([pd.DataFrame(columns=["evidence"]), row]), cohorts=cohorts, now=NOW
    )

    assert receipt is not None and receipt.cohort_hash == selected
    failed = row.copy()
    failed.at[0, "gates"] = [
        {**gate, "passed": False} if gate["name"] == "causal_integrity" else gate for gate in gates
    ]
    assert (
        load_active_readiness_receipt(
            FrameDatabase([pd.DataFrame(columns=["evidence"]), failed]), cohorts=cohorts, now=NOW
        )
        is None
    )

    changed = row.copy()
    changed.at[0, "evidence_hash"] = "e" * 64
    assert (
        load_active_readiness_receipt(
            FrameDatabase([pd.DataFrame(columns=["evidence"]), changed]), cohorts=cohorts, now=NOW
        )
        is None
    )


def test_empty_cohort_selection_has_the_native_zero_identity() -> None:
    assert selected_cohort_hash(()) == "0" * 64


def test_monitor_selection_binds_the_exact_provider_feed() -> None:
    iex = cohort(feed="iex", cohort_id="1" * 64)
    sip = cohort(feed="sip", cohort_id="2" * 64)

    selected = select_monitor_cohorts((iex, sip), stocks=("AAPL",), crypto=(), interval="5m", stock_feed="iex")

    assert selected == (iex,)


def test_forward_evidence_can_issue_persist_and_reload_an_exact_live_receipt(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'readiness.duckdb'}")
    database.initialize()
    cohorts = (cohort(),)
    selection_hash = selected_cohort_hash(cohorts)
    builder = ForwardEvidenceBuilder(database, clock=lambda: NOW)
    for index in range(60):
        period_start = NOW - timedelta(days=60 - index)
        builder.close_selection_period(
            cohort_hash=selection_hash,
            period_start=period_start,
            period_end=period_start + timedelta(days=1),
            closed_trades=2,
            paper_net_return=Decimal("0.002"),
            stressed_net_return=Decimal("0.001"),
            drawdown=Decimal("0.001"),
            reconciliation_mismatches=0,
            health_breakers=0,
            modeled_slippage_bps=Decimal("5"),
            observed_slippage_bps=Decimal("5.5"),
        )

    qualification = evaluate_and_persist_live_readiness(database, cohorts, as_of=NOW)
    loaded = load_active_readiness_receipt(database, cohorts=cohorts, now=NOW + timedelta(minutes=1))

    assert qualification.status == "eligible"
    assert qualification.receipt is not None
    assert loaded == qualification.receipt
