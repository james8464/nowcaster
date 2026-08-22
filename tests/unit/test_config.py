from __future__ import annotations

from pathlib import Path

import pytest

from src.config.settings import Settings


def test_settings_loads_yaml_and_environment_override(project_root, monkeypatch):
    monkeypatch.setenv("NOWCASTER_MODE", "demo")

    settings = Settings.load(project_root)

    assert settings.mode == "demo"
    assert settings.model.forecast_horizons == (30, 14, 7, 1)
    assert settings.universe.companies[0].ticker == "SBUX"


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
