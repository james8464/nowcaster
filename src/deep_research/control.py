from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


class ControlState(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


class ResearchControl:
    """Run-scoped, private and atomic local control channel."""

    def __init__(self, directory: Path, *, run_id: str, nonce: str):
        self.directory = Path(directory)
        self.run_id = run_id.strip()
        self.nonce = nonce.strip()
        if not self.run_id or len(self.nonce) < 32:
            raise ValueError("control identity requires a run ID and a nonce of at least 32 characters")
        self.path = self.directory / f"{self.run_id}.control.json"

    def _write(self, state: ControlState) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        payload = json.dumps(
            {
                "nonce": self.nonce,
                "run_id": self.run_id,
                "state": state.value,
                "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{self.run_id}.", dir=self.directory)
        try:
            os.fchmod(descriptor, 0o600)
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary_name, self.path)
        os.chmod(self.path, 0o600)

    def initialize(self) -> None:
        self._write(ControlState.RUNNING)

    def read(self) -> ControlState:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("control file is missing or malformed") from error
        if payload.get("run_id") != self.run_id or payload.get("nonce") != self.nonce:
            raise ValueError("control identity does not match this run")
        try:
            return ControlState(payload.get("state"))
        except ValueError as error:
            raise ValueError("control state is invalid") from error

    def request(self, state: ControlState) -> None:
        state = ControlState(state)
        current = self.read()
        if current is ControlState.STOPPED and state is not ControlState.STOPPED:
            raise ValueError("terminal control state cannot transition")
        self._write(state)

    def wait_until_runnable(self, *, poll_seconds: float = 0.05) -> ControlState:
        while True:
            state = self.read()
            if state is not ControlState.PAUSED:
                return state
            time.sleep(poll_seconds)


__all__ = ["ControlState", "ResearchControl"]
