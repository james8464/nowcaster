from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.background_research.contracts import LearningBatch, LearningCampaign, LearningStatus
from src.background_research.registry import LearningRegistry
from src.background_research.scheduler import LearningScheduler
from tests.background_research_fixtures import campaign_fixture


@pytest.fixture
def campaign(tmp_path):
    return campaign_fixture(tmp_path)


@pytest.fixture
def registry(tmp_path, campaign):
    result = LearningRegistry(tmp_path / "registry")
    result.register(campaign)
    return result


def reserve(registry, campaign, *, fingerprint="b" * 64, now=None):
    now = now or datetime(2026, 5, 31, tzinfo=UTC)
    return LearningScheduler(registry).next_batch(
        campaign,
        symbol="BTCUSDT",
        data_fingerprint=fingerprint,
        through=now,
        now=now,
    )


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_same_asset_day_and_fingerprint_cannot_buy_another_batch(registry, campaign):
    batch = reserve(registry, campaign)
    assert batch is not None
    before = snapshot(registry.root)
    assert registry.reserve_batch(batch) is False
    assert registry.reserve_batch(batch.model_copy(update={"batch_id": "other"})) is False
    assert snapshot(registry.root) == before


@pytest.mark.parametrize("day,allowed", [(1, False), (2, True)])
def test_direct_reservation_requires_campaign_registration_time(tmp_path, day, allowed):
    campaign = campaign_fixture(tmp_path).model_copy(update={"created_at": datetime(2026, 6, 2, tzinfo=UTC)})
    registry = LearningRegistry(tmp_path / "registry")
    registry.register(campaign)
    created_at = datetime(2026, 6, day, tzinfo=UTC)
    batch = LearningBatch(
        batch_id="direct",
        campaign_hash=campaign.identity_hash,
        symbol="BTCUSDT",
        utc_day=created_at.date(),
        data_fingerprint="b" * 64,
        training_start=datetime(2026, 1, 1, tzinfo=UTC),
        training_end=datetime(2026, 4, 1, tzinfo=UTC),
        validation_end=datetime(2026, 5, 1, tzinfo=UTC),
        holdout_end=datetime(2026, 5, 31, tzinfo=UTC),
        max_attempts=100,
        created_at=created_at,
    )
    before = snapshot(registry.root)
    source_before = snapshot(campaign.source_directory)
    assert registry.reserve_batch(batch) is allowed
    if not allowed:
        assert snapshot(registry.root) == before
    assert snapshot(campaign.source_directory) == source_before


def test_holdout_exposure_survives_campaign_and_dataset_rename(tmp_path, registry, campaign):
    batch = reserve(registry, campaign)
    token = registry.reserve_holdout(batch.batch_id, "a" * 64)
    other = campaign_fixture(tmp_path, campaign_id="renamed")
    registry.register(other)
    overlapping = reserve(registry, other, fingerprint="c" * 64, now=datetime(2026, 6, 1, tzinfo=UTC))
    assert overlapping is not None
    before = snapshot(registry.root)
    with pytest.raises(ValueError, match="exposed"):
        registry.reserve_holdout(overlapping.batch_id, "b" * 64)
    with pytest.raises(ValueError, match="exposed"):
        LearningRegistry(registry.root).reserve_holdout(batch.batch_id, "a" * 64)
    assert len(token) == 64
    assert snapshot(registry.root) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_attempts_per_batch", -1),
        ("max_attempts_per_batch", True),
        ("max_attempts_per_batch", 101),
        ("max_batches_per_asset_day", 0),
        ("max_batches_per_asset_day", True),
        ("max_batches_per_asset_day", 2),
        ("seed", True),
    ],
)
def test_campaign_rejects_unbounded_or_boolean_budgets(campaign, field, value):
    with pytest.raises(ValueError):
        LearningCampaign.model_validate(campaign.model_dump() | {field: value})


@pytest.mark.parametrize(
    "bad_time", [datetime(2026, 5, 31), datetime(2026, 5, 31, tzinfo=timezone(timedelta(hours=1)))]
)
def test_non_utc_campaign_is_rejected(campaign, bad_time):
    with pytest.raises(ValueError, match="UTC"):
        LearningCampaign.model_validate(campaign.model_dump() | {"created_at": bad_time})


