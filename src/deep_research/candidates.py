from __future__ import annotations

import itertools
import math
import random
from contextlib import suppress
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from src.learning.grammar import RuleNode, crossover_rules, mutate_rule
from src.strategies.types import ParameterValue, canonical_hash


@dataclass(frozen=True, slots=True)
class CandidateSearchSpace:
    strategy_id: str
    base_parameters: dict[str, ParameterValue]
    parameter_grid: dict[str, tuple[ParameterValue, ...]]
    seed_rules: tuple[RuleNode, ...]
    indicators: tuple[str, ...]
    thresholds: tuple[float, ...]
    maximum_lag: int = 2
    max_depth: int = 4
    max_nodes: int = 15

    def __post_init__(self) -> None:
        strategy_id = self.strategy_id.strip()
        if not strategy_id:
            raise ValueError("strategy_id must not be empty")
        object.__setattr__(self, "strategy_id", strategy_id)
        if self.maximum_lag < 0 or self.max_depth < 3 or self.max_nodes < 3:
            raise ValueError("candidate grammar bounds are invalid")
        indicators = tuple(sorted({value.strip() for value in self.indicators if value.strip()}))
        thresholds = tuple(sorted({float(value) for value in self.thresholds if math.isfinite(float(value))}))
        if not indicators or not thresholds:
            raise ValueError("candidate search requires indicators and finite thresholds")
        object.__setattr__(self, "indicators", indicators)
        object.__setattr__(self, "thresholds", thresholds)
        for rule in self.seed_rules:
            rule.validate_bounds(max_depth=self.max_depth, max_nodes=self.max_nodes)
        normalized_grid: dict[str, tuple[ParameterValue, ...]] = {}
        for name, values in sorted(self.parameter_grid.items()):
            if not name.strip() or not values:
                raise ValueError("parameter grids require named non-empty values")
            if any(type(value) not in (float, int, str, bool) for value in values):
                raise ValueError("parameter grids accept only scalar JSON values")
            normalized_grid[name.strip()] = tuple(values)
        object.__setattr__(self, "parameter_grid", MappingProxyType(normalized_grid))
        object.__setattr__(self, "base_parameters", MappingProxyType(dict(self.base_parameters)))


@dataclass(frozen=True, slots=True)
class CandidateDefinition:
    kind: str
    strategy_id: str
    parameters: tuple[tuple[str, ParameterValue], ...] = ()
    rule: RuleNode | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"baseline", "parameter", "rule", "crossover"}:
            raise ValueError("candidate kind is outside the closed search space")
        if not self.strategy_id.strip():
            raise ValueError("candidate strategy_id must not be empty")
        object.__setattr__(self, "parameters", tuple(sorted(self.parameters)))
        if self.kind in {"rule", "crossover"} and self.rule is None:
            raise ValueError("rule candidates require a typed rule")

    @property
    def identity(self) -> str:
        return canonical_hash(
            {
                "kind": self.kind,
                "strategy_id": self.strategy_id,
                "parameters": dict(self.parameters),
                "rule": self.rule.canonical if self.rule is not None else None,
            }
        )

    def payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "strategy_id": self.strategy_id,
            "parameters": dict(self.parameters),
            "rule": self.rule._canonical_value() if self.rule is not None else None,
        }


@dataclass(frozen=True, slots=True)
class CandidateGenerationAttempt:
    ordinal: int
    candidate: CandidateDefinition
    duplicate_of: int | None = None


def _parameter_candidates(space: CandidateSearchSpace, generator: random.Random) -> list[CandidateDefinition]:
    if not space.parameter_grid:
        return []
    names = tuple(space.parameter_grid)
    combinations = list(itertools.product(*(space.parameter_grid[name] for name in names)))
    generator.shuffle(combinations)
    candidates: list[CandidateDefinition] = []
    for values in combinations:
        parameters = dict(space.base_parameters)
        parameters.update(dict(zip(names, values, strict=True)))
        candidates.append(
            CandidateDefinition(
                kind="parameter",
                strategy_id=space.strategy_id,
                parameters=tuple(parameters.items()),
            )
        )
    return candidates


def _rule_candidates(space: CandidateSearchSpace, seed: int, required: int) -> list[CandidateDefinition]:
    rules = list(space.seed_rules)
    mutation_source = list(space.seed_rules)
    if not mutation_source:
        mutation_source = [
            RuleNode.compare(
                "gt",
                RuleNode.indicator(space.indicators[0], lag=1),
                RuleNode.number(space.thresholds[0]),
            )
        ]
        rules.extend(mutation_source)
    mutation_index = 0
    while len(rules) < max(required, 4):
        base = mutation_source[mutation_index % len(mutation_source)]
        mutation_index += 1
        try:
            rules.append(
                mutate_rule(
                    base,
                    indicators=space.indicators,
                    thresholds=space.thresholds,
                    maximum_lag=space.maximum_lag,
                    max_depth=space.max_depth,
                    max_nodes=space.max_nodes,
                    seed=seed + mutation_index,
                )
            )
        except ValueError:
            break
    if len(rules) >= 2:
        for conjunction in ("and", "or"):
            with suppress(ValueError):
                rules.append(
                    crossover_rules(
                        rules[0],
                        rules[1],
                        conjunction=conjunction,
                        max_depth=space.max_depth,
                        max_nodes=space.max_nodes,
                    )
                )
    return [CandidateDefinition("rule", space.strategy_id, rule=rule) for rule in rules]


def generate_candidates(
    space: CandidateSearchSpace,
    *,
    count: int,
    seed: int,
) -> tuple[CandidateGenerationAttempt, ...]:
    if isinstance(count, bool) or count < 1:
        raise ValueError("candidate count must be positive")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("candidate seed must be an integer")
    generator = random.Random(seed)
    baseline = CandidateDefinition(
        "baseline",
        space.strategy_id,
        tuple(space.base_parameters.items()),
    )
    pool = [baseline, *_parameter_candidates(space, generator), *_rule_candidates(space, seed, count)]
    if not pool:
        raise ValueError("candidate search space is empty")
    attempts: list[CandidateGenerationAttempt] = []
    first_ordinal: dict[str, int] = {}
    for ordinal in range(1, count + 1):
        candidate = pool[(ordinal - 1) % len(pool)]
        duplicate_of = first_ordinal.get(candidate.identity)
        if duplicate_of is None:
            first_ordinal[candidate.identity] = ordinal
        attempts.append(CandidateGenerationAttempt(ordinal, candidate, duplicate_of))
    return tuple(attempts)


__all__ = [
    "CandidateDefinition",
    "CandidateGenerationAttempt",
    "CandidateSearchSpace",
    "generate_candidates",
]
