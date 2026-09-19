from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from src.research.round_two_contracts import (
    ResearchRoundProtocol,
    RoundCandidate,
    RoundObservation,
    WalkForwardSchedule,
)
from src.research.round_two_quality import summarize_quality
from src.research.round_two_registry import register_round
from src.research.round_two_walkforward import (
    causal_finalized_slice,
    evaluate_round,
    persist_sealed_test_receipt,
    walk_forward_folds,
)
from src.strategies.registry import StrategyRegistry
from src.strategies.types import BarInterval, StrategyFamily, StrategySpec

START = datetime(2026, 1, 1, tzinfo=UTC)


def protocol(**updates):
    return (
        ResearchRoundProtocol.default(round_id="test-round", starts_at=START)
        .model_copy(
            update={
                "symbols": ("BTCUSDT",),
                "candidates": (RoundCandidate(symbol="BTCUSDT", strategy_id="ema", parameters={"fast": [5, 20]}),),
                "schedule": WalkForwardSchedule(
                    starts_at=START, train_days=1, validation_days=1, sealed_test_days=1, step_days=1
                ),
                "minimum_coverage": Decimal("0.001"),
                "warmup_minutes": 1,
                **updates,
            }
        )
        .validated()
    )


def bars(prices=(100, 100, 100, 110, 110, 110, 110, 110), *, day=0):
    return tuple(
        RoundObservation(
            provider="binance",
            feed="spot",
            symbol="BTCUSDT",
            provider_at=START + timedelta(days=day, minutes=i),
            received_at=START + timedelta(days=day, minutes=i),
            available_at=START + timedelta(days=day, minutes=i),
            source_key=f"{day}:{i}",
            close=Decimal(price),
            bid=Decimal(price),
            ask=Decimal(price),
            volume=Decimal("1000"),
        )
        for i, price in enumerate(prices)
    )


def registry(generator=None):
    def signal(spec, frame, context):
        # A real deterministic test strategy. Fast 5 owns the early minute pulse;
        # fast 20 owns a later pulse, whose test-only windfall must not select it.
        minute = pd.to_datetime(frame["close_timestamp"]).dt.minute
        active = minute.eq(1 if spec.parameters["fast"] == 5 else 4)
        return pd.DataFrame(
            {
                "signal": active.astype(int),
                "decision_timestamp": frame["available_at"],
                "data_through": frame["available_at"],
            }
        )

    result = StrategyRegistry()
    result.register(
        StrategySpec(
            strategy_id="ema",
            family=StrategyFamily.TREND,
            version="v1",
            intervals=(BarInterval.ONE_MINUTE,),
            warmup_bars=1,
            parameters={"fast": 5},
        ),
        generator or signal,
    )
    return result


def observations():
    return (
        bars() + bars((100,) * 8, day=1) + bars((100, 100, 100, 120, 120, 120, 1000, 1000), day=2) + bars((100,), day=3)
    )


def evaluate(tmp_path, p=None, obs=None, reg=None):
    p, obs = p or protocol(), observations() if obs is None else obs
    register_round(p, tmp_path)
    return evaluate_round(p, obs, summarize_quality(obs, p), reg or registry(), directory=tmp_path)


def test_selection_uses_train_validation_only_and_retains_rejected_candidates(tmp_path):
    (result,) = evaluate(tmp_path)
    assert result.selected_parameters == {"fast": 5}
    assert result.status == "rejected"
    assert result.sealed_test.net_return > 0
    assert "validation_lower_edge" in result.reasons
    assert set(result.folds[0].baselines) == {"hold", "cash", "direction_matched"}
    assert len(result.folds[0].training_trials) == 2


def test_same_sealed_window_cannot_be_evaluated_twice(tmp_path):
    receipt = persist_sealed_test_receipt(tmp_path, round_hash="a", fold_id="2026-03-01")
    with pytest.raises(ValueError, match="sealed test already evaluated"):
        persist_sealed_test_receipt(tmp_path, round_hash="a", fold_id=receipt.fold_id)


def test_evaluator_reuses_retained_results_without_reopening_a_seal(tmp_path):
    first = evaluate(tmp_path)
    before = (tmp_path / "sealed-test-results.jsonl").read_bytes()
    assert evaluate(tmp_path) == first
    assert (tmp_path / "sealed-test-results.jsonl").read_bytes() == before


