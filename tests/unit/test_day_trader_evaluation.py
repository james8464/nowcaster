"""Fixed, retained comparisons cannot turn selected paper outcomes into promotion."""

from datetime import timedelta
from decimal import Decimal

import pytest

from src.research.day_trader_decision import gate_suggestion
from src.research.day_trader_evaluation import (
    EvaluationLedger,
    EvaluationProtocol,
    FixedVariant,
)
from src.research.day_trader_evaluation import (
    evaluate_variants as _evaluate_variants,
)
from src.research.day_trader_lifecycle import LifecycleObservation, PaperLifecycle, advance_lifecycle
from src.research.round_two_contracts import RoundObservation
from tests.integration.test_day_trader_decision import NOW, context, suggestion


def evaluate_variants(protocol, lifecycles, variants):
    return _evaluate_variants(protocol, lifecycles, variants, evaluated_at=protocol.evaluation_end)


def variant(name="base", candidate="c"):
    return FixedVariant(variant_id=name, candidate_hash=candidate * 64, maximum_holding_seconds=600)


def protocol(variants=None, **changes):
    values = dict(
        source_protocol_hash="a" * 64,
        context_protocol_hash="f" * 64,
        registered_at=NOW - timedelta(days=2),
        training_end=NOW - timedelta(days=1),
        evaluation_end=NOW + timedelta(days=4),
        fold_days=1,
        variants=variants or (variant(),),
        minimum_outcomes=2,
        minimum_active_days=2,
        fee_bps="10",
        slippage_bps="5",
    )
    values.update(changes)
    return EvaluationProtocol(**values)


def history(at=NOW, close="104", candidate="c"):
    report = gate_suggestion(
        suggestion(
            decision_at=at, available_at=at, expires_at=at + timedelta(seconds=5), candidate_hash=candidate * 64
        ),
        context(
            decision_at=at,
            available_at=at,
            expires_at=at + timedelta(seconds=5),
            trends=tuple(
                dict(
                    timeframe_minutes=m, direction="up", strength="1", change_bps="10", last_bar_at=at.replace(second=0)
                )
                for m in (1, 5, 15)
            ),
        ),
        at,
    )
    initial = PaperLifecycle.from_report(report, created_at=at, maximum_holding_seconds=600)
    bar_at = at.replace(second=0) + timedelta(minutes=1)
    bar = RoundObservation(
        provider="binance",
        feed="spot",
        symbol="BTCUSDT",
        provider_at=bar_at,
        received_at=bar_at + timedelta(seconds=1),
        available_at=bar_at + timedelta(seconds=1),
        source_key=at.isoformat(),
        open=close,
        high=close,
        low=close,
        close=close,
        volume="100",
    )
    terminal = advance_lifecycle(initial, LifecycleObservation(bar=bar, evaluated_at=bar.available_at))
    return initial, terminal


def test_costs_apply_to_both_sides_and_drawdown_and_turnover_include_losses():
    p = protocol(maximum_drawdown_bps="100", maximum_daily_turnover="1")
    events = history(close="98") + history(NOW + timedelta(days=1))
    (result,) = evaluate_variants(p, events, p.variants)
    # Conservative entry 101, loss at 98: -297.0297 bps, less 30 bps round-trip cost.
    assert result.outcomes[0].net_return_bps == pytest.approx(Decimal("-327.0297029702970297029702970"))
    assert result.maximum_drawdown_bps >= Decimal("327")
    assert result.maximum_daily_turnover == 2
    assert "drawdown_exceeded" in result.exclusions
    assert "turnover_exceeded" in result.exclusions
    assert result.status == "diagnostic_only"


@pytest.mark.parametrize("failure", ["stale", "provider_error"])
def test_time_limit_does_not_price_stale_or_error_terminal_observations(failure):
    from tests.unit.test_day_trader_lifecycle import observation

    p = protocol()
    events = [history()[0]]
    for seconds in range(58, 598, 60):
        events.append(advance_lifecycle(events[-1], observation(seconds)))
    obs = observation(598, provider_error="feed_error" if failure == "provider_error" else None)
    terminal = LifecycleObservation(
        bar=obs.bar, evaluated_at=NOW + timedelta(seconds=620 if failure == "stale" else 600)
    )
    events.append(advance_lifecycle(events[-1], terminal))
    assert events[-1].exit_reason == "time_limit"
    (result,) = evaluate_variants(p, events, p.variants)
    assert result.outcomes[0].net_return_bps is None
    assert "terminal_price_unavailable" in result.outcomes[0].exclusions


