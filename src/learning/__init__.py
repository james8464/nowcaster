"""Interpretable, causally bounded strategy learning."""

from importlib import import_module

_EXPORTS = {
    **dict.fromkeys(("RuleNode", "mutate_rule", "semantic_dedupe"), "grammar"),
    **dict.fromkeys(("ForwardEvidence", "PromotionDecision", "promote_candidate"), "promotion"),
    **dict.fromkeys(
        (
            "ContextualCandidate",
            "ContextualLearningExperiment",
            "ContextualSearchSpace",
            "LearningExperiment",
            "LearningResult",
            "RuleCandidate",
            "discover_rules",
            "evaluate_contextual_candidate",
            "generate_contextual_candidates",
        ),
        "search",
    ),
}


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module(f"src.learning.{_EXPORTS[name]}"), name)
    globals()[name] = value
    return value


__all__ = [
    "ContextualCandidate",
    "ContextualLearningExperiment",
    "ContextualSearchSpace",
    "ForwardEvidence",
    "LearningExperiment",
    "LearningResult",
    "PromotionDecision",
    "RuleCandidate",
    "RuleNode",
    "discover_rules",
    "evaluate_contextual_candidate",
    "generate_contextual_candidates",
    "mutate_rule",
    "promote_candidate",
    "semantic_dedupe",
]
