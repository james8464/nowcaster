#!/usr/bin/env python3
"""Merge deterministic intraday research into the native demo snapshot."""

from __future__ import annotations

import argparse
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


def merge_research_sections(base: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
    if base.get("schema_version") != 2 or research.get("schema_version") != 2:
        raise ValueError("snapshot synchronization requires schema v2 inputs")
    if missing := [section for section in RESEARCH_SECTIONS if section not in research]:
        raise ValueError(f"research snapshot is missing sections: {missing}")
    merged = deepcopy(base)
    for section in RESEARCH_SECTIONS:
        merged[section] = deepcopy(research[section])
    merged["ensemble_components"] = merged["ensemble_components"][:MAX_NATIVE_ENSEMBLE_COMPONENTS]
    return merged


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--research", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = json.loads(args.base.read_text(encoding="utf-8"))
    research = json.loads(args.research.read_text(encoding="utf-8"))
    merged = merge_research_sections(base, research)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Synchronized Swift snapshot fixture: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
