from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from src.strategies.types import StrategySpec


class SignalGenerator(Protocol):
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class RegisteredStrategy:
    spec: StrategySpec
    generator: SignalGenerator


class StrategyRegistry:
    """An explicit code registry; YAML can configure strategies but never import code."""

    def __init__(self) -> None:
        self._registered: dict[str, RegisteredStrategy] = {}

    def register(self, spec: StrategySpec, generator: SignalGenerator) -> None:
        if spec.strategy_id in self._registered:
            raise ValueError(f"Strategy '{spec.strategy_id}' is already registered")
        if not callable(generator):
            raise TypeError("strategy generator must be callable")
        self._registered[spec.strategy_id] = RegisteredStrategy(spec=spec, generator=generator)

    def register_configured(
        self, specs: Iterable[StrategySpec], generators: Mapping[str, SignalGenerator]
    ) -> None:
        for spec in specs:
            if not spec.enabled:
                continue
            try:
                generator = generators[spec.strategy_id]
            except KeyError as error:
                raise ValueError(f"No statically registered generator for strategy '{spec.strategy_id}'") from error
            self.register(spec, generator)

    def resolve(self, strategy_id: str) -> RegisteredStrategy:
        try:
            return self._registered[strategy_id]
        except KeyError as error:
            raise KeyError(f"Unknown strategy '{strategy_id}'") from error

    def enabled(self) -> tuple[RegisteredStrategy, ...]:
        return tuple(item for item in self._registered.values() if item.spec.enabled)
