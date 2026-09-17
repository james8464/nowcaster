from __future__ import annotations

import csv

import pytest

from src.research.candidate_campaign import CandidateCampaignDefinition
from src.strategies.types import BarInterval


def _definition(csv_path: str | None = None) -> CandidateCampaignDefinition:
    return CandidateCampaignDefinition.model_validate(
        {
            "campaign_id": "wti-intraday-2026-09-17",
            "asset": {
                "symbol": "CL",
                "asset_class": "commodity_future",
                "contract_identity": "NYMEX CLZ26",
                "session_calendar": "CME Globex Energy",
                "roll_policy": "specific_contract_only",
            },
            "strategy_ids": ("volatility_scaled_trend",),
            "interval": BarInterval.FIVE_MINUTES,
            "source": {"provider": "licensed_csv", "feed": "cme", "csv_path": csv_path},
        }
    )


def test_unavailable_source_is_retained_without_creating_bars(tmp_path):
    from src.research.candidate_campaign_runtime import register_campaign

    receipt = register_campaign(_definition(), tmp_path)

    assert receipt.status == "unavailable"
    assert "verified intraday contract data" in receipt.reason
    assert (tmp_path / "campaigns.jsonl").is_file()


def test_nonfinalized_csv_is_rejected_and_retained(tmp_path):
    from src.research.candidate_campaign_runtime import register_campaign

    source = tmp_path / "cl.csv"
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "timestamp", "open", "high", "low", "close", "volume", "finalized", "available_at", "revision"
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "timestamp": "2026-09-17T12:00:00Z",
                "open": "70", "high": "71", "low": "69", "close": "70.5", "volume": "100",
                "finalized": "false", "available_at": "2026-09-17T12:05:00Z", "revision": "0",
            }
        )

    receipt = register_campaign(_definition(str(source)), tmp_path)

    assert receipt.status == "rejected"
    assert "finalized" in receipt.reason
    assert len((tmp_path / "campaigns.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_repeated_campaign_identity_cannot_overwrite_receipt(tmp_path):
    from src.research.candidate_campaign_runtime import register_campaign

    register_campaign(_definition(), tmp_path)

    with pytest.raises(FileExistsError, match="already retained"):
        register_campaign(_definition(), tmp_path)
