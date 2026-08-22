from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator


class BarInterval(StrEnum):
    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    THIRTY_MINUTES = "30m"
    ONE_HOUR = "1h"
    FOUR_HOURS = "4h"
    ONE_DAY = "1d"


class StrategyFamily(StrEnum):
    TREND = "trend"
    MEAN_REVERSION = "mean_reversion"
    VOLATILITY_VOLUME = "volatility_volume"
    SESSION = "session"
    RELATIVE_VALUE = "relative_value"


class StrategyMode(StrEnum):
    DEVELOPMENT = "development"
    WALK_FORWARD_LEARNING = "walk_forward_learning"
    FROZEN = "frozen"
    PAPER = "paper"


ParameterValue = float | int | str | bool
ImmutableParameters = Annotated[
    Mapping[str, ParameterValue],
    PlainSerializer(lambda value: dict(value), return_type=dict[str, ParameterValue], when_used="always"),
]


def _canonical_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is not UTC:
            raise ValueError("timestamps must be explicit UTC datetimes")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class StrategySpec(BaseModel):
    """An immutable, versioned contract for a causal signal generator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str
    family: StrategyFamily
    version: str
    intervals: tuple[BarInterval, ...]
    warmup_bars: int = Field(gt=0)
    parameters: ImmutableParameters
    enabled: bool = True

    @field_validator("strategy_id", "version")
    @classmethod
    def non_empty_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("strategy identifiers must not be empty")
        return normalized

    @field_validator("intervals")
    @classmethod
    def unique_intervals(cls, value: tuple[BarInterval, ...]) -> tuple[BarInterval, ...]:
        if not value:
            raise ValueError("at least one bar interval is required")
        if len(value) != len(set(value)):
            raise ValueError("bar intervals must be unique")
        return value

    @field_validator("parameters")
    @classmethod
    def scalar_parameters(cls, value: Mapping[str, ParameterValue]) -> Mapping[str, ParameterValue]:
        for name, parameter in value.items():
            if not name.strip():
                raise ValueError("parameter names must not be empty")
            if type(parameter) not in (float, int, str, bool):
                raise ValueError("strategy parameters must be scalar JSON values")
        return MappingProxyType(dict(value))

    @model_validator(mode="after")
    def validate_parameter_contract(self) -> StrategySpec:
        if "__generator__" in self.parameters:
            raise ValueError("strategy generators are registered in code, not YAML")
        return self

    @property
    def definition_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="python", exclude={"enabled"}))

    @property
    def deterministic_version(self) -> str:
        return f"{self.version}-{self.definition_hash[:12]}"