def test_concurrent_receipt_writers_only_one_succeeds(tmp_path):
    def reserve(_):
        try:
            persist_sealed_test_receipt(tmp_path, round_hash="a", fold_id="fold")
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(reserve, range(2))).count(True) == 1


def test_fixed_utc_windows_do_not_depend_on_row_counts():
    p = ResearchRoundProtocol.default(round_id="default", starts_at=START)
    folds = walk_forward_folds(p, START + timedelta(days=181))
    assert [(x.train_start, x.train_end, x.validation_end, x.test_end) for x in folds] == [
        (START, START + timedelta(days=90), START + timedelta(days=120), START + timedelta(days=150)),
        (
            START + timedelta(days=30),
            START + timedelta(days=120),
            START + timedelta(days=150),
            START + timedelta(days=180),
        ),
    ]


def test_causal_slice_ignores_future_availability_and_unfinalized_rows():
    items = bars((100, 101, 102))
    late = items[1].model_copy(update={"available_at": START + timedelta(days=1)})
    quote = items[2].model_copy(update={"close": None})
    assert causal_finalized_slice((items[0], late, quote), START, START + timedelta(minutes=3)) == (items[0],)


def test_quality_failure_prevents_strategy_and_sealed_evaluation(tmp_path):
    def forbidden(*args):
        pytest.fail("quality-excluded rows reached the strategy")

    p = protocol(minimum_coverage=Decimal("0.995"))
    (result,) = evaluate(tmp_path, p=p, reg=registry(forbidden))
    assert result.status == "insufficient_data"
    assert "coverage_below_minimum" in result.reasons
    assert not (tmp_path / "sealed-test-receipts.jsonl").exists()


def test_next_observation_fills_and_doubled_costs(tmp_path):
    (result,) = evaluate(tmp_path)
    (trade,) = result.folds[0].train.trades
    assert trade.decision_at == START + timedelta(minutes=1)
    assert trade.entry_at == START + timedelta(minutes=2)
    assert trade.exit_at == START + timedelta(minutes=3)
    assert trade.entry_price == Decimal("100.0500")
    assert trade.exit_price == Decimal("109.9450")
    assert result.train.stressed_net_return < result.train.net_return < result.train.gross_return
    assert trade.quantity <= Decimal("10")  # 1% of the observed 1,000 units.


def test_unavailable_execution_has_no_fills(tmp_path):
    obs = tuple(item.model_copy(update={"bid": None, "ask": None, "volume": None}) for item in observations())
    (result,) = evaluate(tmp_path, obs=obs)
    assert result.sealed_test.trade_count == 0
    assert "execution_unavailable" in result.sealed_test.reasons


def test_incomplete_fold_retains_all_candidates_without_consuming_test(tmp_path):
    p = protocol(
        candidates=(
            RoundCandidate(symbol="BTCUSDT", strategy_id="ema"),
            RoundCandidate(symbol="BTCUSDT", strategy_id="missing"),
        )
    )
    results = evaluate(tmp_path, p=p, obs=bars())
    assert len(results) == 2
    assert all(x.status == "insufficient_data" and "incomplete_fold" in x.reasons for x in results)
    assert not (tmp_path / "sealed-test-receipts.jsonl").exists()


def test_failed_generator_is_retained_without_leaking_exception_content(tmp_path):
    def failed(*args):
        raise RuntimeError("secret input must not escape")

    (result,) = evaluate(tmp_path, reg=registry(failed))
    assert result.status == "rejected"
    assert "strategy_evaluation_failed" in result.reasons
    assert "secret" not in result.model_dump_json()


def test_mutated_registry_requires_new_round(tmp_path):
    evaluate(tmp_path)
    changed = registry()
    entry = changed.resolve("ema")
    other = StrategyRegistry()
    other.register(entry.spec.model_copy(update={"warmup_bars": 3}), entry.generator)
    with pytest.raises(ValueError, match="evaluation identity"):
        evaluate(tmp_path, reg=other)