def test_overlapping_stop_bar_open_cannot_repaint_post_entry_return():
    from tests.unit.test_day_trader_lifecycle import observation

    p = protocol()
    initial = history()[0]
    terminal = advance_lifecycle(initial, observation(58, open="90", low="90", high="100", close="98"))
    assert terminal.exit_reason == "invalidation"
    (result,) = evaluate_variants(p, (initial, terminal), p.variants)
    assert result.outcomes[0].net_return_bps == pytest.approx(Decimal("-327.0297029702970297029702970"))


def test_chronological_folds_purge_cross_boundary_and_never_score_training():
    p = protocol(training_end=(NOW - timedelta(days=1)).replace(second=0))
    training = history(NOW - timedelta(days=1, hours=1))
    crossing = history(p.training_end - timedelta(seconds=30))
    (result,) = evaluate_variants(p, training + crossing + history(), p.variants)
    assert result.scored_outcomes == 1
    assert "training_only" in result.outcomes[0].exclusions
    assert "crosses_fold_boundary" in result.outcomes[1].exclusions
    assert sum(f.scored_outcomes for f in result.folds) == 1
    assert all(f.training_end <= f.test_start < f.test_end for f in result.folds)
    assert "insufficient_outcomes" in result.exclusions


def test_empty_and_insample_only_preserve_every_variant_without_promotion():
    p = protocol((variant(), variant("other", "d")))
    results = evaluate_variants(p, history(NOW - timedelta(days=1, hours=1)), p.variants)
    assert len(results) == 2
    assert all(r.scored_outcomes == 0 and r.status == "diagnostic_only" for r in results)
    assert all("insufficient_outcomes" in r.exclusions for r in results)
    with pytest.raises(ValueError, match="registered variants"):
        evaluate_variants(p, (), p.variants[:1])


def test_multiple_testing_widens_uncertainty_and_keeps_all_folds():
    events = history() + history(NOW + timedelta(days=1), close="98")
    one = protocol()
    many = protocol((variant(), variant("other", "d")))
    (a,) = evaluate_variants(one, events, one.variants)
    b, _ = evaluate_variants(many, events, many.variants)
    assert b.adjusted_alpha < a.adjusted_alpha
    assert b.mean_lower_bps < a.mean_lower_bps < a.mean_upper_bps < b.mean_upper_bps
    assert len(a.folds) == 5
    assert "dependent_outcomes_uncertainty" in a.exclusions


def test_uncertainty_center_is_explicit_daily_block_mean_not_trade_weighted_mean():
    p = protocol()
    events = history() + history(NOW + timedelta(hours=1)) + history(NOW + timedelta(days=1), close="98")
    (result,) = evaluate_variants(p, events, p.variants)
    # Equal-weight daily blocks: one winning day and one losing day.
    assert result.daily_mean_return_bps == pytest.approx(Decimal("-30"))
    assert result.mean_return_bps > result.daily_mean_return_bps


def test_expiry_open_and_future_evidence_are_retained_without_inventing_returns():
    p = protocol()
    initial = history()[0]
    expired = advance_lifecycle(initial, LifecycleObservation(evaluated_at=NOW + timedelta(minutes=2)))
    (result,) = evaluate_variants(p, (initial, expired), p.variants)
    assert result.outcomes[0].net_return_bps is None
    assert "unpriced_expiry" in result.outcomes[0].exclusions
    (result,) = evaluate_variants(p, (initial,), p.variants)
    assert "incomplete" in result.outcomes[0].exclusions
    (result,) = evaluate_variants(p, history(p.evaluation_end), p.variants)
    assert "outside_evaluation_window" in result.outcomes[0].exclusions


