from datetime import UTC, datetime, timedelta

import pytest

from src.background_research.registry import LearningRegistry
from src.background_research.scheduler import LearningScheduler
from tests.background_research_fixtures import campaign_fixture


def setup(tmp_path):
    campaign = campaign_fixture(tmp_path)
    registry = LearningRegistry(tmp_path / "registry")
    registry.register(campaign)
    return campaign, registry, LearningScheduler(registry)


def dispatch(scheduler, campaign, day, fingerprint="b" * 64, **kwargs):
    return scheduler.next_batch(
        campaign, symbol="BTCUSDT", data_fingerprint=fingerprint, through=day, now=day, **kwargs
    )


def test_waits_for_complete_registered_windows(tmp_path):
    campaign, registry, scheduler = setup(tmp_path)
    assert dispatch(scheduler, campaign, datetime(2026, 5, 30, tzinfo=UTC)) is None
    batch = dispatch(scheduler, campaign, datetime(2026, 5, 31, tzinfo=UTC))
    assert batch.training_start == datetime(2026, 1, 1, tzinfo=UTC)
    assert batch.training_end == datetime(2026, 4, 1, tzinfo=UTC)
    assert batch.validation_end == datetime(2026, 5, 1, tzinfo=UTC)
    assert batch.holdout_end == datetime(2026, 5, 31, tzinfo=UTC)


def test_scheduler_rechecks_ownership_inside_transaction_before_reserving(tmp_path, monkeypatch):
    campaign, registry, scheduler = setup(tmp_path)
    prefix = registry.ledger.read_bytes()
    original_read = registry._read
    valid = True

    def verify():
        if not valid:
            raise ValueError("ownership lost while scheduling")

    def lose_after_read():
        nonlocal valid
        result = original_read()
        valid = False
        return result

    monkeypatch.setattr(registry, "_read", lose_after_read)
    with pytest.raises(ValueError, match="ownership lost"):
        dispatch(scheduler, campaign, datetime(2026, 5, 31, tzinfo=UTC), verify_ownership=verify)
    assert registry.ledger.read_bytes() == prefix
    monkeypatch.setattr(registry, "_read", original_read)
    assert dispatch(scheduler, campaign, datetime(2026, 5, 31, tzinfo=UTC)) is not None


def test_unchanged_fingerprint_cannot_buy_new_batch_later(tmp_path):
    campaign, registry, scheduler = setup(tmp_path)
    batch = dispatch(scheduler, campaign, datetime(2026, 5, 31, tzinfo=UTC))
    registry.append_event(batch.batch_id, {"kind": "state", "state": "completed", "reason": "done"})
    assert dispatch(scheduler, campaign, datetime(2026, 7, 1, tzinfo=UTC)) is None


def test_new_fingerprint_waits_for_registered_step_then_advances(tmp_path):
    campaign, registry, scheduler = setup(tmp_path)
    batch = dispatch(scheduler, campaign, datetime(2026, 5, 31, tzinfo=UTC))
    registry.append_event(batch.batch_id, {"kind": "state", "state": "completed", "reason": "done"})
    assert dispatch(scheduler, campaign, datetime(2026, 6, 1, tzinfo=UTC), "c" * 64) is None
    next_batch = dispatch(scheduler, campaign, datetime(2026, 6, 30, tzinfo=UTC), "c" * 64)
    assert next_batch.training_start == datetime(2026, 1, 31, tzinfo=UTC)
    assert next_batch.holdout_end == datetime(2026, 6, 30, tzinfo=UTC)


def test_clock_rollback_cannot_dispatch_or_resume(tmp_path):
    campaign, registry, scheduler = setup(tmp_path)
    batch = dispatch(scheduler, campaign, datetime(2026, 6, 1, tzinfo=UTC))
    assert dispatch(scheduler, campaign, datetime(2026, 5, 31, tzinfo=UTC), "c" * 64) is None
    assert dispatch(scheduler, campaign, datetime(2026, 6, 2, tzinfo=UTC), "c" * 64) == batch


def test_renaming_campaign_does_not_reset_daily_budget(tmp_path):
    campaign, registry, scheduler = setup(tmp_path)
    day = datetime(2026, 5, 31, tzinfo=UTC)
    dispatch(scheduler, campaign, day)
    other = campaign_fixture(tmp_path, campaign_id="renamed")
    registry.register(other)
    assert dispatch(scheduler, other, day, "c" * 64) is None


def test_scheduler_rejects_non_utc_and_future_through(tmp_path):
    campaign, _, scheduler = setup(tmp_path)
    with pytest.raises(ValueError, match="UTC"):
        dispatch(scheduler, campaign, datetime(2026, 5, 31))
    day = datetime(2026, 5, 31, tzinfo=UTC)
    with pytest.raises(ValueError, match="future"):
        scheduler.next_batch(
            campaign, symbol="BTCUSDT", data_fingerprint="b" * 64, through=day + timedelta(days=1), now=day
        )


@pytest.mark.parametrize("state", ["paused", "pausing", "blocked"])
def test_scheduler_does_not_resume_a_paused_or_blocked_batch(tmp_path, state):
    campaign, registry, scheduler = setup(tmp_path)
    day = datetime(2026, 5, 31, tzinfo=UTC)
    batch = dispatch(scheduler, campaign, day)
    registry.append_event(batch.batch_id, {"kind": "state", "state": state, "reason": "user or safety stop"})
    assert dispatch(scheduler, campaign, day + timedelta(days=1)) is None


def test_clock_cannot_precede_campaign_registration(tmp_path):
    campaign = campaign_fixture(tmp_path).model_copy(update={"created_at": datetime(2026, 6, 2, tzinfo=UTC)})
    registry = LearningRegistry(tmp_path / "registry")
    registry.register(campaign)
    assert dispatch(LearningScheduler(registry), campaign, datetime(2026, 6, 1, tzinfo=UTC)) is None


def test_completed_wait_advances_only_new_registered_window_but_unfinished_wait_resumes(tmp_path):
    campaign, registry, scheduler = setup(tmp_path)
    day = datetime(2026, 5, 31, tzinfo=UTC)
    batch = dispatch(scheduler, campaign, day)
    registry.append_event(batch.batch_id, {"kind": "state", "state": "waiting", "reason": "temporary resource wait"})
    assert dispatch(scheduler, campaign, day + timedelta(days=30), "c" * 64) == batch
    registry.append_event(batch.batch_id, {"kind": "completion", "state": "waiting", "reason": "holdout exhausted"})
    assert registry.read_status(campaign.identity_hash).state == "waiting"
    assert dispatch(scheduler, campaign, day + timedelta(days=1), "c" * 64) is None
    assert dispatch(scheduler, campaign, day + timedelta(days=30), "b" * 64) is None
    next_batch = dispatch(scheduler, campaign, day + timedelta(days=30), "c" * 64)
    assert next_batch.training_start == datetime(2026, 1, 31, tzinfo=UTC)
    with pytest.raises(ValueError, match="terminal"):
        registry.append_event(batch.batch_id, {"kind": "state", "state": "training", "reason": "retry"})
