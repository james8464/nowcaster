from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

import src.contextual.service as contextual_service_module
from src.app_snapshot.builder import build_app_snapshot
from src.config.settings import Settings
from src.contextual.repository import ContextualRepository
from src.contextual.service import ContextualResearchService, ContextualRunRequest
from src.database.engine import Database
from src.ingestion.bars import MarketBar
from src.learning.search import global_learning_trial_count
from src.live_monitor.evidence import SealedCohort, load_contextual_live_evidence
from src.live_monitor.repository import LiveMonitorRepository
from src.live_monitor.types import MarketDepth, MarketQuote, MarketStatusEvent
from src.strategies.datasets import BarRepository
from src.strategies.types import BarInterval, StrategyMode, canonical_hash

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STRATEGIES = (
    "ema_adx_trend",
    "macd_histogram_trend",
    "rsi_reversal",
    "bollinger_keltner_squeeze",
)
DATASET_HASH = "d" * 64
PROTOCOL_HASH = "a" * 64


def seed_contextual_market(database: Database, *, count: int = 160) -> datetime:
    versions = {
        spec.strategy_id: spec.deterministic_version
        for spec in Settings.load(PROJECT_ROOT, mode="test").strategies.enabled
    }
    start = datetime(2026, 8, 1, tzinfo=UTC)
    bars: list[MarketBar] = []
    price = 100.0
    for index in range(count):
        opened_at = start + timedelta(minutes=5 * index)
        closed_at = opened_at + timedelta(minutes=5)
        next_price = price * (1.0 + 0.0004 + math.sin(index / 11) * 0.0002)
        bars.append(
            MarketBar(
                provider="binance",
                feed="spot",
                symbol="BTCUSDT",
                interval=BarInterval.FIVE_MINUTES,
                open_timestamp=opened_at,
                close_timestamp=closed_at,
                available_at=closed_at,
                open=price,
                high=max(price, next_price) * 1.001,
                low=min(price, next_price) * 0.999,
                close=next_price,
                volume=200_000 + index * 100,
                quote_volume=(200_000 + index * 100) * next_price,
                payload_hash=canonical_hash(["contextual-service", index, next_price]),
            )
        )
        price = next_price
    BarRepository(database).append(bars)

    repository = ContextualRepository(database, clock=lambda: bars[-1].available_at)
    outcomes = []
    for strategy_index, strategy_id in enumerate(STRATEGIES):
        for index, bar in enumerate(bars[20:140]):
            gross = 0.0015 + 0.0002 * math.sin(index / (5 + strategy_index))
            cost = 0.0001
            outcomes.append(
                {
                    "dataset_hash": DATASET_HASH,
                    "protocol_hash": PROTOCOL_HASH,
                    "code_hash": "code-v1",
                    "config_hash": "config-v1",
                    "source_decision_hash": canonical_hash([strategy_id, index]),
                    "provider": "binance",
                    "feed": "spot",
                    "venue": "Binance",
                    "product": "spot",
                    "asset_class": "crypto",
                    "profile": "crypto_major_spot",
                    "symbol": "BTCUSDT",
                    "interval": "5m",
                    "direction": "long",
                    "mode": "paper",
                    "strategy_id": strategy_id,
                    "strategy_version": versions[strategy_id],
                    "decision_timestamp": bar.open_timestamp,
                    "outcome_available_at": bar.available_at,
                    "gross_return": gross,
                    "modeled_cost": cost,
                    "net_return": gross - cost,
                    "regime_probabilities": {
                        "trend_normal": 0.55,
                        "trend_elevated_volatility": 0.15,
                        "range_liquid": 0.25,
                        "stressed_or_illiquid": 0.05,
                    },
                }
            )
    repository.append_outcome_rows(outcomes)
    return bars[-1].available_at


def contextual_service(tmp_path: Path) -> tuple[ContextualResearchService, Database, datetime]:
    database = Database.from_url(f"duckdb:///{tmp_path / 'contextual-service.duckdb'}")
    database.initialize()
    as_of = seed_contextual_market(database)
    settings = Settings.load(PROJECT_ROOT, mode="test").model_copy(
        update={"database_url": f"duckdb:///{tmp_path / 'contextual-service.duckdb'}"}
    )
    return ContextualResearchService(database, settings), database, as_of


