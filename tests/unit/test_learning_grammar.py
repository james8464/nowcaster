from __future__ import annotations

import pandas as pd
import pytest

from src.learning.grammar import RuleNode, mutate_rule, semantic_dedupe


def test_typed_comparisons_crossovers_and_boolean_nodes_evaluate_causally() -> None:
    fast = RuleNode.indicator("fast", lag=1)
    slow = RuleNode.indicator("slow", lag=1)
    rule = RuleNode.all_of(
        RuleNode.compare("gt", fast, RuleNode.number(2.0)),
        RuleNode.cross("above", fast, slow),
    )
    frame = pd.DataFrame({"fast": [1.0, 2.0, 3.0, 4.0], "slow": [2.0, 2.0, 2.0, 2.0]})

    assert rule.evaluate(frame).tolist() == [False, False, False, True]
    with pytest.raises(ValueError, match="numeric"):
        RuleNode.compare("gt", rule, slow)


def test_canonical_serialization_normalizes_commutativity_and_inverse_comparisons() -> None:
    left = RuleNode.compare("gt", RuleNode.indicator("rsi", lag=1), RuleNode.number(50))
    equivalent = RuleNode.compare("lt", RuleNode.number(50), RuleNode.indicator("rsi", lag=1))
    volume = RuleNode.compare("gt", RuleNode.indicator("volume", lag=1), RuleNode.number(100))

    first = RuleNode.all_of(left, volume)
    second = RuleNode.all_of(volume, equivalent)

    assert first.canonical == second.canonical
    assert first.semantic_hash == second.semantic_hash


def test_semantic_dedupe_removes_only_canonical_duplicates() -> None:
    a = RuleNode.compare("gt", RuleNode.indicator("rsi", lag=1), RuleNode.number(50))
    same = RuleNode.compare("lt", RuleNode.number(50), RuleNode.indicator("rsi", lag=1))
    different = RuleNode.compare("lt", RuleNode.indicator("rsi", lag=1), RuleNode.number(50))

    assert semantic_dedupe((a, same, different)) == (a, different)


def test_semantic_dedupe_normalizes_inverse_crossovers_and_idempotent_boolean_nodes() -> None:
    fast = RuleNode.indicator("fast", lag=1)
    slow = RuleNode.indicator("slow", lag=1)
    above = RuleNode.cross("above", fast, slow)
    same_cross = RuleNode.cross("below", slow, fast)
    repeated = RuleNode.all_of(above, same_cross)

    assert semantic_dedupe((above, same_cross, repeated)) == (above,)


@pytest.mark.parametrize("combine", [RuleNode.all_of, RuleNode.any_of])
def test_nested_boolean_canonicalization_is_associative_and_idempotent(combine) -> None:
    a = RuleNode.compare("gt", RuleNode.indicator("rsi", lag=1), RuleNode.number(50))
    b = RuleNode.compare("gt", RuleNode.indicator("volume", lag=1), RuleNode.number(100))

    nested = combine(combine(a, a), b)
    simple = combine(a, b)

    assert nested.canonical == simple.canonical
    assert nested.semantic_hash == simple.semantic_hash


def test_maximum_depth_and_node_count_are_enforced() -> None:
    leaf = RuleNode.compare("gt", RuleNode.indicator("close", lag=1), RuleNode.number(0))
    deep = RuleNode.negate(RuleNode.negate(leaf))

    assert deep.depth == 4
    assert deep.node_count == 5
    with pytest.raises(ValueError, match="depth"):
        deep.validate_bounds(max_depth=3, max_nodes=10)
    with pytest.raises(ValueError, match="node"):
        deep.validate_bounds(max_depth=5, max_nodes=4)


def test_seeded_mutation_is_deterministic_and_stays_inside_bounds() -> None:
    original = RuleNode.compare("gt", RuleNode.indicator("rsi", lag=1), RuleNode.number(50))

    first = mutate_rule(
        original,
        indicators=("rsi", "volume", "close"),
        thresholds=(20.0, 50.0, 80.0),
        maximum_lag=3,
        max_depth=4,
        max_nodes=9,
        seed=17,
    )
    second = mutate_rule(
        original,
        indicators=("close", "rsi", "volume"),
        thresholds=(80.0, 20.0, 50.0),
        maximum_lag=3,
        max_depth=4,
        max_nodes=9,
        seed=17,
    )

    assert first == second
    assert first != original
    first.validate_bounds(max_depth=4, max_nodes=9)


def test_plain_language_rendering_is_stable_and_interpretable() -> None:
    rule = RuleNode.any_of(
        RuleNode.cross(
            "below",
            RuleNode.indicator("ema_fast", lag=1),
            RuleNode.indicator("ema_slow", lag=1),
        ),
        RuleNode.negate(RuleNode.compare("lt", RuleNode.indicator("rsi", lag=2), RuleNode.number(30))),
    )

    assert rule.render() == (
        "(ema_fast lagged 1 bar crosses below ema_slow lagged 1 bar) OR NOT (rsi lagged 2 bars is less than 30)"
    )


def test_rule_evaluation_is_prefix_invariant_when_future_rows_are_appended() -> None:
    rule = RuleNode.cross(
        "above",
        RuleNode.indicator("fast", lag=1),
        RuleNode.indicator("slow", lag=1),
    )
    prefix = pd.DataFrame({"fast": [1.0, 2.0, 3.0], "slow": [2.0, 2.0, 2.0]})
    extended = pd.concat(
        [prefix, pd.DataFrame({"fast": [1000.0, -1000.0], "slow": [-1000.0, 1000.0]})],
        ignore_index=True,
    )

    assert rule.evaluate(prefix).equals(rule.evaluate(extended).iloc[: len(prefix)])
