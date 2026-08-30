"""Interpretable, causally bounded strategy learning."""

from src.learning.grammar import RuleNode, mutate_rule, semantic_dedupe
from src.learning.promotion import ForwardEvidence, PromotionDecision, promote_candidate
from src.learning.search import (
    ContextualCandidate,
    ContextualLearningExperiment,
    ContextualSearchSpace,
    LearningExperiment,
    LearningResult,
    RuleCandidate,
    discover_rules,
    evaluate_contextual_candidate,
    generate_contextual_candidates,
)

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
