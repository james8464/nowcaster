from __future__ import annotations

import math
import random
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

import pandas as pd

from src.strategies.types import canonical_hash, canonical_json

ValueType = Literal["number", "boolean"]


class RuleOperator(StrEnum):
    INDICATOR = "indicator"
    NUMBER = "number"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CROSS_ABOVE = "cross_above"
    CROSS_BELOW = "cross_below"
    AND = "and"
    OR = "or"
    NOT = "not"


_COMPARISONS = {RuleOperator.GT, RuleOperator.GTE, RuleOperator.LT, RuleOperator.LTE}
_CROSSOVERS = {RuleOperator.CROSS_ABOVE, RuleOperator.CROSS_BELOW}
_BOOLEAN = {RuleOperator.AND, RuleOperator.OR, RuleOperator.NOT}


@dataclass(frozen=True, slots=True)
class RuleNode:
    """One immutable node in the closed, typed learning grammar."""

    operator: RuleOperator
    children: tuple[RuleNode, ...] = ()
    name: str | None = None
    value: float | None = None
    lag: int = 0
    parameters: tuple[tuple[str, float | int], ...] = ()

    def __post_init__(self) -> None:
        try:
            operator = RuleOperator(self.operator)
        except ValueError as error:
            raise ValueError("rule operator is not in the bounded grammar") from error
        object.__setattr__(self, "operator", operator)
        object.__setattr__(self, "children", tuple(self.children))
        parameters = tuple(sorted(self.parameters))
        if len({name for name, _ in parameters}) != len(parameters):
            raise ValueError("indicator parameter names must be unique")
        if any(not name.strip() for name, _ in parameters):
            raise ValueError("indicator parameter names must not be empty")
        if any(isinstance(value, bool) or not math.isfinite(float(value)) for _, value in parameters):
            raise ValueError("indicator parameters must be finite numbers")
        object.__setattr__(self, "parameters", parameters)

        if operator is RuleOperator.INDICATOR:
            if self.children or self.value is not None or self.name is None or not self.name.strip():
                raise ValueError("indicator nodes require only a non-empty name")
            if self.lag < 0:
                raise ValueError("indicator lag cannot be negative")
            object.__setattr__(self, "name", self.name.strip())
        elif operator is RuleOperator.NUMBER:
            if self.children or self.name is not None or self.value is None or self.parameters or self.lag:
                raise ValueError("number nodes require only a finite value")
            if isinstance(self.value, bool) or not math.isfinite(float(self.value)):
                raise ValueError("number nodes require a finite value")
            object.__setattr__(self, "value", float(self.value))
        else:
            if self.name is not None or self.value is not None or self.parameters or self.lag:
                raise ValueError("non-terminal nodes may contain only typed children")
            expected = 1 if operator is RuleOperator.NOT else 2
            if len(self.children) != expected:
                raise ValueError(f"{operator.value} nodes require {expected} children")
            required_type: ValueType = "boolean" if operator in _BOOLEAN else "number"
            if any(child.value_type != required_type for child in self.children):
                label = "numeric" if required_type == "number" else required_type
                raise ValueError(f"{operator.value} nodes require {label} children")

    @classmethod
    def indicator(
        cls,
        name: str,
        *,
        lag: int = 0,
        parameters: tuple[tuple[str, float | int], ...] = (),
    ) -> RuleNode:
        return cls(RuleOperator.INDICATOR, name=name, lag=lag, parameters=parameters)

    @classmethod
    def number(cls, value: float) -> RuleNode:
        return cls(RuleOperator.NUMBER, value=value)

    @classmethod
    def compare(cls, operator: str, left: RuleNode, right: RuleNode) -> RuleNode:
        try:
            selected = RuleOperator(operator)
        except ValueError as error:
            raise ValueError("comparison operator must be gt, gte, lt, or lte") from error
        if selected not in _COMPARISONS:
            raise ValueError("comparison operator must be gt, gte, lt, or lte")
        return cls(selected, (left, right))

    @classmethod
    def cross(cls, direction: str, left: RuleNode, right: RuleNode) -> RuleNode:
        operators = {"above": RuleOperator.CROSS_ABOVE, "below": RuleOperator.CROSS_BELOW}
        try:
            selected = operators[direction]
        except KeyError as error:
            raise ValueError("crossover direction must be above or below") from error
        return cls(selected, (left, right))

    @classmethod
    def all_of(cls, left: RuleNode, right: RuleNode) -> RuleNode:
        return cls(RuleOperator.AND, (left, right))

    @classmethod
    def any_of(cls, left: RuleNode, right: RuleNode) -> RuleNode:
        return cls(RuleOperator.OR, (left, right))

    @classmethod
    def negate(cls, child: RuleNode) -> RuleNode:
        return cls(RuleOperator.NOT, (child,))

    @property
    def value_type(self) -> ValueType:
        if self.operator in {RuleOperator.INDICATOR, RuleOperator.NUMBER}:
            return "number"
        return "boolean"

    @property
    def depth(self) -> int:
        return 1 + max((child.depth for child in self.children), default=0)

    @property
    def node_count(self) -> int:
        return 1 + sum(child.node_count for child in self.children)

    def validate_bounds(self, *, max_depth: int, max_nodes: int) -> None:
        if max_depth <= 0 or max_nodes <= 0:
            raise ValueError("grammar bounds must be positive")
        if self.depth > max_depth:
            raise ValueError(f"rule depth {self.depth} exceeds maximum depth {max_depth}")
        if self.node_count > max_nodes:
            raise ValueError(f"rule node count {self.node_count} exceeds maximum node count {max_nodes}")

    def _canonical_value(self) -> dict[str, object]:
        if self.operator is RuleOperator.INDICATOR:
            return {
                "lag": self.lag,
                "name": self.name,
                "operator": self.operator.value,
                "parameters": dict(self.parameters),
            }
        if self.operator is RuleOperator.NUMBER:
            return {"operator": self.operator.value, "value": self.value}
        operator = self.operator
        children = [child._canonical_value() for child in self.children]
        if operator in {RuleOperator.GT, RuleOperator.GTE}:
            operator = RuleOperator.LT if operator is RuleOperator.GT else RuleOperator.LTE
            children.reverse()
        if operator is RuleOperator.CROSS_BELOW:
            operator = RuleOperator.CROSS_ABOVE
            children.reverse()
        if operator in {RuleOperator.AND, RuleOperator.OR}:
            flattened: list[dict[str, object]] = []
            for child_value in children:
                if child_value.get("operator") == operator.value:
                    nested = child_value.get("children")
                    if not isinstance(nested, list):
                        raise AssertionError("canonical Boolean children must be a list")
                    flattened.extend(nested)
                else:
                    flattened.append(child_value)
            unique = {canonical_json(child): child for child in flattened}
            children = [unique[key] for key in sorted(unique)]
            if len(children) == 1:
                return children[0]
        return {"children": children, "operator": operator.value}

    @property
    def canonical(self) -> str:
        return canonical_json(self._canonical_value())

    @property
    def semantic_hash(self) -> str:
        return canonical_hash(self._canonical_value())

    def evaluate(self, frame: pd.DataFrame) -> pd.Series:
        if self.operator is RuleOperator.INDICATOR:
            assert self.name is not None
            if self.name not in frame:
                raise ValueError(f"indicator column '{self.name}' is unavailable")
            values = pd.to_numeric(frame[self.name], errors="coerce")
            return values.shift(self.lag) if self.lag else values
        if self.operator is RuleOperator.NUMBER:
            return pd.Series(self.value, index=frame.index, dtype=float)
        left = self.children[0].evaluate(frame)
        if self.operator is RuleOperator.NOT:
            return ~left.fillna(False).astype(bool)
        right = self.children[1].evaluate(frame)
        if self.operator is RuleOperator.GT:
            return (left > right).fillna(False)
        if self.operator is RuleOperator.GTE:
            return (left >= right).fillna(False)
        if self.operator is RuleOperator.LT:
            return (left < right).fillna(False)
        if self.operator is RuleOperator.LTE:
            return (left <= right).fillna(False)
        if self.operator is RuleOperator.CROSS_ABOVE:
            return ((left > right) & (left.shift(1) <= right.shift(1))).fillna(False)
        if self.operator is RuleOperator.CROSS_BELOW:
            return ((left < right) & (left.shift(1) >= right.shift(1))).fillna(False)
        if self.operator is RuleOperator.AND:
            return left.fillna(False).astype(bool) & right.fillna(False).astype(bool)
        if self.operator is RuleOperator.OR:
            return left.fillna(False).astype(bool) | right.fillna(False).astype(bool)
        raise AssertionError("unreachable bounded grammar operator")

    def render(self) -> str:
        if self.operator is RuleOperator.INDICATOR:
            suffix = "bar" if self.lag == 1 else "bars"
            return str(self.name) if not self.lag else f"{self.name} lagged {self.lag} {suffix}"
        if self.operator is RuleOperator.NUMBER:
            assert self.value is not None
            return str(int(self.value)) if self.value.is_integer() else format(self.value, ".12g")
        if self.operator is RuleOperator.NOT:
            return f"NOT ({self.children[0].render()})"
        phrases = {
            RuleOperator.GT: "is greater than",
            RuleOperator.GTE: "is at least",
            RuleOperator.LT: "is less than",
            RuleOperator.LTE: "is at most",
            RuleOperator.CROSS_ABOVE: "crosses above",
            RuleOperator.CROSS_BELOW: "crosses below",
        }
        if self.operator in phrases:
            return f"{self.children[0].render()} {phrases[self.operator]} {self.children[1].render()}"
        conjunction = "AND" if self.operator is RuleOperator.AND else "OR"
        rendered = tuple(child.render() for child in self.children)
        grouped = tuple(
            text if child.operator is RuleOperator.NOT else f"({text})"
            for child, text in zip(self.children, rendered, strict=True)
        )
        return f"{grouped[0]} {conjunction} {grouped[1]}"