def publish_contextual_cohort(service, database, effective_at):
    index = database.frame("select outcome_id, content_hash from contextual_outcomes order by outcome_id")
    members = [
        {"strategy_id": key, "strategy_version": service._specs[key].deterministic_version} for key in STRATEGIES
    ]
    metrics = {
        "cohort_id": "c" * 64,
        "cohort_effective_at": effective_at.isoformat(),
        "cohort_members": members,
        "contextual_outcome_count": len(index),
        "contextual_outcome_index_hash": canonical_hash(index.to_dict("records")),
        "contextual_protocol_hash": PROTOCOL_HASH,
    }
    database.upsert(
        "strategy_runs",
        [
            {
                "strategy_run_id": canonical_hash([member, effective_at]),
                "dataset_hash": DATASET_HASH,
                **member,
                "family": service._specs[member["strategy_id"]].family.value,
                "symbol": "BTCUSDT",
                "interval": "5m",
                "mode": "paper",
                "run_timestamp": effective_at,
                "started_at": effective_at,
                "ended_at": effective_at,
                "parameters": {},
                "status": "evaluated",
                "metrics": metrics,
                "source": "test",
                "source_version": "1",
                "created_at": effective_at,
            }
            for member in members
        ],
    )


def test_contextual_research_rejects_a_cohort_published_after_the_decision(tmp_path):
    service, database, as_of = contextual_service(tmp_path)
    publish_contextual_cohort(service, database, as_of + timedelta(days=1))
    request = ContextualRunRequest(("BTCUSDT",), "binance", "spot", BarInterval.FIVE_MINUTES, StrategyMode.PAPER, as_of)
    with pytest.raises(ValueError, match="cohort"):
        service.evaluate_contexts(request)


def test_published_cohort_can_be_replayed_without_publishing_historical_live_evidence(tmp_path):
    service, database, as_of = contextual_service(tmp_path)
    publish_contextual_cohort(service, database, as_of)
    request = ContextualRunRequest(("BTCUSDT",), "binance", "spot", BarInterval.FIVE_MINUTES, StrategyMode.PAPER, as_of)
    result = service.backtest_portfolio(request)
    assert result.observations == 80  # Every remaining timestamp after the 40-timestamp warm-up.
    assert result.status == "all_cash"  # No historical order books: never fabricate tradability.
    assert database.scalar("select count(*) from contextual_weights") == 0
    run = database.frame("select * from backtest_runs").iloc[0]
    assert run["robustness"]["replay_only"] is True
    assert run["robustness"]["sealed_rows"] is False
    assert run["final_test_metrics"]["observations"] > 0


def seed_observed_liquidity(database: Database, as_of: datetime, *, symbol: str = "BTCUSDT") -> None:
    repository = LiveMonitorRepository(database, clock=lambda: as_of)
    last = Decimal(
        str(
            database.scalar(
                "select close from market_bars where symbol = :symbol order by close_timestamp desc limit 1",
                {"symbol": symbol},
            )
        )
    )
    midpoint = last.quantize(Decimal("0.01"))
    identity = {"provider": "binance", "feed": "spot", "symbol": symbol, "provider_time": as_of, "received_at": as_of}
    repository.record_market_event(
        "contextual-test",
        MarketQuote(
            **identity,
            bid=midpoint - Decimal("0.01"),
            ask=midpoint + Decimal("0.01"),
            last=midpoint,
            tick_size=Decimal("0.01"),
        ),
    )
    repository.record_market_event(
        "contextual-test",
        MarketDepth(
            **identity,
            first_update_id=10,
            final_update_id=10,
            snapshot_verified=True,
            bids=(
                {"price": midpoint - Decimal("0.01"), "size": 2},
                {"price": midpoint - Decimal("0.02"), "size": 200_000},
            ),
            asks=(
                {"price": midpoint + Decimal("0.01"), "size": 2},
                {"price": midpoint + Decimal("0.02"), "size": 200_000},
            ),
        ),
    )
    repository.record_market_event(
        "contextual-test",
        MarketStatusEvent(
            **identity,
            kind="status",
            status="instrument_rules",
            details={
                "tradable": True,
                "filters": [
                    {"filterType": "LOT_SIZE", "minQty": "0.0001", "maxQty": "10000", "stepSize": "0.0001"},
                    {"filterType": "MIN_NOTIONAL", "minNotional": "10"},
                ],
            },
        ),
    )


