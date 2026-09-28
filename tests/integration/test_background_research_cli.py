"""Real subprocess lifecycle against marked synthetic retained receipts."""

import json
import os
import select
import signal
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

from src.background_research.registry import LearningRegistry
from src.deep_research.control import ControlState, ResearchControl
from src.research.round_two_quality import append_observations
from tests.background_research_fixtures import learning_fixture

ROOT = Path(__file__).resolve().parents[2]
ENTRY = [sys.executable, str(ROOT / "scripts/live_engine_entry.py")]
PACKAGED = ROOT / "build/engine/dist/nowcaster-engine"


def fixture(tmp_path, *, eligible=False, attempts=4):
    campaign, protocol, observations = learning_fixture(
        tmp_path, training_count=1440 if eligible else None, attempts=attempts
    )
    # The last retained provider minute closes the registered three-day window.
    last = observations[-1].model_copy(
        update={
            "source_key": "synthetic-endpoint",
            "provider_at": campaign.created_at - timedelta(minutes=1),
            "received_at": campaign.created_at - timedelta(seconds=59),
            "available_at": campaign.created_at - timedelta(seconds=58),
        }
    )
    append_observations(campaign.source_directory, protocol, (last,))
    manifest = tmp_path / "synthetic-campaign.json"
    payload = campaign.model_dump(mode="json")
    payload.pop("code_hash")
    manifest.write_text(json.dumps(payload))
    return campaign, manifest


