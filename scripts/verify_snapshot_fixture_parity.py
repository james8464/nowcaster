#!/usr/bin/env python3
"""Verify generated Swift snapshot semantics against the staged fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

RESEARCH_SECTIONS = (
    "strategies",
    "ensemble_components",
    "dataset_coverage",
    "learning_runs",
    "causal_audits",
)
MAX_NATIVE_ENSEMBLE_COMPONENTS = 10


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


def research_semantic_hash(payload: dict[str, Any]) -> str:
    projection: dict[str, Any] = {"schema_version": payload.get("schema_version")}
    for section in RESEARCH_SECTIONS:
        if section not in payload:
            raise ValueError(f"snapshot is missing research section: {section}")
        projection[section] = deepcopy(payload[section])
    projection["ensemble_components"] = projection["ensemble_components"][:MAX_NATIVE_ENSEMBLE_COMPONENTS]
    encoded = json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-research", type=Path, required=True)
    parser.add_argument("--swift-fixture", type=Path, required=True)
    args = parser.parse_args()
    generated = json.loads(args.python_research.read_text(encoding="utf-8"))
    swift = json.loads(args.swift_fixture.read_text(encoding="utf-8"))
    generated_hash = research_semantic_hash(generated)
    swift_hash = research_semantic_hash(swift)
    if generated_hash != swift_hash:
        print(f"Swift snapshot research drift: python={generated_hash} swift={swift_hash}")
        return 1
    print(f"Python/Swift research semantic parity: {generated_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
