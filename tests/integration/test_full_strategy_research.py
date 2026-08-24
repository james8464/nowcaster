from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from src.app_snapshot.models import AppSnapshot
from src.cli import app
from src.config.settings import Settings
from src.database.engine import Database
from src.ingestion.bars import INTERVAL_DURATION, MarketBar
from src.strategies.datasets import BarRepository
from src.strategies.registry import StrategyRegistry
from src.strategies.types import BarInterval, canonical_hash

RUNNER = CliRunner()


class _NetworkForbiddenClient:
    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def request(self, *_args, **_kwargs):
        raise AssertionError("verified external cache must satisfy the exhaustive replay")


def _seed_binance_cache(
    cache_dir: Path,
    *,
    cutoff: datetime,
    earliest: datetime,
    chunk_count: int,
) -> None:
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for interval in (
            BarInterval.FIVE_MINUTES,
            BarInterval.FIFTEEN_MINUTES,
            BarInterval.ONE_HOUR,
            BarInterval.FOUR_HOURS,
        ):
            duration = INTERVAL_DURATION[interval]

            def payload(opened_at: datetime, interval_duration: timedelta = duration) -> bytes:
                raw_bar = [
                    int(opened_at.timestamp() * 1_000),
                    "100",
                    "102",
                    "99",
                    "101",
                    "10",
                    int((opened_at + interval_duration).timestamp() * 1_000) - 1,
                    "1000",
                    10,
                    "5",
                    "500",
                    "0",
                ]
                return json.dumps([raw_bar], separators=(",", ":")).encode()

            parent = cache_dir / "binance" / "spot" / symbol / interval.value
            probe = parent / f"{cutoff.strftime('earliest-through-%Y%m%dT%H%M%SZ')}.json"
            pages = [(probe, payload(earliest))]
            for index in range(chunk_count):
                chunk_start = earliest + timedelta(days=30 * index)
                chunk_end = min(chunk_start + timedelta(days=30), cutoff)
                pages.append(
                    (
                        parent
                        / (f"{int(chunk_start.timestamp() * 1_000)}-{int(chunk_end.timestamp() * 1_000)}-1000.json"),
                        payload(chunk_start),
                    )
                )
            for path, content in pages:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                path.with_suffix(".sha256").write_text(hashlib.sha256(content).hexdigest() + "\n")


def _project_copy(source: Path, destination: Path) -> Path:
    destination.mkdir()
    shutil.copytree(source / "config", destination / "config")
    fixture = destination / "data" / "demo" / "intraday" / "research-fixture.json"
    fixture.parent.mkdir(parents=True)
    shutil.copy2(source / "data" / "demo" / "intraday" / "research-fixture.json", fixture)
    return destination


def _run_ci_research(project_root: Path, destination: Path):
    return RUNNER.invoke(
        app,
        [
            "strategy",
            "research",
            "--project-root",
            str(project_root),
            "--profile",
            "ci",
            "--database-url",
            f"duckdb:///{destination / 'research.duckdb'}",
            "--output-dir",
            str(destination),
        ],
    )