def register(tmp_path, *, eligible=False, attempts=4, entry=ENTRY):
    campaign, manifest = fixture(tmp_path, eligible=eligible, attempts=attempts)
    before = {p.name: p.read_bytes() for p in campaign.source_directory.iterdir() if p.is_file()}
    started = time.monotonic()
    result = subprocess.run(
        [
            *entry,
            "strategy",
            "register-background-research",
            "--registry-directory",
            str(tmp_path / "registry"),
            "--manifest",
            str(manifest),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    if entry == [str(PACKAGED)]:
        print(f"Packaged registration: {time.monotonic() - started:.3f}s")
    event = json.loads(result.stdout.splitlines()[-1])
    assert event["event"] == "registered"
    assert event["schema_version"] == 1
    assert before == {p.name: p.read_bytes() for p in campaign.source_directory.iterdir() if p.is_file()}
    return event["campaign_hash"], before, campaign.source_directory


def launch(tmp_path, campaign_hash, *, run_id="execution-1", nonce="n" * 32, entry=ENTRY, workers=None):
    process = subprocess.Popen(
        [
            *entry,
            "strategy",
            "background-research",
            "--registry-directory",
            str(tmp_path / "registry"),
            "--campaign-hash",
            campaign_hash,
            "--run-id",
            run_id,
            "--control-directory",
            str(tmp_path / "control"),
            "--control-nonce",
            nonce,
            *(["--workers", str(workers)] if workers is not None else []),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    process._launched_at = time.monotonic()
    return process


def event_until(process, predicate, timeout=45):
    deadline = time.monotonic() + timeout
    # Unbuffered OS reads avoid select() missing lines already buffered by TextIO.
    pending = getattr(process, "_event_pending", b"")
    while time.monotonic() < deadline:
        if b"\n" not in pending:
            ready, _, _ = select.select([process.stdout], [], [], 0.2)
            if not ready:
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise AssertionError(f"Worker ended: {process.poll()} {process.stderr.read()}")
            pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            event = json.loads(line)
            if not hasattr(process, "_seen_events"):
                process._seen_events = []
            process._seen_events.append(event)
            if event.get("event") == "ownership":
                process._ownership = event
            if predicate(event):
                process._event_pending = pending
                return event
    raise AssertionError("Worker event timed out")


def stop(process, tmp_path, *, run_id="execution-1", nonce="n" * 32):
    control = ResearchControl(tmp_path / "control", run_id=run_id, nonce=nonce)
    if process.poll() is None:
        control.request(ControlState.STOPPED)
    process.wait(timeout=45)
    assert process.returncode == 0, process.stderr.read()


def cleanup(process):
    if process.poll() is None:
        owner = getattr(process, "_ownership", None)
        if owner is not None:
            from src.background_research.runtime import process_identity

            try:
                assert process_identity(owner["pid"]) == (
                    owner["process_start_seconds"],
                    owner["process_start_microseconds"],
                )
                assert os.getpgid(owner["pid"]) == owner["pid"]
                os.killpg(owner["pid"], signal.SIGKILL)
            except (ProcessLookupError, ValueError, FileNotFoundError):
                pass
        else:
            process.kill()
    process.wait(timeout=10)


def test_real_registration_waiting_ownership_and_second_worker_denied(tmp_path):
    campaign_hash, before, source = register(tmp_path)
    first = launch(tmp_path, campaign_hash)
    try:
        handshake = event_until(first, lambda _: True)
        assert handshake["event"] == "ownership"
        assert handshake["run_id"] == "execution-1"
        assert handshake["nonce"] == "n" * 32
        assert handshake["pid"] == handshake["process_group_id"] == first.pid
        waiting = event_until(first, lambda e: e.get("status", {}).get("state") == "waiting")
        assert waiting["status"]["batch_attempt_count"] == 0
        registry = LearningRegistry(tmp_path / "registry")
        ledger = registry.ledger.read_bytes()
        second = launch(tmp_path, campaign_hash, run_id="execution-2")
        second.wait(timeout=30)
        assert second.returncode != 0
        assert "owned" in second.stderr.read()
        time.sleep(0.3)
        assert registry.ledger.read_bytes() == ledger
        stop(first, tmp_path)
        assert before == {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()}
    finally:
        cleanup(first)


def test_nonce_mismatch_and_terminal_control_are_not_reinitialized(tmp_path):
    campaign_hash, _, _ = register(tmp_path)
    control = ResearchControl(tmp_path / "control", run_id="execution-1", nonce="a" * 32)
    control.initialize()
    original = control.path.read_bytes()
    process = launch(tmp_path, campaign_hash)
    process.wait(timeout=30)
    assert process.returncode != 0
    assert control.path.read_bytes() == original
    control.request(ControlState.STOPPED)
    original = control.path.read_bytes()
    process = launch(tmp_path, campaign_hash, nonce="a" * 32)
    process.wait(timeout=30)
    assert process.returncode != 0
    assert control.path.read_bytes() == original


@pytest.mark.parametrize("packaged", [False, True])
def test_broken_pipe_stops_owned_worker_retains_results_and_releases_lock(tmp_path, packaged):
    if packaged and not PACKAGED.exists():
        pytest.skip("build existing engine helper to exercise frozen output-loss shutdown")
    entry = [str(PACKAGED)] if packaged else ENTRY
    campaign_hash, before, source = register(tmp_path, eligible=True, attempts=50, entry=entry)
    process = launch(tmp_path, campaign_hash, entry=entry, workers=1)
    try:
        event_until(process, lambda e: e.get("stage") == "search", timeout=120)
        registry = LearningRegistry(tmp_path / "registry")
        prefix = registry.ledger.read_bytes()
        with registry._locked():
            state, _, _ = registry._read()
        status = registry.read_status(campaign_hash)
        completed = [event for event in state.events[status.batch_id] if event.outcome == "completed"]
        assert completed
        process.stdout.close()
        # Transport loss is an orderly stop, without any test-side control request.
        process.wait(timeout=45)
        assert process.returncode == 0, process.stderr.read()
        assert registry.ledger.read_bytes().startswith(prefix)
        with registry._locked():
            state, _, _ = registry._read()
        events = state.events[status.batch_id]
        assert all(event in events for event in completed)
        assert any(event.outcome == "interrupted" for event in events)
        assert {event.attempt_id for event in events if event.kind == "attempt"} == {
            event.attempt_id for event in events if event.kind == "attempt_result"
        }
        assert registry.read_status(campaign_hash).state == "paused"
        control = ResearchControl(tmp_path / "control", run_id="execution-1", nonce="n" * 32)
        assert control.read() == ControlState.STOPPED
        retained = registry.ledger.read_bytes()
        time.sleep(0.3)
        assert registry.ledger.read_bytes() == retained
        owner = process._ownership
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            listing = subprocess.check_output(["/bin/ps", "-axo", "pgid=,stat="], text=True)
            live_groups = {
                int(parts[0])
                for line in listing.splitlines()
                if len(parts := line.split()) == 2 and not parts[1].startswith("Z")
            }
            if owner["process_group_id"] not in live_groups:
                break
            time.sleep(0.1)
        assert owner["process_group_id"] not in live_groups
        # Fresh launch proves both the execution and campaign ownership lock exited.
        terminal_control = control.path.read_bytes()
        resumed = launch(tmp_path, campaign_hash, run_id="execution-2", entry=entry, workers=1)
        try:
            resumed_status = event_until(resumed, lambda e: e.get("status", {}).get("state") == "waiting", timeout=120)[
                "status"
            ]
            assert resumed_status["batch_attempt_count"] == 50
            assert control.path.read_bytes() == terminal_control
            stop(resumed, tmp_path, run_id="execution-2")
        finally:
            cleanup(resumed)
        assert before == {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()}
    finally:
        cleanup(process)


def test_term_checkpoint_and_authenticated_relaunch_keep_batch_and_attempt_prefix(tmp_path):
    campaign_hash, _, _ = register(tmp_path, eligible=True, attempts=20)
    process = launch(tmp_path, campaign_hash, workers=1)
    registry = LearningRegistry(tmp_path / "registry")
    try:
        event_until(process, lambda e: e["event"] == "ownership")
        deadline = time.monotonic() + 45
        while registry.read_status(campaign_hash).batch_attempt_count == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        status = registry.read_status(campaign_hash)
        assert status.batch_attempt_count > 0
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=45)
        assert process.returncode == 0, process.stderr.read()
        with registry._locked():
            state, _, _ = registry._read()
        prefix = tuple(e for e in state.events[status.batch_id] if e.kind == "attempt")
        results = {e.attempt_id for e in state.events[status.batch_id] if e.kind == "attempt_result"}
        assert results == {e.attempt_id for e in prefix}
        old_control = (tmp_path / "control/execution-1.control.json").read_bytes()
        old_databases = {
            path: path.read_bytes() for path in (tmp_path / "registry/batches").glob("*/training-*.duckdb")
        }
        resumed = launch(
            tmp_path, campaign_hash, run_id="execution-2", workers=min(2, max(1, (os.cpu_count() or 1) - 2))
        )
        try:
            event_until(resumed, lambda e: e.get("status", {}).get("state") == "waiting", timeout=90)
            after = registry.read_status(campaign_hash)
            assert after.batch_id == status.batch_id
            with registry._locked():
                state, _, _ = registry._read()
            attempts = tuple(e for e in state.events[status.batch_id] if e.kind == "attempt")
            assert attempts[: len(prefix)] == prefix
            assert (tmp_path / "control/execution-1.control.json").read_bytes() == old_control
            assert all(path.read_bytes() == content for path, content in old_databases.items())
            stop(resumed, tmp_path, run_id="execution-2")
        finally:
            cleanup(resumed)
    finally:
        cleanup(process)


@pytest.mark.parametrize("packaged", [False, True])
def test_timeout_cleanup_kills_owned_compute_group_and_crash_resume_retains_attempts(tmp_path, packaged):
    if packaged and not PACKAGED.exists():
        pytest.skip("build existing engine helper to exercise frozen process ownership")
    entry = [str(PACKAGED)] if packaged else ENTRY
    campaign_hash, _, _ = register(tmp_path, eligible=True, attempts=50, entry=entry)
    process = launch(tmp_path, campaign_hash, entry=entry)
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    registry = LearningRegistry(tmp_path / "registry")
    try:
        owner = event_until(process, lambda e: e["event"] == "ownership")
        if packaged:
            print(f"Packaged ownership: {time.monotonic() - process._launched_at:.3f}s")
        assert owner["process_start_seconds"] > 0
        assert 0 <= owner["process_start_microseconds"] < 1_000_000
        assert owner["pid"] == process.pid or owner["parent_pid"] == process.pid
        children = []
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and not children:
            listing = subprocess.check_output(["/bin/ps", "-axo", "pid=,pgid=,command="], text=True)
            children = [
                int(parts[0])
                for line in listing.splitlines()
                if len(parts := line.strip().split(None, 2)) == 3
                and int(parts[1]) == owner["process_group_id"]
                and ("spawn_main" in parts[2] or "--multiprocessing-fork" in parts[2])
            ]
            time.sleep(0.02)
        assert children, "Expected actual candidate compute children"
        os.kill(owner["pid"], signal.SIGSTOP)
        status = registry.read_status(campaign_hash)
        assert status.batch_attempt_count > 0
        with registry._locked():
            state, _, _ = registry._read()
        original = tuple(e for e in state.events[status.batch_id] if e.kind == "attempt")
        from src.background_research.runtime import process_identity

        identity = process_identity(owner["pid"])
        assert identity == (owner["process_start_seconds"], owner["process_start_microseconds"])
        assert os.getpgid(owner["pid"]) == owner["pid"]
        # Native timeout simulation: only this authenticated, still-owned isolated group.
        os.killpg(owner["process_group_id"], signal.SIGKILL)
        process.wait(timeout=10)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            listing = subprocess.check_output(["/bin/ps", "-axo", "pid=,stat="], text=True)
            live = {
                int(parts[0])
                for line in listing.splitlines()
                if len(parts := line.split()) == 2 and not parts[1].startswith("Z")
            }
            if not set(children).intersection(live):
                break
            time.sleep(0.1)
        assert not set(children).intersection(live)
        assert unrelated.poll() is None
        resumed = launch(tmp_path, campaign_hash, run_id="execution-2", entry=entry)
        try:
            event_until(resumed, lambda e: e.get("status", {}).get("state") in {"waiting", "failed"}, timeout=90)
            with registry._locked():
                state, _, _ = registry._read()
            attempts = tuple(e for e in state.events[status.batch_id] if e.kind == "attempt")
            assert attempts[: len(original)] == original
            assert any(e.outcome == "interrupted" for e in state.events[status.batch_id])
            stop(resumed, tmp_path, run_id="execution-2")
        finally:
            cleanup(resumed)
    finally:
        cleanup(process)
        unrelated.terminate()
        unrelated.wait(timeout=10)


def test_corrupt_checkpoint_blocks_visible_error_without_new_attempts(tmp_path):
    campaign_hash, _, _ = register(tmp_path, eligible=True, attempts=20)
    process = launch(tmp_path, campaign_hash)
    registry = LearningRegistry(tmp_path / "registry")
    try:
        event_until(process, lambda e: e["event"] == "ownership")
        deadline = time.monotonic() + 45
        while registry.read_status(campaign_hash).batch_attempt_count == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=45)
        count = registry.read_status(campaign_hash).attempt_count
        # Derived checkpoint is scoped to the authenticated execution; corrupt it
        # and call the library runner with that same identity without resetting it.
        from src.background_research.runtime import BackgroundLearningRunner
        from src.background_research.training import LearningTrainer
        from src.strategies.types import canonical_hash

        database = next((tmp_path / "registry/batches").glob("*/training-*.duckdb"), None)
        if database is None:
            pytest.fail("No actual training checkpoint was created")
        database.write_bytes(b"synthetic corrupt checkpoint")
        control = ResearchControl(tmp_path / "control", run_id="execution-1", nonce="n" * 32)
        # STOPPED stays terminal; fresh execution's linked/corrupt checkpoint must also fail.
        fresh = ResearchControl(tmp_path / "control", run_id="execution-2", nonce="m" * 32)
        fresh.initialize()
        target = database.parent / f"training-{canonical_hash(fresh.run_id)}.duckdb"
        target.write_bytes(b"synthetic corrupt checkpoint")
        with pytest.raises(Exception, match="database|Database|invalid"):
            BackgroundLearningRunner(registry, LearningTrainer(registry)).run(
                campaign_hash, control=fresh, emit=lambda _: None
            )
        assert registry.read_status(campaign_hash).attempt_count == count
        assert control.read() == ControlState.STOPPED
    finally:
        cleanup(process)


def test_stop_immediately_after_ownership_exits_without_reserving_work(tmp_path):
    campaign_hash, _, _ = register(tmp_path, eligible=True)
    process = launch(tmp_path, campaign_hash)
    try:
        event_until(process, lambda e: e["event"] == "ownership")
        stop(process, tmp_path)
        assert LearningRegistry(tmp_path / "registry").read_status(campaign_hash).attempt_count == 0
    finally:
        cleanup(process)


@pytest.mark.parametrize("packaged", [False, True])
def test_stop_at_trainer_preparation_lock_is_orderly_and_preserves_receipts(tmp_path, packaged):
    from datetime import UTC, datetime

    from src.background_research.data import read_learning_source
    from src.background_research.scheduler import LearningScheduler
    from src.deep_research.candidates import generate_candidates
    from src.research.round_two_registry import jsonl_writer_lock
    from src.strategies.types import canonical_hash

    if packaged and not PACKAGED.exists():
        pytest.skip("build existing engine helper to exercise frozen preparation stop")
    entry = [str(PACKAGED)] if packaged else ENTRY
    campaign_hash, before, source_path = register(tmp_path, eligible=True, entry=entry)
    registry = LearningRegistry(tmp_path / "registry")
    with registry._locked():
        state, _, _ = registry._read()
    campaign = state.campaigns[campaign_hash][0]
    now = datetime.now(UTC)
    source = read_learning_source(campaign, now=now)
    batch = LearningScheduler(registry).next_batch(
        campaign, symbol="BTCUSDT", data_fingerprint=source.data_fingerprint, through=source.through, now=now
    )
    attempt = next(iter(generate_candidates(campaign.search_spaces[0].to_search_space(), count=1, seed=campaign.seed)))
    registry.append_event(
        batch.batch_id,
        {
            "kind": "attempt",
            "attempt_id": f"{batch.batch_id}:{attempt.ordinal}",
            "candidate_hash": attempt.candidate.identity,
            "payload": {"ordinal": attempt.ordinal, "generation": 1, "candidate": attempt.candidate.payload()},
        },
    )
    prefix = registry.ledger.read_bytes()
    directory = registry.root / "batches" / canonical_hash(batch.batch_id)
    process = None
    try:
        # Hold the real preparation lock, then observe the authenticated worker's
        # open descriptor before STOP. No production sleeps/hooks/test-only flags.
        with jsonl_writer_lock(directory / "worker"):
            process = launch(tmp_path, campaign_hash, entry=entry)
            owner = event_until(process, lambda e: e["event"] == "ownership", timeout=60)
            assert owner["pid"] == process.pid or owner["parent_pid"] == process.pid
            lock = (directory / ".worker.lock").resolve()
            deadline = time.monotonic() + 45
            preparing = False
            while time.monotonic() < deadline:
                if sys.platform == "darwin":
                    listing = subprocess.run(
                        ["/usr/sbin/lsof", "-a", "-p", str(owner["pid"]), "-Fn"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    ).stdout.splitlines()
                    preparing = f"n{lock}" in listing
                else:
                    preparing = any(path.resolve() == lock for path in Path(f"/proc/{owner['pid']}/fd").iterdir())
                if preparing:
                    break
                time.sleep(0.1)
            assert preparing, "worker never reached its real trainer preparation lock"
            control = ResearchControl(tmp_path / "control", run_id="execution-1", nonce="n" * 32)
            control.request(ControlState.STOPPED)
        event = event_until(process, lambda e: e["event"] == "complete", timeout=45)
        process.wait(timeout=10)
        assert process.returncode == 0, process.stderr.read()
        assert event["status"]["state"] == "paused"
        assert event["status"]["batch_attempt_count"] == 1
        assert not any(e["event"] == "error" for e in process._seen_events)
        assert registry.ledger.read_bytes().startswith(prefix)
        with registry._locked():
            state, _, _ = registry._read()
        assert [e.outcome for e in state.events[batch.batch_id] if e.kind == "attempt_result"] == ["interrupted"]
        assert control.read() is ControlState.STOPPED
        assert before == {p.name: p.read_bytes() for p in source_path.iterdir() if p.is_file()}
    finally:
        if process is not None:
            cleanup(process)


def test_public_source_cli_registers_same_contract(tmp_path):
    campaign_hash, _, _ = register(tmp_path, entry=[sys.executable, "-m", "src.cli"])
    assert LearningRegistry(tmp_path / "registry").read_status(campaign_hash).campaign_id == "learning"


@pytest.mark.parametrize("command", ["register-background-research", "background-research"])
def test_public_background_cli_restricts_environment_before_scientific_imports(command):
    probe = """
import importlib.abc
import os
import runpy
import sys
os.environ['ALPACA_API_KEY'] = 'synthetic-must-not-inherit'
os.environ['OMP_NUM_THREADS'] = '8'
class ImportBoundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition('.')[0] in {'numpy', 'pandas', 'scipy', 'sklearn'}:
            assert os.environ.get('OMP_NUM_THREADS') == '1', 'numeric limits applied after scientific import'
            assert 'ALPACA_API_KEY' not in os.environ, 'credential reached scientific import'
sys.meta_path.insert(0, ImportBoundary())
sys.argv = ['src.cli', 'strategy', sys.argv[1], '--help']
try:
    runpy.run_module('src.cli', run_name='__main__')
except SystemExit as error:
    assert error.code == 0
assert os.environ['OMP_NUM_THREADS'] == '1'
assert 'ALPACA_API_KEY' not in os.environ
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, command], cwd=ROOT, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_public_cli_legacy_import_retains_environment_and_commands():
    probe = """
import os
os.environ['ALPACA_API_KEY'] = 'synthetic-legacy-sentinel'
import src.cli
assert os.environ['ALPACA_API_KEY'] == 'synthetic-legacy-sentinel'
from typer.testing import CliRunner
result = CliRunner().invoke(src.cli.app, ['monitor', '--help'])
assert result.exit_code == 0, result.output
assert 'run' in result.output
"""
    result = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_training_progress_carries_versioned_status_after_durable_results(tmp_path):
    campaign_hash, _, _ = register(tmp_path, eligible=True)
    process = launch(tmp_path, campaign_hash)
    try:
        event_until(process, lambda e: e.get("status", {}).get("state") == "waiting", timeout=90)
        progress = [event for event in process._seen_events if event["event"] == "progress"]
        assert len(progress) > 1
        assert all(event.get("schema_version") == 1 and "status" in event for event in progress)
        assert all(event["status"]["campaign_hash"] == campaign_hash for event in progress)
        stop(process, tmp_path)
    finally:
        cleanup(process)


def test_packaged_helper_runs_eligible_training_with_manifest_parity_and_source_immutability(tmp_path):
    if not PACKAGED.exists():
        pytest.skip("build existing engine helper to exercise frozen candidate evaluation")
    from scripts.engine_manifest import file_hash, verify_manifest

    manifest = json.loads(PACKAGED.with_name("engine-manifest.json").read_text())
    assert verify_manifest(ROOT, PACKAGED, manifest)
    assert manifest["executable_sha256"] == file_hash(PACKAGED)
    if sys.platform == "darwin":
        subprocess.run(["codesign", "--verify", "--strict", str(PACKAGED)], check=True, capture_output=True)
    campaign_hash, before, source = register(tmp_path, eligible=True, entry=[str(PACKAGED)])
    process = launch(tmp_path, campaign_hash, entry=[str(PACKAGED)])
    try:
        owner = event_until(process, lambda e: e["event"] == "ownership", timeout=60)
        print(f"Packaged warm ownership: {time.monotonic() - process._launched_at:.3f}s")
        assert owner["pid"] == process.pid or owner["parent_pid"] == process.pid
        status = event_until(process, lambda e: e.get("status", {}).get("state") == "waiting", timeout=120)["status"]
        assert status["batch_attempt_count"] == 4
        assert status["batch_failure_count"] < 4
        print(f"Packaged training complete: {time.monotonic() - process._launched_at:.3f}s")
        stop(process, tmp_path)
        assert before == {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()}
    finally:
        cleanup(process)
