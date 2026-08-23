from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from src.app_snapshot.models import AppSnapshot


def write_snapshot_atomic(snapshot: AppSnapshot, path: Path) -> Path:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        snapshot.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.stem}-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return path
