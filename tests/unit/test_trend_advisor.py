"""Causal advisory behavior: future evidence and invalid feeds must not create levels."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pandas as pd
import pytest

from src.research.round_two_contracts import ResearchRoundProtocol, RoundCandidate, RoundObservation, RoundStatus
from src.research.round_two_quality import summarize_quality
from src.research.round_two_walkforward import (
    CandidateResult,
    EvaluationMetrics,
    FoldResult,
    SealedTestReceipt,
    WalkForwardFold,
)
from src.strategies.registry import StrategyRegistry
from src.strategies.types import BarInterval, StrategyFamily, StrategySpec

START = datetime(2026, 1, 5, tzinfo=UTC)


def fixture():
    candidate = RoundCandidate(symbol="BTCUSDT", strategy_id="test_trend")
    protocol = (
        ResearchRoundProtocol.default(round_id="advisor-test", starts_at=START - timedelta(days=4))
        .model_copy(update={"symbols": ("BTCUSDT",), "candidates": (candidate,)})
        .validated()
    )
    rows = tuple(
        RoundObservation(
            provider="binance",
            feed="spot",
            symbol="BTCUSDT",
            source_key=f"bar:{i}",
            provider_at=START + timedelta(minutes=i),
            received_at=START + timedelta(minutes=i),
            available_at=START + timedelta(minutes=i),
            close=D(100) + D(i) / 10,
            bid=D(100) + D(i) / 10 - D("0.01"),
            ask=D(100) + D(i) / 10 + D("0.01"),
            volume=D(1000),
        )
        for i in range(66)
    )
    metrics = EvaluationMetrics(
        lower_edge=D("0.01"), coverage=D(1), trade_count=100, net_return=D("0.02"), stressed_net_return=D("0.01")
    )
    fold = WalkForwardFold(
        fold_id="test",
        train_start=START - timedelta(days=3),
        train_end=START - timedelta(days=2),
        validation_end=START - timedelta(days=1),
        test_end=START,
    )
    result = CandidateResult(
        candidate=candidate,
        status=RoundStatus.EXPERIMENTAL_PAPER_ONLY,
        train=metrics,
        validation=metrics,
        sealed_test=metrics,
        folds=(
            FoldResult(
                fold=fold,
                status=RoundStatus.EXPERIMENTAL_PAPER_ONLY,
                train=metrics,
                validation=metrics,
                sealed_test=metrics,
                receipt=SealedTestReceipt(round_hash=protocol.identity_hash, fold_id="test", sealed_at=START),
            ),
        ),
    )
    registry = StrategyRegistry()
    registry.register(
        StrategySpec(
            strategy_id="test_trend",
            family=StrategyFamily.TREND,
            version="v1",
            intervals=(BarInterval.ONE_MINUTE,),
            warmup_bars=5,
            parameters={},
        ),
        trend_signal,
    )
    return protocol, result, rows, registry


def trend_signal(spec, frame, context):
    return pd.DataFrame(
        {
            "signal": (frame.close.diff() > 0).astype(int),
            "decision_timestamp": frame.available_at,
            "data_through": frame.available_at,
        }
    )


def run_advice(protocol, result, rows, registry, **kwargs):
    from src.research.trend_advisor import advise

    return advise(
        protocol,
        result,
        summarize_quality(rows, protocol),
        rows,
        registry=registry,
        decision_at=kwargs.pop("decision_at", rows[-1].available_at),
        **kwargs,
    )


def test_clean_confirmed_trend_has_barrier_derived_research_levels():
    p, result, rows, registry = fixture()
    suggestion = run_advice(p, result, rows, registry)
    assert suggestion.posture == "long_research"
    assert suggestion.paper_only and suggestion.qualification_status == "unqualified"
    assert suggestion.entry_low == D("106.49")
    assert suggestion.entry_high == D("106.51")
    assert suggestion.invalidation == D("105.4449")
    assert suggestion.target == D("108.10765")
    assert suggestion.expires_at == rows[-1].available_at + timedelta(seconds=15)


@pytest.mark.parametrize(
    "change, reason",
    [
        ("short", "spot_short_unsupported"),
        ("rejected", "candidate_not_experimental"),
        ("no_folds", "candidate_evidence_missing"),
        ("future_receipt", "candidate_unavailable_at_decision"),
        ("gap", "continuity_warmup"),
        ("stale", "observation_stale"),
        ("late_feed", "provider_latency"),
        ("no_quote", "quote_unavailable"),
        ("no_volume", "liquidity_unavailable"),
        ("thin", "liquidity_below_minimum"),
        ("flat", "trend_not_aligned"),
        ("unknown_strategy", "candidate_confirmation_unavailable"),
    ],
)
def test_invalid_evidence_abstains_without_price_levels(change, reason):
    p, result, rows, registry = fixture()
    decision = rows[-1].available_at
    if change == "short":
        result = result.model_copy(update={"candidate": result.candidate.model_copy(update={"direction": "short"})})
    if change == "rejected":
        result = result.model_copy(update={"status": RoundStatus.REJECTED})
    if change == "no_folds":
        result = result.model_copy(update={"folds": ()})
    if change == "future_receipt":
        result = result.model_copy(
            update={
                "folds": (
                    result.folds[0].model_copy(
                        update={
                            "receipt": result.folds[0].receipt.model_copy(
                                update={"sealed_at": decision + timedelta(seconds=1)}
                            )
                        }
                    ),
                )
            }
        )
    if change == "gap":
        rows = rows[:-2] + rows[-1:]
    if change == "stale":
        decision += timedelta(seconds=16)
    if change == "late_feed":
        rows = rows[:-1] + (
            rows[-1].model_copy(
                update={
                    "received_at": decision + timedelta(seconds=30),
                    "available_at": decision + timedelta(seconds=30),
                }
            ),
        )
        decision += timedelta(seconds=30)
    if change == "no_quote":
        rows = rows[:-1] + (rows[-1].model_copy(update={"bid": None, "ask": None}),)
    if change == "no_volume":
        rows = rows[:-1] + (rows[-1].model_copy(update={"volume": None}),)
    if change == "thin":
        rows = rows[:-1] + (rows[-1].model_copy(update={"volume": D("0.001")}),)
    if change == "flat":
        rows = tuple(row.model_copy(update={"close": D(100), "bid": D("99.99"), "ask": D("100.01")}) for row in rows)
    if change == "unknown_strategy":
        registry = StrategyRegistry()
    suggestion = run_advice(p, result, rows, registry, decision_at=decision)
    assert suggestion.posture == "stand_aside"
    assert reason in suggestion.reasons
    assert suggestion.entry_low is suggestion.entry_high is suggestion.invalidation is suggestion.target is None


def test_future_bars_never_change_an_earlier_posture_or_evidence_hash():
    p, result, rows, registry = fixture()
    before = run_advice(p, result, rows, registry)
    future = rows[-1].model_copy(
        update={
            "source_key": "future",
            "provider_at": START + timedelta(minutes=66),
            "received_at": START + timedelta(minutes=66),
            "available_at": START + timedelta(minutes=66),
            "close": D(1),
        }
    )
    after = run_advice(p, result, rows + (future,), registry, decision_at=rows[-1].available_at)
    assert before == after


def test_quality_subset_cannot_hide_a_gap_and_duplicate_conflict():
    from src.research.trend_advisor import advise

    p, result, rows, registry = fixture()
    with pytest.raises(ValueError, match="quality evidence"):
        advise(p, result, summarize_quality(rows[:-1], p), rows, registry=registry, decision_at=rows[-1].available_at)
    conflict = rows + (rows[-1].model_copy(update={"close": D(1)}),)
    suggestion = run_advice(p, result, conflict, registry)
    assert "duplicate_source_key" in suggestion.reasons


def test_runtime_publishes_bounded_stand_aside_advisor_for_unavailable_candidate(tmp_path):
    import json

    from src.research.round_two_registry import register_round
    from src.research.round_two_runtime import write_round_report

    p, _, _, _ = fixture()
    register_round(p, tmp_path)
    payload = json.loads(write_round_report(tmp_path).read_text())
    assert payload["trendAdvisor"][0]["posture"] == "stand_aside"
    assert payload["trendAdvisor"][0]["qualificationStatus"] == "unqualified"


def test_published_decision_is_retained_and_changed_policy_requires_new_round(tmp_path, monkeypatch):
    import json

    from src.research import trend_advisor
    from src.research.round_two_registry import register_round
    from src.research.round_two_runtime import write_round_report

    p, _, _, _ = fixture()
    register_round(p, tmp_path)
    write_round_report(tmp_path)
    ledger = tmp_path / "trend-advisor-decisions.jsonl"
    original = ledger.read_bytes()
    assert json.loads(original)["posture"] == "stand_aside"
    monkeypatch.setitem(trend_advisor.POLICY, "minimum_quote_volume", "1")
    with pytest.raises(ValueError, match="advisor policy changed"):
        write_round_report(tmp_path)
    assert ledger.read_bytes() == original


def test_advisor_payload_rejects_actions_and_spot_short():
    from src.research.trend_advisor import TrendAdvisorSuggestion

    p, result, rows, registry = fixture()
    payload = run_advice(p, result, rows, registry).model_dump()
    for field, value in [("order_id", "x"), ("posture", "short_research"), ("qualification_status", "qualified")]:
        with pytest.raises(ValueError):
            TrendAdvisorSuggestion.model_validate({**payload, field: value})


def test_negative_candidate_confirmation_blocks_an_uptrend():
    p, result, rows, _ = fixture()
    registry = StrategyRegistry()

    def abstain(spec, frame, context):
        return pd.DataFrame({"signal": 0, "decision_timestamp": frame.available_at, "data_through": frame.available_at})

    registry.register(
        StrategySpec(
            strategy_id="test_trend",
            family=StrategyFamily.TREND,
            version="v1",
            intervals=(BarInterval.ONE_MINUTE,),
            warmup_bars=5,
            parameters={},
        ),
        abstain,
    )
    suggestion = run_advice(p, result, rows, registry)
    assert suggestion.posture == "stand_aside"
    assert "candidate_not_confirmed" in suggestion.reasons


def test_candidate_identity_stays_fixed_when_evidence_status_changes():
    p, result, rows, registry = fixture()
    experimental = run_advice(p, result, rows, registry)
    rejected = run_advice(p, result.model_copy(update={"status": RoundStatus.REJECTED}), rows, registry)
    assert experimental.candidate_hash == rejected.candidate_hash
    assert experimental.evidence_hash != rejected.evidence_hash


def test_default_unconfigured_strategy_is_explained_in_the_app_report(tmp_path):
    import json

    from src.research.round_two_registry import register_round
    from src.research.round_two_runtime import write_round_report

    register_round(ResearchRoundProtocol.default(round_id="default", starts_at=START), tmp_path)
    payload = json.loads(write_round_report(tmp_path).read_text())
    assert all(item["posture"] == "stand_aside" for item in payload["trendAdvisor"])
    assert all("candidate_confirmation_unavailable" in item["reasons"] for item in payload["trendAdvisor"])


def test_delayed_decision_expires_when_its_evidence_expires():
    p, result, rows, registry = fixture()
    delayed = run_advice(p, result, rows, registry, decision_at=rows[-1].available_at + timedelta(seconds=14))
    assert delayed.posture == "long_research"
    assert delayed.expires_at == rows[-1].available_at + timedelta(seconds=15)
    boundary = run_advice(p, result, rows, registry, decision_at=rows[-1].available_at + timedelta(seconds=15))
    assert boundary.posture == "stand_aside"
    assert "evidence_expired" in boundary.reasons


def test_long_payload_cannot_extend_its_evidence_freshness():
    from src.research.trend_advisor import TrendAdvisorSuggestion

    p, result, rows, registry = fixture()
    payload = run_advice(p, result, rows, registry).model_dump()
    payload.update(
        decision_at=rows[-1].available_at + timedelta(seconds=14),
        expires_at=rows[-1].available_at + timedelta(seconds=29),
    )
    with pytest.raises(ValueError, match="expiry|stale"):
        TrendAdvisorSuggestion.model_validate(payload)


def test_native_candidate_projection_includes_exact_canonical_identity():
    from src.research.round_two_contracts import RoundReport
    from src.research.round_two_runtime import _candidate_snapshot, _native_round_payload
    from src.strategies.types import canonical_hash

    p, result, _, _ = fixture()
    snapshot = _native_round_payload(
        RoundReport(
            round_id=p.round_id,
            protocol_hash=p.identity_hash,
            status=result.status,
            candidates=(_candidate_snapshot(result),),
        )
    )
    assert snapshot["candidates"][0]["candidateHash"] == canonical_hash(result.candidate.model_dump(mode="json"))
