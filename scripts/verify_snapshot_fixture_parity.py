#!/usr/bin/env python3
"""Verify generated Swift snapshot semantics against the staged fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any


def semantic_snapshot_hash(payload: dict[str, Any]) -> str:
    """Hash user-visible snapshot semantics, excluding only export receipt time."""
    normalized = deepcopy(payload)
    metadata = normalized.get("metadata")
    if isinstance(metadata, dict):
        for receipt_field in ("generated_at", "last_refresh", "git_commit"):
            metadata.pop(receipt_field, None)
    pipeline_runs = normalized.get("pipeline_runs")
    if isinstance(pipeline_runs, list):
        normalized_runs = []
        for run in pipeline_runs:
            if not isinstance(run, dict) or run.get("command") == "export_native_snapshot":
                continue
            for receipt_field in ("pipeline_run_id", "started_at", "ended_at"):
                run.pop(receipt_field, None)
            normalized_runs.append(run)
        normalized["pipeline_runs"] = sorted(
            normalized_runs,
            key=lambda run: json.dumps(run, sort_keys=True, separators=(",", ":")),
        )
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _staged_payload(path: Path) -> dict[str, Any]:
    root = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path.parent,
            text=True,
        ).strip()
    )
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    encoded = subprocess.check_output(["git", "show", f":{relative}"], cwd=root)
    return json.loads(encoded)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    generated = json.loads(args.fixture.read_text(encoding="utf-8"))
    staged = _staged_payload(args.fixture)
    generated_hash = semantic_snapshot_hash(generated)
    staged_hash = semantic_snapshot_hash(staged)
    if generated_hash != staged_hash:
        print(f"Swift snapshot semantic drift: staged={staged_hash} generated={generated_hash}")
        return 1
    print(f"Swift snapshot semantic parity: {generated_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