def test_registration_is_immutable_and_does_not_write_source(registry, campaign):
    original_source = snapshot(campaign.source_directory)
    before = snapshot(registry.root)
    registry.register(campaign)
    with pytest.raises(ValueError, match="identity"):
        registry.register(campaign.model_copy(update={"seed": 1}))
    assert snapshot(registry.root) == before
    assert snapshot(campaign.source_directory) == original_source


@pytest.mark.parametrize(
    "field,value", [("fee_bps", Decimal("0")), ("minimum_coverage", Decimal("0.9")), ("minimum_closed_trades", 1)]
)
def test_source_gates_cannot_be_weakened(tmp_path, campaign, field, value):
    registry = LearningRegistry(tmp_path / "empty-registry")
    changed = campaign.model_copy(update={"cost_policy": campaign.cost_policy.model_copy(update={field: value})})
    with pytest.raises(ValueError, match="source protocol"):
        registry.register(changed)
    assert not list(registry.root.glob("*.json*"))


def test_concurrent_reservations_share_one_root_budget(registry, campaign):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: reserve(LearningRegistry(registry.root), campaign), range(8)))
    # Concurrent scheduler reentry returns the exact retained batch, never a second identity.
    assert len({batch.batch_id for batch in results if batch is not None}) == 1
    batch = next(batch for batch in results if batch is not None)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert not any(pool.map(lambda _: LearningRegistry(registry.root).reserve_batch(batch), range(8)))


def test_torn_ledger_blocks_without_repair(registry, campaign):
    ledger = registry.root / "events.jsonl"
    with ledger.open("ab") as stream:
        stream.write(b'{"kind":')
    before = snapshot(registry.root)
    with pytest.raises(ValueError, match="malformed"):
        reserve(registry, campaign)
    assert registry.read_status(campaign.identity_hash).state == "blocked"
    assert snapshot(registry.root) == before


def test_fsync_failure_preserves_committed_bytes(registry, campaign, monkeypatch):
    import os

    before = snapshot(registry.root)

    def fail(_):
        raise OSError("disk sync failed")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError, match="disk sync failed"):
        reserve(registry, campaign)
    assert snapshot(registry.root) == before


def test_reentry_after_crash_retains_attempts_and_original_batch(registry, campaign):
    batch = reserve(registry, campaign)
    registry.append_event(batch.batch_id, {"kind": "attempt", "attempt_id": "1", "candidate_hash": "c" * 64})
    registry.append_event(batch.batch_id, {"kind": "checkpoint", "checkpoint": "checkpoint-1.json"})
    recovered = LearningRegistry(registry.root)
    assert reserve(recovered, campaign) == batch
    status = recovered.read_status(campaign.identity_hash)
    assert status.attempt_count == status.batch_attempt_count == 1
    assert status.last_checkpoint == "checkpoint-1.json"
    assert status.paper_only is True


def test_failed_invalid_rejected_trials_persist_across_campaigns(tmp_path, registry, campaign):
    batch = reserve(registry, campaign)
    for index, outcome in enumerate(("failed", "invalid", "rejected")):
        registry.append_event(
            batch.batch_id,
            {"kind": "attempt", "attempt_id": str(index), "candidate_hash": "c" * 64, "outcome": outcome},
        )
    registry.append_event(batch.batch_id, {"kind": "state", "state": "completed", "reason": "budget finished"})
    other = campaign_fixture(tmp_path, campaign_id="next")
    registry.register(other)
    status = registry.read_status(other.identity_hash)
    assert status.attempt_count == 3
    assert status.failure_count == 2


def test_attempt_budget_and_duplicate_events_cannot_reset_counts(registry, campaign):
    batch = reserve(registry, campaign)
    for index in range(100):
        registry.append_event(batch.batch_id, {"kind": "attempt", "attempt_id": str(index), "candidate_hash": "c" * 64})
    before = snapshot(registry.root)
    for identifier in ("0", "101"):
        with pytest.raises(ValueError):
            registry.append_event(
                batch.batch_id, {"kind": "attempt", "attempt_id": identifier, "candidate_hash": "c" * 64}
            )
    assert snapshot(registry.root) == before