def test_ci_research_accounts_for_every_strategy_and_is_reproducible(tmp_path: Path) -> None:
    """Catch omitted/failed strategies leaking into an ensemble or divergent published artifacts."""

    project_root = Path(__file__).resolve().parents[2]
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    first = _run_ci_research(project_root, first_dir)
    second = _run_ci_research(project_root, second_dir)

    assert first.exit_code == second.exit_code == 0, first.output or second.output
    first_payload = (first_dir / "research-summary.json").read_bytes()
    second_payload = (second_dir / "research-summary.json").read_bytes()
    assert first_payload == second_payload

    summary = json.loads(first_payload)
    configured = {spec.strategy_id for spec in Settings.load(project_root, mode="test").strategies.enabled}
    catalog = summary["strategy_catalog"]
    assert {item["strategy_id"] for item in catalog} == configured
    assert len(catalog) == len(configured)
    assert all(item["status"] in {"evaluated", "rejected", "unavailable", "failed"} for item in catalog)
    assert all(item.get("reason") for item in catalog if item["status"] in {"unavailable", "failed"})

    ignored = {item["strategy_id"] for item in catalog if item["status"] in {"unavailable", "failed"}}
    ensemble_ids = {item["strategy_id"] for item in summary["ensemble_components"]}
    assert ignored
    assert ignored.isdisjoint(ensemble_ids)
    assert all(item["weight"] >= 0 for item in summary["ensemble_components"])
    assert summary["ensemble_policy"] == {
        "equal_weight_shrinkage": 0.5,
        "maximum_family_weight": 0.5,
        "maximum_strategy_weight": 0.25,
        "nonnegative": True,
    }

    snapshot = AppSnapshot.model_validate_json((first_dir / "nowcaster-snapshot.json").read_text(encoding="utf-8"))
    report = (first_dir / "strategy-research.md").read_text(encoding="utf-8")
    assert snapshot.schema_version == 3
    assert len(snapshot.strategies) == summary["snapshot_counts"]["strategies"]
    assert len(snapshot.ensemble_components) == summary["snapshot_counts"]["ensemble_components"]
    assert summary["semantic_snapshot_hash"] in report
    assert "Historical evidence is not live proof" in report
    assert "does not promise profit" in report
    assert summary["data_quality"]["leakage_checks_passed"] is True
    assert summary["data_quality"]["duplicate_logical_bars"] == 0


def test_ci_research_rebuilds_successful_survivors_as_one_cohort_after_a_real_strategy_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.research import full_history

    project_root = Path(__file__).resolve().parents[2]
    original = full_history.build_strategy_registry

    def registry_with_failure(specs):
        built = original(specs)
        replaced = StrategyRegistry()
        for registered in built.enabled():
            generator = registered.generator
            if registered.spec.strategy_id == "rsi_reversal":

                def generator(*_args, **_kwargs):
                    raise RuntimeError("deliberate research failure")

            replaced.register(registered.spec, generator, registered.metadata)
        return replaced

    monkeypatch.setattr(full_history, "build_strategy_registry", registry_with_failure)
    output_dir = tmp_path / "failure"

    result = _run_ci_research(project_root, output_dir)

    assert result.exit_code == 0, result.output
    summary = json.loads((output_dir / "research-summary.json").read_text())
    failed = next(item for item in summary["strategy_catalog"] if item["strategy_id"] == "rsi_reversal")
    assert failed["status"] == "failed"
    assert "deliberate research failure" in failed["reason"]
    assert "rsi_reversal" not in {item["strategy_id"] for item in summary["ensemble_components"]}

    database = Database.from_url(f"duckdb:///{output_dir / 'research.duckdb'}")
    runs = database.frame(
        "select strategy_run_id, strategy_id, symbol, interval, status, metrics "
        "from strategy_runs order by run_timestamp, strategy_id"
    )
    assert runs.loc[(runs["strategy_id"] == "rsi_reversal") & (runs["status"] == "evaluated")].empty
    weights = database.frame("select strategy_id, evidence from ensemble_weights order by strategy_id")
    assert "rsi_reversal" not in set(weights["strategy_id"])

    survivor_cohorts: dict[str, set[str]] = {}
    affected_attempts = [attempt for attempt in summary["attempts"] if "rsi_reversal" in attempt.get("strategies", [])]
    assert affected_attempts
    for attempt in affected_attempts:
        survivors = set(attempt["strategies"]) - {"rsi_reversal"}
        scope_runs = runs.loc[
            (runs["symbol"] == attempt["symbol"])
            & (runs["interval"] == attempt["interval"])
            & (runs["status"] == "evaluated")
        ]

        def member_ids(metrics: object) -> set[str]:
            if not isinstance(metrics, dict):
                return set()
            return {
                str(member["strategy_id"])
                for member in metrics.get("cohort_members", [])
                if isinstance(member, dict) and "strategy_id" in member
            }

        joint = scope_runs.loc[
            scope_runs["metrics"].map(
                lambda metrics, expected_survivors=survivors: member_ids(metrics) == expected_survivors
            )
        ]
        assert set(joint["strategy_id"]) == survivors
        cohort_ids = {str(metrics["cohort_id"]) for metrics in joint["metrics"]}
        decision_hashes = {str(metrics["cohort_decision_hash"]) for metrics in joint["metrics"]}
        assert len(cohort_ids) == len(decision_hashes) == 1
        cohort_id = cohort_ids.pop()
        cohort_weights = weights.loc[
            weights["evidence"].map(
                lambda evidence, selected_cohort_id=cohort_id: (
                    isinstance(evidence, dict) and evidence.get("cohort_id") == selected_cohort_id
                )
            )
        ]
        assert set(cohort_weights["strategy_id"]) == survivors
        assert all(member_ids(evidence) == survivors for evidence in cohort_weights["evidence"])
        survivor_cohorts[cohort_id] = survivors

    snapshot = AppSnapshot.model_validate_json((output_dir / "nowcaster-snapshot.json").read_text())
    snapshot_cohort_ids = {item.cohort_id for item in snapshot.ensemble_components}
    assert len(snapshot_cohort_ids) == 1
    snapshot_cohort_id = snapshot_cohort_ids.pop()
    published_survivors = survivor_cohorts[snapshot_cohort_id]
    assert {item.strategy_id for item in snapshot.ensemble_components} == published_survivors