def semantic_dedupe(nodes: tuple[RuleNode, ...]) -> tuple[RuleNode, ...]:
    unique: dict[str, RuleNode] = {}
    for node in nodes:
        unique.setdefault(node.semantic_hash, node)
    return tuple(unique.values())


def mutate_rule(
    rule: RuleNode,
    *,
    indicators: tuple[str, ...],
    thresholds: tuple[float, ...],
    maximum_lag: int,
    max_depth: int,
    max_nodes: int,
    seed: int,
) -> RuleNode:
    """Return a deterministic bounded mutation independent of caller ordering."""

    names = tuple(sorted({name.strip() for name in indicators if name.strip()}))
    values = tuple(sorted({float(value) for value in thresholds if math.isfinite(float(value))}))
    if not names or not values or maximum_lag < 0:
        raise ValueError("mutation requires indicators, finite thresholds, and a non-negative maximum lag")
    options = [
        RuleNode.compare(
            operator,
            RuleNode.indicator(name, lag=lag),
            RuleNode.number(threshold),
        )
        for operator in ("gt", "gte", "lt", "lte")
        for name in names
        for lag in range(maximum_lag + 1)
        for threshold in values
    ]
    random.Random(seed).shuffle(options)
    for option in options:
        option.validate_bounds(max_depth=max_depth, max_nodes=max_nodes)
        if option.semantic_hash != rule.semantic_hash:
            return option
    raise ValueError("the bounded mutation space contains no semantic alternative")


def crossover_rules(
    left: RuleNode,
    right: RuleNode,
    *,
    conjunction: Literal["and", "or"],
    max_depth: int,
    max_nodes: int,
) -> RuleNode:
    """Combine two Boolean rules without extending the closed grammar."""

    if left.value_type != "boolean" or right.value_type != "boolean":
        raise ValueError("crossover requires two Boolean rules")
    if conjunction == "and":
        child = RuleNode.all_of(left, right)
    elif conjunction == "or":
        child = RuleNode.any_of(left, right)
    else:
        raise ValueError("conjunction must be 'and' or 'or'")
    child.validate_bounds(max_depth=max_depth, max_nodes=max_nodes)
    return child


__all__ = ["RuleNode", "RuleOperator", "crossover_rules", "mutate_rule", "semantic_dedupe"]
