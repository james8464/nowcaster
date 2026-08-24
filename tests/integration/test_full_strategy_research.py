from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from src.app_snapshot.models import AppSnapshot
from src.cli import app
from src.config.settings import Settings
from src.ingestion.bars import INTERVAL_DURATION
from src.strategies.types import BarInterval

RUNNER = CliRunner()


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
    assert snapshot.schema_version == 2
    assert len(snapshot.strategies) == summary["snapshot_counts"]["strategies"]
    assert len(snapshot.ensemble_components) == summary["snapshot_counts"]["ensemble_components"]
    assert summary["semantic_snapshot_hash"] in report
    assert "Historical evidence is not live proof" in report
    assert "does not promise profit" in report
    assert summary["data_quality"]["leakage_checks_passed"] is True
    assert summary["data_quality"]["duplicate_logical_bars"] == 0


def test_live_research_attempts_every_enabled_crypto_interval_from_external_cache(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    cache_dir = tmp_path / "external-cache"
    output_dir = tmp_path / "live"
    cutoff = datetime(2017, 10, 16, 4, tzinfo=UTC)
    earliest = datetime(2017, 8, 17, 4, tzinfo=UTC)

    for symbol in ("BTCUSDT", "ETHUSDT"):
        for interval in (
            BarInterval.FIVE_MINUTES,
            BarInterval.FIFTEEN_MINUTES,
            BarInterval.ONE_HOUR,
            BarInterval.FOUR_HOURS,
        ):
            duration = INTERVAL_DURATION[interval]
            raw_bar = [
                int(earliest.timestamp() * 1_000),
                "100",
                "102",
                "99",
                "101",
                "10",
                int((earliest + duration).timestamp() * 1_000) - 1,
                "1000",
                10,
                "5",
                "500",
                "0",
            ]
            payload = json.dumps([raw_bar], separators=(",", ":")).encode()
            parent = cache_dir / "binance" / "spot" / symbol / interval.value
            probe = parent / f"{cutoff.strftime('earliest-through-%Y%m%dT%H%M%SZ')}.json"
            page_end = earliest + timedelta(days=30)
            page = parent / (f"{int(earliest.timestamp() * 1_000)}-{int(page_end.timestamp() * 1_000)}-1000.json")
            for path in (probe, page):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                path.with_suffix(".sha256").write_text(hashlib.sha256(payload).hexdigest() + "\n")

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
