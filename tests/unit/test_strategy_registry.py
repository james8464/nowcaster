from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.strategies.registry import StrategyRegistry
from src.strategies.types import BarInterval, StrategyFamily, StrategyMode, StrategySpec, canonical_hash
from src.config.settings import Settings, StrategiesConfig


def _generator(*_args: object, **_kwargs: object) -> int:
    return 0


def _spec(**overrides: object) -> StrategySpec:
    values: dict[str, object] = {
        "strategy_id": "ema_adx_trend",
        "family": StrategyFamily.TREND,
        "version": "1.0.0",
        "intervals": (BarInterval.FIVE_MINUTES,),
        "warmup_bars": 50,
        "parameters": {"fast_period": 12, "slow_period": 26, "use_adx": True},
    }
    values.update(overrides)
    return StrategySpec.model_validate(values)


def test_registry_rejects_a_duplicate_strategy_id():
    registry = StrategyRegistry()
    registry.register(_spec(), _generator)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(_spec(version="2.0.0"), _generator)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("intervals", ("2m",)),
        ("parameters", {"threshold": [1, 2]}),
        ("warmup_bars", 0),
    ],
)
def test_strategy_spec_rejects_invalid_interval_or_parameter_contract(field: str, value: object):
    with pytest.raises(ValidationError):
        _spec(**{field: value})


def test_strategy_spec_hash_is_stable_for_equivalent_canonical_inputs():
    first = _spec(parameters={"slow_period": 26, "fast_period": 12, "use_adx": True})
    second = _spec(parameters={"use_adx": True, "fast_period": 12, "slow_period": 26})

    assert first.definition_hash == second.definition_hash
    assert first.deterministic_version == second.deterministic_version
    assert canonical_hash({"when": datetime(2026, 8, 22, 12, tzinfo=UTC)}) == canonical_hash(
        {"when": datetime(2026, 8, 22, 12, tzinfo=UTC)}
    )


def test_strategy_spec_parameters_are_immutable_after_validation():
    spec = _spec()

    with pytest.raises(TypeError):
        spec.parameters["fast_period"] = 20


def test_settings_loads_only_enabled_yaml_strategies(project_root):
    (project_root / "config" / "strategies.yaml").write_text(
        "strategy_weight_cap: 0.25\n"
        "family_weight_caps:\n"
        "  trend: 0.50\n"
        "strategies:\n"
        "  - strategy_id: ema_adx_trend\n"
        "    family: trend\n"
        "    version: 1.0.0\n"
        "    intervals: [5m]\n"
        "    warmup_bars: 50\n"
        "    parameters: {fast_period: 12, slow_period: 26}\n"
        "    enabled: true\n"
        "  - strategy_id: disabled_rsi\n"
        "    family: mean_reversion\n"
        "    version: 1.0.0\n"
        "    intervals: [5m]\n"
        "    warmup_bars: 14\n"
        "    parameters: {period: 14}\n"
        "    enabled: false\n",
        encoding="utf-8",
    )

    settings = Settings.load(project_root, mode="test")

    assert tuple(spec.strategy_id for spec in settings.strategies.enabled) == ("ema_adx_trend",)


@pytest.mark.parametrize(
    "payload",
    [
        {"strategy_weight_cap": 0, "family_weight_caps": {"trend": 0.5}, "strategies": []},
        {"strategy_weight_cap": 0.6, "family_weight_caps": {"trend": 0.5}, "strategies": []},
        {"strategy_weight_cap": 0.25, "family_weight_caps": {"trend": 1.1}, "strategies": []},
    ],
)
def test_strategy_and_family_weight_caps_validate(payload: dict[str, object]):
    with pytest.raises(ValidationError):
        StrategiesConfig.model_validate(payload)


def test_registry_resolves_enabled_registered_strategies_without_yaml_import_paths():
    registry = StrategyRegistry()
    registry.register(_spec(), _generator)
    registry.register(_spec(strategy_id="disabled_rsi", enabled=False), _generator)

    resolved = registry.resolve("ema_adx_trend")

    assert resolved.spec.strategy_id == "ema_adx_trend"
    assert tuple(item.spec.strategy_id for item in registry.enabled()) == ("ema_adx_trend",)
    with pytest.raises(KeyError, match="Unknown strategy"):
        registry.resolve("not-configured")
    assert StrategyMode.FROZEN.value == "frozen"
