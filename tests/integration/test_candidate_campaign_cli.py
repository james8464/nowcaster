from __future__ import annotations

import json

from typer.testing import CliRunner

from src.cli import app


def test_register_campaign_emits_research_only_unavailable_receipt(tmp_path):
    definition = tmp_path / "wti-campaign.json"
    definition.write_text(
        json.dumps(
            {
                "campaign_id": "wti-intraday-2026-09-17",
                "asset": {
                    "symbol": "CL",
                    "asset_class": "commodity_future",
                    "contract_identity": "NYMEX CLZ26",
                    "session_calendar": "CME Globex Energy",
                    "roll_policy": "specific_contract_only",
                },
                "strategy_ids": ["volatility_scaled_trend"],
                "interval": "5m",
                "source": {"provider": "licensed_csv", "feed": "cme", "csv_path": None},
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["strategy", "register-campaign", "--definition", str(definition), "--output-directory", str(tmp_path / "out")],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "unavailable"
    assert "verified intraday contract data" in payload["reason"]
