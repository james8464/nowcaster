from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.config.settings import AssetSelectionConfig, Settings
from src.contextual.types import (
    AssetProfileName,
    MarketRegime,
    StrategyContextKey,
    StrategyDirection,
)
from src.strategies.types import BarInterval, StrategyMode

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_checked_in_assets_have_explicit_contextual_profiles() -> None:
    settings = Settings.load(PROJECT_ROOT, mode="test")

    assert hasattr(settings, "asset_selection")
    assert {item.symbol: getattr(item, "profile", None) for item in settings.instruments.instruments} == {
        "BTCUSDT": "crypto_major_spot",
        "ETHUSDT": "crypto_major_spot",
    }
    profiles = settings.asset_selection.model_dump(mode="json")["profiles"]
    assert profiles["crypto_major_spot"]["allowed_directions"] == ["long"]
    assert profiles["crypto_major_spot"]["allowed_families"] == [
        "trend",
        "mean_reversion",
        "volatility_volume",
        "session",
        "relative_value",
    ]


def _context_key(**changes: object) -> StrategyContextKey:
    values = {
        "dataset_hash": "dataset-v1",
        "protocol_hash": "protocol-v1",
        "provider": "binance",
        "feed": "spot",
        "venue": "Binance",
        "product": "spot",
        "asset_class": "crypto",
        "profile": AssetProfileName.CRYPTO_MAJOR_SPOT,
        "symbol": "BTCUSDT",
        "interval": BarInterval.FIVE_MINUTES,
        "direction": StrategyDirection.LONG,
        "regime": MarketRegime.TREND_NORMAL,
        "mode": StrategyMode.PAPER,
    }
    values.update(changes)
    return StrategyContextKey(**values)  # type: ignore[arg-type]


def test_context_hash_changes_when_direction_or_product_changes() -> None:
    base = _context_key()

    assert base.context_hash != replace(base, direction=StrategyDirection.SHORT).context_hash
    assert base.context_hash != replace(base, product="perpetual").context_hash


def test_settings_rejects_instrument_profile_product_mismatch() -> None:
    settings = Settings.load(PROJECT_ROOT, mode="test")
    payload = settings.model_dump(mode="python")
    payload["instruments"]["instruments"][0]["profile"] = AssetProfileName.CRYPTO_LIQUID_DERIVATIVE

    with pytest.raises(ValueError, match="profile does not support product"):
        Settings.model_validate(payload)


def test_asset_policy_requires_all_profiles_and_safe_effective_breadth() -> None:
    settings = Settings.load(PROJECT_ROOT, mode="test")
    assert settings.asset_selection is not None
    payload = settings.asset_selection.model_dump(mode="python")
    del payload["profiles"][AssetProfileName.US_BROAD_ETF]

    with pytest.raises(ValueError, match="missing asset profile"):
        AssetSelectionConfig.model_validate(payload)

    payload = settings.asset_selection.model_dump(mode="python")
    payload["allocation"]["maximum_strategy_weight"] = 0.26
    with pytest.raises(ValueError, match="reciprocal effective breadth"):
        AssetSelectionConfig.model_validate(payload)
