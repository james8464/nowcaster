"""Public research facade; importing contracts does not start scientific runtimes."""

from importlib import import_module

_EXPORTS = {
    "CandidateCampaignDefinition": "candidate_campaign",
    "run_full_strategy_research": "full_history",
    "FinalizedSpotFeed": "live_paper_signal_runtime",
    "LivePaperSignalRunner": "live_paper_signal_runtime",
    "PremiumProviderAdapter": "round_two_runtime",
    "UnconfiguredPremiumProviderAdapter": "round_two_runtime",
}


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module(f"src.research.{_EXPORTS[name]}"), name)
    globals()[name] = value
    return value


__all__ = [
    "CandidateCampaignDefinition",
    "FinalizedSpotFeed",
    "LivePaperSignalRunner",
    "PremiumProviderAdapter",
    "UnconfiguredPremiumProviderAdapter",
    "run_full_strategy_research",
]
