"""Gate names shared by read-only monitoring and trading readiness evaluation."""

REQUIRED_READINESS_GATES = frozenset(
    {
        "causal_integrity",
        "cohort_integrity",
        "minimum_forward_observations",
        "observation_integrity",
        "operational_integrity",
        "positive_paper_edge",
        "return_accounting",
        "robustness",
        "stressed_net_edge",
    }
)

__all__ = ["REQUIRED_READINESS_GATES"]
