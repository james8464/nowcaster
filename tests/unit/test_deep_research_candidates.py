from __future__ import annotations

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
