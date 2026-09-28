from __future__ import annotations

from dataclasses import replace

import pytest

from src.deep_research.candidates import CandidateDefinition, CandidateSearchSpace, generate_candidates
from src.learning.grammar import RuleNode, crossover_rules


def _rule(name: str, threshold: float) -> RuleNode:
    return RuleNode.compare("gt", RuleNode.indicator(name, lag=1), RuleNode.number(threshold))


def test_generation_is_seeded_bounded_and_has_stable_ordinals() -> None:
    space = CandidateSearchSpace(
        strategy_id="ema_adx_trend",
        base_parameters={"fast": 8, "slow": 21},
        parameter_grid={"fast": (5, 8, 13), "slow": (21, 34)},
        seed_rules=(_rule("rsi", 50), _rule("adx", 20)),
        indicators=("rsi", "adx"),
        thresholds=(20.0, 50.0, 80.0),
        maximum_lag=2,
        max_depth=4,
        max_nodes=15,
    )

    first = generate_candidates(space, count=12, seed=7)
    second = generate_candidates(space, count=12, seed=7)

    assert first == second
    assert [attempt.ordinal for attempt in first] == list(range(1, 13))
    assert all(attempt.candidate.rule is None or attempt.candidate.rule.depth <= 4 for attempt in first)
    assert all(attempt.candidate.rule is None or attempt.candidate.rule.node_count <= 15 for attempt in first)
    assert {attempt.candidate.kind for attempt in first} >= {"baseline", "parameter", "rule"}


def test_semantic_duplicates_are_counted_but_marked_without_evaluation() -> None:
    same = _rule("rsi", 50)
    space = CandidateSearchSpace(
        strategy_id="rsi_reversal",
        base_parameters={"period": 14},
        parameter_grid={},
        seed_rules=(same, same),
        indicators=("rsi",),
        thresholds=(50.0,),
        maximum_lag=1,
        max_depth=4,
        max_nodes=15,
    )

    attempts = generate_candidates(space, count=4, seed=3)

    assert len(attempts) == 4
    assert sum(attempt.duplicate_of is not None for attempt in attempts) >= 1
    assert len({attempt.candidate.identity for attempt in attempts}) < len(attempts)


def test_crossover_stays_inside_closed_typed_grammar() -> None:
    child = crossover_rules(
        _rule("rsi", 50),
        RuleNode.compare("lt", RuleNode.indicator("adx", lag=1), RuleNode.number(25)),
        conjunction="and",
        max_depth=4,
        max_nodes=15,
    )

    assert child.render() == "(rsi lagged 1 bar is greater than 50) AND (adx lagged 1 bar is less than 25)"
    assert "python" not in child.canonical.lower()
    assert "shell" not in child.canonical.lower()


def test_later_generation_mutates_around_the_development_incumbent_without_open_ended_code() -> None:
    space = CandidateSearchSpace(
        strategy_id="ema_adx_trend",
        base_parameters={"fast": 8, "slow": 21},
        parameter_grid={"fast": (5, 8, 13), "slow": (21, 34)},
        seed_rules=(_rule("rsi", 50),),
        indicators=("rsi", "adx"),
        thresholds=(20.0, 50.0, 80.0),
    )
    incumbent = CandidateDefinition(
        "parameter",
        "ema_adx_trend",
        parameters=(("fast", 13), ("slow", 34), ("incumbent_marker", 99)),
    )

    attempts = generate_candidates(space, count=8, seed=19, incumbent=incumbent)

    evolved = [
        dict(attempt.candidate.parameters) for attempt in attempts if attempt.candidate.identity != incumbent.identity
    ]
    assert any(parameters.get("incumbent_marker") == 99 for parameters in evolved)
    assert all(attempt.candidate.kind in {"baseline", "parameter", "rule", "crossover"} for attempt in attempts)


def test_incumbent_only_parameter_neighbors_preserve_all_other_winner_values():
    space = CandidateSearchSpace(
        "ema_adx_trend",
        {"fast": 5, "slow": 21},
        {"fast": (5, 13), "slow": (21, 34)},
        (_rule("rsi", 50),),
        ("rsi",),
        (50.0,),
    )
    winner = CandidateDefinition("parameter", "ema_adx_trend", (("fast", 13), ("slow", 34), ("marker", 99)))
    attempts = generate_candidates(space, count=50, seed=19, incumbent=winner, incumbent_only=True)
    assert len(attempts) == 50
    assert {attempt.candidate.parameters for attempt in attempts} == {
        (("fast", 5), ("marker", 99), ("slow", 34)),
        (("fast", 13), ("marker", 99), ("slow", 21)),
    }
    assert all(attempt.candidate.kind == "parameter" and attempt.candidate.rule is None for attempt in attempts)
    assert sum(attempt.duplicate_of is not None for attempt in attempts) == 48
    assert attempts == generate_candidates(space, count=50, seed=19, incumbent=winner, incumbent_only=True)


def test_incumbent_only_rule_neighbors_change_one_field_not_the_parent_tree():
    winner = CandidateDefinition(
        "rule",
        "rsi_reversal",
        (("period", 7),),
        RuleNode.all_of(_rule("rsi", 50), RuleNode.negate(_rule("adx", 20))),
    )
    space = CandidateSearchSpace(
        "rsi_reversal",
        {"period": 14},
        {"period": (14, 21)},
        (_rule("volume", 100),),
        ("rsi", "adx", "volume"),
        (20.0, 50.0, 80.0),
        maximum_lag=1,
        max_depth=4,
        max_nodes=8,
    )

    def changed_fields(parent, child):
        assert len(parent.children) == len(child.children)
        assert parent.parameters == child.parameters
        assert child.lag <= 1
        return sum(
            getattr(parent, field) != getattr(child, field) for field in ("operator", "name", "value", "lag")
        ) + sum(changed_fields(left, right) for left, right in zip(parent.children, child.children, strict=True))

    attempts = generate_candidates(space, count=50, seed=19, incumbent=winner, incumbent_only=True)
    for attempt in attempts:
        assert attempt.candidate.parameters == (("period", 7),)
        assert attempt.candidate.kind == "rule"
        assert changed_fields(winner.rule, attempt.candidate.rule) == 1
        assert attempt.candidate.rule.depth == 4
        assert attempt.candidate.rule.node_count == 8
    assert any(attempt.duplicate_of is not None for attempt in attempts)
    assert attempts == generate_candidates(space, count=50, seed=19, incumbent=winner, incumbent_only=True)
    for invalid_bound in ({"max_depth": 3}, {"max_nodes": 7}, {"maximum_lag": 0}):
        with pytest.raises(ValueError, match="exceeds"):
            generate_candidates(
                replace(space, **invalid_bound), count=50, seed=19, incumbent=winner, incumbent_only=True
            )


def test_exhausted_incumbent_parameter_neighborhood_repeats_parent_for_budget_accounting():
    space = CandidateSearchSpace("rsi_reversal", {"period": 14}, {}, (), ("rsi",), (50.0,))
    winner = CandidateDefinition("parameter", "rsi_reversal", (("period", 14),))
    attempts = generate_candidates(space, count=3, seed=19, incumbent=winner, incumbent_only=True)
    assert [attempt.candidate for attempt in attempts] == [winner, winner, winner]
    assert [attempt.duplicate_of for attempt in attempts] == [None, 1, 1]
    with pytest.raises(ValueError, match="incumbent"):
        generate_candidates(space, count=3, seed=19, incumbent_only=True)
