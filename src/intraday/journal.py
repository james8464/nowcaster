"""Append-only journal for experimental account-specific paper decisions."""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.strategies.types import canonical_hash

ZERO_HASH = "0" * 64


class PaperEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: Literal["session_window", "no_trade", "opened", "managed", "closed", "feed_gap", "provider_error"]
    occurred_at: datetime
    payload: dict[str, str]
    previous_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify(self):
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() != timedelta(0):
            raise ValueError("paper event must have UTC time")
        if any(
            any(secret in key.lower() for secret in ("token", "password", "secret", "api_key")) for key in self.payload
        ):
            raise ValueError("credentials cannot enter paper journal")
        expected = canonical_hash(self.model_dump(mode="json", exclude={"record_hash"}))
        if self.record_hash != expected:
            raise ValueError("paper event hash mismatch")
        return self


class PaperJournal:
    def __init__(self, directory: Path, protocol_hash: str):
        self.directory = Path(directory).expanduser().resolve()
        if {"ProspectiveStudies", "live-paper-study"} & set(self.directory.parts):
            raise ValueError("protected prospective study path")
        if len(protocol_hash) != 64 or any(c not in "0123456789abcdef" for c in protocol_hash):
            raise ValueError("protocol hash invalid")
        self.protocol_hash = protocol_hash
        self.directory.mkdir(parents=True, exist_ok=True)
        self.manifest = self.directory / "manifest.json"
        self.event_file = self.directory / "events.jsonl"
        self.lock_file = self.directory / ".writer.lock"
        identity = {"schema_version": 1, "paper_only": True, "protocol_hash": protocol_hash}
        if not self.manifest.exists():
            if self.event_file.exists():
                raise ValueError("journal events have no protocol manifest")
            with self.manifest.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(identity, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        if json.loads(self.manifest.read_text(encoding="utf-8")) != identity:
            raise ValueError("journal protocol mismatch")
        self._lock = None
        retained = self.events()
        self._last_hash = retained[-1].record_hash if retained else ZERO_HASH
        self._last_size = self.event_file.stat().st_size if self.event_file.exists() else 0
        self._semantic_ids = {
            canonical_hash({"kind": event.kind, "occurred_at": event.occurred_at.isoformat(), "payload": event.payload})
            for event in retained
        }

    def __enter__(self):
        self._lock = self.lock_file.open("a+b")
        fcntl.flock(self._lock, fcntl.LOCK_EX)
        size = self.event_file.stat().st_size if self.event_file.exists() else 0
        if size != self._last_size:
            fcntl.flock(self._lock, fcntl.LOCK_UN)
            self._lock.close()
            self._lock = None
            raise ValueError("another paper writer changed the journal")
        return self

    def __exit__(self, *_):
        if self._lock is not None:
            fcntl.flock(self._lock, fcntl.LOCK_UN)
            self._lock.close()
            self._lock = None

    def events(self) -> tuple[PaperEvent, ...]:
        if not self.event_file.exists():
            return ()
        data = self.event_file.read_bytes()
        if data and not data.endswith(b"\n"):
            raise ValueError("journal has unterminated record")
        result = []
        previous = ZERO_HASH
        semantic_ids = set()
        for line in data.splitlines():
            if len(line) > 1024 * 1024:
                raise ValueError("oversized journal record")
            event = PaperEvent.model_validate_json(line)
            if event.protocol_hash != self.protocol_hash or event.previous_hash != previous:
                raise ValueError("journal hash chain mismatch")
            semantic = canonical_hash(
                {"kind": event.kind, "occurred_at": event.occurred_at.isoformat(), "payload": event.payload}
            )
            if semantic in semantic_ids:
                raise ValueError("duplicate paper event")
            semantic_ids.add(semantic)
            result.append(event)
            previous = event.record_hash
        return tuple(result)

    def append(self, kind: str, occurred_at: datetime, payload: dict[str, str]) -> PaperEvent:
        if self._lock is None:
            raise ValueError("journal writes require writer lock")
        previous = self._last_hash
        data = {
            "schema_version": 1,
            "protocol_hash": self.protocol_hash,
            "kind": kind,
            "occurred_at": occurred_at,
            "payload": payload,
            "previous_hash": previous,
        }
        data["record_hash"] = canonical_hash({**data, "occurred_at": occurred_at.isoformat().replace("+00:00", "Z")})
        event = PaperEvent.model_validate(data)
        semantic = canonical_hash({"kind": event.kind, "occurred_at": event.occurred_at.isoformat(),
                                   "payload": event.payload})
        if semantic in self._semantic_ids:
            raise ValueError("duplicate paper event")
        encoded = (event.model_dump_json() + "\n").encode()
        with self.event_file.open("ab") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        self._last_hash = event.record_hash
        self._last_size += len(encoded)
        self._semantic_ids.add(semantic)
        return event
