"""Credential-free, authenticated ownership of a checkpointable research worker."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from scripts.engine_manifest import digest, environment_identity, file_hash, source_hashes
from src.deep_research.control import ControlState, ResearchControl

if TYPE_CHECKING:
    from src.background_research.contracts import LearningCampaign, LearningStatus
    from src.background_research.registry import LearningRegistry
    from src.background_research.training import LearningTrainer


def restrict_background_environment() -> None:
    """Allow only local process essentials, never provider/account credentials."""
    allowed = {"HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SYSTEMROOT"}
    # Only the installed bootloader's known private markers may reach its children.
    if getattr(sys, "frozen", False):
        allowed.update(
            {"_PYI_ARCHIVE_FILE", "_PYI_APPLICATION_HOME_DIR", "_PYI_PARENT_PROCESS_LEVEL", "_PYI_SPLASH_IPC"}
        )
    retained = {key: value for key, value in os.environ.items() if key in allowed}
    os.environ.clear()
    os.environ.update(retained)
    # Apply the existing worker limits before importing numpy/pandas themselves.
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[name] = "1"


def runtime_code_identity(*, source_root: Path | None = None) -> str:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        adjacent = executable.parent / "engine-manifest.json"
        path = adjacent if adjacent.is_file() else executable.parent.parent / "Resources/engine-manifest.json"
        manifest = json.loads(path.read_text())
        build = json.loads((Path(sys._MEIPASS) / "engine-build.json").read_text())
        if any(manifest.get(key) != value for key, value in build.items()):
            raise ValueError("frozen runtime build identity mismatch")
        if manifest.get("executable_sha256") != file_hash(executable):
            raise ValueError("frozen runtime executable identity mismatch")
        # Runtime config is extracted beside the bundled code and may be used by replay.
        for name, expected in build["source_files"].items():
            if name.startswith("config/") and file_hash(Path(sys._MEIPASS) / name) != expected:
                raise ValueError("frozen runtime configuration identity mismatch")
        return digest({"mode": "frozen", "manifest": manifest})
    root = source_root or Path(__file__).resolve().parents[2]
    return digest({"mode": "source", "files": source_hashes(root), "environment": environment_identity()})


def process_identity(pid: int) -> tuple[int, int]:
    """OS process birth identity; native ownership checks must compare both fields."""
    if sys.platform == "darwin":
        import ctypes

        class BSDInfo(ctypes.Structure):
            _fields_ = [
                ("prefix", ctypes.c_uint32 * 12),
                ("names", ctypes.c_char * 48),
                ("details", ctypes.c_uint32 * 6),
                ("seconds", ctypes.c_uint64),
                ("microseconds", ctypes.c_uint64),
            ]

        info = BSDInfo()
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        size = library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        if size != ctypes.sizeof(info):
            raise ValueError("process birth identity is unavailable")
        return info.seconds, info.microseconds
    # Linux test hosts: boot-relative start ticks are exact; second field is zero.
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return int(fields[19]), 0


@contextmanager
def _exclusive(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(descriptor).st_nlink != 1:
            raise ValueError("worker lock cannot be linked")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("campaign is already owned by another worker") from error
        yield
    finally:
        os.close(descriptor)


class BackgroundLearningRunner:
    def __init__(self, registry: LearningRegistry, trainer: LearningTrainer):
        self.registry, self.trainer = registry, trainer

    def run(self, campaign_hash: str, *, control: ResearchControl, emit: Callable[[dict], None]) -> LearningStatus:
        with self.registry._locked():
            state, _, _ = self.registry._read()
            campaign = state.campaigns[campaign_hash][0]
        if campaign.code_hash != runtime_code_identity():
            raise ValueError("registered runtime code/environment identity mismatch")
        # The command boundary rejects pre-existing terminal executions. A stop
        # arriving after its ownership handshake must still exit successfully.
        control.read()
        with _exclusive(self.registry.root / f".campaign-{campaign_hash}.worker.lock"):
            try:
                return self._loop(campaign, control, emit)
            except Exception as error:
                status = self.registry.read_status(campaign_hash)
                try:
                    if status.batch_id:
                        with self.registry._locked():
                            state, _, _ = self.registry._read()
                        if not state.batch_finished(status.batch_id):
                            self.registry.append_event(
                                status.batch_id,
                                {
                                    "kind": "state",
                                    "state": "blocked",
                                    "reason": "Worker checkpoint or persistence failed",
                                },
                            )
                finally:
                    self._emit_status(
                        status.model_copy(
                            update={
                                "state": "blocked",
                                "reason": f"Worker stopped: {type(error).__name__}",
                            }
                        ),
                        emit,
                        event="error",
                    )
                raise

    def _loop(self, campaign, control, emit):
        from src.background_research.data import load_learning_data, read_learning_source
        from src.background_research.scheduler import LearningScheduler
        from src.background_research.training import TerminalControlError

        scheduler = LearningScheduler(self.registry)
        next_check = 0.0
        last_status = None
        while True:
            command = control.read()
            if command is ControlState.STOPPED:
                status = self.registry.read_status(campaign.identity_hash)
                if status.batch_id:
                    with self.registry._locked():
                        state, _, _ = self.registry._read()
                    if not state.batch_finished(status.batch_id):
                        events = state.events[status.batch_id]
                        finished = {event.attempt_id for event in events if event.kind == "attempt_result"}
                        for event in events:
                            if event.kind == "attempt" and event.attempt_id not in finished:
                                self.registry.append_event(
                                    status.batch_id,
                                    {
                                        "kind": "attempt_result",
                                        "attempt_id": event.attempt_id,
                                        "candidate_hash": event.candidate_hash,
                                        "outcome": "interrupted",
                                        "payload": {
                                            **event.payload,
                                            "reason": "Execution stopped before retained result",
                                        },
                                    },
                                )
                        self.registry.append_event(
                            status.batch_id,
                            {
                                "kind": "state",
                                "state": "paused",
                                "reason": "Execution stopped; retained batch may resume",
                            },
                        )
                        status = self.registry.read_status(campaign.identity_hash)
                self._emit_status(status, emit, event="complete")
                return status
            if command is ControlState.RUNNING and time.monotonic() >= next_check:
                now = datetime.now(UTC)
                source = read_learning_source(campaign, now=now)
                if control.read() is not ControlState.RUNNING:
                    continue
                with self.registry._locked():
                    state, _, _ = self.registry._read()
                unfinished = [
                    batch
                    for batch in state.batches.values()
                    if batch.campaign_hash == campaign.identity_hash and not state.batch_finished(batch.batch_id)
                ]
                batch = unfinished[-1] if unfinished else None
                if batch is not None:
                    # Resume authority follows full source-prefix/campaign/runtime validation.
                    load_learning_data(campaign, batch, now=now)
                    if control.read() is not ControlState.RUNNING:
                        continue
                    if state.batch_state(batch.batch_id) in {"paused", "pausing", "blocked"}:
                        self.registry.append_event(
                            batch.batch_id,
                            {
                                "kind": "state",
                                "state": "training",
                                "reason": "Authenticated execution resumes retained batch",
                            },
                        )
                elif source.through is not None:
                    for symbol in campaign.symbols:
                        batch = scheduler.next_batch(
                            campaign,
                            symbol=symbol,
                            data_fingerprint=source.data_fingerprint,
                            through=source.through,
                            now=now,
                        )
                        if batch is not None:
                            break
                if batch is not None:
                    if control.read() is not ControlState.RUNNING:
                        continue

                    def progress(event):
                        emit(
                            {
                                **event,
                                "schema_version": 1,
                                "status": self.registry.read_status(campaign.identity_hash).model_dump(mode="json"),
                            }
                        )

                    try:
                        status = self.trainer.run_batch(campaign, batch, control=control, emit=progress)
                    except TerminalControlError:
                        # STOP can arrive during trainer preparation after our last
                        # read. Only that typed, authenticated cancellation is orderly;
                        # concurrent source/checkpoint failures still fail visibly.
                        if control.read() is not ControlState.STOPPED:
                            raise
                        continue
                    if status.state == "paused" and control.read() is ControlState.RUNNING:
                        control.request(ControlState.PAUSED)
                else:
                    status = self.registry.read_status(campaign.identity_hash)
                    if status.state == "idle":
                        status = status.model_copy(
                            update={"state": "waiting", "reason": "Waiting for eligible retained observations"}
                        )
                self._emit_status(status, emit)
                last_status = status
                # Even exhausted/insufficient batches never become a tight search loop.
                next_check = time.monotonic() + 30.0
            elif command is ControlState.PAUSED and (last_status is None or last_status.state != "paused"):
                last_status = self.registry.read_status(campaign.identity_hash).model_copy(
                    update={"state": "paused", "reason": "Research paused by control"}
                )
                self._emit_status(last_status, emit)
            time.sleep(0.25)

    @staticmethod
    def _emit_status(status, emit, *, event="progress"):
        emit(
            {
                "event": event,
                "stage": "background_research",
                "progress": 1 if event == "complete" else 0,
                "message": status.reason,
                "schema_version": 1,
                "status": status.model_dump(mode="json"),
            }
        )


def register_background_research(registry_directory: Path, manifest: Path) -> LearningCampaign:
    from src.background_research.contracts import LearningCampaign
    from src.background_research.registry import LearningRegistry

    payload = json.loads(manifest.read_text())
    identity = runtime_code_identity()
    if payload.get("code_hash", identity) != identity:
        raise ValueError("registration runtime code/environment identity mismatch")
    payload["code_hash"] = identity
    campaign = LearningCampaign.model_validate(payload)
    LearningRegistry(registry_directory).register(campaign)
    return campaign


def run_background_research(
    *, registry_directory, campaign_hash, run_id, control_directory, control_nonce, emit, workers=None
):
    from src.background_research.registry import LearningRegistry, _safe_directory

    registry = LearningRegistry(registry_directory)
    with registry._locked():
        state, _, _ = registry._read()
        campaign = state.campaigns[campaign_hash][0]
    directory = _safe_directory(Path(control_directory))
    source = campaign.source_directory.resolve()
    if directory == source or source in directory.parents or directory in source.parents:
        raise ValueError("control directory must be separate from selected source")
    control = ResearchControl(directory, run_id=run_id, nonce=control_nonce)
    if control.path.is_symlink() or (control.path.exists() and control.path.stat().st_nlink > 1):
        raise ValueError("control file cannot be linked")
    # A per-execution lock prevents races while first creating the private channel.
    with _exclusive(directory / f".{run_id}.execution.lock"):
        if control.path.exists():
            if control.read() is ControlState.STOPPED:
                raise ValueError("terminal control requires a fresh execution identity")
        else:
            control.initialize()
        if os.getpgrp() != os.getpid():
            os.setsid()
        pid = os.getpid()
        seconds, microseconds = process_identity(pid)
        ownership = {
            "event": "ownership",
            "stage": "background_research",
            "schema_version": 1,
            "run_id": run_id,
            "nonce": control_nonce,
            "pid": pid,
            "parent_pid": os.getppid(),
            "process_group_id": os.getpgrp(),
            "process_start_seconds": seconds,
            "process_start_microseconds": microseconds,
            "campaign_hash": campaign_hash,
        }
        owner_path = directory / f"{run_id}.ownership.json"
        if owner_path.exists() or owner_path.is_symlink():
            raise ValueError("execution identity already has an ownership receipt; use a fresh run ID")
        with owner_path.open("x") as stream:
            os.chmod(owner_path, 0o600)
            stream.write(json.dumps(ownership) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        previous = signal.signal(signal.SIGTERM, lambda *_: control.request(ControlState.STOPPED))
        try:
            emit(ownership)
            if control.read() is ControlState.STOPPED:
                status = registry.read_status(campaign_hash)
                BackgroundLearningRunner._emit_status(status, emit, event="complete")
                return status
            from src.background_research.training import LearningTrainer

            return BackgroundLearningRunner(registry, LearningTrainer(registry, workers=workers)).run(
                campaign_hash, control=control, emit=emit
            )
        finally:
            signal.signal(signal.SIGTERM, previous)


def background_main(argv=None) -> int:
    restrict_background_environment()
    parser = argparse.ArgumentParser(description="Local paper-only background research")
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register-background-research")
    register.add_argument("--registry-directory", type=Path, required=True)
    register.add_argument("--manifest", type=Path, required=True)
    run = commands.add_parser("background-research")
    run.add_argument("--registry-directory", type=Path, required=True)
    run.add_argument("--campaign-hash", required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--control-directory", type=Path, required=True)
    run.add_argument("--control-nonce", required=True)
    run.add_argument("--workers", type=int)
    args = parser.parse_args(argv)
    pipe_open = True

    def emit(event):
        nonlocal pipe_open
        if pipe_open:
            try:
                print(json.dumps(event), flush=True)
            except BrokenPipeError:
                pipe_open = False
                if args.command == "background-research":
                    control = ResearchControl(
                        args.control_directory.expanduser().resolve(), run_id=args.run_id, nonce=args.control_nonce
                    )
                    control.request(ControlState.STOPPED)
                # Prevent interpreter shutdown from failing after durable work succeeds.
                with open(os.devnull, "w") as sink:
                    os.dup2(sink.fileno(), sys.stdout.fileno())

    try:
        if args.command == "register-background-research":
            from src.background_research.registry import LearningRegistry

            campaign = register_background_research(args.registry_directory, args.manifest)
            emit(
                {
                    "event": "registered",
                    "stage": "background_research",
                    "schema_version": 1,
                    "campaign_hash": campaign.identity_hash,
                    "runtime_code_identity": campaign.code_hash,
                    "status": LearningRegistry(args.registry_directory)
                    .read_status(campaign.identity_hash)
                    .model_dump(mode="json"),
                }
            )
        else:
            values = vars(args).copy()
            values.pop("command")
            run_background_research(**values, emit=emit)
        return 0
    except Exception as error:
        # Paths/account payloads are never included; these errors describe local contracts only.
        print(
            json.dumps(
                {
                    "event": "error",
                    "stage": "background_research",
                    "schema_version": 1,
                    "message": str(error)
                    if type(error) is ValueError
                    else f"Background worker failed: {type(error).__name__}",
                }
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2