@pytest.mark.parametrize("protected_name", ["ProspectiveStudies", "live-paper-study"])
def test_symlink_into_protected_study_is_rejected(tmp_path, protected_name):
    protected = tmp_path / protected_name
    protected.mkdir()
    sentinel = protected / "sentinel"
    sentinel.write_bytes(b"untouched")
    alias = tmp_path / "innocent-alias"
    alias.symlink_to(protected, target_is_directory=True)
    with pytest.raises(ValueError, match="protected"):
        LearningRegistry(alias / "background")
    assert snapshot(protected) == {"sentinel": b"untouched"}


def test_registry_cannot_be_inside_source(registry, campaign):
    before = snapshot(campaign.source_directory)
    with pytest.raises(ValueError, match="source"):
        LearningRegistry(campaign.source_directory / "background")
    assert snapshot(campaign.source_directory) == before


def test_status_is_versioned_strict_json(registry, campaign):
    status = registry.read_status(campaign.identity_hash)
    assert LearningStatus.model_validate_json(status.model_dump_json()) == status
    for update in (
        {"schema_version": 2},
        {"paper_only": False},
        {"attempt_count": True},
        {"state": "trading"},
        {"extra": "field"},
    ):
        with pytest.raises(ValueError):
            LearningStatus.model_validate(status.model_dump() | update)


@pytest.mark.parametrize("update", [{"schema_version": True}, {"paper_only": 1}])
def test_status_rejects_boolean_version_and_integer_paper_flag(registry, campaign, update):
    with pytest.raises(ValueError):
        LearningStatus.model_validate(registry.read_status(campaign.identity_hash).model_dump() | update)


def test_source_manifest_symlink_cannot_read_protected_study(tmp_path, campaign):
    protected = tmp_path / "ProspectiveStudies"
    protected.mkdir()
    protocol = campaign.source_directory / "protocol.json"
    retained = protected / "protocol.json"
    protocol.rename(retained)
    protocol.symlink_to(retained)
    before = snapshot(protected)
    with pytest.raises(ValueError, match="protected"):
        LearningRegistry(tmp_path / "registry").register(campaign)
    assert snapshot(protected) == before


@pytest.mark.parametrize("failure_at", range(1, 8))
def test_each_transaction_fsync_failure_retains_prior_bytes(registry, campaign, monkeypatch, failure_at):
    import os

    before = snapshot(registry.root)
    original_fsync = os.fsync
    calls = 0

    def fail_selected(descriptor):
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OSError("injected disk failure")
        return original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_selected)
    with pytest.raises(OSError, match="injected disk failure"):
        reserve(registry, campaign)
    assert snapshot(registry.root) == before


def test_search_grammar_is_frozen_and_round_trips(campaign):
    from src.background_research.contracts import LearningSearchSpace
    from src.learning.grammar import RuleNode

    parameters = {"fast": (5, 10)}
    space = LearningSearchSpace(
        symbol="BTCUSDT",
        strategy_id="ema",
        parameter_grid=parameters,
        indicators=("close",),
        thresholds=(1.0,),
        seed_rules=(RuleNode.compare("gt", RuleNode.indicator("close", lag=1), RuleNode.number(1)),),
    )
    parameters["fast"] = (50,)
    recovered = LearningSearchSpace.model_validate_json(space.model_dump_json())
    assert recovered.to_search_space().parameter_grid["fast"] == (5, 10)
    assert recovered.seed_rules[0].children[0].lag == 1
    changed = campaign.model_copy(update={"search_spaces": (space, campaign.search_spaces[1])})
    assert LearningCampaign.model_validate_json(changed.model_dump_json()).identity_hash == changed.identity_hash
    assert changed.identity_hash != campaign.identity_hash


