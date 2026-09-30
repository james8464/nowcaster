import math
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pandas as pd
import pytest

from src.background_research.data import load_learning_data, read_learning_source
from src.background_research.preparation import _GRAMMAR
from src.background_research.registry import LearningRegistry
from src.background_research.scheduler import LearningScheduler
from src.research.round_two_quality import append_observations
from tests.background_research_fixtures import learning_fixture


def test_declared_rule_features_are_causal_and_missing_operands_fail_closed():
    from src.background_research.data import _rule_active, _rule_features
    from src.background_research.preparation import _GRAMMAR
    from src.learning.grammar import RuleNode
    from src.strategies.indicators import build_indicators
    from src.strategies.session import SessionCalendar

    frame = pd.DataFrame(
        {
            "open_timestamp": pd.date_range("2026-01-01", periods=70, freq="min", tz="UTC"),
            "available_at": pd.date_range("2026-01-01 00:01:02", periods=70, freq="min", tz="UTC"),
            "finalized": True,
            "open": range(100, 170),
            "high": range(102, 172),
            "low": range(98, 168),
            "close": range(101, 171),
            "volume": 1000.0,
        }
    )
    session = SessionCalendar.continuous_utc()
    features = _rule_features(frame, session, 20)
    expected = build_indicators(frame, session)
    prefix = _rule_features(frame.iloc[:50], session, 20)
    pd.testing.assert_frame_equal(prefix, features.iloc[:50])
    for names, _ in _GRAMMAR.values():
        for name in names:
            pd.testing.assert_series_equal(features[name], expected[name])
            assert features[name].iloc[-1] > 0
            rule = RuleNode.compare("gt", RuleNode.indicator(name), RuleNode.number(0))
            pd.testing.assert_series_equal(_rule_active(rule, prefix), _rule_active(rule, features).iloc[:50])
    negated = RuleNode("not", (RuleNode.compare("gt", RuleNode.indicator("ema_26", lag=2), RuleNode.number(1e9)),))
    assert not _rule_active(negated, features).iloc[:27].any()
    assert _rule_active(negated, features).iloc[27:].all()
    missing = features.copy()
    missing.loc[60, "ema_26"] = float("nan")
    assert not _rule_active(negated, missing).iloc[62]
    for column, value in [("finalized", False), ("available_at", frame.available_at.iloc[0]), ("close", float("nan"))]:
        invalid = frame.copy()
        invalid.loc[30, column] = value
        with pytest.raises(ValueError):
            _rule_features(invalid, session, 20)


@pytest.mark.parametrize("family,names", _GRAMMAR.items())
def test_prepared_campaign_evaluates_every_declared_indicator(tmp_path, family, names):
    import json

    from src.background_research.contracts import LearningCampaign
    from src.background_research.preparation import prepare_background_research
    from src.background_research.training import candidate_payload
    from src.deep_research.candidates import CandidateDefinition
    from src.deep_research.evaluation import evaluate_candidate_payload
    from src.learning.grammar import RuleNode
    from src.research.round_two_registry import register_round

    original, protocol, rows = learning_fixture(tmp_path, native_context=True, observations_per_day=70)
    protocol = protocol.model_copy(
        update={"candidates": (protocol.candidates[0].model_copy(update={"strategy_id": family}),)}
    )
    source = register_round(protocol, tmp_path / "prepared-source")
    append_observations(source, protocol, rows)
    output = tmp_path / "manifest" / "campaign.json"
    prepare_background_research(
        source_directory=source,
        output=output,
        campaign_id="prepared",
        seed=42,
        created_at=original.created_at.isoformat(),
    )
    campaign = LearningCampaign.model_validate({**json.loads(output.read_text()), "code_hash": "a" * 64})
    registry = LearningRegistry(tmp_path / "registry")
    registry.register(campaign)
    retained = read_learning_source(campaign, now=campaign.created_at)
    batch = LearningScheduler(registry).next_batch(
        campaign,
        symbol="BTCUSDT",
        data_fingerprint=retained.data_fingerprint,
        through=campaign.created_at,
        now=campaign.created_at,
    )
    data = load_learning_data(campaign, batch, now=campaign.created_at)
    assert campaign.search_spaces[0].indicators == names[0]
    for name in names[0]:
        candidate = CandidateDefinition(
            "rule", family, rule=RuleNode.compare("gte", RuleNode.indicator(name), RuleNode.number(0))
        )
        evidence = evaluate_candidate_payload(candidate_payload(candidate, data, data.training, symbol="BTCUSDT"))
        assert len(evidence.decisions) == 70
        assert any(signal for _, signal in evidence.decisions)
        payload = candidate_payload(candidate, data, data.training, symbol="BTCUSDT")
        prefix = evaluate_candidate_payload(
            replace(
                payload,
                retained_input=replace(payload.retained_input, observations=payload.retained_input.observations[:50]),
            )
        )
        assert prefix.decisions == evidence.decisions[:50]


def reserved(tmp_path, **kwargs):
    campaign, protocol, observations = learning_fixture(tmp_path, **kwargs)
    registry = LearningRegistry(tmp_path / "learning")
    registry.register(campaign)
    source = read_learning_source(campaign, now=campaign.created_at)
    batch = LearningScheduler(registry).next_batch(
        campaign,
        symbol="BTCUSDT",
        data_fingerprint=source.data_fingerprint,
        through=campaign.created_at,
        now=campaign.created_at,
    )
    return campaign, protocol, observations, registry, batch


