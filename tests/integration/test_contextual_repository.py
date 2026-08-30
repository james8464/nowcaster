from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from src.contextual.eligibility import AssetEligibilityEvidence
from src.contextual.repository import ContextualRepository
from src.contextual.types import AssetProfileName, EligibilityState, StrategyDirection
from src.database.engine import Database
from src.strategies.types import BarInterval

NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)
EXPECTED_TABLES = {
    "contextual_outcomes",
    "asset_eligibility_evidence",
    "regime_posteriors",
    "contextual_estimates",
    "contextual_covariances",
    "contextual_weights",
    "portfolio_research_decisions",
    "contextual_learning_trials",
    "contextual_drift_events",
}


def _evidence() -> AssetEligibilityEvidence:
    return AssetEligibilityEvidence(
        evidence_id="eligibility-1",
        state=EligibilityState.ELIGIBLE,
        reasons=(),
        structural_reasons=(),
        data_reasons=(),
        liquidity_reasons=(),
        quality_score=0.90,
        policy_hash="p" * 64,
        input_hash="i" * 64,
        as_of=NOW,
        data_through=datetime(2026, 8, 30, 11, 59, 55, tzinfo=UTC),
        provider="binance",
        feed="spot",
        venue="Binance",
        product="spot",
        asset_class="crypto",
        profile=AssetProfileName.CRYPTO_MAJOR_SPOT,
        symbol="BTCUSDT",
        interval=BarInterval.FIVE_MINUTES,
        direction=StrategyDirection.LONG,
        liquidity_grade="observed",
        source_event_watermark="bar-100",
    )


def test_contextual_repository_is_append_only_and_idempotent(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'contextual.duckdb'}")
    database.initialize()
    repository = ContextualRepository(database, clock=lambda: NOW)
    evidence = _evidence()

    assert set(database.table_names()) >= EXPECTED_TABLES
    assert repository.append_eligibility(evidence) == 1
    assert repository.append_eligibility(evidence) == 0
    with pytest.raises(ValueError, match="hash"):
        repository.append_eligibility(replace(evidence, quality_score=0.99))
    assert database.scalar("select count(*) from asset_eligibility_evidence") == 1


def test_schema_v13_initialization_is_idempotent(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'migration.duckdb'}")
    database.initialize()
    database.initialize()

    assert database.schema_version() == 13
    assert database.scalar("select count(*) from schema_versions where version = 13") == 1


def test_repository_appends_remaining_contextual_evidence_types(tmp_path) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'all-evidence.duckdb'}")
    database.initialize()
    repository = ContextualRepository(database, clock=lambda: NOW)
    context = {
        "dataset_hash": "dataset-v1",
        "protocol_hash": "protocol-v1",
        "provider": "binance",
        "feed": "spot",
        "venue": "Binance",
        "product": "spot",
        "asset_class": "crypto",
        "profile": "crypto_major_spot",
        "symbol": "BTCUSDT",
        "interval": "5m",
    }

    assert repository.append_regime_posterior(
        {
            **context,
            "model_hash": "model-v1",
            "decision_timestamp": NOW,
            "feature_through": NOW,
            "training_through": NOW,
            "status": "fitted",
            "probabilities": {
                "trend_normal": 0.4,
                "trend_elevated_volatility": 0.2,
                "range_liquid": 0.3,
                "stressed_or_illiquid": 0.1,
            },
        }
    ) == 1
    assert repository.append_portfolio_decision(
        {
            "selection_id": "selection-v1",
            "decision_hash": "decision-v1",
            "context_hash": "context-v1",
            "symbol": "BTCUSDT",
            "direction": "long",
            "effective_at": NOW,
            "status": "selected",
            "selected": True,
            "weight": 0.05,
            "exclusion_reasons": [],
        }
    ) == 1
    assert repository.append_learning_trial(
        {
            "global_trial_id": "trial-v1",
            "dataset_hash": "dataset-v1",
            "protocol_hash": "protocol-v1",
            "candidate_hash": "candidate-v1",
            "ordinal": 1,
            "evaluated_at": NOW,
            "status": "succeeded",
            "definition": {"risk_penalty": 4.0},
        }
    ) == 1
    assert repository.append_drift_event(
        {
            "context_hash": "context-v1",
            "effective_at": NOW,
            "status": "warning",
            "reason": "calibration_shift",
        }
    ) == 1
