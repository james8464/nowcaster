"""Causal asset-selection and contextual strategy-allocation primitives."""

from src.contextual.types import (
    AssetProfileName,
    ContextLevel,
    EligibilityState,
    MarketRegime,
    StrategyContextKey,
    StrategyDirection,
)

_SERVICE_EXPORTS = {
    "ContextualBacktestResult",
    "ContextualLearningResult",
    "ContextualProgress",
    "ContextualResearchService",
    "ContextualRunRequest",
    "ContextualRunResult",
    "UniverseScreenResult",
}


def __getattr__(name: str):
    if name in _SERVICE_EXPORTS:
        from src.contextual import service

        return getattr(service, name)
    raise AttributeError(name)


__all__ = [
    "AssetProfileName",
    "ContextLevel",
    "EligibilityState",
    "MarketRegime",
    "StrategyContextKey",
    "StrategyDirection",
    "ContextualBacktestResult",
    "ContextualLearningResult",
    "ContextualProgress",
    "ContextualResearchService",
    "ContextualRunRequest",
    "ContextualRunResult",
    "UniverseScreenResult",
]
