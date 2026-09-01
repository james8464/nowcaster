from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.models.trade_outcomes import BarrierPolicy
from src.research.opportunity_audit import OpportunityResearchProtocol, audit_opportunity_scope, gap_safe_atr
from src.strategies.library import StrategyContext
from src.strategies.registry import StrategyRegistry
from src.strategies.types import BarInterval, StrategyFamily, StrategySpec


def test_executable_audit_policy_tracks_the_live_monitor_expiry_contract() -> None:
    from scripts.audit_day_trading_opportunities import _barrier_policy
    from src.live_monitor.levels import DEFAULT_TRADE_LEVEL_POLICY

    policy = _barrier_policy()

    assert policy.maximum_bars == DEFAULT_TRADE_LEVEL_POLICY.expires_after_bars == 3
    assert policy.stop_r == float(DEFAULT_TRADE_LEVEL_POLICY.atr_multiplier)
    assert policy.target_r == float(DEFAULT_TRADE_LEVEL_POLICY.minimum_target_2_r)


def _bars() -> pd.DataFrame:
    opens = pd.date_range("2026-01-01T00:00:00Z", periods=90, freq="5min")
    return pd.DataFrame(
        {
            "provider": "binance",
            "feed": "spot",
            "symbol": "BTCUSDT",
            "interval": "5m",
            "open_timestamp": opens,
            "close_timestamp": opens + pd.Timedelta(minutes=5),
            "available_at": opens + pd.Timedelta(minutes=5),
            "revision": 1,
            "finalized": True,
            "open": 100.0,
            "high": 101.0,
            "low": 99.8,
            "close": 100.5,
            "volume": 1_000.0,
            "atr": 0.5,
        }
    )


def _generator(direction: int):
    def generate(_spec: StrategySpec, bars: pd.DataFrame, _context: StrategyContext) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "decision_timestamp": bars["close_timestamp"].copy(),
                "data_through": bars["close_timestamp"].copy(),
                "signal": direction,
                "strength": 1.0,
                "reason": "fixed causal fixture",
            }
        )

    return generate


def _registry(direction: int = 1) -> StrategyRegistry:
    registry = StrategyRegistry()
    for strategy_id, family in (("trend", StrategyFamily.TREND), ("volume", StrategyFamily.VOLATILITY_VOLUME)):
        registry.register(
            StrategySpec(
                strategy_id=strategy_id,
                family=family,
                version="1",
                intervals=(BarInterval.FIVE_MINUTES,),
                warmup_bars=1,
                parameters={},
            ),
            _generator(direction),
        )
    return registry


def _alternating_registry() -> StrategyRegistry:
    registry = StrategyRegistry()

    def generate(_spec: StrategySpec, bars: pd.DataFrame, _context: StrategyContext) -> pd.DataFrame:
        signal = pd.Series([1 if index % 2 == 0 else -1 for index in range(len(bars))])
        return pd.DataFrame(
            {
                "decision_timestamp": bars["close_timestamp"].copy(),
                "data_through": bars["close_timestamp"].copy(),
                "signal": signal,
                "strength": 1.0,
                "reason": "alternating directional fixture",
            }
        )

    for strategy_id, family in (("trend", StrategyFamily.TREND), ("volume", StrategyFamily.VOLATILITY_VOLUME)):
        registry.register(
            StrategySpec(
                strategy_id=strategy_id,
                family=family,
                version="1",
                intervals=(BarInterval.FIVE_MINUTES,),
                warmup_bars=1,
                parameters={},
            ),
            generate,
        )
    return registry


def _protocol() -> OpportunityResearchProtocol:
    return OpportunityResearchProtocol(
        development_fraction=0.6,
        validation_fraction=0.2,
        minimum_development_opportunities=10,
        minimum_validation_opportunities=5,
        minimum_bootstrap_probability=0.95,
        bootstrap_samples=100,
        consensus_minimum_breadth=2,
        consensus_minimum_families=2,
        consensus_vote_threshold=0.8,
    )


def test_scope_selects_components_without_using_holdout_and_keeps_result_non_promotable() -> None:
    policy = BarrierPolicy(target_r=1, stop_r=1, maximum_bars=2, round_trip_cost_bps=10)
    first = audit_opportunity_scope(_bars(), _registry(), policy, protocol=_protocol())
    damaged = _bars()
    damaged.loc[damaged.index >= 72, ["high", "low", "close"]] = [100.2, 99.0, 99.5]
    second = audit_opportunity_scope(damaged, _registry(), policy, protocol=_protocol())

    assert first["selected_components"] == ["trend:long", "volume:long"]
    assert second["selected_components"] == first["selected_components"]
    assert second["selection_hash"] == first["selection_hash"]
    assert first["candidate_ensemble"]["holdout"]["opportunities"] > 0
    assert first["decision"]["status"] == "retrospective_candidate_found_forward_test_required"
    assert second["decision"]["status"] == "no_reliable_strategy_found"
    assert first["evidence_tier"] == "retrospective_archive_only"
    assert first["eligible_for_live_promotion"] is False


def test_scope_tests_long_and_short_hypotheses_independently() -> None:
    result = audit_opportunity_scope(
        _bars(),
        _alternating_registry(),
        BarrierPolicy(target_r=1, stop_r=1, maximum_bars=2, round_trip_cost_bps=10),
        protocol=_protocol(),
    )

    assert result["selected_components"] == ["trend:long", "volume:long"]
    directional = {item["strategy_id"]: item for item in result["strategies"]}
    assert directional["trend:long"]["passed_retrospective_gate"] is True
    assert directional["trend:short"]["passed_retrospective_gate"] is False
    assert result["multiplicity"]["directional_hypotheses"] == 4
    assert result["multiplicity"]["development_and_validation_tests"] == 8
    assert result["multiplicity"]["familywise_bootstrap_probability_threshold"] > 0.99


