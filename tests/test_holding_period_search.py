from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.research.holding_period_search import (
    _select_candidate,
    discovery_source_hash,
    research_runtime_fingerprint,
    search_scope,
    validate_discovery,
)
from src.strategies.library import StrategyContext
from src.strategies.registry import StrategyRegistry
from src.strategies.types import BarInterval, StrategyFamily, StrategySpec

STRATEGY_IDS = (
    "bollinger_keltner_squeeze",
    "macd_histogram_trend",
    "volatility_scaled_trend",
)


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class _FakeArchive:
    def __init__(self, _client, *, cache_dir):
        self.cache_dir = cache_dir

    def fetch(self, *, symbol, interval, start, end):
        bars = _bars()
        bars["symbol"] = symbol
        return SimpleNamespace(
            bars=bars,
            manifest=({"archive_name": f"{symbol}.zip", "sha256": symbol.lower()},),
            unavailable=(),
        )


def _run_fake_search(script, monkeypatch, tmp_path, root):
    monkeypatch.setattr(script.httpx, "Client", _FakeClient)
    monkeypatch.setattr(script, "BinancePublicArchive", _FakeArchive)
    return script.run_search(
        root=root,
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "discovery",
        start=datetime(2025, 1, 1, tzinfo=UTC),
        end=datetime(2025, 2, 1, tzinfo=UTC),
    )


def _bars(*, periods: int = 120, future_drop: bool = False) -> pd.DataFrame:
    opened = pd.date_range("2025-01-01T00:00:00Z", periods=periods, freq="1h")
    close = pd.Series(100.0, index=range(periods))
    if future_drop:
        close.iloc[int(periods * 0.75) :] = 95.0
    return pd.DataFrame(
        {
            "provider": "binance",
            "feed": "spot",
            "symbol": "BTCUSDT",
            "interval": "1h",
            "open_timestamp": opened,
            "close_timestamp": opened + pd.Timedelta(hours=1),
            "available_at": opened + pd.Timedelta(hours=1),
            "revision": 1,
            "finalized": True,
            "open": close,
            "high": close + 0.05,
            "low": close - 0.05,
            "close": close,
            "volume": 1_000.0,
            "atr": 0.2,
        }
    )


def _registry(calls: dict[str, int] | None = None) -> StrategyRegistry:
    registry = StrategyRegistry()

    def generate(spec: StrategySpec, bars: pd.DataFrame, _context: StrategyContext) -> pd.DataFrame:
        if calls is not None:
            calls[spec.strategy_id] = calls.get(spec.strategy_id, 0) + 1
        return pd.DataFrame(
            {
                "decision_timestamp": bars["close_timestamp"],
                "data_through": bars["close_timestamp"],
                "signal": 1,
                "strength": 1.0,
                "reason": "causal fixed test signal",
            }
        )

    families = (StrategyFamily.VOLATILITY_VOLUME, StrategyFamily.TREND, StrategyFamily.VOLATILITY_VOLUME)
    for strategy_id, family in zip(STRATEGY_IDS, families, strict=True):
        registry.register(
            StrategySpec(
                strategy_id=strategy_id,
                family=family,
                version="1",
                intervals=(BarInterval.ONE_HOUR,),
                warmup_bars=2,
                parameters={},
            ),
            generate,
        )
    return registry


def test_search_scope_predeclares_twelve_stable_non_promotable_configurations() -> None:
    first = search_scope(_bars(), symbol="BTCUSDT", registry=_registry())
    second = search_scope(_bars(), symbol="BTCUSDT", registry=_registry())

    assert len(first["configurations"]) == 12
    assert len({row["candidate_id"] for row in first["configurations"]}) == 12
    assert [row["candidate_id"] for row in second["configurations"]] == [
        row["candidate_id"] for row in first["configurations"]
    ]
    assert {row["strategy_id"] for row in first["configurations"]} == set(STRATEGY_IDS)
    assert {(row["stop_atr"], row["target_atr"], row["maximum_bars"]) for row in first["configurations"]} == {
        (1, 1.5, 6),
        (1, 1.5, 12),
        (2, 3.0, 6),
        (2, 3.0, 12),
    }
    assert first["selected_candidate"]["screen_passed"] is False
    assert first["promotable"] is False


def test_selection_uses_worst_fold_before_full_sample() -> None:
    weak_worst_fold = {
        "candidate_id": "a",
        "full": {"mean_stressed_return": 0.20},
        "folds": [{"mean_stressed_return": value, "trades": 30} for value in (-0.05, 0.3, 0.3, 0.3)],
    }
    strong_worst_fold = {
        "candidate_id": "b",
        "full": {"mean_stressed_return": 0.01},
        "folds": [{"mean_stressed_return": 0.001, "trades": 30}] * 4,
    }

    assert _select_candidate([weak_worst_fold, strong_worst_fold])["candidate_id"] == "b"


def test_scope_precomputes_each_causal_generator_once_and_future_changes_do_not_repaint_prefix() -> None:
    calls: dict[str, int] = {}
    original = search_scope(_bars(), symbol="BTCUSDT", registry=_registry(calls))
    changed = search_scope(_bars(future_drop=True), symbol="BTCUSDT", registry=_registry())

    assert calls == {strategy_id: 1 for strategy_id in STRATEGY_IDS}
    assert [row["signal_prefix_hash"] for row in changed["configurations"]] == [
        row["signal_prefix_hash"] for row in original["configurations"]
    ]


