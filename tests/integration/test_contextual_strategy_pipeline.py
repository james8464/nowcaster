from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import delete

from src.database.schema import contextual_outcomes
from src.strategies.pipeline import EvaluationOptions
from tests.integration.test_strategy_cli import (
    _configure_strategy,
    _csv_pipeline,
    _ingest_options,
    _scope,
    _write_bars,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _configure_contextual_strategy(project_root: Path) -> None:
    _configure_strategy(project_root)
    for filename in ("asset_selection.yaml", "instruments.yaml"):
        (project_root / "config" / filename).write_text(
            (PROJECT_ROOT / "config" / filename).read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def test_contextual_research_cache_identity_binds_runtime_and_policy_not_storage(
    project_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_contextual_strategy(project_root)
    bars = tmp_path / "contextual-cache.csv"
    _write_bars(bars, 80)
    pipeline, _database = _csv_pipeline(project_root, "duckdb:///:memory:", bars)
    scope = _scope("rsi_reversal")
    registered = pipeline._registered_many(scope)
    as_of = datetime(2026, 8, 20, tzinfo=UTC)

    def identity():
        return pipeline._cohort_payload(scope, registered, "a" * 64, as_of)

    original = identity()
    settings = pipeline._settings
    pipeline._settings = settings.model_copy(update={"database_url": "duckdb:///relocated.duckdb"})
    monkeypatch.setattr("src.strategies.pipeline.git_commit", lambda *_args: "f" * 40)
    assert identity() == original

    pipeline._settings = settings.model_copy(
        update={"deep_research": settings.deep_research.model_copy(update={"crypto_fee_bps": 12.0})}
    )
    assert identity() != original
    pipeline._settings = settings
    monkeypatch.setattr("src.strategies.pipeline.research_source_hash", lambda *_args: "e" * 64)
    assert identity() != original


def test_strategy_evaluation_publishes_contextual_outcomes_without_final_rows(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_contextual_strategy(project_root)
    bar_count = 800
    bars_path = tmp_path / "contextual-bars.csv"
    _write_bars(bars_path, bar_count)
    pipeline, database = _csv_pipeline(
        project_root,
        f"duckdb:///{tmp_path / 'contextual-strategy.duckdb'}",
        bars_path,
    )
    scope = _scope("rsi_reversal")

    assert pipeline.ingest(_ingest_options(scope, count=bar_count)).status == "completed"
    snapshot, unavailable = pipeline._capture_research_snapshot(scope)
    assert snapshot is not None, unavailable
    sealed_final_start = pipeline._raw_final_boundary(snapshot.causal_bars).final_start

    outcome = pipeline.evaluate(EvaluationOptions(scope=scope))
    rows = database.frame("select * from contextual_outcomes order by outcome_available_at")

    assert outcome.status == "completed"
    assert not rows.empty
    assert (pd.to_datetime(rows["outcome_available_at"], utc=True) < sealed_final_start).all()
    assert set(rows["direction"]) <= {"long", "short"}
    assert set(rows["profile"]) == {"crypto_major_spot"}
    assert rows["evidence"].map(lambda item: item["source_decision_hash"]).notna().all()
    assert rows["evidence"].map(lambda item: item["eligibility_evidence_id"]).notna().all()
    probability_sums = rows["regime_probabilities"].map(lambda item: sum(item.values()))
    assert probability_sums.to_numpy() == pytest.approx(1.0)

    contextual_hashes = set(rows["content_hash"].astype(str))
    pipeline._settings = pipeline._settings.model_copy(
        update={"database_url": "duckdb:///a/different/research-location.duckdb"}
    )
    monkeypatch.setattr("src.strategies.pipeline.git_commit", lambda *_args: "f" * 40)
    assert pipeline.evaluate(EvaluationOptions(scope=scope)).status == "reused"
    repeated = database.frame("select content_hash from contextual_outcomes")
    assert set(repeated["content_hash"].astype(str)) == contextual_hashes

    missing_id = str(rows.iloc[0]["outcome_id"])
    with database.engine.begin() as connection:
        connection.execute(delete(contextual_outcomes).where(contextual_outcomes.c.outcome_id == missing_id))
    assert pipeline.evaluate(EvaluationOptions(scope=scope)).status == "completed"
    assert database.scalar("select count(*) from contextual_outcomes") == len(rows)