@pytest.mark.parametrize("damage", [None, "stale", "tampered", "delta_only"])
def test_observed_liquidity_requires_fresh_authenticated_full_depth_and_models_impact(tmp_path, damage) -> None:
    database = Database.from_url(f"duckdb:///{tmp_path / 'liquidity.duckdb'}")
    database.initialize()
    as_of = seed_contextual_market(database, count=1_100)
    seed_observed_liquidity(database, as_of - timedelta(minutes=1) if damage == "stale" else as_of)
    if damage in {"tampered", "delta_only"}:
        row = database.frame("select * from live_market_events where event_type = 'depth'").iloc[0]
        payload = dict(row["payload"])
        payload["snapshot_verified"] = False
        with database.engine.begin() as connection:
            from src.database.schema import TABLES

            connection.execute(
                TABLES["live_market_events"]
                .update()
                .where(TABLES["live_market_events"].c.event_id == row["event_id"])
                .values(payload=payload, payload_hash=canonical_hash(payload) if damage == "delta_only" else "bad")
            )
    settings = Settings.load(PROJECT_ROOT, mode="test")
    service = ContextualResearchService(database, settings)
    result = service.screen_universe(
        ContextualRunRequest(
            symbols=("BTCUSDT",),
            provider="binance",
            feed="spot",
            interval=BarInterval.FIVE_MINUTES,
            mode=StrategyMode.PAPER,
            as_of=as_of,
        )
    )
    evidence = result.eligibility[0]
    if damage is None:
        assert evidence.state.value == "eligible", evidence.reasons
        assert evidence.estimated_price_impact_bps > 0
        assert evidence.participation_rate > 0
    else:
        assert evidence.state.value != "eligible"
        assert evidence.estimated_price_impact_bps is None


def test_evaluate_contexts_emits_ordered_stages_and_persists_cash_safe_result(
    tmp_path: Path,
) -> None:
    service, database, as_of = contextual_service(tmp_path)
    events = []
    request = ContextualRunRequest(
        symbols=("BTCUSDT",),
        provider="binance",
        feed="spot",
        interval=BarInterval.FIVE_MINUTES,
        mode=StrategyMode.PAPER,
        as_of=as_of,
    )

    result = service.evaluate_contexts(request, events.append)

    assert [event.stage for event in events] == [
        "eligibility",
        "regimes",
        "hierarchy",
        "covariance",
        "allocation",
        "portfolio",
    ]
    assert result.portfolio.cash_weight == 1.0
    assert result.portfolio.status == "all_cash"
    assert database.scalar("select count(*) from contextual_estimates") > 0
    assert database.scalar("select count(*) from contextual_weights") == len(STRATEGIES)
    assert database.scalar("select count(*) from portfolio_research_decisions") == 1


def test_contextual_replay_ignores_future_weights_and_does_not_consume_its_own_rerun(tmp_path) -> None:
    service, _, as_of = contextual_service(tmp_path)
    request = ContextualRunRequest(
        symbols=("BTCUSDT",),
        provider="binance",
        feed="spot",
        interval=BarInterval.FIVE_MINUTES,
        mode=StrategyMode.PAPER,
        as_of=as_of - timedelta(seconds=1),
    )
    first = service.evaluate_contexts(request)
    service.evaluate_contexts(replace(request, as_of=as_of))
    replay = service.evaluate_contexts(request)
    assert {key: value.allocation_id for key, value in first.allocations.items()} == {
        key: value.allocation_id for key, value in replay.allocations.items()
    }


