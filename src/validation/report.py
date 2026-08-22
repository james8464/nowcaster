from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.utils.provenance import canonical_hash
from src.validation.fundamentals import QualityIssue


def issue_to_row(issue: QualityIssue, *, stage: str, source: str) -> dict[str, Any]:
    detected_at = datetime.now(UTC)
    return {
        "issue_id": canonical_hash([stage, issue.entity_key, issue.rule, str(issue.observed_value)])[:24],
        "stage": stage,
        "entity_key": issue.entity_key,
        "severity": issue.severity,
        "rule": issue.rule,
        "observed_value": str(issue.observed_value) if issue.observed_value is not None else None,
        "message": issue.message,
        "detected_at": detected_at,
        "source": source,
        "source_version": "quality-rules-v1",
        "created_at": detected_at,
    }