def test_original_source_prefix_survives_new_receipts_and_phases_have_real_availability(tmp_path):
    campaign, protocol, observations, _, batch = reserved(tmp_path)
    before = load_learning_data(campaign, batch, now=campaign.created_at)
    future = observations[-1].model_copy(
        update={
            "source_key": "new",
            "provider_at": campaign.created_at,
            "received_at": campaign.created_at,
            "available_at": campaign.created_at,
            "open": Decimal("999999"),
            "high": Decimal("999999"),
            "low": Decimal("999999"),
            "close": Decimal("999999"),
        }
    )
    append_observations(campaign.source_directory, protocol, (future,))
    after = load_learning_data(campaign, batch, now=campaign.created_at + timedelta(days=1))
    assert before == after
    assert len(after.training.observations) == 40
    assert after.training.observations[0].available_at.second == 2
    assert all(
        row.provider_at < batch.training_end and row.available_at < batch.training_end
        for row in after.training.observations
    )
    assert "coverage_below_minimum" in after.training.reasons
    assert after.training.coverage < Decimal("0.995")


def test_replaced_or_missing_original_prefix_blocks_recovery(tmp_path):
    campaign, protocol, _, _, batch = reserved(tmp_path)
    path = campaign.source_directory / "observations.jsonl"
    path.write_bytes(path.read_bytes().replace(b'"close":"101"', b'"close":"100"', 1))
    with pytest.raises(ValueError, match="prefix|fingerprint"):
        load_learning_data(campaign, batch, now=campaign.created_at)


def test_future_receipt_and_late_revision_never_enter_earlier_phase(tmp_path):
    campaign, protocol, observations, _, batch = reserved(tmp_path)
    late = observations[0].model_copy(
        update={
            "source_key": "revision",
            "received_at": batch.validation_end,
            "available_at": batch.validation_end,
            "close": Decimal("102"),
        }
    )
    append_observations(campaign.source_directory, protocol, (late,))
    # Explicitly bind a new source prefix to inspect its phase partition.
    source = read_learning_source(campaign, now=campaign.created_at)
    data = load_learning_data(
        campaign, batch.model_copy(update={"data_fingerprint": source.data_fingerprint}), now=campaign.created_at
    )
    assert data.training.observations == observations[:40]
    assert late in data.observations
    assert late not in data.validation.observations
    assert late not in data.holdout.observations


def test_actual_replay_decisions_and_trades_are_invariant_to_unavailable_extreme_prices(tmp_path):
    from src.background_research.training import candidate_payload
    from src.deep_research.candidates import CandidateDefinition
    from src.deep_research.evaluation import evaluate_candidate_payload
    from src.learning.grammar import RuleNode

    campaign, _, _, _, batch = reserved(tmp_path)
    data = load_learning_data(campaign, batch, now=campaign.created_at)
    candidate = CandidateDefinition(
        "rule",
        "desk_donchian_breakout_1m",
        rule=RuleNode.compare("gt", RuleNode.indicator("close"), RuleNode.number(100)),
    )
    payload = candidate_payload(candidate, data, data.training, symbol="BTCUSDT")
    original = evaluate_candidate_payload(payload)
    cutoff = data.training.observations[32].available_at
    rows = tuple(
        row
        if row.available_at < cutoff
        else row.model_copy(
            update={
                "open": Decimal("999999"),
                "high": Decimal("999999"),
                "low": Decimal("999999"),
                "close": Decimal("999999"),
                "bid": Decimal("999998"),
                "ask": Decimal("1000000"),
            }
        )
        for row in data.training.observations
    )
    changed = evaluate_candidate_payload(
        replace(payload, retained_input=replace(payload.retained_input, observations=rows))
    )
    earlier_decisions = tuple(item for item in original.decisions if item[0] < cutoff)
    earlier_trades = tuple(trade for trade in original.retained_metrics.trades if trade.exit_at < cutoff)
    assert len(earlier_decisions) == 32
    assert earlier_trades
    assert earlier_decisions == tuple(item for item in changed.decisions if item[0] < cutoff)
    assert earlier_trades == tuple(trade for trade in changed.retained_metrics.trades if trade.exit_at < cutoff)
    assert all(trade.entry_at > trade.decision_at for trade in earlier_trades)
    assert all(trade.fees > 0 and trade.slippage_cost > 0 and trade.spread_cost > 0 for trade in earlier_trades)
    assert original.retained_metrics.stressed_net_return <= original.retained_metrics.net_return
    compounded_net = (
        math.prod(1 + gross - cost for gross, cost in zip(original.gross_returns, original.costs, strict=True)) - 1
    )
    assert compounded_net == pytest.approx(float(original.retained_metrics.net_return))
    revision = rows[0].model_copy(
        update={
            "source_key": "late-revision",
            "received_at": cutoff,
            "available_at": cutoff,
            "open": Decimal("999999"),
            "high": Decimal("999999"),
            "low": Decimal("999999"),
            "close": Decimal("999999"),
        }
    )
    append_observations(campaign.source_directory, data.protocol, (revision,))
    revised_source = read_learning_source(campaign, now=campaign.created_at)
    revised_data = load_learning_data(
        campaign,
        batch.model_copy(update={"data_fingerprint": revised_source.data_fingerprint}),
        now=campaign.created_at,
    )
    revised = evaluate_candidate_payload(
        candidate_payload(candidate, revised_data, revised_data.training, symbol="BTCUSDT")
    )
    assert revised.decisions == original.decisions
    assert revised.retained_metrics.trades == original.retained_metrics.trades
    assert "observation_stale" in revised.retained_metrics.reasons