def test_contextual_outcome_scalar_tampering_cannot_change_authenticated_fit(tmp_path) -> None:
    service, database, as_of = contextual_service(tmp_path)
    with database.engine.begin() as connection:
        connection.execute(text("update contextual_outcomes set net_return = 0.75"))
    with pytest.raises(ValueError, match="authentication"):
        service.evaluate_contexts(
            ContextualRunRequest(
                symbols=("BTCUSDT",),
                provider="binance",
                feed="spot",
                interval=BarInterval.FIVE_MINUTES,
                mode=StrategyMode.PAPER,
                as_of=as_of,
            )
        )


def test_prior_allocation_sql_time_cannot_disguise_a_future_weight(tmp_path):
    service, database, as_of = contextual_service(tmp_path)
    request = ContextualRunRequest(("BTCUSDT",), "binance", "spot", BarInterval.FIVE_MINUTES, StrategyMode.PAPER, as_of)
    service.evaluate_contexts(request)
    with database.engine.begin() as connection:
        connection.execute(
            text("update contextual_weights set effective_at = :at"), {"at": as_of - timedelta(seconds=2)}
        )
    with pytest.raises(ValueError, match="authentication"):
        service.evaluate_contexts(replace(request, as_of=as_of - timedelta(seconds=1)))


def prepared_live_context(tmp_path):
    from tests.unit.test_live_monitor_evidence import component

    service, database, as_of = contextual_service(tmp_path)
    publish_contextual_cohort(service, database, as_of)
    request = ContextualRunRequest(
        symbols=("BTCUSDT",),
        provider="binance",
        feed="spot",
        interval=BarInterval.FIVE_MINUTES,
        mode=StrategyMode.PAPER,
        as_of=as_of,
    )
    service.evaluate_contexts(request)
    live_cohort = SealedCohort.model_construct(
        cohort_id="c" * 64,
        provider="binance",
        feed="spot",
        dataset_hash=DATASET_HASH,
        symbol="BTCUSDT",
        interval="5m",
        mode="paper",
        cost_buffer_multiplier=Decimal("1"),
        components=tuple(
            component("macd_histogram_trend", "0.25").model_copy(
                update={
                    "spec": service._specs[key],
                    "strategy_version": service._specs[key].deterministic_version,
                }
            )
            for key in STRATEGIES
        ),
        contextual_protocol_hash=PROTOCOL_HASH,
        contextual_outcome_index_hash=database.frame("select metrics from strategy_runs limit 1").iloc[0]["metrics"][
            "contextual_outcome_index_hash"
        ],
    )
    return service, database, as_of, request, live_cohort


def test_live_loader_requires_one_authenticated_exact_contextual_cohort(tmp_path: Path) -> None:
    _, database, as_of, _, live_cohort = prepared_live_context(tmp_path)

    loaded = load_contextual_live_evidence(database, (live_cohort,), now=as_of)

    envelope = loaded[("binance", "spot", "BTCUSDT", "5m", "long")]
    assert envelope.context_hash
    assert envelope.drift_status == "stable"
    assert envelope.portfolio_selected is False

    with database.engine.begin() as connection:
        connection.execute(text("update contextual_weights set content_hash = 'tampered'"))
    assert load_contextual_live_evidence(database, (live_cohort,), now=as_of) == {}


@pytest.mark.parametrize("damage", ["mode", "version", "members", "partial_weights", "protocol", "cohort"])
def test_live_loader_cannot_attach_a_different_or_incomplete_strategy_cohort(tmp_path, damage):
    _, database, as_of, _, cohort = prepared_live_context(tmp_path)
    if damage == "mode":
        cohort = cohort.model_copy(update={"mode": "frozen"})
    elif damage == "version":
        cohort = cohort.model_copy(
            update={
                "components": (
                    cohort.components[0].model_copy(update={"strategy_version": "stale-version"}),
                    *cohort.components[1:],
                )
            }
        )
    elif damage == "members":
        cohort = cohort.model_copy(update={"components": cohort.components[:1]})
    elif damage == "partial_weights":
        with database.engine.begin() as connection:
            connection.execute(text("delete from contextual_weights where strategy_id = :key"), {"key": STRATEGIES[0]})
    elif damage == "protocol":
        cohort = cohort.model_copy(update={"contextual_protocol_hash": "b" * 64})
    else:
        cohort = cohort.model_copy(update={"cohort_id": "b" * 64})
    assert load_contextual_live_evidence(database, (cohort,), now=as_of) == {}