def test_real_registered_strategy_accepts_finalized_minute_ledger(tmp_path):
    from src.strategies.library import build_strategy_registry

    spec = StrategySpec(
        strategy_id="macd_histogram_trend",
        family=StrategyFamily.TREND,
        version="v1",
        intervals=(BarInterval.ONE_MINUTE,),
        warmup_bars=2,
        parameters={"fast_period": 2, "slow_period": 3, "signal_period": 2},
    )
    p = protocol(candidates=(RoundCandidate(symbol="BTCUSDT", strategy_id=spec.strategy_id),))
    obs = tuple(
        item.model_copy(update={"open": item.close, "high": item.close, "low": item.close}) for item in observations()
    )
    (result,) = evaluate(tmp_path, p=p, obs=obs, reg=build_strategy_registry((spec,)))
    assert len(result.folds[0].training_trials) == 1
    assert "strategy_evaluation_failed" not in result.reasons
    assert result.folds[0].receipt is not None


def test_failures_are_durably_retained_even_when_no_test_can_run(tmp_path):
    evaluate(tmp_path, p=protocol(minimum_coverage=Decimal("0.995")))
    content = (tmp_path / "candidate-results.jsonl").read_text()
    assert "coverage_below_minimum" in content
    assert "insufficient_data" in content


def test_hold_baseline_reports_unrealized_gross_return(tmp_path):
    (result,) = evaluate(tmp_path)
    hold = result.folds[0].baselines["hold"]
    assert hold.open_quantity > 0
    assert hold.gross_return > hold.net_return > 0
    assert hold.trade_count == 0


def test_late_and_hidden_quality_evidence_cannot_reach_strategy(tmp_path):
    def forbidden(*args):
        pytest.fail("late observations reached strategy")

    p = protocol()
    obs = list(observations())
    obs[2] = obs[2].model_copy(update={"available_at": obs[2].provider_at + timedelta(seconds=16)})
    register_round(p, tmp_path)
    (result,) = evaluate_round(
        p,
        tuple(x for i, x in enumerate(obs) if i != 2),
        summarize_quality(obs, p),
        registry(forbidden),
        directory=tmp_path,
    )
    assert result.status == "insufficient_data"
    assert "observation_stale" in result.reasons


def test_latency_waits_for_a_later_observation(tmp_path):
    (result,) = evaluate(tmp_path, p=protocol(latency_ms=90000))
    (trade,) = result.train.trades
    assert trade.entry_at == START + timedelta(minutes=3)
    assert trade.exit_at == START + timedelta(minutes=5)


def test_changed_trade_size_and_participation_gates_bound_execution(tmp_path):
    p = protocol(maximum_volume_participation=Decimal("0.00001"), maximum_initial_cash_exposure=Decimal("0.00001"))
    (result,) = evaluate(tmp_path, p=p)
    assert result.train.trades[0].quantity == Decimal("0.00099")


def test_positive_lower_edges_can_only_produce_paper_only_status(tmp_path):
    p = protocol(
        minimum_closed_trades=2,
        candidates=(RoundCandidate(symbol="BTCUSDT", strategy_id="ema"),),
        schedule=WalkForwardSchedule(starts_at=START, train_days=2, validation_days=2, sealed_test_days=2, step_days=2),
    )
    obs = sum((bars(day=day) for day in range(6)), ()) + bars((100,), day=6)
    (result,) = evaluate(tmp_path, p=p, obs=obs)
    assert result.status == "experimental_paper_only"
    assert result.paper_only is True
    assert result.qualification_status == "unqualified"
    assert result.sealed_test.lower_edge > 0


def test_failed_parameter_trials_remain_visible_alongside_selected_parameters(tmp_path):
    normal = registry().resolve("ema").generator

    def sometimes_fails(spec, frame, context):
        if spec.parameters["fast"] == 20:
            raise RuntimeError("bad parameter")
        return normal(spec, frame, context)

    (result,) = evaluate(tmp_path, reg=registry(sometimes_fails))
    assert len(result.folds[0].training_trials) == 2
    assert result.selected_parameters == {"fast": 5}
    assert "training_trial_failed" in result.reasons


def test_duplicate_eligible_rows_cannot_trigger_same_observation_execution(tmp_path):
    p, obs = protocol(), observations()
    register_round(p, tmp_path)
    with pytest.raises(ValueError, match="duplicate eligible source key"):
        evaluate_round(p, (obs[0],) + obs, summarize_quality(obs, p), registry(), directory=tmp_path)


