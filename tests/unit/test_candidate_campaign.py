from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.strategies.types import BarInterval


def _wti_definition(**overrides: object):
    from src.research.candidate_campaign import CandidateCampaignDefinition

    payload: dict[str, object] = {
        "campaign_id": "wti-intraday-2026-09-17",
        "asset": {
            "symbol": "CL",
            "asset_class": "commodity_future",
            "contract_identity": "NYMEX CL front contract",
            "session_calendar": "CME Globex Energy",
            "roll_policy": "specific_contract_only",
        },
        "strategy_ids": ("volatility_scaled_trend", "donchian_breakout"),
        "interval": BarInterval.FIVE_MINUTES,
        "source": {"provider": "licensed_csv", "feed": "cme_settlement", "csv_path": None},
    }
    payload.update(overrides)
    return CandidateCampaignDefinition.model_validate(payload)


def test_wti_requires_futures_identity_and_intraday_interval():
    with pytest.raises(ValidationError, match="WTI requires a futures contract identity"):
        _wti_definition(asset={"symbol": "WTI", "asset_class": "commodity_future"})

    with pytest.raises(ValidationError, match="intraday"):
        _wti_definition(interval=BarInterval.ONE_DAY)


def test_campaign_identity_changes_when_roll_policy_changes():
    specific = _wti_definition()
    continuous = _wti_definition(
        asset={
            "symbol": "CL",
            "asset_class": "commodity_future",
            "contract_identity": "NYMEX CL continuous front contract",
            "session_calendar": "CME Globex Energy",
            "roll_policy": "back_adjusted_5_day_volume_roll",
        }
    )

    assert specific.identity_hash != continuous.identity_hash
    assert specific.state_label == "Research only"
