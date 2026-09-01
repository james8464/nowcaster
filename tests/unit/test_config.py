from __future__ import annotations

from pathlib import Path

import pytest

from src.config.settings import DeepResearchConfig, InstrumentConfig, Settings


def test_settings_loads_yaml_and_environment_override(project_root, monkeypatch):
    monkeypatch.setenv("NOWCASTER_MODE", "demo")

    settings = Settings.load(project_root)

    assert settings.mode == "demo"
    assert settings.model.forecast_horizons == (30, 14, 7, 1)
    assert settings.universe.companies[0].ticker == "SBUX"


def test_settings_can_refuse_environment_file_loading(project_root, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    (project_root / ".env").write_text("SEC_USER_AGENT=must-not-load\n", encoding="utf-8")

    settings = Settings.load(project_root, mode="test", load_environment_file=False)

    assert settings.sec_user_agent is None


def test_settings_rejects_duplicate_tickers(project_root):
    universe = project_root / "config" / "universe.yaml"
    universe.write_text(
        "companies:\n"
        "  - {ticker: SBUX, cik: '829224', name: Starbucks, enabled: true}\n"
        "  - {ticker: sbux, cik: '829224', name: Duplicate, enabled: true}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate ticker"):
        Settings.load(project_root)


def test_settings_rejects_unsupported_horizon(project_root):
    (project_root / "config" / "model.yaml").write_text(
        "forecast_horizons: [0]\nminimum_training_quarters: 8\nrandom_seed: 42\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="positive"):
        Settings.load(project_root)


def test_checked_in_configuration_is_valid():
    root = Path(__file__).resolve().parents[2]

    settings = Settings.load(root, mode="test")

    assert len(settings.universe.companies) == 14
    assert settings.universe.companies[1].wikipedia_article == "Nike,_Inc."


def test_research_config_identity_ignores_storage_but_preserves_cost_and_strategy_policy(project_root):
    settings = Settings.load(project_root, mode="test")
    relocated = settings.model_copy(update={"database_url": "duckdb:///another/research.duckdb"})
    changed_cost = settings.model_copy(
        update={"deep_research": settings.deep_research.model_copy(update={"crypto_fee_bps": 12.0})}
    )
    changed_strategy = settings.model_copy(
        update={"model": settings.model.model_copy(update={"random_seed": settings.model.random_seed + 1})}
    )

    assert settings.config_hash_payload() != relocated.config_hash_payload()
    assert settings.research_config_hash_payload() == relocated.research_config_hash_payload()
    assert settings.research_config_hash_payload() != changed_cost.research_config_hash_payload()
    assert settings.research_config_hash_payload() != changed_strategy.research_config_hash_payload()


def test_bundled_crypto_instruments_are_exact_non_shortable_binance_usdt_spot_products() -> None:
    root = Path(__file__).resolve().parents[2]
    settings = Settings.load(root, mode="test")
    instruments = settings.instruments.instruments

    assert {item.symbol for item in instruments} == {"BTCUSDT", "ETHUSDT"}
    for item in instruments:
        assert item.provider == "binance"
        assert item.feed == "spot"
        assert item.venue == "Binance"
        assert item.currency == "USDT"
        assert item.product == "spot"
        assert item.shortable is False
        assert item.short_mechanism == "none"
        assert item.funding_applicable is False
        assert item.borrow_applicable is False
        assert item.trading_calendar == "24x7"


def test_instrument_and_research_safety_settings_cannot_be_weakened() -> None:
    with pytest.raises(ValueError, match="short mechanism"):
        InstrumentConfig(
            symbol="BTCUSDT",
            name="Bitcoin / Tether",
            asset_class="crypto",
            provider="binance",
            feed="spot",
            venue="Binance",
            currency="USDT",
            product="spot",
            shortable=True,
            short_mechanism="none",
        )
    with pytest.raises(ValueError):
        DeepResearchConfig(reserved_processors=0)
    with pytest.raises(ValueError):
        DeepResearchConfig(minimum_effective_calibration_observations=99)
    with pytest.raises(ValueError):
        DeepResearchConfig(maximum_calibration_error=0.11)


def test_checked_in_research_thresholds_reserve_compute_and_bind_promotion_evidence() -> None:
    root = Path(__file__).resolve().parents[2]
    policy = Settings.load(root, mode="test").deep_research

    assert policy.reserved_processors == 2
    assert policy.minimum_effective_calibration_observations >= 100
    assert policy.minimum_isotonic_calibration_observations >= 1_000
    assert policy.minimum_promotion_observations >= 1_000
    assert policy.minimum_effective_promotion_observations >= 300
    assert policy.minimum_promotion_trades >= 300
    assert policy.minimum_rolling_holdouts >= 3
    assert policy.minimum_promotion_bootstrap_probability >= 0.99
    assert policy.maximum_promotion_pbo_probability <= 0.10