@pytest.mark.parametrize("delay", [1, 2])
def test_later_drift_quarantines_existing_weights_and_a_refresh_cannot_clear_it(tmp_path, delay):
    service, database, as_of, request, cohort = prepared_live_context(tmp_path)
    context_hash = database.scalar("select context_hash from contextual_weights limit 1")
    service.repository.append_drift_event(
        {
            "context_hash": context_hash,
            "effective_at": as_of + timedelta(seconds=1),
            "status": "confirmed",
            "reason": "forward_edge_deterioration",
        }
    )
    loaded = load_contextual_live_evidence(database, (cohort,), now=as_of + timedelta(seconds=2))
    assert loaded[("binance", "spot", "BTCUSDT", "5m", "long")].drift_status == "confirmed"
    service.evaluate_contexts(replace(request, as_of=as_of + timedelta(seconds=delay)))
    service.evaluate_contexts(replace(request, as_of=as_of + timedelta(seconds=delay)))
    refreshed = load_contextual_live_evidence(database, (cohort,), now=as_of + timedelta(seconds=3))
    assert refreshed[("binance", "spot", "BTCUSDT", "5m", "long")].drift_status == "confirmed"
    assert (
        database.scalar("select status from contextual_drift_events order by effective_at desc limit 1") == "confirmed"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "update asset_eligibility_evidence set state = 'eligible'",
        "update asset_eligibility_evidence set quality_score = 0.999",
        "update asset_eligibility_evidence set policy_hash = 'bad'",
        "update contextual_covariances set observations = 999999",
        "update contextual_weights set cash_weight = 0.999",
    ],
)
def test_live_loader_rejects_scalar_mirrors_that_disagree_with_authenticated_payload(tmp_path, mutation):
    _, database, as_of, _, cohort = prepared_live_context(tmp_path)
    with database.engine.begin() as connection:
        connection.execute(text(mutation))
    assert load_contextual_live_evidence(database, (cohort,), now=as_of) == {}


def test_full_contextual_pipeline_is_causal_and_explainable(tmp_path):
    database = Database.from_url(f"duckdb:///{tmp_path / 'contextual-acceptance.duckdb'}")
    database.initialize()
    as_of = seed_contextual_market(database, count=1_100)
    original_bars = database.frame("select * from market_bars order by open_timestamp")
    clone_bars = [
        MarketBar.model_validate(
            {
                **{key: row[key] for key in MarketBar.model_fields if key in row},
                "symbol": "ETHUSDT",
                "payload_hash": canonical_hash(["ETHUSDT", row["payload_hash"]]),
            }
        )
        for row in original_bars.to_dict("records")
    ]
    BarRepository(database).append(clone_bars)
    original_outcomes = database.frame("select evidence from contextual_outcomes")
    ContextualRepository(database, clock=lambda: as_of).append_outcome_rows(
        [
            {
                **payload,
                "symbol": "ETHUSDT",
                "source_decision_hash": canonical_hash(["ETHUSDT", payload["source_decision_hash"]]),
            }
            for payload in original_outcomes["evidence"]
        ]
    )
    for symbol in ("BTCUSDT", "ETHUSDT"):
        seed_observed_liquidity(database, as_of, symbol=symbol)
    service = ContextualResearchService(database, Settings.load(PROJECT_ROOT, mode="test"))
    request = ContextualRunRequest(
        ("BTCUSDT", "ETHUSDT"), "binance", "spot", BarInterval.FIVE_MINUTES, StrategyMode.PAPER, as_of
    )
    result = service.evaluate_contexts(request)
    assert all(item.state.value == "eligible" for item in result.screen.eligibility)
    assert len(result.portfolio.selected) == 1
    assert any("correlat" in reason for reasons in result.portfolio.exclusions.values() for reason in reasons)
    assert all(allocation.status == "allocated" for allocation in result.allocations.values())
    snapshot = build_app_snapshot(database, service.settings)
    projected = [item for item in snapshot.signals if item.context_hash]
    assert len(projected) == 2
    assert sum(item.portfolio_selected is True for item in projected) == 1
    assert all(item.contextual_evidence_hash and item.eligibility_state == "eligible" for item in projected)
    assert any(item.portfolio_conflicts for item in projected if not item.portfolio_selected)
    future = clone_bars[-1].model_copy(
        update={
            "open_timestamp": as_of,
            "close_timestamp": as_of + timedelta(minutes=5),
            "available_at": as_of + timedelta(minutes=5),
            "low": clone_bars[-1].close / 2,
            "close": clone_bars[-1].close / 2,
            "payload_hash": canonical_hash("future shock"),
        }
    )
    BarRepository(database).append([future])
    replay = service.evaluate_contexts(request)
    assert replay.evidence_hash == result.evidence_hash
    assert replay.portfolio.selection_id == result.portfolio.selection_id
    assert {key: value.allocation_id for key, value in replay.allocations.items()} == {
        key: value.allocation_id for key, value in result.allocations.items()
    }


