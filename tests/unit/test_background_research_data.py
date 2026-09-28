import math
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from src.background_research.data import load_learning_data, read_learning_source
from src.background_research.registry import LearningRegistry
from src.background_research.scheduler import LearningScheduler
from src.research.round_two_quality import append_observations
from tests.background_research_fixtures import learning_fixture


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
