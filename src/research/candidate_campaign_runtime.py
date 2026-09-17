"""Append-only source receipts for separate candidate-market research."""

from __future__ import annotations

import csv
import fcntl
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from src.ingestion.csv_bars import REQUIRED_COLUMNS
from src.research.candidate_campaign import CampaignReceipt, CandidateCampaignDefinition
from src.strategies.types import canonical_hash


def _receipt(
    definition: CandidateCampaignDefinition, *, status: str, reason: str
) -> CampaignReceipt:
    payload = {
        "campaign_id": definition.campaign_id,
        "campaign_hash": definition.identity_hash,
        "status": status,
        "reason": reason,
    }
    return CampaignReceipt.model_validate({**payload, "receipt_hash": canonical_hash(payload)})


def _utc_timestamp(value: str, *, field: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not UTC:
        raise ValueError(f"{field} must be explicit UTC")
    return parsed


def inspect_campaign_source(definition: CandidateCampaignDefinition) -> CampaignReceipt:
    """Return a strict source assessment without importing, filling, or replaying bars."""

    source_path = definition.source.csv_path
    if source_path is None:
        return _receipt(
            definition,
            status="unavailable",
            reason="WTI needs verified intraday contract data before research can begin",
        )
    path = Path(source_path)
    if not path.is_file():
        return _receipt(definition, status="rejected", reason="declared campaign CSV is unavailable")
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or ())
            if missing:
                return _receipt(definition, status="rejected", reason=f"CSV is missing columns: {sorted(missing)}")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as error:
        return _receipt(definition, status="rejected", reason=f"CSV cannot be read: {type(error).__name__}")
    if not rows:
        return _receipt(definition, status="rejected", reason="CSV contains no intraday bars")
    try:
        for row in rows:
            if str(row["finalized"]).strip().lower() not in {"1", "true", "yes"}:
                return _receipt(definition, status="rejected", reason="all campaign CSV rows must be finalized")
            timestamp = _utc_timestamp(row["timestamp"], field="timestamp")
            available_at = _utc_timestamp(row["available_at"], field="available_at")
            if available_at < timestamp:
                return _receipt(definition, status="rejected", reason="available_at precedes timestamp")
            if int(row["revision"]) < 0:
                return _receipt(definition, status="rejected", reason="revision must be non-negative")
            for field in ("open", "high", "low", "close", "volume"):
                if float(row[field]) < 0:
                    return _receipt(definition, status="rejected", reason=f"{field} must be non-negative")
    except (KeyError, TypeError, ValueError):
        return _receipt(definition, status="rejected", reason="CSV contains malformed intraday bars")
    return _receipt(definition, status="available", reason="verified finalized intraday CSV is available for research")


def register_campaign(definition: CandidateCampaignDefinition, directory: Path) -> CampaignReceipt:
    """Persist exactly one source receipt per immutable campaign identity."""

    directory.mkdir(parents=True, exist_ok=True)
    registry_path = directory / "campaigns.jsonl"
    lock_path = directory / "campaigns.lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous: list[dict[str, object]] = []
        if registry_path.exists():
            previous = [json.loads(line) for line in registry_path.read_text(encoding="utf-8").splitlines() if line]
        if any(row.get("campaign_hash") == definition.identity_hash for row in previous):
            raise FileExistsError("campaign receipt is already retained")
        receipt = inspect_campaign_source(definition)
        with registry_path.open("ab") as output:
            output.write((json.dumps(receipt.model_dump(mode="json"), sort_keys=True) + "\n").encode())
            output.flush()
            os.fsync(output.fileno())
        return receipt


__all__ = ["inspect_campaign_source", "register_campaign"]