def test_validation_failure_does_not_discard_training_trials(tmp_path):
    normal = registry().resolve("ema").generator

    def invalid_validation(spec, frame, context):
        if pd.Timestamp(frame.iloc[-1]["available_at"]).day == 2:
            raise RuntimeError("invalid validation")
        return normal(spec, frame, context)

    (result,) = evaluate(tmp_path, reg=registry(invalid_validation))
    assert len(result.folds[0].training_trials) == 2
    assert result.folds[0].receipt is None


def test_selection_is_durable_before_the_first_sealed_signal(tmp_path):
    import json

    normal = registry().resolve("ema").generator

    def inspect_seal(spec, frame, context):
        if pd.Timestamp(frame.iloc[-1]["available_at"]).day == 3:
            receipt = json.loads((tmp_path / "sealed-test-receipts.jsonl").read_text().splitlines()[0])
            assert receipt["selections"][0]["parameters"] == {"fast": 5}
            assert "validation_lower_edge" in receipt["selections"][0]["reasons"]
        return normal(spec, frame, context)

    (result,) = evaluate(tmp_path, reg=registry(inspect_seal))
    assert result.sealed_test.net_return > 0
    assert "sealed_evaluation_failed" not in result.reasons


def test_feature_context_is_bounded_without_future_rows(tmp_path):
    normal = registry().resolve("ema").generator

    def bounded(spec, frame, context):
        assert len(frame) <= 3
        return normal(spec, frame, context)

    (result,) = evaluate(tmp_path, p=protocol(maximum_feature_bars=3), reg=registry(bounded))
    assert result.sealed_test.net_return > 0


def test_strategy_warmup_exceeding_feature_cap_is_rejected(tmp_path):
    original = registry().resolve("ema")
    reg = StrategyRegistry()
    reg.register(original.spec.model_copy(update={"warmup_bars": 4}), original.generator)
    (result,) = evaluate(tmp_path, p=protocol(maximum_feature_bars=3), reg=reg)
    assert result.status == "rejected"
    assert "feature_cap_below_warmup" in result.reasons


def test_simultaneous_barriers_choose_stop_and_fill_only_later_quote(tmp_path):
    def always_long(spec, frame, context):
        return pd.DataFrame(
            {"signal": 1, "decision_timestamp": frame["available_at"], "data_through": frame["available_at"]}
        )

    obs = list(observations())
    obs[3] = obs[3].model_copy(update={"close": Decimal("100"), "low": Decimal("90"), "high": Decimal("110")})
    obs[4] = obs[4].model_copy(update={"bid": Decimal("95"), "ask": Decimal("95")})
    (result,) = evaluate(tmp_path, obs=obs, reg=registry(always_long))
    trade = result.train.trades[0]
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_at == START + timedelta(minutes=4)
    assert trade.exit_price == Decimal("94.9525")


def test_incremental_round_reuses_old_fold_and_evaluates_only_new_sealed_fold(tmp_path):
    (first,) = evaluate(tmp_path)
    original_seals = (tmp_path / "sealed-test-receipts.jsonl").read_bytes()
    extended = observations()[:-1] + bars(day=3) + bars((100,), day=4)
    (second,) = evaluate(tmp_path, obs=extended)
    assert len(second.folds) == 2
    assert second.folds[0] == first.folds[0]
    assert second.folds[1].fold.test_end == START + timedelta(days=4)
    updated_seals = (tmp_path / "sealed-test-receipts.jsonl").read_bytes()
    assert updated_seals.startswith(original_seals)
    assert len(updated_seals.splitlines()) == 2
    assert len((tmp_path / "sealed-test-results.jsonl").read_text().splitlines()) == 2


def test_changed_receipt_identity_is_not_reused(tmp_path):
    import json

    evaluate(tmp_path)
    path = tmp_path / "sealed-test-receipts.jsonl"
    receipt = json.loads(path.read_text())
    receipt["round_hash"] = "b" * 64
    path.write_text(json.dumps(receipt) + "\n")
    with pytest.raises(ValueError, match="sealed receipt identity"):
        evaluate(tmp_path)


def test_consumed_seal_without_complete_results_cannot_be_replayed(tmp_path):
    evaluate(tmp_path)
    (tmp_path / "candidate-results.jsonl").unlink()
    with pytest.raises(ValueError, match="sealed test has no complete retained results"):
        evaluate(tmp_path)