def test_unpriced_and_open_lifecycles_still_count_hypothetical_activity():
    p = protocol()
    initial = history()[0]
    expired = advance_lifecycle(initial, LifecycleObservation(evaluated_at=NOW + timedelta(minutes=2)))
    (closed,) = evaluate_variants(p, (initial, expired), p.variants)
    (opened,) = evaluate_variants(p, (initial,), p.variants)
    assert closed.maximum_daily_turnover == 2
    assert opened.maximum_daily_turnover == 1


def test_same_decision_cannot_be_counted_twice_by_changing_creation_time():
    p = protocol()
    initial = history()[0]
    duplicate = PaperLifecycle.from_report(
        initial.origin_report, created_at=NOW + timedelta(seconds=1), maximum_holding_seconds=600
    )
    with pytest.raises(ValueError, match="duplicate decision"):
        evaluate_variants(p, (initial, duplicate), p.variants)


def test_outcomes_not_yet_known_at_evaluation_time_cannot_contribute():
    p = protocol()
    (result,) = _evaluate_variants(p, history(), p.variants, evaluated_at=NOW)
    assert result.scored_outcomes == 0
    assert "outcome_not_yet_known" in result.outcomes[0].exclusions


def test_forged_or_omitted_lifecycle_chain_and_foreign_candidates_rejected():
    p = protocol()
    initial, terminal = history()
    with pytest.raises(ValueError, match="initial"):
        evaluate_variants(p, (terminal,), p.variants)
    with pytest.raises(ValueError):
        evaluate_variants(p, (initial, terminal.model_copy(update={"exit_reason": "invalidation"})), p.variants)
    with pytest.raises(ValueError, match="candidate"):
        evaluate_variants(p, history(candidate="d"), p.variants)


def test_evaluation_ledger_idempotent_append_recovery_and_corruption(tmp_path):
    p = protocol()
    ledger = EvaluationLedger(tmp_path, p)
    results = evaluate_variants(p, history(), p.variants)
    ledger.append(results, history())
    original = ledger.events_path.read_bytes()
    ledger.append(results, history())
    assert ledger.events_path.read_bytes() == original
    assert EvaluationLedger(tmp_path, p).evaluations() == (results,)
    changed = evaluate_variants(p, history(close="98"), p.variants)
    with pytest.raises(ValueError, match="rewritten"):
        ledger.append(changed, history(close="98"))
    with ledger.events_path.open("ab") as stream:
        stream.write(b"{")
    with pytest.raises(ValueError):
        ledger.append(results, history())


def test_append_later_completion_preserves_initial_evaluation_and_all_history(tmp_path):
    p = protocol()
    initial, terminal = history()
    ledger = EvaluationLedger(tmp_path, p)
    first = evaluate_variants(p, (initial,), p.variants)
    ledger.append(first, (initial,))
    prefix = ledger.events_path.read_bytes()
    final = evaluate_variants(p, (initial, terminal), p.variants)
    ledger.append(final, (initial, terminal))
    assert ledger.events_path.read_bytes().startswith(prefix)
    assert ledger.evaluations() == (first, final)


def test_ledger_recomputes_metrics_and_rejects_forged_rehashed_result(tmp_path):
    from src.research.day_trader_evaluation import VariantEvaluation
    from src.strategies.types import canonical_hash

    p = protocol()
    (result,) = evaluate_variants(p, history(), p.variants)
    wire = result.model_dump(mode="json", exclude={"result_hash"})
    wire["scored_outcomes"] = 1000
    fake = VariantEvaluation.model_validate({**wire, "result_hash": canonical_hash(wire)})
    with pytest.raises(ValueError, match="recomputed"):
        EvaluationLedger(tmp_path, p).append((fake,), history())


def test_protocol_and_results_cannot_be_mutated_or_smuggle_actions(tmp_path):
    p = protocol()
    (result,) = evaluate_variants(p, history(), p.variants)
    with pytest.raises(ValueError):
        result.status = "qualified"
    with pytest.raises(ValueError):
        EvaluationProtocol.model_validate({**p.model_dump(), "order": "buy"})
    EvaluationLedger(tmp_path, p)
    with pytest.raises(ValueError):
        EvaluationLedger(tmp_path, p.model_copy(update={"fee_bps": Decimal("0")}))
    with pytest.raises(ValueError):
        protocol(registered_at=NOW)