def test_attempt_completion_is_one_time_and_artifacts_remain_retained(registry, campaign):
    batch = reserve(registry, campaign)
    registry.append_event(batch.batch_id, {"kind": "attempt", "attempt_id": "1", "candidate_hash": "c" * 64})
    result = {"kind": "attempt_result", "attempt_id": "1", "candidate_hash": "c" * 64, "outcome": "failed"}
    registry.append_event(batch.batch_id, result)
    registry.append_event(
        batch.batch_id, {"kind": "artifact", "phase": "interruption", "payload": {"reason": "worker stopped"}}
    )
    before = snapshot(registry.root)
    with pytest.raises(ValueError, match="already retained"):
        registry.append_event(batch.batch_id, result)
    status = LearningRegistry(registry.root).read_status(campaign.identity_hash)
    assert status.failure_count == status.attempt_count == 1
    assert snapshot(registry.root) == before


def test_changed_source_blocks_resuming_without_mutating_registry(registry, campaign):
    import json

    reserve(registry, campaign)
    manifest = campaign.source_directory / "protocol.json"
    protocol = json.loads(manifest.read_text())
    protocol["fee_bps"] = "0"
    manifest.write_text(json.dumps(protocol))
    before = snapshot(registry.root)
    with pytest.raises(ValueError, match="source protocol"):
        reserve(registry, campaign)
    assert snapshot(registry.root) == before


def _crash_after_ledger_replace(root, campaign_json):
    import os

    original_replace = os.replace

    def crash_after_replace(source, destination):
        original_replace(source, destination)
        os._exit(73)

    os.replace = crash_after_replace
    reserve(LearningRegistry(root), LearningCampaign.model_validate_json(campaign_json))


def test_crash_after_ledger_commit_recovers_from_ledger_not_status(registry, campaign):
    import multiprocessing

    process = multiprocessing.get_context("spawn").Process(
        target=_crash_after_ledger_replace,
        args=(registry.root, campaign.model_dump_json()),
    )
    process.start()
    process.join(10)
    assert process.exitcode == 73
    recovered = LearningRegistry(registry.root)
    batch = reserve(recovered, campaign)
    assert batch is not None
    assert recovered.read_status(campaign.identity_hash).batch_id == batch.batch_id


def test_direct_reservation_is_atomic_across_processes(tmp_path, registry, campaign):
    import multiprocessing

    batch = reserve(registry, campaign)
    target = LearningRegistry(tmp_path / "concurrent-registry")
    target.register(campaign)
    context = multiprocessing.get_context("spawn")
    with context.Pool(4) as pool:
        results = pool.starmap(_direct_reserve, [(target.root, batch.model_dump_json())] * 8)
    assert results.count(True) == 1
    assert results.count(False) == 7


def _direct_reserve(root, batch_json):
    from src.background_research.contracts import LearningBatch

    return LearningRegistry(root).reserve_batch(LearningBatch.model_validate_json(batch_json))


def test_ledger_with_duplicate_json_keys_blocks_without_repair(registry, campaign):
    ledger = registry.root / "events.jsonl"
    raw = ledger.read_bytes().replace(b'"sequence":0', b'"sequence":99,"sequence":0', 1)
    ledger.write_bytes(raw)
    before = snapshot(registry.root)
    with pytest.raises(ValueError, match="malformed"):
        reserve(registry, campaign)
    assert snapshot(registry.root) == before


def test_schedule_and_candidate_families_must_match_source(tmp_path, campaign):
    registry = LearningRegistry(tmp_path / "registry")
    schedule = campaign.schedule.model_copy(update={"validation_days": 1})
    with pytest.raises(ValueError, match="source protocol"):
        registry.register(campaign.model_copy(update={"schedule": schedule}))
    space = campaign.search_spaces[0].model_copy(update={"strategy_id": "unregistered"})
    with pytest.raises(ValueError, match="source protocol"):
        registry.register(campaign.model_copy(update={"search_spaces": (space, campaign.search_spaces[1])}))


def test_seed_rules_must_respect_registered_maximum_lag():
    from src.background_research.contracts import LearningSearchSpace
    from src.learning.grammar import RuleNode

    with pytest.raises(ValueError, match="lag"):
        LearningSearchSpace(
            symbol="BTCUSDT",
            strategy_id="ema",
            indicators=("close",),
            thresholds=(1.0,),
            maximum_lag=2,
            seed_rules=(RuleNode.compare("gt", RuleNode.indicator("close", lag=3), RuleNode.number(1)),),
        )
