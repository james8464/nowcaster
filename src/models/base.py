from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class ModelSpec:
    name: str
    ablation: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelRunRecord:
    run_id: str
    model_name: str
    ablation: str
    training_start: date
    training_end: date
    test_start: date
    test_end: date
    observations: int
    feature_columns: list[str]
    test_indices: list[int]
    parameters: dict[str, Any]
    metrics: dict[str, float | int]
    fitted_model: Any | None = None


@dataclass(frozen=True)
class ExpandingFold:
    train_indices: list[int]
    test_indices: list[int]
    training_start: date
    training_end: date
    test_start: date
    test_end: date