def test_minimum_target_distance_excludes_signals_and_costs_are_explicit() -> None:
    bars = _bars()
    bars["atr"] = 100.0  # Caller-provided risk must not bypass the causal internal calculation.
    result = search_scope(bars, symbol="BTCUSDT", registry=_registry())

    assert all(row["diagnostics"]["minimum_target_distance_excluded"] == 120 for row in result["configurations"])
    assert all(row["full"]["trades"] == 0 for row in result["configurations"])
    assert result["assumptions"]["round_trip_cost_bps"] == 34
    assert result["assumptions"]["stressed_round_trip_cost_bps"] == 68
    assert result["assumptions"]["minimum_target_distance_bps"] == 68


def test_actual_configured_generators_do_not_repaint_an_earlier_prefix() -> None:
    from pathlib import Path

    from scripts.search_holding_periods import _research_registry

    root = Path(__file__).resolve().parents[1]
    original = search_scope(_bars(periods=180), symbol="BTCUSDT", registry=_research_registry(root))
    changed = search_scope(_bars(periods=180, future_drop=True), symbol="BTCUSDT", registry=_research_registry(root))

    assert [row["signal_prefix_hash"] for row in changed["configurations"]] == [
        row["signal_prefix_hash"] for row in original["configurations"]
    ]


def test_run_search_writes_discovery_and_beginner_report(monkeypatch, tmp_path) -> None:
    from scripts import search_holding_periods as script

    root = Path(__file__).resolve().parents[1]
    output_dir = tmp_path / "discovery"

    result = _run_fake_search(script, monkeypatch, tmp_path, root)

    persisted = json.loads((output_dir / "discovery.json").read_text())
    assert len(result["candidates"]) == 2
    assert len(result["trials"]) == 24
    assert persisted == result
    assert persisted["promotable"] is False
    assert persisted["chronology"]["folds"] == 4
    assert persisted["end_exclusive"] == "2025-02-01T00:00:00+00:00"
    assert persisted["archive_manifest_hash"]
    assert persisted["source_hash"] == discovery_source_hash(root)
    assert "not promotable" in (output_dir / "report.md").read_text().lower()
    validate_discovery(persisted, root=root)


def test_discovery_validator_rejects_incomplete_or_tampered_search(monkeypatch, tmp_path) -> None:
    from scripts import search_holding_periods as script

    root = Path(__file__).resolve().parents[1]
    valid = _run_fake_search(script, monkeypatch, tmp_path, root)

    candidate_only = deepcopy(valid)
    candidate_only["trials"] = deepcopy(candidate_only["candidates"])
    with pytest.raises(ValueError, match="24"):
        validate_discovery(candidate_only, root=root)

    changed_source = deepcopy(valid)
    changed_source["source_hash"] = "0" * 64
    with pytest.raises(ValueError, match="source"):
        validate_discovery(changed_source, root=root)

    dropped_losing_trial = deepcopy(valid)
    dropped_losing_trial["trials"].pop()
    with pytest.raises(ValueError, match="24"):
        validate_discovery(dropped_losing_trial, root=root)

    flipped_flag = deepcopy(valid)
    flipped_flag["trials"][0]["screen_passed"] = not flipped_flag["trials"][0]["screen_passed"]
    with pytest.raises(ValueError, match="screen"):
        validate_discovery(flipped_flag, root=root)

    wrong_selection = deepcopy(valid)
    symbol = wrong_selection["candidates"][0]["symbol"]
    alternative = next(
        row
        for row in wrong_selection["trials"]
        if row["symbol"] == symbol and row["candidate_id"] != wrong_selection["candidates"][0]["candidate_id"]
    )
    wrong_selection["candidates"][0] = deepcopy(alternative)
    with pytest.raises(ValueError, match="selected"):
        validate_discovery(wrong_selection, root=root)


def test_discovery_source_hash_includes_search_inputs_but_excludes_exact_prospective_modules(tmp_path) -> None:
    files = {
        "src/package.py": "search logic",
        "src/research/prospective.py": "ledger one",
        "src/research/prospective_types.py": "ledger two",
        "src/research/prospective_statistics.py": "ledger three",
        "src/research/prospective_runtime.py": "ledger four",
        "config/strategies.yaml": "strategies: []",
        "pyproject.toml": "[project]",
        "scripts/search_holding_periods.py": "search orchestration",
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    original = discovery_source_hash(tmp_path)

    (tmp_path / "src/research/prospective_runtime.py").write_text("unrelated ledger fix")
    assert discovery_source_hash(tmp_path) == original

    (tmp_path / "config/strategies.yaml").write_text("strategies: [changed]")
    assert discovery_source_hash(tmp_path) != original


def test_discovery_source_hash_binds_explicit_runtime_fingerprint(monkeypatch, tmp_path) -> None:
    from src.research import holding_period_search as module

    files = {
        "src/package.py": "search logic",
        "config/strategies.yaml": "strategies: []",
        "pyproject.toml": "[project]",
        "scripts/search_holding_periods.py": "search orchestration",
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    fingerprint = research_runtime_fingerprint()
    original = discovery_source_hash(tmp_path)

    monkeypatch.setattr(module, "research_runtime_fingerprint", lambda: {**fingerprint, "python": "changed"})

    assert discovery_source_hash(tmp_path) != original
