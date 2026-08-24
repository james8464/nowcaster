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
    "deep_research_runs",
    "causal_audits",
)
MAX_NATIVE_ENSEMBLE_COMPONENTS = 10


def merge_research_sections(base: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
    if base.get("schema_version") not in {2, 3, 5} or research.get("schema_version") not in {2, 3, 5}:
        raise ValueError("snapshot synchronization requires schema v2, v3, or v5 inputs")
    if missing := [section for section in RESEARCH_SECTIONS if section not in research]:
        raise ValueError(f"research snapshot is missing sections: {missing}")
    merged = deepcopy(base)
    merged["schema_version"] = 5
    for section in RESEARCH_SECTIONS:
        merged[section] = deepcopy(research[section])
    merged["ensemble_components"] = merged["ensemble_components"][:MAX_NATIVE_ENSEMBLE_COMPONENTS]
    merged.setdefault(
        "broker_status",
        {
            "environment": "research",
            "state": "live_locked",
            "account_suffix": None,
            "session_status": "not_started",
            "reconciled_at": None,
            "unresolved_mismatches": 0,
        },
    )
    merged.setdefault("broker_positions", [])
    merged.setdefault("broker_orders", [])
    merged.setdefault("broker_events", [])
    merged.setdefault(
        "risk_status",
        {"state": "not_evaluated", "allowed": False, "reasons": ["live_locked"], "utilization": {}, "decided_at": None},
    )
    merged.setdefault(
        "forward_readiness",
        {
            "state": "live_locked",
            "cohort_hash": None,
            "observed_periods": 0,
            "closed_trades": 0,
            "receipt_expires_at": None,
            "gates": [
                {
                    "name": "external_forward_evidence",
                    "passed": False,
                    "detail": "Paper evidence and external release conditions are not yet complete.",
                }
            ],
        },
    )
    merged.setdefault(
        "emergency_status",
        {"frozen": False, "flatten_state": "not_requested", "reason": None, "observed_at": None},
    )
    return merged


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--research", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    base = json.loads(args.base.read_text(encoding="utf-8"))
    research = json.loads(args.research.read_text(encoding="utf-8"))
    merged = merge_research_sections(base, research)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = (
        json.dumps(merged, separators=(",", ":"), ensure_ascii=False)
        if args.compact
        else json.dumps(merged, indent=2, ensure_ascii=False)
    )
    args.output.write_text(rendered + "\n", encoding="utf-8")
    print(f"Synchronized Swift snapshot fixture: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