def test_scope_rejects_losing_rules_and_abstains_instead_of_forcing_opportunities() -> None:
    result = audit_opportunity_scope(
        _bars(),
        _registry(direction=-1),
        BarrierPolicy(target_r=1, stop_r=1, maximum_bars=2, round_trip_cost_bps=10),
        protocol=_protocol(),
    )

    assert result["selected_components"] == []
    assert result["candidate_ensemble"] is None
    assert result["decision"]["status"] == "no_reliable_strategy_found"
    assert "no component passed" in result["decision"]["reason"]


def test_scope_resets_indicator_warmup_after_every_market_data_gap() -> None:
    bars = _bars().iloc[:20].copy()
    for name in ("open_timestamp", "close_timestamp", "available_at"):
        bars.loc[bars.index >= 10, name] += pd.Timedelta(minutes=5)
    registry = StrategyRegistry()

    def warmup_generator(spec: StrategySpec, frame: pd.DataFrame, _context: StrategyContext) -> pd.DataFrame:
        ready = pd.Series(range(len(frame)), index=frame.index) + 1 >= spec.warmup_bars
        return pd.DataFrame(
            {
                "decision_timestamp": frame["close_timestamp"].to_numpy(),
                "data_through": frame["close_timestamp"].to_numpy(),
                "signal": ready.astype(int).to_numpy(),
                "strength": ready.astype(float).to_numpy(),
                "reason": "segment warmup fixture",
            }
        )

    registry.register(
        StrategySpec(
            strategy_id="warmup",
            family=StrategyFamily.TREND,
            version="1",
            intervals=(BarInterval.FIVE_MINUTES,),
            warmup_bars=3,
            parameters={},
        ),
        warmup_generator,
    )

    result = audit_opportunity_scope(
        bars,
        registry,
        BarrierPolicy(target_r=1, stop_r=1, maximum_bars=2),
        protocol=OpportunityResearchProtocol(
            development_fraction=0.5,
            validation_fraction=0.25,
            minimum_development_opportunities=1,
            minimum_validation_opportunities=1,
            minimum_bootstrap_probability=0.51,
            bootstrap_samples=20,
            consensus_minimum_breadth=1,
            consensus_minimum_families=1,
            consensus_vote_threshold=0.5,
        ),
    )

    assert result["strategies"][0]["diagnostics"]["signals_considered"] == 16


def test_gap_safe_atr_restarts_risk_warmup_after_every_market_data_gap() -> None:
    bars = _bars().iloc[:30].copy()
    for name in ("open_timestamp", "close_timestamp", "available_at"):
        bars.loc[bars.index >= 15, name] += pd.Timedelta(minutes=5)
    bars.loc[bars.index < 15, ["high", "low", "close"]] = [120.0, 80.0, 100.0]

    result = gap_safe_atr(bars, period=14)

    assert result.iloc[14] == 40.0
    assert result.iloc[15:28].isna().all()
    assert result.iloc[28] == pytest.approx(1.2)


def test_audit_report_does_not_present_unqualified_diagnostics_as_selected_rules() -> None:
    from scripts.audit_day_trading_opportunities import _report

    report = _report(
        {
            "start": "2017-08-17T00:00:00+00:00",
            "end": "2026-09-01T00:00:00+00:00",
            "overall_decision": "no_reliable_strategy_found",
            "verified_archive_files": 2,
            "invalid_archive_boundary_rows": 1,
            "archive_manifest_hash": "a" * 64,
            "scopes": [
                {
                    "symbol": "BTCUSDT",
                    "interval": "5m",
                    "data_quality": {"bars": 100, "missing_bars": 2},
                    "strategies": [{"strategy_id": "raw_a"}, {"strategy_id": "raw_b"}],
                    "selected_components": [],
                    "diagnostic_all_component_consensus": {
                        "holdout": {
                            "opportunities": 12,
                            "mean_gross_return": 0.0,
                            "mean_net_return": -0.0034,
                        }
                    },
                    "decision": {"status": "no_reliable_strategy_found"},
                }
            ],
        }
    )

    assert "| BTCUSDT | 5m | 100 | 2 | 2 | 0 | 12 | -34.00 bps | no_reliable_strategy_found |" in report
    assert "not a candidate ensemble" in report
    assert "before costs ranged from 0.00 to 0.00 bps" in report


def test_committed_opportunity_audit_result_is_locked_and_arithmetically_coherent() -> None:
    path = Path(__file__).parents[2] / "data" / "research" / "day-trading-opportunity-audit-2026-09-01.json"
    result = json.loads(path.read_text())
    scopes = result["scopes"]

    assert result["evidence_tier"] == "retrospective_archive_only"
    assert result["eligible_for_live_promotion"] is False
    assert result["overall_decision"] == "no_reliable_strategy_found"
    assert result["live_money_status"] == "locked"
    assert all(item["rules_selected"] == 0 for item in scopes)
    assert sum(item["bars"] for item in scopes) == result["total_candle_rows_across_scopes"]
    assert sum(item["invalid_boundary_rows"] for item in scopes) == result["invalid_archive_boundary_rows"]
    assert sum(item["diagnostic_holdout_setups"] for item in scopes) == result["total_diagnostic_holdout_setups"]
    assert sum(item["directional_hypotheses_tested"] for item in scopes) == result["total_directional_hypotheses"]
    for item in scopes:
        assert item["diagnostic_holdout_mean_net_return"] == pytest.approx(
            item["diagnostic_holdout_mean_gross_return"] - result["policy"]["round_trip_cost_bps"] / 10_000
        )
