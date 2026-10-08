"""Hash-chained, sanitized account-quote and indicator decision capture."""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from src.strategies.types import canonical_hash

ZERO_HASH = "0" * 64


class SessionJournal:
    def __init__(self, directory: Path, identity: dict):
        self.directory = Path(directory).expanduser().resolve()
        if {"ProspectiveStudies", "live-paper-study"} & set(self.directory.parts):
            raise ValueError("protected study path")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.manifest = self.directory / "live_round.json"
        self.path = self.directory / "quotes.jsonl"
        self.lock = self.directory / ".quote_writer.lock"
        if not self.manifest.exists():
            if self.path.exists():
                raise ValueError("quote journal has no identity")
            with self.manifest.open("x", encoding="utf-8") as stream:
                json.dump(identity, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
        if json.loads(self.manifest.read_text(encoding="utf-8")) != identity:
            raise ValueError("live round identity changed")
        retained = self.events()
        self._last_hash = retained[-1]["record_hash"] if retained else ZERO_HASH
        self._last_size = self.path.stat().st_size if self.path.exists() else 0

    def events(self) -> tuple[dict, ...]:
        if not self.path.exists():
            return ()
        data = self.path.read_bytes()
        if data and not data.endswith(b"\n"):
            raise ValueError("unterminated quote journal")
        previous = ZERO_HASH
        events = []
        for line in data.splitlines():
            if len(line) > 65536:
                raise ValueError("oversized quote event")
            event = json.loads(line)
            digest = event.pop("record_hash")
            if event.get("previous_hash") != previous or canonical_hash(event) != digest:
                raise ValueError("quote journal chain mismatch")
            event["record_hash"] = digest
            events.append(event)
            previous = digest
        return tuple(events)

    def append(self, kind: str, at: datetime, payload: dict[str, str]) -> dict:
        if at.tzinfo is None or at.utcoffset() != timedelta(0):
            raise ValueError("event time must be UTC")
        if kind not in {"quote", "gap", "decision"}:
            raise ValueError("unknown quote event kind")
        if any(word in key.lower() for key in payload for word in ("token", "password", "secret", "api_key")):
            raise ValueError("credential field prohibited")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in payload.items()):
            raise ValueError("quote journal payload must be strings")
        with self.lock.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            size = self.path.stat().st_size if self.path.exists() else 0
            if size != self._last_size:
                raise ValueError("another quote writer changed the journal")
            event = {"kind": kind, "at": at.isoformat(), "payload": payload,
                     "previous_hash": self._last_hash}
            event["record_hash"] = canonical_hash(event)
            encoded = (json.dumps(event, sort_keys=True) + "\n").encode()
            with self.path.open("ab") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            self._last_hash = event["record_hash"]
            self._last_size += len(encoded)
            fcntl.flock(lock, fcntl.LOCK_UN)
        return event
