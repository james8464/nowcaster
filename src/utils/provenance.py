from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def git_commit(root: Path | None = None) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@dataclass(frozen=True)
class RunContext:
    command: str
    config_hash: str
    git_commit: str
    started_at: datetime


def capture_run_context(command: str, config_hash: str, root: Path | None = None) -> RunContext:
    return RunContext(command, config_hash, git_commit(root), datetime.now(UTC))