def test_contextual_learning_reserves_all_trials_and_keeps_the_champion_shadow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, database, as_of = contextual_service(tmp_path)
    evaluator = contextual_service_module.evaluate_contextual_candidate

    def assert_reserved_before_evaluation(*args, **kwargs):
        assert database.scalar("select count(*) from contextual_learning_trials") == 4
        return evaluator(*args, **kwargs)

    monkeypatch.setattr(contextual_service_module, "evaluate_contextual_candidate", assert_reserved_before_evaluation)
    request = ContextualRunRequest(
        symbols=("BTCUSDT",),
        provider="binance",
        feed="spot",
        interval=BarInterval.FIVE_MINUTES,
        mode=StrategyMode.PAPER,
        as_of=as_of,
    )

    result = service.learn_contextual(request, evaluation_budget=4, seed=42)

    assert result.status == "shadow"
    assert result.trial_count == 4
    assert result.shadow_cohort_hash
    assert database.scalar("select count(*) from contextual_learning_trials") == 4
    assert database.scalar("select count(*) from contextual_learning_trial_events") >= 4
    assert database.scalar("select count(*) from readiness_receipts") == 0
    assert database.scalar("select count(*) from contextual_learning_trials where status = 'generated'") == 4
    assert (
        global_learning_trial_count(
            database,
            dataset_hash=DATASET_HASH,
            search_family="contextual_policy_search",
        )
        == 4
    )

    repeated = service.learn_contextual(request, evaluation_budget=4, seed=42)
    assert repeated.global_trial_id == result.global_trial_id
    assert database.scalar("select count(*) from contextual_learning_trials") == 4


def test_contextual_covariance_receipts_are_scoped_to_the_exact_evaluation_time(tmp_path: Path) -> None:
    service, database, as_of = contextual_service(tmp_path)
    request = ContextualRunRequest(
        symbols=("BTCUSDT",),
        provider="binance",
        feed="spot",
        interval=BarInterval.FIVE_MINUTES,
        mode=StrategyMode.PAPER,
        as_of=as_of,
    )
    service.evaluate_contexts(request)
    service.evaluate_contexts(replace(request, as_of=as_of + timedelta(seconds=1)))

    assert database.scalar("select count(*) from contextual_covariances") == 2


def test_identical_math_allocations_remain_distinct_across_asset_contexts(tmp_path: Path) -> None:
    service, database, as_of = contextual_service(tmp_path)
    result = service.evaluate_contexts(
        ContextualRunRequest(
            symbols=("BTCUSDT",),
            provider="binance",
            feed="spot",
            interval=BarInterval.FIVE_MINUTES,
            mode=StrategyMode.PAPER,
            as_of=as_of,
        )
    )
    allocation = result.allocations["BTCUSDT:long"]
    context = database.frame("select evidence from contextual_weights limit 1").iloc[0]["evidence"]["context"]
    other_context = {**context, "context_hash": "e" * 64, "symbol": "ETHUSDT"}

    service.repository.append_allocation(allocation, other_context)

    assert database.scalar("select count(*) from contextual_weights") == 2 * len(STRATEGIES)
