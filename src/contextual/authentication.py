"""Verify JSON evidence and its queryable SQL mirrors at consumer boundaries."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime

import pandas as pd

from src.strategies.types import canonical_hash


def evidence_mirrors_match(row: Mapping, *, weight_record: bool = False) -> bool:
    payload = row.get("evidence")
    if not isinstance(payload, dict):
        return False
    hashed = {"payload": payload, "strategy_id": str(row.get("strategy_id"))} if weight_record else payload
    if canonical_hash(hashed) != str(row.get("content_hash", "")):
        return False
    excluded = {"evidence", "content_hash", "source", "source_version", "created_at", "allocation_id"}
    aliases = {"as_of": "effective_at", "evidence_id": "eligibility_id"}
    for source in (payload, payload.get("context"), payload.get("covariance"), payload.get("allocation")):
        if not isinstance(source, dict):
            continue
        for name, expected in source.items():
            column = aliases.get(name, name)
            if column in excluded or column not in row:
                continue
            observed = row[column]
            try:
                if isinstance(expected, datetime) or name in {
                    "effective_at",
                    "as_of",
                    "decision_timestamp",
                    "feature_through",
                    "training_through",
                    "data_through",
                }:
                    if pd.Timestamp(expected) != pd.Timestamp(observed):
                        return False
                elif isinstance(expected, (float, int)) and not isinstance(expected, bool):
                    if not math.isfinite(float(observed)) or not math.isclose(
                        float(expected), float(observed), rel_tol=1e-6, abs_tol=1e-7
                    ):
                        return False
                elif expected != observed:
                    return False
            except (ValueError, TypeError, ArithmeticError):
                return False
    return True
