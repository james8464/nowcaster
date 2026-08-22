from __future__ import annotations

from src.app_snapshot.builder import build_app_snapshot


def test_demo_populates_native_equity_crypto_and_backtest_sections(demo_database) -> None:
    settings, database = demo_database
    snapshot = build_app_snapshot(database, settings)
    assert {item.asset_class for item in snapshot.instruments} == {"equity", "crypto"}
    assert {item.asset_class for item in snapshot.backtests} == {"equity", "crypto"}
    assert all(item.readiness in {"decision_ready", "research_only", "not_ready"} for item in snapshot.backtests)
    assert all(signal.posture in {"long_research", "short_research", "abstain"} for signal in snapshot.signals)
    crypto_signals = [signal for signal in snapshot.signals if signal.asset_class == "crypto"]
    assert any(signal.calibrated_probability is not None for signal in crypto_signals)
    assert snapshot.overview.instrument_count == len(snapshot.instruments)


def test_demo_exports_validated_native_snapshot(demo_database) -> None:
    settings, _ = demo_database
    path = settings.project_root / "data" / "app" / "nowcaster-snapshot.json"
    assert path.exists()
