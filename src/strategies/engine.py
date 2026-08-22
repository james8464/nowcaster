from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import pandas as pd

from src.database.engine import Database
from src.strategies.ensemble import (
    DEFAULT_ENSEMBLE_CONFIG,
    EnsembleConfig,
    EnsembleDecision,
    canonical_decision_hash,
    combine_current_signals,
    compute_evidence_weights,
    fixed_share_update,
    persist_evidence_weights,
)
from src.strategies.types import StrategyMode
from src.strategies.validation import DEFAULT_VALIDATION_CONFIG, StrategyEvaluation, ValidationConfig


def generate_current_decision(
    evaluations: Sequence[StrategyEvaluation],
    resolved_outcomes: pd.DataFrame,
    as_of: datetime,
    *,
    config: EnsembleConfig = DEFAULT_ENSEMBLE_CONFIG,
    validation_config: ValidationConfig = DEFAULT_VALIDATION_CONFIG,
    database: Database | None = None,
) -> EnsembleDecision:
    """Generate an unlabeled as-of decision from sealed evidence and resolved feedback only."""

    modes = {evaluation.mode for evaluation in evaluations}
    if len(modes) > 1:
        raise ValueError("strategy evaluations in one decision must use the same mode")
    weights = compute_evidence_weights(
        evaluations,
        as_of=as_of,
        config=config,
        validation_config=validation_config,
    )
    if modes and StrategyMode.FROZEN not in modes:
        weights = fixed_share_update(
            weights,
            resolved_outcomes,
            evaluations=evaluations,
            as_of=as_of,
            validation_config=validation_config,
            config=config,
        )
    if database is not None:
        persist_evidence_weights(database, weights)
    return combine_current_signals(
        evaluations,
        weights,
        as_of=as_of,
        config=config,
        validation_config=validation_config,
    )


def decision_to_signal_frame(
    decision: EnsembleDecision,
    *,
    symbol: str,
    strategy_id: str = "evidence_ensemble",
) -> pd.DataFrame:
    """Adapt one immutable ensemble decision to Task 4's executable signal contract."""

    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol or not strategy_id.strip():
        raise ValueError("symbol and strategy_id must not be empty")
    if normalized_symbol != decision.symbol:
        raise ValueError("execution symbol does not match decision context")
    if decision.decision_hash != canonical_decision_hash(decision):
        raise ValueError("ensemble decision hash does not match its canonical payload")
    if decision.data_through is None:
        raise ValueError("an executable ensemble decision requires an explicit data_through timestamp")
    data_through = decision.data_through
    if data_through > decision.as_of:
        raise ValueError("an ensemble decision cannot depend on future data")
    return pd.DataFrame(
        [
            {
                "strategy_id": strategy_id.strip(),
                "symbol": decision.symbol,
                "decision_timestamp": pd.Timestamp(decision.as_of),
                "data_through": pd.Timestamp(data_through),
                "signal": decision.signal,
                "strength": decision.vote_margin if decision.signal else 0.0,
                "reason": ",".join(decision.reasons) if decision.reasons else "evidence ensemble gates passed",
                "decision_hash": decision.decision_hash,
            }
        ]
    )


__all__ = ["decision_to_signal_frame", "generate_current_decision"]