def test_research_fails_closed_on_prepopulated_or_post_cutoff_database(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "contaminated.duckdb"
    database = Database.from_url(f"duckdb:///{database_path}")
    database.initialize()
    opened_at = datetime(2026, 8, 21, tzinfo=UTC)
    BarRepository(database).append(
        [
            MarketBar(
                provider="csv",
                feed="contaminant",
                symbol="BTCUSDT",
                interval=BarInterval.FIVE_MINUTES,
                open_timestamp=opened_at,
                close_timestamp=opened_at + timedelta(minutes=5),
                available_at=opened_at + timedelta(minutes=5),
                retrieved_at=opened_at + timedelta(minutes=5),
                open=100,
                high=101,
                low=99,
                close=100,
                volume=10,
                payload_hash=canonical_hash(["future contaminant"]),
            )
        ]
    )
    output_dir = tmp_path / "blocked"

    result = RUNNER.invoke(
        app,
        [
            "strategy",
            "research",
            "--project-root",
            str(project_root),
            "--profile",
            "ci",
            "--database-url",
            f"duckdb:///{database_path}",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code != 0
    assert "clean isolated database" in str(result.exception)
    assert not (output_dir / "research-summary.json").exists()


def test_live_research_defaults_to_fresh_database_inside_output_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = Path(__file__).resolve().parents[2]
    project_root = _project_copy(source_root, tmp_path / "project")
    output_dir = tmp_path / "isolated-output"
    cache_dir = tmp_path / "external-cache"
    cutoff = datetime(2017, 10, 16, 4, tzinfo=UTC)
    earliest = datetime(2017, 8, 17, 4, tzinfo=UTC)
    _seed_binance_cache(cache_dir, cutoff=cutoff, earliest=earliest, chunk_count=1)
    monkeypatch.setattr("src.research.full_history.httpx.Client", _NetworkForbiddenClient)
    monkeypatch.setattr("src.app_snapshot.builder.git_commit", lambda _root: "test-fixture")

    result = RUNNER.invoke(
        app,
        [
            "strategy",
            "research",
            "--project-root",
            str(project_root),
            "--profile",
            "live",
            "--output-dir",
            str(output_dir),
            "--cache-dir",
            str(cache_dir),
            "--cutoff",
            cutoff.isoformat().replace("+00:00", "Z"),
            "--max-chunks-per-scope",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (output_dir / "research.duckdb").exists()
    assert not (project_root / "data" / "nowcaster.duckdb").exists()


def test_live_research_attempts_every_enabled_crypto_interval_from_external_cache(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    cache_dir = tmp_path / "external-cache"
    output_dir = tmp_path / "live"
    cutoff = datetime(2017, 10, 16, 4, tzinfo=UTC)
    earliest = datetime(2017, 8, 17, 4, tzinfo=UTC)

    _seed_binance_cache(cache_dir, cutoff=cutoff, earliest=earliest, chunk_count=1)

    result = RUNNER.invoke(
        app,
        [
            "strategy",
            "research",
            "--project-root",
            str(project_root),
            "--profile",
            "live",
            "--database-url",
            f"duckdb:///{tmp_path / 'live.duckdb'}",
            "--output-dir",
            str(output_dir),
            "--cache-dir",
            str(cache_dir),
            "--cutoff",
            cutoff.isoformat().replace("+00:00", "Z"),
            "--max-chunks-per-scope",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    attempts = json.loads((output_dir / "research-summary.json").read_text())["attempted_coverage"]
    for symbol in ("BTCUSDT", "ETHUSDT"):
        scoped = [
            item
            for item in attempts
            if item["provider_symbol"] == symbol and item["interval"] == BarInterval.FOUR_HOURS.value
        ]
        assert scoped
        assert any(item["attempted_start"] == earliest.isoformat().replace("+00:00", "Z") for item in scoped)


def test_live_research_exhaustively_replays_earliest_to_cutoff_with_exact_gap_accounting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    cache_dir = tmp_path / "external-cache"
    cutoff = datetime(2017, 10, 16, 4, tzinfo=UTC)
    earliest = datetime(2017, 8, 17, 4, tzinfo=UTC)
    _seed_binance_cache(cache_dir, cutoff=cutoff, earliest=earliest, chunk_count=2)
    monkeypatch.setattr("src.research.full_history.httpx.Client", _NetworkForbiddenClient)

    summaries: list[bytes] = []
    for name in ("first", "replay"):
        output_dir = tmp_path / name
        result = RUNNER.invoke(
            app,
            [
                "strategy",
                "research",
                "--project-root",
                str(project_root),
                "--profile",
                "live",
                "--database-url",
                f"duckdb:///{tmp_path / f'{name}.duckdb'}",
                "--output-dir",
                str(output_dir),
                "--cache-dir",
                str(cache_dir),
                "--cutoff",
                cutoff.isoformat().replace("+00:00", "Z"),
            ],
        )
        assert result.exit_code == 0, result.output
        summaries.append((output_dir / "research-summary.json").read_bytes())

    assert summaries[0] == summaries[1]
    summary = json.loads(summaries[0])
    attempts = summary["attempted_coverage"]
    assert len(attempts) == 16
    assert not any("diagnostic chunk limit" in item["reason"] for item in attempts)
    assert {item["attempted_end"] for item in attempts if item["attempted_start"]} >= {
        cutoff.isoformat().replace("+00:00", "Z")
    }
    assert summary["data_quality"]["coverage_gap_count"] == 16
    assert summary["data_quality"]["rows"] == 16
    for interval in (
        BarInterval.FIVE_MINUTES,
        BarInterval.FIFTEEN_MINUTES,
        BarInterval.ONE_HOUR,
        BarInterval.FOUR_HOURS,
    ):
        expected = int(timedelta(days=30) / INTERVAL_DURATION[interval]) - 1
        matching = [item for item in attempts if item["interval"] == interval.value]
        assert len(matching) == 4
        assert {item["reason"] for item in matching} == {f"data unavailable: {expected} requested bars remain missing"}
