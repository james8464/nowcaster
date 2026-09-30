from pathlib import Path

import pytest

from src.background_research import runtime
from src.background_research.registry import LearningRegistry
from src.background_research.training import LearningTrainer
from src.deep_research.control import ResearchControl
from tests.background_research_fixtures import learning_fixture


def test_runtime_rejects_library_fixture_identity_before_dispatch(tmp_path):
    campaign, _, _ = learning_fixture(tmp_path)
    registry = LearningRegistry(tmp_path / "registry")
    registry.register(campaign)
    control = ResearchControl(tmp_path / "control", run_id="identity", nonce="n" * 32)
    control.initialize()
    with pytest.raises(ValueError, match="runtime.*identity"):
        runtime.BackgroundLearningRunner(registry, LearningTrainer(registry)).run(
            campaign.identity_hash, control=control, emit=lambda _: None
        )
    assert registry.read_status(campaign.identity_hash).attempt_count == 0


def test_source_runtime_identity_detects_uncommitted_code_and_environment_change(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    module = tmp_path / "src/example.py"
    module.write_text("VALUE = 1\n")
    first = runtime.runtime_code_identity(source_root=tmp_path)
    module.write_text("VALUE = 2\n")
    assert runtime.runtime_code_identity(source_root=tmp_path) != first
    second = runtime.runtime_code_identity(source_root=tmp_path)
    monkeypatch.setattr(runtime, "environment_identity", lambda: {"changed": "environment"})
    assert runtime.runtime_code_identity(source_root=tmp_path) != second


def test_background_environment_drops_provider_credentials(monkeypatch):
    import os

    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setenv("ALPACA_API_KEY", "must-not-inherit")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-inherit")
    monkeypatch.setenv("UNRECOGNIZED_PROVIDER_TOKEN", "must-not-inherit")
    monkeypatch.setenv("_PYI_UNRECOGNIZED_PROVIDER_TOKEN", "must-not-inherit")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    runtime.restrict_background_environment()
    import os

    assert "ALPACA_API_KEY" not in os.environ
    assert "AWS_SECRET_ACCESS_KEY" not in os.environ
    assert "UNRECOGNIZED_PROVIDER_TOKEN" not in os.environ
    assert "_PYI_UNRECOGNIZED_PROVIDER_TOKEN" not in os.environ
    assert os.environ["PATH"] == "/usr/bin:/bin"


def test_environment_digest_hashes_actual_distribution_artifacts(tmp_path):
    from scripts.engine_manifest import distribution_identity

    artifact = tmp_path / "library.py"
    artifact.write_text("value = 1\n")

    class SyntheticDistribution:
        version = "1.0"
        files = [Path("library.py")]

        def locate_file(self, path):
            return tmp_path / path

    original = distribution_identity(SyntheticDistribution())
    artifact.write_text("value = 2\n")
    assert distribution_identity(SyntheticDistribution()) != original


def test_manual_worker_limit_reserves_host_cores_without_changing_campaign(tmp_path, monkeypatch):
    monkeypatch.setattr("src.background_research.training.os.cpu_count", lambda: 8)
    registry = LearningRegistry(tmp_path / "registry")
    assert LearningTrainer(registry).workers == 4
    assert LearningTrainer(registry, workers=2).workers == 2
    with pytest.raises(ValueError, match="workers"):
        LearningTrainer(registry, workers=7)
    with pytest.raises(ValueError, match="workers"):
        LearningTrainer(registry, workers=0)


@pytest.mark.parametrize("boundary", ["source", "prefix", "trainer"])
def test_stop_during_real_preparation_preserves_receipts_and_completes(tmp_path, monkeypatch, boundary):
    from src.background_research import data, training
    from src.deep_research.candidates import generate_candidates
    from src.deep_research.control import ControlState
    from tests.unit.test_background_research_data import reserved

    campaign, _, _, registry, batch = reserved(tmp_path)
    monkeypatch.setattr(runtime, "runtime_code_identity", lambda: campaign.code_hash)
    control = ResearchControl(tmp_path / "control", run_id="preparing", nonce="n" * 32)
    control.initialize()
    for attempt in generate_candidates(campaign.search_spaces[0].to_search_space(), count=2, seed=campaign.seed):
        event = {
            "attempt_id": f"{batch.batch_id}:{attempt.ordinal}",
            "candidate_hash": attempt.candidate.identity,
            "payload": {"ordinal": attempt.ordinal, "generation": 1, "candidate": attempt.candidate.payload()},
        }
        registry.append_event(batch.batch_id, {**event, "kind": "attempt"})
        if attempt.ordinal == 1:
            registry.append_event(batch.batch_id, {**event, "kind": "attempt_result", "outcome": "completed"})
    prefix = registry.ledger.read_bytes()
    source = {p: p.read_bytes() for p in campaign.source_directory.iterdir() if p.is_file()}
    owner, name = {
        "source": (data, "read_learning_source"),
        "prefix": (data, "load_learning_data"),
        "trainer": (training, "load_learning_data"),
    }[boundary]
    original = getattr(owner, name)

    def stop_after_preparation(*args, **kwargs):
        result = original(*args, **kwargs)
        control.request(ControlState.STOPPED)
        return result

    monkeypatch.setattr(owner, name, stop_after_preparation)
    emitted = []
    status = runtime.BackgroundLearningRunner(registry, LearningTrainer(registry)).run(
        campaign.identity_hash, control=control, emit=emitted.append
    )
    assert status.state == "paused"
    assert status.batch_attempt_count == 2
    assert registry.ledger.read_bytes().startswith(prefix)
    with registry._locked():
        state, _, _ = registry._read()
    results = [event.outcome for event in state.events[batch.batch_id] if event.kind == "attempt_result"]
    assert sorted(results) == ["completed", "interrupted"]
    assert emitted[-1]["event"] == "complete"
    assert not any(event["event"] == "error" for event in emitted)
    assert all(p.read_bytes() == content for p, content in source.items())
    assert control.read() is ControlState.STOPPED


def test_stop_does_not_hide_unrelated_preparation_failure(tmp_path, monkeypatch):
    from src.background_research import training
    from src.deep_research.control import ControlState
    from tests.unit.test_background_research_data import reserved

    campaign, _, _, registry, batch = reserved(tmp_path)
    monkeypatch.setattr(runtime, "runtime_code_identity", lambda: campaign.code_hash)
    control = ResearchControl(tmp_path / "control", run_id="preparing", nonce="n" * 32)
    control.initialize()
    original = training.load_learning_data

    def fail_after_preparation(*args, **kwargs):
        original(*args, **kwargs)
        control.request(ControlState.STOPPED)
        raise ValueError("synthetic checkpoint failure")

    monkeypatch.setattr(training, "load_learning_data", fail_after_preparation)
    emitted = []
    with pytest.raises(ValueError, match="synthetic checkpoint failure"):
        runtime.BackgroundLearningRunner(registry, LearningTrainer(registry)).run(
            campaign.identity_hash, control=control, emit=emitted.append
        )
    assert registry.read_status(campaign.identity_hash).state == "blocked"
    assert emitted[-1]["event"] == "error"


@pytest.mark.parametrize("loss", ["unlinked", "replaced", "symlink", "hardlink"])
def test_lost_campaign_lock_blocks_visible_status_without_new_dispatch(tmp_path, monkeypatch, loss):
    """Catches continuing under an unlinked lock that a second worker can replace."""
    from src.deep_research.control import ControlState

    campaign, _, _ = learning_fixture(tmp_path)
    registry = LearningRegistry(tmp_path / "registry")
    registry.register(campaign)
    monkeypatch.setattr(runtime, "runtime_code_identity", lambda: campaign.code_hash)
    control = ResearchControl(tmp_path / "control", run_id="lost-lock", nonce="n" * 32)
    control.initialize()
    prefix = registry.ledger.read_bytes()
    emitted = []

    def lose_lock(event):
        emitted.append(event)
        if event["event"] == "progress":
            lock = registry.root / f".campaign-{campaign.identity_hash}.worker.lock"
            if loss == "hardlink":
                (tmp_path / "aliased-lock").hardlink_to(lock)
            else:
                lock.unlink()
                if loss == "replaced":
                    lock.touch()
                elif loss == "symlink":
                    target = tmp_path / "other-lock"
                    target.touch()
                    lock.symlink_to(target)

    # Bound a broken implementation to one further loop; STOP must not hide loss.
    monkeypatch.setattr(runtime.time, "sleep", lambda _: control.request(ControlState.STOPPED))
    with pytest.raises(ValueError, match="lock"):
        runtime.BackgroundLearningRunner(registry, LearningTrainer(registry)).run(
            campaign.identity_hash, control=control, emit=lose_lock
        )
    assert emitted[-1]["event"] == "error"
    assert emitted[-1]["status"]["state"] == "blocked"
    assert emitted[-1]["status"]["attempt_count"] == 0
    assert registry.ledger.read_bytes() == prefix


def test_lock_loss_after_training_result_does_not_write_shared_checkpoint_or_disturb_new_owner(tmp_path, monkeypatch):
    from contextlib import ExitStack

    from tests.unit.test_background_research_data import reserved

    campaign, _, _, registry, batch = reserved(tmp_path, training_count=1440)
    monkeypatch.setattr(runtime, "runtime_code_identity", lambda: campaign.code_hash)
    control = ResearchControl(tmp_path / "control", run_id="lost-during-training", nonce="n" * 32)
    control.initialize()
    original_append = registry.append_event
    retained = {}
    emitted = []
    with ExitStack() as owners:

        def replace_after_result(batch_id, event, **kwargs):
            result = original_append(batch_id, event, **kwargs)
            if event["kind"] == "attempt_result" and not retained:
                lock = registry.root / f".campaign-{campaign.identity_hash}.worker.lock"
                lock.unlink()
                retained["new_owner"] = owners.enter_context(runtime._exclusive(lock))
                retained["prefix"] = registry.ledger.read_bytes()
            return result

        monkeypatch.setattr(registry, "append_event", replace_after_result)
        with pytest.raises(ValueError, match="lock"):
            runtime.BackgroundLearningRunner(registry, LearningTrainer(registry, workers=1)).run(
                campaign.identity_hash, control=control, emit=emitted.append
            )
        assert retained["prefix"] == registry.ledger.read_bytes()
        retained["new_owner"]()  # Old execution neither unlinks nor signals a new owner.
    assert emitted[-1]["event"] == "error"
    assert emitted[-1]["status"]["state"] == "blocked"
    assert emitted[-1]["status"]["batch_id"] == batch.batch_id
    assert "OwnershipLostError" in emitted[-1]["message"]


def test_training_write_rechecks_ownership_after_waiting_for_registry_lock(tmp_path, monkeypatch):
    """An outer check cannot authorize a commit after a blocked writer loses its lock."""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import ExitStack, contextmanager
    from threading import Event

    from tests.unit.test_background_research_data import reserved

    campaign, _, _, registry, batch = reserved(tmp_path)
    trainer = LearningTrainer(registry, workers=1)
    writer_waiting = Event()
    original_locked = registry._locked

    @contextmanager
    def observed_lock():
        writer_waiting.set()
        with original_locked():
            yield

    monkeypatch.setattr(registry, "_locked", observed_lock)
    path = registry.root / f".campaign-{campaign.identity_hash}.worker.lock"
    with runtime._exclusive(path) as verify, ExitStack() as owners, ThreadPoolExecutor(max_workers=1) as executor:
        trainer._verify_ownership = verify
        prefix = registry.ledger.read_bytes()
        with original_locked():
            pending = executor.submit(trainer._append, batch, kind="state", state="training", reason="obsolete")
            assert writer_waiting.wait(5)
            path.unlink()
            replacement_verify = owners.enter_context(runtime._exclusive(path))
        with pytest.raises(runtime.OwnershipLostError):
            pending.result(timeout=5)
        assert registry.ledger.read_bytes() == prefix
        replacement_verify()
        # Replacement lock file is retained, not removed by the obsolete writer.
        assert path.exists()
