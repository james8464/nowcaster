"""Append-only persistence for immutable Research Round 2 protocol manifests."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from src.research.round_two_contracts import ResearchRoundProtocol
from src.strategies.types import canonical_json


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_first_manifest(manifest: Path, payload: bytes) -> bool:
    """Atomically create ``manifest`` once, returning false when it already exists."""
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{manifest.name}.", dir=manifest.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, manifest)
        except FileExistsError:
            return False
        _fsync_directory(manifest.parent)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def register_round(protocol: ResearchRoundProtocol, directory: Path) -> Path:
    """Register exactly one protocol identity at ``directory`` without overwriting it."""
    protocol = protocol.validated()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "protocol.json"
    payload = (canonical_json(protocol.model_dump(mode="json")) + "\n").encode("utf-8")
    if _write_first_manifest(manifest, payload):
        return directory
    retained = load_round_protocol(directory)
    if retained.identity_hash != protocol.identity_hash:
        raise ValueError("protocol identity does not match retained round; register a new round")
    return directory


def load_round_protocol(directory: Path) -> ResearchRoundProtocol:
    manifest = Path(directory) / "protocol.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"round protocol manifest does not exist: {manifest}")
    return ResearchRoundProtocol.model_validate_json(manifest.read_text(encoding="utf-8"))
